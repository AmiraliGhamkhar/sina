"""OpenAI-compatible HTTP chat-completions transport.

Shared plumbing for every provider whose wire format is
``POST {base}/v1/chat/completions`` (llama-server, LM Studio, vLLM, OpenAI
itself, ...). Phlox's ``llm_client`` uses the same unified-client idea; this
implementation adds explicit retry policy and never logs request bodies
(clinical transcripts must not leak into logs — see docs/ARCHITECTURE.md
"Data Hygiene").

Only :mod:`httpx` is used; ``client`` is injectable so tests can plug in
``httpx.MockTransport`` with zero network.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ai.base import LLMCompletion, LLMMessage, LLMUsage, ProviderError, ProviderUnavailableError

logger = logging.getLogger(__name__)


def normalize_base_url(raw: str) -> str:
    """Accept 'host:8080', 'http(s)://host:port[/v1]' → 'http(s)://host:port'."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise ValueError("base_url is required")
    if "://" not in url:
        url = "http://" + url
    for suffix in ("/chat/completions", "/completions", "/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


@dataclass(frozen=True)
class OpenAICompatConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_s: float = 120.0
    #: total attempts = max_retries + 1 (transient transport/5xx/429 only)
    max_retries: int = 2
    backoff_base_s: float = 0.4
    backoff_cap_s: float = 8.0
    extra_headers: Mapping[str, str] | None = None

    @classmethod
    def from_mapping(cls, cfg: Mapping[str, Any]) -> OpenAICompatConfig:
        return cls(
            base_url=normalize_base_url(str(cfg.get("base_url", ""))),
            model=str(cfg.get("model") or ""),
            api_key=(str(cfg["api_key"]) if cfg.get("api_key") else None),
            timeout_s=float(cfg.get("timeout_s", 120.0)),
            max_retries=int(cfg.get("max_retries", 2)),
            backoff_base_s=float(cfg.get("backoff_base_s", 0.4)),
            backoff_cap_s=float(cfg.get("backoff_cap_s", 8.0)),
            extra_headers=cfg.get("extra_headers") or None,
        )


class OpenAICompatClient:
    """Minimal, well-behaved chat-completions client.

    Error contract: transport/5xx/429 → :class:`ProviderUnavailableError`
    (retryable) after the retry budget; other 4xx → :class:`ProviderError`
    (not retryable). Response bodies are truncated in error messages;
    prompts/completions are never logged.
    """

    def __init__(
        self,
        config: OpenAICompatConfig,
        *,
        client: httpx.AsyncClient | None = None,
        provider_name: str = "openai-compatible",
    ) -> None:
        self.config = config
        self._provider_name = provider_name
        self._external_client = client
        self._client = client

    # -- lifecycle ---------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_s, connect=10.0)
            )
        return self._client

    async def aclose(self) -> None:
        # never close an injected client — its owner does that
        if self._client is not None and self._external_client is None:
            await self._client.aclose()
            self._client = None

    # -- request building ----------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if self.config.extra_headers:
            headers.update(self.config.extra_headers)
        return headers

    def _payload(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None,
        temperature: float,
        max_tokens: int | None,
        json_mode: bool,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    # -- operations ----------------------------------------------------------
    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        url = f"{self.config.base_url}/v1/chat/completions"
        payload = self._payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            stream=False,
        )
        data = await self._post_json(url, payload)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"malformed completion response from {self._provider_name}",
                provider=self._provider_name,
            ) from exc
        usage = None
        if isinstance(data.get("usage"), Mapping):
            usage = LLMUsage(
                prompt_tokens=data["usage"].get("prompt_tokens"),
                completion_tokens=data["usage"].get("completion_tokens"),
                total_tokens=data["usage"].get("total_tokens"),
            )
        return LLMCompletion(
            text=text,
            model=data.get("model"),
            usage=usage,
            meta={"finish_reason": (data.get("choices") or [{}])[0].get("finish_reason")},
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        url = f"{self.config.base_url}/v1/chat/completions"
        payload = self._payload(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
            stream=True,
        )
        attempt = 0
        while True:
            try:
                async with self.client.stream(
                    "POST", url, json=payload, headers=self._headers()
                ) as resp:
                    if resp.status_code >= 500 or resp.status_code == 429:
                        body = (await resp.aread()).decode(errors="replace")[:300]
                        raise _Transient(f"stream open failed {resp.status_code}: {body}")
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode(errors="replace")[:300]
                        raise ProviderError(
                            f"provider rejected stream request: {resp.status_code} {body}",
                            provider=self._provider_name,
                        )
                    async for delta in _iter_sse_deltas(resp):
                        yield delta
                return
            except _Transient as exc:
                if attempt >= self.config.max_retries:
                    raise ProviderUnavailableError(
                        f"{self._provider_name} stream failed: {exc}",
                        provider=self._provider_name,
                    ) from exc
                attempt += 1
                await asyncio.sleep(self._backoff(attempt))

    async def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            started = time.perf_counter()
            try:
                resp = await self.client.post(url, json=payload, headers=self._headers())
                elapsed_ms = (time.perf_counter() - started) * 1000
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise _Transient(
                        f"upstream {resp.status_code}: {resp.text[:300]}"
                    )
                if resp.status_code >= 400:
                    raise ProviderError(
                        f"upstream {resp.status_code}: {resp.text[:300]}",
                        provider=self._provider_name,
                    )
                logger.debug(
                    "llm request ok provider=%s ms=%.0f", self._provider_name, elapsed_ms
                )
                return resp.json()
            except (httpx.TransportError, _Transient) as exc:
                last_exc = exc
                logger.warning(
                    "llm attempt %d/%d failed provider=%s err=%s",
                    attempt + 1,
                    self.config.max_retries + 1,
                    self._provider_name,
                    type(exc).__name__,
                )
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self._backoff(attempt + 1))
        raise ProviderUnavailableError(
            f"{self._provider_name} unavailable after retries: {last_exc}",
            provider=self._provider_name,
        )

    def _backoff(self, attempt: int) -> float:
        delay = min(
            self.config.backoff_cap_s,
            self.config.backoff_base_s * (2 ** (attempt - 1)),
        )
        return delay * (0.5 + random.random())  # full jitter


class _Transient(Exception):
    """Internal marker for retryable upstream states."""


async def _iter_sse_deltas(resp: httpx.Response) -> AsyncIterator[str]:
    """Parse OpenAI-style SSE: ``data: {json}`` lines until ``[DONE]``."""
    async for raw in resp.aiter_lines():
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue  # tolerate keep-alive/comment frames
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = (choices[0].get("delta") or {}).get("content")
        if delta:
            yield delta
