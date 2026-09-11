"""Qwen2-Audio ASR HTTP adapter (provider name: qwen-asr), fa-robust.

Targets vLLM/OpenAI-audio-compatible deployments exposing
``POST /v1/audio/transcriptions`` (this repo NEVER loads model weights —
inference runs in the external service; spec: llama.cpp/Qwen are external).
Persian robustness is a property of the served model; the adapter pins the
language tag and requests verbose_json so segment timestamps survive.

    MS_STT__QWEN_ASR__URL                 e.g. http://127.0.0.1:8020
    MS_STT__QWEN_ASR__MODEL               served model id (optional)
    MS_STT__QWEN_ASR__TIMEOUT_S
Streaming uses the same VAD-windowed strategy as whisper-local (Qwen
services are batch-only over HTTP).
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

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
from ai.stt._common import VadEvent, VadSegmenter, pcm_duration_ms, pcm_to_wav

logger = logging.getLogger(__name__)


def qwen_asr_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("url"))


class QwenAsrProvider(STTProvider):
    name = "qwen-asr"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cfg = dict(config or {})
        url = str(cfg.get("url") or "").rstrip("/")
        if not url:
            raise ProviderError("qwen-asr requires 'url' (MS_STT__QWEN_ASR__URL)", provider=self.name)
        self._base = url
        self._model = str(cfg.get("model") or "")
        self._timeout_s = float(cfg.get("timeout_s", 180.0))
        api_key = cfg.get("api_key")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_s)
        self._vad_kwargs = {
            "threshold": float(cfg.get("vad_threshold", 0.02)),
            "silence_ms": int(cfg.get("vad_silence_ms", 600)),
            "interim_ms": int(cfg.get("vad_interim_ms", 2000)),
            "pre_roll_ms": int(cfg.get("vad_pre_roll_ms", 300)),
            "max_segment_ms": int(cfg.get("max_segment_ms", 20000)),
        }
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.LOCAL,
            supports_streaming=True,  # VAD-windowed (service is request/response)
            supports_batch=True,
            languages=("fa", "en", "ar", "*"),
            latency_hint_ms=int(cfg.get("latency_hint_ms", 3000)),
            extra={"model": self._model, "streaming_mode": "vad-windowed"},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        data = request.audio
        wav = data if (request.encoding == "wav" or data[:4] == b"RIFF") else pcm_to_wav(
            data, request.sample_rate, request.channels
        )
        fields: dict[str, Any] = {"response_format": "verbose_json"}
        if self._model:
            fields["model"] = self._model
        if request.language and request.language not in ("fa-en", "fa-en-mixed"):
            fields["language"] = request.language
        prompt = ", ".join(str(h) for h in (request.context_hints or ()) if h)
        if prompt:
            fields["prompt"] = prompt
        try:
            resp = await self._client.post(
                f"{self._base}/v1/audio/transcriptions",
                headers=self._headers,
                files={"file": ("audio.wav", wav, "audio/wav")},
                data=fields,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"qwen-asr service unreachable: {exc}", provider=self.name) from exc
        if resp.status_code >= 500:
            raise ProviderUnavailableError(f"qwen-asr error {resp.status_code}", provider=self.name)
        if resp.status_code >= 400:
            raise ProviderError(
                f"qwen-asr rejected request ({resp.status_code})",
                provider=self.name,
                status_hint=resp.status_code,
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise ProviderError("qwen-asr returned non-JSON", provider=self.name) from exc

        segments: list[TranscriptSegment] = []
        for seg in body.get("segments") or []:
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            conf = seg.get("confidence", seg.get("avg_logprob"))
            segments.append(
                TranscriptSegment(
                    text=text,
                    start_ms=int(float(seg.get("start", 0)) * 1000),
                    end_ms=int(float(seg.get("end", seg.get("start", 0))) * 1000),
                    language=request.language,
                    confidence=float(conf) if isinstance(conf, (int, float)) else None,
                )
            )
        if not segments:
            text = str(body.get("text", "")).strip()
            if text:
                dur = pcm_duration_ms(request.audio, request.sample_rate, request.channels)
                segments.append(
                    TranscriptSegment(text=text, start_ms=0, end_ms=max(dur, 1), language=request.language)
                )
        return segments

    async def stream(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]:
        vad = VadSegmenter(**self._vad_kwargs)
        async def _feed() -> AsyncIterator[TranscriptSegment]:
            async for chunk in chunks:
                for event in vad.feed(chunk):
                    async for seg in self._dispatch(event):
                        yield seg
            for event in vad.flush():
                async for seg in self._dispatch(event):
                    yield seg
        try:
            async for seg in _feed():
                yield seg
        finally:
            if hasattr(chunks, "aclose"):
                await chunks.aclose()

    async def _dispatch(self, event: VadEvent) -> AsyncIterator[TranscriptSegment]:
        segments = await self.transcribe(STTRequest(audio=event.audio, language=None))
        for seg in segments:
            # session timeline = cut start + buffer-relative offset
            yield TranscriptSegment(
                text=seg.text,
                start_ms=event.start_ms + seg.start_ms,
                end_ms=event.start_ms + max(seg.end_ms, seg.start_ms + 1),
                is_final=event.kind == "final",
                language=seg.language,
                confidence=seg.confidence,
            )

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._client.get(
                f"{self._base}/v1/models", headers=self._headers, timeout=min(5.0, self._timeout_s)
            )
            ok = resp.status_code == 200
            detail = None if ok else f"HTTP {resp.status_code}"
        except Exception as exc:
            return ProviderHealth(ok=False, detail=f"{type(exc).__name__}")
        return ProviderHealth(ok=ok, latency_ms=(time.perf_counter() - started) * 1000, detail=detail)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
