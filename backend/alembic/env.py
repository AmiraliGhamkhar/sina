"""Alembic environment (Phase 7).

- URL resolution: ``MS_DATABASE__URL`` (same setting as the API) or the
  ``-x db_url=...`` one-shot override — never a hard-coded URL here.
- Target metadata: ``api.models.orm.Base.metadata`` — the single schema
  definition the runtime also uses (``create_all`` is the dev convenience;
  alembic is the production path).
- Async engine (asyncpg/aiosqlite) via ``run_sync``.
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

# make `api` (backend/) and `ai` (repo root) importable regardless of how
# alembic is invoked — the editable install is NOT required for migrations
_here = os.path.dirname(__file__)
for _p in (os.path.join(_here, ".."), os.path.join(_here, "..", "..")):
    _p = os.path.abspath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from api.db.engine import normalize_database_url  # noqa: E402
from api.models.orm import ALL_MODELS, Base  # noqa: E402, F401 — imports register tables

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    url = override or os.environ.get("MS_DATABASE__URL", "")
    if not url:
        raise SystemExit(
            "set MS_DATABASE__URL (or pass -x db_url=...) — migrations never guess"
        )
    return normalize_database_url(url)


target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    """Ignore dev-only sqlite artifacts; everything else migrates."""
    if type_ == "table" and name in ("sqlite_stat1", "alembic_version"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_database_url(), poolclass=None)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
