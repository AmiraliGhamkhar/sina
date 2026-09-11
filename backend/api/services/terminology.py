"""Terminology normalization service (Phase 6, spec §5 step 8 + §6).

Produces a *derived, reversible* canonical view of transcript/report text:
Persian dictation variants of English clinical terms (ام‌آرآی، سی‌تی،
پرفشاری خون …) map to their canonical form (MRI، CT، hypertension …) so that
(a) the LLM prompt uses one consistent lexicon and (b) validation grounds
``MRI`` in the draft against ``ام آر آی`` in the transcript without false
mismatches.

Safety contract (spec §8):
- the stored transcript/report is NEVER rewritten — normalization returns a
  new string plus the substitution list (reversible: ``restore`` rebuilds
  the original);
- the engine refuses patterns containing digits, dose units, negation or
  laterality tokens (belt-and-braces with the data-level constraint);
- whole-term matching only — no substring corruption.

Used by: report drafting (prompt transcript view + validation grounding),
the editor preview endpoint (``POST /api/v1/terminology/normalize``).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from api.services.terminology_data import (
    _PROTECTED_TOKENS,
    DEFAULT_CATALOG,
    TermEntry,
    TerminologyCatalog,
    _tokens,
)

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize_script(text: str) -> str:
    """Script-level canonicalization shared with the command parser and
    validator: NFKC, Persian/Arabic digits → ASCII, Arabic ي/ك → Persian,
    ZWNJ → space (so ``میلی‌گرم`` == ``میلی گرم``), collapsed whitespace."""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    # collapse horizontal whitespace but KEEP line structure — paragraph
    # markers from voice commands must survive normalization
    t = re.sub(r"[^\S\n]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


@dataclass(frozen=True)
class Substitution:
    #: span in the NORMALIZED string [start, end)
    start: int
    end: int
    original: str
    replacement: str
    canonical: str
    category: str


@dataclass
class NormalizationResult:
    normalized: str = ""
    substitutions: list[Substitution] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.substitutions)

    def restore(self) -> str:
        """Rebuild the (script-normalized) original from the normalized view
        by reversing substitutions — right-to-left so spans stay valid."""
        out = self.normalized
        for sub in sorted(self.substitutions, key=lambda s: -s.start):
            out = out[: sub.start] + sub.original + out[sub.end :]
        return out


class TerminologyNormalizer:
    """Whole-term, longest-match-first bilingual substitution engine."""

    def __init__(self, catalog: TerminologyCatalog = DEFAULT_CATALOG) -> None:
        self._catalog = catalog
        self._patterns: list[tuple[re.Pattern[str], TermEntry]] = []
        self._build()

    def _build(self) -> None:
        # surface → entry, longest surface first (greedy longest match)
        surfaces: list[tuple[str, TermEntry]] = []
        for entry in self._catalog.entries:
            for variant in (*entry.variants, entry.canonical):
                surfaces.append((normalize_script(variant).lower(), entry))
        surfaces.sort(key=lambda pair: len(pair[0]), reverse=True)

        seen: set[str] = set()
        for surface, entry in surfaces:
            if not surface or surface in seen:
                continue
            seen.add(surface)
            self._guard(surface, entry)
            # boundary lookarounds: no word char (either script) on either
            # side. Longest-first ordering plus boundaries keeps related
            # entries correct ("CT angiography" wins over "CT").
            pattern = re.compile(
                r"(?<![_\w\u0600-\u06FF])" + re.escape(surface) + r"(?![_\w\u0600-\u06FF])",
                re.IGNORECASE,
            )
            self._patterns.append((pattern, entry))

    @staticmethod
    def _guard(surface: str, entry: TermEntry) -> None:
        bad = sorted(_tokens(surface) & _PROTECTED_TOKENS)
        if bad:
            raise ValueError(
                f"terminology entry {entry.canonical!r}: variant {surface!r} "
                f"contains protected token(s) {bad} — normalization must never "
                "touch validation-critical words"
            )
        if any(ch.isdigit() for ch in surface):
            raise ValueError(
                f"terminology entry {entry.canonical!r}: variant {surface!r} contains digits"
            )

    # -- API ------------------------------------------------------------------

    @property
    def catalog(self) -> TerminologyCatalog:
        return self._catalog

    def normalize(self, text: str) -> NormalizationResult:
        """Return the canonical view + reversible substitution list. The
        input's script is normalized first (digits/ZWNJ/spelling); that step
        is lossy for layout only, never for content."""
        if not text:
            return NormalizationResult(normalized="")
        base = normalize_script(text)
        subs: list[Substitution] = []
        out = base
        # single pass: collect matches on the base string, longest-first,
        # non-overlapping (greedy by position then length)
        taken: list[tuple[int, int]] = []
        matches: list[tuple[int, int, TermEntry, str]] = []
        for pattern, entry in self._patterns:
            for m in pattern.finditer(out):
                span = (m.start(), m.end())
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                matches.append((m.start(), m.end(), entry, m.group(0)))
                taken.append(span)
        matches.sort(key=lambda t: t[0])
        pieces: list[str] = []
        cursor = 0
        for start, end, entry, original in matches:
            replacement = entry.canonical if original != entry.canonical else original
            if replacement == original:
                continue  # already canonical — nothing to record
            pieces.append(out[cursor:start])
            sub = Substitution(
                start=sum(len(p) for p in pieces),
                end=sum(len(p) for p in pieces) + len(replacement),
                original=original,
                replacement=replacement,
                canonical=entry.canonical,
                category=entry.category,
            )
            pieces.append(replacement)
            cursor = end
            subs.append(sub)
        pieces.append(out[cursor:])
        return NormalizationResult(normalized="".join(pieces), substitutions=subs)
