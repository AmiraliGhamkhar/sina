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

    Later phases add: whisper-local / qwen-asr (STT), speechmatics / deepgram
    (cloud STT), openai / anthropic / gemini (cloud LLM). Each addition is a
    single ``register()`` call plus a module under ai/stt or ai/llm — no other
    file should need to change.
    """
    from ai.llm.llama_server import LlamaServerProvider, llama_server_configured
    from ai.llm.mock import MockLlmProvider
    from ai.stt.mock import MockSttProvider

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
    return registry
