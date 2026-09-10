"""Fire-and-forget background tasks for work that must not block the response.

Embedding generation and Langfuse tracing are auxiliary — the user shouldn't
wait on them after their answer has streamed. `spawn` schedules a coroutine on
the running loop and keeps a strong reference so it isn't garbage-collected
mid-flight (a documented asyncio footgun); exceptions are logged, never raised
into the request that spawned them.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    task = asyncio.create_task(_guard(coro))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _guard(coro) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001 - a background failure must stay contained
        logger.warning("background task failed: %r", e)
