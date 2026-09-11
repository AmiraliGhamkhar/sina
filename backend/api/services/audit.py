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

import asyncio
import json
import logging
import queue
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
        # Phase 7 — durable sink (set via attach_db; None keeps file-only)
        self._repo = None
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=2000)
        self._writer = None

    # -- DB sink -----------------------------------------------------------

    def attach_db(self, repo) -> None:
        """Enable the durable sink (api.repositories.audit.AuditRepository)
        and start the background writer. Call from an async context (app
        lifespan) — safe to call once at startup."""
        import asyncio

        if self._repo is not None:
            return
        self._repo = repo

        async def _writer() -> None:
            while True:
                item = await asyncio.to_thread(self._queue.get)
                if item is None:
                    return
                try:
                    await self._repo.append(
                        item["event"], item.get("user_id"), item.get("payload") or {}
                    )
                except Exception:  # noqa: BLE001 — never break the app on audit I/O
                    logger.warning("audit db write failed", exc_info=True)

        try:
            self._writer = asyncio.get_running_loop().create_task(_writer())
        except RuntimeError:  # pragma: no cover — sync test context
            self._repo = None
            logger.warning("audit db attach outside event loop — file sink only")

    async def stop_writer(self) -> None:
        if self._writer is not None:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._writer, timeout=5)
            except Exception:  # pragma: no cover
                logger.debug("audit writer stop failed", exc_info=True)
            self._writer = None

    def _enqueue_db(self, record: dict[str, Any]) -> None:
        if self._repo is None:
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:  # drop the DB copy, JSONL already has it
            logger.warning("audit db queue full; record kept in JSONL only")

    def emit(self, event: str, **meta: Any) -> None:
        """Append one JSONL audit record (+ DB queue in persistence mode).
        Never raises into request flow: a failed audit write logs locally
        but must not break the encounter."""
        sanitized = _sanitize(meta)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event,
            **sanitized,
        }
        self._enqueue_db(
            {"event": event, "user_id": sanitized.get("user_id"), "payload": sanitized}
        )
        if not self._enabled:
            logger.debug("audit (disabled) event=%s", event)
            return
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("audit write failed event=%s", event, exc_info=True)
