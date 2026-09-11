"""Cross-worker health sharing via Redis (Phase 5, optional).

Single-process deploys never need this: one HealthTracker instance IS the
shared view. With multiple API workers (docker-compose scale / PMII), each
worker's demotion decisions would otherwise diverge — the mirror publishes
this worker's tracker snapshot (+ a queue-depth gauge) on an interval and
re-hydrates from the union on startup, so a provider killed by one worker's
outage is demoted everywhere within an interval tick.

Design constraints:
- redis.asyncio is imported LAZILY behind ``settings.redis.url`` — the API
  boots with no redis driver installed (single-worker dev/CI default).
- The mirror must never take the API down: every Redis error degrades to a
  no-op publish with a debug log (health decisions stay local and correct).
- Keys are plain JSON with TTL; no Lua, no pub/sub. This is coordination
  state, not source of truth (Postgres is, Phase 7).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)

HEALTH_KEY = "medicalscribe:provider_health"
QUEUE_DEPTH_KEY = "medicalscribe:queue_depth"
MIRROR_TTL_S = 120


class KvClient(Protocol):
    """Minimal client surface we need (redis.asyncio.Redis satisfies it;
    tests inject a dict-backed fake)."""

    async def get(self, key: str) -> bytes | str | None: ...
    async def set(self, key: str, value: str, ex: int | None = ...) -> Any: ...


class HealthMirror:
    def __init__(
        self,
        *,
        tracker,
        metrics,
        url: str,
        interval_s: float,
        client: KvClient | None = None,
        client_factory=None,
    ) -> None:
        self._tracker = tracker
        self._metrics = metrics
        self._url = url
        self._interval_s = interval_s
        self._client = client
        self._client_factory = client_factory
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._url) and self._interval_s > 0

    def _make_client(self) -> KvClient | None:
        if self._client is not None:
            return self._client
        try:  # lazy import: redis is an optional extra (pyproject [cache])
            import redis.asyncio as aioredis  # type: ignore

            return aioredis.from_url(self._url)
        except Exception:  # pragma: no cover - driver not installed
            logger.warning("redis mirror disabled: redis.asyncio unavailable")
            return None

    async def hydrate(self) -> int:
        """Absorb peers' snapshot into the local tracker (startup join)."""
        if not self.enabled:
            return 0
        client = self._make_client()
        if client is None:
            return 0
        try:
            raw = await client.get(HEALTH_KEY)
            depth = await client.get(QUEUE_DEPTH_KEY)
        except Exception:
            logger.debug("health mirror hydrate failed; continuing local-only", exc_info=True)
            return 0
        absorbed = 0
        if raw:
            data = json.loads(raw)
            for name, entry in (data.get("providers") or {}).items():
                self._tracker.absorb(name, entry)
                absorbed += 1
        if depth is not None:
            try:
                self._metrics.observe_ms("queue_depth_remote_max", float(depth))
            except (TypeError, ValueError):  # pragma: no cover
                pass
        return absorbed

    async def publish(self) -> bool:
        if not self.enabled:
            return False
        client = self._make_client()
        if client is None:
            return False
        payload = {
            "providers": dict(self._tracker.snapshot()),
            "queue_depth": self._queue_depth(),
        }
        try:
            await client.set(HEALTH_KEY, json.dumps(payload), ex=MIRROR_TTL_S)
            await client.set(QUEUE_DEPTH_KEY, str(payload["queue_depth"]), ex=MIRROR_TTL_S)
            self._metrics.incr("health_mirror_publishes")
            return True
        except Exception:
            self._metrics.incr("health_mirror_failures")
            logger.debug("health mirror publish failed", exc_info=True)
            return False

    def _queue_depth(self) -> int:
        """Aggregate in-flight audio backlog across this worker's live hubs."""
        from api.services.transcription_hub import TranscriptionHub

        return TranscriptionHub.aggregate_queue_depth()

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._client = self._make_client()
        if self._client is None:
            return
        await self.hydrate()
        self._task = asyncio.create_task(self._loop(), name="health-mirror")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client is not None and hasattr(self._client, "aclose"):
            try:
                await self._client.aclose()
            except Exception:  # pragma: no cover
                pass
        self._client = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            await self.publish()
