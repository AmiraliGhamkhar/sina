"""9Router STT adapter — provider name ``9router``.

Speech-to-text through a self-hosted
`9Router <https://github.com/decolua/9router>`_ instance, which exposes an
OpenAI Whisper-compatible ``POST {base}/api/v1/audio/transcriptions`` and
relays it to whichever upstream the ``provider/model`` id names (Groq Whisper,
HuggingFace, Deepgram, AssemblyAI, Nvidia NIM, Gemini, …) with automatic
account fallback.

Wire contract (9Router ``handleStt``): multipart form with ``file`` (required)
and ``model`` (required, ``provider/model``), plus optional ``language``,
``prompt``, ``response_format`` and ``temperature`` which are forwarded to
OpenAI-compatible upstreams. Responses are either ``{"text": …}`` or, when the
upstream honours ``response_format=verbose_json``, a timed
``{"text": …, "segments": [{start, end, text}], "words": […]}``. Both are
parsed here.

Streaming: 9Router's STT is request/response, so live dictation uses the
shared :class:`~ai.stt._common.VadSegmenter` windowed pseudo-streaming — the
same mechanism as ``whisper-local``.

    MS_STT__NINE_ROUTER__BASE_URL   (e.g. http://127.0.0.1:20128 — routes only if set)
    MS_STT__NINE_ROUTER__MODEL      "provider/model", e.g. groq/whisper-large-v3-turbo
    MS_STT__NINE_ROUTER__API_KEY    optional (only needed if REQUIRE_API_KEY=true)
    MS_STT__NINE_ROUTER__HOTWORDS   medical terms → Whisper ``prompt``
    MS_STT__NINE_ROUTER__VAD_*      silence_ms / interim_ms / threshold / …
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
from ai.nine_router_client import (
    fetch_models,
    nine_router_headers,
    normalize_nine_router_base_url,
    resolve_model_id,
)
from ai.stt._common import VadEvent, VadSegmenter, pcm_duration_ms, pcm_to_wav

logger = logging.getLogger(__name__)


def nine_router_stt_configured(cfg: Mapping[str, Any]) -> bool:
    """Registry predicate: a base URL must be present to route here."""
    return bool((cfg or {}).get("base_url"))


def _map_language(language: str | None) -> str | None:
    """Mixed Persian/English → ``fa``; embedded English medical terms survive
    through the Whisper ``prompt``. ``None`` lets the upstream auto-detect."""
    if not language:
        return None
    return {"fa-en": "fa", "fa-en-mixed": "fa", "en-fa": "fa"}.get(language, language)


class NineRouterSttProvider(STTProvider):
    name = "9router"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        cfg = dict(config or {})
        if not cfg.get("base_url"):
            raise ProviderError(
                "9router STT requires 'base_url' (MS_STT__NINE_ROUTER__BASE_URL)",
                provider=self.name,
            )
        api_prefix = cfg.get("api_prefix")
        self._base = normalize_nine_router_base_url(
            str(cfg["base_url"]), api_prefix if api_prefix is not None else "/api"
        )
        self._model = str(cfg.get("model") or "").strip()
        self._api_key = str(cfg.get("api_key") or "") or None
        self._timeout_s = float(cfg.get("timeout_s", 300.0))
        #: ``verbose_json`` asks OpenAI-compatible upstreams for timed segments;
        #: upstreams that ignore it simply return {"text": …} and we degrade.
        self._response_format = str(cfg.get("response_format") or "verbose_json")
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
        raw_privacy = str(cfg.get("privacy_class") or "local").strip().lower()
        privacy = PrivacyClass.CLOUD if raw_privacy in ("cloud", "remote") else PrivacyClass.LOCAL
        if raw_privacy not in ("local", "cloud", "remote", ""):
            logger.warning("9router stt: unknown privacy_class %r — using LOCAL", raw_privacy)
        self._capabilities = ProviderCapabilities(
            privacy=privacy,
            supports_streaming=True,  # via VAD-windowed pseudo-streaming
            supports_batch=True,
            languages=("*",),
            cost_hint_per_unit=0.0,
            latency_hint_ms=int(cfg.get("latency_hint_ms", 1500)),
            extra={
                "model": self._model,
                "router": "9router",
                "streaming_mode": "vad-windowed",
            },
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def default_model(self) -> str:
        return self._model

    def _headers(self) -> dict[str, str]:
        return nine_router_headers(self._api_key)

    # -- model discovery -------------------------------------------------------

    async def list_models(self, kind: str = "stt") -> list[dict[str, Any]]:
        """Live STT catalog (``GET {base}/api/v1/models/stt``)."""
        return await fetch_models(
            self._client,
            self._base,
            kind=kind,
            headers=self._headers(),
            timeout=min(10.0, self._timeout_s),
            provider_name=self.name,
        )

    # -- batch -----------------------------------------------------------------

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        audio = request.audio
        if request.encoding == "wav" or audio[:4] == b"RIFF":
            wav, filename = audio, "audio.wav"
        else:
            wav = pcm_to_wav(audio, request.sample_rate, request.channels)
            filename = "audio.wav"
        data: dict[str, Any] = {
            "model": resolve_model_id(self._model, None),
            "response_format": self._response_format,
            "temperature": "0",
        }
        language = _map_language(request.language)
        if language:
            data["language"] = language
        prompt = self._prompt(request.context_hints)
        if prompt:
            data["prompt"] = prompt
        resp = await self._post(
            "/v1/audio/transcriptions",
            files={"file": (filename, wav, "audio/wav")},
            data=data,
        )
        return self._segments(self._parse_json(resp), request)

    def _prompt(self, context_hints: Any) -> str:
        """Baseline medical hotwords + per-request hints → Whisper ``prompt``.

        9Router forwards ``prompt`` to OpenAI-compatible upstreams; other
        formats drop it, which is safe (it is a bias hint, never a constraint).
        """
        terms = list(self._hotwords) + [str(h) for h in (context_hints or ())]
        seen: list[str] = []
        for term in terms:
            term = term.strip()
            if term and term not in seen:
                seen.append(term)
        return ", ".join(seen[:40])  # Whisper prompts are token-limited

    def _segments(
        self, body: Mapping[str, Any], request: STTRequest
    ) -> list[TranscriptSegment]:
        """Parse ``verbose_json`` segments when present, else a single span.

        Upstreams reachable through 9Router disagree about how much timing they
        return: OpenAI/Groq honour ``verbose_json``, while the Deepgram/Gemini/
        Nvidia/HuggingFace relays always answer ``{"text": …}``. Both are valid
        here — a whole-buffer segment still produces a usable transcript.
        """
        out: list[TranscriptSegment] = []
        for seg in body.get("segments") or []:
            if not isinstance(seg, Mapping):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            start = seg.get("start")
            end = seg.get("end")
            if isinstance(start, (int, float)):
                start_ms = int(float(start) * 1000)
                end_ms = int(float(end if isinstance(end, (int, float)) else start) * 1000)
            else:  # some relays report offsets in milliseconds
                start_ms = int(seg.get("offset_from") or seg.get("start_ms") or 0)
                end_ms = int(seg.get("offset_to") or seg.get("end_ms") or start_ms + 1)
            out.append(
                TranscriptSegment(
                    text=text,
                    start_ms=start_ms,
                    end_ms=max(end_ms, start_ms + 1),
                    is_final=True,
                    language=str(seg.get("language") or request.language or "") or None,
                )
            )
        if out:
            return out

        text = str(
            body.get("text") or body.get("transcript") or body.get("transcription") or ""
        ).strip()
        if not text:
            return []
        duration = body.get("duration")
        if isinstance(duration, (int, float)):
            end_ms = max(int(float(duration) * 1000), 1)
        else:
            end_ms = max(pcm_duration_ms(request.audio, request.sample_rate, request.channels), 1)
        return [
            TranscriptSegment(
                text=text,
                start_ms=0,
                end_ms=end_ms,
                is_final=True,
                language=request.language,
            )
        ]

    # -- pseudo-streaming ------------------------------------------------------

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
            raise ProviderUnavailableError(
                f"9router transcription failed: {exc}", provider=self.name
            ) from exc
        for seg in segments:
            # upstream timings are relative to the dispatched buffer — shift
            # them onto the session timeline
            yield TranscriptSegment(
                text=seg.text,
                start_ms=event.start_ms + seg.start_ms,
                end_ms=event.start_ms + max(seg.end_ms, seg.start_ms + 1),
                is_final=event.kind == "final",
                language=seg.language,
                confidence=seg.confidence,
            )

    # -- http plumbing ---------------------------------------------------------

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self._client.post(
                f"{self._base}{path}", headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"9router unreachable at {self._base}: {exc}", provider=self.name
            ) from exc
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"9router auth failed ({resp.status_code}) — check "
                "MS_STT__NINE_ROUTER__API_KEY",
                provider=self.name,
                status_hint=resp.status_code,
            )
        if resp.status_code == 429 or resp.status_code >= 500:
            # 9Router answers 503 when every connected account is rate-limited
            raise ProviderUnavailableError(
                f"9router error {resp.status_code}: {resp.text[:200]}", provider=self.name
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"9router rejected the transcription request ({resp.status_code}): "
                f"{resp.text[:200]}",
                provider=self.name,
                status_hint=resp.status_code,
            )
        return resp

    @staticmethod
    def _parse_json(resp: httpx.Response) -> Mapping[str, Any]:
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001 — non-JSON is a proxy/upstream fault
            raise ProviderError(
                "9router returned non-JSON from /v1/audio/transcriptions", provider="9router"
            ) from exc
        if not isinstance(body, Mapping):
            raise ProviderError(
                "9router returned an unexpected transcription payload", provider="9router"
            )
        return body

    async def health(self) -> ProviderHealth:
        """``GET {base}/api/v1/models/stt`` — proves reachability + auth and
        reports whether any STT-capable upstream is actually connected."""
        started = time.perf_counter()
        try:
            resp = await self._client.get(
                f"{self._base}/v1/models/stt",
                headers=self._headers(),
                timeout=min(5.0, self._timeout_s),
            )
        except Exception as exc:  # noqa: BLE001 — health is data, never a raise
            return ProviderHealth(
                ok=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=f"{type(exc).__name__} (is 9router running at {self._base}?)",
            )
        latency = round((time.perf_counter() - started) * 1000, 1)
        if resp.status_code in (401, 403):
            return ProviderHealth(
                ok=False,
                latency_ms=latency,
                detail=f"HTTP {resp.status_code} (check MS_STT__NINE_ROUTER__API_KEY)",
            )
        if resp.status_code != 200:
            return ProviderHealth(ok=False, latency_ms=latency, detail=f"HTTP {resp.status_code}")
        detail = None
        try:
            body = resp.json()
            items = body.get("data") if isinstance(body, Mapping) else body
            if not items:
                detail = "reachable, but no STT upstream is connected in 9router"
        except Exception:  # noqa: BLE001
            pass
        return ProviderHealth(ok=True, latency_ms=latency, detail=detail)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["NineRouterSttProvider", "nine_router_stt_configured"]
