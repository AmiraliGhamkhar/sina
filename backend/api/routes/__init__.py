"""Route package: one module per resource.

``API_ROUTERS`` are mounted under /api/v1; health mounts at root (infra
probes first); the WebSocket router mounts at root with its own versioned
path (/ws/v1/...) so proxy rules can stay stable.
"""
from fastapi import APIRouter

from api.routes.auth import router as auth_router
from api.routes.health import router as health_router
from api.routes.meta import router as meta_router
from api.routes.providers import router as providers_router
from api.routes.transcripts import router as transcripts_router
from api.routes.ws import router as ws_router

API_ROUTERS: list[APIRouter] = [auth_router, meta_router, providers_router, transcripts_router]
ROOT_ROUTERS: list[APIRouter] = [health_router]
WS_ROUTERS: list[APIRouter] = [ws_router]
