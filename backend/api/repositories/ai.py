"""AI provider + AI request repositories (Phase 7)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.models.orm import AIProvider, AIRequest


class AIProviderRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def upsert_metadata(
        self, *, kind: str, name: str, privacy_class: str, capabilities: dict, configured: bool
    ) -> None:
        async with self._sm() as session:
            pk = f"{kind}:{name}"
            row = await session.get(AIProvider, pk)
            if row is None:
                row = AIProvider(id=pk, kind=kind, name=name)
                session.add(row)
            row.privacy_class = privacy_class
            row.capabilities = capabilities
            row.configured = configured
            await session.commit()

    async def set_secret(self, kind: str, name: str, ciphertext: str | None) -> bool:
        async with self._sm() as session:
            row = await session.get(AIProvider, f"{kind}:{name}")
            if row is None:
                return False
            row.secret_ciphertext = ciphertext
            row.configured = ciphertext is not None or row.configured
            await session.commit()
            return True

    async def get_secret(self, kind: str, name: str) -> str | None:
        """Ciphertext only — decrypt at the single consumption point (vault)."""
        async with self._sm() as session:
            row = await session.get(AIProvider, f"{kind}:{name}")
            return row.secret_ciphertext if row else None

    async def list(self) -> list[dict[str, Any]]:
        """Metadata for the admin UI — never includes secret material."""
        async with self._sm() as session:
            result = await session.execute(select(AIProvider).order_by(AIProvider.id))
            return [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "name": r.name,
                    "privacy_class": r.privacy_class,
                    "capabilities": r.capabilities,
                    "configured": r.configured,
                    "has_secret": r.secret_ciphertext is not None,
                }
                for r in result.scalars()
            ]


class AIRequestRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def record(self, **fields: Any) -> str:
        async with self._sm() as session:
            row = AIRequest(
                user_id=fields.get("user_id"),
                kind=fields["kind"],
                task=fields["task"],
                provider=fields["provider"],
                status=fields.get("status", "ok"),
                routed_provider=fields.get("routed_provider"),
                privacy_override=bool(fields.get("privacy_override", False)),
                latency_ms=int(fields.get("latency_ms") or 0),
                prompt_tokens=fields.get("prompt_tokens"),
                completion_tokens=fields.get("completion_tokens"),
                total_tokens=fields.get("total_tokens"),
                error_code=fields.get("error_code"),
                encounter_id=fields.get("encounter_id"),
                session_id=fields.get("session_id"),
            )
            session.add(row)
            await session.commit()
            return row.id

    async def recent(self, *, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        async with self._sm() as session:
            stmt = select(AIRequest).order_by(AIRequest.created_at.desc()).limit(limit)
            if kind:
                stmt = stmt.where(AIRequest.kind == kind)
            result = await session.execute(stmt)
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                    "user_id": r.user_id,
                    "kind": r.kind,
                    "task": r.task,
                    "provider": r.provider,
                    "status": r.status,
                    "latency_ms": r.latency_ms,
                    "total_tokens": r.total_tokens,
                    "encounter_id": r.encounter_id,
                    "session_id": r.session_id,
                }
                for r in result.scalars()
            ]

    async def token_totals_since(self, since: datetime) -> dict[str, int]:
        """Aggregate token usage for the cost ledger backfill."""
        from sqlalchemy import func

        async with self._sm() as session:
            result = await session.execute(
                select(
                    AIRequest.provider,
                    func.coalesce(func.sum(AIRequest.total_tokens), 0),
                )
                .where(AIRequest.created_at >= since)
                .group_by(AIRequest.provider)
            )
            return {provider: int(total) for provider, total in result.all()}
