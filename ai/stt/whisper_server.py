"""whisper.cpp ``whisper-server`` HTTP adapter (provider name: whisper-local).

whisper-server is run as an EXTERNAL service (systemd/container) — this
adapter never spawns or manages the binary. Wire contract (whisper.cpp
server): ``POST /inference`` multipart ``file`` + form params,
``GET /health`` liveness probe.

Pseudo-streaming: utterances are cut by the shared :class:`VadSegmenter`
(buffer/interval/silence knobs below) and each cut is transcribed as a batch
request; long utterances additionally re-dispatch the open buffer to produce
interims — matching the roadmap's "windowed pseudo-streaming with VAD".

Configuration (via backend Settings → registry factory):

    MS_STT__WHISPER_SERVER__URL               e.g. http://127.0.0.1:8001
    MS_STT__WHISPER_SERVER__MODEL             label passed through as-is
    MS_STT__WHISPER_SERVER__API_KEY           optional (reverse-proxy auth)
    MS_STT__WHISPER_SERVER__HOTWORDS          comma list → initial_prompt
    MS_STT__WHISPER_SERVER__VAD_*             silence_ms / interim_ms / threshold ...
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


def whisper_local_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("url"))


def _map_language(language: str | None) -> str | None:
    """fa-en mixed → whisper 'fa' (embedded English survives via initial
    prompt); unknown tags pass through; None → server auto-detect."""
    if not language:
        return None
    return {"fa-en": "fa", "fa-en-mixed": "fa", "en-fa": "fa"}.get(language, language)


class WhisperServerProvider(STTProvider):
    name = "whisper-local"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cfg = dict(config or {})
        url = str(cfg.get("url") or "").rstrip("/")
        if not url:
            raise ProviderError(
                "whisper-local requires 'url' (MS_STT__WHISPER_SERVER__URL)", provider=self.name
            )
        self._base = url
        self._model = str(cfg.get("model") or "large-v3")
        self._timeout_s = float(cfg.get("timeout_s", 300.0))
        api_key = cfg.get("api_key")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        hotwords = list(cfg.get("hotwords") or [])
        self._hotwords = tuple(str(h) for h in hotwords if str(h).strip())
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_s)
        self._vad_kwargs = {
            "threshold": float(cfg.get("vad_threshold", 0.02)),
            "silence_ms": int(cfg.get("vad_silence_ms", 600)),
            "interim_ms": int(cfg.get("vad_interim_ms", 1500)),
            "pre_roll_ms": int(cfg.get("vad_pre_roll_ms", 300)),
            "max_segment_ms": int(cfg.get("max_segment_ms", 30000)),
        }
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.LOCAL,
            supports_streaming=True,  # via VAD-windowed pseudo-streaming
            supports_batch=True,
            languages=("*",),
            cost_hint_per_unit=0.0,
            latency_hint_ms=int(cfg.get("latency_hint_ms", 2500)),
            extra={"model": self._model, "streaming_mode": "vad-windowed"},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    # -- batch ---------------------------------------------------------------

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        data = request.audio
        if request.encoding == "wav" or data[:4] == b"RIFF":
            wav = data
        else:
            wav = pcm_to_wav(data, request.sample_rate, request.channels)
        payload: dict[str, Any] = {
            "response_format": "json",
            "temperature": "0",
            "word_timestamps": "true",
        }
        language = _map_language(request.language)
        if language:
            payload["language"] = language
        prompt = self._initial_prompt(request.context_hints)
        if prompt:
            payload["initial_prompt"] = prompt
        resp = await self._post(
            "/inference",
            files={"file": ("audio.wav", wav, "audio/wav")},
            data=payload,
        )
        body = self._parse_json(resp)
        return self._segments(body, request)

    def _initial_prompt(self, context_hints: Any) -> str:
        terms = list(self._hotwords) + [str(h) for h in (context_hints or ())]
        seen: list[str] = []
        for t in terms:
            if t and t not in seen:
                seen.append(t)
        return ", ".join(seen[:40])  # whisper prompts are token-limited

    def _segments(self, body: Mapping[str, Any], request: STTRequest) -> list[TranscriptSegment]:
        out: list[TranscriptSegment] = []
        raw_segments = body.get("segments") or []
        for seg in raw_segments:
            if isinstance(seg.get("start"), (int, float)):
                start_ms = int(float(seg["start"]) * 1000)
                end_ms = int(float(seg.get("end", seg["start"])) * 1000)
            else:  # older whisper.cpp builds report only offset/duration (ms)
                start_ms = int(seg.get("offset_from", 0))
                end_ms = int(seg.get("offset_to", start_ms + seg.get("duration", 0) * 1000))
            text = str(seg.get("text", "")).strip()
            if text:
                out.append(
                    TranscriptSegment(
                        text=text,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        is_final=True,
                        language=request.language,
                        confidence=None,  # whisper does not expose per-segment confidence
                    )
                )
        if not out and body.get("transcription"):
            text = str(body["transcription"]).strip()
            dur = pcm_duration_ms(request.audio, request.sample_rate, request.channels)
            out.append(
                TranscriptSegment(text=text, start_ms=0, end_ms=max(dur, 1), language=request.language)
            )
        return out

    # -- pseudo-streaming --------------------------------------------------------

    async def stream(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]:
        vad = VadSegmenter(**self._vad_kwargs)
        queue_started = time.perf_counter()
        try:
            async for chunk in chunks:
                for event in vad.feed(chunk):
                    async for seg in self._dispatch(event, queue_started):
                        yield seg
            for event in vad.flush():
                async for seg in self._dispatch(event, queue_started):
                    yield seg
        finally:
            aclose = chunks.aclose if hasattr(chunks, "aclose") else None
            if aclose is not None:
                await aclose()

    async def _dispatch(
        self, event: VadEvent, queue_started: float
    ) -> AsyncIterator[TranscriptSegment]:
        request = STTRequest(audio=event.audio, language=None)
        try:
            segments = await self.transcribe(request)
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover — transport wrapper covers this
            raise ProviderUnavailableError(f"whisper-server request failed: {exc}") from exc
        for seg in segments:
            # provider timestamps are relative to the dispatched buffer —
            # translate them onto the session timeline
            yield TranscriptSegment(
                text=seg.text,
                start_ms=event.start_ms + seg.start_ms,
                end_ms=event.start_ms + max(seg.end_ms, seg.start_ms + 1),
                is_final=event.kind == "final",
                language=seg.language,
                confidence=seg.confidence,
            )

    # -- http plumbing ----------------------------------------------------------

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._client.post(
                f"{self._base}{path}", headers=self._headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"whisper-server unreachable at {self._base}: {exc}", provider=self.name
            ) from exc
        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"whisper-server error {resp.status_code}", provider=self.name
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"whisper-server rejected request ({resp.status_code})",
                provider=self.name,
                status_hint=resp.status_code,
            )
        return resp

    @staticmethod
    def _parse_json(resp: httpx.Response) -> Mapping[str, Any]:
        try:
            return resp.json()
        except Exception as exc:
            raise ProviderError("whisper-server returned non-JSON", provider="whisper-local") from exc

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._client.get(
                f"{self._base}/health", headers=self._headers, timeout=min(5.0, self._timeout_s)
            )
            ok = resp.status_code == 200
            detail = None if ok else f"HTTP {resp.status_code}"
        except Exception as exc:
            return ProviderHealth(ok=False, detail=f"{type(exc).__name__}")
        return ProviderHealth(ok=ok, latency_ms=(time.perf_counter() - started) * 1000, detail=detail)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
