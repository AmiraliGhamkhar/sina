"""Provider listing schemas (safe subset of registry descriptors)."""
from __future__ import annotations

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
