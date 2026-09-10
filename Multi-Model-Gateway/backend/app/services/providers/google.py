"""Adapter for Google Gemini (google-genai SDK). Lazy-imported like Anthropic;
roles are mapped user->user, assistant->model, system->system_instruction,
tool->skipped (best effort). The async client lives under `aio`; streaming runs
through client.aio.models.generate_content_stream. The SDK's chunk.text is the
chained accumulation of the response so far, so each yielded chunk is the
delta since the previous one.
"""
from typing import AsyncIterator

from .base import LLMProvider, StreamChunk, ToolResponse


class GoogleProvider(LLMProvider):
    name = "google"
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
            from google import genai
        except ImportError:
            self._unavailable = "google-genai package not installed"
            self._client = None
            return
        self._unavailable = None
        self._genai = genai
        self._client = genai.Client(api_key=api_key)

    def _require_client(self):
        if self._unavailable:
            raise RuntimeError(self._unavailable)
        return self._client

    @staticmethod
    def _build_contents(messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Split system messages into system_instruction and map the rest to
        Gemini roles. 'tool' and other roles are skipped (best effort)."""
        system_parts: list[str] = []
        contents: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system" and content:
                system_parts.append(content)
            elif role == "assistant" and content:
                contents.append({"role": "model", "parts": [content]})
            elif role == "user" and content:
                contents.append({"role": "user", "parts": [content]})
        system = "\n\n".join(system_parts) if system_parts else None
        return system, contents

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
        system_instruction, contents = self._build_contents(messages)
        config_kwargs: dict = dict(temperature=temperature)
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens
        if top_p is not None:
            config_kwargs["top_p"] = top_p
        # stop sequences not exposed in GenerateContentConfig; extra_sampling ignored
        config = self._genai.types.GenerateContentConfig(**config_kwargs)

        prev = ""
        async for chunk in client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            # .text is cumulative — yield only the delta since the last chunk
            text = chunk.text or ""
            if len(text) > len(prev):
                yield StreamChunk(content=text[len(prev):])
            prev = text

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None = None,
    ) -> str:
        client = self._require_client()
        system_instruction, contents = self._build_contents(messages)
        config_kwargs: dict = dict(temperature=temperature)
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens
        config = self._genai.types.GenerateContentConfig(**config_kwargs)

        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        return response.text or ""

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
        # surface "google-genai package not installed" first when the SDK is
        # missing; otherwise tool calling is simply not implemented yet
        self._require_client()
        raise RuntimeError("tool calling not supported for google provider yet")
