"""Lazy arq enqueue pool for the API process ; worker has its own."""
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings

_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _pool


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
