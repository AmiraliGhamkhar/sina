"""Speechmatics adapter — realtime WebSocket streaming + batch job API.

Live:  ``wss://{region}.rt.speechmatics.com/v2`` — ``StartRecognition``
handshake (``message`` discriminator, object ``audio_format``, nested
``transcription_config``), binary ``AddAudio`` frames, then
``AddPartialTranscript`` / ``AddTranscript`` messages translated into
:class:`TranscriptSegment` events (partials → interims, finals → segment
commits), terminated by ``EndOfStream`` → ``EndOfTranscript``.

Batch: job lifecycle ``POST /api/v2/jobs`` → ``PATCH …/send-audio`` →
``GET …/jobs/{id}`` poll → ``GET …/result``. Persian configured with
punctuation enabled; ``operating_params`` passthrough lets ops tune without a
code change.

    MS_STT__SPEECHMATICS__API_KEY  (required to route here)
    MS_STT__SPEECHMATICS__REGION   eu | us | au | global  (realtime)
    MS_STT__SPEECHMATICS__LANGUAGE fa (default)
    MS_STT__SPEECHMATICS__OPERATING_DOMAIN general

Realtime note: ``region`` selects the *realtime* cluster (``{region}.rt.…``);
``global`` auto-routes to the nearest region for lowest latency, while a pinned
region is what you want for data residency. Batch hosts keep their own map —
the two address spaces are genuinely different at Speechmatics.

The WS client sits behind the same transport seam as the Deepgram adapter so
protocol tests are network-free.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
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
from ai.stt._common import pcm_duration_ms
from ai.stt.ws_transport import ConnectionClosed, WsConnector, default_connector

logger = logging.getLogger(__name__)

#: Batch (Jobs API) hosts, keyed by the legacy region names in existing configs.
_BATCH_REGION_HOSTS = {
    "eu2": "https://eu2.asr.speechmatics.com/v2",
    "us": "https://us.asr.speechmatics.com/v2",
}

#: Realtime hosts. Speechmatics Realtime SaaS is addressed as
#: ``wss://{region}.rt.speechmatics.com/v2`` — a different address space from
#: Batch, and with no ``/stream/ws`` suffix. ``global`` auto-routes each
#: connection to the nearest region; pin a region when data residency matters.
_RT_REGION_HOSTS = {
    "global": "wss://global.rt.speechmatics.com/v2",
    "eu": "wss://eu.rt.speechmatics.com/v2",
    "us": "wss://us.rt.speechmatics.com/v2",
    "au": "wss://au.rt.speechmatics.com/v2",
    # tolerated spellings
    "eu1": "wss://eu.rt.speechmatics.com/v2",
    "us1": "wss://us.rt.speechmatics.com/v2",
    "au1": "wss://au.rt.speechmatics.com/v2",
    # legacy aliases: eu2/us2 name *Batch* enterprise clusters with no
    # realtime counterpart, so they fall back to the nearest public region.
    "eu2": "wss://eu.rt.speechmatics.com/v2",
    "us2": "wss://us.rt.speechmatics.com/v2",
}
_DEFAULT_BATCH_REGION = "eu2"
_DEFAULT_RT_REGION = "eu"

#: WebSocket close payload → documented error type.
#: https://docs.speechmatics.com/api-ref/realtime-transcription-websocket#websocket-errors
_RT_CLOSE_CODES = {
    1003: "protocol_error",
    1008: "policy_violation",
    1011: "internal_error",
    4001: "not_authorised",
    4003: "not_allowed",
    4005: "quota_exceeded",
    4013: "job_error",
}
#: The docs recommend a 5–10 s client retry interval for exactly these three.
_RT_RETRYABLE_CLOSE_CODES = frozenset({1011, 4005, 4013})

#: In-band ``Error`` message types worth retrying (vs. a config/protocol fault
#: that will fail identically every time).
_RT_RETRYABLE_ERROR_TYPES = frozenset(
    {
        "quota_exceeded",
        "timelimit_exceeded",
        "job_error",
        "idle_timeout",
        "session_timeout",
        "unknown_error",
    }
)

#: ``transcription_config.max_delay`` is documented as ``>= 0.7`` and ``<= 4``.
_MAX_DELAY_RANGE = (0.7, 4.0)



def speechmatics_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("api_key"))


def _clamp_max_delay(value: float) -> float:
    """``transcription_config.max_delay`` is documented as ``>= 0.7, <= 4``.

    Clamping beats sending a value the API rejects with ``invalid_config`` —
    the operator's intent (lower latency) is preserved as closely as allowed.
    """
    low, high = _MAX_DELAY_RANGE
    if value < low or value > high:
        logger.warning(
            "speechmatics max_delay %.2f outside [%.1f, %.1f] — clamped", value, low, high
        )
    return max(low, min(high, value))


def _normalize_vocab(raw: Any) -> tuple[Any, ...]:
    """``additional_vocab`` accepts bare strings or
    ``{"content": …, "sounds_like": […]}`` objects; pass both through, drop junk.
    """
    out: list[Any] = []
    for item in raw or ():
        if isinstance(item, Mapping):
            content = str(item.get("content") or "").strip()
            if content:
                out.append(dict(item))
        elif str(item).strip():
            out.append(str(item).strip())
    return tuple(out)


def _first_number(
    metadata: Mapping[str, Any], results: list[Mapping[str, Any]], key: str
) -> float:
    """Segment start: ``metadata`` first, then the earliest ``results`` entry.

    Timings are in **seconds** (float) throughout the Realtime API.
    """
    value = metadata.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    for res in results:
        value = res.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _last_number(
    metadata: Mapping[str, Any], results: list[Mapping[str, Any]], key: str, fallback: float
) -> float:
    """Segment end: ``metadata`` first, then the latest ``results`` entry."""
    value = metadata.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    for res in reversed(results):
        value = res.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return fallback


def _mean_word_confidence(results: list[Mapping[str, Any]]) -> float | None:
    """Average of each ``word`` result's top alternative confidence.

    Punctuation and entity results carry no meaningful confidence, and only the
    chosen (first) alternative is a statement about this transcript.
    """
    values: list[float] = []
    for res in results:
        if res.get("type") != "word":
            continue
        alternatives = res.get("alternatives") or []
        first = alternatives[0] if alternatives else None
        if not isinstance(first, Mapping):
            continue
        confidence = first.get("confidence")
        if isinstance(confidence, (int, float)):
            values.append(float(confidence))
    if not values:
        return None
    return round(sum(values) / len(values), 4)


class SpeechmaticsProvider(STTProvider):
    name = "speechmatics"

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
            raise ProviderError("speechmatics requires 'api_key'", provider=self.name)
        region = str(cfg.get("region") or _DEFAULT_BATCH_REGION).lower().strip()
        batch_default = _BATCH_REGION_HOSTS.get(region, _BATCH_REGION_HOSTS[_DEFAULT_BATCH_REGION])
        rt_default = _RT_REGION_HOSTS.get(region, _RT_REGION_HOSTS[_DEFAULT_RT_REGION])
        self._batch_url = str(cfg.get("batch_url") or batch_default).rstrip("/")
        self._rt_url = str(cfg.get("rt_url") or rt_default).rstrip("/")
        self._region = region
        self._language = str(cfg.get("language") or "en")
        self._domain = str(cfg.get("operating_domain") or "general")
        self._timeout_s = float(cfg.get("timeout_s", 60.0))
        self._poll_interval = float(cfg.get("job_poll_interval_s", 1.0))
        self._job_timeout = float(cfg.get("job_timeout_s", 300.0))
        self._max_retries = int(cfg.get("max_retries", 2))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_s)
        self._connector = transport_factory or default_connector

        # -- realtime knobs (all optional; defaults match the documented API) --
        self._rt_sample_rate = int(cfg.get("rt_sample_rate", 16000))
        self._max_delay = _clamp_max_delay(float(cfg.get("max_delay", 3.5)))
        self._enable_partials = bool(cfg.get("enable_partials", True))
        self._diarization = str(cfg.get("diarization") or "none")
        self._additional_vocab = _normalize_vocab(cfg.get("additional_vocab"))
        #: a session that boots with ``additional_vocab`` can take up to 15 s to
        #: acknowledge StartRecognition (documented), hence the wide default.
        self._handshake_timeout = float(cfg.get("handshake_timeout_s", 20.0))
        self._rt_idle_timeout = float(cfg.get("rt_idle_timeout_s", 30.0))
        # Realtime spells the specialized-model field ``domain``; reuse the
        # batch operating_domain unless it is the neutral default.
        rt_domain = cfg.get("rt_domain") or (
            self._domain if self._domain and self._domain != "general" else None
        )
        self._rt_domain = str(rt_domain).strip() if rt_domain else None

        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.CLOUD,
            supports_streaming=True,
            supports_batch=True,
            languages=("fa", "en", "*"),
            cost_hint_per_unit=float(cfg.get("cost_hint_per_minute", 0.006)),
            latency_hint_ms=900,
            extra={"region": self._region, "domain": self._domain},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def _map_language(language: str | None, fallback: str) -> str:
        if not language:
            return fallback
        return {"fa-en": "fa", "fa-en-mixed": "fa"}.get(language, language)

    # -- batch job lifecycle ---------------------------------------------------

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        config = {
            "type": "transcription",
            "audio_format": "pcm_s16le" if request.encoding != "wav" else "wav",
            "sampling_rate": request.sample_rate,
            "max_delay": 120,
            "operating_params": {
                "language": self._map_language(request.language, self._language),
                "operating_domain": self._domain,
                "enable_punctuation": True,
                "enable_formatting": True,
            },
        }
        if request.context_hints:
            config["operating_params"]["custom_vocabulary"] = [str(h) for h in request.context_hints]
        headers = self._auth()
        try:
            start = await self._client.post(
                f"{self._batch_url}/jobs", headers=headers, json=config
            )
            self._raise_for(start, "job start")
            request_id = start.json()["request_id"]
            send = await self._client.patch(
                f"{self._batch_url}/jobs/{request_id}/send-audio",
                headers={**headers, "Content-Type": "application/octet-stream"},
                content=request.audio,
            )
            if send.status_code not in (200, 202):
                raise ProviderError(
                    f"speechmatics audio upload failed ({send.status_code})",
                    provider=self.name,
                    status_hint=send.status_code,
                )
            body = await self._await_result(request_id, headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"speechmatics unreachable: {exc}", provider=self.name) from exc
        return self._parse_batch_result(body, request)

    async def _await_result(self, request_id: str, headers: dict[str, str]) -> Mapping[str, Any]:
        deadline = time.perf_counter() + self._job_timeout
        delay = self._poll_interval
        while True:
            status = await self._client.get(f"{self._batch_url}/jobs/{request_id}", headers=headers)
            self._raise_for(status, "job status")
            state = status.json().get("job_status")
            if state == "done":
                break
            if state == "error":
                raise self._job_error(status.json())
            if time.perf_counter() > deadline:
                raise ProviderUnavailableError(
                    f"speechmatics job {request_id} timed out", provider=self.name
                )
            await asyncio.sleep(min(delay, max(0.05, deadline - time.perf_counter())))
            delay = min(delay * 2, 5.0)  # bounded poll backoff
        result = await self._client.get(
            f"{self._batch_url}/jobs/{request_id}/result",
            headers=headers,
            params={"scheme": "2", "srt": "false"},
        )
        self._raise_for(result, "job result")
        return result.json()

    @staticmethod
    def _job_error(body: Mapping[str, Any]) -> ProviderError:
        detail = str((body.get("status") or {}).get("reason") or "job failed")
        return ProviderError(f"speechmatics: {detail}", provider="speechmatics")

    @staticmethod
    def _raise_for(resp: httpx.Response, what: str) -> None:
        if resp.status_code in (401, 403):
            raise ProviderError(
                f"speechmatics auth failed on {what}", provider="speechmatics", status_hint=resp.status_code
            )
        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"speechmatics {what} error {resp.status_code}", provider="speechmatics"
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"speechmatics rejected {what} ({resp.status_code})",
                provider="speechmatics",
                status_hint=resp.status_code,
            )

    def _parse_batch_result(
        self, body: Mapping[str, Any], request: STTRequest
    ) -> list[TranscriptSegment]:
        results = body.get("results") or {}
        text = str(results.get("transcript", "")).strip()
        out: list[TranscriptSegment] = []
        elements = results.get("elements") or []
        if elements:
            # sentence-split on end-of-utterance marks; timestamps are seconds
            current: list[dict] = []
            for el in elements:
                current.append(el)
                if el.get("end_time") is not None and (el.get("is_end_of_transcript") or el.get("punctuation")):
                    out.append(self._segment_from_elements(current, request))
                    current = []
            if current:
                out.append(self._segment_from_elements(current, request))
        elif text:
            dur = pcm_duration_ms(request.audio, request.sample_rate, request.channels)
            out.append(
                TranscriptSegment(
                    text=text,
                    start_ms=0,
                    end_ms=max(dur, 1),
                    confidence=results.get("metadata", {}).get("confidence"),
                    language=request.language,
                )
            )
        return out

    @staticmethod
    def _segment_from_elements(elements: list[dict], request: STTRequest) -> TranscriptSegment:
        words = []
        confidences = []
        for el in elements:
            value = str(el.get("value", el.get("text", ""))).strip()
            if el.get("type") == "Punctuation" and value in {".", "!", "?", ","}:
                if value in {".", "!", "?"}:
                    words.append(value)
            elif value:
                words.append(value)
            if isinstance(el.get("confidence"), (int, float)):
                confidences.append(float(el["confidence"]))
        start_ms = int(float(elements[0].get("start_time", 0.0)) * 1000)
        end_ms = int(float(elements[-1].get("end_time", elements[0].get("start_time", 0.0))) * 1000)
        text = " ".join(words)
        for a, b in ((" ,", ","), (" .", "."), (" ?", "?"), (" !", "!")):
            text = text.replace(a, b)
        return TranscriptSegment(
            text=text.strip(),
            start_ms=start_ms,
            end_ms=max(end_ms, start_ms + 1),
            confidence=sum(confidences) / len(confidences) if confidences else None,
            language=request.language,
        )

    # -- realtime streaming ---------------------------------------------------------

    def _start_recognition_message(self) -> dict[str, Any]:
        """``StartRecognition`` exactly as the Realtime API documents it.

        Three shapes matter and all three differ from the legacy layout: the
        discriminator field is ``message`` (not ``message_type``),
        ``audio_format`` is an *object* carrying ``type``/``encoding``/
        ``sample_rate`` (not a bare string plus a sibling ``sampling_rate``),
        and every recognition knob lives inside the **required**
        ``transcription_config`` object.
        """
        transcription_config: dict[str, Any] = {
            "language": self._language,
            "max_delay": self._max_delay,
            "enable_partials": self._enable_partials,
        }
        if self._rt_domain:
            transcription_config["domain"] = self._rt_domain
        if self._diarization and self._diarization != "none":
            transcription_config["diarization"] = self._diarization
        if self._additional_vocab:
            transcription_config["additional_vocab"] = list(self._additional_vocab)
        return {
            "message": "StartRecognition",
            "audio_format": {
                "type": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self._rt_sample_rate,
            },
            "transcription_config": transcription_config,
        }

    async def stream(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]:
        transport = await self._connector(self._rt_url, self._auth())
        pump: asyncio.Task[None] | None = None
        try:
            await transport.send_text(json.dumps(self._start_recognition_message()))
            ack = await self._await_message(
                transport, "RecognitionStarted", timeout=self._handshake_timeout
            )
            if ack is None:
                raise ProviderUnavailableError(
                    "speechmatics did not acknowledge StartRecognition", provider=self.name
                )
            logger.debug(
                "speechmatics session started id=%s", ack.get("id"),
            )
            pump = asyncio.create_task(self._pump(transport, chunks))
            try:
                async for seg in self._read_results(transport):
                    yield seg
                # let an audio-send failure surface instead of being swallowed
                await pump
            except BaseException:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pump
                raise
        except ProviderError:
            raise
        except ConnectionClosed as exc:
            self._raise_for_close(exc)
        finally:
            await transport.close()

    async def _pump(self, transport: Any, chunks: AsyncIterator[bytes]) -> None:
        """Send binary ``AddAudio`` frames, then ``EndOfStream``.

        ``EndOfStream.last_seq_no`` is required and counts the audio frames
        sent; the server echoes each one back as ``AudioAdded.seq_no``.
        """
        seq_no = 0
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                await transport.send_bytes(chunk)
                seq_no += 1
        finally:
            with contextlib.suppress(ConnectionClosed, OSError):
                await transport.send_text(
                    json.dumps({"message": "EndOfStream", "last_seq_no": seq_no})
                )

    async def _await_message(
        self, transport: Any, message: str, *, timeout: float
    ) -> Mapping[str, Any] | None:
        """Wait for one named server message; surface ``Error`` immediately."""
        deadline = time.perf_counter() + timeout
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(transport.recv(), timeout=remaining)
            except ConnectionClosed as exc:
                self._raise_for_close(exc)
                return None
            except TimeoutError:
                return None
            if isinstance(raw, bytes):
                continue  # AudioAdded is JSON, but tolerate stray binary
            msg = self._decode(raw)
            if msg is None:
                continue
            name = msg.get("message")
            if name == message:
                return msg
            if name == "Error":
                raise self._error_from_message(msg)

    async def _read_results(self, transport: Any) -> AsyncIterator[TranscriptSegment]:
        while True:
            try:
                raw = await asyncio.wait_for(transport.recv(), timeout=self._rt_idle_timeout)
            except ConnectionClosed as exc:
                self._raise_for_close(exc)
                return
            except TimeoutError as exc:
                raise ProviderUnavailableError(
                    f"speechmatics realtime idle timeout ({self._rt_idle_timeout}s without a "
                    "message)",
                    provider=self.name,
                ) from exc
            if isinstance(raw, bytes):
                continue
            msg = self._decode(raw)
            if msg is None:
                continue
            name = msg.get("message")
            if name == "EndOfTranscript":
                return
            if name == "Error":
                raise self._error_from_message(msg)
            if name in ("AddTranscript", "AddPartialTranscript"):
                seg = self._segment_from_transcript(msg, final=name == "AddTranscript")
                if seg is not None:
                    yield seg
                continue
            if name in ("Warning", "Info"):
                # duration_limit_exceeded / idle_timeout / recognition_quality …
                # operational signal, never a transcript — log and carry on.
                logger.info(
                    "speechmatics realtime %s type=%s reason=%s",
                    name,
                    msg.get("type"),
                    str(msg.get("reason"))[:200],
                )
                continue
            # RecognitionStarted / AudioAdded / EndOfUtterance /
            # AddTranslation / AddPartialTranslation / SpeakersResult —
            # acknowledged, nothing to emit for transcription.

    @staticmethod
    def _decode(raw: str) -> Mapping[str, Any] | None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("speechmatics sent a non-JSON text frame (%d bytes)", len(raw))
            return None
        return msg if isinstance(msg, Mapping) else None

    def _segment_from_transcript(
        self, msg: Mapping[str, Any], *, final: bool
    ) -> TranscriptSegment | None:
        """``AddTranscript`` / ``AddPartialTranscript`` → one segment.

        ``metadata.transcript`` is the documented, already-formatted text for
        the segment ("we have taken care of all the necessary formatting"), so
        it is the primary source; ``results`` is the fallback and supplies the
        timings and confidences either way.
        """
        metadata = msg.get("metadata") if isinstance(msg.get("metadata"), Mapping) else {}
        results = [r for r in (msg.get("results") or []) if isinstance(r, Mapping)]
        text = str(metadata.get("transcript") or "").strip() or self._text_from_results(results)
        if not text:
            return None
        start_ms = int(float(_first_number(metadata, results, "start_time")) * 1000)
        end_ms = int(float(_last_number(metadata, results, "end_time", start_ms / 1000)) * 1000)
        return TranscriptSegment(
            text=text,
            start_ms=start_ms,
            end_ms=max(end_ms, start_ms + 1),
            is_final=final,
            # "For AddPartialTranscript messages the confidence field for
            # alternatives has no meaning and should not be relied on."
            confidence=None if not final else _mean_word_confidence(results),
            language=self._language or None,
        )

    @staticmethod
    def _text_from_results(results: list[Mapping[str, Any]]) -> str:
        """Assemble text from ``word``/``punctuation`` results.

        ``attaches_to`` says how a mark binds: ``previous`` glues to the token
        before, ``next`` to the one after, ``both`` to each side (hyphens,
        apostrophes). Getting this wrong is very visible in Persian, where a
        stray space around the ZWNJ changes the word.
        """
        tokens: list[str] = []
        glue_next = False
        for res in results:
            if res.get("type") not in ("word", "punctuation"):
                continue
            alternatives = res.get("alternatives") or []
            first = alternatives[0] if alternatives else None
            content = str(first.get("content") or "").strip() if isinstance(first, Mapping) else ""
            if not content:
                continue
            # attaches_to is an enum — next | previous | none | both — so match
            # the whole value; substring tests get "both" wrong in both
            # directions and drop the hyphen.
            attaches_to = str(res.get("attaches_to") or "none")
            is_punctuation = res.get("type") == "punctuation"
            if tokens and (
                glue_next or (is_punctuation and attaches_to in ("previous", "both"))
            ):
                tokens[-1] = tokens[-1] + content
            else:
                tokens.append(content)
            glue_next = is_punctuation and attaches_to in ("next", "both")
        return " ".join(tokens).strip()

    def _error_from_message(self, msg: Mapping[str, Any]) -> ProviderError:
        """Map an in-band ``Error`` onto the retryable/non-retryable contract.

        After any ``Error`` the server terminates transcription and closes the
        connection, so this is always fatal for the stream — the only question
        is whether another provider (or a later retry) could succeed.
        """
        etype = str(msg.get("type") or "unknown_error")
        reason = str(msg.get("reason") or etype)
        code = msg.get("code")
        detail = f"speechmatics realtime {etype}: {reason}"
        if code is not None:
            detail += f" (code {code})"
        if etype in _RT_RETRYABLE_ERROR_TYPES:
            return ProviderUnavailableError(detail, provider=self.name)
        return ProviderError(
            detail,
            provider=self.name,
            status_hint=401 if etype == "not_authorised" else 400,
        )

    def _raise_for_close(self, exc: ConnectionClosed) -> None:
        """Translate a WS close into the provider error contract.

        A close with no code is an orderly end-of-session (we already saw
        ``EndOfTranscript``, or the test script ran out) — not an error.
        """
        code = exc.close_code
        if code is None:
            return
        label = _RT_CLOSE_CODES.get(code, "error")
        if code in (4001, 4003):
            raise ProviderError(
                f"speechmatics realtime closed {code} ({label}) — check "
                "MS_STT__SPEECHMATICS__API_KEY",
                provider=self.name,
                status_hint=401 if code == 4001 else 403,
            ) from exc
        detail = f"speechmatics realtime closed {code} ({label})"
        if code in _RT_RETRYABLE_CLOSE_CODES:
            detail += " — retry after 5-10s per Speechmatics guidance"
        raise ProviderUnavailableError(detail, provider=self.name) from exc

    # -- health -------------------------------------------------------------------

    async def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            resp = await self._client.get(f"{self._batch_url}/account", headers=self._auth(), timeout=5.0)
            if resp.status_code == 200:
                return ProviderHealth(ok=True, latency_ms=(time.perf_counter() - started) * 1000)
            return ProviderHealth(
                ok=False, detail=f"HTTP {resp.status_code} (check MS_STT__SPEECHMATICS__API_KEY)"
            )
        except Exception as exc:
            return ProviderHealth(ok=False, detail=f"{type(exc).__name__}")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
