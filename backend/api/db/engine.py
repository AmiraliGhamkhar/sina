"""Async engine + session factory (Phase 7).

Import-safety: SQLAlchemy is imported lazily so the base dev install (no
``db`` extra) still runs the whole in-memory test suite — DB mode activates
only when ``MS_DATABASE__URL`` is set AND the drivers are importable.
"""
from __future__ import annotations

import logging
from typing import Any

from api.db.url import DatabaseUrlError, normalize_database_url

logger = logging.getLogger(__name__)


class DatabaseNotConfigured(RuntimeError):
    pass


def _imports():
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    return AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Owns the async engine + sessionmaker; None-safe everywhere."""

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 10) -> None:
        AsyncEngine, _AsyncSession, async_sessionmaker, create_async_engine = _imports()
        from sqlalchemy.pool import NullPool

        try:
            normalized = normalize_database_url(url)
        except DatabaseUrlError as exc:
            raise DatabaseNotConfigured(str(exc)) from exc
        self.url = normalized
        self._is_sqlite = normalized.startswith("sqlite")
        kwargs: dict[str, Any] = {"echo": echo}
        if self._is_sqlite:
            # sqlite+aiosqlite: single connection semantics; pool args unsupported
            kwargs["poolclass"] = NullPool
        else:
            kwargs.update({"pool_size": pool_size, "pool_pre_ping": True})
        self.engine: AsyncEngine = create_async_engine(normalized, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_sqlite(self) -> bool:
        return self._is_sqlite

    def session(self):
        return self.sessionmaker()

    async def create_all(self) -> None:
        from api.models.orm import Base

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        from api.models.orm import Base

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def ping(self) -> bool:
        from sqlalchemy import text

        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 — readiness probe, never raises
            return False

    async def aclose(self) -> None:
        await self.engine.dispose()


def open_database(url: str | None, *, echo: bool = False, pool_size: int = 10) -> Database | None:
    """Return a Database when a URL is configured and drivers import; None
    keeps the app in the fully-functional in-memory dev mode."""
    if not url or not url.strip():
        return None
    try:
        return Database(url, echo=echo, pool_size=pool_size)
    except ImportError as exc:  # db extra not installed
        logger.error("MS_DATABASE__URL set but drivers missing (%s) — running in-memory", exc)
        return None
    except DatabaseNotConfigured as exc:
        logger.error("bad MS_DATABASE__URL (%s) — running in-memory", exc)
        return None
