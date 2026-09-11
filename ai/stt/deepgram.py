"""Deepgram STT adapter — live WS streaming + HTTP prerecorded batch.

Streaming (spec §"cloud STT via backend WS"): connect to
``wss://api.deepgram.com/v1/listen?...`` with interim_results + endpointing,
push binary PCM16 frames, translate ``Results``/``UtteranceEnd`` messages into
:class:`TranscriptSegment` events (is_final false for interims). Keyword
boosting carries medical hotwords (drug names, laterality terms) — from the
provider config and per-request ``context_hints``.

The WS client sits behind the :mod:`ai.stt.ws_transport` seam so protocol
tests run against scripted fake transports (acceptance: no live creds in CI).

    MS_STT__DEEPGRAM__API_KEY
    MS_STT__DEEPGRAM__MODEL              default nova-2-general
    MS_STT__DEEPGRAM__KEYWORDS           base boosting list (medical terms)
    MS_STT__DEEPGRAM__BASE_URL/WS_URL    only for self-hosted/proxy setups
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlencode

import httpx

from ai.base import (
    PrivacyClass,
    ProviderCapabilities,
    ProviderError,
    ProviderHealth,
    ProviderUnavailableError,
    STTProvider,
    STTRequest,
    TranscriptSegment,
)
from ai.stt._common import pcm_duration_ms
from ai.stt.ws_transport import ConnectionClosed, WsConnector, default_connector

logger = logging.getLogger(__name__)

DEEPGRAM_BASE = "https://api.deepgram.com"
DEEPGRAM_WS = "wss://api.deepgram.com/v1/listen"


def deepgram_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("api_key"))


def _listen_params(
    *, model: str, language: str | None, keywords: list[str], streaming: bool, endpointing_ms: int
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": model,
        "punctuate": "true",
        "smart_format": "true",
        "diarize": "false",
    }
    if streaming:
        params["interim_results"] = "true"
        params["endpointing"] = str(endpointing_ms)
        params["utterance_end_ms"] = "1000"
    lang = None
    if language:
        lang = {"fa-en": "fa", "fa-en-mixed": "fa"}.get(language, language)
    if lang:
        params["language"] = lang
    if keywords:
        params["keywords"] = keywords
    return params


class DeepgramProvider(STTProvider):
    name = "deepgram"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        transport_factory: WsConnector | None = None,
    ) -> None:
        cfg = dict(config or {})
        self._api_key = str(cfg.get("api_key") or "")
        if not self._api_key:
            raise ProviderError("deepgram requires 'api_key'", provider=self.name)
        self._model = str(cfg.get("model") or "nova-2-general")
        self._base_url = str(cfg.get("base_url") or DEEPGRAM_BASE).rstrip("/")
        ws_url = str(cfg.get("ws_url") or "").strip()
        if not ws_url:
            # derive from base (supports proxies); default is Deepgram cloud
            https_base = self._base_url.replace("https://", "").replace("http://", "")
            ws_url = ("wss://" if not self._base_url.startswith("http://") else "ws://") + https_base + "/v1/listen"
        self._ws_url = ws_url
        self._keywords = [str(k) for k in (cfg.get("keywords") or []) if str(k).strip()]
        self._endpointing_ms = int(cfg.get("endpointing_ms", 300))
        self._timeout_s = float(cfg.get("timeout_s", 60.0))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_s)
        self._connector = transport_factory or default_connector
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.CLOUD,
            supports_streaming=True,
            supports_batch=True,
            languages=("*",),
            cost_hint_per_unit=float(cfg.get("cost_hint_per_minute", 0.0043)),  # USD/audio-min
            latency_hint_ms=800,
            extra={"model": self._model, "boosting": "keywords"},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._api_key}"}

    # -- batch (prerecorded) ---------------------------------------------------

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        params = _listen_params(
            model=self._model,
            language=request.language,
            keywords=self._all_keywords(request.context_hints),
            streaming=False,
            endpointing_ms=self._endpointing_ms,
        )
        content_type = "audio/wav" if request.audio[:4] == b"RIFF" else (
            f"audio/L16;rate={request.sample_rate};encoding=linear16;channels={request.channels}"
        )
        url = f"{self._base_url}/v1/listen?{urlencode(params, doseq=True)}"
        try:
            resp = await self._client.post(
                url,
                headers={**self._auth_headers(), "Content-Type": content_type},
                content=request.audio,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"deepgram unreachable: {exc}", provider=self.name) from exc
        if resp.status_code in (401, 403):
            raise ProviderError(
                "deepgram authentication failed", provider=self.name, status_hint=resp.status_code
            )
        if resp.status_code >= 500:
            raise ProviderUnavailableError(f"deepgram error {resp.status_code}", provider=self.name)
        if resp.status_code >= 400:
            raise ProviderError(
                f"deepgram rejected request ({resp.status_code})",
                provider=self.name,
                status_hint=resp.status_code,
            )
        body = resp.json()
        results = (body.get("results") or {})
        out: list[TranscriptSegment] = []
        for seg in results.get("segments") or []:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            out.append(
                TranscriptSegment(
                    text=text,
                    start_ms=int(float(seg.get("start_ms", float(seg.get("start", 0)) * 1000))),
                    end_ms=int(float(seg.get("end_ms", float(seg.get("end", 0)) * 1000))),
                    confidence=seg.get("confidence"),
                    language=request.language,
                )
            )
        if not out:
            alt = (results.get("channels") or [{}])[0].get("alternatives") or [{}]
            text = str(alt[0].get("transcript", "")).strip()
            if text:
                dur = pcm_duration_ms(request.audio, request.sample_rate, request.channels)
                out.append(
                    TranscriptSegment(
                        text=text,
                        start_ms=0,
                        end_ms=max(dur, 1),
                        confidence=alt[0].get("confidence"),
                        language=request.language,
                    )
                )
        return out

    def _all_keywords(self, context_hints: Any) -> list[str]:
        hints = [str(h) for h in (context_hints or ()) if str(h).strip()]
        return list(dict.fromkeys([*self._keywords, *hints]))

    # -- streaming ------------------------------------------------------------------

    async def stream(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]:
        params = _listen_params(
            model=self._model,
            language=None,  # keep provider auto-detect for live mixed speech
            keywords=self._keywords,
            streaming=True,
            endpointing_ms=self._endpointing_ms,
        )
        url = f"{self._ws_url}?{urlencode(params, doseq=True)}"
        transport = await self._connector(url, self._auth_headers())
        audio_finished = asyncio.Event()
        try:
            pump = asyncio.create_task(self._pump(transport, chunks, audio_finished))
            while True:
                try:
                    raw = await asyncio.wait_for(transport.recv(), timeout=30.0)
                except ConnectionClosed:
                    break  # server closed after CloseStream — orderly end
                except TimeoutError as exc:
                    raise ProviderUnavailableError(
                        "deepgram stream timeout", provider=self.name
                    ) from exc
                if isinstance(raw, bytes):
                    continue
                seg = self._on_ws_message(raw)
                if seg == "close":
                    break
                if seg is not None:
                    yield seg
            await pump
        except ProviderError:
            raise
        except ConnectionClosed as exc:
            raise ProviderUnavailableError(f"deepgram stream closed: {exc}", provider=self.name) from exc
        finally:
            await transport.close()

    async def _pump(self, transport: Any, chunks: AsyncIterator[bytes], done: asyncio.Event) -> None:
        try:
            async for chunk in chunks:
                await transport.send_bytes(chunk)
        finally:
            done.set()
        # force a final flush before closing (Deepgram honors "Finalize")
        await transport.send_text(json.dumps({"type": "Finalize"}))
        await transport.send_text(json.dumps({"type": "CloseStream"}))

    def _on_ws_message(self, raw: str) -> TranscriptSegment | str | None:
        """One server message → segment | 'close' | None."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("deepgram: ignoring non-JSON ws frame")
            return None
        mtype = msg.get("type")
        if mtype in ("Open", "Listening", "Metadata", "UtteranceEnd", "ChannelMetadata", "SpeechFinished"):
            if mtype == "UtteranceEnd":
                logger.debug("deepgram utterance-end marker")
            return None
        if mtype == "Error":
            desc = str(msg.get("description", "unknown deepgram error"))
            low = desc.lower()
            if "not authorized" in low or "api key" in low or "credentials" in low:
                raise ProviderError(f"deepgram: {desc}", provider=self.name)
            raise ProviderUnavailableError(f"deepgram: {desc}", provider=self.name)
        if mtype == "ConnectionClosed":
            return "close"
        if mtype != "Results":
            return None
        is_final = bool(msg.get("is_final"))
        speech_final = bool(msg.get("speech_final"))
        start_ms = int(msg.get("start_ms", float(msg.get("start", 0.0)) * 1000))
        end_ms = int(msg.get("end_ms", start_ms + float(msg.get("duration", 0.0)) * 1000))
        alts = (msg.get("channel") or {}).get("alternatives") or [{}]
        alt = alts[0] or {}
        text = str(alt.get("transcript", "")).strip()
        if not text:
            return None
        if is_final and not speech_final:
            # stable endpointed transcript — provisional until speech_final;
            # surface it as an interim so the UI never loses words
            is_final = False
        return TranscriptSegment(
            text=text,
            start_ms=start_ms,
            end_ms=max(end_ms, start_ms + 1),
            is_final=is_final,
            confidence=alt.get("confidence"),
        )

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._client.get(
                f"{self._base_url}/v1/projects", headers=self._auth_headers(), timeout=5.0
            )
            if resp.status_code == 200:
                return ProviderHealth(
                    ok=True, latency_ms=(time.perf_counter() - started) * 1000
                )
            return ProviderHealth(
                ok=False, detail=f"HTTP {resp.status_code} (check MS_STT__DEEPGRAM__API_KEY)"
            )
        except Exception as exc:
            return ProviderHealth(ok=False, detail=f"{type(exc).__name__}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
