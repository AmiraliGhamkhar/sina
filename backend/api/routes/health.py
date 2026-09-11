"""Liveness + readiness probes (no auth; excluded from rate limiting).

Readiness is dependency-aware but driver-free: TCP probes derived from the
configured URLs, so Phase 1 needs no psycopg/sqlalchemy imports. Components
that are *not configured* report ``{"status": "disabled"}`` and never block
readiness — the API must come up for the WPF shell before Postgres does in
dev.
"""
from __future__ import annotations

import asyncio
import socket

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.db.url import parse_url_for_probe
from api.schemas.common import HealthResponse, ReadinessResponse
from api.version import API_VERSION, PHASE

router = APIRouter(tags=["health"])

PROBE_TIMEOUT_S = 1.0


def _tcp_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    del request  # state hook for future checks
    return HealthResponse(version=API_VERSION, phase=PHASE)


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    components: dict[str, dict] = {}

    # postgres: real engine ping once Phase 7 owns the engine (falls back to
    # the driver-free TCP probe when the URL is set but the engine could not
    # be built, e.g. missing db extra)
    db = getattr(request.app.state, "db", None)
    if settings.database.url and db is not None:
        ok = await db.ping()
        components["postgres"] = {"status": "ok" if ok else "unreachable", "probe": "engine"}
    elif not settings.database.url:
        components["postgres"] = {"status": "disabled"}
    else:
        target = parse_url_for_probe(settings.database.url)
        if target is None:
            components["postgres"] = {
                "status": "configured", "note": "engine unavailable, not probed"
            }
        else:
            host, port = target
            ok = await asyncio.to_thread(_tcp_probe, host, port)
            components["postgres"] = {
                "status": "ok" if ok else "unreachable", "host": host, "port": port
            }

    for name, url in (("redis", settings.redis.url),):
        if not url:
            components[name] = {"status": "disabled"}
            continue
        target = parse_url_for_probe(url)
        if target is None:
            components[name] = {"status": "configured", "note": "local/unix target, not probed"}
            continue
        host, port = target
        ok = await asyncio.to_thread(_tcp_probe, host, port)
        components[name] = {"status": "ok" if ok else "unreachable", "host": host, "port": port}

    providers = getattr(request.app.state, "providers", {})
    components["provider_router"] = {"status": "ok", "registered": len(providers) or 0}

    ready = all(c["status"] in ("ok", "disabled", "configured") for c in components.values())
    payload = ReadinessResponse(ready=ready, components=components)
    return JSONResponse(status_code=200 if ready else 503, content=payload.model_dump())
