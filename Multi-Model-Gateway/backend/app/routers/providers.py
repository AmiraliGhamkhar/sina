"""CRUD router for user-configured providers (bring-your-own-key).

Keys are encrypted at rest and never returned; ProviderOut exposes a masked
suffix only. Ownership follows the codebase convention: 404 for "not found",
403 for "someone else's".
"""
import ipaddress
import socket
import uuid
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.security import get_current_user
from app.db import get_db
from app.models.providers import Provider
from app.services.provider_registry import (
    list_providers,
    mask_key,
    seed_default_providers,
    test_provider,
)

router = APIRouter()

ProviderType = Literal["openai_compatible", "openai", "anthropic", "google", "openrouter"]
ProviderRole = Literal["local", "cloud"]


def _validate_provider_base_url(base_url: str | None) -> None:
    """Validate a provider base_url: must be http(s) with a hostname, and —
    when ALLOW_PRIVATE_PROVIDER_URLS is False — must resolve to a public
    (non-private/loopback/link-local/reserved) address. Returns early for
    None/empty; raises ValueError with a reason on any violation."""
    if not base_url:
        return
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("base_url must be http(s)")
    if not parsed.hostname:
        raise ValueError("base_url must include a hostname")
    if settings.ALLOW_PRIVATE_PROVIDER_URLS:
        return
    # Public-only mode: mirror services/search.py::_assert_public_host. A
    # resolution failure is tolerated (the check is skipped) — a transient DNS
    # miss shouldn't block saving a provider; the real connection will surface
    # an unreachable host with a clear error.
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local      # covers 169.254.0.0/16 incl. cloud metadata
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"base_url must be a public URL, got non-public address {addr} (host '{parsed.hostname}')"
            )


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: ProviderType
    role: ProviderRole
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    default_model: Optional[str] = None
    is_default: bool = False
    enabled: bool = True

    @model_validator(mode="after")
    def _require_base_url_for_compat(self):
        if self.type == "openai_compatible" and not self.base_url:
            raise ValueError("base_url is required for openai_compatible providers")
        _validate_provider_base_url(self.base_url)
        return self


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    type: Optional[ProviderType] = None
    role: Optional[ProviderRole] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # None = leave unchanged
    default_model: Optional[str] = None
    is_default: Optional[bool] = None
    enabled: Optional[bool] = None


class ProviderOut(BaseModel):
    id: UUID
    name: str
    type: str
    role: str
    base_url: Optional[str] = None
    default_model: Optional[str] = None
    is_default: bool
    enabled: bool
    created_at: datetime
    api_key_masked: str


def _to_out(row) -> ProviderOut:
    api_key_masked = ""
    if row.api_key_encrypted:
        try:
            api_key_masked = mask_key(crypto.decrypt_secret(row.api_key_encrypted))
        except ValueError:
            # key material unreadable (e.g. SECRET_KEY rotated) — show a generic mask
            api_key_masked = "****"
    return ProviderOut(
        id=row.id,
        name=row.name,
        type=row.type,
        role=row.role,
        base_url=row.base_url,
        default_model=row.default_model,
        is_default=row.is_default,
        enabled=row.enabled,
        created_at=row.created_at,
        api_key_masked=api_key_masked,
    )


async def _get_owned_provider(db: AsyncSession, provider_id: UUID, user_id: str) -> Provider:
    result = await db.execute(select(Provider).where(Provider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    if str(provider.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorised")
    return provider


async def _clear_role_defaults(db: AsyncSession, user_id: str, role: str) -> None:
    """Un-mark every other provider in a role so exactly one can be default."""
    await db.execute(
        update(Provider)
        .where(Provider.user_id == user_id, Provider.role == role)
        .values(is_default=False)
    )


@router.get("/providers")
async def get_providers(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    await seed_default_providers(db, user_id)
    rows = await list_providers(db, user_id)
    return {"data": [_to_out(r) for r in rows]}


@router.post("/providers")
async def create_provider(
    request: ProviderCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    if request.is_default:
        await _clear_role_defaults(db, user_id, request.role)

    provider = Provider(
        id=uuid.uuid4(),
        user_id=user_id,
        name=request.name,
        type=request.type,
        role=request.role,
        base_url=request.base_url,
        api_key_encrypted=crypto.encrypt_secret(request.api_key) if request.api_key else None,
        default_model=request.default_model,
        is_default=request.is_default,
        enabled=request.enabled,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return _to_out(provider)


@router.patch("/providers/{provider_id}")
async def update_provider(
    provider_id: UUID,
    request: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    provider = await _get_owned_provider(db, provider_id, user_id)

    data = request.model_dump(exclude_none=True)

    # Validate against the provider's effective state after the patch: switching
    # an existing provider to openai_compatible (or clearing its base_url while
    # it stays openai_compatible) must not leave it without a base_url.
    eff_type = data.get("type") or provider.type
    eff_base_url = data.get("base_url") if "base_url" in data else provider.base_url
    if eff_type == "openai_compatible" and not (eff_base_url and eff_base_url.strip()):
        raise HTTPException(
            status_code=422,
            detail="base_url is required for openai_compatible providers",
        )
    try:
        _validate_provider_base_url(eff_base_url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if "api_key" in data:
        data["api_key_encrypted"] = crypto.encrypt_secret(data.pop("api_key"))
    if data.get("is_default"):
        # clear defaults in the role the provider will end up in after this patch
        role_for_clear = data.get("role") or provider.role
        await _clear_role_defaults(db, user_id, role_for_clear)

    for key, value in data.items():
        setattr(provider, key, value)

    await db.commit()
    await db.refresh(provider)
    return _to_out(provider)


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    provider = await _get_owned_provider(db, provider_id, user_id)
    await db.delete(provider)
    await db.commit()
    return {"detail": "Provider deleted"}


@router.post("/providers/{provider_id}/test")
async def test_provider_endpoint(
    provider_id: UUID,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    provider = await _get_owned_provider(db, provider_id, user_id)
    return await test_provider(provider)
