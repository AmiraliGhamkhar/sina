"""Startup seeding (Phase 7): roles, built-in templates, admin bootstrap,
provider metadata mirror. Idempotent — safe on every boot."""
from __future__ import annotations

import logging

from api.models.orm import ReportTemplate as ReportTemplateRow
from api.models.orm import Role

logger = logging.getLogger(__name__)

ROLES: tuple[tuple[str, str, list[str]], ...] = (
    ("admin", "Platform administrator", ["*"]),
    ("physician", "Clinician: dictate, edit, finalize/approve reports", [
        "transcribe", "report.draft", "report.edit", "report.finalize", "report.approve",
    ]),
    ("clinician", "Alias of physician (default role)", [
        "transcribe", "report.draft", "report.edit", "report.finalize", "report.approve",
    ]),
    ("scribe", "Medical scribe: transcribe + edit, no approval", [
        "transcribe", "report.draft", "report.edit",
    ]),
    ("auditor", "Read-only compliance access", ["audit.read"]),
)


async def seed_all(app) -> None:
    """Requires app.state.db (call only in DB mode)."""
    db = getattr(app.state, "db", None)
    if db is None:
        return
    settings = app.state.settings

    # roles (idempotent)
    async with db.session() as session:
        for role_id, description, permissions in ROLES:
            if await session.get(Role, role_id) is None:
                session.add(Role(id=role_id, description=description, permissions=permissions))
        await session.commit()

    # built-in templates as durable rows (custom rows untouched)
    from api.services.templates import BUILTIN_TEMPLATES

    async with db.session() as session:
        from sqlalchemy import select

        existing = set((await session.execute(select(ReportTemplateRow.key))).scalars())
        for template in BUILTIN_TEMPLATES:
            if template.key not in existing:
                session.add(
                    ReportTemplateRow(
                        key=template.key,
                        name=template.name,
                        category=template.category,
                        description=template.description,
                        sections=[s.as_dict() for s in template.sections],
                        builtin=True,
                        version=template.version,
                    )
                )
        await session.commit()

    # admin bootstrap (explicit env password only — never a default credential)
    from api.repositories.users import UserRepository
    from api.services.auth_service import PasswordHasher

    admin_pw = settings.auth.bootstrap_admin_password
    if admin_pw is not None:
        username = settings.auth.bootstrap_admin_username
        users = UserRepository(db.sessionmaker)
        if await users.get_by_username(username) is None:
            hasher = PasswordHasher(
                time_cost=settings.auth.argon2_time_cost,
                memory_cost=settings.auth.argon2_memory_cost,
                parallelism=settings.auth.argon2_parallelism,
            )
            await users.create(
                username=username,
                role_id="admin",
                password_hash=hasher.hash(admin_pw.get_secret_value()),
                display_name="Administrator",
            )
            logger.info("bootstrapped admin user %s", username)
        else:
            logger.info("admin user %s already exists — bootstrap skipped", username)

    await _mirror_providers(app)


async def _mirror_providers(app) -> None:
    """Provider metadata rows (spec §13 AIProvider): env-configured view.
    Secrets are NOT mirrored — they live in env or the encrypted vault."""
    from ai.base import ProviderKind
    from api.repositories.ai import AIProviderRepository

    settings = app.state.settings
    registry = app.state.ai_registry
    repo = AIProviderRepository(app.state.db.sessionmaker)
    for kind in (ProviderKind.STT, ProviderKind.LLM):
        for descriptor in registry.descriptors(kind):
            caps = descriptor.capabilities
            await repo.upsert_metadata(
                kind=kind.value,
                name=descriptor.name,
                privacy_class=caps.privacy.value,
                capabilities={
                    "supports_streaming": caps.supports_streaming,
                    "supports_batch": caps.supports_batch,
                    "languages": list(caps.languages),
                    "latency_hint_ms": caps.latency_hint_ms,
                },
                configured=registry.is_configured(
                    kind, descriptor.name,
                    settings.provider_config(kind.value, descriptor.name),
                ),
            )
