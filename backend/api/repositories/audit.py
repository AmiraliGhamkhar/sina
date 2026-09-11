"""Audit repository (Phase 7): append-only rows + admin query API."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.models.orm import AuditLogRow


class AuditRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def append(self, event: str, user_id: str | None, payload: dict[str, Any]) -> str:
        async with self._sm() as session:
            row = AuditLogRow(event=event, user_id=user_id, payload=payload)
            session.add(row)
            await session.commit()
            return row.id

    async def query(
        self,
        *,
        event: str | None = None,
        user_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._sm() as session:
            stmt = select(AuditLogRow).order_by(AuditLogRow.created_at.desc()).limit(limit)
            if offset:
                stmt = stmt.offset(offset)
            if event:
                stmt = stmt.where(AuditLogRow.event == event)
            if user_id:
                stmt = stmt.where(AuditLogRow.user_id == user_id)
            if since:
                stmt = stmt.where(AuditLogRow.created_at >= since)
            result = await session.execute(stmt)
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                    "event": r.event,
                    "user_id": r.user_id,
                    "payload": r.payload,
                }
                for r in result.scalars()
            ]

    async def count(self, *, event: str | None = None) -> int:
        async with self._sm() as session:
            stmt = select(func.count()).select_from(AuditLogRow)
            if event:
                stmt = stmt.where(AuditLogRow.event == event)
            result = await session.execute(stmt)
            return int(result.scalar() or 0)

    async def purge_before(self, before: datetime | None = None) -> int:
        """Retention helper (admin); audit rows are append-only otherwise."""
        from sqlalchemy import delete

        async with self._sm() as session:
            result = await session.execute(
                delete(AuditLogRow).where(AuditLogRow.created_at < (before or datetime.now(UTC)))
            )
            await session.commit()
            return result.rowcount or 0
