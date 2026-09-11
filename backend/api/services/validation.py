"""Clinical validation service (Phase 6, spec §9) — the medical-safety core.

Compares generated report sections against the transcript evidence and emits
``ValidationWarning`` objects for clinician review. NEVER rewrites content:
validation failures are warnings, not edits (spec §9 "validation failures
should generate clinician-review warnings").

Checks (spec §9):
- numbers & dosages (unit-aware, bilingual: ``40 mg`` == ``۴۰ میلی‌گرم``;
  ``10 mg`` must not become ``100 mg``)
- units (mg vs mcg …)
- laterality (right must not become left — pair + swap detection)
- negation ("no effusion" must not become "effusion" — cue-scope analysis)
- dates (Gregorian + Jalali, with conversion for cross-calendar comparison)
- patient identifiers (MRN / national-id / phone)
- anatomical terminology grounding

Evidence policy: the transcript AND its terminology-normalized view are both
evidence (canonical forms of the same words); numbers, negation, laterality
and units are never touched by normalization, so the union adds only term
synonyms — it cannot hide a fabrication.

Codes kept backward-compatible with the Phase 4 light check:
``unverified_number``, ``laterality_unverified``, ``negation_shift_suspected``
(+ new sharper codes: ``laterality_mismatch``, ``negation_mismatch``,
``unverified_date``, ``date_mismatch``, ``unverified_identifier``,
``unverified_anatomy``, ``unit_mismatch``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from api.services.terminology import normalize_script
from api.services.terminology_data import DEFAULT_CATALOG

Severity = Literal["info", "warning", "critical"]

# --------------------------------------------------------------------------
# lexicons (data — extend here, engine stays generic)
# --------------------------------------------------------------------------

#: surface → canonical unit. ZWNJ is already space-normalized at match time.
_UNIT_MAP: dict[str, str] = {
    # mass
    "mg": "mg", "milligram": "mg", "milligrams": "mg", "میلی گرم": "mg", "میلیگرم": "mg",
    "mcg": "mcg", "ug": "mcg", "µg": "mcg", "microgram": "mcg", "میکروگرم": "mcg", "میکرو گرم": "mcg",
    "g": "g", "gram": "g", "grams": "g", "gr": "g", "گرم": "g",
    "kg": "kg", "kilogram": "kg", "کیلوگرم": "kg", "کیلو گرم": "kg",
    # volume
    "ml": "ml", "milliliter": "ml", "milliliters": "ml", "سی سی": "ml", "cc": "ml",
    "میلی لیتر": "ml", "میلی لیتری": "ml",
    "l": "l", "liter": "l", "liters": "l", "لیتر": "l",
    # length
    "mm": "mm", "میلی متر": "mm", "میلی متری": "mm",
    "cm": "cm", "سانتی متر": "cm", "سانتی متری": "cm",
    "m": "m", "meter": "m",
    # fraction / misc
    "%": "%", "درصد": "%",
    "mmhg": "mmhg", "میلی متر مرکب": "mmhg", "میلی متر جیوه": "mmhg",
    "bpm": "bpm",
    "mmol": "mmol", "میلی مول": "mmol",
    "meq": "meq",
    "iu": "iu",
    "unit": "unit", "units": "unit", "u": "unit", "واحد": "unit", "واحدی": "unit",
    "mg/dl": "mg/dl", "میلی گرم بر دسی لیتر": "mg/dl",
    "mmol/l": "mmol/l", "میلی مول بر لیتر": "mmol/l",
}

#: units where a numeric alteration is dose-critical
_DOSE_UNITS = frozenset({"mg", "mcg", "g", "iu", "unit", "mmol", "meq", "mg/dl", "mmol/l", "ml"})

#: common drug names as bilingual (english, persian) pairs — shared canonical
#: id keeps cross-language negation/dose checks working
_DRUG_PAIRS: tuple[tuple[str, str], ...] = (
    ("furosemide", "فوروسماید"), ("metformin", "متفورمین"), ("atorvastatin", "آتورواستاتین"),
    ("aspirin", "آسپرین"), ("warfarin", "وارفارین"), ("heparin", "هپارین"),
    ("insulin", "انسولین"), ("metoprolol", "متوپرولول"), ("amlodipine", "آملودیپین"),
    ("lisinopril", "لیزینوپریل"), ("omeprazole", "امپرازول"), ("prednisolone", "پردنیزولون"),
    ("diclofenac", "دیکلوفناک"), ("gabapentin", "گاباپنتین"), ("levothyroxine", "لووتیروکسین"),
    ("amoxicillin", "آموکسی سیلین"), ("ceftriaxone", "سفتریاکسون"), ("clopidogrel", "کلوپیدوگرل"),
    ("losartan", "لوزارتان"), ("sertraline", "سرترالین"), ("morphine", "مورفین"),
    ("tramadol", "ترامادول"), ("pantoprazole", "پنتوپرازول"), ("ibuprofen", "ایبوپروفن"),
    ("azithromycin", "آزیترومایسین"), ("vancomycin", "وانکومایسین"),
    ("carvedilol", "کاریلوزا"), ("hydrochlorothiazide", "هیدروکلروتیازید"),
    ("digoxin", "دیگوکسین"), ("spironolactone", "اسپیرونولاکتون"),
)
_DRUGS: tuple[str, ...] = tuple(d for pair in _DRUG_PAIRS for d in pair)

#: anatomy lexicon: surface → canonical id (fa + en)
_ANATOMY: dict[str, str] = {
    # fa
    "قلب": "heart", "کبد": "liver", "ریه": "lung", "مغز": "brain", "کلیه": "kidney",
    "کلیه ها": "kidney", "زانو": "knee", "لگن": "pelvis", "ستون فقرات": "spine",
    "گردن": "neck", "شانه": "shoulder", "آرنج": "elbow", "مچ": "wrist",
    "انگشت": "finger", "ران": "thigh", "ساق": "calf", "پا": "foot", "دست": "hand",
    "بازو": "arm", "شکم": "abdomen", "قفسه سینه": "chest", "حنجره": "larynx",
    "گوش": "ear", "چشم": "eye", "بینی": "nose", "گلو": "throat", "طحال": "spleen",
    "مثانه": "bladder", "پروستات": "prostate", "رحم": "uterus", "تخمدان": "ovary",
    "مری": "esophagus", "معده": "stomach", "روده": "bowel", "پانکراس": "pancreas",
    "تیروئید": "thyroid", "ورید": "vein", "شریان": "artery", "عروق": "vessels",
    "مهره": "vertebra", "دیسک": "disc", "مفصل": "joint", "تاندون": "tendon",
    "رباط": "ligament", "عصب": "nerve", "نخاع": "cord", "سینوس": "sinus",
    "لنفاوی": "lymph-node", "غدد": "glands", "فک": "jaw", "دندان": "tooth",
    "پوست": "skin", "مفصل ران": "hip", "هموروید": "hemorrhoid", "بیضه": "testis",
    # en
    "heart": "heart", "liver": "liver", "lung": "lung", "lungs": "lung", "brain": "brain",
    "kidney": "kidney", "kidneys": "kidney", "knee": "knee", "pelvis": "pelvis",
    "spine": "spine", "neck": "neck", "shoulder": "shoulder", "elbow": "elbow",
    "wrist": "wrist", "finger": "finger", "thigh": "thigh", "calf": "calf",
    "foot": "foot", "feet": "foot", "hand": "hand", "arm": "arm", "abdomen": "abdomen",
    "chest": "chest", "larynx": "larynx", "ear": "ear", "eye": "eye", "nose": "nose",
    "throat": "throat", "spleen": "spleen", "bladder": "bladder", "prostate": "prostate",
    "uterus": "uterus", "ovary": "ovary", "esophagus": "esophagus", "stomach": "stomach",
    "bowel": "bowel", "pancreas": "pancreas", "thyroid": "thyroid", "vein": "vein",
    "artery": "artery", "vertebra": "vertebra", "disc": "disc", "joint": "joint",
    "tendon": "tendon", "ligament": "ligament", "nerve": "nerve", "cord": "cord",
    "sinus": "sinus", "hip": "hip", "jaw": "jaw", "skin": "skin",
}

#: negation cues and their scope direction
_NEG_PREFIX_CUES = (
    "بدون", "عدم", "نداشتن", "no ", "not ", "without ", "denies ", "denied ",
    "negative for ", "absence of ", "free of ", "none of ",
)
#: sentence-level (verb-final) negation: "نمی" covers مصرف نمی‌کند-style verbs
_NEG_SUFFIX_CUES = ("ندارد", "نیست", "نداشته", "نبود", "نمی", "منفی")
#: contrast conjunctions cut a negation scope (avoid over-negation)
_NEG_SCOPE_CUTS = ("اما ", "ولی ", "ول ", "though ", "but ", "however ", "except ")

#: findings/symptoms tracked for term-level negation flips (bilingual)
_NEG_TRACKED: dict[str, str] = {
    "تب": "fever", "fever": "fever", "درد": "pain", "pain": "pain",
    "خونریزی": "bleeding", "bleeding": "bleeding", "هماتوری": "bleeding",
    "افیوژن": "effusion", "effusion": "effusion", "ماس": "mass", "mass": "mass",
    "ندول": "nodule", "nodule": "nodule", "شکستگی": "fracture", "fracture": "fracture",
    "ترشح": "discharge", "discharge": "discharge", "سرفه": "cough", "cough": "cough",
    "اسهال": "diarrhea", "diarrhea": "diarrhea", "استفراغ": "vomiting", "vomiting": "vomiting",
    "ادم": "edema", "سوفل": "murmur", "murmur": "murmur", "وییز": "wheeze", "wheeze": "wheeze",
    "راش": "rash", "rash": "rash", "ضایعه": "lesion", "lesion": "lesion",
    "متاستاز": "metastasis", "لنفادنوپاتی": "lymphadenopathy",
    "سردرد": "headache", "سرگیجه": "dizziness", "تنگی نفس": "dyspnea",
    "برادی کاردی": "bradycardia", "تاکی کاردی": "tachycardia",
    "ایسکمی": "ischemia", "ischemia": "ischemia", "نکروز": "necrosis",
    "تنگی": "stenosis", "stenosis": "stenosis", "انسداد": "obstruction",
    "obstruction": "obstruction", "دیسفون": "hoarseness",
}
# drugs are also negation-tracked ("denies aspirin" vs "on aspirin") —
# bilingual pair → ONE canonical id so cross-language flips are detected
for _en, _fa in _DRUG_PAIRS:
    _NEG_TRACKED.setdefault(_en, f"drug:{_en}")
    _NEG_TRACKED.setdefault(_fa, f"drug:{_en}")
# terminology canonicals/variants are negation-tracked too (e.g. pleural effusion)
for _entry in DEFAULT_CATALOG.entries:
    for _surface in (*_entry.variants, _entry.canonical):
        _NEG_TRACKED.setdefault(_surface, f"term:{_entry.canonical}")

_LATERALITY = {"چپ": "left", "راست": "right", "left": "left", "right": "right"}

_JALALI_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}
_GREG_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟؛;])\s+")
_TOKEN_RE = re.compile(r"[\w\u0600-\u06FF]+")

# --------------------------------------------------------------------------
# warning model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationWarning:
    code: str
    severity: Severity
    message: str
    section_id: str | None = None
    #: quoted draft snippet the warning refers to (never the full section)
    evidence: str | None = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "section_id": self.section_id,
            "evidence": self.evidence,
        }


@dataclass
class ValidationResult:
    warnings: list[ValidationWarning] = field(default_factory=list)
    numbers_verified: int = 0
    missing_sections: list[str] = field(default_factory=list)

    def add(self, code: str, severity: Severity, message: str, section_id: str | None = None,
            evidence: str | None = None) -> None:
        self.warnings.append(
            ValidationWarning(code, severity, message, section_id, evidence)
        )


# --------------------------------------------------------------------------
# extraction primitives
# --------------------------------------------------------------------------


def _canonical_number(raw: str) -> Decimal | None:
    t = raw.replace("٫", ".").replace("٬", "")
    # thousands vs decimal comma: "1,500" → 1500; "1,5" → 1.5
    if "," in t:
        head, _, tail = t.partition(",")
        t = head + tail if len(tail) == 3 and head.isdigit() else head + "." + tail
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str | None
    raw: str
    start: int
    end: int


def extract_quantities(text: str) -> list[Quantity]:
    """Every number in text with its canonical unit (Persian multi-word
    units included) and its [start, end) span in the normalized text."""
    out: list[Quantity] = []
    norm = normalize_script(text)
    for m in re.finditer(r"\d+(?:[.,٫]\d+)?", norm):
        value = _canonical_number(m.group())
        if value is None:
            continue
        rest = norm[m.end():]
        unit: str | None = None
        # up to 4 following words (Persian multi-word units), punctuation-tolerant
        words = re.match(r"\s{0,2}([\w\u0600-\u06FF/%]+(?:\s+[\w\u0600-\u06FF/%]+){0,3})", rest)
        if words:
            toks = words.group().strip().split()
            for n in (4, 3, 2, 1):
                if len(toks) >= n:
                    joined = " ".join(toks[:n])
                    if joined in _UNIT_MAP:
                        unit = _UNIT_MAP[joined]
                        break
                    if n == 1 and toks[0] in _UNIT_MAP:
                        unit = _UNIT_MAP[toks[0]]
                        break
        out.append(Quantity(value, unit, m.group(), m.start(), m.end()))
    return out


def _near_drug(text_norm: str, index: int) -> bool:
    window = text_norm[max(0, index - 60): index + 60]
    return any(drug in window for drug in _DRUGS)


def extract_laterality_pairs(text: str) -> set[tuple[str, str]]:
    """(side, anatomy-canonical) pairs with a ±3-token proximity window."""
    norm = normalize_script(text)
    tokens = norm.split()
    pairs: set[tuple[str, str]] = set()
    for i, tok in enumerate(tokens):
        side = _LATERALITY.get(tok.lower())
        if side is None:
            continue
        lo, hi = max(0, i - 3), min(len(tokens), i + 4)
        for j in (*range(lo, i), *range(i + 1, hi)):
            anatomy = _anatomy_canonical(tokens[j])
            if anatomy:
                pairs.add((side, anatomy))
    return pairs


def _anatomy_canonical(token: str) -> str | None:
    low = token.lower()
    if low in _ANATOMY:
        return _ANATOMY[low]
    # Persian ezafe/morphology: «زانوی» ~ «زانو» (prefix match, len ≥ 3)
    if len(low) >= 3:
        for surface in _ANATOMY:
            if len(surface) >= 3 and low.startswith(surface):
                return _ANATOMY[surface]
    return None


def extract_anatomy(text: str) -> set[str]:
    norm = normalize_script(text)
    found: set[str] = set()
    for tok in norm.split():
        canon = _anatomy_canonical(tok)
        if canon:
            found.add(canon)
    for surface in _ANATOMY:  # multi-word surfaces («قفسه سینه»)
        if len(surface.split()) > 1 and f" {surface} " in f" {norm} ":
            found.add(_ANATOMY[surface])
    return found


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _negated_terms(text: str) -> set[str]:
    """Canonical ids of tracked clinical terms that are NEGATED in text.
    Cue-scope heuristic: prefix cues negate to the sentence end (cut at
    contrast conjunctions); suffix cues (ندارد/منفی) negate the whole
    sentence / the preceding two terms."""
    norm = normalize_script(text)
    negated: set[str] = set()
    for sentence in _sentences(norm):
        sent = f" {sentence} "
        # suffix cues: whole-sentence negation (Persian verb-final pattern)
        if any(f" {cue}" in sent or sent.endswith(f" {cue} ") or cue in sent for cue in _NEG_SUFFIX_CUES):
            for term_id in _terms_in(sentence):
                negated.add(term_id)
            continue
        low = sentence.lower()
        for cue in _NEG_PREFIX_CUES:
            start = 0
            while True:
                idx = low.find(cue, start)
                if idx == -1:
                    break
                scope = sentence[idx + len(cue):]
                for cut in _NEG_SCOPE_CUTS:
                    cut_idx = scope.lower().find(cut)
                    if cut_idx != -1:
                        scope = scope[:cut_idx]
                for term_id in _terms_in(scope):
                    negated.add(term_id)
                start = idx + len(cue)
    return negated


def _terms_in(span: str) -> set[str]:
    """Tracked clinical terms present in a span (punctuation-tolerant, with
    Persian prefix tolerance for ezafe forms) → canonical ids."""
    tokens = _TOKEN_RE.findall(normalize_script(span).lower())
    low = " " + " ".join(tokens) + " "
    found: set[str] = set()
    for surface, term_id in _NEG_TRACKED.items():
        s = surface.lower()
        if " " in s or s.isascii():
            if f" {s} " in low:
                found.add(term_id)
        else:
            # Persian single-word: token prefix tolerance («افیوژنی» ~ «افیوژن»)
            for tok in tokens:
                if tok == s or (len(s) >= 4 and tok.startswith(s)):
                    found.add(term_id)
                    break
    return found


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """Standard Jalali→Gregorian conversion (well-known astronomical
    arithmetic, public-domain formulation — adapted from the classic
    jalaali algorithm; marked as adapted third-party math)."""
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    sal_a = 0
    if gy % 4 == 0 and gy % 100 != 0 or gy % 400 == 0:
        sal_a = [0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366]
    else:
        sal_a = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365]
    gm = 0
    for i in range(1, 13):
        if gd <= sal_a[i]:
            gm = i
            break
    gd -= sal_a[gm - 1]
    return gy, gm, gd


def _gregorian_ord(y: int, m: int, d: int) -> int | None:
    """Exact day ordinal via stdlib datetime (proleptic Gregorian)."""
    import datetime as _dt

    try:
        return _dt.date(y, m, d).toordinal()
    except ValueError:
        return None


@dataclass(frozen=True)
class ExtractedDate:
    y: int
    m: int
    d: int
    calendar: str  # "jalali" | "gregorian"
    raw: str
    start: int = 0
    end: int = 0

    @property
    def gregorian_ordinal(self) -> int | None:
        if self.calendar == "jalali":
            g = jalali_to_gregorian(self.y, self.m, self.d)
            return _gregorian_ord(*g)
        return _gregorian_ord(self.y, self.m, self.d)


def extract_dates(text: str) -> list[ExtractedDate]:
    norm = normalize_script(text)
    low = norm.lower()
    out: list[ExtractedDate] = []

    def _cal(year: int) -> str:
        return "jalali" if 1300 <= year <= 1500 else "gregorian"

    # numeric y/m/d (jalali years 1300-1500; gregorian 1900-2100)
    for m in re.finditer(r"(?<!\d)(\d{4})([/\-])(\d{1,2})\2(\d{1,2})(?!\d)(?!\.\d)", norm):
        y, mo, d = int(m.group(1)), int(m.group(3)), int(m.group(4))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        cal = _cal(y)
        if cal == "gregorian" and not (1900 <= y <= 2100):
            continue
        out.append(ExtractedDate(y, mo, d, cal, m.group(), m.start(), m.end()))

    # numeric d/m/y — both day-first and month-first candidates when ambiguous
    for m in re.finditer(r"(?<!\d)(?<!\.\d)(\d{1,2})([/\-])(\d{1,2})\2(\d{4})(?!\d)", norm):
        a, b, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
        cal = _cal(y)
        if cal == "gregorian" and not (1900 <= y <= 2100):
            continue
        candidates = [(a, b)] if a > 12 else ([(a, b)] if b <= 12 else [(b, a)])
        if a <= 12 and b <= 12 and a != b:
            candidates = [(a, b), (b, a)]  # ambiguous — accept either reading
        for d, mo in candidates:
            if 1 <= mo <= 12 and 1 <= d <= 31:
                out.append(ExtractedDate(y, mo, d, cal, m.group(), m.start(), m.end()))

    # «12 مرداد 1403»
    for fa_month, num in _JALALI_MONTHS.items():
        for m in re.finditer(rf"(\d{{1,2}})\s+{fa_month}\s*,?\s*(\d{{4}})", low):
            y, d = int(m.group(2)), int(m.group(1))
            if 1300 <= y <= 1500 and 1 <= d <= 31:
                out.append(ExtractedDate(y, num, d, "jalali", m.group(), m.start(), m.end()))

    # «August 2 2024» / «2 August 2024»
    for en_month, num in _GREG_MONTHS.items():
        for m in re.finditer(rf"{en_month}\.?\s+(\d{{1,2}})\s*,?\s*(\d{{4}})", low):
            y, d = int(m.group(2)), int(m.group(1))
            if 1900 <= y <= 2100 and 1 <= d <= 31:
                out.append(ExtractedDate(y, num, d, "gregorian", m.group(), m.start(), m.end()))
        for m in re.finditer(rf"(\d{{1,2}})\s+{en_month}\.?\s*,?\s*(\d{{4}})", low):
            y, d = int(m.group(2)), int(m.group(1))
            if 1900 <= y <= 2100 and 1 <= d <= 31:
                out.append(ExtractedDate(y, num, d, "gregorian", m.group(), m.start(), m.end()))

    return out


_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", re.compile(r"(?<!\d)(?:\+98|0098|0)(9\d{9})(?!\d)")),
    ("national_id", re.compile(r"(?<!\d)(?<!\d\.)(\d{10})(?!\d)(?!\.\d)")),
    (
        "mrn",
        re.compile(r"(?:mrn|کد بیمار(?:ت)?|شناسه بیمار)[\s:#=\-]*(\d{4,12})", re.IGNORECASE),
    ),
)


def extract_identifiers(text: str) -> list[tuple[str, str]]:
    norm = normalize_script(text)
    out: list[tuple[str, str]] = []
    for kind, pattern in _ID_PATTERNS:
        for m in pattern.finditer(norm):
            out.append((kind, m.group(1)))
    return out


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------


def validate_sections(
    transcript: str,
    sections: dict[str, str],
    *,
    missing_token: str = "[[MISSING]]",
    normalized_transcript: str | None = None,
) -> ValidationResult:
    """Validate generated sections against transcript evidence.

    ``normalized_transcript`` (terminology-normalized view) is additional
    EVIDENCE — canonical synonyms of what was dictated — never a replacement.
    """
    result = ValidationResult()
    evidence_raw = normalize_script(transcript)
    evidence_norm = normalize_script(normalized_transcript or "")
    evidence_all = evidence_raw + "\n" + evidence_norm

    ev_quantities = extract_quantities(evidence_all)
    ev_laterality_pairs = extract_laterality_pairs(evidence_all)
    ev_laterality_sides = {side for side, _anat in ev_laterality_pairs}
    for fa_word, side in (("چپ", "left"), ("راست", "right")):
        if re.search(rf"(?<![\w\u0600-\u06FF]){fa_word}(?![\w\u0600-\u06FF])", evidence_all):
            ev_laterality_sides.add(side)
    for en_word, side in (("left", "left"), ("right", "right")):
        if re.search(rf"\b{en_word}\b", evidence_all, re.IGNORECASE):
            ev_laterality_sides.add(side)
    ev_anatomy = extract_anatomy(evidence_all)
    ev_dates = extract_dates(evidence_all)
    ev_date_ords = [d.gregorian_ordinal for d in ev_dates if d.gregorian_ordinal is not None]
    ev_identifiers = set(extract_identifiers(evidence_all))
    ev_negated = _negated_terms(evidence_all)
    # density is a per-utterance count — compute on the RAW transcript only
    # (the normalized view is a synonym projection; counting both would
    # double every cue)
    ev_neg_density = _negation_density(evidence_raw)
    ev_terms = _terms_in(evidence_all)

    for sid, value in sections.items():
        if not value.strip() or value.strip() == missing_token:
            result.missing_sections.append(sid)
            continue
        draft_norm = normalize_script(value)
        draft_lower = draft_norm.lower()

        # -- numbers / doses / units ------------------------------------------
        # numbers that are components of a date are checked by the date rules
        date_spans = [(d.start, d.end) for d in extract_dates(value)]
        for q in extract_quantities(value):
            if any(s < q.end and q.start < e for s, e in date_spans):
                continue
            q_value, q_unit, raw = q.value, q.unit, q.raw
            exact = [ev for ev in ev_quantities if ev.value == q_value and ev.unit == q_unit]
            if exact:
                result.numbers_verified += 1
                continue
            same_value = [ev for ev in ev_quantities if ev.value == q_value]
            if same_value:
                ev_units = sorted({str(ev.unit) for ev in same_value})
                near = ", ".join(f"{ev.value} {ev.unit or ''}".strip() for ev in same_value[:3])
                shown = f"{raw} {q_unit or ''}".strip()
                dose_ctx = q_unit in _DOSE_UNITS or _near_drug(draft_norm, q.start)
                result.add(
                    "unit_mismatch",
                    "critical" if dose_ctx else "warning",
                    f"'{shown}': transcript has the value with unit(s) "
                    f"{ev_units} ({near}) — verify unit",
                    sid,
                    raw,
                )
                continue
            same_unit = [ev for ev in ev_quantities if ev.unit == q_unit]
            near_miss = [ev for ev in same_unit if _digits_overlap(ev.value, q_value)]
            dose_ctx = q_unit in _DOSE_UNITS or _near_drug(draft_norm, q.start)
            detail = ""
            if near_miss:
                ev = near_miss[0]
                ev_shown = f"{ev.value} {ev.unit or ''}".strip()
                detail = f" (transcript has '{ev_shown}' — possible alteration)"
            shown = f"{raw} {q_unit or ''}".strip()
            result.add(
                "unverified_number",
                "critical" if (dose_ctx or near_miss) else "warning",
                f"number '{shown}' is not in the transcript{detail}",
                sid,
                raw,
            )

        # -- laterality --------------------------------------------------------
        draft_pairs = extract_laterality_pairs(value)
        pair_flagged_sides: set[str] = set()
        for side, anatomy in sorted(draft_pairs):
            if (side, anatomy) in ev_laterality_pairs:
                continue
            mirror = "right" if side == "left" else "left"
            if (mirror, anatomy) in ev_laterality_pairs:
                pair_flagged_sides.add(side)
                result.add(
                    "laterality_mismatch",
                    "critical",
                    f"draft says {side} {anatomy}; transcript says {mirror} {anatomy}",
                    sid,
                    f"{side} {anatomy}",
                )
            elif side not in ev_laterality_sides:
                pair_flagged_sides.add(side)
                result.add(
                    "laterality_unverified",
                    "warning",
                    f"'{side}' ({anatomy}) is not in the transcript",
                    sid,
                    f"{side} {anatomy}",
                )
        for word, side in (("چپ", "left"), ("راست", "right"), ("left", "left"), ("right", "right")):
            if word.isascii():
                draft_count = len(re.findall(rf"\b{word}\b", draft_lower))
            else:
                draft_count = len(
                    re.findall(rf"(?<![\w\u0600-\u06FF]){word}(?![\w\u0600-\u06FF])", draft_norm)
                )
            if draft_count and side not in ev_laterality_sides and side not in pair_flagged_sides:
                result.add(
                    "laterality_unverified",
                    "warning",
                    f"'{word}' appears in section '{sid}' but not in the transcript",
                    sid,
                    word,
                )

        # -- negation ----------------------------------------------------------
        draft_negated = _negated_terms(value)
        draft_terms = _terms_in(value)
        for term_id in sorted(draft_terms - draft_negated):
            if term_id in ev_negated:
                result.add(
                    "negation_mismatch",
                    "critical",
                    f"'{term_id}' is NEGATED in the transcript but asserted in the draft",
                    sid,
                    term_id,
                )
        for term_id in sorted(draft_negated - ev_negated):
            if term_id in ev_terms:
                result.add(
                    "negation_added",
                    "warning",
                    f"draft adds a negation for '{term_id}' that the transcript does not have",
                    sid,
                    term_id,
                )
        # -- dates ---------------------------------------------------------------
        for draft_date in extract_dates(value):
            ord_ = draft_date.gregorian_ordinal
            if ord_ is None or ord_ in ev_date_ords:
                continue
            if any(abs(ord_ - ev) <= 31 for ev in ev_date_ords):
                result.add(
                    "date_mismatch",
                    "critical",
                    f"date '{draft_date.raw}' does not match any transcript date exactly "
                    "(close value found — verify)",
                    sid,
                    draft_date.raw,
                )
            else:
                result.add(
                    "unverified_date",
                    "warning",
                    f"date '{draft_date.raw}' is not in the transcript",
                    sid,
                    draft_date.raw,
                )

        # -- identifiers ------------------------------------------------------------
        for kind, ident in extract_identifiers(value):
            if (kind, ident) not in ev_identifiers:
                masked = ident[:3] + "···" + ident[-2:] if len(ident) > 6 else "···"
                result.add(
                    "unverified_identifier",
                    "critical",
                    f"{kind} '{masked}' is not in the transcript",
                    sid,
                    kind,
                )

        # -- anatomy grounding ---------------------------------------------------------
        for anatomy in sorted(extract_anatomy(value) - ev_anatomy):
            result.add(
                "unverified_anatomy",
                "warning",
                f"anatomical term '{anatomy}' is not mentioned in the transcript",
                sid,
                anatomy,
            )

    # coarse negation-density tripwire — AGGREGATE across sections (content
    # legitimately moves between sections; only a net loss across the whole
    # draft is suspicious). Suppressed when a term-level mismatch fired.
    drafted_any = any(
        v.strip() and v.strip() != missing_token for v in sections.values()
    )
    draft_total_density = sum(
        _negation_density(normalize_script(v))
        for v in sections.values()
        if v.strip() and v.strip() != missing_token
    )
    if drafted_any and draft_total_density < ev_neg_density and not any(
        w.code == "negation_mismatch" for w in result.warnings
    ):
        result.add(
            "negation_shift_suspected",
            "warning",
            f"draft drops negation cues overall ({ev_neg_density} → {draft_total_density})",
            None,
            None,
        )

    return result


def _negation_density(text_norm: str) -> int:
    return sum(text_norm.count(cue.strip()) for cue in (*_NEG_PREFIX_CUES, *_NEG_SUFFIX_CUES))


def _digits_overlap(a: Decimal, b: Decimal) -> bool:
    """10 vs 100 (digit-superset), 0.5 vs 5 — classic STT/LLM number slips."""

    def _s(d: Decimal) -> str:
        return format(d.normalize(), "f").replace(".", "")

    sa, sb = _s(a), _s(b)
    return set(sa).issubset(set(sb)) or set(sb).issubset(set(sa))

