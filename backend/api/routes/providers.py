"""Provider listing for the WPF "AI Settings" screen.

Exposes registry metadata + optional live health — never secrets, never raw
config values. This endpoint is the client's entire window into the AI layer.

``GET /providers/{name}/models`` adds *live catalog discovery* for providers
that declare it (9Router): its ``provider/model`` ids depend on which upstream
accounts the operator connected, so the client cannot hardcode a list.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, Request

from ai.base import ProviderError, ProviderKind
from ai.registry import ProviderNotFound
from api.auth.deps import OptionalPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.providers import (
    ProviderCapabilitiesInfo,
    ProviderHealthInfo,
    ProviderInfo,
    ProviderModelCatalog,
    ProviderModelInfo,
)
from api.services.ai_bridge import provider_config_with_secrets

router = APIRouter(prefix="/providers", tags=["providers"])

#: catalog kinds the discovery endpoint accepts. ``stt`` selects the STT
#: adapter; everything else is reachable through the LLM adapter's client,
#: which shares the same base URL and credentials.
_CATALOG_KINDS = "^(stt|llm|tts|embedding|image|web)$"


async def _describe(request: Request, kind: ProviderKind) -> list[ProviderInfo]:
    state = request.app.state
    registry = state.ai_registry
    out: list[ProviderInfo] = []
    for d in registry.descriptors(kind):
        cfg = await provider_config_with_secrets(request.app, kind.value, d.name)
        caps = d.capabilities
        out.append(
            ProviderInfo(
                name=d.name,
                kind=kind.value,
                description=d.description,
                configured=registry.is_configured(kind, d.name, cfg),
                supports_model_discovery=d.supports_model_discovery,
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
        infos.extend(await _describe(request, k))

    if probe == "health":
        state = request.app.state

        async def _probe(info: ProviderInfo) -> None:
            try:
                provider = state.ai_registry.create(
                    ProviderKind(info.kind),
                    info.name,
                    await provider_config_with_secrets(request.app, info.kind, info.name),
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


@router.get("/{name}/models", response_model=ProviderModelCatalog)
async def list_provider_models(
    request: Request,
    principal: OptionalPrincipal,
    name: str,
    kind: str = Query(default="llm", pattern=_CATALOG_KINDS),
) -> ProviderModelCatalog:
    """Live model catalog for one provider (9Router today).

    Fails loudly rather than returning an empty list: an operator who wired up
    9Router but connected no upstream accounts needs to see *that*, not a
    mysteriously blank dropdown.
    """
    state = request.app.state
    registry = state.ai_registry
    provider_kind = ProviderKind.STT if kind == "stt" else ProviderKind.LLM
    try:
        descriptor = registry.get_descriptor(provider_kind, name)
    except ProviderNotFound as exc:
        raise ApiError(
            404, ErrorCode.NOT_FOUND, f"unknown {provider_kind.value} provider '{name}'"
        ) from exc
    if not descriptor.supports_model_discovery:
        raise ApiError(
            501,
            ErrorCode.CAPABILITY_NOT_IMPLEMENTED,
            f"provider '{name}' has no live model catalog",
        )

    cfg = await provider_config_with_secrets(request.app, provider_kind.value, name)
    if not registry.is_configured(provider_kind, name, cfg):
        prefix = "MS_STT" if provider_kind is ProviderKind.STT else "MS_LLM"
        raise ApiError(
            409,
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"provider '{name}' is not configured server-side "
            f"(set {prefix}__NINE_ROUTER__BASE_URL)",
        )

    provider = registry.create(provider_kind, name, cfg)
    list_models = getattr(provider, "list_models", None)
    if list_models is None:  # descriptor flag and adapter disagree — our bug
        raise ApiError(
            501,
            ErrorCode.CAPABILITY_NOT_IMPLEMENTED,
            f"provider '{name}' cannot list models",
        )
    try:
        models = await asyncio.wait_for(list_models(kind), timeout=15.0)
    except ProviderError as exc:
        # unreachable / bad key / upstream 5xx — surfaced verbatim, no secrets
        raise ApiError(502, ErrorCode.PROVIDER_UNAVAILABLE, str(exc)) from exc
    except TimeoutError as exc:
        raise ApiError(
            504, ErrorCode.PROVIDER_UNAVAILABLE, f"'{name}' model catalog timed out"
        ) from exc

    return ProviderModelCatalog(
        provider=name,
        kind=kind,
        # a model id is not a secret; nothing else from cfg is ever echoed
        configured_model=str(cfg.get("model") or "").strip() or None,
        models=[ProviderModelInfo.model_validate(m) for m in models],
    )
