"""9Router adapter tests — LLM (chat/completions) and STT (audio/transcriptions).

9Router (github.com/decolua/9router) is a self-hosted proxy fronting 40+
upstreams behind one OpenAI-compatible API under ``/api/v1``. HTTP is mocked
with ``httpx.MockTransport``: real request shapes, zero network. Focus is on
the things that differ from a plain OpenAI-compatible endpoint — the ``/api``
route prefix, ``provider/model`` identifiers, the dynamic model catalog, and
optional Bearer auth (9Router only enforces a key when ``REQUIRE_API_KEY=true``).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from providers_support import agen, collect, pcm_silence, pcm_speech

from ai.base import (
    LLMMessage,
    PrivacyClass,
    ProviderError,
    ProviderUnavailableError,
    STTRequest,
)
from ai.llm.nine_router import NineRouterProvider, nine_router_configured
from ai.nine_router_client import (
    DEFAULT_API_PREFIX,
    normalize_nine_router_base_url,
    parse_models_payload,
    resolve_model_id,
)
from ai.stt.nine_router import NineRouterSttProvider, nine_router_stt_configured

BASE = "http://9router.test:20128"
API = f"{BASE}{DEFAULT_API_PREFIX}"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _llm(handler, **cfg) -> NineRouterProvider:
    cfg.setdefault("base_url", BASE)
    cfg.setdefault("model", "claude/claude-sonnet-4")
    cfg.setdefault("backoff_base_s", 0.001)
    return NineRouterProvider(cfg, client=_client(handler))


def _stt(handler, **cfg) -> NineRouterSttProvider:
    cfg.setdefault("base_url", BASE)
    cfg.setdefault("model", "groq/whisper-large-v3-turbo")
    return NineRouterSttProvider(cfg, client=_client(handler))


def messages() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="draft a clinical note"),
        LLMMessage(role="user", content="بیمار با درد قفسه سینه مراجعه کرد"),
    ]


# ---- base URL normalization ----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "9router.test:20128",
        "http://9router.test:20128",
        "http://9router.test:20128/",
        "http://9router.test:20128/v1",
        "http://9router.test:20128/api",
        "http://9router.test:20128/api/",
        "http://9router.test:20128/api/v1",
        "http://9router.test:20128/api/v1/chat/completions",
    ],
)
def test_base_url_forms_fold_onto_api_prefix(raw):
    """9Router's OpenAI-compatible surface lives at /api/v1/… — every plausible
    operator spelling must land on the same base."""
    assert normalize_nine_router_base_url(raw) == API


def test_base_url_prefix_override_and_default():
    # a reverse proxy that already rewrites /v1/* onto 9Router
    assert normalize_nine_router_base_url(BASE, "") == BASE
    assert normalize_nine_router_base_url(BASE, "gateway") == f"{BASE}/gateway"
    # empty input falls back to 9Router's own default port
    assert normalize_nine_router_base_url("") == f"http://127.0.0.1:20128{DEFAULT_API_PREFIX}"


def test_configured_predicates_require_base_url_only():
    """The API key is optional: 9Router serves unauthenticated requests unless
    REQUIRE_API_KEY=true, so requiring one would block a valid setup."""
    assert nine_router_configured({}) is False
    assert nine_router_configured({"base_url": BASE}) is True
    assert nine_router_stt_configured({}) is False
    assert nine_router_stt_configured({"base_url": BASE}) is True


def test_missing_base_url_is_a_clear_error():
    with pytest.raises(ProviderError) as exc:
        NineRouterProvider({"model": "claude/x"})
    assert "MS_LLM__NINE_ROUTER__BASE_URL" in str(exc.value)
    with pytest.raises(ProviderError) as exc2:
        NineRouterSttProvider({})
    assert "MS_STT__NINE_ROUTER__BASE_URL" in str(exc2.value)


def test_privacy_class_defaults_local_and_is_switchable():
    """9Router normally runs inside the deployment boundary, so it must be
    eligible for privacy-required encounters by default."""
    local = NineRouterProvider({"base_url": BASE, "model": "claude/x"})
    assert local.capabilities.privacy is PrivacyClass.LOCAL
    assert local.capabilities.is_local

    cloud = NineRouterProvider(
        {"base_url": BASE, "model": "claude/x", "privacy_class": "cloud"}
    )
    assert cloud.capabilities.privacy is PrivacyClass.CLOUD

    stt_local = NineRouterSttProvider({"base_url": BASE, "model": "groq/whisper"})
    assert stt_local.capabilities.privacy is PrivacyClass.LOCAL
    stt_cloud = NineRouterSttProvider(
        {"base_url": BASE, "model": "groq/whisper", "privacy_class": "cloud"}
    )
    assert stt_cloud.capabilities.privacy is PrivacyClass.CLOUD

    # an unrecognized value must not silently become "cloud" and drop out of
    # privacy-required routing
    odd = NineRouterProvider({"base_url": BASE, "model": "m/m", "privacy_class": "on-prem"})
    assert odd.capabilities.privacy is PrivacyClass.LOCAL


# ---- LLM: chat completions -----------------------------------------------------


def test_llm_complete_hits_api_v1_and_parses_openai_shape():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "claude/claude-sonnet-4",
                "choices": [
                    {"message": {"role": "assistant", "content": "S: درد قفسه سینه"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 9, "total_tokens": 49},
            },
        )

    prov = _llm(handler, api_key="9r-secret")
    result = asyncio.run(prov.complete(messages(), temperature=0.1, max_tokens=900))

    assert seen["url"] == f"{API}/v1/chat/completions"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer 9r-secret"
    assert seen["body"]["model"] == "claude/claude-sonnet-4"
    assert seen["body"]["temperature"] == 0.1
    assert seen["body"]["max_tokens"] == 900
    assert seen["body"]["stream"] is False
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]

    assert result.text == "S: درد قفسه سینه"
    assert result.usage is not None and result.usage.total_tokens == 49
    assert result.meta["finish_reason"] == "stop"
    asyncio.run(prov.aclose())


def test_llm_works_without_an_api_key():
    """No Authorization header at all when no key is configured."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    prov = _llm(handler)
    asyncio.run(prov.complete(messages()))
    assert seen["auth"] is None
    asyncio.run(prov.aclose())


