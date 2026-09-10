"""Provider contracts shared by the AI router, the backend, and tests.

Concepts (not code) adapted from:
- open-medical-scribe (MIT) provider factories: uniform provider objects with
  a ``name`` and a task-shaped entry point, mock provider parity for tests.
- Multi-Model-Gateway (MIT) provider registry: cheap health probes and static
  capability metadata let the router decide *before* executing a task.

Design rules for new providers:
- No provider may log or persist raw transcripts by default.
- ``health()`` must be cheap (< ~2 s) and must not process audio.
- Streaming STT yields interim segments first, then a final segment per
  utterance; consumers rely on ``TranscriptSegment.is_final``.
"""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderKind(str, Enum):
    STT = "stt"
    LLM = "llm"


class PrivacyClass(str, Enum):
    """Where data processed by a provider physically goes.

    LOCAL: data never leaves the deployment boundary (e.g. whisper-server on
    the LAN, llama-server on the same host).
    CLOUD: data leaves the boundary (Deepgram, Speechmatics, OpenAI, ...).
    Privacy-required requests must route to LOCAL providers only — this is a
    hard constraint of the routing layer, not a preference.
    """

    LOCAL = "local"
    CLOUD = "cloud"


class ProviderError(RuntimeError):
    """Raised by providers for task failures.

    ``retryable`` hints to the router that another provider (or the same one,
    later) may succeed; ``status_hint`` is an optional HTTP-ish code used for
    API translation, never trusted for control flow.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        retryable: bool = False,
        status_hint: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status_hint = status_hint


class ProviderUnavailableError(ProviderError):
    """Provider is configured but not reachable/healthy right now."""

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Static metadata used for routing and validation (no instantiation)."""

    privacy: PrivacyClass
    supports_streaming: bool = False
    supports_batch: bool = True
    #: language tags handled natively; ``("*",)`` means open-ended multilingual
    languages: tuple[str, ...] = ("*",)
    #: free-form cost hint, USD per minute (audio) or per 1K tokens (text).
    #: 0.0 for self-hosted providers. Used only as a tie-breaker in AUTO mode.
    cost_hint_per_unit: float = 0.0
    #: expected latency class in ms for a representative request; used for
    #: AUTO scoring and as a prior before live measurements exist.
    latency_hint_ms: int = 500
    #: provider-specific extras surfaced to the client manifest (display only)
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return self.privacy is PrivacyClass.LOCAL


@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None
    checked_at: str | None = None


@dataclass(frozen=True)
class TranscriptSegment:
    """One utterance (final) or hypothesis-so-far (interim) of a transcript."""

    text: str
    start_ms: int
    end_ms: int
    is_final: bool = True
    language: str | None = None
    confidence: float | None = None
    #: provider-assigned segment id; backend assigns one when absent
    segment_id: str | None = None


@dataclass(frozen=True)
class STTRequest:
    """Batch transcription request.

    ``audio`` is raw bytes in the format described by ``encoding``.
    ``context_hints`` carries medical vocabulary / prior-note context used by
    providers that support hotwords (Deepgram Boosting, Speechmatics context).
    """

    audio: bytes
    encoding: str = "pcm_s16le"
    sample_rate: int = 16000
    channels: int = 1
    #: "fa", "en" or None for auto-detect (mixed Persian/English is expected;
    #: auto-detect must preserve embedded English medical terms).
    language: str | None = None
    context_hints: Sequence[str] = ()
    vad: bool = True


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMCompletion:
    text: str
    model: str | None = None
    usage: LLMUsage | None = None
    #: provider-visible metadata safe to audit (never includes secrets)
    meta: Mapping[str, Any] = field(default_factory=dict)


class BaseProvider(abc.ABC):
    """Common identity + health contract for every provider."""

    #: unique registry name, e.g. "whisper-local", "deepgram", "llama-server"
    name: str = "abstract"
    kind: ProviderKind

    @property
    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        ...

    async def health(self) -> ProviderHealth:
        """Cheap liveness probe. Default: assume healthy if constructed.

        Providers with remote dependencies override this (e.g. llama-server
        polls ``GET /health``).
        """
        return ProviderHealth(ok=True)


class STTProvider(BaseProvider):
    """Speech-to-text provider contract (server-side; WPF never sees this)."""

    kind = ProviderKind.STT

    @abc.abstractmethod
    async def transcribe(self, request: STTRequest) -> list[TranscriptSegment]:
        """Batch transcription of a complete audio buffer."""

    async def stream(
        self, chunks: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptSegment]:
        """Live transcription over PCM16 mono chunks.

        Providers without native streaming MAY implement this by buffering and
        re-dispatching :meth:`transcribe` on VAD-like boundaries (this is how
        the local whisper-server adapter is planned). Providers without any
        streaming support raise :class:`ProviderError` — never silently drop.
        """
        raise ProviderError(
            f"provider '{self.name}' does not support streaming", provider=self.name
        )

    async def aclose(self) -> None:
        """Release sessions/connections. Idempotent."""


class LLMProvider(BaseProvider):
    """Text-generation provider contract used for note drafting/normalization."""

    kind = ProviderKind.LLM

    @abc.abstractmethod
    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        """Single completion. ``json_mode`` requests valid JSON output where
        the provider supports it (used for structured report sections)."""

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Token deltas. Default implementation degrades gracefully by
        yielding the whole completion at once."""
        result = await self.complete(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        yield result.text

    async def aclose(self) -> None:
        """Release connections. Idempotent."""
