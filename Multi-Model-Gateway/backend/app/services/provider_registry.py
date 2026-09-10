"""Registry: turns DB Provider rows into live adapter instances and answers
"which provider should this user/role use" questions.

Seeding keeps the old env-var behavior working: on first list, rows are created
from settings.LM_URL / get_openrouter_api_key() so the existing local-first
routing keeps working without any user configuration.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings, get_openrouter_api_key
from app.core.exceptions import AppError, ForbiddenError, NotFoundError
from app.models.providers import Provider
from app.services.providers import (
    LLMProvider,
    AnthropicProvider,
    GoogleProvider,
    OpenAICompatProvider,
    OpenAIProvider,
    OpenRouterProvider,
)

logger = logging.getLogger(__name__)


class NoProviderError(AppError):
    status_code = 503
    detail = "no provider configured for this role"


class ProviderConfigError(AppError):
    status_code = 400
    detail = "provider configuration error"


def mask_key(key: str) -> str:
    """Mask a key for display: '****' + last 4 chars (or just '****')."""
    if len(key) <= 4:
        return "****"
    return "****" + key[-4:]


def row_to_provider(row) -> LLMProvider:
    """Build the right adapter for a Provider row, decrypting its key."""
    api_key = None
    if row.api_key_encrypted:
        api_key = crypto.decrypt_secret(row.api_key_encrypted)

    if row.type == "openai_compatible":
        if not row.base_url:
            raise ValueError("base_url is required for openai_compatible providers")
        return OpenAICompatProvider(
            base_url=row.base_url,
            api_key=api_key,
            default_model=row.default_model,
        )
    if row.type == "openai":
        return OpenAIProvider(api_key=api_key, default_model=row.default_model)
    if row.type == "openrouter":
        return OpenRouterProvider(api_key=api_key, default_model=row.default_model)
    if row.type == "anthropic":
        return AnthropicProvider(base_url=row.base_url, api_key=api_key, default_model=row.default_model)
    if row.type == "google":
        return GoogleProvider(base_url=row.base_url, api_key=api_key, default_model=row.default_model)
    raise ValueError(f"unknown provider type: {row.type}")


async def list_providers(db: AsyncSession, user_id: str) -> list:
    result = await db.execute(
        select(Provider)
        .where(Provider.user_id == user_id)
        .order_by(Provider.created_at.asc())
    )
    return list(result.scalars().all())


async def get_default_provider(db: AsyncSession, user_id: str, role: str):
    """Best provider row for a role: the marked default first, else the oldest
    enabled one. Disabled rows are never returned. None when nothing is
    configured for the role."""
    result = await db.execute(
        select(Provider).where(
            Provider.user_id == user_id,
            Provider.role == role,
            Provider.is_default.is_(True),
            Provider.enabled.is_(True),
        )
    )
    row = result.scalars().first()
    if row is not None:
        return row

    result = await db.execute(
        select(Provider)
        .where(Provider.user_id == user_id, Provider.role == role, Provider.enabled.is_(True))
        .order_by(Provider.created_at.asc())
    )
    return result.scalars().first()


async def resolve_provider(
    db: AsyncSession,
    user_id: str,
    *,
    role: str,
    provider_id: str | None = None,
) -> tuple[LLMProvider, str | None]:
    """Resolve a provider: either the given row (ownership-checked) or the
    role's default. Returns (adapter, default_model)."""
    if provider_id is not None:
        result = await db.execute(select(Provider).where(Provider.id == provider_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"provider {provider_id} not found")
        if str(row.user_id) != str(user_id):
            raise ForbiddenError("unauthorised")
        return row_to_provider(row), row.default_model

    row = await get_default_provider(db, user_id, role)
    if row is None:
        raise NoProviderError(f"no provider configured for {role}")
    return row_to_provider(row), row.default_model


async def test_provider(row) -> dict:
    """One-token round-trip against a provider row. Never raises: the result is
    a dict the router can return directly."""
    if not row.default_model:
        return {"ok": False, "error": "no default model set"}
    try:
        provider = row_to_provider(row)
        await provider.complete(
            messages=[{"role": "user", "content": "ping"}],
            model=row.default_model,
            temperature=0.1,
            max_tokens=1,
        )
    except Exception as exc:  # noqa: BLE001 — any failure must surface as a generic test result
        # the real exception stays server-side; the client gets a generic message
        # so provider internals (urls, key fragments, sdk tracebacks) never leak.
        logger.warning("provider test failed (%s): %s", getattr(row, "name", "?"), exc)
        return {"ok": False, "error": "provider test failed"}
    return {"ok": True, "model": row.default_model}


async def seed_default_providers(db: AsyncSession, user_id: str) -> None:
    """Create backward-compat rows from env settings. Idempotent: rows are
    keyed by (user_id, name), and existing names are never touched. base_urls
    are stored as entered; the OpenAI-compatible adapter appends /v1 when the
    URL lacks it (see providers/openai_compat.py)."""
    result = await db.execute(
        select(Provider.name).where(Provider.user_id == user_id)
    )
    existing = {name for (name,) in result.all()}

    created = False

    if "Local (LM Studio)" not in existing and settings.LM_URL:
        db.add(
            Provider(
                id=uuid.uuid4(),
                user_id=user_id,
                name="Local (LM Studio)",
                type="openai_compatible",
                role="local",
                base_url=settings.LM_URL,
                api_key_encrypted=crypto.encrypt_secret("LM-STUDIO"),
                default_model=settings.LM_CHAT_MODEL or settings.LM_DEFAULT_MODEL,
                is_default=True,
                enabled=True,
            )
        )
        created = True

    openrouter_key = get_openrouter_api_key()
    if "OpenRouter" not in existing and openrouter_key:
        db.add(
            Provider(
                id=uuid.uuid4(),
                user_id=user_id,
                name="OpenRouter",
                type="openrouter",
                role="cloud",
                base_url="https://openrouter.ai/api/v1",
                api_key_encrypted=crypto.encrypt_secret(openrouter_key),
                default_model=settings.OPENROUTER_DEFAULT_MODEL or None,
                is_default=True,
                enabled=True,
            )
        )
        created = True

    if created:
        await db.commit()
