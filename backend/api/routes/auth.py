"""Auth endpoints.

Phase 1 freezes the *contract* (schemas + routes) and returns 501
AUTH_NOT_IMPLEMENTED so the WPF login flow can be built and tested against
real status codes; credential verification, password hashing, refresh
rotation and lockout policy land in Phase 7 (docs/ARCHITECTURE.md §Auth).

GET /me is fully functional today for bearer-token principals (JWT or dev
token) — it exercises the auth dependency chain end-to-end.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.auth.deps import CurrentPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.auth import LoginRequest, PrincipalInfo, RefreshRequest, TokenPair
from api.version import PHASE

router = APIRouter(prefix="/auth", tags=["auth"])

_NOT_READY_DETAIL = (
    f"credential authentication is scheduled for Phase 7 (current Phase {PHASE}); "
    "dev builds may use MS_AUTH__DEV_TOKEN with Authorization: Bearer"
)


def _not_implemented() -> ApiError:
    return ApiError(501, ErrorCode.AUTH_NOT_IMPLEMENTED, _NOT_READY_DETAIL)


@router.post("/login", response_model=TokenPair)
async def login(request: Request, body: LoginRequest) -> TokenPair:
    settings = request.app.state.settings
    # Dev convenience: static token login without a database.
    dev = settings.auth.dev_token
    if (
        dev is not None
        and not settings.is_production
        and body.username == "dev"
        and dev.get_secret_value() == body.password
    ):
        from api.auth.tokens import create_token

        secret = settings.auth.jwt_secret.get_secret_value() if settings.auth.jwt_secret else "dev-only-secret-change-me"
        access, _jti = create_token(
            secret, sub="dev", role="admin", ttl_minutes=settings.auth.access_ttl_minutes
        )
        refresh, _ = create_token(
            secret,
            sub="dev",
            role="admin",
            ttl_minutes=settings.auth.refresh_ttl_days * 24 * 60,
            typ="refresh",
        )
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            expires_in=settings.auth.access_ttl_minutes * 60,
        )
    raise _not_implemented()


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest) -> TokenPair:
    raise _not_implemented()


@router.post("/logout")
async def logout(principal: CurrentPrincipal) -> dict:
    # Real implementation revokes the refresh token (Phase 7).
    return {"status": "ok", "note": "no revocation store yet (Phase 7)", "user_id": principal.user_id}


@router.get("/me", response_model=PrincipalInfo)
async def me(principal: CurrentPrincipal) -> PrincipalInfo:
    return PrincipalInfo(user_id=principal.user_id, role=principal.role, is_dev=principal.is_dev)
