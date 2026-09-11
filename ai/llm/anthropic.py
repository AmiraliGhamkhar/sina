"""Anthropic Messages-API provider (httpx only; no SDK).

    MS_LLM__CLOUD__ANTHROPIC_API_KEY / ANTHROPIC_MODEL / ANTHROPIC_BASE_URL

Provider contract mapping notes:
- ``system`` role messages → the API's top-level ``system`` parameter.
- ``max_tokens`` is REQUIRED by Anthropic → default 1600 when the caller
  doesn't set one.
- ``json_mode`` has no native response_format: enforced by system
  instruction (strict JSON, no prose/fences) — callers parse defensively and
  the report service runs one repair round-trip (Phlox pattern).
- usage: input_tokens/output_tokens → prompt/completion tokens.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx

from ai.base import (
    LLMCompletion,
    LLMMessage,
    LLMProvider,
    LLMUsage,
    PrivacyClass,
    ProviderCapabilities,
    ProviderHealth,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"


def anthropic_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("api_key"))


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cfg = dict(config or {})
        base = str(cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
        if "://" not in base:
            base = "https://" + base
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self._base = base
        self._model = str(cfg.get("model") or "claude-sonnet-4-20250514")
        self._api_key = str(cfg.get("api_key") or "")
        if not self._api_key:
            from ai.base import ProviderError

            raise ProviderError("anthropic requires 'api_key'", provider=self.name)
        self._timeout_s = float(cfg.get("timeout_s", 120.0))
        self._max_retries = int(cfg.get("max_retries", 2))
        self._backoff_s = float(cfg.get("backoff_base_s", 0.4))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_s)
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.CLOUD,
            supports_streaming=True,
            languages=("*",),
            cost_hint_per_unit=3.0,
            latency_hint_ms=3000,
            extra={"model": self._model, "wire": "anthropic-messages"},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _split_system(messages: Sequence[LLMMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        turns: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                role = "assistant" if m.role == "assistant" else "user"
                if turns and turns[-1]["role"] == role:
                    turns[-1]["content"].append({"type": "text", "text": m.content})
                else:
                    turns.append({"role": role, "content": [{"type": "text", "text": m.content}]})
        return ("\n\n".join(system_parts) or None), turns

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        system, turns = self._split_system(messages)
        if json_mode:
            strict = (
                "Respond with STRICT JSON only — a single object, no prose, no code fences."
            )
            system = f"{system}\n\n{strict}" if system else strict
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": turns,
            "max_tokens": max_tokens or 1600,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        data = await self._post(payload)
        text = "".join(
            str(block.get("text", ""))
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = None
        if isinstance(data.get("usage"), Mapping):
            u = data["usage"]
            usage = LLMUsage(
                prompt_tokens=u.get("input_tokens"),
                completion_tokens=u.get("output_tokens"),
                total_tokens=(u.get("input_tokens") or 0) + (u.get("output_tokens") or 0) or None,
            )
        return LLMCompletion(
            text=text,
            model=data.get("model"),
            usage=usage,
            meta={"stop_reason": data.get("stop_reason")},
        )

    async def _post(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        url = f"{self._base}/v1/messages"
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(url, json=payload, headers=self._headers())
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(min(8.0, self._backoff_s * 2**attempt) + random.random() * 0.1)
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except json.JSONDecodeError as exc2:
                    from ai.base import ProviderError

                    raise ProviderError(
                        "anthropic returned non-JSON", provider=self.name
                    ) from exc2
            body = self._error_detail(resp)
            from ai.base import ProviderError

            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() else min(
                    8.0, self._backoff_s * 2**attempt
                )
                await asyncio.sleep(min(delay, self._timeout_s))
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                raise ProviderUnavailableError(
                    f"anthropic: {body}", provider=self.name
                )
            raise ProviderError(
                f"anthropic: {body}", provider=self.name, status_hint=resp.status_code
            )
        raise ProviderUnavailableError(
            f"anthropic unreachable: {last_exc}", provider=self.name
        )

    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        try:
            err = resp.json().get("error") or {}
            return f"{err.get('type', resp.status_code)}: {err.get('message', '')}"[:300]
        except Exception:
            return f"HTTP {resp.status_code}"

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        system, turns = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": turns,
            "max_tokens": max_tokens or 1600,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system
        async with self._client.stream(
            "POST", f"{self._base}/v1/messages", json=payload, headers=self._headers()
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                from ai.base import ProviderError

                raise ProviderError(
                    f"anthropic stream failed ({resp.status_code})", provider=self.name
                )
            data: list[str] = []
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                elif event.get("type") == "message_stop":
                    data.clear()  # consumed; keep flake-clean
                    return
                elif event.get("type") == "error":
                    from ai.base import ProviderError

                    raise ProviderError(
                        f"anthropic stream error: {event.get('error')}", provider=self.name
                    )

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            # minimal count-tokens round-trip: cheap, authenticated, no generation
            resp = await self._client.post(
                f"{self._base}/v1/messages/count_tokens",
                json={"model": self._model, "messages": [{"role": "user", "content": "ping"}]},
                headers=self._headers(),
                timeout=5.0,
            )
            ok = resp.status_code == 200
            detail = None if ok else f"HTTP {resp.status_code}"
        except Exception as exc:
            return ProviderHealth(ok=False, detail=f"{type(exc).__name__}")
        return ProviderHealth(ok=ok, latency_ms=(time.perf_counter() - started) * 1000, detail=detail)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
