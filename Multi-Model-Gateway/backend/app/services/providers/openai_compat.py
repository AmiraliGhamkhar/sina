"""Adapter for any OpenAI-wire-compatible endpoint (LM Studio, Ollama, Groq,
vLLM, ...). Local by default; sampling params that aren't OpenAI spec are passed
via extra_body so local servers can still honor them. stream_options is
deliberately NOT passed — some LM Studio builds stop streaming token-by-token
when it is present.
"""
from typing import AsyncIterator

from openai import AsyncOpenAI

from .base import LLMProvider, StreamChunk, ToolResponse, _tool_response_from_openai

_DUMMY_KEY = "LM-STUDIO"


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"
    is_cloud = False

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        super().__init__(base_url=base_url, api_key=api_key, default_model=default_model)
        # OpenAI-wire endpoints expect the /v1 suffix. Normalize once here so
        # every caller (legacy env fallback, seeded rows, user-entered URLs)
        # gets a working endpoint; URLs that already contain /v1 anywhere are
        # left untouched.
        if base_url and "/v1" not in base_url:
            base_url = base_url.rstrip("/") + "/v1"
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or _DUMMY_KEY)

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None,
        stop: list[str] | None,
        top_p: float | None,
        extra_sampling: dict,
    ) -> AsyncIterator[StreamChunk]:
        kwargs: dict = dict(model=model, messages=messages, stream=True, temperature=temperature)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if stop:
            kwargs["stop"] = stop
        if top_p is not None:
            kwargs["top_p"] = top_p
        if extra_sampling:
            kwargs["extra_body"] = extra_sampling

        stream = await self._client.chat.completions.create(**kwargs)
        completion_tokens = 0
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                completion_tokens += 1
                yield StreamChunk(content=delta.content)
        # local servers don't report usage; estimate completion tokens by chunk count
        yield StreamChunk(completion_tokens=completion_tokens)

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None = None,
    ) -> str:
        kwargs: dict = dict(model=model, messages=messages, stream=False, temperature=temperature)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = await self._client.chat.completions.create(**kwargs)
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    async def chat_with_tools(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        tools: list[dict],
        tool_choice: str,
        max_tokens: int | None = None,
        extra_sampling: dict | None = None,
    ) -> ToolResponse:
        kwargs: dict = dict(
            model=model,
            messages=messages,
            stream=False,
            temperature=temperature,
            tools=tools,
            tool_choice=tool_choice,
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if extra_sampling:
            kwargs["extra_body"] = extra_sampling

        response = await self._client.chat.completions.create(**kwargs)
        return _tool_response_from_openai(response)
