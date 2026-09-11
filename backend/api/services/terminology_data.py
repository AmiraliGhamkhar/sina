"""Bilingual medical terminology dictionary (Phase 6, spec §6).

Data, not code: the normalizer (``terminology.py``) is generic; everything
domain-specific lives in this table so it can be extended (Phase 7 moves it
to a DB-backed source without touching the engine).

Catalog policy (decided, documented):
- Persian *transliterations* of English clinical terms map to the English
  term (ام‌آرآی → MRI) — spec §6 explicitly wants medically meaningful
  English terms preserved (MRI, CT, ECG, hypertension, …).
- Clinical loanwords (دیابت، سیروز، آرتروز) map to their English source.
- Native Persian terms clinicians actually dictate (تب، سردرد، سونوگرافی)
  are left alone — the platform does not anglicize Persian prose.
- Acronym collisions with units/words are avoided: no ``ms`` (milliseconds),
  ``us``, ``mi`` variants.

Zero-hallucination constraints (spec §8):
- entries NEVER contain digits, dose units, negation or laterality words —
  normalization is incapable of altering validation-critical tokens by
  construction, and the engine re-checks this at pattern-build time;
- matching is whole-term (word boundaries both sides), so a variant can
  never corrupt a larger word;
- substitution is reversible — the stored transcript always keeps the
  original text; the normalized view is derived.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TermEntry:
    #: canonical form used by the normalized view (keeps the English term)
    canonical: str
    #: clinical category (advisory metadata for the editor UI)
    category: str
    #: surface forms mapping to the canonical (Persian dictation spellings,
    #: STT variants, English synonyms)
    variants: tuple[str, ...] = ()
    note: str = ""


def _e(canonical: str, category: str, *variants: str, note: str = "") -> TermEntry:
    return TermEntry(
        canonical=canonical, category=category, variants=tuple(v for v in variants if v), note=note
    )


TERMS: tuple[TermEntry, ...] = (
    # -- imaging / modalities -------------------------------------------------
    _e("MRI", "imaging", "ام آر آی", "ام‌آرآی", "ام‌آر ای", "رزونانس مغناطیسی", "magnetic resonance imaging", "mri scan"),
    _e("CT", "imaging", "سی تی", "سی‌تی", "سی‌تی اسکن", "سی تی اسکن", "computed tomography", "cat scan"),
    _e("CT angiography", "imaging", "آنژیوگرافی سی تی", "آنژیوگرافی سی‌تی", "cta"),
    _e("MRI angiography", "imaging", "آنژیوگرافی ام آر آی", "آنژیوگرافی ام‌آرآی", "mra"),
    _e("X-ray", "imaging", "رادیوگرافی", "عکس ساده", "xray", "x ray", "radiography"),
    _e("PET", "imaging", "پت اسکن", "پت‌اسکن", "pet scan", "positron emission tomography"),
    _e("mammography", "imaging", "ماموگرافی", "mammogram"),
    # -- cardiology ------------------------------------------------------------
    _e("ECG", "cardiology", "نوار قلب", "الکتروکاردیوگرام", "الکتروکاردیوگرافی", "ekg", "electrocardiogram"),
    _e("echocardiogram", "cardiology", "اکوکاردیوگرافی", "اکو کاردیوگرافی", "اکو", "echo", "echocardiography"),
    _e("myocardial infarction", "cardiology", "سکته قلبی", "انفارکتوس میوکارد", "اینفارکت میوکارد", "میوکاردیال اینفارکشن", "اینفارکشن قلبی", "heart attack", "myocardial infarct", note="commonly abbreviated MI in notes"),
    _e("hypertension", "cardiology", "پرفشاری خون", "فشار خون بالا", "htn", "high blood pressure"),
    _e("atrial fibrillation", "cardiology", "فیبریلاسیون دهلیزی", "فبریلاسیون دهلیزی", "a-fib"),
    _e("congestive heart failure", "cardiology", "نارسایی قلبی", "نارسایی احتقانی قلب", "chf", "heart failure"),
    _e("coronary artery disease", "cardiology", "بیماری عروق کرونر", "تنگی عروق کرونر", "cad"),
    _e("tachycardia", "cardiology", "تاکی کاردی", "تاکی‌کاردی", "تاکیکاردی"),
    _e("bradycardia", "cardiology", "برادی کاردی", "برادی‌کاردی", "برادیکاردی"),
    # -- endocrine --------------------------------------------------------------
    _e("diabetes mellitus", "endocrine", "دیابت", "بیماری قند", "دیابتیس", "diabetes", "dm"),
    _e("hypothyroidism", "endocrine", "کم‌کاری تیروئید", "کم کاری تیروئید", "هیپوتیروئیدیسم"),
    _e("hyperthyroidism", "endocrine", "پرکاری تیروئید", "پر کاری تیروئید", "هایپرتیروئیدیسم"),
    # -- pulmonary ----------------------------------------------------------------
    _e("COPD", "pulmonary", "بیماری انسدادی مزمن ریه", "سی او پی دی", "chronic obstructive pulmonary disease"),
    _e("pulmonary embolism", "pulmonary", "آمبولی ریه", "آمبولی ریوی", "لخته ریه"),
    _e("pleural effusion", "pulmonary", "افیوژن پلور", "افیوژن جنب", "مایع پلور", "مایع جنب", "plural effusion"),
    _e("dyspnea", "pulmonary", "تنگی نفس", "shortness of breath", "sob"),
    # -- neurology -----------------------------------------------------------------
    _e("stroke", "neurology", "سکته مغزی", "استروک", "cva", "cerebrovascular accident"),
    _e("epilepsy", "neurology", "صرع"),
    _e("multiple sclerosis", "neurology", "مالتیپل اسکلروزیس", "ام اس"),
    _e("EEG", "neurology", "نوار مغز", "الکتروانسفالوگرام", "الکتروانسفالوگرافی", "electroencephalogram"),
    _e("EMG", "neurology", "الکترومیوگرافی", "الکترومیوگرام", "electromyography"),
    # -- gastro / renal ---------------------------------------------------------------
    _e("GERD", "gastroenterology", "ریفلاکس", "رض فلوی", "reflux", "gastroesophageal reflux disease"),
    _e("cirrhosis", "gastroenterology", "سیروز کبدی", "سیروز"),
    _e("hepatitis", "gastroenterology", "هپاتیت"),
    _e("chronic kidney disease", "nephrology", "بیماری مزمن کلیه", "نارسایی کلیه", "ckd", "chronic renal failure"),
    _e("hematuria", "nephrology", "خون در ادرار", "هماتوری"),
    _e("proteinuria", "nephrology", "پروتئین در ادرار", "پروتئینوری"),
    # -- infectious ----------------------------------------------------------------------
    _e("tuberculosis", "infectious", "سل", "توبرکلوز", "tb"),
    _e("COVID", "infectious", "کرونا", "کووید", "covid"),
    # -- musculoskeletal --------------------------------------------------------------------
    _e("osteoarthritis", "musculoskeletal", "استئوآرتریت", "آرتروز", "oa", "degenerative joint disease"),
    _e("rheumatoid arthritis", "musculoskeletal", "آرتریت روماتوئید", "روماتوئید", "ra"),
    _e("fracture", "musculoskeletal", "شکستگی", "فکتور", "fx"),
    _e("herniated disc", "musculoskeletal", "دیسک کمر", "فتق دیسک", "هرنی دیسک", "disc herniation"),
    # -- oncology ---------------------------------------------------------------------------
    _e("carcinoma", "oncology", "کارسینوم"),
    _e("metastasis", "oncology", "متاستاز", "متاستازها", "mets"),
    _e("lymphadenopathy", "oncology", "لنفاادنوپاتی", "لنفادنوپاتی", "درگیری غدد لنفاوی", "adenopathy"),
    _e("chemotherapy", "oncology", "شیمی درمانی", "شیمی‌درمانی"),
    # -- hematology / general ------------------------------------------------------------------
    _e("anemia", "hematology", "کم‌خونی", "کم خونی", "انمی"),
    _e("chest pain", "general", "درد قفسه سینه", "درد سینه"),
    _e("dysphagia", "general", "سختی بلع", "دیستفاژی", "difficulty swallowing"),
    _e("edema", "general", "ادم"),
)

#: tokens that must never appear inside a variant or canonical (the engine
#: refuses to build such patterns; ``validate_entries`` powers the tests)
_PROTECTED_TOKENS = frozenset(
    {
        # laterality
        "چپ", "راست", "left", "right",
        # negation (fa)
        "بدون", "ندارد", "نمی", "نیست", "عدم", "منفی",
        # negation (en)
        "no", "not", "without", "denies", "negative", "none",
        # units / timing (number safety)
        "mg", "mcg", "g", "kg", "ml", "cc", "mm", "cm", "bpm", "mmhg",
        "ms", "mmol", "meq", "iu", "unit", "units", "mg/dl", "mmol/l",
        "day", "days", "hour", "hours", "week", "weeks", "month", "months",
    }
)

_WORD_RE = re.compile(r"[\w\u0600-\u06FF]+")


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)}


@dataclass(frozen=True)
class TerminologyCatalog:
    entries: tuple[TermEntry, ...] = field(default_factory=lambda: TERMS)

    def search(self, query: str, limit: int = 25) -> list[TermEntry]:
        q = query.strip().lower()
        if not q:
            return list(self.entries[:limit])
        hits = [
            e
            for e in self.entries
            if q in e.canonical.lower() or any(q in v.lower() for v in e.variants)
        ]
        return hits[:limit]

    def validate_entries(self) -> list[str]:
        """No entry may embed validation-critical tokens or digits — powers
        the safety tests; the engine re-checks at pattern-build time."""
        problems: list[str] = []
        for e in self.entries:
            tokens = _tokens(e.canonical) | {t for v in e.variants for t in _tokens(v)}
            bad = sorted(tokens & _PROTECTED_TOKENS)
            if bad:
                problems.append(f"{e.canonical}: protected tokens {bad}")
            for surface in (e.canonical, *e.variants):
                if any(ch.isdigit() for ch in surface):
                    problems.append(f"{e.canonical}: digits in {surface!r}")
            if not e.variants:
                problems.append(f"{e.canonical}: no variants (dead entry)")
        return problems


DEFAULT_CATALOG = TerminologyCatalog()
