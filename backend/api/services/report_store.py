"""Report lifecycle store (Phase 6, spec §10 + §5 steps 9-12).

State machine: ``draft → finalized → approved`` — every transition is an
EXPLICIT clinician action; AI output enters as a draft and can never become
a medical record on its own (spec §8).

Rules enforced here (the API layer just maps exceptions to HTTP codes):
- ``finalize``: allowed from draft; blocked while CRITICAL validation
  warnings are unacknowledged (clinician must review-and-justify first);
- ``approve``: allowed from finalized; same critical-acknowledgment gate —
  approval means "I reviewed this";
- approved reports are IMMUTABLE: section edits are rejected; corrections
  start a new amendment draft linked to the approved version;
- acknowledgments require a recorded justification (spec §18 acceptance).

Storage: in-memory LRU (Phase 7 → PostgreSQL Report/ReportSection rows +
revision table). The service API is the seam repositories will implement.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from api.services.validation import ValidationWarning

STATUS_DRAFT = "draft"
STATUS_FINALIZED = "finalized"
STATUS_APPROVED = "approved"

MAX_REPORTS = 500


class ReportStateError(RuntimeError):
    """Illegal lifecycle transition or edit — carries a stable reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _warn_id(report_id: str, index: int) -> str:
    return f"{report_id}:w{index:02d}"


@dataclass
class Acknowledgment:
    warning_id: str
    justification: str
    user_id: str | None
    at: str


@dataclass
class ReportEvent:
    """Append-only lifecycle log entry (Phase 7 persists this as revisions)."""

    action: str
    at: str
    user_id: str | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    report_id: str
    encounter_id: str
    session_id: str | None
    template_key: str | None
    template_name: str | None
    language: str
    status: str = STATUS_DRAFT
    sections: dict[str, str] = field(default_factory=dict)  # id → markdown
    section_titles: dict[str, str] = field(default_factory=dict)
    warnings: list[ValidationWarning] = field(default_factory=list)
    acknowledgments: dict[str, Acknowledgment] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    routing_reason: str = ""
    privacy_override_applied: bool = False
    phi_redaction_applied: bool = False
    terminology_substitutions: int = 0
    created_by: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    amended_from: str | None = None
    events: list[ReportEvent] = field(default_factory=list)

    # -- derived views -------------------------------------------------------

    @property
    def unacknowledged_critical(self) -> list[ValidationWarning]:
        out: list[ValidationWarning] = []
        for i, w in enumerate(self.warnings):
            if (
                w.severity == "critical"
                and w.code != "provider_fallback"
                and _warn_id(self.report_id, i) not in self.acknowledgments
            ):
                out.append(w)
        return out

    def warning_dtos(self) -> list[dict[str, Any]]:
        out = []
        for i, w in enumerate(self.warnings):
            wid = _warn_id(self.report_id, i)
            ack = self.acknowledgments.get(wid)
            out.append(
                {
                    "id": wid,
                    **w.as_dict(),
                    "acknowledged": ack is not None,
                    "acknowledgment_justification": ack.justification if ack else None,
                }
            )
        return out

    def summary(self) -> dict[str, Any]:
        critical, blocking = 0, 0
        for i, w in enumerate(self.warnings):
            if w.severity == "critical" and w.code != "provider_fallback":
                critical += 1
                if _warn_id(self.report_id, i) not in self.acknowledgments:
                    blocking += 1
        return {
            "report_id": self.report_id,
            "draft_id": self.report_id,  # compat alias (Phase 4 wire shape)
            "encounter_id": self.encounter_id,
            "session_id": self.session_id,
            "status": self.status,
            "template_key": self.template_key,
            "template_name": self.template_name,
            "language": self.language,
            "provider": self.provider,
            "model": self.model,
            "routing_reason": self.routing_reason,
            "privacy_override_applied": self.privacy_override_applied,
            "phi_redaction_applied": self.phi_redaction_applied,
            "terminology_substitutions": self.terminology_substitutions,
            "section_ids": list(self.sections),
            "critical_warnings": critical,
            "blocking_warnings": blocking,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "amended_from": self.amended_from,
        }


