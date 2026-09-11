"""In-memory transcript buffers for live sessions (Phase 2).

Process-local encounter buffers: final segments survive session end so the
editor/report flows work before PostgreSQL exists (Phase 7 will replace this
module's storage backend with `repositories/`, keeping the same service API).

Safety posture: interim hypotheses are NEVER stored — only provider finals
plus explicit clinician edits (`edited=True`), each with monotonically
increasing revisions so the future persistence layer can detect conflicts.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from ai.base import TranscriptSegment

MAX_SESSIONS = 200


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


@dataclass
class StoredTranscript:
    session_id: str
    user_id: str
    provider: str | None = None
    language: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    segments: list[StoredSegment] = field(default_factory=list)

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
        self, session_id: str, *, user_id: str, provider: str | None, language: str | None
    ) -> StoredTranscript:
        with self._lock:
            transcript = StoredTranscript(
                session_id=session_id, user_id=user_id, provider=provider, language=language
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

    # -- writes (hub + editor) -----------------------------------------------
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
