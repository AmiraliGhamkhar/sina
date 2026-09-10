"""Shared DB-light / optional-deps stubs for offline tests.

Modules in `app.services` (agent package, tools package, workspace store) import
a chain of optional runtime deps — asyncpg, pgvector, prometheus_client,
langfuse, redis, arq — that exist in the Docker image but not on a bare host. To
exercise these modules offline we install lightweight stubs into sys.modules
ONLY during the import, then restore the real modules so sibling test files are
unaffected.

`import_with_stubs(import_fn)` runs `import_fn()` with the stubs installed and
restores sys.modules afterward. Use it like:

    def _load():
        from app.services.agent.agent import get_allowed_tools
        return get_allowed_tools

    try:
        get_allowed_tools = import_with_stubs(_load)
    except Exception as exc:
        get_allowed_tools = None
        _IMPORT_ERROR = exc
"""

from __future__ import annotations

import sys
import types
from typing import Callable, TypeVar

T = TypeVar("T")


def _install_stubs() -> list[tuple[str, object]]:
    """Install stubs; return (name, prior_value) pairs to restore later."""
    saved: list[tuple[str, object]] = []

    def save(name: str, value: object) -> None:
        saved.append((name, value))
        sys.modules[name] = value

    if "app.db" not in sys.modules:
        from sqlalchemy.orm import declarative_base
        fake = types.ModuleType("app.db")
        fake.AsyncSessionLocal = None
        fake.Base = declarative_base()
        fake.get_db = lambda: None
        save("app.db", fake)

    if "pgvector" not in sys.modules and "pgvector.sqlalchemy" not in sys.modules:
        from sqlalchemy.types import TypeDecorator, TypeEngine

        class Vector(TypeDecorator):
            impl = TypeEngine
            cache_ok = True

            def __init__(self, *a, **k):
                super().__init__()

        pv = types.ModuleType("pgvector")
        pv_sa = types.ModuleType("pgvector.sqlalchemy")
        pv_sa.Vector = Vector
        save("pgvector", pv)
        save("pgvector.sqlalchemy", pv_sa)

    if "arq" not in sys.modules:
        arq_pkg = types.ModuleType("arq")
        arq_pkg.__path__ = []
        arq_pkg.create_pool = lambda *a, **k: None
        arq_conn = types.ModuleType("arq.connections")

        class _ArqRedis:
            pass

        class _RedisSettings:
            @staticmethod
            def from_dsn(*a, **k):
                return None

        arq_conn.ArqRedis = _ArqRedis
        arq_conn.RedisSettings = _RedisSettings
        arq_pkg.connections = arq_conn
        save("arq", arq_pkg)
        save("arq.connections", arq_conn)

    if "langfuse" not in sys.modules:
        lf = types.ModuleType("langfuse")
        lf.get_client = lambda *a, **k: None
        lf.Langfuse = lambda *a, **k: None
        save("langfuse", lf)

    if "redis" not in sys.modules:
        redis_pkg = types.ModuleType("redis")
        redis_pkg.__path__ = []
        redis_asyncio = types.ModuleType("redis.asyncio")
        redis_asyncio.from_url = lambda *a, **k: None
        redis_asyncio.Redis = lambda *a, **k: None
        redis_pkg.asyncio = redis_asyncio
        save("redis", redis_pkg)
        save("redis.asyncio", redis_asyncio)

    if "prometheus_client" not in sys.modules:
        pc = types.ModuleType("prometheus_client")

        class _Labels:
            def inc(self, *_a, **_k):
                return None

            def observe(self, *_a, **_k):
                return None

        class _Metric:
            def __init__(self, *_a, **_k):
                pass

            def labels(self, *_a, **_k):
                return _Labels()

        pc.Counter = _Metric
        pc.Gauge = _Metric
        pc.Histogram = _Metric
        save("prometheus_client", pc)

    return saved


def import_with_stubs(import_fn: Callable[[], T], *args, **kwargs) -> T:
    """Run `import_fn` with optional-deps stubs installed, then restore them."""
    saved = _install_stubs()
    try:
        result = import_fn(*args, **kwargs)
    finally:
        for name, prior in reversed(saved):
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
    return result
