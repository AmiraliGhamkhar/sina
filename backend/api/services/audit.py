"""Audit sink for compliance-relevant events.

Concept from open-medical-scribe's auditLogger (append-only JSONL), hardened
for this project: the writer accepts *only* scalar metadata and enforces a
deny-list on key names, so a careless caller can never persist a transcript
body. DB-backed audit (with queries) lands in Phase 7; the file sink remains
as the local fallback.

Events (Phase 1 active): provider_selected, session_started, session_stopped,
auth_denied. (Phase 7+: login, report_finalized, report_approved, settings_changed.)
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: keys whose *values* must never reach the audit log
FORBIDDEN_KEYS = {
    "transcript",
    "text",
    "audio",
    "chunk",
    "api_key",
    "authorization",
    "password",
    "token",
    "prompt",
    "completion",
}


def _sanitize(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if key.lower() in FORBIDDEN_KEYS:
            out[key] = "[REDACTED]"
            continue
        if isinstance(value, str):
            out[key] = value if len(value) <= 512 else f"[TRUNCATED len={len(value)}]"
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, dict):
            out[key] = _sanitize(value)
        elif isinstance(value, (list, tuple)) and len(value) <= 32:
            out[key] = [v if isinstance(v, (int, float, bool, str)) else "[OBJ]" for v in value]
        else:
            out[key] = f"[{type(value).__name__}]"
    return out


class AuditLog:
    def __init__(self, path: Path | None, *, enabled: bool = True) -> None:
        self._path = path
        self._enabled = enabled and path is not None
        self._lock = threading.Lock()

    def emit(self, event: str, **meta: Any) -> None:
        """Append one JSONL audit record. Never raises into request flow:
        a failed audit write logs locally but must not break the encounter."""
        if not self._enabled:
            logger.debug("audit (disabled) event=%s", event)
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            **_sanitize(meta),
        }
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("audit write failed event=%s", event, exc_info=True)
