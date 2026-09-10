"""Adapter for Anthropic. The anthropic SDK is lazy-imported inside __init__ so
the rest of the app works without it; calling methods on an unavailable adapter
raises RuntimeError with a clear message.
"""
from typing import AsyncIterator

from .base import LLMProvider, StreamChunk, ToolResponse


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    is_cloud = True

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        super().__init__(base_url=base_url, api_key=api_key, default_model=default_model)
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            self._unavailable = "anthropic package not installed"
            self._client = None
            return
        self._unavailable = None
        self._client = AsyncAnthropic(api_key=api_key)

    def _require_client(self):
        if self._unavailable:
            raise RuntimeError(self._unavailable)
        return self._client

    @staticmethod
    def _split_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Anthropic takes system as a top-level arg; only user/assistant turns
        go in the messages list. Unknown roles are dropped (best effort)."""
        system_parts: list[str] = []
        turns: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system" and content:
                system_parts.append(content)
            elif role in ("user", "assistant") and content:
                turns.append({"role": role, "content": content})
        system = "\n\n".join(system_parts) if system_parts else None
        return system, turns

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
        client = self._require_client()
        system, turns = self._split_messages(messages)
        kwargs: dict = dict(
            model=model,
            messages=turns,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens or 4096,  # Anthropic requires max_tokens
        )
        if system:
            kwargs["system"] = system
        if stop:
            kwargs["stop_sequences"] = stop
        if top_p is not None:
            kwargs["top_p"] = top_p
        # extra_sampling ignored: Anthropic has no generic extra-params channel

        stream = await client.messages.create(**kwargs)
        async for event in stream:
            if event.type == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is not None and getattr(delta, "type", None) == "text_delta":
                    text = getattr(delta, "text", None)
                    if text:
                        yield StreamChunk(content=text)
            elif event.type == "message_delta":
                usage = getattr(event, "usage", None)
                if usage is not None:
                    yield StreamChunk(
                        prompt_tokens=getattr(usage, "input_tokens", None),
                        completion_tokens=getattr(usage, "output_tokens", None),
                    )

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None = None,
    ) -> str:
        client = self._require_client()
        system, turns = self._split_messages(messages)
        kwargs: dict = dict(
            model=model,
            messages=turns,
            temperature=temperature,
            max_tokens=max_tokens or 4096,
        )
        if system:
            kwargs["system"] = system
        response = await client.messages.create(**kwargs)
        if not response.content:
            return ""
        return response.content[0].text

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
        # surface "anthropic package not installed" first when the SDK is
        # missing; otherwise tool calling is simply not implemented yet
        self._require_client()
        raise RuntimeError("tool calling not supported for anthropic provider yet")
