"""Provider listing for the WPF "AI Settings" screen.

Exposes registry metadata + optional live health — never secrets, never raw
config values. This endpoint is the client's entire window into the AI layer.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request

from ai.base import ProviderKind
from api.auth.deps import OptionalPrincipal
from api.schemas.providers import ProviderCapabilitiesInfo, ProviderHealthInfo, ProviderInfo

router = APIRouter(prefix="/providers", tags=["providers"])


def _describe(request: Request, kind: ProviderKind) -> list[ProviderInfo]:
    state = request.app.state
    settings = state.settings
    registry = state.ai_registry
    out: list[ProviderInfo] = []
    for d in registry.descriptors(kind):
        cfg = settings.provider_config(kind.value, d.name)
        caps = d.capabilities
        out.append(
            ProviderInfo(
                name=d.name,
                kind=kind.value,
                description=d.description,
                configured=registry.is_configured(kind, d.name, cfg),
                capabilities=ProviderCapabilitiesInfo(
                    privacy_class=caps.privacy.value,
                    supports_streaming=caps.supports_streaming,
                    languages=list(caps.languages),
                    latency_hint_ms=caps.latency_hint_ms,
                    cost_hint_per_unit=caps.cost_hint_per_unit,
                ),
            )
        )
    return out


@router.get("", response_model=list[ProviderInfo])
async def list_providers(
    request: Request,
    principal: OptionalPrincipal,
    kind: str | None = Query(default=None, pattern="^(stt|llm)$"),
    probe: str | None = Query(default=None, pattern="^health$"),
) -> list[ProviderInfo]:
    kinds = [ProviderKind(kind)] if kind else list(ProviderKind)
    infos: list[ProviderInfo] = []
    for k in kinds:
        infos.extend(_describe(request, k))

    if probe == "health":
        state = request.app.state

        async def _probe(info: ProviderInfo) -> None:
            try:
                provider = state.ai_registry.create(
                    ProviderKind(info.kind),
                    info.name,
                    state.settings.provider_config(info.kind, info.name),
                )
                health = await asyncio.wait_for(provider.health(), timeout=5.0)
                info.health = ProviderHealthInfo(
                    ok=health.ok, latency_ms=health.latency_ms, detail=health.detail
                )
                state.provider_health.record_success(
                    f"{info.kind}:{info.name}", health.latency_ms
                )
            except Exception:  # a failing probe is data, never a 500
                state.provider_health.record_failure(f"{info.kind}:{info.name}")
                info.health = ProviderHealthInfo(ok=False, detail="probe failed")

        await asyncio.gather(*(_probe(i) for i in infos if i.configured))
    return infos