def test_llm_per_call_model_overrides_configured_default():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    prov = _llm(handler, model="claude/default")
    asyncio.run(prov.complete(messages(), model="gemini/gemini-2.5-flash"))
    assert seen["model"] == "gemini/gemini-2.5-flash"
    asyncio.run(prov.aclose())


def test_llm_json_mode_sets_response_format():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
        )

    prov = _llm(handler)
    asyncio.run(prov.complete(messages(), json_mode=True))
    assert seen["body"]["response_format"] == {"type": "json_object"}
    asyncio.run(prov.aclose())


def test_llm_requires_provider_slash_model_id():
    """9Router selects the upstream from the model id itself; an empty id is a
    configuration fault worth naming, not an opaque 400 from the proxy."""
    prov = _llm(lambda r: httpx.Response(200), model="")
    with pytest.raises(ProviderError) as exc:
        asyncio.run(prov.complete(messages()))
    assert "provider/model" in str(exc.value)
    assert "MS_LLM__NINE_ROUTER__MODEL" in str(exc.value)

    assert resolve_model_id("claude/x", None) == "claude/x"
    assert resolve_model_id("claude/x", "gemini/y") == "gemini/y"
    with pytest.raises(ProviderError):
        resolve_model_id("", "  ")


def test_llm_stream_parses_sse_deltas():
    sse = "".join(
        f"data: {json.dumps(chunk)}\n\n"
        for chunk in (
            {"choices": [{"delta": {"content": "S: "}}]},
            {"choices": [{"delta": {"content": "درد "}}]},
            {"choices": [{"delta": {"content": "قفسه سینه"}}]},
            {"choices": [{"delta": {}}]},
        )
    ) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, content=sse.encode(), headers={"Content-Type": "text/event-stream"}
        )

    prov = _llm(handler)
    deltas = asyncio.run(collect(prov.stream(messages())))
    assert "".join(deltas) == "S: درد قفسه سینه"
    asyncio.run(prov.aclose())


def test_llm_streaming_disabled_falls_back_to_one_chunk():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "whole note"}, "finish_reason": "stop"}]}
        )

    prov = _llm(handler, streaming=False)
    assert prov.capabilities.supports_streaming is False
    deltas = asyncio.run(collect(prov.stream(messages())))
    assert deltas == ["whole note"]
    asyncio.run(prov.aclose())


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, ProviderUnavailableError), (500, ProviderUnavailableError), (503, ProviderUnavailableError)],
)
def test_llm_transient_status_is_retryable(status, expected):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(status)
        return httpx.Response(status, json={"error": {"message": "all accounts rate limited"}})

    prov = _llm(handler, max_retries=2)
    with pytest.raises(expected):
        asyncio.run(prov.complete(messages()))
    assert len(calls) == 3  # 1 attempt + 2 retries
    asyncio.run(prov.aclose())


