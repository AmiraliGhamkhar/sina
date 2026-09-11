"""Transcript retrieval + clinician editing (Phase 2, in-memory store).

GET    /api/v1/transcripts/{session_id}
PATCH  /api/v1/transcripts/{session_id}/segments/{segment_id}

Session scoping/enforcement, persistence and revision-conflict handling land
with PostgreSQL + JWT user identity in Phase 7; the response contract here is
already final.
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
