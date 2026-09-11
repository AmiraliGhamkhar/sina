"""Version, client manifest, and (Phase 1) stats introspection.

GET /api/v1/config/manifest is the WPF shell's only source of truth for
server-side policy: WS path/protocol, audio format, languages, voice-command
catalog, routing modes and feature flags. The client must render all of it
dynamically — no mirrored constants in C#.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.auth.deps import OptionalPrincipal
from api.schemas.common import VersionResponse
from api.schemas.manifest import ManifestResponse
from api.version import API_VERSION, APP_NAME, PHASE, WS_PROTOCOL_MIN, WS_PROTOCOL_VERSION

router = APIRouter(tags=["meta"])


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(
        name=APP_NAME,
        api_version=API_VERSION,
        ws_protocol=WS_PROTOCOL_VERSION,
        ws_protocol_min=WS_PROTOCOL_MIN,
        phase=PHASE,
    )


@router.get("/config/manifest", response_model=ManifestResponse)
async def client_manifest(request: Request, principal: OptionalPrincipal) -> ManifestResponse:
    settings = request.app.state.settings
    ws = settings.websocket
    # Phase 2 flips live_transcription on; Phase 1 clients must degrade.
    features_ok_phase = PHASE >= 2
    return ManifestResponse(
        server_version=API_VERSION,
        phase=PHASE,
        ws_protocol=WS_PROTOCOL_VERSION,
        ws_path=ws.path,
        max_message_bytes=ws.max_message_bytes,
        features={  # type: ignore[typeddict-item]
            "live_transcription": features_ok_phase,
            "voice_commands": PHASE >= 6,
            # draft generation live from P4; finalize/approve flow lands in P6/P7
            "report_generation": PHASE >= 4,
            "editing_enabled": True,
            "cloud_providers_enabled": bool(
                settings.stt.deepgram.api_key or settings.stt.speechmatics.api_key
            ),
            "audit_enabled": settings.audit.enabled,
        },
        routing_modes=[settings.routing.mode]
        if settings.routing.mode != "auto"
        else ["local", "cloud", "hybrid", "auto"],
    )


@router.get("/observability/stats")
async def stats(request: Request, principal: OptionalPrincipal) -> dict:
    """Process-local metrics snapshot (Prometheus endpoint: Phase 8)."""
    payload = request.app.state.metrics.snapshot()
    payload["ws_sessions"] = request.app.state.sessions.snapshot()
    # Phase 5 acceptance: health demotion + budget state observable here
    payload["provider_health"] = dict(request.app.state.provider_health.snapshot())
    payload["cost"] = request.app.state.cost_ledger.snapshot()
    return payload
