"""llama-server provider — external llama.cpp HTTP server.

Per the project spec, llama.cpp/llama-server is treated as an *external
service*: this module never embeds, launches, or manages llama.cpp binaries
(compare open-medical-scribe's electron/llamaServer.js which spawns a child
process — deliberately NOT adopted for the server-side deployment).

Wire: OpenAI-compatible ``/v1/chat/completions`` + llama-server's own
``GET /health`` probe. All knobs are configuration:

    MS_LLM__LLAMA_SERVER__BASE_URL   (e.g. http://llm-host:8080)
    MS_LLM__LLAMA_SERVER__MODEL      (label advertised by the server)
    MS_LLM__LLAMA_SERVER__API_KEY    (optional; llama-server is normally open)
    MS_LLM__LLAMA_SERVER__TIMEOUT_S
    MS_LLM__LLAMA_SERVER__MAX_RETRIES
    MS_LLM__LLAMA_SERVER__STREAMING  (default true)

llama-server is never exposed to the WPF client: the backend proxies through
this provider only.
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


def llama_server_configured(cfg: Mapping[str, Any]) -> bool:
    """Registry predicate: a base URL must be present to route here."""
    return bool((cfg or {}).get("base_url"))


class LlamaServerProvider(LLMProvider):
    name = "llama-server"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = dict(config or {})
        self._compat_config = OpenAICompatConfig.from_mapping(self._cfg)
        self._client = OpenAICompatClient(
            self._compat_config, client=client, provider_name=self.name
        )
        self._streaming_enabled = bool(self._cfg.get("streaming", True))
        self._props: Mapping[str, Any] | None = None
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.LOCAL,
            supports_streaming=self._streaming_enabled,
            languages=("*",),
            latency_hint_ms=int(self._cfg.get("latency_hint_ms", 1500)),
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    @property
    def base_url(self) -> str:
        return self._compat_config.base_url

    async def health(self) -> ProviderHealth:
        """llama-server exposes ``GET /health`` (200 once the model is loaded)."""
        started = time.perf_counter()
        try:
            resp = await self._client.client.get(
                f"{self.base_url}/health", timeout=5.0
            )
            ok = resp.status_code == 200
            return ProviderHealth(
                ok=ok,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=None if ok else f"status={resp.status_code}",
            )
        except httpx.HTTPError as exc:
            return ProviderHealth(
                ok=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=f"{type(exc).__name__}",
            )

    async def props(self) -> Mapping[str, Any]:
        """Capability discovery: ``GET /props`` (model path, n_ctx, ...).

        Cached after first success; failures return {} so callers never block
        on an unavailable optional endpoint. Feeds capacity checks before long
        prompts (Phase 5 queue-depth builds on this).
        """
        if self._props is not None:
            return self._props
        try:
            resp = await self._client.client.get(f"{self.base_url}/props", timeout=5.0)
            self._props = resp.json() if resp.status_code == 200 else {}
        except Exception:
            self._props = {}
        return self._props

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        return await self._client.complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        if not self._streaming_enabled:
            result = await self.complete(messages, model=model, temperature=temperature)
            yield result.text
            return
        async for delta in self._client.stream(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        ):
            yield delta

    async def aclose(self) -> None:
        await self._client.aclose()
