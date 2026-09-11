"""OpenAI chat-completions provider.

Wire format is OpenAI-compatible, so this is a thin configuration of the
shared :class:`~ai.llm.openai_compat.OpenAICompatClient` (httpx only — the
official SDKs are deliberately NOT pulled in: one transport, one retry
policy, one data-hygiene story across every OpenAI-shaped endpoint).

    MS_LLM__CLOUD__OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from ai.base import (
    LLMCompletion,
    LLMMessage,
    LLMProvider,
    PrivacyClass,
    ProviderCapabilities,
    ProviderHealth,
)
from ai.llm.openai_compat import OpenAICompatClient, OpenAICompatConfig


def openai_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("api_key"))


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = dict(config or {})
        self._compat = OpenAICompatClient(
            OpenAICompatConfig.from_mapping(self._cfg), client=client, provider_name=self.name
        )
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.CLOUD,
            supports_streaming=True,
            languages=("*",),
            cost_hint_per_unit=0.6,  # ~USD per 1M tokens class (tie-break only)
            latency_hint_ms=2500,
            extra={"model": str(self._cfg.get("model") or ""), "wire": "openai-compatible"},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        return await self._compat.complete(
            messages, model=model, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        async for delta in self._compat.stream(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        ):
            yield delta

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._compat.client.get(
                f"{self._compat.config.base_url}/v1/models",
                headers={"Authorization": f"Bearer {self._compat.config.api_key}"},
                timeout=5.0,
            )
            ok = resp.status_code == 200
            detail = None if ok else f"HTTP {resp.status_code} (check MS_LLM__CLOUD__OPENAI_API_KEY)"
        except Exception as exc:
            return ProviderHealth(ok=False, detail=f"{type(exc).__name__}")
        return ProviderHealth(ok=ok, latency_ms=(time.perf_counter() - started) * 1000, detail=detail)

    async def aclose(self) -> None:
        await self._compat.aclose()
