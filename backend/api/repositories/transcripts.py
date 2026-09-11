"""Transcript persistence repository (Phase 7).

The in-memory TranscriptStore remains the live-session buffer (sync, used by
the hub at audio cadence); this repository is the durable sink:
- ``flush`` upserts a whole session (idempotent — safe on retries/disconnect)
- ``update_segment`` applies clinician edits / command effects
- ``snapshot`` rehydrates the REST view after the memory LRU evicts
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.models.orm import Transcript, TranscriptSegment


class TranscriptRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def flush(
        self,
        *,
        session_id: str,
        user_id: str | None,
        provider: str | None,
        language: str | None,
        status: str,
        started_at: float | None,
        ended_at: float | None,
        audio_duration_ms: int,
        encounter_id: str | None,
        segments: list[dict[str, Any]],
    ) -> None:
        """Idempotent whole-session upsert (delete segments, rewrite)."""
        async with self._sm() as session:
            existing = await session.get(Transcript, session_id)
            if existing is None:
                existing = Transcript(session_id=session_id, encounter_id=encounter_id)
                session.add(existing)
            existing.user_id = user_id
            existing.provider = provider
            existing.language = language
            existing.status = status
            existing.audio_duration_ms = audio_duration_ms
            existing.encounter_id = encounter_id
            existing.started_at = _dt(started_at) or datetime.now(UTC)
            existing.ended_at = _dt(ended_at)
            await session.flush()
            await session.execute(
                delete(TranscriptSegment).where(TranscriptSegment.transcript_id == session_id)
            )
            for position, seg in enumerate(segments):
                session.add(
                    TranscriptSegment(
                        transcript_id=session_id,
                        segment_id=str(seg["segment_id"]),
                        position=position,
                        text=str(seg.get("text") or ""),
                        start_ms=int(seg.get("start_ms") or 0),
                        end_ms=int(seg.get("end_ms") or 0),
                        language=seg.get("language"),
                        confidence=seg.get("confidence"),
                        edited=bool(seg.get("edited")),
                        revision=int(seg.get("revision") or 0),
                        kind=str(seg.get("kind") or "dictated"),
                        meta=seg.get("meta"),
                    )
                )
            await session.commit()

    async def update_segment(
        self, session_id: str, segment_id: str, *, text: str, revision: int
    ) -> bool:
        from sqlalchemy import update

        async with self._sm() as session:
            result = await session.execute(
                update(TranscriptSegment)
                .where(
                    TranscriptSegment.transcript_id == session_id,
                    TranscriptSegment.segment_id == segment_id,
                )
                .values(text=text, edited=True, revision=revision, updated_at=datetime.now(UTC))
            )
            await session.commit()
            return bool(result.rowcount)

    async def get(self, session_id: str) -> Transcript | None:
        async with self._sm() as session:
            result = await session.execute(
                select(Transcript)
                .where(Transcript.session_id == session_id)
                .options()  # segments loaded explicitly below
            )
            return result.scalar_one_or_none()

    async def snapshot(self, session_id: str) -> dict[str, Any] | None:
        """REST-shaped view from durable rows (GET fallback after LRU)."""
        async with self._sm() as session:
            transcript = (
                await session.execute(select(Transcript).where(Transcript.session_id == session_id))
            ).scalar_one_or_none()
            if transcript is None:
                return None
            segs = (
                await session.execute(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.transcript_id == session_id)
                    .order_by(TranscriptSegment.position)
                )
            ).scalars()
            segments = [
                {
                    "segment_id": s.segment_id,
                    "text": s.text,
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "language": s.language,
                    "confidence": s.confidence,
                    "edited": s.edited,
                    "revision": s.revision,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    "kind": s.kind,
                    "meta": s.meta,
                }
                for s in segs
            ]
            return {
                "session_id": transcript.session_id,
                "provider": transcript.provider,
                "language": transcript.language,
                "status": transcript.status,
                "segment_count": len(segments),
                "audio_duration_ms": transcript.audio_duration_ms,
                "segments": segments,
            }

    async def link_encounter(self, session_id: str, encounter_id: str | None) -> None:
        from sqlalchemy import update

        async with self._sm() as session:
            await session.execute(
                update(Transcript)
                .where(Transcript.session_id == session_id)
                .values(encounter_id=encounter_id)
            )
            await session.commit()


def _dt(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, UTC)
