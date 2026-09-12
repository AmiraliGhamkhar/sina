"""9Router LLM adapter — provider name ``9router``.

Routes note-drafting / normalization through a self-hosted
`9Router <https://github.com/decolua/9router>`_ instance, which fronts 40+
upstreams (Claude, GPT, Gemini, Groq, Copilot, …) behind one
OpenAI-compatible API with automatic account fallback.

Wire: ``POST {base}/api/v1/chat/completions`` (streaming SSE supported),
liveness + catalog via ``GET {base}/api/v1/models``. Everything is a thin
configuration of :class:`~ai.llm.openai_compat.OpenAICompatClient` — one
transport, one retry policy, one data-hygiene story.

    MS_LLM__NINE_ROUTER__BASE_URL      (e.g. http://127.0.0.1:20128 — routes only if set)
    MS_LLM__NINE_ROUTER__MODEL         "provider/model", e.g. claude/claude-sonnet-4
    MS_LLM__NINE_ROUTER__API_KEY       optional (only needed if REQUIRE_API_KEY=true)
    MS_LLM__NINE_ROUTER__PRIVACY_CLASS local (default) | cloud
    MS_LLM__NINE_ROUTER__API_PREFIX    /api — set "" behind a path-rewriting proxy
    MS_LLM__NINE_ROUTER__STREAMING     default true

Privacy: 9Router normally runs on the same host as the backend, so the default
class is LOCAL and it participates in privacy-required routing. If your
instance relays to cloud accounts and you want private encounters kept away
from it, set ``PRIVACY_CLASS=cloud``.
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
    ProviderError,
    ProviderHealth,
)
from ai.llm.openai_compat import OpenAICompatClient, OpenAICompatConfig
from ai.nine_router_client import (
    fetch_models,
    nine_router_headers,
    normalize_nine_router_base_url,
    resolve_model_id,
)

logger = logging.getLogger(__name__)


def nine_router_configured(cfg: Mapping[str, Any]) -> bool:
    """Registry predicate: a base URL must be present to route here.

    The API key is deliberately *not* required — 9Router serves unauthenticated
    requests unless ``REQUIRE_API_KEY=true``.
    """
    return bool((cfg or {}).get("base_url"))


def _privacy_class(cfg: Mapping[str, Any]) -> PrivacyClass:
    raw = str(cfg.get("privacy_class") or "local").strip().lower()
    if raw in ("cloud", "remote"):
        return PrivacyClass.CLOUD
    if raw not in ("local", ""):
        logger.warning("9router: unknown privacy_class %r — falling back to LOCAL", raw)
    return PrivacyClass.LOCAL


class NineRouterProvider(LLMProvider):
    name = "9router"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = dict(config or {})
        if not self._cfg.get("base_url"):
            raise ProviderError(
                "9router requires 'base_url' (MS_LLM__NINE_ROUTER__BASE_URL)",
                provider=self.name,
            )
        self._api_key = str(self._cfg.get("api_key") or "") or None
        self._api_prefix = self._cfg.get("api_prefix")
        base_url = normalize_nine_router_base_url(
            str(self._cfg["base_url"]),
            self._api_prefix if self._api_prefix is not None else "/api",
        )
        # OpenAICompatConfig re-normalizes; handing it the already-prefixed URL
        # keeps a single source of truth for the /api path.
        compat_cfg = OpenAICompatConfig.from_mapping(
            {
                **self._cfg,
                "base_url": base_url,
                "api_key": self._api_key,
                "extra_headers": self._cfg.get("extra_headers") or None,
            }
        )
        self._compat = OpenAICompatClient(
            compat_cfg, client=client, provider_name=self.name
        )
        self._model = str(self._cfg.get("model") or "").strip()
        self._streaming_enabled = bool(self._cfg.get("streaming", True))
        self._privacy = _privacy_class(self._cfg)
        self._capabilities = ProviderCapabilities(
            privacy=self._privacy,
            supports_streaming=self._streaming_enabled,
            languages=("*",),
            # 9Router itself is free/local, but the upstream it relays to may
            # not be — keep a non-zero hint so AUTO mode still prefers truly
            # local llama-server when both are healthy.
            cost_hint_per_unit=float(self._cfg.get("cost_hint_per_unit", 0.0)),
            latency_hint_ms=int(self._cfg.get("latency_hint_ms", 2000)),
            extra={
                "model": self._model,
                "wire": "openai-compatible",
                "router": "9router",
                "transport": "openai-compatible-http",
            },
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    @property
    def base_url(self) -> str:
        return self._compat.config.base_url

    @property
    def default_model(self) -> str:
        return self._model

    def _auth_headers(self) -> dict[str, str]:
        return nine_router_headers(self._api_key)

    # -- model discovery (surfaced to the WPF AI Settings screen) --------------

    async def list_models(self, kind: str = "llm") -> list[dict[str, Any]]:
        """Live ``provider/model`` catalog for this 9Router instance.

        The catalog depends on which upstream accounts the operator connected,
        so it is fetched rather than hardcoded. Advisory only — a failure never
        blocks note generation.
        """
        return await fetch_models(
            self._compat.client,
            self.base_url,
            kind=kind,
            headers=self._auth_headers(),
            timeout=min(10.0, self._compat.config.timeout_s),
            provider_name=self.name,
        )

    # -- completions -----------------------------------------------------------

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
            messages,
            model=resolve_model_id(self._model, model),
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
        resolved = resolve_model_id(self._model, model)
        if not self._streaming_enabled:
            result = await self.complete(
                messages, model=resolved, temperature=temperature, max_tokens=max_tokens
            )
            yield result.text
            return
        async for delta in self._compat.stream(
            messages, model=resolved, temperature=temperature, max_tokens=max_tokens
        ):
            yield delta

    # -- health ----------------------------------------------------------------

    async def health(self) -> ProviderHealth:
        """``GET {base}/api/v1/models`` — cheap, and proves auth + reachability.

        A 9Router instance with no connected accounts still answers 200 with an
        empty list; that is reported as *healthy but unusable* in the detail so
        the AI Settings screen explains why drafting fails.
        """
        started = time.perf_counter()
        try:
            resp = await self._compat.client.get(
                f"{self.base_url}/v1/models",
                headers=self._auth_headers(),
                timeout=min(5.0, self._compat.config.timeout_s),
            )
        except httpx.HTTPError as exc:
            return ProviderHealth(
                ok=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=f"{type(exc).__name__} (is 9router running at {self.base_url}?)",
            )
        latency = round((time.perf_counter() - started) * 1000, 1)
        if resp.status_code in (401, 403):
            return ProviderHealth(
                ok=False,
                latency_ms=latency,
                detail=f"HTTP {resp.status_code} (check MS_LLM__NINE_ROUTER__API_KEY)",
            )
        if resp.status_code != 200:
            return ProviderHealth(
                ok=False, latency_ms=latency, detail=f"HTTP {resp.status_code}"
            )
        detail = None
        try:
            body = resp.json()
            count = len(body.get("data") or []) if isinstance(body, dict) else len(body or [])
            if count == 0:
                detail = "reachable, but no upstream accounts are connected"
        except Exception:  # noqa: BLE001 — a body we cannot parse is still 200
            pass
        return ProviderHealth(ok=True, latency_ms=latency, detail=detail)

    async def aclose(self) -> None:
        await self._compat.aclose()


__all__ = ["NineRouterProvider", "nine_router_configured"]