def test_llm_bad_key_is_not_retryable():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    prov = _llm(handler, api_key="wrong")
    with pytest.raises(ProviderError) as exc:
        asyncio.run(prov.complete(messages()))
    assert not isinstance(exc.value, ProviderUnavailableError)
    assert len(calls) == 1  # a bad key never improves on retry
    asyncio.run(prov.aclose())


def test_llm_unreachable_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    prov = _llm(handler)
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(prov.complete(messages()))
    asyncio.run(prov.aclose())


# ---- health --------------------------------------------------------------------


def test_llm_health_ok_and_reports_empty_catalog():
    def healthy(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        return httpx.Response(200, json={"object": "list", "data": [{"id": "claude/x"}]})

    h = asyncio.run(_llm(healthy).health())
    assert h.ok and h.detail is None

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": []})

    h2 = asyncio.run(_llm(empty).health())
    # reachable but unusable — the operator needs to know why drafting fails
    assert h2.ok
    assert "no upstream accounts" in (h2.detail or "")


def test_llm_health_failure_modes():
    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    h = asyncio.run(_llm(denied).health())
    assert not h.ok and "MS_LLM__NINE_ROUTER__API_KEY" in (h.detail or "")

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    h2 = asyncio.run(_llm(dead).health())
    assert not h2.ok and "ConnectError" in (h2.detail or "")


def test_stt_health_uses_stt_catalog():
    def healthy(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models/stt"
        return httpx.Response(200, json={"object": "list", "data": [{"id": "groq/whisper-large-v3-turbo"}]})

    h = asyncio.run(_stt(healthy).health())
    assert h.ok and h.detail is None

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": []})

    h2 = asyncio.run(_stt(empty).health())
    assert h2.ok and "no STT upstream" in (h2.detail or "")

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    h3 = asyncio.run(_stt(denied).health())
    assert not h3.ok and "MS_STT__NINE_ROUTER__API_KEY" in (h3.detail or "")


# ---- model discovery -----------------------------------------------------------

CATALOG = {
    "object": "list",
    "data": [
        {
            "id": "claude/claude-sonnet-4",
            "object": "model",
            "owned_by": "claude",
            "context_length": 200000,
            "max_completion_tokens": 64000,
            "capabilities": {"tools": True, "vision": True},
        },
        {"id": "gemini/gemini-2.5-flash", "object": "model", "owned_by": "gemini"},
    ],
}


def test_llm_list_models_normalizes_catalog():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models"
        assert request.headers.get("authorization") == "Bearer k"
        return httpx.Response(200, json=CATALOG)

    models = asyncio.run(_llm(handler, api_key="k").list_models())
    assert [m["id"] for m in models] == ["claude/claude-sonnet-4", "gemini/gemini-2.5-flash"]
    assert models[0]["owned_by"] == "claude"
    assert models[0]["context_length"] == 200000
    assert models[0]["max_completion_tokens"] == 64000
    assert models[0]["capabilities"] == {"tools": True, "vision": True}
    assert models[1].get("context_length") is None


def test_stt_list_models_targets_stt_catalog():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/models/stt"
        return httpx.Response(
            200, json={"object": "list", "data": [{"id": "groq/whisper-large-v3-turbo"}]}
        )

    models = asyncio.run(_stt(handler).list_models())
    assert models == [{"id": "groq/whisper-large-v3-turbo", "kind": "stt", "owned_by": "groq"}]


def test_parse_models_payload_tolerates_upstream_shapes():
    """9Router's catalog reflects whatever its upstreams returned, so the parser
    accepts every shape it can produce instead of raising on an odd one."""
    assert parse_models_payload({"data": [{"id": "a/b"}]}, kind="llm")[0]["id"] == "a/b"
    assert parse_models_payload([{"id": "a/b"}], kind="llm")[0]["id"] == "a/b"
    assert parse_models_payload({"models": ["plain/string"]}, kind="llm")[0]["id"] == "plain/string"
    assert parse_models_payload({"results": [{"id": "x/y"}]}, kind="stt")[0]["kind"] == "stt"
    # an explicit kind on the entry wins over the requested one
    assert parse_models_payload({"data": [{"id": "p/search", "kind": "webSearch"}]}, kind="llm")[0][
        "kind"
    ] == "webSearch"
    # junk is dropped, never fatal
    assert parse_models_payload({"data": [{"no_id": 1}, 42, {"id": "  "}]}, kind="llm") == []
    assert parse_models_payload(None, kind="llm") == []
    assert parse_models_payload("nope", kind="llm") == []
    # owned_by is derived from the provider prefix when the router omits it
    assert parse_models_payload({"data": [{"id": "groq/whisper"}]}, kind="stt")[0][
        "owned_by"
    ] == "groq"


def test_list_models_error_mapping():
    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}})

    with pytest.raises(ProviderError) as exc:
        asyncio.run(_llm(denied, api_key="bad").list_models())
    assert not isinstance(exc.value, ProviderUnavailableError)

    def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="all accounts unavailable")

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(_llm(busy).list_models())

    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(_llm(dead).list_models())

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy login</html>")

    with pytest.raises(ProviderError):
        asyncio.run(_llm(html).list_models())


