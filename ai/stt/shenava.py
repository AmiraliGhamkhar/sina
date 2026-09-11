"""Shenava Koochik Persian STT — in-process sherpa-onnx adapter (name: shenava).

Runs the open Shenava-1 ``Koochik`` FastConformer cache-aware CTC model
(https://huggingface.co/Reza2kn/Shenava-Koochik-v1.0-tract-streaming, Apache-2.0)
directly inside the API process via `sherpa-onnx
<https://github.com/k2-fsa/sherpa-onnx>`_ — no external service needed. The
same graph family is distributed by sherpa-onnx as
``sherpa-onnx-nemo-ctc-fa-shenava-koochik-v1.0-streaming-*``; this adapter is
source-agnostic: it only needs ``model.onnx`` + ``tokens.txt`` paths.

sherpa-onnx is an OPTIONAL dependency (``pip install ".[local-ai]"``): the
import is lazy so the rest of the stack (and CI) works without it. The
``OnlineRecognizer.from_nemo_ctc`` loader implements the NeMo cache-aware
streaming contract (121-frame chunks, 112-frame shift, threaded caches) so
this adapter gets true incremental decoding — interims come from the live
decoder state, finals from the shared energy-VAD utterance boundaries.

Configuration (Settings → registry factory):

    MS_STT__SHENAVA__MODEL_PATH      default: auto-resolved from MS_MODELS__DIR
    MS_STT__SHENAVA__TOKENS_PATH     (model manager downloads to
    MS_STT__SHENAVA__NUM_THREADS      models/shenava-koochik/…)
    MS_STT__SHENAVA__VAD_*           silence_ms / interim_ms / threshold ...

Never logs or persists raw audio.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

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
from ai.stt._common import VadEvent, VadSegmenter, pcm_duration_ms

logger = logging.getLogger(__name__)

#: model manager's canonical install folder for this provider (see
#: api/services/model_catalog.py) — auto-configure convention when the
#: explicit MS_STT__SHENAVA__MODEL_PATH is not set.
CATALOG_ID = "shenava-koochik"
DEFAULT_MODEL_FILE = "model.int8.onnx"
DEFAULT_TOKENS_FILE = "tokens.txt"


def resolve_shenava_paths(
    model_path: str | None, tokens_path: str | None, models_dir: str | None
) -> tuple[str | None, str | None]:
    """Explicit config wins; otherwise resolve from the model-manager folder.

    This is the auto-configure seam: the moment the Shenava model finishes
    downloading into ``{MS_MODELS__DIR}/shenava-koochik/``, the provider
    becomes configured with no env changes or restart (in-process factory).
    """
    if model_path and tokens_path:
        return model_path, tokens_path
    base = Path(models_dir) / CATALOG_ID if models_dir else Path(CATALOG_ID)
    m = model_path or (str(base / DEFAULT_MODEL_FILE) if (base / DEFAULT_MODEL_FILE).is_file() else None)
    t = tokens_path or (str(base / DEFAULT_TOKENS_FILE) if (base / DEFAULT_TOKENS_FILE).is_file() else None)
    return m, t


def shenava_configured(cfg: Mapping[str, Any]) -> bool:
    return bool((cfg or {}).get("model_path") and (cfg or {}).get("tokens_path"))


def _load_runtime():
    """Import sherpa_onnx (+numpy) lazily; clear error when the extra is absent."""
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError as exc:  # pragma: no cover - exercised via fakes in tests
        raise ProviderUnavailableError(
            "shenava requires the 'local-ai' extra: pip install \".[local-ai]\" "
            "(sherpa-onnx + numpy)",
            provider="shenava",
        ) from exc
    return sherpa_onnx, np


class ShenavaProvider(STTProvider):
    """In-process Persian streaming STT. LOCAL by construction."""

    name = "shenava"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        model_path = cfg.get("model_path")
        tokens_path = cfg.get("tokens_path")
        if not model_path or not tokens_path:
            raise ProviderError(
                "shenava requires 'model_path' + 'tokens_path' "
                "(MS_STT__SHENAVA__* or model-manager download of 'shenava-koochik')",
                provider=self.name,
            )
        missing = [p for p in (model_path, tokens_path) if not Path(p).is_file()]
        if missing:
            raise ProviderError(
                f"shenava model files not found: {missing}",
                provider=self.name,
            )
        sherpa_onnx, _ = _load_runtime()
        try:
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_nemo_ctc(
                tokens=str(tokens_path),
                model=str(model_path),
                num_threads=int(cfg.get("num_threads", 2)),
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                provider="cpu",
            )
        except Exception as exc:  # noqa: BLE001 — bad file must not crash the caller
            raise ProviderError(
                f"shenava failed to load model '{model_path}': {exc}", provider=self.name
            ) from exc
        self._sherpa = sherpa_onnx
        self._sample_rate = 16000
        self._vad_kwargs = {
            "threshold": float(cfg.get("vad_threshold", 0.02)),
            "silence_ms": int(cfg.get("vad_silence_ms", 600)),
            "interim_ms": int(cfg.get("vad_interim_ms", 1200)),
            "pre_roll_ms": int(cfg.get("vad_pre_roll_ms", 300)),
            "max_segment_ms": int(cfg.get("max_segment_ms", 30000)),
        }
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.LOCAL,
            supports_streaming=True,
            supports_batch=True,
            languages=("fa",),
            cost_hint_per_unit=0.0,
            latency_hint_ms=int(cfg.get("latency_hint_ms", 600)),
            extra={
                "model": Path(model_path).name,
                "streaming_mode": "nemo-cache-aware-ctc",
                "engine": "sherpa-onnx",
            },
        )
        logger.info("shenava provider ready model=%s threads=%s", model_path, cfg.get("num_threads", 2))

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health(self) -> ProviderHealth:
        # constructed == model loaded; a cheap in-process probe (no audio)
        return ProviderHealth(ok=True)

    # -- helpers (sync pybind calls, dispatched off the event loop) ------------

    def _new_stream(self):
        return self._recognizer.create_stream()

    def _accept(self, stream, samples) -> None:
        stream.accept_waveform(self._sample_rate, samples)

    def _drain(self, stream) -> None:
        recognizer = self._recognizer
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)

    # -- batch ----------------------------------------------------------------

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        from ai.stt._common import wav_to_pcm

        data = request.audio
        if request.encoding == "wav" or data[:4] == b"RIFF":
            data, _, _ = wav_to_pcm(data)
        if not data:
            return []
        np = _load_runtime()[1]
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        stream = await asyncio.to_thread(self._new_stream)
        await asyncio.to_thread(self._accept, stream, samples)
        await asyncio.to_thread(stream.input_finished)
        await asyncio.to_thread(self._drain, stream)
        text = await asyncio.to_thread(self._recognizer.get_result, stream)
        text = (text or "").strip()
        if not text:
            return []
        return [
            TranscriptSegment(
                text=text,
                start_ms=0,
                end_ms=pcm_duration_ms(data, request.sample_rate or 16000),
                is_final=True,
                language="fa",
            )
        ]

    # -- streaming --------------------------------------------------------------

    async def stream(
        self, chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptSegment]:
        """Live transcription: VAD utterance boundaries, incremental decode.

        Audio flows into one persistent sherpa stream (cache-aware CTC state);
        the shared energy VAD only gates *segmentation* — interim hypotheses
        are emitted while speech continues, and a final segment (plus stream
        reset) when the VAD cuts the utterance.
        """
        np = _load_runtime()[1]
        segmenter = VadSegmenter(**self._vad_kwargs)
        stream = await asyncio.to_thread(self._new_stream)
        pending = bytearray()  # odd-byte guard between chunks
        pos_ms = 0

        def _events_to_segments(events: list[VadEvent]) -> list[TranscriptSegment]:
            out: list[TranscriptSegment] = []
            for ev in events:
                text = (self._recognizer.get_result(stream) or "").strip()
                if ev.kind == "interim":
                    if text:
                        out.append(
                            TranscriptSegment(
                                text=text,
                                start_ms=ev.start_ms,
                                end_ms=max(ev.end_ms, pos_ms),
                                is_final=False,
                                language="fa",
                            )
                        )
                else:  # final: close the utterance, reset decoder state
                    out.append(
                        TranscriptSegment(
                            text=text or "",
                            start_ms=ev.start_ms,
                            end_ms=max(ev.end_ms, ev.start_ms + 1),
                            is_final=True,
                            language="fa",
                        )
                    )
                    self._recognizer.reset(stream)
            return out

        async for chunk in chunks:
            if not chunk:
                continue
            pending += chunk
            usable = len(pending) - (len(pending) % 2)
            data = bytes(pending[:usable])
            del pending[:usable]
            events = segmenter.feed(data)
            pos_ms += pcm_duration_ms(data, self._sample_rate)
            if not data:
                continue
            samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            await asyncio.to_thread(self._accept, stream, samples)
            await asyncio.to_thread(self._drain, stream)
            for seg in await asyncio.to_thread(_events_to_segments, events):
                yield seg

        # end of stream: close any open utterance
        events = segmenter.flush()
        if events:
            for seg in await asyncio.to_thread(_events_to_segments, events):
                yield seg

    async def aclose(self) -> None:  # nothing to release (no sockets)
        return None
