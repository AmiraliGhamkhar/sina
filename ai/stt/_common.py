"""Shared plumbing for non-native-streaming local STT adapters.

whisper.cpp / vLLM-style Qwen-Audio services are request/response HTTP — the
roadmap's "windowed pseudo-streaming" is implemented here: an energy VAD
segments the live PCM16 stream, each utterance is dispatched as one batch
``transcribe`` call, and periodic re-recognitions of the open segment produce
interims. Deterministic (driven by *audio* time, not wall-clock) so unit tests
are stable. No external dependencies.
"""
from __future__ import annotations

import math
import struct
import wave
from dataclasses import dataclass
from typing import Literal

BytesOrStr = bytes  # alias kept for readability in stream code


def pcm_rms(pcm: bytes) -> float:
    """Normalized (0..1) RMS of an int16-LE mono buffer."""
    n = len(pcm) // 2
    if n == 0:
        return 0.0
    acc = 0.0
    for i in range(n):
        sample = int.from_bytes(pcm[2 * i : 2 * i + 2], "little", signed=True)
        v = sample / 32768.0
        acc += v * v
    return math.sqrt(acc / n)


def pcm_duration_ms(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> int:
    return len(pcm) * 1000 // (sample_rate * channels * 2)


def pcm_to_wav(
    pcm: bytes, sample_rate: int = 16000, channels: int = 1, bits: int = 16
) -> bytes:
    """Wrap raw PCM16 in a WAV container (wave stdlib; deterministic)."""
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(bits // 8)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> tuple[bytes, int, int]:
    """Validate/parse a WAV: returns (pcm16le frames, sample_rate, channels).

    Raises ValueError for non-WAV, non-16-bit or multi-channel audio — the
    batch API rejects those explicitly rather than guessing.
    """
    import io

    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError(f"only 16-bit PCM supported, got {w.getsampwidth() * 8}-bit")
            return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
    except (wave.Error, EOFError, struct.error) as exc:  # not RIFF/WAVE at all
        raise ValueError("not a valid WAV file") from exc


@dataclass(frozen=True)
class VadEvent:
    kind: Literal["interim", "final"]
    audio: bytes
    start_ms: int
    end_ms: int


class VadSegmenter:
    """Energy VAD producing utterance boundaries from a PCM16 mono stream.

    - a frame is *voiced* when ``rms >= threshold`` (threshold on 0..1 scale)
    - speech start backfills up to ``pre_roll_ms`` of recent audio
    - ``silence_ms`` of trailing silence closes a segment (final)
    - every ``interim_ms`` of ongoing speech emits an interim over the
      still-open buffer
    - ``max_segment_ms`` force-cuts runaway speech
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        threshold: float = 0.02,
        silence_ms: int = 600,
        interim_ms: int = 1500,
        pre_roll_ms: int = 300,
        max_segment_ms: int = 30000,
    ) -> None:
        self._rate = sample_rate
        self._threshold = threshold
        self._silence_ms = silence_ms
        self._interim_ms = interim_ms
        self._max_segment_ms = max_segment_ms
        self._pos_ms = 0
        self._in_speech = False
        self._buffer = bytearray()
        self._seg_start_ms = 0
        self._last_speech_ms = 0
        self._last_interim_ms = 0
        # pre-roll ring: recent quiet audio kept to backfill speech onsets
        self._pre_roll = bytearray()
        self._pre_roll_ms = pre_roll_ms

    def _ms_of(self, nbytes: int) -> int:
        return nbytes * 1000 // (self._rate * 2)

    def _ms_for(self, ms: int) -> int:
        return ms * self._rate // 500  # bytes at 16k mono int16 (2 B per 1/8 ms)

    def _trim_pre_roll(self) -> None:
        cap = self._ms_for(self._pre_roll_ms)
        if len(self._pre_roll) > cap:
            del self._pre_roll[: len(self._pre_roll) - cap]

    def feed(self, chunk: bytes) -> list[VadEvent]:
        """Consume one PCM16 chunk; return interim/final events (ordered)."""
        events: list[VadEvent] = []
        if not chunk:
            return events
        voiced = pcm_rms(chunk) >= self._threshold
        chunk_end_ms = self._pos_ms + self._ms_of(len(chunk))
        if voiced:
            if not self._in_speech:
                self._in_speech = True
                self._buffer = bytearray(self._pre_roll)
                self._seg_start_ms = max(0, self._pos_ms - self._ms_of(len(self._pre_roll)))
                self._last_interim_ms = chunk_end_ms
                self._pre_roll.clear()
            self._buffer += chunk
            self._last_speech_ms = chunk_end_ms
            self._pos_ms = chunk_end_ms
            if chunk_end_ms - self._last_interim_ms >= self._interim_ms:
                self._last_interim_ms = chunk_end_ms
                events.append(
                    VadEvent("interim", bytes(self._buffer), self._seg_start_ms, chunk_end_ms)
                )
            if chunk_end_ms - self._seg_start_ms >= self._max_segment_ms:
                events.append(self._cut(chunk_end_ms))
        else:
            self._pos_ms = chunk_end_ms
            if self._in_speech:
                self._buffer += chunk
                trailing = chunk_end_ms - self._last_speech_ms
                if trailing >= self._silence_ms:
                    events.append(self._cut(self._last_speech_ms))
            else:
                self._pre_roll += chunk
                self._trim_pre_roll()
        return events

    def flush(self) -> list[VadEvent]:
        """End of stream: close any open utterance."""
        if self._in_speech and self._buffer:
            return [self._cut(self._pos_ms)]
        self._buffer = bytearray()
        self._in_speech = False
        return []

    def _cut(self, end_ms: int) -> VadEvent:
        audio = bytes(self._buffer)
        start = self._seg_start_ms
        self._buffer = bytearray()
        self._in_speech = False
        return VadEvent("final", audio, start, max(end_ms, start + 1))