# ---- STT: transcriptions -------------------------------------------------------


def test_stt_transcribe_sends_whisper_multipart_to_api_v1():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = request.content
        seen["ctype"] = request.headers.get("content-type", "")
        return httpx.Response(
            200,
            json={
                "text": "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است.",
                "language": "fa",
                "duration": 4.2,
            },
        )

    prov = _stt(handler, api_key="9r-secret", hotwords=["furosemide", "ECG"])
    segments = asyncio.run(
        prov.transcribe(
            STTRequest(audio=pcm_speech(4200), language="fa-en", context_hints=("aspirin",))
        )
    )

    assert seen["url"] == f"{API}/v1/audio/transcriptions"
    assert seen["method"] == "POST"
    assert seen["ctype"].startswith("multipart/form-data")
    body = seen["body"]
    # raw PCM is wrapped in a WAV container before upload
    assert b"RIFF" in body and b'filename="audio.wav"' in body
    for field in (b'name="model"', b'name="file"', b'name="language"', b'name="prompt"'):
        assert field in body
    assert b"groq/whisper-large-v3-turbo" in body
    assert b"verbose_json" in body  # asks OpenAI-compatible upstreams for timings
    assert b"fa" in body  # fa-en mixed mapped to fa
    assert b"furosemide" in body and b"aspirin" in body  # hotwords + hints → prompt

    assert len(segments) == 1
    assert "ECG" in segments[0].text and "بدون" in segments[0].text
    assert segments[0].start_ms == 0 and segments[0].end_ms == 4200  # duration → ms
    assert segments[0].is_final
    asyncio.run(prov.aclose())


def test_stt_transcribe_parses_verbose_json_segments():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "دوز 40 mg furosemide. بدون effusion.",
                "language": "fa",
                "segments": [
                    {"id": 0, "start": 0.2, "end": 2.6, "text": "دوز 40 mg furosemide."},
                    {"id": 1, "start": 2.8, "end": 4.4, "text": "بدون effusion."},
                ],
            },
        )

    segments = asyncio.run(_stt(handler).transcribe(STTRequest(audio=pcm_speech(4400))))
    assert [(s.start_ms, s.end_ms) for s in segments] == [(200, 2600), (2800, 4400)]
    assert segments[0].text == "دوز 40 mg furosemide."
    assert all(s.is_final for s in segments)


