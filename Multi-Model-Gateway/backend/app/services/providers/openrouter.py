"""Adapter for OpenRouter. It speaks the OpenAI wire protocol but rejects
non-standard sampling params, so extra_sampling is never forwarded. Usage is
requested via stream_options and read from the final usage chunk.
"""
from typing import AsyncIterator

from openai import AsyncOpenAI

from .base import LLMProvider, StreamChunk, ToolResponse, _tool_response_from_openai

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(LLMProvider):
    name = "openrouter"
    is_cloud = True

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url or OPENROUTER_BASE_URL,
            api_key=api_key,
            default_model=default_model,
        )
        self._client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

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
        kwargs: dict = dict(
            model=model,
            messages=messages,
            stream=True,
            temperature=temperature,
            stream_options={"include_usage": True},
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if stop:
            kwargs["stop"] = stop
        if top_p is not None:
            kwargs["top_p"] = top_p
        # extra_sampling ignored: OpenRouter rejects unknown params

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.usage is not None:
                # final chunk carries the usage object
                yield StreamChunk(
                    prompt_tokens=chunk.usage.prompt_tokens,
                    completion_tokens=chunk.usage.completion_tokens,
                )
                continue
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield StreamChunk(content=delta.content)

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
        # extra_sampling ignored: OpenRouter rejects unknown params

        response = await self._client.chat.completions.create(**kwargs)
        return _tool_response_from_openai(response)
