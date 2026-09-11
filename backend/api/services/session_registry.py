"""In-process registry of live transcription sessions.

Redis-backed shared state lands in Phase 7 (rate limits + cross-worker
coordination); until then a single worker is documented as the deployment
unit for WS sessions (docker-compose runs one api worker; scale with sticky
routing when Redis arrives).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

SessionState = Literal["starting", "recording", "paused", "stopping", "closed"]


@dataclass
class WsSession:
    session_id: str
    user_id: str
    provider: str | None = None
    state: SessionState = "starting"
    created_at: float = field(default_factory=time.time)
    segments_final: int = 0
    audio_frames: int = 0

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "provider": self.provider,
            "state": self.state,
            "age_seconds": round(time.time() - self.created_at, 1),
            "segments_final": self.segments_final,
            "audio_frames": self.audio_frames,
        }


class SessionRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, WsSession] = {}

    @staticmethod
    def new_id() -> str:
        return f"ws_{uuid.uuid4().hex[:16]}"

    def create(self, *, user_id: str, provider: str | None = None) -> WsSession:
        session = WsSession(session_id=self.new_id(), user_id=user_id, provider=provider)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> WsSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def transition(self, session_id: str, state: SessionState) -> WsSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.state = state
            return session

    def remove(self, session_id: str) -> WsSession | None:
        with self._lock:
            return self._sessions.pop(session_id, None)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [s.snapshot() for s in self._sessions.values()]
