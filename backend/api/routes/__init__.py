"""Route package: one module per resource.

``API_ROUTERS`` are mounted under /api/v1; health mounts at root (infra
probes first); the WebSocket router mounts at root with its own versioned
path (/ws/v1/...) so proxy rules can stay stable.
"""
from fastapi import APIRouter

from api.routes.admin import router as admin_router
from api.routes.auth import router as auth_router
from api.routes.clinical import router as clinical_router
from api.routes.health import router as health_router
from api.routes.meta import router as meta_router
from api.routes.metrics import router as metrics_router
from api.routes.models import router as models_router
from api.routes.providers import router as providers_router
from api.routes.reports import router as reports_router
from api.routes.templates import router as templates_router
from api.routes.terminology import router as terminology_router
from api.routes.transcribe import router as transcribe_router
from api.routes.transcripts import router as transcripts_router
from api.routes.ws import router as ws_router

API_ROUTERS: list[APIRouter] = [
    auth_router,
    meta_router,
    providers_router,
    models_router,
    transcribe_router,
    transcripts_router,
    reports_router,
    templates_router,
    terminology_router,
    clinical_router,
    admin_router,
]
ROOT_ROUTERS: list[APIRouter] = [health_router, metrics_router]
WS_ROUTERS: list[APIRouter] = [ws_router]
