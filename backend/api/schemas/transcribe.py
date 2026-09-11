"""Batch transcription API schemas (Phase 3)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BatchSegment(BaseModel):
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    language: str | None = None


class TranscribeBatchResponse(BaseModel):
    request_id: str
    provider: str
    #: "local" | "cloud" | "hybrid" | "auto" — what was actually honored
    mode: str
    #: True when privacy forced a local pick despite a cloud preference
    privacy_override_applied: bool = False
    language: str | None = None
    audio_duration_ms: int
    segment_count: int
    #: all segments joined with blank lines — convenience for uploads without
    #: per-segment UI needs
    text: str = ""
    latency_ms: int = 0
    segments: list[BatchSegment] = Field(default_factory=list)
