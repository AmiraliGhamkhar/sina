"""Google Gemini generateContent provider (httpx only; no SDK).

    MS_LLM__CLOUD__GEMINI_API_KEY / GEMINI_MODEL / GEMINI_BASE_URL

Contract mapping:
- ``system`` → top-level ``systemInstruction``; assistant role → ``model``.
- ``json_mode`` → ``generationConfig.responseMimeType = application/json``.
- usage: ``usageMetadata.promptTokenCount/candidatesTokenCount``.
- API key travels as ``x-goog-api-key`` header (never in the URL — query
  strings would leak into access logs, violating the data-hygiene rule).
"""
from __future__ import annotations

import asyncio
import json
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
    ProviderError,
    ProviderHealth,
    ProviderUnavailableError,
)


def gemini_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("api_key"))


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cfg = dict(config or {})
        base = str(cfg.get("base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
        if "://" not in base:
            base = "https://" + base
        self._base = base
        self._model = str(cfg.get("model") or "gemini-2.0-flash")
        self._api_key = str(cfg.get("api_key") or "")
        if not self._api_key:
            raise ProviderError("gemini requires 'api_key'", provider=self.name)
        self._timeout_s = float(cfg.get("timeout_s", 120.0))
        self._max_retries = int(cfg.get("max_retries", 2))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_s)
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.CLOUD,
            supports_streaming=True,
            languages=("*",),
            cost_hint_per_unit=0.7,
            latency_hint_ms=2200,
            extra={"model": self._model, "wire": "gemini-generateContent"},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}

    @staticmethod
    def _contents(messages: Sequence[LLMMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            role = "model" if m.role == "assistant" else "user"
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].append({"text": m.content})
            else:
                contents.append({"role": role, "parts": [{"text": m.content}]})
        return ("\n\n".join(system_parts) or None), contents

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        model = model or self._model
        system, contents = self._contents(messages)
        gen_cfg: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            gen_cfg["maxOutputTokens"] = max_tokens
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {"contents": contents, "generationConfig": gen_cfg}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        data = await self._post(f"/v1beta/models/{model}:generateContent", payload)
        parts_text = ""
        for cand in data.get("candidates") or []:
            for part in ((cand.get("content") or {}).get("parts")) or []:
                parts_text += str(part.get("text", ""))
        finish = (data.get("candidates") or [{}])[0].get("finishReason")
        usage = None
        if isinstance(data.get("usageMetadata"), Mapping):
            um = data["usageMetadata"]
            usage = LLMUsage(
                prompt_tokens=um.get("promptTokenCount"),
                completion_tokens=um.get("candidatesTokenCount"),
                total_tokens=um.get("totalTokenCount"),
            )
        return LLMCompletion(text=parts_text, model=model, usage=usage, meta={"finish_reason": finish})

    async def _post(self, path: str, payload: dict[str, Any]) -> Mapping[str, Any]:
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.post(url, json=payload, headers=self._headers())
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(min(8.0, 0.4 * 2**attempt) + random.random() * 0.1)
                continue
            if resp.status_code == 200:
                try:
                    return resp.json()
                except json.JSONDecodeError as exc:
                    raise ProviderError("gemini returned non-JSON", provider=self.name) from exc
            err = {}
            try:
                err = resp.json().get("error") or {}
            except Exception:
                pass
            detail = f"{err.get('status', resp.status_code)}: {err.get('message', '')}"[:300]
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < self._max_retries:
                    await asyncio.sleep(min(8.0, 0.4 * 2**attempt))
                    continue
                raise ProviderUnavailableError(f"gemini: {detail}", provider=self.name)
            raise ProviderError(
                f"gemini: {detail}", provider=self.name, status_hint=resp.status_code
            )
        raise ProviderUnavailableError(f"gemini unreachable: {last_exc}", provider=self.name)

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        model = model or self._model
        system, contents = self._contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        async with self._client.stream(
            "POST",
            f"{self._base}/v1beta/models/{model}:streamGenerateContent?alt=sse",
            json=payload,
            headers=self._headers(),
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                raise ProviderError(f"gemini stream failed ({resp.status_code})", provider=self.name)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    chunk = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                for cand in chunk.get("candidates") or []:
                    for part in ((cand.get("content") or {}).get("parts")) or []:
                        text = part.get("text")
                        if text:
                            yield text

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._client.get(
                f"{self._base}/v1beta/models",
                headers={"x-goog-api-key": self._api_key},
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
