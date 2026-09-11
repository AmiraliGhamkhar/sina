"""In-memory transcript buffers for live sessions (Phase 2, extended Phase 6).

Process-local encounter buffers: final segments survive session end so the
editor/report flows work before PostgreSQL exists (Phase 7 will replace this
module's storage backend with `repositories/`, keeping the same service API).

Safety posture: interim hypotheses are NEVER stored — only provider finals,
explicit clinician edits (`edited=True`) and *explicit* voice-command effects
(markers, command edits), each with monotonically increasing revisions so the
future persistence layer can detect conflicts.

Phase 6 additions:
- structural markers (``paragraph`` / ``section`` / ``finalized_section``)
  stored as zero-audio segments with ``kind`` metadata;
- ``delete_last_sentence`` + journal-backed restore (voice-command targets);
- ``assemble`` — the prompt/report view of a session, honoring markers and
  optional terminology normalization.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from ai.base import TranscriptSegment

MAX_SESSIONS = 200

#: segment kinds — anything not "dictated" was produced by an explicit
#: clinician-side voice command (never by AI inference)
KIND_DICTATED = "dictated"
KIND_PARAGRAPH = "paragraph"
KIND_SECTION = "section"
KIND_FINALIZED_SECTION = "finalized_section"
KIND_REPEAT = "repeat"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟])\s+")


@dataclass
class StoredSegment:
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    language: str | None = None
    confidence: float | None = None
    #: True once the clinician changed provider output (spec §5 step 7)
    edited: bool = False
    revision: int = 0
    updated_at: str | None = None
    #: Phase 6 — who produced this segment (dictated vs command markers)
    kind: str = KIND_DICTATED
    #: Phase 6 — marker payload, e.g. {"section_title": "سابقه بیماری"}
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_marker(self) -> bool:
        return self.kind != KIND_DICTATED


@dataclass
class JournalEntry:
    """One applied transcript-mutating command, reversible for ``undo_last``."""

    command_id: str
    undo_op: str  # remove_segment | restore_text | append_segment
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoredTranscript:
    session_id: str
    user_id: str
    provider: str | None = None
    language: str | None = None
    #: clinical linkage from session.start (Phase 7 persistence)
    encounter_id: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    segments: list[StoredSegment] = field(default_factory=list)
    #: Phase 6 — command undo journal (structural effects only; pause/resume
    #: are session state, their inverse is the opposite command)
    command_journal: list[JournalEntry] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "provider": self.provider,
            "language": self.language,
            "status": "open" if self.ended_at is None else "completed",
            "segment_count": len(self.segments),
            "audio_duration_ms": int(((self.ended_at or time.time()) - self.started_at) * 1000),
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "text": s.text,
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "language": s.language,
                    "confidence": s.confidence,
                    "edited": s.edited,
                    "revision": s.revision,
                    "updated_at": s.updated_at,
                    "kind": s.kind,
                    "meta": s.meta or None,
                }
                for s in self.segments
            ],
        }


class TranscriptStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._lock = RLock()
        self._by_session: OrderedDict[str, StoredTranscript] = OrderedDict()
        self._max = max_sessions

    # -- lifecycle -----------------------------------------------------------
    def open(
        self,
        session_id: str,
        *,
        user_id: str,
        provider: str | None,
        language: str | None,
        encounter_id: str | None = None,
    ) -> StoredTranscript:
        with self._lock:
            transcript = StoredTranscript(
                session_id=session_id,
                user_id=user_id,
                provider=provider,
                language=language,
                encounter_id=encounter_id,
            )
            self._by_session[session_id] = transcript
            self._evict_locked()
            return transcript

    def close(self, session_id: str) -> None:
        with self._lock:
            t = self._by_session.get(session_id)
            if t is not None:
                t.ended_at = time.time()

    def _evict_locked(self) -> None:
        # LRU on open order; never evict an *open* transcript while over cap
        while len(self._by_session) > self._max:
            for key, t in self._by_session.items():
                if t.ended_at is not None:
                    self._by_session.pop(key)
                    break
            else:
                break  # everything still open — keep (bounded by sessions anyway)

    # -- writes (hub + editor + commands) --------------------------------------
    def append_final(self, session_id: str, segment: TranscriptSegment) -> StoredSegment | None:
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return None
            stored = StoredSegment(
                segment_id=segment.segment_id or f"seg_{len(t.segments):04d}",
                text=segment.text,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                language=segment.language,
                confidence=segment.confidence,
            )
            t.segments.append(stored)
            return stored

    def edit_segment(self, session_id: str, segment_id: str, text: str) -> StoredSegment | None:
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return None
            for s in t.segments:
                if s.segment_id == segment_id:
                    if s.text == text:
                        return s
                    s.text = text
                    s.edited = True
                    s.revision += 1
                    s.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    return s
            return None

    # -- Phase 6: markers, command edits, journal --------------------------------

    def _end_ms_locked(self, t: StoredTranscript) -> int:
        return t.segments[-1].end_ms if t.segments else 0

    def append_marker(
        self, session_id: str, kind: str, meta: dict[str, Any] | None = None
    ) -> StoredSegment | None:
        if kind not in (KIND_PARAGRAPH, KIND_SECTION, KIND_FINALIZED_SECTION, KIND_REPEAT):
            raise ValueError(f"unsupported marker kind {kind!r}")
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return None
            at = self._end_ms_locked(t)
            stored = StoredSegment(
                segment_id=f"cmd_{len(t.segments):04d}",
                text=(meta or {}).get("text", ""),
                start_ms=at,
                end_ms=at,
                kind=kind,
                meta=dict(meta or {}),
            )
            t.segments.append(stored)
            return stored

    def edit_segment_by_command(
        self, session_id: str, segment_id: str, text: str, command_id: str
    ) -> StoredSegment | None:
        """Text mutation driven by an explicit voice command (not a clinician
        keystroke edit): still bumps revision for the audit trail."""
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return None
            for s in t.segments:
                if s.segment_id == segment_id:
                    s.text = text
                    s.revision += 1
                    s.edited = True
                    s.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    s.meta = {**s.meta, "last_command": command_id}
                    return s
            return None

    def remove_segment(self, session_id: str, segment_id: str) -> StoredSegment | None:
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return None
            for i, s in enumerate(t.segments):
                if s.segment_id == segment_id:
                    return t.segments.pop(i)
            return None

    def last_text_segment(self, session_id: str) -> StoredSegment | None:
        """Last segment carrying dictated text (skips trailing markers)."""
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return None
            for s in reversed(t.segments):
                if s.text.strip():
                    return s
            return None

    def delete_last_sentence(self, session_id: str) -> dict[str, Any] | None:
        """Remove the last sentence of the last text-bearing segment.
        Returns journal-ready info or None when there is nothing to delete."""
        with self._lock:
            seg = self.last_text_segment(session_id)
            if seg is None:
                return None
            sentences = [s for s in _SENTENCE_SPLIT.split(seg.text.strip()) if s.strip()]
            if not sentences:
                return None
            removed = sentences[-1].strip()
            t = self._by_session[session_id]
            index = t.segments.index(seg)
            if len(sentences) == 1:
                # whole segment consumed — remove it (journal can restore)
                t.segments.remove(seg)
                return {
                    "segment_id": seg.segment_id,
                    "removed_sentence": removed,
                    "prior_text": seg.text,
                    "new_text": "",
                    "segment_removed": True,
                    "index": index,
                    "kind": seg.kind,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                }
            new_text = " ".join(sentences[:-1])
            seg.text = new_text
            seg.revision += 1
            seg.edited = True
            seg.meta = {**seg.meta, "last_command": "delete_last_sentence"}
            seg.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return {
                "segment_id": seg.segment_id,
                "removed_sentence": removed,
                "prior_text": seg.text + " " + removed,
                "new_text": new_text,
                "segment_removed": False,
            }

    def insert_segment_at(self, session_id: str, index: int, segment: StoredSegment) -> None:
        """Exact-position restore for undo (command journal only)."""
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return
            t.segments.insert(max(0, min(index, len(t.segments))), segment)

    # -- journal ------------------------------------------------------------------

    def push_journal(self, session_id: str, entry: JournalEntry) -> None:
        with self._lock:
            t = self._by_session.get(session_id)
            if t is not None:
                t.command_journal.append(entry)

    def pop_journal(self, session_id: str) -> JournalEntry | None:
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None or not t.command_journal:
                return None
            return t.command_journal.pop()

    def journal_depth(self, session_id: str) -> int:
        with self._lock:
            t = self._by_session.get(session_id)
            return len(t.command_journal) if t else 0

    # -- reads ---------------------------------------------------------------
    def get(self, session_id: str) -> StoredTranscript | None:
        with self._lock:
            return self._by_session.get(session_id)

    def snapshot(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            t = self._by_session.get(session_id)
            return t.summary() if t else None

    def count(self) -> int:
        with self._lock:
            return len(self._by_session)

    def sessions_for_encounter(self, encounter_id: str) -> list[str]:
        """Session ids linked to an encounter (live + completed, in memory)."""
        with self._lock:
            return [
                t.session_id
                for t in self._by_session.values()
                if t.encounter_id == encounter_id
            ]

    # -- Phase 6: prompt/report assembly ------------------------------------------

    def assemble(self, session_id: str, normalizer=None) -> dict[str, Any] | None:
        """Build the derived text view used for report prompts: paragraph
        markers become blank-line breaks, section markers become markdown
        headings, finalized-section markers become explicit fences. The
        optional normalizer (TerminologyNormalizer) canonicalizes terms —
        the returned ``substitutions`` keep it reversible. Original segments
        are never modified."""
        with self._lock:
            t = self._by_session.get(session_id)
            if t is None:
                return None
            parts: list[str] = []
            finalized_sections: list[str] = []
            section_titles: list[str] = []
            for s in t.segments:
                if s.kind == KIND_PARAGRAPH:
                    parts.append("\n\n")
                elif s.kind == KIND_SECTION:
                    title = (s.meta or {}).get("section_title", "")
                    if title:
                        section_titles.append(title)
                        parts.append(f"\n\n## {title}\n")
                elif s.kind == KIND_FINALIZED_SECTION:
                    title = (s.meta or {}).get("section_title", "")
                    finalized_sections.append(title or "(current)")
                    parts.append("\n\n[بخش نهایی شد / section finalized]\n")
                else:
                    if s.text.strip():
                        parts.append(s.text.strip())
                        parts.append(" ")
            text = re.sub(r"[ \t]+", " ", "".join(parts)).strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            subs: list[dict[str, Any]] = []
            if normalizer is not None and text:
                result = normalizer.normalize(text)
                text = result.normalized
                subs = [
                    {
                        "original": sub.original,
                        "replacement": sub.replacement,
                        "category": sub.category,
                    }
                    for sub in result.substitutions
                ]
            return {
                "text": text,
                "substitutions": subs,
                "sections": section_titles,
                "finalized_sections": finalized_sections,
                "segment_count": len(t.segments),
            }
