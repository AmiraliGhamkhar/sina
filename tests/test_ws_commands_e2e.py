"""Phase 6 — voice commands end-to-end over the real WebSocket path.

A scripted mock STT dictates clinical speech interleaved with command
utterances; the hub must parse commands server-side, emit ``command.detected``
frames, apply transcript effects, keep ambiguous speech verbatim (with a
warning), and honor voice-pause semantics.
"""
from __future__ import annotations

from ai.base import PrivacyClass, ProviderCapabilities, ProviderKind
from ai.registry import ProviderDescriptor
from ai.stt.mock import MockSttProvider

TOKEN = "dev-token-1234567890"

SCRIPT = [
    {"text": "بیمار با درد زانوی راست مراجعه کرد.", "start_ms": 0, "end_ms": 2000,
     "language": "fa"},
    {"text": "پاراگراف جدید", "start_ms": 2000, "end_ms": 3000, "language": "fa"},
    {"text": "معاینه بدون تورم است.", "start_ms": 3000, "end_ms": 4500, "language": "fa"},
    {"text": "حذف جمله آخر", "start_ms": 4500, "end_ms": 5500, "language": "fa"},
    {"text": "درج بخش طرح درمان", "start_ms": 5500, "end_ms": 6800, "language": "fa"},
    {"text": "دوز 40 mg فوروسماید تجویز شد.", "start_ms": 6800, "end_ms": 8200, "language": "fa-en"},
    {"text": "بعد از این پاراگراف جدید شروع می‌شود و درد ادامه دارد.", "start_ms": 8200,
     "end_ms": 10000, "language": "fa"},
]


def _register_scripted_mock(client, name: str = "cmd-mock") -> None:
    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name=name,
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL, supports_streaming=True
            ),
            factory=lambda cfg: MockSttProvider(
                {"script": list(SCRIPT), "interim_delay_s": 0.0}
            ),
            configured=lambda cfg: True,
        )
    )


def _start(**kw) -> dict:
    frame = {"v": 1, "type": "session.start"}
    frame.update(kw)
    return frame


def _drain_until(ws, expected_outcomes: int, limit: int = 400) -> list[dict]:
    """Read frames until `expected_outcomes` transcript.final/command.detected
    frames arrived (each scripted dictation settles as exactly one of the
    two; suppressed finals settle as neither)."""
    frames: list[dict] = []
    outcomes = 0
    for _ in range(limit):
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") in ("transcript.final", "command.detected"):
            outcomes += 1
            if outcomes >= expected_outcomes:
                return frames
    raise AssertionError(f"only {outcomes}/{expected_outcomes} outcomes in {limit} frames")


def _run_session(client, provider: str, expected_outcomes: int):
    _register_scripted_mock(client, provider)
    with client.websocket_connect(f"/ws/v1/transcribe?token={TOKEN}") as ws:
        ws.send_json(_start(provider=provider))
        started = ws.receive_json()
        assert started["type"] == "session.started"
        session_id = started["session_id"]
        ws.send_bytes(b"\x00" * 3200)
        frames = _drain_until(ws, expected_outcomes)
        ws.send_json({"v": 1, "type": "session.stop"})
        for _ in range(50):
            frame = ws.receive_json()
            if frame.get("type") == "session.completed":
                break
    return session_id, frames


def test_commands_detected_and_effects_applied(client):
    session_id, frames = _run_session(client, "cmd-mock", expected_outcomes=7)
    commands = [f for f in frames if f["type"] == "command.detected"]
    ids = [c["command"] for c in commands]
    assert "new_paragraph" in ids
    assert "delete_last_sentence" in ids
    assert "insert_section" in ids
    insert = next(c for c in commands if c["command"] == "insert_section")
    assert insert["args"]["section_title"] == "طرح درمان"
    assert insert["utterance_text"] == "درج بخش طرح درمان"

    # transcript store reflects the effects
    body = client.get(f"/api/v1/transcripts/{session_id}").json()
    segments = body["segments"]
    kinds = [s["kind"] for s in segments]
    assert "paragraph" in kinds
    assert "section" in kinds
    # the command utterances never entered the transcript as dictation
    texts = [s["text"] for s in segments if s["kind"] == "dictated"]
    assert all("پاراگراف جدید" not in t or "شروع می‌شود" in t for t in texts)
    assert all("حذف جمله" not in t for t in texts)
    assert all("درج بخش" not in t for t in texts)
    # delete-last-sentence removed the last sentence of the prior segment
    # ("معاینه بدون تورم است." was dictated right before the delete command)
    tum = [t for t in texts if "تورم" in t]
    assert tum == []  # the whole one-sentence segment was removed
    # later dictation survived
    assert any("40 mg" in t for t in texts)


def test_ambiguous_trigger_keeps_text_and_warns(client):
    session_id, frames = _run_session(client, "cmd-mock2", expected_outcomes=7)
    warnings = [f for f in frames if f["type"] == "warning"]
    assert any(w["code"] == "COMMAND_AMBIGUOUS" for w in warnings)
    body = client.get(f"/api/v1/transcripts/{session_id}").json()
    texts = [s["text"] for s in body["segments"] if s["kind"] == "dictated"]
    # the ambiguous sentence is preserved verbatim
    assert any("پاراگراف جدید شروع می‌شود" in t for t in texts)


def test_voice_pause_suppresses_speech_until_resume(client):
    script = [
        {"text": "توقف ضبط", "start_ms": 0, "end_ms": 800, "language": "fa"},
        {"text": "این جمله نباید ثبت شود.", "start_ms": 800, "end_ms": 1800, "language": "fa"},
        {"text": "ادامه ضبط", "start_ms": 1800, "end_ms": 2600, "language": "fa"},
        {"text": "این جمله ثبت می‌شود.", "start_ms": 2600, "end_ms": 3600, "language": "fa"},
    ]
    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name="pause-mock",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL, supports_streaming=True
            ),
            factory=lambda cfg: MockSttProvider(
                {"script": list(script), "interim_delay_s": 0.0}
            ),
            configured=lambda cfg: True,
        )
    )
    with client.websocket_connect(f"/ws/v1/transcribe?token={TOKEN}") as ws:
        ws.send_json(_start(provider="pause-mock"))
        ws.receive_json()  # session.started
        ws.send_bytes(b"\x00" * 3200)
        frames = _drain_until(ws, 3)  # 2 commands + 1 surviving final
        ws.send_json({"v": 1, "type": "session.stop"})
        for _ in range(50):
            if ws.receive_json().get("type") == "session.completed":
                break

    commands = [f["command"] for f in frames if f["type"] == "command.detected"]
    assert "pause_recording" in commands and "resume_recording" in commands
    finals = [f["text"] for f in frames if f["type"] == "transcript.final"]
    assert "این جمله ثبت می‌شود." in finals
    assert "این جمله نباید ثبت شود." not in finals


def test_command_metrics_counter(client):
    before = client.get("/api/v1/observability/stats").json()["counters"].get(
        "voice_commands_executed", 0
    )
    _run_session(client, "cmd-mock3", expected_outcomes=7)
    after = client.get("/api/v1/observability/stats").json()["counters"][
        "voice_commands_executed"
    ]
    assert after >= before + 3
