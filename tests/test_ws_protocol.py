"""WebSocket protocol v1 conformance tests (control plane).

These double as executable documentation for docs/WEBSOCKET_PROTOCOL.md.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _ws(client: TestClient, token: str | None = "dev-token-1234567890"):
    url = "/ws/v1/transcribe"
    if token:
        url = f"{url}?token={token}"
    return client.websocket_connect(url)


def _start(**kw) -> dict:
    frame = {"v": 1, "type": "session.start"}
    frame.update(kw)
    return frame


def test_session_lifecycle_with_mock_provider(client):
    with _ws(client) as ws:
        ws.send_json(_start(language="fa-en", provider="mock", privacy_required=True))
        started = ws.receive_json()
        assert started["type"] == "session.started"
        assert started["protocol"] == 1
        assert started["provider"] == "mock"
        assert started["session_id"]

        ws.send_json({"v": 1, "type": "session.pause", "session_id": started["session_id"]})
        assert ws.receive_json()["type"] == "session.paused"

        ws.send_json({"v": 1, "type": "session.pause"})  # illegal transition
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "SESSION_STATE_INVALID"

        ws.send_json({"v": 1, "type": "session.resume"})
        assert ws.receive_json()["type"] == "session.recording"

        ws.send_json({"v": 1, "type": "session.stop"})
        done = ws.receive_json()
        assert done["type"] == "session.completed"
        assert done["provider"] == "mock"


def test_audio_chunk_accepted_shape_but_not_implemented(client):
    import base64

    with _ws(client) as ws:
        ws.send_json(_start())
        assert ws.receive_json()["type"] == "session.started"
        frame = {
            "v": 1,
            "type": "audio.chunk",
            "seq": 0,
            "ts_ms": 12,
            "pcm16_b64": base64.b64encode(b"\x00" * 320).decode(),
        }
        ws.send_json(frame)
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "CAPABILITY_NOT_IMPLEMENTED"
        assert err["recoverable"] is True  # connection stays usable

        # malformed audio frame is a protocol error instead
        ws.send_json({"v": 1, "type": "audio.chunk", "seq": -5, "ts_ms": 0, "pcm16_b64": "!!"})
        err2 = ws.receive_json()
        assert err2["code"] == "PROTOCOL_FRAME_INVALID"


def test_unknown_fields_rejected_in_start(client):
    with _ws(client) as ws:
        ws.send_json(_start(bogus_field=1))
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "PROTOCOL_FRAME_INVALID"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4400


def test_protocol_version_negotiation(client):
    with _ws(client) as ws:
        ws.send_json({"v": 2, "type": "session.start"})
        err = ws.receive_json()
        assert err["code"] == "PROTOCOL_VERSION_UNSUPPORTED"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4409


def test_first_frame_must_be_session_start(client):
    with _ws(client) as ws:
        ws.send_json({"v": 1, "type": "audio.chunk", "seq": 0, "ts_ms": 0, "pcm16_b64": "AA=="})
        err = ws.receive_json()
        assert err["code"] == "SESSION_STATE_INVALID"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_invalid_json_frame_survives_after_start(client):
    with _ws(client) as ws:
        ws.send_json(_start())
        assert ws.receive_json()["type"] == "session.started"
        ws.send_text("{not json")
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "PROTOCOL_FRAME_INVALID"
        # connection remains usable after a bad frame
        ws.send_json({"v": 1, "type": "session.stop"})
        assert ws.receive_json()["type"] == "session.completed"


def test_invalid_json_as_first_frame_closes(client):
    with _ws(client) as ws:
        ws.send_text("{not json")
        err = ws.receive_json()
        assert err["code"] == "PROTOCOL_FRAME_INVALID"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_unknown_message_type_replies_error(client):
    with _ws(client) as ws:
        ws.send_json(_start())
        ws.receive_json()
        ws.send_json({"v": 1, "type": "cheese.request"})
        err = ws.receive_json()
        assert err["type"] == "error" and "cheese.request" in err["message"]


def test_bad_token_closes_unauthorized(app):
    # close-before-accept → the test client raises during connect handshake
    with TestClient(app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/ws/v1/transcribe?token=not-valid"):
                pass


def test_audit_records_session_with_selected_provider(client, tmp_path):
    with _ws(client) as ws:
        ws.send_json(_start(provider="mock"))
        ws.receive_json()
        ws.send_json({"v": 1, "type": "session.stop"})
        ws.receive_json()
    audit_file = tmp_path / "audit.jsonl"
    lines = [json.loads(raw) for raw in audit_file.read_text().splitlines()]
    started = [rec for rec in lines if rec["event"] == "session_started"]
    assert started, lines
    rec = started[0]
    assert rec["provider"] == "mock"
    assert "privacy" in rec["routing_reason"] or rec["privacy_override"] in (True, False)
    assert "session_stopped" in {rec["event"] for rec in lines}
