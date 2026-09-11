"""Report-template repository (Phase 7)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.models.orm import ReportTemplate


def _to_dict(row: ReportTemplate) -> dict[str, Any]:
    return {
        "key": row.key,
        "name": row.name,
        "category": row.category,
        "description": row.description,
        "sections": list(row.sections or []),
        "builtin": row.builtin,
        "version": row.version,
        "deleted": row.deleted,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


class TemplateRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def list(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        async with self._sm() as session:
            result = await session.execute(select(ReportTemplate).order_by(ReportTemplate.key))
            rows = [r for r in result.scalars() if include_deleted or not r.deleted]
            return [_to_dict(r) for r in rows]

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._sm() as session:
            row = await session.get(ReportTemplate, key)
            if row is None or row.deleted:
                return None
            return _to_dict(row)

    async def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        async with self._sm() as session:
            row = await session.get(ReportTemplate, data["key"])
            if row is None:
                row = ReportTemplate(key=data["key"])
                session.add(row)
            for field in (
                "name", "category", "description", "sections", "builtin", "version", "deleted",
            ):
                if field in data:
                    setattr(row, field, data[field])
            await session.commit()
            await session.refresh(row)
            return _to_dict(row)

    async def soft_delete(self, key: str) -> bool:
        async with self._sm() as session:
            row = await session.get(ReportTemplate, key)
            if row is None:
                return False
            row.deleted = True
            await session.commit()
            return True
