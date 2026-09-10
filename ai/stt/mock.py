"""Mock STT provider.

Interface-parity provider (pattern seen in open-medical-scribe's
mockProvider.js, reimplemented for the Python ABCs here). It exists so that
the full pipeline — router → provider → segment stream → WebSocket → WPF —
can be exercised in CI with zero external dependencies and a deterministic,
mixed Persian/English corpus.

The canned script intentionally includes:
- mixed fa/en medical terms (MRI, ECG, hypertension)
- dosage + units (mg)
- a negation ("no effusion")
so downstream validation tests have meaningful input without real audio.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
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

DEFAULT_SCRIPT: tuple[Mapping[str, Any], ...] = (
    {
        "text": "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است.",
        "start_ms": 0,
        "end_ms": 3200,
        "language": "fa-en",
    },
    {
        "text": "سابقه hypertension دارد و لوزارتان 50 میلی‌گرم روزانه مصرف می‌کند.",
        "start_ms": 3200,
        "end_ms": 7400,
        "language": "fa-en",
    },
    {
        "text": "اکوکاردیوگرافی انجام شد؛ no effusion دیده نشد. MRI مغز نرمال گزارش شد.",
        "start_ms": 7400,
        "end_ms": 12000,
        "language": "fa-en",
    },
)


class MockSttProvider(STTProvider):
    name = "mock"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        script = cfg.get("script") or DEFAULT_SCRIPT
        self._script: tuple[Mapping[str, Any], ...] = tuple(script)
        self._interim_delay = float(cfg.get("interim_delay_s", 0.05))
        self._fail_with: str | None = cfg.get("fail_with")  # tests inject faults
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.LOCAL,
            supports_streaming=True,
            languages=("fa", "en", "fa-en"),
            latency_hint_ms=1,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def health(self) -> ProviderHealth:
        if self._fail_with:
            raise ProviderUnavailableError(self._fail_with, provider=self.name)
        return ProviderHealth(ok=True, latency_ms=0.1)

    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        if self._fail_with:
            raise ProviderError(self._fail_with, provider=self.name, retryable=False)
        return [self._segment(s, final=True) for s in self._script]

    async def stream(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]:
        """Emit word-by-word interims then a final per scripted segment.

        Audio bytes are drained (so back-pressure behaves like a real
        provider) but not analyzed — this is a mock.
        """
        if self._fail_with:
            raise ProviderError(self._fail_with, provider=self.name, retryable=False)
        consumed = asyncio.ensure_future(self._drain(chunks))
        try:
            for scripted in self._script:
                segment = self._segment(scripted, final=True)
                words = segment.text.split(" ")
                partial: list[str] = []
                for word in words[:-1]:
                    partial.append(word)
                    await asyncio.sleep(self._interim_delay)
                    yield TranscriptSegment(
                        text=" ".join(partial),
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        is_final=False,
                        language=segment.language,
                    )
                await asyncio.sleep(self._interim_delay)
                yield segment
        finally:
            consumed.cancel()

    @staticmethod
    def _segment(scripted: Mapping[str, Any], *, final: bool) -> TranscriptSegment:
        return TranscriptSegment(
            text=str(scripted["text"]),
            start_ms=int(scripted["start_ms"]),
            end_ms=int(scripted["end_ms"]),
            is_final=final,
            language=scripted.get("language"),
            confidence=0.95 if final else None,
        )

    @staticmethod
    async def _drain(chunks: AsyncIterator[bytes]) -> None:
        async for _ in chunks:
            pass
