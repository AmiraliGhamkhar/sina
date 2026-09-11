"""Report API schemas (Phase 4 draft wire + Phase 6 lifecycle).

Lifecycle: draft → finalized → approved, every step an explicit clinician
action; critical validation warnings must be acknowledged with a recorded
justification before finalize/approve (spec §5/§9/§10).
"""
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
    #: inline transcript (Phase 4 wire — still supported)
    transcript: str | None = Field(default=None, min_length=1, max_length=80_000)
    #: OR a live/finished dictation session — the server assembles the
    #: stored segments (markers + terminology-normalized view for the prompt)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    #: server-side template key wins over the inline spec
    template_key: str | None = Field(default=None, max_length=64)
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


class WarningOut(BaseModel):
    id: str
    code: str
    severity: str = "warning"
    message: str
    section_id: str | None = None
    evidence: str | None = None
    acknowledged: bool = False
    acknowledgment_justification: str | None = None


class UsageOut(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ReportDraftResponse(BaseModel):
    report_id: str
    #: compat alias for the Phase 4 wire shape (same value)
    draft_id: str
    encounter_id: str
    session_id: str | None = None
    status: str = "draft"
    provider: str
    model: str | None = None
    routing_reason: str = ""
    privacy_override_applied: bool = False
    #: True when identifiers were scrubbed before a CLOUD provider was used
    phi_redaction_applied: bool = False
    language: str
    template_key: str | None = None
    template_name: str | None = None
    sections: list[DraftSectionOut]
    missing_sections: list[str] = Field(default_factory=list)
    warnings: list[WarningOut] = Field(default_factory=list)
    usage: UsageOut | None = None
    latency_ms: int = 0
    #: number of terminology substitutions applied to the prompt view
    terminology_substitutions: int = 0
    transcript_source: Literal["inline", "session"] = "inline"


class ReportSectionEditRequest(BaseModel):
    sections: dict[str, str] = Field(min_length=1, max_length=20)
    #: sections are plain markdown; the dict maps section id → new text


class AcknowledgeRequest(BaseModel):
    warning_id: str = Field(min_length=1, max_length=64)
    #: recorded justification — required (spec §18 acceptance criteria)
    justification: str = Field(min_length=3, max_length=1000)


class ReportOut(BaseModel):
    report_id: str
    draft_id: str
    encounter_id: str
    session_id: str | None = None
    status: str
    template_key: str | None = None
    template_name: str | None = None
    language: str
    provider: str | None = None
    model: str | None = None
    routing_reason: str = ""
    privacy_override_applied: bool = False
    phi_redaction_applied: bool = False
    terminology_substitutions: int = 0
    sections: list[DraftSectionOut]
    warnings: list[WarningOut]
    critical_warnings: int = 0
    blocking_warnings: int = 0
    created_by: str | None = None
    created_at: str = ""
    updated_at: str = ""
    amended_from: str | None = None


class ReportListOut(BaseModel):
    reports: list[ReportOut]
    total: int


class LifecycleAck(BaseModel):
    report_id: str
    status: str
    message: str = ""
