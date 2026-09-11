"""llama-server / OpenAI-compatible client tests using httpx.MockTransport —
real request shapes, zero network. Retry budget, SSE parsing and health probe
are all covered here because Phase 4+ builds directly on this client."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ai.base import LLMMessage, ProviderError, ProviderUnavailableError
from ai.llm.llama_server import LlamaServerProvider, llama_server_configured
from ai.llm.openai_compat import normalize_base_url


def provider(handler, **cfg) -> LlamaServerProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    base = {"base_url": "http://llm:8080", "model": "qwen2.5", "backoff_base_s": 0.001}
    base.update(cfg)
    return LlamaServerProvider(base, client=client)


def messages() -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="draft clinical note"),
        LLMMessage(role="user", content="transcript text"),
    ]


def test_normalize_base_url_forms():
    assert normalize_base_url("llm-host:8080/") == "http://llm-host:8080"
    assert normalize_base_url("https://h:8080/v1") == "https://h:8080"
    assert normalize_base_url("https://h/v1/chat/completions") == "https://h"
    with pytest.raises(ValueError):
        normalize_base_url("")


def test_configured_predicate_requires_url():
    assert llama_server_configured({}) is False
    assert llama_server_configured({"base_url": "http://x"}) is True


def test_complete_sends_openai_shape_and_parses_response():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5",
                "choices": [{"message": {"role": "assistant", "content": "OK done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
            },
        )

    result = asyncio.run(provider(handler).complete(messages(), max_tokens=256))
    assert result.text == "OK done"
    assert result.usage.total_tokens == 14
    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] is None  # llama-server typically has no auth
    assert seen["body"]["model"] == "qwen2.5"
    assert seen["body"]["max_tokens"] == 256
    assert seen["body"]["stream"] is False
    assert seen["body"]["messages"][0]["role"] == "system"


def test_json_mode_and_api_key_forwarded():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    p = provider(handler, api_key="sk-test")
    asyncio.run(p.complete(messages(), json_mode=True))


def test_retry_then_success_on_503():
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(503, text="model not loaded yet")
        return httpx.Response(200, json={"choices": [{"message": {"content": "up"}}]})

    result = asyncio.run(provider(handler, max_retries=3).complete(messages()))
    assert result.text == "up"
    assert state["n"] == 3


def test_retry_budget_exhausted_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down " + "x" * 400)

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider(handler, max_retries=1).complete(messages()))


def test_permanent_4xx_not_retried():
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    with pytest.raises(ProviderError):
        asyncio.run(provider(handler, max_retries=5).complete(messages()))
    assert state["n"] == 1  # 4xx must not consume the retry budget


def test_stream_parses_sse_deltas_until_done():
    sse = (
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        b"data: \n"  # keepalive noise is tolerated
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    async def run() -> str:
        chunks = []
        async for delta in provider(handler).stream(messages()):
            chunks.append(delta)
        return "".join(chunks)

    assert asyncio.run(run()) == "Hello"


def test_health_probe_maps_llama_server_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200)

    health = asyncio.run(provider(handler).health())
    assert health.ok is True
    assert health.latency_ms is not None


def test_health_probe_tolerates_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    health = asyncio.run(provider(handler).health())
    assert health.ok is False
    assert "ConnectError" in (health.detail or "")


def test_error_message_redacts_never_leaks_full_bodies():
    # provider error messages are truncated; bodies must not balloon into logs
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="y" * 5000)

    with pytest.raises(ProviderUnavailableError) as exc:
        asyncio.run(provider(handler, max_retries=0).complete(messages()))
    assert len(str(exc.value)) < 1200
