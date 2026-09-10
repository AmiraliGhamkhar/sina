"""Bridge between FastAPI request handling and the AI router.

The only module allowed to translate a client request into a routing
decision: it assembles candidates from the provider registry (+ live health
from the tracker), applies the server's privacy default policy, and calls the
pure :func:`ai.router.route`. The decision object (including *why* a provider
was selected) is what the audit log records — spec §11 "record the selected
provider for auditing".
"""
from __future__ import annotations

from fastapi import WebSocket

from ai.base import ProviderKind
from ai.router import (
    ProviderCandidate,
    RouteDecision,
    RouteRequest,
    RoutingMode,
    TaskKind,
    route,
)
from api.schemas.ws import SessionStart


def build_candidates(app, kind: ProviderKind) -> list[ProviderCandidate]:
    """Flatten registry descriptors into pure routing candidates."""
    settings = app.state.settings
    registry = app.state.ai_registry
    health = app.state.provider_health
    candidates: list[ProviderCandidate] = []
    for d in registry.descriptors(kind):
        configured = registry.is_configured(kind, d.name, settings.provider_config(kind.value, d.name))
        if not configured:
            continue
        candidates.append(
            ProviderCandidate(
                name=d.name,
                kind=kind,
                privacy=d.capabilities.privacy,
                healthy=health.is_healthy(f"{kind.value}:{d.name}"),
                supports_streaming=d.capabilities.supports_streaming,
                languages=d.capabilities.languages,
                latency_hint_ms=d.capabilities.latency_hint_ms,
                cost_hint_per_unit=d.capabilities.cost_hint_per_unit,
            )
        )
    return candidates


def stt_route_request(app, start: SessionStart) -> RouteRequest:
    settings = app.state.settings
    privacy = start.privacy_required if start.privacy_required is not None else settings.routing.privacy_default
    mode = RoutingMode(start.mode) if start.mode else RoutingMode(settings.routing.mode)
    task = TaskKind.TRANSCRIBE_STREAM
    return RouteRequest(
        task=task,
        kind=ProviderKind.STT,
        mode=mode,
        privacy_required=bool(privacy),
        preferred=start.provider,
        strict_preference=False,  # a busy cloud pick may fall back; health does the rest
        language=start.language,
        session_id=None,
    )


async def select_stt_provider(websocket: WebSocket, start: SessionStart) -> RouteDecision:
    app = websocket.app
    candidates = build_candidates(app, ProviderKind.STT)
    decision = route(stt_route_request(app, start), candidates)
    # materialize the provider instance now so config errors surface *before*
    # session.started (the router promises a usable provider, not a hopeful name)
    settings = app.state.settings
    app.state.ai_registry.create(
        ProviderKind.STT, decision.provider, settings.provider_config("stt", decision.provider)
    )
    return decision
