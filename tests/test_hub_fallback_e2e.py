"""Phase 5 acceptance: mid-session provider failure transparently falls back.

A routed STT provider dies with a RETRYABLE error one segment into a live
WebSocket session. The session must not die: exactly one PROVIDER_FALLBACK
warning, transcription continues, health demotion is visible via the stats
endpoint, and a privacy-required session never sees the fallback cross the
wall (the chain is filtered upstream in route())."""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from ai.base import (
    PrivacyClass,
    ProviderCapabilities,
    ProviderError,
    ProviderKind,
    STTProvider,
    STTRequest,
    TranscriptSegment,
)
from ai.registry import ProviderDescriptor


class _FlakyStt(STTProvider):
    """Emits one final segment, then dies retryably — without ever draining
    the audio iterator (a real provider dies with audio still in flight)."""

    name = "flakystt"  # health tracker keys off provider.name

    def __init__(self) -> None:
        self._caps = ProviderCapabilities(
            privacy=PrivacyClass.LOCAL,
            supports_streaming=True,
            supports_batch=True,
            languages=("fa", "en"),
            latency_hint_ms=5,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        return [TranscriptSegment(text="batch tail from flaky", start_ms=0, end_ms=900)]

    async def stream(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]:
        yield TranscriptSegment(text="partial before death", start_ms=0, end_ms=400, is_final=True)
        raise ProviderError("flaky died mid-stream", provider="flakystt", retryable=True)


def _register(client: TestClient, name: str, instance: STTProvider) -> None:
    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name=name,
            kind=ProviderKind.STT,
            capabilities=instance.capabilities,
            factory=lambda cfg, _p=instance: _p,
            configured=lambda cfg: True,
        )
    )


def _read_until_type(ws, wanted: str, limit: int = 60):
    frames = []
    for _ in range(limit):
        frame = ws.receive_json()
        frames.append(frame)
        if frame.get("type") == wanted:
            return frame, frames
    raise AssertionError(f"'{wanted}' not seen; frames={frames}")


def test_live_session_survives_provider_death_and_falls_back(client):
    app = client.app
    app.state.provider_health.failure_threshold = 1  # make demotion immediate
    _register(client, "flakystt", _FlakyStt())

    with client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "session.start",
                "language": "fa-en",
                "mode": "local",
                "privacy_required": True,
                "provider": "flakystt",
                "audio": {"sample_rate": 16000, "channels": 1, "format": "pcm_s16le"},
            }
        )
        started, _ = _read_until_type(ws, "session.started")
        assert started["provider"] == "flakystt"

        ws.send_bytes(b"\x01\x02" * 800)
        first_final, _ = _read_until_type(ws, "transcript.final")
        assert first_final["text"] == "partial before death"

        warn, _ = _read_until_type(ws, "warning")
        assert warn["code"] == "PROVIDER_FALLBACK"

        # the mock takes over and produces its own final — session never died
        second_final, _ = _read_until_type(ws, "transcript.final")
        assert second_final["text"] != "partial before death"

        ws.send_json({"v": 1, "type": "session.stop", "session_id": started["session_id"]})
        completed, tail = _read_until_type(ws, "session.completed")
        assert completed["segment_count"] >= 1
        # exactly one fallback frame across the whole session
        fallback_warns = [f for f in tail if f.get("code") == "PROVIDER_FALLBACK"]
        assert fallback_warns == []  # no second warning after completed

    stats = client.get("/api/v1/observability/stats").json()
    assert stats["provider_health"]["stt:flakystt"]["healthy"] is False
    assert stats["provider_health"]["stt:flakystt"]["consecutive_failures"] >= 1
    assert stats["counters"].get("stt_fallbacks", 0) >= 1


def test_fallback_kill_switch_restores_hard_failure(client):
    app = client.app
    app.state.settings.routing.fallback_enabled = False
    _register(client, "flakystt", _FlakyStt())

    with client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "session.start",
                "language": "fa-en",
                "mode": "local",
                "privacy_required": True,
                "provider": "flakystt",
                "audio": {"sample_rate": 16000, "channels": 1, "format": "pcm_s16le"},
            }
        )
        _read_until_type(ws, "session.started")
        ws.send_bytes(b"\x01\x02" * 800)
        err, frames = _read_until_type(ws, "error")
        assert err["code"] == "PROVIDER_UNAVAILABLE"
        assert err["recoverable"] is True
        assert not [f for f in frames if f.get("code") == "PROVIDER_FALLBACK"]


def test_privacy_required_batch_never_falls_back_to_cloud(client):
    """Defense in depth at the batch endpoint: with a LOCAL provider wired to
    die retryably, CLOUD candidates must not rescue it when privacy is on —
    the request 502s instead of crossing the wall."""

    class _CloudOk(_FlakyStt):
        name = "cloudstt"

        def __init__(self):
            super().__init__()
            self._caps = ProviderCapabilities(
                privacy=PrivacyClass.CLOUD,
                supports_streaming=True,
                supports_batch=True,
                languages=("fa", "en"),
                latency_hint_ms=1,
            )

        async def transcribe(self, request):
            return [TranscriptSegment(text="CLOUD LEAK", start_ms=0, end_ms=1)]

    class _SickLocal(_FlakyStt):
        name = "mock"

        async def transcribe(self, request):
            raise ProviderError("sick local provider", provider="mock", retryable=True)

    # EVERY local provider fails at runtime; a healthy CLOUD provider exists
    # — routing must refuse to rescue the request via the cloud provider.
    client.app.state.ai_registry.unregister(ProviderKind.STT, "mock")
    _register(client, "mock", _SickLocal())
    _register(client, "cloudstt", _CloudOk())

    resp = client.post(
        "/api/v1/transcribe/batch",
        data={"language": "fa", "privacy_required": "true", "mode": "auto"},
        files={"file": ("a.raw", b"\x00\x01" * 400, "application/octet-stream")},
    )
    assert resp.status_code == 502, resp.text  # tried local only, never escalated
    err = resp.json()["error"]
    assert [t["provider"] for t in err["details"]["tried"]] == ["mock"]
    assert "CLOUD LEAK" not in resp.text
