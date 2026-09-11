"""Phase 3 — POST /api/v1/transcribe/batch: multipart upload → routed batch
transcription. Uses fixed local/cloud STT providers so the full path
(router → provider → normalization → audit) runs without any network.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api.config import Settings
from fastapi.testclient import TestClient
from providers_support import make_wav, pcm_speech

from ai.base import (
    PrivacyClass,
    ProviderCapabilities,
    ProviderKind,
    STTProvider,
    STTRequest,
    TranscriptSegment,
)
from ai.registry import ProviderDescriptor

FIXED_TEXTS = (
    "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است.",
    "دوز 40 mg furosemide تجویز شد. درد سمت چپ.",
)


class _FixedStt(STTProvider):
    """Deterministic batch-only provider for endpoint tests."""

    def __init__(self, config: Mapping[str, Any] | None = None, *, privacy=PrivacyClass.LOCAL):
        self._privacy = privacy
        self.last_request: STTRequest | None = None
        self._caps = ProviderCapabilities(
            privacy=privacy,
            supports_streaming=False,
            supports_batch=True,
            languages=("fa", "en", "*"),
            latency_hint_ms=5000,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        self.last_request = request
        return [
            TranscriptSegment(text=text, start_ms=i * 2000, end_ms=i * 2000 + 1900, confidence=0.9)
            for i, text in enumerate(FIXED_TEXTS)
        ]


def _register(client: TestClient, name: str, instance: _FixedStt) -> None:
    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name=name,
            kind=ProviderKind.STT,
            capabilities=instance.capabilities,
            factory=lambda cfg, _p=instance: _p,
            configured=lambda cfg: True,
        )
    )


def _post(client, **data):
    wav = make_wav(pcm_speech(4200))
    return client.post(
        "/api/v1/transcribe/batch",
        files={"file": ("rec.wav", wav, "audio/wav")},
        data=data,
    )


def test_batch_happy_path(client):
    fake = _FixedStt()
    _register(client, "batchfix", fake)
    resp = _post(client, provider="batchfix", language="fa-en", context_hints="aspirin, chp")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "batchfix"
    assert body["segment_count"] == 2
    assert body["mode"] == "auto"
    assert body["request_id"].startswith("bat_")
    assert body["audio_duration_ms"] == 4200
    assert body["segments"][0]["segment_id"].endswith("_0000")
    assert body["text"] == "\n\n".join(FIXED_TEXTS)
    # hints reach the provider; audio is decoded PCM from the WAV container
    assert fake.last_request is not None
    assert "aspirin" in fake.last_request.context_hints
    assert len(fake.last_request.audio) == len(pcm_speech(4200))
    assert fake.last_request.sample_rate == 16000


def test_batch_privacy_wall_beats_cloud_preference(client):
    cloud = _FixedStt(privacy=PrivacyClass.CLOUD)
    _register(client, "cloudfix", cloud)
    resp = _post(client, provider="cloudfix", mode="cloud", privacy_required="true")
    assert resp.status_code == 200
    body = resp.json()
    # privacy pins to LOCAL: the explicitly preferred cloud provider is NOT used
    assert body["provider"] != "cloudfix"
    assert body["privacy_override_applied"] is True
    assert cloud.last_request is None


def test_batch_cloud_mode_without_cloud_provider_503(client, settings: Settings):
    # no cloud STT configured; privacy off → mode filter applies → nothing left
    resp = _post(client, privacy_required="false", mode="cloud")
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["code"] == "NO_PROVIDER"
    # the refusal message names candidates but never echoes audio/token data
    assert "transcribe_batch" in err["message"]


def test_batch_rejects_bad_audio(client):
    # stereo → 400
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm_speech(200))
    resp = client.post(
        "/api/v1/transcribe/batch", files={"file": ("s.wav", buf.getvalue(), "audio/wav")}
    )
    assert resp.status_code == 400
    assert "mono" in resp.json()["error"]["message"]

    # garbage → 400 (RIFF header but not a parsable wave)
    resp2 = client.post(
        "/api/v1/transcribe/batch", files={"file": ("x.wav", b"RIFF....JUNK", "audio/wav")}
    )
    assert resp2.status_code == 400


def test_batch_empty_and_oversize(client, app):
    resp = client.post("/api/v1/transcribe/batch", files={"file": ("e.wav", b"", "audio/wav")})
    assert resp.status_code == 400

    app.state.settings.stt.batch_max_bytes = 128
    resp2 = _post(client)  # ~134 KB of speech
    assert resp2.status_code == 413
    app.state.settings.stt.batch_max_bytes = 64 * 1024 * 1024


def test_batch_raw_pcm_and_unknown_provider_fallback(client):
    # raw mono pcm (no container)
    resp = client.post(
        "/api/v1/transcribe/batch",
        files={"file": ("stream.pcm", pcm_speech(1000), "application/octet-stream")},
        data={"sample_rate": "16000", "provider": "does-not-exist"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # unknown explicit provider is non-strict → falls back (mock, lowest latency)
    assert body["provider"] == "mock"
    assert body["audio_duration_ms"] == 1000


def test_batch_audit_has_no_transcript_content(client, tmp_path):
    fake = _FixedStt()
    _register(client, "batchfix2", fake)
    resp = _post(client, provider="batchfix2")
    assert resp.status_code == 200
    audit_text = (tmp_path / "audit.jsonl").read_text()
    assert "transcribe_batch_completed" in audit_text
    for forbidden in ("ECG", "furosemide", "rec.wav", "TOKEN"):
        assert forbidden not in audit_text


def test_provider_listing_includes_phase3_adapters(client):
    resp = client.get("/api/v1/providers", params={"kind": "stt"})
    assert resp.status_code == 200
    entries = {p["name"]: p for p in resp.json()}
    for name in ("whisper-local", "qwen-asr", "deepgram", "speechmatics"):
        assert name in entries
        # nothing configured in tests → visible but not routable, never probed
        assert entries[name]["configured"] is False
        assert entries[name]["health"] is None
    assert entries["deepgram"]["capabilities"]["privacy_class"] == "cloud"
    assert entries["whisper-local"]["capabilities"]["privacy_class"] == "local"
    # listing text must not contain credential material
    low = resp.text.lower()
    for forbidden in ("api_key", "bearer", "secret", "token"):
        assert forbidden not in low
