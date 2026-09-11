"""Transcription hub — the WS session ↔ STT provider bridge (Phase 2).

One hub instance per live WebSocket session. Responsibilities:

- Ingest audio: binary WS frames and/or JSON ``audio.chunk`` (base64 PCM16)
  → bounded queue → :meth:`ai.base.STTProvider.stream` consumer.
- Relay provider segments as ``transcript.interim`` / ``transcript.final``
  frames through a shared send-lock (the control loop may interleave acks).
- Pause semantics (protocol doc): audio during pause is buffered up to
  ``pause_buffer_ms``; overflow is dropped with a single ``warning`` frame;
  on resume the buffered tail is re-injected.
- Final segments go to the :class:`TranscriptStore` (interims never stored).
- Faults: provider errors become typed ``error`` frames and feed the shared
  health tracker; the control loop decides whether to retry (fallback chains
  are Phase 5).

Back-pressure: a bounded queue with a short wait, then a DROP policy — a
slow provider must never grow memory unboundedly during a clinical session;
dropping audio is worse than pausing capture, so we also raise a warning the
UI surfaces.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

from ai.base import ProviderError, STTProvider, TranscriptSegment
from api.schemas.ws import ErrorFrame, TranscriptFinal, TranscriptInterim, WarningFrame
from api.services.transcript_store import TranscriptStore

logger = logging.getLogger(__name__)

SendFrame = Callable[[dict], Awaitable[None]]

#: 16 kHz mono PCM16 — the format negotiated in ``session.start.audio``
DEFAULT_BYTES_PER_SECOND = 16_000 * 2


class TranscriptionHub:
    def __init__(
        self,
        *,
        provider: STTProvider,
        session_id: str,
        store: TranscriptStore,
        send_frame: SendFrame,
        metrics,
        health_tracker=None,
        audio_q_maxsize: int = 512,
        pause_buffer_ms: int = 2000,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self._bytes_per_second = float(max(1, sample_rate) * max(1, channels) * 2)
        self.provider = provider
        self.session_id = session_id
        self._store = store
        self._send = send_frame
        self._metrics = metrics
        self._health = health_tracker
        self._audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=audio_q_maxsize)
        self._pump: asyncio.Task | None = None
        self._closed = False
        self._paused = False
        self._pause_buf = bytearray()
        self._pause_buffer_bytes = max(1, int(self._bytes_per_second * pause_buffer_ms / 1000))
        self._pause_overflow_warned = False
        self._audio_bytes = 0
        self._dropped_bytes = 0
        self._interim_count = 0
        self._final_count = 0
        self._utterance_seq = 0
        self._pending_id: str | None = None
        self._started_at = time.time()
        self._stream_failed = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._pump is None:
            self._pump = asyncio.create_task(self._run(), name=f"stt-hub-{self.session_id}")

    async def stop(self, *, timeout_s: float = 10.0) -> dict:
        """Close the feed, wait for the provider to flush, return stats."""
        if self._closed:
            return self.stats()
        self._closed = True
        self._pause_buf.clear()  # paused audio never enters the transcript
        try:
            self._audio_q.put_nowait(None)
        except asyncio.QueueFull:
            # drop the oldest queued chunk to make room for the sentinel
            try:
                self._audio_q.get_nowait()
                self._audio_q.put_nowait(None)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass
        if self._pump is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._pump), timeout=timeout_s)
            except TimeoutError:
                self._pump.cancel()
                await self._send(
                    WarningFrame(
                        session_id=self.session_id,
                        code="PROVIDER_FLUSH_TIMEOUT",
                        message="provider did not flush within timeout; tail audio was dropped",
                    ).model_dump(mode="json")
                )
            except Exception:  # pump already reported its own error frame
                logger.debug("hub pump ended with error", exc_info=True)
        return self.stats()

    def stats(self) -> dict:
        return {
            "audio_seconds": round(self._audio_bytes / self._bytes_per_second, 2),
            "dropped_audio_seconds": round(self._dropped_bytes / self._bytes_per_second, 2),
            "interim_frames": self._interim_count,
            "final_segments": self._final_count,
            "stream_failed": self._stream_failed,
            "duration_ms": int((time.time() - self._started_at) * 1000),
        }

    # -- audio in ------------------------------------------------------------

    async def feed(self, data: bytes) -> None:
        """Called by the WS route for every inbound audio frame (already decoded)."""
        if self._closed or not data:
            return
        if self._paused:
            self._pause_buf.extend(data)
            if len(self._pause_buf) > self._pause_buffer_bytes:
                overflow = len(self._pause_buf) - self._pause_buffer_bytes
                del self._pause_buf[:overflow]
                self._dropped_bytes += overflow
                if not self._pause_overflow_warned:
                    self._pause_overflow_warned = True
                    await self._send(
                        WarningFrame(
                            session_id=self.session_id,
                            code="AUDIO_DROPPED_PAUSED",
                            message="paused > buffer window; additional audio discarded",
                        ).model_dump(mode="json")
                    )
            return
        self._audio_bytes += len(data)
        try:
            self._audio_q.put_nowait(data)
        except asyncio.QueueFull:
            try:
                await asyncio.wait_for(self._audio_q.put(data), timeout=0.25)
            except TimeoutError:
                self._dropped_bytes += len(data)
                self._metrics.incr("stt_audio_dropped_bytes_frames")
                await self._send(
                    WarningFrame(
                        session_id=self.session_id,
                        code="AUDIO_OVERLOADED",
                        message="transcription queue saturated; frame dropped — check provider latency",
                    ).model_dump(mode="json")
                )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        buffered, self._pause_buf = bytes(self._pause_buf), bytearray()
        self._pause_overflow_warned = False
        if buffered:
            # re-inject ≤ pause-buffer tail synchronously (queue has room by cap)
            try:
                self._audio_q.put_nowait(buffered)
                self._audio_bytes += len(buffered)
            except asyncio.QueueFull:
                self._dropped_bytes += len(buffered)

    # -- provider pump ---------------------------------------------------------

    async def _audio_iterator(self):
        while True:
            item = await self._audio_q.get()
            if item is None:
                return
            yield item

    async def _run(self) -> None:
        kind_key = f"stt:{self.provider.name}"
        started = time.perf_counter()
        first_segment_seen = False
        try:
            async for segment in self.provider.stream(self._audio_iterator()):
                if not first_segment_seen:
                    first_segment_seen = True
                    self._metrics.observe_ms("stt_first_segment_ms", (time.perf_counter() - started) * 1000)
                await self._emit(segment)
            if self._health is not None:
                self._health.record_success(kind_key, (time.perf_counter() - started) * 1000)
        except ProviderError as exc:
            self._stream_failed = True
            if self._health is not None:
                self._health.record_failure(kind_key)
            logger.warning("provider stream error (%s): %s", self.provider.name, type(exc).__name__)
            await self._send(
                ErrorFrame(
                    session_id=self.session_id,
                    code="PROVIDER_UNAVAILABLE",
                    message=str(exc)[:300],
                    recoverable=bool(getattr(exc, "retryable", True)),
                ).model_dump(mode="json")
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # provider bug — keep the socket usable, tell the client
            self._stream_failed = True
            if self._health is not None:
                self._health.record_failure(kind_key)
            logger.exception("provider stream crashed")
            await self._send(
                ErrorFrame(
                    session_id=self.session_id,
                    code="INTERNAL",
                    message="transcription stream crashed (see server log; no transcript data was logged)",
                    recoverable=False,
                ).model_dump(mode="json")
            )

    async def _emit(self, segment: TranscriptSegment) -> None:
        # Stable per-utterance id: the first interim of an utterance opens it,
        # the final closes it. Provider-supplied ids always win.
        if segment.segment_id:
            seg_id = segment.segment_id
        elif segment.is_final:
            seg_id = self._pending_id or f"seg_{self._utterance_seq:04d}"
        else:
            if self._pending_id is None:
                self._pending_id = f"seg_{self._utterance_seq:04d}"
            seg_id = self._pending_id

        if segment.is_final:
            stored = self._store.append_final(
                self.session_id, replace(segment, segment_id=seg_id)
            )
            self._final_count += 1
            self._utterance_seq += 1
            self._pending_id = None
            self._metrics.incr("stt_segments_final")
            frame = TranscriptFinal(
                session_id=self.session_id,
                segment_id=stored.segment_id if stored else seg_id,
                text=segment.text,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                language=segment.language,
                confidence=segment.confidence,
            )
        else:
            self._interim_count += 1
            frame = TranscriptInterim(
                session_id=self.session_id,
                segment_id=seg_id,
                text=segment.text,
                start_ms=segment.start_ms,
            )
        await self._send(frame.model_dump(mode="json"))
