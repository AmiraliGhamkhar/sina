"""Speechmatics adapter tests — batch job lifecycle (HTTP MockTransport)
and realtime WS protocol (ScriptedTransport). Persian config, medical-token
preservation, error mapping."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from providers_support import (
    ScriptedTransport,
    agen,
    collect,
    load_fixture,
    pcm_silence,
    pcm_speech,
    scripted_connector,
)

from ai.base import ProviderError, ProviderUnavailableError, STTRequest
from ai.stt.speechmatics import SpeechmaticsProvider

JOB_RESULT = load_fixture("speechmatics_result.json")


def _provider(handler=None, **cfg) -> SpeechmaticsProvider:
    cfg.setdefault("api_key", "sm-key")
    client = _client(handler) if handler else None
    return SpeechmaticsProvider(cfg, client=client)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---- batch job lifecycle -------------------------------------------------------


def test_speechmatics_batch_job_flow_and_preservation():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        calls.append((method, path))
        if method == "POST" and path.endswith("/jobs"):
            body = json.loads(request.content)
            assert body["operating_params"]["language"] == "fa"
            assert body["audio_format"] == "pcm_s16le"
            assert request.headers["Authorization"] == "Bearer sm-key"
            return httpx.Response(200, json={"request_id": "job-1"})
        if method == "PATCH" and path.endswith("send-audio"):
            assert len(request.content) == len(pcm_speech(2600))
            return httpx.Response(202, json={"message": "accepted"})
        if path.endswith("/jobs/job-1"):
            return httpx.Response(200, json={"job_status": "done", "status": {}})
        if path.endswith("/result"):
            return httpx.Response(200, json=JOB_RESULT)
        raise AssertionError(f"unexpected call {method} {path}")

    prov = SpeechmaticsProvider({"api_key": "sm-key", "language": "fa"}, client=_client(handler))
    segments = asyncio.run(
        prov.transcribe(STTRequest(audio=pcm_speech(2600), language="fa-en"))
    )
    assert ("POST", "/v2/jobs") in calls
    joined = " ".join(s.text for s in segments)
    for token in ("MRI", "چپ", "بدون", "effusion"):
        assert token in joined
    assert segments[0].start_ms == 200  # first element start 0.2 s
    assert segments[-1].end_ms >= 2600
    asyncio.run(prov.aclose())


def test_speechmatics_batch_job_error_and_auth():
    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "job-1"})
        if request.method == "PATCH":
            return httpx.Response(202, json={})
        return httpx.Response(
            200, json={"job_status": "error", "status": {"reason": "audio format invalid"}}
        )

    prov = _provider(failing_handler)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(prov.transcribe(STTRequest(audio=pcm_speech(100))))
    assert "audio format invalid" in str(exc.value)

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"details": "bad auth token"})

    prov2 = _provider(denied)
    with pytest.raises(ProviderError) as exc2:
        asyncio.run(prov2.transcribe(STTRequest(audio=pcm_speech(100))))
    assert exc2.value.status_hint == 401


def test_speechmatics_urls_and_health():
    prov = _provider(region="us")
    assert prov._batch_url == "https://us.asr.speechmatics.com/v2"
    assert prov._rt_url == "wss://us.rt.speechmatics.com/v2/stream/ws"

    def ok(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"account": {"member_id": 1}})

    health = asyncio.run(_provider(ok).health())
    assert health.ok

    def dead(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    h2 = asyncio.run(_provider(dead).health())
    assert not h2.ok


# ---- realtime WS ---------------------------------------------------------------

RT_MESSAGES = [
    {"message_type": "RecognitionStart", "request_id": "r", "id": "s1"},
    {
        "message_type": "RecognitionResult",
        "eof": False,
        "transcript": "به نظر می‌رسد",
        "results": [
            {
                "is_final": False,
                "start_time": 0.1,
                "end_time": 1.0,
                "message_response": [{"type": "AddTranscript", "alternatives": [{"value": " به نظر"}]}],
            }
        ],
    },
    {
        "message_type": "RecognitionResult",
        "eof": False,
        "transcript": "",
        "results": [
            {
                "is_final": True,
                "start_time": 0.1,
                "end_time": 2.4,
                "confidence": 0.91,
                "message_response": [
                    {"type": "AddTranscript", "alternatives": [{"value": " بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است."}]}
                ],
            }
        ],
    },
    {
        "message_type": "RecognitionResult",
        "eof": True,
        "results": [
            {
                "is_final": True,
                "start_time": 2.6,
                "end_time": 4.2,
                "message_response": [
                    {"type": "AddTranscript", "alternatives": [{"value": " دوز 40 mg furosemide."}]}
                ],
            }
        ],
    },
    {"message_type": "EndOfStream"},
]


def test_speechmatics_realtime_stream_protocol():
    transport = ScriptedTransport(RT_MESSAGES)
    connector = scripted_connector(transport)
    prov = SpeechmaticsProvider(
        {"api_key": "sm-key", "language": "fa", "operating_domain": "general"},
        client=_client(lambda r: httpx.Response(200)),
        transport_factory=connector,
    )
    chunks = [pcm_speech(100), pcm_speech(100), pcm_silence(100)]

    async def run():
        return await collect(prov.stream(agen(chunks)))

    events = asyncio.run(run())
    # handshake
    first = json.loads(transport.sent[0][1])
    assert first["message_type"] == "StartRecognition"
    assert first["audio_format"] == "pcm_s16le"
    assert first["sampling_rate"] == 16000
    assert first["operating_params"]["language"] == "fa"
    assert first["enable_partials"] is True
    # audio frames then end-of-stream control
    assert [k for k, _ in transport.sent] == ["text"] + ["bytes"] * 3 + ["text"]
    assert json.loads(transport.sent[-1][1]) == {"message_type": "EndOfStream"}
    assert connector.captured["headers"]["Authorization"] == "Bearer sm-key"
    assert connector.captured["url"] == "wss://eu2.rt.speechmatics.com/v2/stream/ws"

    assert [e.is_final for e in events] == [False, True, True]
    assert events[0].text == "به نظر"
    assert "ECG" in events[1].text and "بدون" in events[1].text
    assert events[1].confidence == 0.91
    assert events[-1].text == "دوز 40 mg furosemide."
    assert events[-1].start_ms == 2600 and events[-1].end_ms == 4200
    assert transport.closed
    asyncio.run(prov.aclose())


def test_speechmatics_recognition_failed_maps_to_unavailable():
    transport = ScriptedTransport(
        [
            {"message_type": "RecognitionStart"},
            {"message_type": "RecognitionFailed", "reason": "language not supported"},
        ]
    )
    prov = SpeechmaticsProvider(
        {"api_key": "sm-key"},
        client=_client(lambda r: httpx.Response(200)),
        transport_factory=scripted_connector(transport),
    )

    async def run():
        return await collect(prov.stream(agen([pcm_speech(100)])))

    with pytest.raises(ProviderUnavailableError) as exc:
        asyncio.run(run())
    assert "language not supported" in str(exc.value)
    assert transport.closed


def test_stream_without_ack_is_failure():
    transport = ScriptedTransport([{"message_type": "RecognitionFailed", "reason": "quota exceeded"}])
    prov = SpeechmaticsProvider(
        {"api_key": "sm-key"},
        client=_client(lambda r: httpx.Response(200)),
        transport_factory=scripted_connector(transport),
    )

    async def run():
        return await collect(prov.stream(agen([])))

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(run())
