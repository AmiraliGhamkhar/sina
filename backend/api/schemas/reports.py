"""Report-draft API schemas (Phase 4). Persistence (draft rows, revisions,
finalize/approve) lands with PostgreSQL in Phase 7; the wire contract here is
already final."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PatientContextIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    age: str | None = Field(default=None, max_length=10)
    sex: str | None = Field(default=None, max_length=20)
    mrn: str | None = Field(default=None, max_length=40)
    encounter_date: str | None = Field(default=None, max_length=40)


class SectionSpecIn(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    title: str = Field(default="", max_length=160)
    instruction: str = Field(default="", max_length=500)


class ReportTemplateIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    sections: list[SectionSpecIn] | None = Field(default=None, max_length=20)


class ReportDraftRequest(BaseModel):
    #: session transcripts are in-memory until Phase 7; the draft endpoint
    #: takes the finalized text directly (the client owns the session)
    transcript: str = Field(min_length=1, max_length=80_000)
    template: ReportTemplateIn | None = None
    patient_context: PatientContextIn | None = None
    language: str = Field(default="fa-en", max_length=16)
    mode: Literal["local", "cloud", "hybrid", "auto"] | None = None
    privacy_required: bool | None = None
    provider: str | None = Field(default=None, max_length=64)


class DraftSectionOut(BaseModel):
    id: str
    title: str
    markdown: str
    missing: bool = False


class GroundingWarningOut(BaseModel):
    code: str
    message: str
    section_id: str | None = None


class UsageOut(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ReportDraftResponse(BaseModel):
    draft_id: str
    encounter_id: str
    provider: str
    model: str | None = None
    routing_reason: str = ""
    privacy_override_applied: bool = False
    #: True when identifiers were scrubbed before a CLOUD provider was used
    phi_redaction_applied: bool = False
    language: str
    template_name: str | None = None
    sections: list[DraftSectionOut]
    missing_sections: list[str] = Field(default_factory=list)
    warnings: list[GroundingWarningOut] = Field(default_factory=list)
    usage: UsageOut | None = None
    latency_ms: int = 0
