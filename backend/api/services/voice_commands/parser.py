"""Voice-command parser (Phase 6, spec §7).

Match policy — safety first (spec §8: never destroy clinical content):

1. **Exact**: the whole utterance equals a trigger (after script
   normalization, case folding and punctuation stripping). → command.
2. **Anchored**: for arg-taking commands only, the utterance *starts with*
   a trigger and the remainder is a non-empty argument. → command + args.
3. **Mode prefix**: an explicit ``فرمان``/``command`` prefix strips and
   re-tries rules 1–2 (escape hatch when clinical speech legitimately
   contains a trigger phrase).
4. **Ambiguous**: a trigger occurs strictly inside a longer utterance that
   is not an anchored/arg match. → NOT a command; the caller keeps the text
   verbatim and emits a ``COMMAND_AMBIGUOUS`` warning so the clinician can
   rephrase or use the mode prefix.

Normalization mirrors the terminology engine (NFKC, Persian/Arabic digits →
ASCII, ي/ك → ی/ک, ZWNJ → space) so STT spelling variance cannot break
triggers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from api.services.terminology import normalize_script
from api.services.voice_commands.catalog import (
    MODE_PREFIXES,
    CommandSpec,
    VoiceCommandCatalog,
)

_PUNCT_RE = re.compile(r"[?!.,،؛:!()«»\"'\[\]{}…—–-]+")
_WS_RE = re.compile(r"\s+")


def normalize_utterance(text: str) -> str:
    """Script normalization + punctuation strip + case fold + space collapse.
    ``پاراگراف  جدید`` == ``پاراگراف جدید`` == ``New Paragraph.``"""
    t = normalize_script(text).lower()
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


class MatchKind(str, Enum):
    EXACT = "exact"
    ANCHORED = "anchored"
    MODE_PREFIX = "mode_prefix"


@dataclass(frozen=True)
class ParsedCommand:
    command_id: str
    kind: MatchKind
    #: normalized trigger that matched
    trigger: str
    args: dict[str, str] = field(default_factory=dict)
    #: the raw utterance as dictated (for the command.detected frame)
    utterance_text: str = ""
    #: True when a trigger was found but the utterance is NOT a command —
    #: caller must keep the text and warn (never delete clinical speech)
    ambiguous_trigger: str | None = None

    @property
    def is_command(self) -> bool:
        return self.ambiguous_trigger is None


class VoiceCommandParser:
    def __init__(self, catalog: VoiceCommandCatalog) -> None:
        self._catalog = catalog
        # normalized triggers sorted longest-first so "پاراگراف جدید"
        # out-ranks shorter prefixes; deterministic tie-break by catalog order
        self._triggers: list[tuple[str, str, bool]] = []
        for spec in catalog.specs:
            for trig in spec.triggers:
                self._triggers.append((normalize_utterance(trig), spec.command_id, spec.takes_arg))
        self._triggers.sort(key=lambda t: (-len(t[0]), t[1]))
        self._prefixes = sorted((normalize_utterance(p) for p in MODE_PREFIXES), key=len, reverse=True)

    # -- API ------------------------------------------------------------------

    def parse(self, text: str) -> ParsedCommand | None:
        """Parse one finalized utterance. Returns None when the text contains
        no trigger at all (the common case — plain clinical speech)."""
        norm = normalize_utterance(text)
        if not norm:
            return None

        mode_prefix = False
        for prefix in self._prefixes:
            if norm == prefix:
                return None  # bare prefix — not a command, keep as speech
            if norm.startswith(prefix + " "):
                remainder = norm[len(prefix) :].strip()
                hit = self._match_exact(remainder) or self._match_anchored(remainder)
                if hit is not None:
                    return ParsedCommand(
                        command_id=hit[1].command_id,
                        kind=MatchKind.MODE_PREFIX,
                        trigger=hit[0],
                        args=hit[2] or {},
                        utterance_text=text.strip(),
                    )
                mode_prefix = True
                norm = remainder
                break

        exact = self._match_exact(norm)
        if exact is not None:
            return ParsedCommand(
                command_id=exact[1].command_id,
                kind=MatchKind.EXACT,
                trigger=exact[0],
                args={},
                utterance_text=text.strip(),
            )
        anchored = self._match_anchored(norm)
        if anchored is not None:
            return ParsedCommand(
                command_id=anchored[1].command_id,
                kind=MatchKind.ANCHORED,
                trigger=anchored[0],
                args=anchored[2] or {},
                utterance_text=text.strip(),
            )

        # not a command — but if a trigger is embedded mid-speech, flag it
        if not mode_prefix:
            for trig, _cid, _arg in self._triggers:
                if self._contains_word(norm, trig):
                    return ParsedCommand(
                        command_id="",
                        kind=MatchKind.EXACT,
                        trigger=trig,
                        args={},
                        utterance_text=text.strip(),
                        ambiguous_trigger=trig,
                    )
        return None

    # -- internals ------------------------------------------------------------

    def _match_exact(self, norm: str) -> tuple[str, CommandSpec, dict[str, str]] | None:
        for trig, cid, _takes_arg in self._triggers:
            if norm == trig:
                return (trig, self._catalog.require(cid), {})
        return None

    def _match_anchored(self, norm: str) -> tuple[str, CommandSpec, dict[str, str]] | None:
        for trig, cid, takes_arg in self._triggers:
            if not takes_arg:
                continue
            if norm.startswith(trig + " "):
                arg = norm[len(trig) :].strip()
                if not arg:
                    continue
                spec = self._catalog.require(cid)
                arg_name = spec.arg_name or "arg"
                return (trig, spec, {arg_name: arg})
        return None

    @staticmethod
    def _contains_word(haystack: str, needle: str) -> bool:
        """Whole-phrase containment on normalized text (space-delimited)."""
        if not needle:
            return False
        return f" {needle} " in f" {haystack} " or haystack.startswith(needle + " ") or haystack.endswith(" " + needle)
