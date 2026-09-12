"""Speechmatics adapter tests — batch job lifecycle (HTTP MockTransport)
and realtime WS protocol (ScriptedTransport). Persian config, medical-token
preservation, error mapping.

The realtime assertions follow the published protocol
(docs.speechmatics.com/api-ref/realtime-transcription-websocket): ``message``
discriminator, object ``audio_format``, required nested ``transcription_config``,
``EndOfStream.last_seq_no``, and AddPartialTranscript / AddTranscript /
EndOfTranscript on the way back.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from providers_support import (
    BlockingTransport,
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
from ai.stt.ws_transport import ConnectionClosed

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
    # Batch and Realtime are separate address spaces at Speechmatics.
    assert prov._batch_url == "https://us.asr.speechmatics.com/v2"
    assert prov._rt_url == "wss://us.rt.speechmatics.com/v2"

    def ok(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"account": {"member_id": 1}})

    health = asyncio.run(_provider(ok).health())
    assert health.ok

    def dead(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    h2 = asyncio.run(_provider(dead).health())
    assert not h2.ok


def test_speechmatics_realtime_endpoints_per_region():
    """Realtime hosts are ``{region}.rt.speechmatics.com/v2``; ``global``
    auto-routes, and the legacy batch cluster names alias onto a public region
    rather than producing a host that does not exist."""
    expected = {
        "eu": "wss://eu.rt.speechmatics.com/v2",
        "us": "wss://us.rt.speechmatics.com/v2",
        "au": "wss://au.rt.speechmatics.com/v2",
        "global": "wss://global.rt.speechmatics.com/v2",
        "eu2": "wss://eu.rt.speechmatics.com/v2",
        "us2": "wss://us.rt.speechmatics.com/v2",
    }
    for region, url in expected.items():
        assert _provider(region=region)._rt_url == url, region
    # an explicit override always wins
    assert _provider(rt_url="wss://proxy.internal/v2")._rt_url == "wss://proxy.internal/v2"


# ---- realtime WS ---------------------------------------------------------------
#
# Message shapes below follow
# https://docs.speechmatics.com/api-ref/realtime-transcription-websocket
# exactly: the discriminator field is ``message``, ``audio_format`` is an
# object, every recognition knob lives in the required ``transcription_config``,
# and transcripts arrive as AddPartialTranscript / AddTranscript.


def _word(content: str, start: float, end: float, confidence: float = 0.9) -> dict:
    return {
        "type": "word",
        "start_time": start,
        "end_time": end,
        "alternatives": [{"content": content, "confidence": confidence, "language": "fa"}],
    }


def _punct(content: str, at: float, attaches_to: str = "previous") -> dict:
    return {
        "type": "punctuation",
        "start_time": at,
        "end_time": at,
        "attaches_to": attaches_to,
        "alternatives": [{"content": content, "confidence": 1.0}],
    }


def _transcript_message(
    name: str, transcript: str, start: float, end: float, results: list[dict] | None = None
) -> dict:
    return {
        "message": name,
        "format": "2.1",
        "metadata": {"start_time": start, "end_time": end, "transcript": transcript},
        "results": results if results is not None else [],
    }


RT_MESSAGES = [
    {"message": "RecognitionStarted", "id": "session-1", "language_pack_info": {"itn": True}},
    # interims carry no meaningful confidence (documented) and no metadata text
    {
        "message": "AddPartialTranscript",
        "format": "2.1",
        "metadata": {"start_time": 0.1, "end_time": 1.0, "transcript": "به نظر می‌رسد"},
        "results": [_word("به", 0.1, 0.4), _word("نظر", 0.4, 1.0)],
    },
    {
        "message": "AddTranscript",
        "format": "2.1",
        "metadata": {
            "start_time": 0.1,
            "end_time": 2.4,
            "transcript": "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است.",
        },
        "results": [
            _word("ECG", 1.8, 2.1, 0.94),
            _word("بدون", 2.1, 2.3, 0.88),
            _punct(".", 2.4),
        ],
    },
    {
        "message": "AddTranscript",
        "format": "2.1",
        "metadata": {"start_time": 2.6, "end_time": 4.2, "transcript": "دوز 40 mg furosemide."},
        "results": [_word("furosemide", 3.6, 4.1, 0.97), _punct(".", 4.2)],
    },
    {"message": "EndOfTranscript"},
]


def _rt_provider(messages: list, **cfg):
    cfg.setdefault("api_key", "sm-key")
    cfg.setdefault("language", "fa")
    transport = ScriptedTransport(messages)
    connector = scripted_connector(transport)
    prov = SpeechmaticsProvider(
        cfg, client=_client(lambda r: httpx.Response(200)), transport_factory=connector
    )
    return prov, transport, connector


def test_speechmatics_realtime_stream_protocol():
    prov, transport, connector = _rt_provider(RT_MESSAGES)
    chunks = [pcm_speech(100), pcm_speech(100), pcm_silence(100)]

    events = asyncio.run(collect(prov.stream(agen(chunks))))

    # -- handshake -----------------------------------------------------------
    start = json.loads(transport.sent[0][1])
    assert start["message"] == "StartRecognition"
    assert start["audio_format"] == {
        "type": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 16000,
    }
    tcfg = start["transcription_config"]
    assert tcfg["language"] == "fa"
    assert tcfg["enable_partials"] is True
    assert 0.7 <= tcfg["max_delay"] <= 4.0
    # the legacy flat layout must be gone — the API rejects it as invalid_config
    for legacy in ("message_type", "sampling_rate", "operating_params", "enable_partials"):
        assert legacy not in start

    # -- audio frames then EndOfStream --------------------------------------
    assert [k for k, _ in transport.sent] == ["text"] + ["bytes"] * 3 + ["text"]
    assert json.loads(transport.sent[-1][1]) == {
        "message": "EndOfStream",
        "last_seq_no": 3,
    }
    assert connector.captured["headers"]["Authorization"] == "Bearer sm-key"
    assert connector.captured["url"] == "wss://eu.rt.speechmatics.com/v2"

    # -- transcript events ---------------------------------------------------
    assert [e.is_final for e in events] == [False, True, True]
    assert events[0].text == "به نظر می‌رسد"
    assert events[0].confidence is None  # partials: documented as meaningless
    assert events[0].start_ms == 100 and events[0].end_ms == 1000

    assert "ECG" in events[1].text and "بدون" in events[1].text
    assert events[1].confidence == pytest.approx((0.94 + 0.88) / 2, abs=1e-4)
    assert events[1].start_ms == 100 and events[1].end_ms == 2400

    # embedded English drug name + dose survive untouched
    assert events[-1].text == "دوز 40 mg furosemide."
    assert events[-1].start_ms == 2600 and events[-1].end_ms == 4200
    assert transport.closed
    asyncio.run(prov.aclose())


def test_speechmatics_realtime_config_passthrough():
    """additional_vocab / domain / diarization land inside transcription_config,
    and max_delay is clamped to the documented 0.7–4 s window."""
    prov, transport, _ = _rt_provider(
        [{"message": "RecognitionStarted"}, {"message": "EndOfTranscript"}],
        additional_vocab=["furosemide", "warfarin", {"content": "میوکارد", "sounds_like": ["myocard"]}],
        rt_domain="medical",
        diarization="speaker",
        max_delay=12.0,
        rt_sample_rate=8000,
    )
    asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    tcfg = json.loads(transport.sent[0][1])["transcription_config"]
    assert tcfg["additional_vocab"] == [
        "furosemide",
        "warfarin",
        {"content": "میوکارد", "sounds_like": ["myocard"]},
    ]
    assert tcfg["domain"] == "medical"
    assert tcfg["diarization"] == "speaker"
    assert tcfg["max_delay"] == 4.0  # clamped, not sent out of range
    assert json.loads(transport.sent[0][1])["audio_format"]["sample_rate"] == 8000


def test_speechmatics_realtime_omits_neutral_optional_fields():
    """A default session must not send diarization/domain — "general" is not a
    realtime domain value and "none" is already the default."""
    prov, transport, _ = _rt_provider(
        [{"message": "RecognitionStarted"}, {"message": "EndOfTranscript"}],
        operating_domain="general",
    )
    asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    tcfg = json.loads(transport.sent[0][1])["transcription_config"]
    assert "domain" not in tcfg
    assert "diarization" not in tcfg
    assert "additional_vocab" not in tcfg


def test_speechmatics_realtime_assembles_from_results_when_metadata_text_absent():
    """metadata.transcript is the primary source; results[] is the fallback, and
    punctuation must glue per attaches_to (visible in Persian: a stray space
    around the ZWNJ changes the word)."""
    prov, _, _ = _rt_provider(
        [
            {"message": "RecognitionStarted"},
            {
                "message": "AddTranscript",
                "metadata": {"start_time": 0.0, "end_time": 1.5, "transcript": ""},
                "results": [
                    _word("درد", 0.0, 0.4),
                    _word("قفسه", 0.4, 0.9),
                    _word("سینه", 0.9, 1.3),
                    _punct("-", 1.3, attaches_to="both"),
                    _word("شدید", 1.3, 1.5),
                ],
            },
            {"message": "EndOfTranscript"},
        ]
    )
    events = asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    assert len(events) == 1
    assert events[0].text == "درد قفسه سینه-شدید"
    assert events[0].end_ms == 1500


def test_speechmatics_realtime_ignores_operational_messages():
    """Info / Warning / AudioAdded / EndOfUtterance carry no transcript and must
    not become segments or terminate the stream."""
    prov, _, _ = _rt_provider(
        [
            {"message": "RecognitionStarted"},
            {"message": "Info", "type": "recognition_quality", "reason": "broadcast", "quality": "broadcast"},
            {"message": "AudioAdded", "seq_no": 1},
            {"message": "Warning", "type": "duration_limit_exceeded", "reason": "too long", "duration_limit": 120},
            {"message": "EndOfUtterance", "metadata": {"start_time": 1.0, "end_time": 1.0}},
            _transcript_message("AddTranscript", "فقط یک جمله.", 0.2, 1.0),
            {"message": "EndOfTranscript"},
        ]
    )
    events = asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    assert [e.text for e in events] == ["فقط یک جمله."]
    assert events[0].is_final is True


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        ("invalid_language", ProviderError),
        ("invalid_config", ProviderError),
        ("not_authorised", ProviderError),
        ("quota_exceeded", ProviderUnavailableError),
        ("job_error", ProviderUnavailableError),
        ("idle_timeout", ProviderUnavailableError),
    ],
)
def test_speechmatics_realtime_error_message_mapping(error_type, expected):
    """In-band ``Error`` is always fatal for the stream; only the retryability
    differs — a config fault would fail identically on the next attempt."""
    prov, transport, _ = _rt_provider(
        [
            {"message": "RecognitionStarted"},
            {"message": "Error", "type": error_type, "reason": f"because {error_type}", "code": 4005},
        ]
    )
    with pytest.raises(expected) as exc:
        asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    assert error_type in str(exc.value)
    assert transport.closed


def test_speechmatics_realtime_auth_error_is_not_retryable():
    """not_authorised → ProviderError (retryable=False) with an HTTP-ish hint:
    a bad key fails identically on every retry, so the router should move on to
    another provider rather than hammer Speechmatics."""
    prov, _, _ = _rt_provider(
        [{"message": "Error", "type": "not_authorised", "reason": "bad key"}]
    )
    with pytest.raises(ProviderError) as exc:
        asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    assert not isinstance(exc.value, ProviderUnavailableError)
    assert exc.value.status_hint == 401


@pytest.mark.parametrize(
    ("close_code", "expected_retryable", "label"),
    [(4001, False, "not_authorised"), (4003, False, "not_allowed"), (4005, True, "quota_exceeded")],
)
def test_speechmatics_realtime_close_code_maps_to_provider_error(
    close_code, expected_retryable, label
):
    """A WS close carrying a documented error code maps onto the retryability
    contract instead of leaking a raw transport exception."""
    transport = ScriptedTransport([ConnectionClosed("closed", close_code=close_code)])
    prov = SpeechmaticsProvider(
        {"api_key": "sm-key", "language": "fa"},
        client=_client(lambda r: httpx.Response(200)),
        transport_factory=scripted_connector(transport),
    )
    with pytest.raises(ProviderError) as exc:
        asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    assert isinstance(exc.value, ProviderUnavailableError) is expected_retryable
    assert str(close_code) in str(exc.value)
    assert label in str(exc.value)
    assert transport.closed


def test_speechmatics_realtime_orderly_close_is_not_an_error():
    """A close with no code means the session ended normally — the adapter must
    finish the stream rather than report a failure."""
    transport = ScriptedTransport(
        [
            {"message": "RecognitionStarted"},
            _transcript_message("AddTranscript", "پایان.", 0.0, 0.8),
            ConnectionClosed("bye"),  # close_code is None
        ]
    )
    prov = SpeechmaticsProvider(
        {"api_key": "sm-key", "language": "fa"},
        client=_client(lambda r: httpx.Response(200)),
        transport_factory=scripted_connector(transport),
    )
    events = asyncio.run(collect(prov.stream(agen([pcm_speech(50)]))))
    assert [e.text for e in events] == ["پایان."]


def test_stream_without_started_ack_is_failure():
    """An Error in place of RecognitionStarted fails the handshake — including
    the quota case the docs say to retry after 5–10 s."""
    prov, transport, _ = _rt_provider(
        [{"message": "Error", "type": "quota_exceeded", "reason": "concurrent limit reached"}]
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        asyncio.run(collect(prov.stream(agen([]))))
    assert "concurrent limit reached" in str(exc.value)
    assert transport.closed


def test_speechmatics_realtime_idle_timeout_is_unavailable():
    """A server that goes quiet must surface as a retryable failure, not hang
    the dictation session forever."""
    transport = BlockingTransport([{"message": "RecognitionStarted"}])
    prov = SpeechmaticsProvider(
        {"api_key": "sm-key", "language": "fa", "rt_idle_timeout_s": 0.05},
        client=_client(lambda r: httpx.Response(200)),
        transport_factory=scripted_connector(transport),
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        asyncio.run(collect(prov.stream(agen([]))))
    assert "idle timeout" in str(exc.value)
    assert transport.closed
