"""Per-user cap on open SSE streams.

The chat/agent/research stream endpoints hold an HTTP connection open
for the whole generation, so one client can pin many connections for free.
This module keeps a per-user in-memory count of open streams and refuses new
ones past settings.MAX_CONCURRENT_STREAMS with a 429. In-memory is enough: the
backend is a single container (no horizontal scaling), and the count must be
exact per process anyway.
"""
import asyncio

from fastapi import HTTPException

from app.core.config import settings

_active: dict[str, int] = {}
_lock = asyncio.Lock()


async def acquire_stream_slot(user_id: str) -> None:
    """Reserve one stream slot for this user, or 429 when the cap is hit.

    Must run in the router handler BEFORE the StreamingResponse is created so
    the rejection is a real HTTP response, not an in-stream SSE error.
    """
    async with _lock:
        count = _active.get(user_id, 0)
        if count >= settings.MAX_CONCURRENT_STREAMS:
            raise HTTPException(status_code=429, detail="too many concurrent streams")
        _active[user_id] = count + 1


async def release_stream_slot(user_id: str) -> None:
    """Free one stream slot for this user.

    Called from a stream generator's finally — fires on normal completion,
    error, and client disconnect. The counter is popped at 0 and never goes
    negative.
    """
    async with _lock:
        count = _active.get(user_id, 0)
        if count <= 1:
            _active.pop(user_id, None)
        else:
            _active[user_id] = count - 1
