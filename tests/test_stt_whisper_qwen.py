"""whisper-local + qwen-asr adapter tests against recorded-style fixtures.

HTTP is mocked with httpx.MockTransport — response bodies are verbatim-style
whisper.cpp / OpenAI-audio payloads from tests/fixtures. Acceptance focus:
normalization to TranscriptSegment, medical token preservation
(numbers/doses, negation, laterality, embedded English), error mapping and
the VAD-windowed stream path.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from providers_support import agen, collect, load_fixture, pcm_silence, pcm_speech

from ai.base import ProviderError, ProviderUnavailableError, STTRequest
from ai.stt.qwen_asr import QwenAsrProvider
from ai.stt.whisper_server import WhisperServerProvider

WHISPER_FIXTURE = load_fixture("whisper_inference.json")
QWEN_FIXTURE = load_fixture("qwen_transcription.json")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _whisper(handler, **cfg) -> WhisperServerProvider:
    return WhisperServerProvider({"url": "http://fake-whisper:8001", **cfg}, client=_client(handler))


# ---- whisper-local -------------------------------------------------------------


def test_whisper_transcribe_normalizes_and_preserves():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(200, json=WHISPER_FIXTURE)

    prov = _whisper(handler, hotwords=["furosemide", "ECG"])
    segments = asyncio.run(
        prov.transcribe(
            STTRequest(audio=pcm_speech(12000), language="fa-en", context_hints=("aspirin",))
        )
    )
    assert captured["path"] == "/inference"
    assert len(segments) == 3
    # seconds → ms conversion
    assert segments[0].start_ms == 350 and segments[0].end_ms == 3120
    assert all(s.is_final for s in segments)
    # medical-content preservation, end to end
    joined = " ".join(s.text for s in segments)
    for token in ("ECG", "hypertension", "40 mg", "furosemide", "بدون", "ندارد", "چپ"):
        assert token in joined
    # WAV container actually sent, hotwords + hints in initial_prompt
    assert b"RIFF" in captured["body"] and b'filename="audio.wav"' in captured["body"]
    assert b"initial_prompt" in captured["body"]
    assert b"furosemide" in captured["body"] and b"aspirin" in captured["body"]
    assert b'name="language"' in captured["body"]  # fa-en mapped to fa
    asyncio.run(prov.aclose())


def test_whisper_language_mapping_and_auto():
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=WHISPER_FIXTURE)

    prov = _whisper(handler)
    asyncio.run(prov.transcribe(STTRequest(audio=pcm_speech(100), language="fa-en")))
    asyncio.run(prov.transcribe(STTRequest(audio=pcm_speech(100), language=None)))
    assert b'name="language"' in seen[0] and b"\r\nfa\r\n" in seen[0]
    assert b'name="language"' not in seen[1]  # None → server auto-detect
    asyncio.run(prov.aclose())


def test_whisper_error_mapping():
    def unavailable(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    prov = _whisper(unavailable)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        asyncio.run(prov.transcribe(STTRequest(audio=pcm_speech(10))))
    assert excinfo.value.retryable is True

    def rejected(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="bad form")

    prov2 = _whisper(rejected)
    with pytest.raises(ProviderError) as exc2:
        asyncio.run(prov2.transcribe(STTRequest(audio=pcm_speech(10))))
    assert exc2.value.retryable is False
    assert exc2.value.status_hint == 422
    asyncio.run(prov2.aclose())


def test_whisper_health_probe():
    def ok(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/health"
        return httpx.Response(200, text="OK")

    prov = _whisper(ok)
    health = asyncio.run(prov.health())
    assert health.ok and health.latency_ms is not None

    def down(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    prov2 = _whisper(down)
    health2 = asyncio.run(prov2.health())
    assert not health2.ok
    assert health2.detail == "ConnectError"
    asyncio.run(prov2.aclose())


def test_whisper_stream_yields_interims_then_finals():
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=WHISPER_FIXTURE)

    prov = _whisper(
        handler,
        vad_silence_ms=400,
        vad_interim_ms=1000,
        vad_pre_roll_ms=0,
    )
    chunks = [pcm_speech(100)] * 25 + [pcm_silence(100)] * 6

    async def run():
        return await collect(prov.stream(agen(chunks)))

    events = asyncio.run(run())
    interims = [e for e in events if not e.is_final]
    finals = [e for e in events if e.is_final]
    # silence-close produced exactly one finals batch (3 fixture segments)
    assert len(finals) == 3
    assert interims and len(interims) % 3 == 0  # each interim dispatch = full batch
    assert calls["n"] == len(interims) // 3 + 1
    assert finals[0].text == WHISPER_FIXTURE["segments"][0]["text"]
    # timeline: session time = cut start + buffer-relative offset
    assert finals[0].start_ms >= 0
    assert finals[-1].end_ms > finals[0].start_ms
    # interim events precede the final cut in stream order
    kinds = [e.is_final for e in events]
    assert kinds[0] is False and kinds[-1] is True
    asyncio.run(prov.aclose())


# ---- qwen-asr -------------------------------------------------------------------


def test_qwen_transcribe_verbose_json_segments():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(200, json=QWEN_FIXTURE)

    prov = QwenAsrProvider(
        {"url": "http://fake-qwen:8020", "model": "qwen2-audio-instruct"}, client=_client(handler)
    )
    segments = asyncio.run(
        prov.transcribe(STTRequest(audio=pcm_speech(6200), language="fa"))
    )
    assert captured["path"] == "/v1/audio/transcriptions"
    assert len(segments) == 2
    assert segments[0].start_ms == 200 and segments[0].end_ms == 2800
    assert b"qwen2-audio-instruct" in captured["body"]
    assert b"\r\nfa\r\n" in captured["body"]
    # preservation through normalization
    joined = " ".join(s.text for s in segments)
    for token in ("ECG", "بدون", "140", "90"):
        assert token in joined
    asyncio.run(prov.aclose())


def test_qwen_plain_text_fallback_single_segment():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "سلام ECG نرمال است."})

    prov = QwenAsrProvider({"url": "http://x"}, client=_client(handler))
    (seg,) = asyncio.run(prov.transcribe(STTRequest(audio=pcm_speech(1500))))
    assert seg.text == "سلام ECG نرمال است."
    assert 1400 <= seg.end_ms <= 1600
    assert seg.is_final
    asyncio.run(prov.aclose())


def test_qwen_unreachable_maps_to_provider_error():
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    prov = QwenAsrProvider({"url": "http://x"}, client=_client(handler))
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(prov.transcribe(STTRequest(audio=b"\x00\x00" * 800)))
    asyncio.run(prov.aclose())


def test_qwen_stream_produces_final_segment():
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=QWEN_FIXTURE)

    prov = QwenAsrProvider(
        {
            "url": "http://x",
            "vad_silence_ms": 400,
            "vad_interim_ms": 100000,
            "vad_pre_roll_ms": 0,
        },
        client=_client(handler),
    )
    chunks = [pcm_speech(100)] * 12 + [pcm_silence(100)] * 6

    async def run():
        return await collect(prov.stream(agen(chunks)))

    events = asyncio.run(run())
    assert events and all(e.is_final for e in events)
    assert events[0].text.startswith("بیمار")
    asyncio.run(prov.aclose())


def test_providers_registered_and_configured_predicates():
    from ai.base import ProviderKind
    from ai.registry import build_default_registry

    reg = build_default_registry()
    stt_names = {d.name for d in reg.descriptors(ProviderKind.STT)}
    assert {"mock", "whisper-local", "qwen-asr", "deepgram", "speechmatics"} <= stt_names
    assert reg.is_configured(ProviderKind.STT, "whisper-local", {"url": "http://x"})
    assert not reg.is_configured(ProviderKind.STT, "whisper-local", {})
    assert not reg.is_configured(ProviderKind.STT, "deepgram", {})
    assert reg.is_configured(ProviderKind.STT, "speechmatics", {"api_key": "k"})
    caps = reg.get_descriptor(ProviderKind.STT, "deepgram").capabilities
    assert caps.supports_batch and caps.supports_streaming
