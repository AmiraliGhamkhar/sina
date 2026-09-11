"""Phase 2 — end-to-end audio pipeline tests (mock STT over real WS frames).

Covers: binary + base64 audio ingestion, interim→final relaying with stable
segment ids, transcript store retrieval + clinician edit, pause buffer drop
policy, and provider fault surfacing. No external services anywhere.
"""
from __future__ import annotations

import base64

from ai.base import PrivacyClass, ProviderCapabilities, ProviderKind
from ai.registry import ProviderDescriptor
from ai.stt.mock import DEFAULT_SCRIPT, MockSttProvider


def _start(**kw) -> dict:
    frame = {"v": 1, "type": "session.start"}
    frame.update(kw)
    return frame


def _read_until(ws, type_name: str, limit: int = 80) -> dict:
    for _ in range(limit):
        frame = ws.receive_json()
        if frame.get("type") == type_name:
            return frame
    raise AssertionError(f"no '{type_name}' within {limit} frames")


def _collect_segment(client, extra_frames: int = 0) -> dict:
    """Run one full session; return {segment frames, completed frame}."""
    finals: dict[str, dict] = {}
    with client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890") as ws:
        ws.send_json(_start())
        _read_until(ws, "session.started")
        for _ in range(extra_frames):
            ws.send_bytes(b"\x01\x02" * 160)  # ~10 ms of "audio" each
        for _ in range(len(DEFAULT_SCRIPT)):
            frame = _read_until(ws, "transcript.final")
            finals[frame["segment_id"]] = frame
        ws.send_json({"v": 1, "type": "session.stop"})
        completed = _read_until(ws, "session.completed")
    return {"finals": finals, "completed": completed}


def test_session_streams_finals_over_mock_provider(client):
    out = _collect_segment(client, extra_frames=2)
    assert out["completed"]["segment_count"] == len(DEFAULT_SCRIPT)
    assert len(out["finals"]) == len(DEFAULT_SCRIPT)
    # medical-content preservation through the whole relay chain
    all_text = " ".join(f["text"] for f in out["finals"].values())
    for term in ("ECG", "MRI", "hypertension", "no effusion", "50"):
        assert term in all_text


def test_transcript_store_serves_completed_session(client):
    with client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890") as ws:
        ws.send_json(_start())
        started = _read_until(ws, "session.started")
        session_id = started["session_id"]
        ws.send_bytes(b"\x00" * 3200)
        for _ in range(len(DEFAULT_SCRIPT)):
            _read_until(ws, "transcript.final")
        ws.send_json({"v": 1, "type": "session.stop"})
        _read_until(ws, "session.completed")

    resp = client.get(f"/api/v1/transcripts/{session_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["provider"] == "mock"
    assert body["segment_count"] == len(DEFAULT_SCRIPT)
    first = body["segments"][0]
    assert first["edited"] is False and first["revision"] == 0
    assert first["end_ms"] >= first["start_ms"]


def test_clinician_edit_marks_segment_and_audits(client, tmp_path):
    with client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890") as ws:
        ws.send_json(_start())
        started = _read_until(ws, "session.started")
        session_id = started["session_id"]
        for _ in range(len(DEFAULT_SCRIPT)):
            _read_until(ws, "transcript.final")
        ws.send_json({"v": 1, "type": "session.stop"})
        _read_until(ws, "session.completed")

    seg = client.get(f"/api/v1/transcripts/{session_id}").json()["segments"][0]
    new_text = "بیمار با درد قفسه سینه مراجعه کرد. ECG نرمال است."
    patch = client.patch(
        f"/api/v1/transcripts/{session_id}/segments/{seg['segment_id']}",
        json={"text": new_text},
    )
    assert patch.status_code == 200
    assert patch.json()["edited"] is True and patch.json()["revision"] == 1

    again = client.get(f"/api/v1/transcripts/{session_id}").json()
    assert again["segments"][0]["text"] == new_text
    # identical re-edit must NOT bump the revision
    again_patch = client.patch(
        f"/api/v1/transcripts/{session_id}/segments/{seg['segment_id']}",
        json={"text": new_text},
    )
    assert again_patch.json()["revision"] == 1

    audit_text = (tmp_path / "audit.jsonl").read_text()
    assert "transcript_segment_edited" in audit_text
    assert new_text not in audit_text  # deny-list: content never lands in audit


def test_unknown_session_and_segment_404(client):
    resp = client.get("/api/v1/transcripts/ws_nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_pause_overflow_drops_audio_with_warning(client):
    with client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890") as ws:
        ws.send_json(_start())
        _read_until(ws, "session.started")
        ws.send_json({"v": 1, "type": "session.pause"})
        _read_until(ws, "session.paused")
        # 200 chunks × 3200 B = 640 KB >> 2 s buffer → one overflow warning
        for _ in range(200):
            ws.send_bytes(b"\x00" * 3200)
        warning = _read_until(ws, "warning")
        assert warning["code"] == "AUDIO_DROPPED_PAUSED"
        ws.send_json({"v": 1, "type": "session.stop"})
        _read_until(ws, "session.completed")


def test_base64_audio_chunk_accepted_end_to_end(client):
    with client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890") as ws:
        ws.send_json(_start())
        _read_until(ws, "session.started")
        ws.send_json(
            {
                "v": 1,
                "type": "audio.chunk",
                "seq": 0,
                "ts_ms": 0,
                "pcm16_b64": base64.b64encode(b"\x00" * 3200).decode(),
            }
        )
        final = _read_until(ws, "transcript.final")
        assert final["text"] == DEFAULT_SCRIPT[0]["text"]
        ws.send_json({"v": 1, "type": "session.stop"})
        _read_until(ws, "session.completed")


def test_provider_fault_surfaces_as_error_frame(client, app):
    # register a faulting provider directly on this app's registry
    registry = app.state.ai_registry
    registry.register(
        ProviderDescriptor(
            name="boom",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL, supports_streaming=True
            ),
            factory=lambda cfg: MockSttProvider({"fail_with": "provider exploded"}),
        )
    )
    try:
        with client.websocket_connect(
            "/ws/v1/transcribe?token=dev-token-1234567890"
        ) as ws:
            ws.send_json(_start(provider="boom"))
            _read_until(ws, "session.started")
            err = _read_until(ws, "error")
            assert err["code"] == "PROVIDER_UNAVAILABLE"
            assert "provider exploded" in err["message"]
    finally:
        registry.unregister(ProviderKind.STT, "boom")
