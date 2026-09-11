"""Report template service (Phase 6, spec §10).

Data-driven templates — radiology, ultrasound, CT, MRI, SOAP, general
clinical note are seeded as DATA (never hard-coded in UI); custom templates
arrive via CRUD or LLM-assisted extraction from an example note (Phlox
concept, adapted: extraction returns an *unsaved draft* the clinician
reviews — nothing auto-persists).

Storage: in-memory (Phase 7 swaps this for ReportTemplate rows in
PostgreSQL; the service API is the stable seam). Built-ins are immutable:
they can be listed and used but not modified or deleted — clinicians fork
them into custom templates instead.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

_SECTION_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

CATEGORIES = ("general", "soap", "radiology", "ultrasound", "ct", "mri", "custom")
FORMAT_STYLES = ("narrative", "bullets", "numbered", "lab_values", "heading_with_bullets")


@dataclass(frozen=True)
class TemplateSection:
    id: str
    title: str
    instruction: str = ""
    required: bool = False
    format_style: str = "narrative"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "instruction": self.instruction,
            "required": self.required,
            "format_style": self.format_style,
        }


@dataclass
class ReportTemplate:
    key: str
    name: str
    category: str
    description: str = ""
    sections: list[TemplateSection] = field(default_factory=list)
    builtin: bool = False
    version: int = 1
    deleted: bool = False  # soft delete (custom only)
    created_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "sections": [s.as_dict() for s in self.sections],
            "builtin": self.builtin,
            "version": self.version,
            "deleted": self.deleted,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _s(
    sid: str, title: str, instruction: str = "", required: bool = False, style: str = "narrative"
) -> TemplateSection:
    return TemplateSection(id=sid, title=title, instruction=instruction, required=required,
                           format_style=style)


#: ---- built-ins (seeded as data; mirrored into note_prompt defaults) --------
BUILTIN_TEMPLATES: tuple[ReportTemplate, ...] = (
    ReportTemplate(
        key="general-clinical-note",
        name="یادداشت بالینی عمومی / General Clinical Note",
        category="general",
        description="Default mixed-language clinical note.",
        builtin=True,
        created_at="2025-01-01T00:00:00Z",
        sections=[
            _s("chief_complaint", "شکایت اصلی", "Why the patient presented", True),
            _s("history", "تاریخچه", "History of present illness"),
            _s("exam", "معاینه فیزیکی", "Objective findings only"),
            _s("assessment", "ارزیابی", "Impression / differential"),
            _s("plan", "طرح درمان", "Medications, doses, follow-up", True, "numbered"),
        ],
    ),
    ReportTemplate(
        key="soap-note",
        name="SOAP",
        category="soap",
        description="Subjective / Objective / Assessment / Plan.",
        builtin=True,
        created_at="2025-01-01T00:00:00Z",
        sections=[
            _s("subjective", "Subjective", "Patient-reported symptoms, onset, pertinent negatives",
               False, "bullets"),
            _s("objective", "Objective", "Exam findings, vitals, investigation results",
               False, "bullets"),
            _s("assessment", "Assessment", "Clinical impression / working differential"),
            _s("plan", "Plan", "Medications with doses, orders, follow-up", True, "numbered"),
        ],
    ),
    ReportTemplate(
        key="radiology-report",
        name="گزارش رادیولوژی / Radiology Report",
        category="radiology",
        description="Standard radiography report.",
        builtin=True,
        created_at="2025-01-01T00:00:00Z",
        sections=[
            _s("clinical_history", "شرح حال بالینی", "Indication / clinical question", True),
            _s("technique", "تکنیک", "Modality, views, technique"),
            _s("findings", "یافته‌ها", "Observed findings only — verbatim measurements", True),
            _s("impression", "برداشت", "Radiologist impression grounded in findings", True),
            _s("recommendations", "توصیه‌ها", "Further workup if stated", False, "bullets"),
        ],
    ),
    ReportTemplate(
        key="ultrasound-report",
        name="گزارش سونوگرافی / Ultrasound Report",
        category="ultrasound",
        description="General ultrasound report.",
        builtin=True,
        created_at="2025-01-01T00:00:00Z",
        sections=[
            _s("clinical_history", "شرح حال بالینی", "Indication", True),
            _s("technique", "تکنیک", "Probe / region examined"),
            _s("findings", "یافته‌ها", "Observed findings with measurements", True),
            _s("impression", "برداشت", "Impression grounded in findings", True),
        ],
    ),
    ReportTemplate(
        key="ct-report",
        name="گزارش سی‌تی / CT Report",
        category="ct",
        description="Computed tomography report (with/without contrast).",
        builtin=True,
        created_at="2025-01-01T00:00:00Z",
        sections=[
            _s("clinical_history", "شرح حال بالینی", "Indication", True),
            _s("technique", "تکنیک", "Protocol, contrast phase, coverage"),
            _s("findings", "یافته‌ها", "Systematic findings — verbatim measurements", True),
            _s("impression", "برداشت", "Impression grounded in findings", True),
            _s("recommendations", "توصیه‌ها", "Further workup if stated", False, "bullets"),
        ],
    ),
    ReportTemplate(
        key="mri-report",
        name="گزارش ام‌آرآی / MRI Report",
        category="mri",
        description="Magnetic resonance imaging report.",
        builtin=True,
        created_at="2025-01-01T00:00:00Z",
        sections=[
            _s("clinical_history", "شرح حال بالینی", "Indication", True),
            _s("technique", "تکنیک", "Sequences, region, contrast"),
            _s("findings", "یافته‌ها", "Systematic findings — verbatim measurements", True),
            _s("impression", "برداشت", "Impression grounded in findings", True),
            _s("recommendations", "توصیه‌ها", "Further workup if stated", False, "bullets"),
        ],
    ),
)


class TemplateError(ValueError):
    """Validation failure with a stable message (never echoes note content)."""


class TemplateService:
    def __init__(self, builtins: tuple[ReportTemplate, ...] = BUILTIN_TEMPLATES) -> None:
        self._by_key: dict[str, ReportTemplate] = {t.key: t for t in builtins}

    # -- reads ---------------------------------------------------------------

    def list(self, *, include_deleted: bool = False) -> list[ReportTemplate]:
        return [
            t
            for t in self._by_key.values()
            if include_deleted or not t.deleted
        ]

    def get(self, key: str) -> ReportTemplate | None:
        t = self._by_key.get(key)
        return t if t and not t.deleted else None

    # -- writes ----------------------------------------------------------------

    def create(
        self,
        *,
        key: str | None,
        name: str,
        category: str = "custom",
        description: str = "",
        sections: list[dict[str, Any]],
    ) -> ReportTemplate:
        key = (key or "").strip().lower().replace(" ", "-")
        if not _KEY_RE.match(key or ""):
            raise TemplateError(
                "key must be 2-64 chars of a-z 0-9 '-' '_' (start letter/digit)"
            )
        if key in self._by_key and not self._by_key[key].deleted:
            raise TemplateError(f"template key '{key}' already exists")
        if category not in CATEGORIES:
            raise TemplateError(f"category must be one of {CATEGORIES}")
        parsed = self._validate_sections(sections)
        template = ReportTemplate(
            key=key,
            name=name.strip()[:120],
            category=category,
            description=description.strip()[:500],
            sections=parsed,
            builtin=False,
            version=1,
            created_at=_now(),
            updated_at=_now(),
        )
        self._by_key[key] = template
        return template

    def update(self, key: str, *, name: str | None = None, description: str | None = None,
               sections: list[dict[str, Any]] | None = None) -> ReportTemplate:
        template = self._require_custom(key)
        if name is not None:
            template.name = name.strip()[:120]
        if description is not None:
            template.description = description.strip()[:500]
        if sections is not None:
            template.sections = self._validate_sections(sections)
        template.version += 1
        template.updated_at = _now()
        return template

    def delete(self, key: str) -> None:
        template = self._require_custom(key)
        template.deleted = True  # soft delete — audit trail survives
        template.updated_at = _now()

    def fork(self, key: str, *, new_key: str, new_name: str | None = None) -> ReportTemplate:
        """Copy a built-in (or custom) template into an editable custom one."""
        source = self.get(key)
        if source is None:
            raise TemplateError(f"template '{key}' not found")
        return self.create(
            key=new_key,
            name=new_name or f"{source.name} (copy)",
            category="custom",
            description=source.description,
            sections=[s.as_dict() for s in source.sections],
        )

    # -- validation -------------------------------------------------------------

    @staticmethod
    def _validate_sections(sections: list[dict[str, Any]]) -> list[TemplateSection]:
        if not sections or len(sections) > 20:
            raise TemplateError("1-20 sections required")
        seen: set[str] = set()
        out: list[TemplateSection] = []
        for raw in sections:
            sid = str(raw.get("id") or "").strip()
            if not _SECTION_ID_RE.match(sid):
                raise TemplateError(f"section id '{sid or '(empty)'}' must be [a-z0-9_] 1-64")
            if sid in seen:
                raise TemplateError(f"duplicate section id '{sid}'")
            seen.add(sid)
            style = str(raw.get("format_style") or "narrative")
            if style not in FORMAT_STYLES:
                raise TemplateError(f"format_style must be one of {FORMAT_STYLES}")
            out.append(
                TemplateSection(
                    id=sid,
                    title=str(raw.get("title") or sid)[:160],
                    instruction=str(raw.get("instruction") or "")[:500],
                    required=bool(raw.get("required")),
                    format_style=style,
                )
            )
        return out

    def _require_custom(self, key: str) -> ReportTemplate:
        template = self._by_key.get(key)
        if template is None or template.deleted:
            raise TemplateError(f"template '{key}' not found")
        if template.builtin:
            raise TemplateError(f"built-in template '{key}' is immutable — fork it instead")
        return template
