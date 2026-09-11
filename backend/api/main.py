"""FastAPI application factory + dev entrypoint.

Run (dev):
    python -m uvicorn api.main:app --app-dir backend --reload
Run (container):
    uvicorn api.main:app --host 0.0.0.0 --port 8000

App state is composed once at startup — settings, provider registry, health
tracker, session registry, audit sink, metrics — so routes depend on the
container, not on module-level singletons (mirrors Phlox's
``initialize_and_get_app`` idea without its global config manager).
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from ai.registry import build_default_registry
from ai.router.health import HealthTracker
from api.config import Settings, get_settings
from api.errors import install_error_handlers
from api.routes import API_ROUTERS, ROOT_ROUTERS, WS_ROUTERS
from api.services.audit import AuditLog
from api.services.cost import CostLedger
from api.services.health_mirror import HealthMirror
from api.services.report_store import ReportStore
from api.services.session_registry import SessionRegistry
from api.services.templates import TemplateService
from api.services.terminology import TerminologyNormalizer
from api.services.transcript_store import TranscriptStore
from api.services.voice_commands import VoiceCommandService
from api.telemetry import Metrics, setup_logging
from api.version import API_VERSION, APP_NAME, PHASE, WS_PROTOCOL_VERSION

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.server.log_level, settings.server.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "MedicalScribe API v%s (phase %d) starting env=%s ws_protocol=v%d",
            API_VERSION,
            PHASE,
            settings.server.env,
            WS_PROTOCOL_VERSION,
        )
        if not settings.is_production:
            logger.warning(
                "dev-mode server: docs enabled, auth may fall back to MS_AUTH__DEV_TOKEN; "
                "never expose this port beyond localhost"
            )
        try:
            await app.state.health_mirror.start()  # no-op unless MS_REDIS__URL + interval set
        except Exception:  # pragma: no cover - never block boot on coordination state
            logger.warning("health mirror start failed; single-worker semantics apply", exc_info=True)
        yield
        try:
            await app.state.health_mirror.stop()
        except Exception:  # pragma: no cover - shutdown path
            logger.debug("health mirror stop failed", exc_info=True)
        try:
            await app.state.ai_registry.aclose_all()
        except Exception:  # pragma: no cover - best effort shutdown
            logger.debug("provider shutdown cleanup failed", exc_info=True)

    app = FastAPI(
        title=APP_NAME,
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # -- state -----------------------------------------------------------
    app.state.settings = settings
    app.state.metrics = Metrics()
    app.state.sessions = SessionRegistry()
    app.state.provider_health = HealthTracker(
        failure_threshold=settings.routing.failure_threshold,
        cooldown_s=settings.routing.health_cooldown_s,
    )
    app.state.cost_ledger = CostLedger(budget_tokens_per_day=settings.routing.budget_tokens_per_day)
    app.state.health_mirror = HealthMirror(
        tracker=app.state.provider_health,
        metrics=app.state.metrics,
        url=settings.redis.url or "",
        interval_s=settings.routing.health_mirror_interval_s,
    )
    app.state.ai_registry = build_default_registry()
    app.state.transcript_store = TranscriptStore()
    app.state.terminology = TerminologyNormalizer()
    app.state.template_service = TemplateService()
    app.state.report_store = ReportStore()
    app.state.voice_commands = VoiceCommandService()
    app.state.audit = AuditLog(
        Path(settings.audit.log_file) if settings.audit.enabled else None,
        enabled=settings.audit.enabled,
    )

    # -- middleware --------------------------------------------------------
    if settings.cors.allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors.allow_origins,
            allow_credentials=settings.cors.allow_credentials,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def _timing_and_count(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        template = getattr(route, "path", "unmatched")
        metrics: Metrics = request.app.state.metrics
        metrics.incr(f"http.status.{response.status_code}")
        metrics.observe_ms(f"http.{template}", elapsed_ms)
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        # access log: method + route template + status — never query strings
        logger.info(
            "%s %s → %d",
            request.method,
            template,
            response.status_code,
            extra={"route": template, "status": response.status_code, "duration_ms": round(elapsed_ms, 1)},
        )
        return response

    # -- errors, routes ------------------------------------------------------
    install_error_handlers(app)
    for r in ROOT_ROUTERS:
        app.include_router(r)
    for r in API_ROUTERS:
        app.include_router(r, prefix="/api/v1")
    for r in WS_ROUTERS:
        app.include_router(r)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": APP_NAME,
            "api_version": API_VERSION,
            "phase": PHASE,
            "health": "/health",
            "openapi": "/docs" if not settings.is_production else None,
            "ws": settings.websocket.path,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=_settings.server.host,
        port=_settings.server.port,
        reload=not _settings.is_production,
    )
