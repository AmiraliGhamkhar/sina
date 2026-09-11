"""Transcript REST schemas (Phase 2 in-memory store view).

Phase 7 moves the same shapes onto PostgreSQL rows; keep the wire format
stable now so the WPF editor never changes.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class TranscriptSegmentDto(BaseModel):
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    language: str | None = None
    confidence: float | None = None
    edited: bool = False
    revision: int = 0
    updated_at: str | None = None
    #: Phase 6 — dictated vs voice-command markers (paragraph/section/…)
    kind: str = "dictated"
    #: marker payload, e.g. {"section_title": "طرح درمان"} (None for dictation)
    meta: dict | None = None


class TranscriptResponse(BaseModel):
    session_id: str
    provider: str | None = None
    language: str | None = None
    status: str = "open"  # open | completed
    segment_count: int = 0
    audio_duration_ms: int = 0
    segments: list[TranscriptSegmentDto] = Field(default_factory=list)


class SegmentEditRequest(BaseModel):
    """Clinician edit (spec §5 step 7). Empty string deletes text but keeps
    the segment; never used to *add* segments (commands/reports do that)."""

    text: str = Field(..., max_length=8000)