class ReportStore:
    def __init__(self, max_reports: int = MAX_REPORTS) -> None:
        self._lock = RLock()
        self._by_id: dict[str, Report] = {}
        self._max = max_reports

    # -- creation ---------------------------------------------------------------

    def create_draft(
        self,
        *,
        encounter_id: str,
        session_id: str | None,
        template_key: str | None,
        template_name: str | None,
        language: str,
        sections: dict[str, str],
        section_titles: dict[str, str],
        warnings: list[ValidationWarning],
        provider: str | None,
        model: str | None,
        routing_reason: str,
        privacy_override_applied: bool,
        phi_redaction_applied: bool,
        terminology_substitutions: int,
        created_by: str | None,
    ) -> Report:
        report_id = f"rpt_{uuid.uuid4().hex[:16]}"
        report = Report(
            report_id=report_id,
            encounter_id=encounter_id,
            session_id=session_id,
            template_key=template_key,
            template_name=template_name,
            language=language,
            sections=dict(sections),
            section_titles=dict(section_titles),
            warnings=list(warnings),
            provider=provider,
            model=model,
            routing_reason=routing_reason,
            privacy_override_applied=privacy_override_applied,
            phi_redaction_applied=phi_redaction_applied,
            terminology_substitutions=terminology_substitutions,
            created_by=created_by,
        )
        report.events.append(
            ReportEvent("draft_generated", _now(), created_by,
                        {"provider": provider, "warning_count": len(warnings)})
        )
        with self._lock:
            self._by_id[report_id] = report
            self._evict_locked()
        return report

    # -- reads --------------------------------------------------------------------

    def get(self, report_id: str) -> Report | None:
        with self._lock:
            return self._by_id.get(report_id)

    def list_by_encounter(self, encounter_id: str) -> list[Report]:
        with self._lock:
            return [r for r in self._by_id.values() if r.encounter_id == encounter_id]

    def count(self) -> int:
        with self._lock:
            return len(self._by_id)

    def _evict_locked(self) -> None:
        # oldest-first eviction; approved reports are pinned (medical record)
        while len(self._by_id) > self._max:
            for rid, r in self._by_id.items():
                if r.status != STATUS_APPROVED:
                    del self._by_id[rid]
                    break
            else:
                break

    # -- lifecycle -------------------------------------------------------------------

    def edit_sections(
        self, report_id: str, sections: dict[str, str], *, user_id: str | None
    ) -> Report:
        with self._lock:
            report = self._require(report_id)
            if report.status == STATUS_APPROVED:
                raise ReportStateError(
                    "approved reports are immutable — create an amendment draft instead"
                )
            for sid in sections:
                if sid not in report.sections:
                    raise ReportStateError(
                        f"unknown section '{sid}' (sections cannot be added after drafting)"
                    )
            for sid, text in sections.items():
                if not isinstance(text, str):
                    raise ReportStateError(f"section '{sid}' must be a string")
                report.sections[sid] = text
            report.updated_at = _now()
            report.events.append(
                ReportEvent(
                    "sections_edited", _now(), user_id, {"section_ids": sorted(sections)}
                )
            )
            return report

    def acknowledge(
        self, report_id: str, warning_id: str, justification: str, *, user_id: str | None
    ) -> Report:
        with self._lock:
            report = self._require(report_id)
            if report.status == STATUS_APPROVED:
                raise ReportStateError("approved reports accept no further acknowledgments")
            justification = (justification or "").strip()
            if len(justification) < 3:
                raise ReportStateError("acknowledgment requires a justification (≥3 chars)")
            valid_ids = {_warn_id(report_id, i) for i in range(len(report.warnings))}
            if warning_id not in valid_ids:
                raise ReportStateError(f"warning '{warning_id}' not found")
            report.acknowledgments[warning_id] = Acknowledgment(
                warning_id=warning_id, justification=justification[:1000], user_id=user_id, at=_now()
            )
            report.updated_at = _now()
            report.events.append(
                ReportEvent("warning_acknowledged", _now(), user_id, {"warning_id": warning_id})
            )
            return report

    def finalize(self, report_id: str, *, user_id: str | None) -> Report:
        with self._lock:
            report = self._require(report_id)
            if report.status != STATUS_DRAFT:
                raise ReportStateError(
                    f"cannot finalize a report in status '{report.status}' (draft required)"
                )
            blocking = report.unacknowledged_critical
            if blocking:
                raise ReportStateError(
                    "unacknowledged critical warnings block finalization — review and "
                    "acknowledge each with a justification first "
                    f"({len(blocking)} remaining)"
                )
            report.status = STATUS_FINALIZED
            report.updated_at = _now()
            report.events.append(ReportEvent("finalized", _now(), user_id, {}))
            return report

    def approve(self, report_id: str, *, user_id: str | None) -> Report:
        with self._lock:
            report = self._require(report_id)
            if report.status != STATUS_FINALIZED:
                raise ReportStateError(
                    f"cannot approve a report in status '{report.status}' "
                    "(finalize first — approval is a separate explicit action)"
                )
            blocking = report.unacknowledged_critical
            if blocking:
                raise ReportStateError(
                    "unacknowledged critical warnings block approval "
                    f"({len(blocking)} remaining)"
                )
            report.status = STATUS_APPROVED
            report.updated_at = _now()
            report.events.append(ReportEvent("approved", _now(), user_id, {}))
            return report

    def reopen(self, report_id: str, *, user_id: str | None) -> Report:
        """finalized → draft (explicit clinician pull-back before approval)."""
        with self._lock:
            report = self._require(report_id)
            if report.status != STATUS_FINALIZED:
                raise ReportStateError(f"cannot reopen a report in status '{report.status}'")
            report.status = STATUS_DRAFT
            report.updated_at = _now()
            report.events.append(ReportEvent("reopened", _now(), user_id, {}))
            return report

    def amend(self, report_id: str, *, user_id: str | None) -> Report:
        """Approved → new linked draft (the approved version stays untouched)."""
        with self._lock:
            approved = self._require(report_id)
            if approved.status != STATUS_APPROVED:
                raise ReportStateError("amendment starts from an approved report")
            new_id = f"rpt_{uuid.uuid4().hex[:16]}"
            amendment = Report(
                report_id=new_id,
                encounter_id=approved.encounter_id,
                session_id=approved.session_id,
                template_key=approved.template_key,
                template_name=approved.template_name,
                language=approved.language,
                status=STATUS_DRAFT,
                sections=dict(approved.sections),
                section_titles=dict(approved.section_titles),
                warnings=[],  # clinician-authored edits start clean; re-validate on next draft
                provider=approved.provider,
                model=approved.model,
                routing_reason="amendment",
                privacy_override_applied=approved.privacy_override_applied,
                phi_redaction_applied=approved.phi_redaction_applied,
                created_by=user_id,
                amended_from=approved.report_id,
            )
            amendment.events.append(
                ReportEvent("amendment_created", _now(), user_id,
                            {"from": approved.report_id})
            )
            self._by_id[new_id] = amendment
            approved.events.append(ReportEvent("amended", _now(), user_id, {"to": new_id}))
            return amendment

    def _require(self, report_id: str) -> Report:
        report = self._by_id.get(report_id)
        if report is None:
            raise ReportStateError(f"report '{report_id}' not found or expired")
        return report
