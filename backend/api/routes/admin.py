"""Admin API (Phase 7): user management, audit queries, provider secrets,
AI usage ledger. Admin-role gated (spec §14 authorization)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ai.base import ProviderKind
from api.auth.deps import CurrentPrincipal
from api.errors import ApiError, ErrorCode

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(principal) -> None:
    if principal.role != "admin":
        raise ApiError(
            403, ErrorCode.FORBIDDEN, f"admin role required (you are '{principal.role}')"
        )


def _require_db(request: Request):
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise ApiError(
            501, ErrorCode.DB_NOT_CONFIGURED, "admin APIs require MS_DATABASE__URL"
        )
    return db


# -- users ------------------------------------------------------------------


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="clinician", pattern=r"^(admin|physician|clinician|scribe|auditor)$")
    display_name: str = Field(default="", max_length=120)


@router.get("/users")
async def list_users(request: Request, principal: CurrentPrincipal) -> dict:
    _require_admin(principal)
    from api.repositories.users import UserRepository

    users = await UserRepository(_require_db(request).sessionmaker).list()
    return {
        "users": [
            {
                "user_id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "role": u.role_id,
                "is_active": u.is_active,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "created_at": u.created_at.isoformat() if u.created_at else "",
            }
            for u in users
        ],
        "total": len(users),
    }


@router.post("/users", status_code=201)
async def create_user(request: Request, body: UserCreate, principal: CurrentPrincipal) -> dict:
    _require_admin(principal)
    from api.repositories.users import UserRepository
    from api.services.auth_service import PasswordHasher

    settings = request.app.state.settings
    users = UserRepository(_require_db(request).sessionmaker)
    if await users.get_by_username(body.username) is not None:
        raise ApiError(422, ErrorCode.VALIDATION, f"username '{body.username}' already exists")
    hasher = PasswordHasher(
        time_cost=settings.auth.argon2_time_cost,
        memory_cost=settings.auth.argon2_memory_cost,
        parallelism=settings.auth.argon2_parallelism,
    )
    user = await users.create(
        username=body.username,
        role_id=body.role,
        password_hash=hasher.hash(body.password),
        display_name=body.display_name or body.username,
    )
    request.app.state.audit.emit(
        "user_created", user_id=user.id, role=body.role, created_by=principal.user_id
    )
    return {"user_id": user.id, "username": user.username, "role": user.role_id}


class UserActivePatch(BaseModel):
    is_active: bool


@router.patch("/users/{user_id}")
async def set_user_active(
    request: Request, user_id: str, body: UserActivePatch, principal: CurrentPrincipal
) -> dict:
    _require_admin(principal)
    from api.repositories.users import UserRepository

    users = UserRepository(_require_db(request).sessionmaker)
    updated = await users.set_active(user_id, body.is_active)
    if updated is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"user '{user_id}' not found")
    request.app.state.audit.emit(
        "user_active_changed", user_id=user_id, is_active=body.is_active,
        changed_by=principal.user_id,
    )
    return {"user_id": user_id, "is_active": updated.is_active}


# -- audit queries ---------------------------------------------------------------


@router.get("/audit")
async def query_audit(
    request: Request,
    principal: CurrentPrincipal,
    event: str | None = Query(default=None, max_length=64),
    user_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    _require_admin(principal)
    from api.repositories.audit import AuditRepository

    repo = AuditRepository(_require_db(request).sessionmaker)
    rows = await repo.query(event=event, user_id=user_id, limit=limit, offset=offset)
    return {"events": rows, "total": len(rows), "limit": limit, "offset": offset}


# -- provider secrets (encrypted at rest; never returned) --------------------------


class SecretPut(BaseModel):
    secret: str = Field(min_length=8, max_length=512)


@router.put("/providers/{kind}/{name}/secret", status_code=204)
async def put_provider_secret(
    request: Request, kind: str, name: str, body: SecretPut, principal: CurrentPrincipal
) -> None:
    _require_admin(principal)
    if kind not in ("stt", "llm"):
        raise ApiError(422, ErrorCode.VALIDATION, "kind must be stt or llm")
    vault = getattr(request.app.state, "secret_vault", None)
    provider_repo = getattr(request.app.state, "provider_repo", None)
    if vault is None or not vault.enabled or provider_repo is None:
        raise ApiError(
            501,
            ErrorCode.DB_NOT_CONFIGURED,
            "provider secret storage requires MS_SECURITY__SECRET_ENCRYPTION_KEY and a database",
        )
    # the provider must exist in the mirror
    descriptors = {
        d.name for d in request.app.state.ai_registry.descriptors(ProviderKind(kind))
    }
    if name not in descriptors:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"unknown {kind} provider '{name}'")
    ok = await provider_repo.set_secret(kind, name, vault.encrypt(body.secret))
    if not ok:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"provider row for '{name}' missing (restart?)")
    request.app.state.audit.emit(
        "provider_secret_stored", provider=f"{kind}:{name}", user_id=principal.user_id
    )  # the secret itself never reaches the audit log (deny-list + never passed)


@router.delete("/providers/{kind}/{name}/secret", status_code=204)
async def delete_provider_secret(
    request: Request, kind: str, name: str, principal: CurrentPrincipal
) -> None:
    _require_admin(principal)
    provider_repo = getattr(request.app.state, "provider_repo", None)
    if provider_repo is None:
        raise ApiError(501, ErrorCode.DB_NOT_CONFIGURED, "requires a database")
    await provider_repo.set_secret(kind, name, None)
    request.app.state.audit.emit(
        "provider_secret_cleared", provider=f"{kind}:{name}", user_id=principal.user_id
    )


# -- AI usage ledger -----------------------------------------------------------------


@router.get("/ai-requests")
async def list_ai_requests(
    request: Request,
    principal: CurrentPrincipal,
    kind: str | None = Query(default=None, pattern="^(stt|llm)$"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    _require_admin(principal)
    from api.repositories.ai import AIRequestRepository

    repo = AIRequestRepository(_require_db(request).sessionmaker)
    rows = await repo.recent(limit=limit, kind=kind)
    return {"requests": rows, "total": len(rows)}
