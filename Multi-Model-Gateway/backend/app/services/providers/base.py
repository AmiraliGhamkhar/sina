"""Uniform provider adapter protocol.

Each adapter wraps one provider family behind two methods: stream_chat (the
chat path) and complete (single non-streamed answer, used later by deep
research). Constructors take keyword args so the provider registry can build
them from DB rows.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class StreamChunk:
    """One unit of a streamed response. content deltas on every chunk; token
    usage (if the provider reports it) on the final chunk."""

    content: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class ToolCall:
    """A function-call request emitted by the model. arguments is the raw
    JSON string exactly as the model produced it."""

    id: str
    name: str
    arguments: str


@dataclass
class ToolResponse:
    """Non-streamed model reply that may carry tool calls. token fields stay
    None when the provider does not report usage."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _tool_response_from_openai(response) -> ToolResponse:
    """Map an OpenAI-wire non-streamed response (choices[0].message + usage)
    onto ToolResponse. Shared by every adapter that speaks the OpenAI wire
    protocol (openai_compat, openai, openrouter)."""
    if not response.choices:
        return ToolResponse()
    message = response.choices[0].message
    tool_calls = [
        ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
        for tc in (message.tool_calls or [])
    ]
    usage = response.usage
    return ToolResponse(
        content=message.content or None,
        tool_calls=tool_calls or None,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )


class LLMProvider(ABC):
    """Base class every provider adapter implements."""

    name: str
    is_cloud: bool

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.default_model = default_model

    @abstractmethod
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
        """Stream one StreamChunk per content delta.

        The final chunk carries prompt_tokens/completion_tokens when the
        provider reports them, otherwise those fields stay None.
        """
        raise NotImplementedError

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None = None,
    ) -> str:
        """Non-streaming single answer (used by deep research)."""
        raise NotImplementedError

    @abstractmethod
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
        """Non-streamed call where the model may return tool calls.

        Providers that cannot do tool calling raise RuntimeError so the agent
        loop can surface a clear message instead of a generic failure.
        """
        raise NotImplementedError
