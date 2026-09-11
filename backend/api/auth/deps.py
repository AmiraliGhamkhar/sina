"""FastAPI dependencies resolving the caller principal.

Policy (Phase 1):
1. Dev static token (``MS_AUTH__DEV_TOKEN``) → synthetic "dev" principal.
   Hard-disabled when env == "production". Checked first so dev flows don't
   need JWT signing material at all.
2. ``Authorization: Bearer <jwt>`` when ``MS_AUTH__JWT_SECRET`` is set.
3. Anything else → 401 UNAUTHENTICATED on auth-required routes.

WebSocket connections use the same resolution via the ``token`` query
parameter (browsers) or the Authorization header (WPF's
ClientWebSocket supports headers; query param also accepted for parity).
"""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, Request, WebSocket

from api.auth.tokens import Principal, TokenError, decode_token
from api.config import Settings, get_settings
from api.errors import ApiError, ErrorCode

logger = logging.getLogger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_app_settings(request: Request) -> Settings:
    """Canonical per-app settings accessor — create_app() stores the active
    Settings on app.state; tests and multi-app factories never touch the
    process-global :func:`get_settings` cache."""
    return request.app.state.settings


AppSettingsDep = Annotated[Settings, Depends(get_app_settings)]


def _bearer(request_headers) -> str | None:
    header = request_headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def resolve_principal(headers, query_params, settings: Settings) -> Principal | None:
    """Return the authenticated principal, ``None`` for anonymous, or raise
    :class:`ApiError` (401) for a *bad* token. Anonymous is only OK for
    endpoints that don't require auth."""
    token = _bearer(headers) or query_params.get("token")
    if not token:
        return None
    dev = settings.auth.dev_token
    if dev is not None and not settings.is_production:
        if hmac.compare_digest(dev.get_secret_value(), token):
            return Principal(user_id="dev", role="admin", is_dev=True)
    jwt_secret = settings.auth.jwt_secret
    if jwt_secret is not None:
        try:
            return decode_token(jwt_secret.get_secret_value(), token)
        except TokenError as exc:
            raise ApiError(401, ErrorCode.UNAUTHENTICATED, str(exc)) from exc
    raise ApiError(401, ErrorCode.UNAUTHENTICATED, "invalid token")


async def get_current_principal(
    request: Request, settings: AppSettingsDep
) -> Principal:
    principal = resolve_principal(request.headers, request.query_params, settings)
    if principal is None:
        raise ApiError(
            401,
            ErrorCode.UNAUTHENTICATED,
            "authentication required (Bearer JWT; dev builds may use MS_AUTH__DEV_TOKEN)",
        )
    return principal


async def get_optional_principal(request: Request, settings: AppSettingsDep) -> Principal | None:
    try:
        return resolve_principal(request.headers, request.query_params, settings)
    except ApiError:
        return None


async def websocket_principal(websocket: WebSocket, settings: Settings) -> Principal:
    """Resolve WS auth. May close the socket itself on rejection (the route
    catches that and answers 4401)."""
    principal = resolve_principal(websocket.headers, websocket.query_params, settings)
    if principal is None:
        if settings.is_production:
            raise ApiError(401, ErrorCode.UNAUTHENTICATED, "websocket authentication required")
        # Dev convenience: anonymous WS in non-production only, loudly logged.
        logger.warning("anonymous websocket accepted (dev mode only)")
        return Principal(user_id="anon", role="clinician", is_dev=True)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]
