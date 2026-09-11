"""OpenAI / Anthropic / Gemini adapter tests + llama-server /props discovery.
httpx.MockTransport only — wire shapes verified against recorded payloads."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ai.base import LLMMessage, ProviderError, ProviderUnavailableError
from ai.llm.anthropic import AnthropicProvider
from ai.llm.gemini import GeminiProvider
from ai.llm.llama_server import LlamaServerProvider
from ai.llm.openai import OpenAIProvider

MSGS = [
    LLMMessage(role="system", content="be terse"),
    LLMMessage(role="user", content="draft a note"),
    LLMMessage(role="assistant", content="ok"),
    LLMMessage(role="user", content="now really"),
]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---- openai (via shared compat transport) --------------------------------------


def test_openai_complete_usage_and_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini-2024",
                "choices": [
                    {"message": {"role": "assistant", "content": '{"sections": {}}'},
                     "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            },
        )

    prov = OpenAIProvider(
        {"base_url": "https://api.openai.com", "model": "gpt-4o-mini", "api_key": "sk-x"},
        client=_client(handler),
    )
    out = asyncio.run(
        prov.complete(MSGS, temperature=0.1, max_tokens=90, json_mode=True)
    )
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-x"
    body = seen["body"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 90
    assert body["messages"][0] == {"role": "system", "content": "be terse"}
    assert out.usage.total_tokens == 150
    assert out.meta["finish_reason"] == "stop"
    assert out.model == "gpt-4o-mini-2024"


def test_openai_401_not_retryable():
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Incorrect API key"}})

    prov = OpenAIProvider({"base_url": "https://api.openai.com", "model": "m", "api_key": "k"}, client=_client(handler))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(prov.complete(MSGS))
    assert exc.value.retryable is False


# ---- anthropic -------------------------------------------------------------------


def test_anthropic_payload_mapping_and_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "model": "claude-sonnet-4-20250514",
                "content": [
                    {"type": "thinking", "thinking": "ignored"},
                    {"type": "text", "text": '{"sections": {}}'},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 55, "output_tokens": 12},
            },
        )

    prov = AnthropicProvider({"api_key": "sk-ant", "model": "claude-sonnet-4-20250514"}, client=_client(handler))
    out = asyncio.run(prov.complete(MSGS, json_mode=True, max_tokens=200))
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    body = seen["body"]
    # system extracted from the turn list
    assert "be terse" in body["system"] and "STRICT JSON" in body["system"]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user"]  # adjacent same-role merged
    assert all(isinstance(m["content"], list) for m in body["messages"])
    assert out.usage.prompt_tokens == 55 and out.usage.completion_tokens == 12
    assert out.text == '{"sections": {}}'
    assert out.meta["stop_reason"] == "end_turn"


def test_anthropic_error_mapping_and_retry():
    calls = {"n": 0}

    def overloaded(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": {"type": "rate_limit", "message": "slow down"}})

    prov = AnthropicProvider(
        {"api_key": "k", "max_retries": 1, "backoff_base_s": 0.0},
        client=_client(overloaded),
    )
    with pytest.raises(ProviderUnavailableError) as exc:
        asyncio.run(prov.complete(MSGS))
    assert "rate_limit" in str(exc.value)
    assert calls["n"] == 2  # initial + 1 retry

    def bad_key(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    with pytest.raises(ProviderError) as exc2:
        asyncio.run(
            AnthropicProvider({"api_key": "k", "max_retries": 0}, client=_client(bad_key)).complete(MSGS)
        )
    assert exc2.value.retryable is False
    assert exc2.value.status_hint == 401


# ---- gemini ------------------------------------------------------------------------


def test_gemini_payload_roles_and_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "A"}, {"text": "B"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4, "totalTokenCount": 14},
            },
        )

    prov = GeminiProvider({"api_key": "g-key", "model": "gemini-2.0-flash"}, client=_client(handler))
    out = asyncio.run(prov.complete(MSGS, json_mode=True, max_tokens=300))
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    assert seen["headers"]["x-goog-api-key"] == "g-key"
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "be terse"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["maxOutputTokens"] == 300
    assert out.text == "AB"
    assert out.usage.total_tokens == 14
    assert out.meta["finish_reason"] == "STOP"


def test_gemini_error_status_detail_and_health():
    def blocked(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "permission denied", "status": "PERMISSION_DENIED"}})

    prov = GeminiProvider({"api_key": "k"}, client=_client(blocked))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(prov.complete(MSGS))
    assert "PERMISSION_DENIED" in str(exc.value)
    assert exc.value.status_hint == 403

    def ok(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    health = asyncio.run(GeminiProvider({"api_key": "k"}, client=_client(ok)).health())
    assert health.ok


# ---- llama-server: /props discovery ------------------------------------------------


def test_llama_props_discovery_cached():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            calls["n"] += 1
            return httpx.Response(200, json={"model_path": "/models/qwen.Q4_K_M.gguf", "n_ctx": 8192})
        return httpx.Response(404)

    prov = LlamaServerProvider({"base_url": "http://llm:8080"}, client=_client(handler))
    props = asyncio.run(prov.props())
    assert props["n_ctx"] == 8192
    asyncio.run(prov.props())
    assert calls["n"] == 1  # cached


def test_llm_provider_registry_membership():
    from ai.base import ProviderKind
    from ai.registry import build_default_registry

    reg = build_default_registry()
    llm_names = {d.name for d in reg.descriptors(ProviderKind.LLM)}
    assert {"mock", "llama-server", "openai", "anthropic", "gemini"} <= llm_names
    assert reg.is_configured(ProviderKind.LLM, "openai", {"api_key": "sk"})
    assert not reg.is_configured(ProviderKind.LLM, "anthropic", {})
    caps = reg.get_descriptor(ProviderKind.LLM, "gemini").capabilities
    from ai.base import PrivacyClass

    assert caps.privacy is PrivacyClass.CLOUD
