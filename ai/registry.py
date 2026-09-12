"""Centralized provider registration.

The only sanctioned place where provider *classes* meet *configuration*.
Adapted concept from Multi-Model-Gateway's provider_registry (turning stored
config rows into adapter instances) and open-medical-scribe's factory maps —
in Python, with capabilities declared statically on the descriptor so the
router can decide without constructing (or contacting) anything.

Registration is explicit and import-safe: importing this module must never
read secrets, open sockets or start processes. Providers that need network
endpoints get them injected through ``config`` at ``create()`` time.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ai.base import PrivacyClass, ProviderCapabilities, ProviderKind

logger = logging.getLogger(__name__)

Factory = Callable[[Mapping[str, Any]], Any]


class RegistryError(Exception):
    """Base class for registry errors."""


class ProviderNotFound(RegistryError):
    def __init__(self, name: str, kind: ProviderKind | None = None) -> None:
        scope = f" of kind '{kind.value}'" if kind else ""
        super().__init__(f"no provider registered under '{name}'{scope}")
        self.name = name
        self.kind = kind


class ProviderAlreadyRegistered(RegistryError):
    pass


def _key(kind: ProviderKind, name: str) -> str:
    return f"{kind.value}:{name}"


@dataclass(frozen=True)
class ProviderDescriptor:
    """Metadata needed to select a provider + a factory to build it.

    ``configured`` is a cheap predicate over settings: an unconfigured cloud
    provider stays visible in listings (so admins can act) but is never
    routable. ``factory`` receives the provider's config mapping and must not
    perform blocking I/O.
    """

    name: str
    kind: ProviderKind
    capabilities: ProviderCapabilities
    factory: Factory
    #: short human description surfaced to the client manifest
    description: str = ""
    #: predicate name for audit output ("config.llama_server_url" etc.)
    config_keys: tuple[str, ...] = ()
    #: the adapter implements ``list_models(kind)`` and can answer
    #: ``GET /api/v1/providers/{name}/models`` with a live catalog. Declared
    #: statically so listings never have to construct a provider to find out.
    supports_model_discovery: bool = False
    configured: Callable[[Mapping[str, Any]], bool] = field(
        default=lambda _cfg: True
    )

    @property
    def key(self) -> str:
        return _key(self.kind, self.name)


class ProviderRegistry:
    def __init__(self) -> None:
        self._descriptors: dict[str, ProviderDescriptor] = {}
        self._instances: dict[str, Any] = {}

    # -- registration -------------------------------------------------------
    def register(self, descriptor: ProviderDescriptor) -> None:
        if descriptor.key in self._descriptors:
            raise ProviderAlreadyRegistered(
                f"provider '{descriptor.key}' already registered"
            )
        self._descriptors[descriptor.key] = descriptor
        logger.debug("registered provider %s", descriptor.key)

    def unregister(self, kind: ProviderKind, name: str) -> None:
        self._descriptors.pop(_key(kind, name), None)
        self._instances.pop(_key(kind, name), None)

    # -- discovery ----------------------------------------------------------
    def descriptors(self, kind: ProviderKind | None = None) -> list[ProviderDescriptor]:
        items = list(self._descriptors.values())
        if kind is not None:
            items = [d for d in items if d.kind is kind]
        return sorted(items, key=lambda d: d.key)

    def get_descriptor(self, kind: ProviderKind, name: str) -> ProviderDescriptor:
        try:
            return self._descriptors[_key(kind, name)]
        except KeyError as exc:
            raise ProviderNotFound(name, kind) from exc

    # -- instantiation ------------------------------------------------------
    def create(
        self,
        kind: ProviderKind,
        name: str,
        config: Mapping[str, Any] | None = None,
        *,
        cache: bool = True,
    ) -> Any:
        """Build (and optionally cache) a provider instance.

        ``config`` is the *provider-specific* slice of app settings
        (secrets included); factories must never pull secrets from env
        directly so that tests can drive everything from a dict.
        """
        cfg = dict(config or {})
        descriptor = self.get_descriptor(kind, name)
        key = descriptor.key
        if cache and key in self._instances:
            return self._instances[key]
        instance = descriptor.factory(cfg)
        if cache:
            self._instances[key] = instance
        return instance

    def is_configured(
        self, kind: ProviderKind, name: str, config: Mapping[str, Any] | None = None
    ) -> bool:
        descriptor = self.get_descriptor(kind, name)
        try:
            return bool(descriptor.configured(dict(config or {})))
        except Exception:  # a broken predicate must not crash listings
            logger.warning("configured() failed for %s", descriptor.key, exc_info=True)
            return False

    def __len__(self) -> int:
        return len(self._descriptors)

    async def aclose_all(self) -> None:
        """Best-effort release of all cached provider instances (shutdown)."""
        for instance in list(self._instances.values()):
            aclose = getattr(instance, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # pragma: no cover
                    logger.debug("provider aclose failed", exc_info=True)
        self._instances.clear()


def build_default_registry() -> ProviderRegistry:
    """Registry with the providers bundled in this repo.

    STT: mock, whisper-local, qwen-asr, shenava, deepgram, speechmatics,
    9router. LLM: mock, llama-server, openai, anthropic, gemini, 9router.

    ``9router`` (https://github.com/decolua/9router) is registered under *both*
    kinds: the self-hosted proxy exposes an OpenAI-compatible
    ``/api/v1/chat/completions`` and a Whisper-compatible
    ``/api/v1/audio/transcriptions`` over the same base URL, fronting 40+
    upstreams with account rotation. Both adapters declare
    ``supports_model_discovery`` because 9Router's catalog is dynamic — it
    reflects whichever upstream accounts the operator has connected.

    Each addition stays a single ``register()`` call plus a module under
    ``ai/llm`` or ``ai/stt``; no other file should need to change.
    """
    from ai.llm.anthropic import AnthropicProvider, anthropic_configured
    from ai.llm.gemini import GeminiProvider, gemini_configured
    from ai.llm.llama_server import LlamaServerProvider, llama_server_configured
    from ai.llm.mock import MockLlmProvider
    from ai.llm.nine_router import NineRouterProvider, nine_router_configured
    from ai.llm.openai import OpenAIProvider, openai_configured
    from ai.stt.deepgram import DeepgramProvider, deepgram_configured
    from ai.stt.mock import MockSttProvider
    from ai.stt.nine_router import NineRouterSttProvider, nine_router_stt_configured
    from ai.stt.qwen_asr import QwenAsrProvider, qwen_asr_configured
    from ai.stt.shenava import ShenavaProvider, shenava_configured
    from ai.stt.speechmatics import SpeechmaticsProvider, speechmatics_configured
    from ai.stt.whisper_server import WhisperServerProvider, whisper_local_configured

    registry = ProviderRegistry()
    registry.register(
        ProviderDescriptor(
            name="mock",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                languages=("fa", "en", "fa-en"),
                latency_hint_ms=1,
            ),
            factory=lambda cfg: MockSttProvider(cfg),
            description="Deterministic mock STT used by tests and Phase 1/2 demos.",
        )
    )
    registry.register(
        ProviderDescriptor(
            name="whisper-local",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                supports_batch=True,
                languages=("*",),
                latency_hint_ms=2500,
            ),
            factory=lambda cfg: WhisperServerProvider(cfg),
            configured=whisper_local_configured,
            description=(
                "Self-hosted whisper.cpp server (external process); VAD-windowed "
                "pseudo-streaming. Configure MS_STT__WHISPER_SERVER__URL."
            ),
            config_keys=("stt.whisper_server.url",),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="qwen-asr",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                supports_batch=True,
                languages=("fa", "en", "*"),
                latency_hint_ms=3000,
            ),
            factory=lambda cfg: QwenAsrProvider(cfg),
            configured=qwen_asr_configured,
            description=(
                "Qwen2-Audio ASR behind an OpenAI-audio-compatible HTTP service; "
                "strong Persian. Configure MS_STT__QWEN_ASR__URL."
            ),
            config_keys=("stt.qwen_asr.url",),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="shenava",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                supports_batch=True,
                languages=("fa",),
                latency_hint_ms=600,
            ),
            factory=lambda cfg: ShenavaProvider(cfg),
            configured=shenava_configured,
            description=(
                "In-process Persian STT: Shenava Koochik FastConformer CTC via "
                "sherpa-onnx (pip install \".[local-ai]\"). Auto-configured once "
                "the 'shenava-koochik' model is downloaded from the Models screen."
            ),
            config_keys=("stt.shenava.model_path", "stt.shenava.tokens_path"),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="deepgram",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.CLOUD,
                supports_streaming=True,
                supports_batch=True,
                languages=("*",),
                cost_hint_per_unit=0.0043,
                latency_hint_ms=800,
            ),
            factory=lambda cfg: DeepgramProvider(cfg),
            configured=deepgram_configured,
            description=(
                "Deepgram Nova-2 via WebSocket streaming + prerecorded HTTP; "
                "keyword boosting for medical terms; key via MS_STT__DEEPGRAM__* env."
            ),
            config_keys=("stt.deepgram.api_key",),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="speechmatics",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.CLOUD,
                supports_streaming=True,
                supports_batch=True,
                languages=("fa", "en", "*"),
                cost_hint_per_unit=0.006,
                latency_hint_ms=900,
            ),
            factory=lambda cfg: SpeechmaticsProvider(cfg),
            configured=speechmatics_configured,
            description=(
                "Speechmatics realtime + batch with Persian support. "
                "key via MS_STT__SPEECHMATICS__* env."
            ),
            config_keys=("stt.speechmatics.api_key",),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="9router",
            kind=ProviderKind.STT,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                supports_batch=True,
                languages=("*",),
                latency_hint_ms=1500,
                extra={"router": "9router", "streaming_mode": "vad-windowed"},
            ),
            factory=lambda cfg: NineRouterSttProvider(cfg),
            configured=nine_router_stt_configured,
            supports_model_discovery=True,
            description=(
                "Self-hosted 9Router proxy (github.com/decolua/9router): Whisper-compatible "
                "POST /api/v1/audio/transcriptions relayed to 40+ upstreams with automatic "
                "account fallback. VAD-windowed pseudo-streaming. Configure "
                "MS_STT__NINE_ROUTER__BASE_URL + __MODEL (form 'provider/model')."
            ),
            config_keys=("stt.nine_router.base_url", "stt.nine_router.model"),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="openai",
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.CLOUD,
                supports_streaming=True,
                languages=("*",),
                cost_hint_per_unit=0.6,
                latency_hint_ms=2500,
            ),
            factory=lambda cfg: OpenAIProvider(cfg),
            configured=openai_configured,
            description="OpenAI chat completions (key via MS_LLM__CLOUD__* env).",
            config_keys=("llm.cloud.openai_api_key",),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="anthropic",
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.CLOUD,
                supports_streaming=True,
                languages=("*",),
                cost_hint_per_unit=3.0,
                latency_hint_ms=3000,
            ),
            factory=lambda cfg: AnthropicProvider(cfg),
            configured=anthropic_configured,
            description="Anthropic messages API (key via MS_LLM__CLOUD__* env).",
            config_keys=("llm.cloud.anthropic_api_key",),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="gemini",
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.CLOUD,
                supports_streaming=True,
                languages=("*",),
                cost_hint_per_unit=0.7,
                latency_hint_ms=2200,
            ),
            factory=lambda cfg: GeminiProvider(cfg),
            configured=gemini_configured,
            description="Google Gemini generateContent (key via MS_LLM__CLOUD__* env).",
            config_keys=("llm.cloud.gemini_api_key",),
        )
    )
    registry.register(
        ProviderDescriptor(
            name="mock",
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                latency_hint_ms=1,
            ),
            factory=lambda cfg: MockLlmProvider(cfg),
            description="Deterministic mock LLM that echoes a structured draft.",
        )
    )
    registry.register(
        ProviderDescriptor(
            name="llama-server",
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                languages=("fa", "en", "*"),
                latency_hint_ms=1500,
                extra={"transport": "openai-compatible-http"},
            ),
            factory=lambda cfg: LlamaServerProvider(cfg),
            description=(
                "Self-hosted llama.cpp server (external process). "
                "Configured via MS_LLM__LLAMA_SERVER__BASE_URL."
            ),
            config_keys=("llama_server.base_url",),
            configured=llama_server_configured,
        )
    )
    registry.register(
        ProviderDescriptor(
            name="9router",
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(
                privacy=PrivacyClass.LOCAL,
                supports_streaming=True,
                languages=("*",),
                latency_hint_ms=2000,
                extra={"router": "9router", "transport": "openai-compatible-http"},
            ),
            factory=lambda cfg: NineRouterProvider(cfg),
            configured=nine_router_configured,
            supports_model_discovery=True,
            description=(
                "Self-hosted 9Router proxy (github.com/decolua/9router): OpenAI-compatible "
                "POST /api/v1/chat/completions fronting 40+ upstreams (Claude/GPT/Gemini/…) "
                "with account rotation and auto-fallback. Configure "
                "MS_LLM__NINE_ROUTER__BASE_URL + __MODEL (form 'provider/model')."
            ),
            config_keys=("llm.nine_router.base_url", "llm.nine_router.model"),
        )
    )
    return registry
