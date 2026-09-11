"""Transcript retrieval + clinician editing (Phase 2, in-memory store).

GET    /api/v1/transcripts/{session_id}
PATCH  /api/v1/transcripts/{session_id}/segments/{segment_id}

Phase 7 persistence: the in-memory store stays the live-session buffer;
GET falls back to the durable ``transcripts`` rows after the memory LRU
evicts, and PATCH edits write through to the database when configured.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.auth.deps import OptionalPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.transcripts import SegmentEditRequest, TranscriptResponse

router = APIRouter(prefix="/transcripts", tags=["transcripts"])


def _store(request: Request):
    return request.app.state.transcript_store


@router.get("/{session_id}", response_model=TranscriptResponse)
async def get_transcript(request: Request, session_id: str, principal: OptionalPrincipal) -> TranscriptResponse:
    snapshot = _store(request).snapshot(session_id)
    if snapshot is None:
        repo = getattr(request.app.state, "transcript_repo", None)
        if repo is not None:
            snapshot = await repo.snapshot(session_id)  # durable fallback (P7)
    if snapshot is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"transcript '{session_id}' not found or expired")
    return TranscriptResponse(**snapshot)


@router.patch("/{session_id}/segments/{segment_id}", response_model=dict)
async def edit_segment(
    request: Request,
    session_id: str,
    segment_id: str,
    body: SegmentEditRequest,
    principal: OptionalPrincipal,
) -> dict:
    store = _store(request)
    updated = store.edit_segment(session_id, segment_id, body.text)
    if updated is None:
        snapshot = store.snapshot(session_id)
        if snapshot is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, f"transcript '{session_id}' not found or expired")
        raise ApiError(404, ErrorCode.NOT_FOUND, f"segment '{segment_id}' not found")
    # durable write-through (P7): the memory store is authoritative while the
    # session is live; the DB copy serves post-eviction reads
    repo = getattr(request.app.state, "transcript_repo", None)
    if repo is not None:
        try:
            await repo.update_segment(
                session_id, segment_id, text=updated.text, revision=updated.revision
            )
        except Exception:  # noqa: BLE001 — persistence lag never blocks a clinician edit
            request.app.state.metrics.incr("transcript_db_write_failures")
    # audit: who changed what kind of field — content stays out (deny-list)
    request.app.state.audit.emit(
        "transcript_segment_edited",
        session_id=session_id,
        segment_id=segment_id,
        revision=updated.revision,
        user_id=principal.user_id if principal else None,
        text_length=len(body.text),
    )
    return {
        "segment_id": updated.segment_id,
        "revision": updated.revision,
        "edited": updated.edited,
        "updated_at": updated.updated_at,
    }
