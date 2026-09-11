"""Auth endpoints (Phase 7: real credential authentication).

- ``POST /auth/login`` — argon2 verification against the users table
  (requires MS_DATABASE__URL); lockout after repeated failures; issues a
  short-TTL access JWT + one-time refresh token.
- ``POST /auth/refresh`` — rotation: the presented refresh token is consumed
  (single use); reuse of a consumed token revokes ALL of the user's sessions.
- ``POST /auth/logout`` — consumes the presented refresh token.
- ``GET  /auth/me`` — principal echo (unchanged since Phase 1).

The dev-token path remains for non-production builds without a database.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from api.auth.deps import CurrentPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    PrincipalInfo,
    RefreshRequest,
    TokenPair,
)
from api.services.auth_service import AuthError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_service(request: Request):
    return request.app.state.auth_service


def _auth_error(exc: AuthError) -> ApiError:
    status = exc.status_code
    code = {
        401: ErrorCode.UNAUTHENTICATED,
        403: ErrorCode.FORBIDDEN,
        429: ErrorCode.RATE_LIMITED,
        501: ErrorCode.AUTH_NOT_IMPLEMENTED,
    }.get(status, ErrorCode.INTERNAL)
    return ApiError(status, code, exc.reason)


@router.post("/login", response_model=TokenPair)
async def login(request: Request, body: LoginRequest) -> TokenPair:
    settings = request.app.state.settings
    # Dev convenience: static token login without a database (never in prod).
    dev = settings.auth.dev_token
    if (
        dev is not None
        and not settings.is_production
        and body.username == "dev"
        and dev.get_secret_value() == body.password
    ):
        service = _auth_service(request)
        try:
            pair = await service.issue_pair("dev", "admin", body.device_name or "dev")
        except AuthError as exc:  # no jwt secret configured
            raise _auth_error(exc) from exc
        request.app.state.audit.emit(
            "login", user_id="dev", method="dev-token", ok=True
        )
        return TokenPair(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
        )

    try:
        service = _auth_service(request)
        pair = await service.login(
            body.username, body.password, device=body.device_name or ""
        )
    except AuthError as exc:
        request.app.state.audit.emit(
            "login", user_id=None, username_len=len(body.username), ok=False,
            reason=exc.reason[:120],
        )
        raise _auth_error(exc) from exc
    request.app.state.audit.emit(
        "login", user_id=pair.user_id, method="credentials", ok=True
    )
    return TokenPair(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(request: Request, body: RefreshRequest) -> TokenPair:
    try:
        service = _auth_service(request)
        pair = await service.refresh(body.refresh_token)
    except AuthError as exc:
        raise _auth_error(exc) from exc
    return TokenPair(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout")
async def logout(
    request: Request, principal: CurrentPrincipal, body: LogoutRequest | None = None
) -> dict:
    # revoke the presented refresh token (rotation makes it single-use anyway)
    service = _auth_service(request)
    if body is not None and body.refresh_token:
        await service.logout(body.refresh_token)
    request.app.state.audit.emit("logout", user_id=principal.user_id)
    return {"status": "ok", "user_id": principal.user_id}


@router.get("/me", response_model=PrincipalInfo)
async def me(principal: CurrentPrincipal) -> PrincipalInfo:
    return PrincipalInfo(user_id=principal.user_id, role=principal.role, is_dev=principal.is_dev)