def test_stt_transcribe_falls_back_to_plain_text():
    """9Router's Deepgram/Gemini/Nvidia/HuggingFace relays always answer
    {"text": …} regardless of response_format — that must still transcribe."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "فقط متن ساده."})

    segments = asyncio.run(
        _stt(handler).transcribe(STTRequest(audio=pcm_speech(2000), language="fa"))
    )
    assert len(segments) == 1
    assert segments[0].text == "فقط متن ساده."
    assert segments[0].end_ms == 2000  # derived from the PCM buffer length


@pytest.mark.parametrize("key", ["transcript", "transcription"])
def test_stt_transcribe_accepts_alternate_text_keys(key):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={key: "متن جایگزین"})

    segments = asyncio.run(_stt(handler).transcribe(STTRequest(audio=pcm_speech(1000))))
    assert segments[0].text == "متن جایگزین"


def test_stt_transcribe_empty_result_yields_no_segments():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "", "segments": []})

    assert asyncio.run(_stt(handler).transcribe(STTRequest(audio=pcm_silence(500)))) == []


def test_stt_wav_input_is_not_re_wrapped():
    from providers_support import make_wav

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ok"})

    wav = make_wav(pcm_speech(300))
    asyncio.run(
        _stt(handler).transcribe(STTRequest(audio=wav, encoding="wav", sample_rate=16000))
    )
    # exactly one RIFF header — the container was passed through untouched
    assert seen["body"].count(b"RIFF") == 1


def test_stt_language_auto_detect_omits_field():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ok"})

    asyncio.run(_stt(handler).transcribe(STTRequest(audio=pcm_speech(300), language=None)))
    assert b'name="language"' not in seen["body"]


def test_stt_response_format_is_configurable():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ok"})

    asyncio.run(
        _stt(handler, response_format="json").transcribe(STTRequest(audio=pcm_speech(300)))
    )
    assert b"verbose_json" not in seen["body"]
    assert b'name="response_format"' in seen["body"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderError),
        (403, ProviderError),
        (400, ProviderError),
        (429, ProviderUnavailableError),
        (500, ProviderUnavailableError),
        # 9Router answers 503 when every connected account is rate-limited
        (503, ProviderUnavailableError),
    ],
)
def test_stt_error_mapping(status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "upstream refused"}})

    prov = _stt(handler, api_key="k" if status in (401, 403) else None)
    with pytest.raises(expected) as exc:
        asyncio.run(prov.transcribe(STTRequest(audio=pcm_speech(300))))
    if status in (401, 403):
        assert not isinstance(exc.value, ProviderUnavailableError)
        assert exc.value.status_hint == status
        assert "MS_STT__NINE_ROUTER__API_KEY" in str(exc.value)


def test_stt_unreachable_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderUnavailableError) as exc:
        asyncio.run(_stt(handler).transcribe(STTRequest(audio=pcm_speech(300))))
    assert "9router unreachable" in str(exc.value)


def test_stt_non_json_response_is_a_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with pytest.raises(ProviderError):
        asyncio.run(_stt(handler).transcribe(STTRequest(audio=pcm_speech(300))))


# ---- STT: VAD-windowed pseudo-streaming ----------------------------------------


def test_stt_stream_yields_interims_then_finals_on_session_timeline():
    """9Router's STT is request/response, so live dictation cuts utterances with
    the shared VAD and shifts each result onto the session timeline."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(len(calls))
        return httpx.Response(200, json={"text": f"segment {len(calls)}"})

    prov = _stt(
        handler,
        vad_silence_ms=300,
        vad_interim_ms=400,
        vad_threshold=0.02,
        vad_pre_roll_ms=0,
    )
    chunks = [pcm_speech(200) for _ in range(5)] + [pcm_silence(400)]
    events = asyncio.run(collect(prov.stream(agen(chunks))))

    assert calls, "the VAD must have dispatched at least one utterance"
    assert any(not e.is_final for e in events), "long speech should produce interims"
    assert events[-1].is_final, "trailing silence closes the utterance"
    # every event sits inside the 1400 ms of audio that was fed in
    assert all(0 <= e.start_ms <= e.end_ms <= 1400 for e in events)
    assert all(e.text.startswith("segment ") for e in events)
    asyncio.run(prov.aclose())


def test_stt_stream_silence_only_emits_nothing():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("silence must not reach the provider")

    prov = _stt(handler)
    events = asyncio.run(collect(prov.stream(agen([pcm_silence(500)]))))
    assert events == []
    asyncio.run(prov.aclose())


def test_stt_stream_propagates_provider_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    prov = _stt(handler, api_key="bad")
    with pytest.raises(ProviderError):
        asyncio.run(collect(prov.stream(agen([pcm_speech(2000), pcm_silence(800)]))))
    asyncio.run(prov.aclose())


# ---- registry wiring -----------------------------------------------------------


