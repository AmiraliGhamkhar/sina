"""Rate limiting (Phase 7, spec §14 — MMG pattern, adapted).

Fixed-window counters keyed by identity:
- general bucket: per client IP (or per user when authenticated)
- auth bucket: per IP for ``/auth/login`` + ``/auth/refresh`` (stricter)

Backends:
- ``RedisBackend`` — shared across workers (INCR + EXPIRE); every Redis
  error **degrades open** (availability over strictness for a clinical tool;
  the auth bucket still has the lockout guard as a second layer).
- ``InProcessBackend`` — single worker (the documented default deployment).

Never applied to ``/health`` probes (infra requirements).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"rate limited; retry after {retry_after_s}s")
        self.retry_after_s = max(1, int(retry_after_s))


class Backend(Protocol):
    async def hit(self, key: str, window_s: int, limit: int) -> tuple[bool, int]:
        """Count one event; return (allowed, retry_after_seconds)."""
        ...


class InProcessBackend:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._counters: dict[str, tuple[int, float]] = {}  # key → (count, window_start)

    async def hit(self, key: str, window_s: int, limit: int) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            count, start = self._counters.get(key, (0, now))
            if now - start >= window_s:
                count, start = 0, now
            count += 1
            self._counters[key] = (count, start)
            if len(self._counters) > 10_000:  # bounded memory
                cutoff = now - window_s
                self._counters = {
                    k: v for k, v in self._counters.items() if v[1] > cutoff
                }
            if count > limit:
                retry_after = int(window_s - (now - start)) + 1
                return False, max(1, retry_after)
            return True, 0


class RedisBackend:
    """Shared counters. Degrades open on any Redis failure."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, socket_timeout=1.0, socket_connect_timeout=1.0)

    async def hit(self, key: str, window_s: int, limit: int) -> tuple[bool, int]:
        redis_key = f"ms:rl:{key}:{int(time.time() // window_s)}"
        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                await self._redis.expire(redis_key, window_s)
            if count > limit:
                ttl = await self._redis.ttl(redis_key)
                return False, max(1, ttl)
            return True, 0
        except Exception:  # noqa: BLE001 — availability over strictness
            logger.warning("rate-limit backend unreachable — allowing (degrade-open)")
            return True, 0

    async def aclose(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # pragma: no cover
            pass


AUTH_PATHS = ("/api/v1/auth/login", "/api/v1/auth/refresh")


class RateLimiter:
    def __init__(self, backend: Backend, *, enabled: bool = True) -> None:
        self._backend = backend
        self._enabled = enabled

    async def check_request(
        self, *, path: str, client_ip: str, user_id: str | None, per_minute: int,
        auth_per_minute: int, window_s: int,
    ) -> None:
        if not self._enabled:
            return
        identity = user_id or f"ip:{client_ip}"
        if path in AUTH_PATHS:
            key = f"auth:{client_ip}"
            limit = auth_per_minute
        else:
            key = f"api:{identity}"
            limit = per_minute
        try:
            allowed, retry_after = await self._backend.hit(key, window_s, limit)
        except Exception:  # noqa: BLE001 — availability over strictness; the
            # login lockout guard (auth_service) remains as the second layer
            logger.warning("rate-limit backend failed — allowing (degrade-open)", exc_info=True)
            return
        if not allowed:
            raise RateLimitExceeded(retry_after)
