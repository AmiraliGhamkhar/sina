"""Phase 8 — the shared Redis rate-limit backend against a real Redis.

Runs only when MS_TEST_REDIS_URL is set (CI provisions a redis service);
skipped locally otherwise (in-process backend covers the logic; RedisBackend
is a thin INCR/EXPIRE wrapper).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

REDIS_URL = os.environ.get("MS_TEST_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_URL, reason="MS_TEST_REDIS_URL not set (no redis service)"
)


def test_redis_backend_counts_and_blocks():
    from api.services.rate_limit import RedisBackend

    backend = RedisBackend(REDIS_URL)

    async def scenario() -> None:
        key = f"test:{uuid.uuid4().hex}"
        allowed1, retry1 = await backend.hit(key, window_s=60, limit=2)
        allowed2, _ = await backend.hit(key, window_s=60, limit=2)
        allowed3, retry3 = await backend.hit(key, window_s=60, limit=2)
        assert allowed1 and allowed2
        assert not allowed3
        assert retry3 >= 1  # TTL-based retry hint
        # a different key is an independent bucket
        other, _ = await backend.hit(f"test:{uuid.uuid4().hex}", window_s=60, limit=2)
        assert other
        await backend.aclose()

    asyncio.run(scenario())


def test_redis_backend_degrades_open_when_unreachable():
    from api.services.rate_limit import RedisBackend

    backend = RedisBackend("redis://127.0.0.1:1/0")  # nothing listens there

    async def scenario() -> None:
        allowed, retry = await backend.hit("unreachable", window_s=60, limit=2)
        assert allowed is True and retry == 0
        await backend.aclose()

    asyncio.run(scenario())