def test_providers_registered_under_both_kinds():
    """9Router serves chat *and* transcription over one base URL, so it must be
    routable as both an LLM and an STT provider."""
    from ai.base import ProviderKind
    from ai.registry import build_default_registry

    registry = build_default_registry()
    stt_names = {d.name for d in registry.descriptors(ProviderKind.STT)}
    llm_names = {d.name for d in registry.descriptors(ProviderKind.LLM)}
    assert "9router" in stt_names and "9router" in llm_names

    stt_d = registry.get_descriptor(ProviderKind.STT, "9router")
    llm_d = registry.get_descriptor(ProviderKind.LLM, "9router")
    assert stt_d.supports_model_discovery and llm_d.supports_model_discovery
    assert stt_d.capabilities.privacy is PrivacyClass.LOCAL
    assert llm_d.capabilities.privacy is PrivacyClass.LOCAL
    assert stt_d.capabilities.supports_streaming and stt_d.capabilities.supports_batch
    assert llm_d.capabilities.supports_streaming

    # unconfigured → visible in listings but never routable
    assert registry.is_configured(ProviderKind.LLM, "9router", {}) is False
    assert registry.is_configured(ProviderKind.LLM, "9router", {"base_url": BASE}) is True

    built = registry.create(
        ProviderKind.LLM, "9router", {"base_url": BASE, "model": "claude/x"}, cache=False
    )
    assert isinstance(built, NineRouterProvider)
    assert built.base_url == API


def test_settings_expose_nine_router_slices(monkeypatch):
    """Env → Settings → registry factory dict, secrets unwrapped exactly once."""
    from api.config import Settings

    monkeypatch.setenv("MS_LLM__NINE_ROUTER__BASE_URL", BASE)
    monkeypatch.setenv("MS_LLM__NINE_ROUTER__MODEL", "claude/claude-sonnet-4")
    monkeypatch.setenv("MS_LLM__NINE_ROUTER__API_KEY", "llm-key")
    monkeypatch.setenv("MS_LLM__NINE_ROUTER__PRIVACY_CLASS", "cloud")
    monkeypatch.setenv("MS_STT__NINE_ROUTER__BASE_URL", BASE)
    monkeypatch.setenv("MS_STT__NINE_ROUTER__MODEL", "groq/whisper-large-v3-turbo")
    monkeypatch.setenv("MS_STT__NINE_ROUTER__API_KEY", "stt-key")
    monkeypatch.setenv('MS_STT__NINE_ROUTER__HOTWORDS', '["furosemide","warfarin"]')

    settings = Settings()
    llm_cfg = settings.provider_config("llm", "9router")
    assert llm_cfg["base_url"] == BASE
    assert llm_cfg["model"] == "claude/claude-sonnet-4"
    assert llm_cfg["api_key"] == "llm-key"
    assert llm_cfg["privacy_class"] == "cloud"
    assert llm_cfg["api_prefix"] == "/api"

    stt_cfg = settings.provider_config("stt", "9router")
    assert stt_cfg["base_url"] == BASE
    assert stt_cfg["model"] == "groq/whisper-large-v3-turbo"
    assert stt_cfg["api_key"] == "stt-key"
    assert stt_cfg["hotwords"] == ["furosemide", "warfarin"]
    assert stt_cfg["response_format"] == "verbose_json"
    assert stt_cfg["privacy_class"] == "local"

    # the factory dicts must build working adapters
    assert NineRouterProvider(llm_cfg).capabilities.privacy is PrivacyClass.CLOUD
    assert NineRouterSttProvider(stt_cfg).capabilities.privacy is PrivacyClass.LOCAL


def test_settings_expose_speechmatics_realtime_knobs(monkeypatch):
    from api.config import Settings

    monkeypatch.setenv("MS_STT__SPEECHMATICS__API_KEY", "sm-key")
    monkeypatch.setenv("MS_STT__SPEECHMATICS__REGION", "global")
    monkeypatch.setenv("MS_STT__SPEECHMATICS__MAX_DELAY", "2")
    monkeypatch.setenv("MS_STT__SPEECHMATICS__ENABLE_PARTIALS", "false")
    monkeypatch.setenv("MS_STT__SPEECHMATICS__RT_DOMAIN", "medical")
    monkeypatch.setenv('MS_STT__SPEECHMATICS__ADDITIONAL_VOCAB', '["furosemide"]')

    cfg = Settings().provider_config("stt", "speechmatics")
    assert cfg["region"] == "global"
    assert cfg["max_delay"] == 2.0
    assert cfg["enable_partials"] is False
    assert cfg["rt_domain"] == "medical"
    assert cfg["additional_vocab"] == ["furosemide"]

    from ai.stt.speechmatics import SpeechmaticsProvider

    prov = SpeechmaticsProvider(cfg)
    assert prov._rt_url == "wss://global.rt.speechmatics.com/v2"
    start = prov._start_recognition_message()
    assert start["transcription_config"]["domain"] == "medical"
    assert start["transcription_config"]["enable_partials"] is False
    assert start["transcription_config"]["additional_vocab"] == ["furosemide"]
