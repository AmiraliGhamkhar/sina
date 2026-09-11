"""Deepgram adapter tests — prerecorded (HTTP, MockTransport) and live
streaming (WS via ScriptedTransport). Verifies protocol translation,
keyword boosting, confidence/time normalization, medical-token
preservation, and error mapping — all from recorded-style fixtures.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from providers_support import (
    ScriptedTransport,
    agen,
    collect,
    load_fixture,
    pcm_speech,
    scripted_connector,
)

from ai.base import ProviderError, ProviderUnavailableError, STTRequest
from ai.stt.deepgram import DeepgramProvider

LISTEN_FIXTURE = load_fixture("deepgram_listen.json")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(handler=None, **cfg) -> DeepgramProvider:
    cfg.setdefault("api_key", "dg-key")
    return DeepgramProvider(cfg, client=_client(handler or (lambda r: httpx.Response(200, json=LISTEN_FIXTURE))))


# ---- prerecorded batch -----------------------------------------------------


def test_deepgram_batch_params_auth_and_normalization():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(200, json=LISTEN_FIXTURE)

    prov = _provider(
        handler,
        keywords=["furosemide"],
        model="nova-2-general",
    )
    segments = asyncio.run(
        prov.transcribe(
            STTRequest(
                audio=pcm_speech(9100),
                language="fa-en",
                context_hints=("aspirin", "چپ", "furosemide"),  # dup deliberate
            )
        )
    )
    q = parse_qs(urlsplit(str(seen["url"])).query)
    assert q["model"] == ["nova-2-general"]
    assert q["language"] == ["fa"]  # fa-en mapped for the wire
    assert q["punctuate"] == ["true"]
    assert q["smart_format"] == ["true"]
    assert q["keywords"] == ["furosemide", "aspirin", "چپ"]  # dedup, order kept
    assert seen["headers"]["authorization"] == "Token dg-key"
    assert seen["headers"]["content-type"].startswith("audio/L16")
    assert len(seen["body"]) == len(pcm_speech(9100))  # raw pcm body, no container

    assert len(segments) == 3
    assert segments[0].start_ms == 100 and segments[0].end_ms == 2900
    assert segments[1].confidence == 0.91
    joined = " ".join(s.text for s in segments)
    for token in ("ECG", "بدون", "50 mg", "aspirin", "no allergy"):
        assert token in joined
    asyncio.run(prov.aclose())


def test_deepgram_batch_text_fallback_and_errors():
    def only_alt(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": {"channels": [{"alternatives": [{"transcript": "سلام ECG.", "confidence": 0.77}]}]}},
        )

    prov = _provider(only_alt)
    (seg,) = asyncio.run(prov.transcribe(STTRequest(audio=pcm_speech(1000))))
    assert seg.text == "سلام ECG." and seg.confidence == 0.77
    assert seg.end_ms == 1000

    def unauthorized(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"description": "invalid credentials"})

    with pytest.raises(ProviderError) as exc:
        asyncio.run(_provider(unauthorized).transcribe(STTRequest(audio=b"\x00\x00" * 800)))
    assert exc.value.retryable is False
    assert exc.value.status_hint == 401

    def server_down(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="maintenance")

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(_provider(server_down).transcribe(STTRequest(audio=b"\x00\x00" * 800)))
    asyncio.run(prov.aclose())


def test_deepgram_health_probe():
    def ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/projects"
        return httpx.Response(200, json={"results": []})

    health = asyncio.run(_provider(ok).health())
    assert health.ok

    def denied(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    h2 = asyncio.run(_provider(denied).health())
    assert not h2.ok and "403" in (h2.detail or "")


def test_deepgram_ws_url_derivation_for_proxies():
    prov = _provider(base_url="http://127.0.0.1:9990")
    assert prov._ws_url == "ws://127.0.0.1:9990/v1/listen"
    prov2 = _provider()
    assert prov2._ws_url == "wss://api.deepgram.com/v1/listen"


# ---- live streaming over WS ----------------------------------------------------

STREAM_MESSAGES = [
    {"type": "Open", "task_id": "t1", "api_version": "1.0"},
    {"type": "Listening", "request_id": "r1"},
    {"type": "Metadata", "request_id": "r1", "channels": 1, "model_info": {"name": "nova-2-general"}},
    {
        "type": "Results", "channel_index": [0, 1], "start_ms": 100, "end_ms": 1300,
        "is_final": False, "speech_final": False,
        "channel": {"alternatives": [{"transcript": "بیمار با درد", "confidence": 0.55}]},
    },
    {
        "type": "Results", "channel_index": [0, 1], "start_ms": 100, "end_ms": 2800,
        "is_final": True, "speech_final": False,
        "channel": {"alternatives": [{"transcript": "بیمار با درد قفسه سینه", "confidence": 0.8}]},
    },
    {
        "type": "Results", "channel_index": [0, 1], "start_ms": 100, "end_ms": 3200,
        "is_final": True, "speech_final": True,
        "channel": {"alternatives": [{"transcript": "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است.", "confidence": 0.92}]},
    },
    {"type": "UtteranceEnd", "last_word_end_ms": 3200},
    {
        "type": "Results", "channel_index": [0, 1], "start_ms": 3400, "end_ms": 5100,
        "is_final": True, "speech_final": True,
        "channel": {"alternatives": [{"transcript": "دوز 50 mg aspirin.", "confidence": 0.9}]},
    },
    {"type": "ConnectionClosed"},
]


def test_deepgram_stream_interim_final_semantics():
    transport = ScriptedTransport(STREAM_MESSAGES)
    connector = scripted_connector(transport)
    prov = DeepgramProvider(
        {"api_key": "dg-key", "keywords": ["furosemide"], "endpointing_ms": 300},
        client=_client(lambda r: httpx.Response(200)),
        transport_factory=connector,
    )
    chunks = [pcm_speech(100) for _ in range(5)]

    async def run():
        return await collect(prov.stream(agen(chunks)))

    events = asyncio.run(run())
    # URL carries live params + auth via header
    url = connector.captured["url"]
    assert url.startswith("wss://api.deepgram.com/v1/listen?")
    q = parse_qs(urlsplit(url).query)
    assert q["interim_results"] == ["true"]
    assert q["endpointing"] == ["300"]
    assert q["utterance_end_ms"] == ["1000"]
    assert q["keywords"] == ["furosemide"]
    assert "language" not in q  # live mixed speech → provider auto-detect
    assert connector.captured["headers"]["Authorization"] == "Token dg-key"

    # client sent every audio chunk, then Finalize + CloseStream
    kinds = [k for k, _ in transport.sent]
    assert kinds == ["bytes"] * 5 + ["text", "text"]
    closes = [json.loads(p) for k, p in transport.sent if k == "text"]
    assert closes == [{"type": "Finalize"}, {"type": "CloseStream"}]

    # message translation: interim, interim (is_final without speech_final is
    # demoted), final, final
    assert [e.is_final for e in events] == [False, False, True, True]
    assert events[0].text == "بیمار با درد"
    assert events[-1].text == "دوز 50 mg aspirin."
    assert events[-1].confidence == 0.9
    assert events[-2].start_ms == 100 and events[-2].end_ms == 3200
    for token in ("ECG", "بدون", "50 mg"):
        assert token in " ".join(e.text for e in events if e.is_final)
    assert transport.closed  # transport always released
    asyncio.run(prov.aclose())


def test_deepgram_stream_error_frame_maps():
    transport = ScriptedTransport(
        [{"type": "Open"}, {"type": "Error", "description": "not authorized to access"}]
    )
    prov = DeepgramProvider(
        {"api_key": "bad"}, client=_client(lambda r: httpx.Response(200)),
        transport_factory=scripted_connector(transport),
    )

    async def run():
        return await collect(prov.stream(agen([pcm_speech(100)])))

    with pytest.raises(ProviderError) as exc:
        asyncio.run(run())
    assert "not authorized" in str(exc.value)
    assert exc.value.retryable is False
    assert transport.closed
