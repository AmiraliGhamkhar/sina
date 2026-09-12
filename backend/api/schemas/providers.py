"""Provider listing schemas (safe subset of registry descriptors)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProviderCapabilitiesInfo(BaseModel):
    privacy_class: str = "local"  # "local" | "cloud"
    supports_streaming: bool = False
    languages: list[str] = Field(default_factory=list)
    latency_hint_ms: int = 0
    cost_hint_per_unit: float = 0.0


class ProviderHealthInfo(BaseModel):
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None


class ProviderInfo(BaseModel):
    name: str
    kind: str  # "stt" | "llm"
    description: str = ""
    #: registry-level: does required config exist server-side?
    configured: bool = False
    #: live probe result; null unless ?probe=health was requested
    health: ProviderHealthInfo | None = None
    capabilities: ProviderCapabilitiesInfo = Field(default_factory=ProviderCapabilitiesInfo)
    #: the provider can answer GET /providers/{name}/models with a live catalog
    supports_model_discovery: bool = False


class ProviderModelInfo(BaseModel):
    """One entry of a provider's live model catalog.

    9Router ids are ``provider/model`` (``claude/claude-sonnet-4``,
    ``groq/whisper-large-v3-turbo``) and its catalog depends on which upstream
    accounts the operator connected — so it is discovered, never hardcoded.
    """

    id: str
    kind: str = "llm"
    #: upstream provider within the router (``owned_by`` in OpenAI terms)
    owned_by: str | None = None
    context_length: int | None = None
    max_completion_tokens: int | None = None
    #: router-supplied capability flags (vision/tools/reasoning/…); display only
    capabilities: dict[str, Any] | None = None


class ProviderModelCatalog(BaseModel):
    provider: str
    kind: str
    #: the model the server is configured to use (MS_*__NINE_ROUTER__MODEL).
    #: Echoed so the client can mark the active choice — a model id is not a
    #: secret, and nothing else from the provider config is ever exposed.
    configured_model: str | None = None
    models: list[ProviderModelInfo] = Field(default_factory=list)
