"""Report persistence repository (Phase 7).

The in-memory ReportStore stays the live cache (it holds ValidationWarning
dataclasses etc.); this repository serializes full report state to one row +
append-only revisions. JSON columns keep the dataclass round-trip exact.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.models.orm import Report, ReportRevision


class ReportRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def save(self, data: dict[str, Any], *, revision: dict[str, Any] | None = None) -> None:
        """Idempotent upsert of the whole report state + optional revision row.
        ``data`` is the report_store.Report summary + sections payload."""
        async with self._sm() as session:
            row = await session.get(Report, data["report_id"])
            if row is None:
                row = Report(report_id=data["report_id"])
                session.add(row)
            row.encounter_id = data["encounter_id"]
            row.session_id = data.get("session_id")
            row.template_key = data.get("template_key")
            row.template_name = data.get("template_name")
            row.language = data.get("language", "fa-en")
            row.status = data["status"]
            row.sections = data.get("sections", {})
            row.section_titles = data.get("section_titles", {})
            row.warnings = data.get("warnings", [])
            row.provider = data.get("provider")
            row.model = data.get("model")
            row.routing_reason = data.get("routing_reason", "")
            row.privacy_override_applied = bool(data.get("privacy_override_applied"))
            row.phi_redaction_applied = bool(data.get("phi_redaction_applied"))
            row.terminology_substitutions = int(data.get("terminology_substitutions") or 0)
            row.created_by = data.get("created_by")
            row.amended_from = data.get("amended_from")
            row.updated_at = datetime.now(UTC)
            if revision:
                session.add(
                    ReportRevision(
                        report_id=data["report_id"],
                        action=str(revision.get("action", "update")),
                        user_id=revision.get("user_id"),
                        detail=dict(revision.get("detail") or {}),
                    )
                )
            await session.commit()

    async def load(self, report_id: str) -> dict[str, Any] | None:
        async with self._sm() as session:
            row = await session.get(Report, report_id)
            if row is None:
                return None
            return {
                "report_id": row.report_id,
                "encounter_id": row.encounter_id,
                "session_id": row.session_id,
                "template_key": row.template_key,
                "template_name": row.template_name,
                "language": row.language,
                "status": row.status,
                "sections": dict(row.sections or {}),
                "section_titles": dict(row.section_titles or {}),
                "warnings": list(row.warnings or []),
                "provider": row.provider,
                "model": row.model,
                "routing_reason": row.routing_reason,
                "privacy_override_applied": row.privacy_override_applied,
                "phi_redaction_applied": row.phi_redaction_applied,
                "terminology_substitutions": row.terminology_substitutions,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
                "amended_from": row.amended_from,
            }

    async def list_by_encounter(self, encounter_id: str) -> list[dict[str, Any]]:
        async with self._sm() as session:
            result = await session.execute(
                select(Report).where(Report.encounter_id == encounter_id).order_by(Report.created_at)
            )
            out = []
            for row in result.scalars():
                data = await self.load(row.report_id)  # cheap: same session cache expired; fine
                if data:
                    out.append(data)
            return out

    async def revisions(self, report_id: str) -> list[dict[str, Any]]:
        async with self._sm() as session:
            result = await session.execute(
                select(ReportRevision)
                .where(ReportRevision.report_id == report_id)
                .order_by(ReportRevision.at)
            )
            return [
                {
                    "id": r.id,
                    "action": r.action,
                    "user_id": r.user_id,
                    "detail": r.detail,
                    "at": r.at.isoformat(),
                }
                for r in result.scalars()
            ]
