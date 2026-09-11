"""Grounded note-prompt builder + strict-JSON handling (Phase 4).

Grounding contract (docs/ARCHITECTURE.md §7): the model sees ONLY the
transcript, the patient context block, and the template section specs — never
secrets, never other encounters. The prompt is machine-delimited with
``<<<BLOCK>>>`` sentinels so (a) the mock provider can stay honest and (b)
the fidelity checker knows which text came from where.

Three pieces live here so the report route stays thin:
1. :func:`build_messages` — the grounded prompt,
2. :func:`redact_phi` — best-effort PHI scrub before ANY cloud provider
   (privacy-required encounters never route cloud regardless),
3. :func:`reconcile` + :func:`grounding_warnings` — strict JSON parse (with
   fence tolerance), section alignment, and the *light* fidelity check
   (numbers / laterality / negation) that satisfies the "preserved or
   flagged" acceptance bar. The full clinical validator lands in Phase 6.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

MISSING_TOKEN = "[[MISSING]]"

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

DEFAULT_SECTIONS: tuple[dict[str, str], ...] = (
    {"id": "chief_complaint", "title": "شکایت اصلی", "instruction": "Why the patient presented"},
    {"id": "history", "title": "تاریخچه", "instruction": "History of present illness"},
    {"id": "exam", "title": "معاینه فیزیکی", "instruction": "Objective findings only"},
    {"id": "assessment", "title": "ارزیابی", "instruction": "Impression / differential"},
    {"id": "plan", "title": "طرح درمان", "instruction": "Medications, doses, follow-up"},
)

_SYSTEM = """You are a clinical documentation assistant for Persian/English mixed dictation.
You ONLY restructure the provided TRANSCRIPT into the requested note sections.
Hard rules — violating any is a critical failure:
1. Never add facts: no diagnosis, dose, number, test, laterality or timing that
   is not literally present in the TRANSCRIPT. Patient CONTEXT is for headings
   only, never as clinical evidence.
2. If a section has no supporting content in the TRANSCRIPT, output the exact
   token {missing} as its whole value. Never guess, never write "normal".
3. Copy numbers, units, drug names, and English medical terms VERBATIM
   (e.g. "40 mg", "ECG", "furosemide"). Do not translate, normalize, or drop
   negation words ("بدون", "ندارد", "no", "without") or laterality ("چپ",
   "راست", "left", "right").
