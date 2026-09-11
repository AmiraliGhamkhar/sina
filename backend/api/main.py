"""FastAPI application factory + dev entrypoint.

Run (dev):
    python -m uvicorn api.main:app --app-dir backend --reload
Run (container):
    uvicorn api.main:app --host 0.0.0.0 --port 8000

Phase 7: when MS_DATABASE__URL is set the app boots in durable mode —
SQLAlchemy engine + repositories on app.state, argon2 credential auth,
refresh rotation, Fernet-encrypted provider secrets, Redis-or-in-process
rate limiting and audit dual-write. Without the URL everything degrades to
the fully functional in-memory dev mode (no feature is silently broken;
DB-backed routes answer 501 DB_NOT_CONFIGURED).

App state is composed once at startup — settings, provider registry, health
tracker, session registry, audit sink, metrics — so routes depend on the
container, not on module-level singletons (mirrors Phlox's
``initialize_and_get_app`` idea without its global config manager).
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai.registry import build_default_registry
from ai.router.health import HealthTracker
from api.config import Settings, get_settings
from api.db.engine import open_database
from api.errors import ErrorCode, install_error_handlers
from api.routes import API_ROUTERS, ROOT_ROUTERS, WS_ROUTERS
from api.services.audit import AuditLog
from api.services.auth_service import AuthService
from api.services.cost import CostLedger
from api.services.health_mirror import HealthMirror
from api.services.model_manager import ModelManager
from api.services.pii_ner import RedactionService
from api.services.rate_limit import InProcessBackend, RateLimiter, RateLimitExceeded, RedisBackend
from api.services.report_store import ReportStore
from api.services.secrets import SecretVault
from api.services.session_registry import SessionRegistry
from api.services.templates import TemplateService
from api.services.terminology import TerminologyNormalizer
from api.services.transcript_store import TranscriptStore
from api.services.voice_commands import VoiceCommandService
from api.telemetry import Metrics, maybe_init_otel, setup_logging
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
        # -- Phase 7 durable boot (no-op in in-memory mode) ------------------
        db = getattr(app.state, "db", None)
        if db is not None:
            try:
                if settings.database.auto_create:
                    await db.create_all()  # dev convenience; prod = alembic
                from datetime import datetime

                from api.services.seed import seed_all

                await seed_all(app)
                await app.state.template_service.load_from_db()
                app.state.audit.attach_db(app.state.audit_repo)
                # Phase 8: durable cost-budget backfill — a mid-day restart
                # no longer resets the day's token spend
                if app.state.ai_requests is not None:
                    day_start = datetime.now(UTC).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    try:
                        totals = await app.state.ai_requests.token_totals_since(day_start)
                        if totals:
                            app.state.cost_ledger.backfill(totals)
                    except Exception:  # noqa: BLE001 — cost guard fails open
                        logger.warning("cost ledger backfill failed", exc_info=True)
                logger.info(
                    "durable mode: database ready url=%s", db.url.split("@")[-1]
                )
            except Exception:  # pragma: no cover — DB down must not kill the API
                logger.exception(
                    "durable-mode initialization failed — serving in-memory semantics "
                    "(restart with a healthy DB to re-enable persistence)"
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
            await app.state.audit.stop_writer()
        except Exception:  # pragma: no cover - shutdown path
            logger.debug("audit writer stop failed", exc_info=True)
        try:
            await app.state.ai_registry.aclose_all()
        except Exception:  # pragma: no cover - best effort shutdown
            logger.debug("provider shutdown cleanup failed", exc_info=True)
        try:
            await app.state.model_manager.aclose()
        except Exception:  # pragma: no cover - best effort shutdown
            logger.debug("model manager shutdown cleanup failed", exc_info=True)
        db = getattr(app.state, "db", None)
        if db is not None:
            try:
                await db.aclose()
            except Exception:  # pragma: no cover - shutdown path
                logger.debug("db close failed", exc_info=True)

    app = FastAPI(
        title=APP_NAME,
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    # Phase 8: optional OTel tracing (no-op unless enabled + extra installed)
    maybe_init_otel(settings, app)

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
    app.state.voice_commands = VoiceCommandService()
    # NER-based pre-cloud PHI redaction (regex fallback built-in)
    app.state.redaction = RedactionService(
        settings.llm.cloud, settings.models, metrics=app.state.metrics
    )

    # -- Phase 7 durable state (None-safe: in-memory mode keeps everything) --
    db = open_database(
        settings.database.url,
        echo=settings.database.echo,
        pool_size=settings.database.pool_size,
    )
    app.state.db = db
    if db is None:
        if settings.database.url:
            logger.error(
                "MS_DATABASE__URL is set but the engine could not be built — "
                "running IN-MEMORY (data will not persist)"
            )
        app.state.transcript_repo = None
        app.state.report_repo = None
        app.state.provider_repo = None
        app.state.ai_requests = None
        app.state.audit_repo = None
        app.state.secret_vault = SecretVault(None)
        app.state.auth_service = AuthService(settings=settings)
        app.state.rate_limiter = RateLimiter(InProcessBackend(), enabled=settings.rate_limit.enabled)
        app.state.template_service = TemplateService()
        app.state.report_store = ReportStore()
    else:
        from api.repositories.ai import AIProviderRepository, AIRequestRepository
        from api.repositories.audit import AuditRepository
        from api.repositories.reports import ReportRepository
        from api.repositories.templates import TemplateRepository
        from api.repositories.transcripts import TranscriptRepository
        from api.repositories.users import RefreshTokenRepository, UserRepository
        from api.services.auth_service import RefreshStore

        app.state.transcript_repo = TranscriptRepository(db.sessionmaker)
        app.state.report_repo = ReportRepository(db.sessionmaker)
        app.state.provider_repo = AIProviderRepository(db.sessionmaker)
        app.state.ai_requests = AIRequestRepository(db.sessionmaker)
        app.state.audit_repo = AuditRepository(db.sessionmaker)
        vault_key = settings.security.secret_encryption_key
        if vault_key is not None:
            app.state.secret_vault = SecretVault(vault_key.get_secret_value())
        else:
            logger.warning(
                "MS_SECURITY__SECRET_ENCRYPTION_KEY not set — provider secrets "
                "from env still work; admin secret storage is disabled"
            )
            app.state.secret_vault = SecretVault(None)
        app.state.auth_service = AuthService(
            settings=settings,
            users=UserRepository(db.sessionmaker),
            refresh_store=RefreshStore(RefreshTokenRepository(db.sessionmaker)),
        )
        # Redis backend when configured; any Redis failure degrades OPEN to
        # in-process counters (the login lockout guard remains as second layer)
        if settings.redis.url:
            app.state.rate_limiter = RateLimiter(
                RedisBackend(settings.redis.url), enabled=settings.rate_limit.enabled
            )
        else:
            app.state.rate_limiter = RateLimiter(
                InProcessBackend(), enabled=settings.rate_limit.enabled
            )
        app.state.template_service = TemplateService(
            repo=TemplateRepository(db.sessionmaker)
        )
        app.state.report_store = ReportStore(repo=ReportRepository(db.sessionmaker))
    app.state.audit = AuditLog(
        Path(settings.audit.log_file) if settings.audit.enabled else None,
        enabled=settings.audit.enabled,
    )
    # -- model hub (verified downloads + auto-configure) ----------------------
    app.state.model_manager = ModelManager(
        settings.models, metrics=app.state.metrics, audit=app.state.audit
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
        # Phase 7 rate limiting (auth paths get a tighter bucket). Health
        # probes + the Prometheus scrape path are exempt. Redis hiccups
        # degrade open — see RateLimiter.
        if request.url.path not in ("/health", "/health/ready", "/metrics"):
            try:
                await app.state.rate_limiter.check_request(
                    path=request.url.path,
                    client_ip=request.client.host if request.client else "unknown",
                    user_id=None,  # auth not resolved yet; ip/user bucket chosen below
                    per_minute=settings.rate_limit.requests_per_minute,
                    auth_per_minute=settings.rate_limit.auth_per_minute,
                    window_s=settings.rate_limit.window_seconds,
                )
            except RateLimitExceeded as exc:
                app.state.metrics.incr("http.rate_limited")
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "code": ErrorCode.RATE_LIMITED,
                            "message": "rate limit exceeded — slow down",
                        }
                    },
                    headers={"Retry-After": str(max(1, int(exc.retry_after_s)))},
                )
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