4. Output STRICT JSON only: {{"sections": {{"<id>": "<markdown>", ...}}}} with
   EXACTLY these section ids in this order: {ids}. No other keys, no prose, no
   code fences."""


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    instruction: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SectionSpec:
        return cls(
            id=str(raw.get("id") or "").strip(),
            title=str(raw.get("title") or "").strip(),
            instruction=str(raw.get("instruction") or "").strip(),
        )


def resolve_sections(raw_sections: Sequence[Mapping[str, Any]] | None) -> tuple[SectionSpec, ...]:
    specs = [SectionSpec.from_mapping(r) for r in (raw_sections or DEFAULT_SECTIONS)]
    specs = [s for s in specs if s.id]
    return tuple(specs) or tuple(SectionSpec.from_mapping(d) for d in DEFAULT_SECTIONS)


def build_messages(
    *,
    transcript: str,
    sections: Sequence[SectionSpec],
    patient_context: Mapping[str, Any] | None = None,
    language: str = "fa-en",
) -> list[Any]:
    from ai.base import LLMMessage

    ids = ", ".join(s.id for s in sections)
    system = _SYSTEM.format(missing=MISSING_TOKEN, ids=ids)
    ctx_lines = []
    for key in ("name", "age", "sex", "mrn", "encounter_date"):
        value = (patient_context or {}).get(key)
        if value not in (None, ""):
            ctx_lines.append(f"{key}: {value}")
    section_json = json.dumps(
        [{"id": s.id, "title": s.title, "instruction": s.instruction} for s in sections],
        ensure_ascii=False,
    )
    user = (
        "<<<TRANSCRIPT>>>\n"
        + transcript.strip()
        + "\n<<<END TRANSCRIPT>>>\n"
        "<<<CONTEXT>>>\n"
        + ("\n".join(ctx_lines) or "(none)")
        + "\n<<<END CONTEXT>>>\n"
        "<<<SECTIONS>>>\n"
        + section_json
        + "\n<<<END SECTIONS>>>\n"
        "Return the strict JSON object now."
    )
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


# ---- PHI redaction (best effort, pre-cloud) --------------------------------


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Iranian national id: 10 consecutive digits (optionally dashed)
    (re.compile(r"(?<!\d)\d{10}(?!\d)"), "[REDACTED:national-id]"),
    (re.compile(r"(?<!\d)\d{3}-?\d{7}(?!\d)"), "[REDACTED:national-id]"),
    # mobile numbers (09xxxxxxxxx, +98 9xxxxxxxxx)
    (re.compile(r"(?<!\d)(?:\+98|0098|0)?9\d{9}(?!\d)"), "[REDACTED:phone]"),
    # mobile numbers in Persian digits (۰۹… / ۹…) — dictation is Persian-first;
    # \d alone doesn't help because the literals 0/9 above are ASCII-only
    (re.compile(r"(?<![0-9۰-۹])[۰]?۹[۰-۹]{9}(?![0-9۰-۹])"), "[REDACTED:phone]"),
    # long digit runs (MRN/account style)
    (re.compile(r"(?<!\d)\d{8,}(?!\d)"), "[REDACTED:id]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[REDACTED:email]"),
)


def redact_phi(text: str) -> str:
    """Scrub identifier-shaped strings. Doses/clinical small numbers survive
    by design (they're short); this is a pre-cloud courtesy layer, NOT a
    de-identification guarantee (the real guarantee is the privacy wall:
    privacy_required routes LOCAL only)."""
    out = text
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out


def looks_like_phi_heavy(text: str) -> bool:
    return "[REDACTED" in text


# ---- strict JSON + reconciliation ------------------------------------------------


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_model_json(raw: str) -> Mapping[str, Any]:
    cleaned = _FENCE.sub("", raw.strip())
    # tolerate leading prose by grabbing the outermost object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, Mapping):
        raise ValueError("model output is not a JSON object")
    return data


def reconcile(data: Mapping[str, Any], sections: Sequence[SectionSpec]) -> dict[str, str]:
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, Mapping):
        raise ValueError("missing 'sections' object in model output")
    out: dict[str, str] = {}
    for spec in sections:
        value = raw_sections.get(spec.id)
        if not isinstance(value, str) or not value.strip():
            out[spec.id] = MISSING_TOKEN
        else:
            out[spec.id] = value.strip()
    return out


def repair_messages(
    original: Sequence[Any], bad_output: str, sections: Sequence[SectionSpec]
) -> list[Any]:
    from ai.base import LLMMessage

    ids = ", ".join(s.id for s in sections)
    return [
        *original,
        LLMMessage(role="assistant", content=bad_output[:4000]),
        LLMMessage(
            role="user",
            content=(
                "That was not valid strict JSON. Respond again with ONLY this "
                "object shape: {\"sections\": {" + ", ".join(f'"{s.id}": "…"' for s in sections) + "}} "
                f"— section ids exactly [{ids}], markdown strings as values, "
                "no fences, no commentary."
            ),
        ),
    ]


# ---- light fidelity check ---------------------------------------------------------

_LATERALITY = ("چپ", "راست", "left", "right")
_NEGATIONS = ("بدون", "ندارد", "نشد", "no ", "without", "not ", "denies", "negative")
_NUMBER = re.compile(r"\d+(?:[\./,]\d+)?\s?(?:mg|mcg|g|ml|l|mm|cm|%|cc|unit|units|bar|bpm|°|day|days|hour|hours)?", re.IGNORECASE)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    return t.translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS).replace("ي", "ی").replace("ك", "ک")


def _numbers(text: str) -> set[str]:
    norm = _norm(text)
    return {re.sub(r"\s+", " ", m.group()).strip().lower() for m in _NUMBER.finditer(norm)}


@dataclass
class FidelityReport:
    warnings: list[dict[str, str]] = field(default_factory=list)
    numbers_verified: int = 0
    missing_sections: list[str] = field(default_factory=list)


def grounding_warnings(
    transcript: str, sections: Mapping[str, str]
) -> FidelityReport:
    """Flag draft content the transcript cannot substantiate. Numbers are
    set-comparisons (per section), laterality/negation counts are coarse
    tripwires — advisory only, never auto-edits (spec §9)."""
    report = FidelityReport()
    t_norm = _norm(transcript)
    t_numbers = _numbers(t_norm)
    t_lower = t_norm.lower()
    for sid, value in sections.items():
        if value.strip() == MISSING_TOKEN:
            report.missing_sections.append(sid)
            continue
        v_norm = _norm(value)
        fabricated = _numbers(v_norm) - t_numbers
        for num in sorted(fabricated):
            report.warnings.append(
                {
                    "code": "unverified_number",
                    "message": f"number '{num}' in section '{sid}' is not in the transcript",
                    "section_id": sid,
                }
            )
        report.numbers_verified += len(_numbers(v_norm) & t_numbers)
        for word in _LATERALITY:
            draft_count = v_norm.lower().count(word)
            src_count = t_lower.count(word)
            if draft_count > src_count:
                report.warnings.append(
                    {
                        "code": "laterality_unverified",
                        "message": f"'{word}' appears {draft_count}× in section '{sid}', {src_count}× in transcript",
                        "section_id": sid,
                    }
                )
        draft_neg = sum(v_norm.lower().count(n) for n in _NEGATIONS)
        src_neg = sum(t_lower.count(n) for n in _NEGATIONS)
        if draft_neg != src_neg and sid not in ("",):
            # only flag when the draft *reduces/increases* negation density on
            # a non-verbatim section — interventional noise is checked by count
            if draft_neg < src_neg:
                report.warnings.append(
                    {
                        "code": "negation_shift_suspected",
                        "message": f"section '{sid}' drops negation cues ({src_neg} → {draft_neg})",
                        "section_id": sid,
                    }
                )
    return report
