"""Database URL normalization (driver-aware, no driver imports).

Keeping this dependency-free means Phase 1 boots without SQLAlchemy
installed; Phase 7 consumes it from ``db/engine.py``.
"""
from __future__ import annotations

import re


class DatabaseUrlError(ValueError):
    pass


def normalize_database_url(raw: str, *, for_driver: str = "asyncpg") -> str:
    """postgres://user:pw@host/db → postgresql+asyncpg://user:pw@host/db

    - ``postgresql://`` / ``postgres://`` → ``postgresql+asyncpg://`` (prod)
    - ``sqlite:///path`` → ``sqlite+aiosqlite:///path`` (tests/dev)
    - URLs that already carry a driver are returned unchanged.
    """
    url = (raw or "").strip()
    if not url:
        raise DatabaseUrlError("database url is empty")
    if url.startswith("sqlite"):
        if url.startswith("sqlite:") and not re.match(r"^sqlite(\+\w+)?:///", url):
            raise DatabaseUrlError(f"sqlite urls must use the sqlite:///path form: {url}")
        if "+" in url.split(":", 1)[0]:
            return url
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    if url.startswith("postgresql+"):
        return url
    m = re.match(r"^(?:postgres|postgresql)://(.*)$", url, re.DOTALL)
    if not m:
        raise DatabaseUrlError(f"unsupported database url scheme: {url.split('://')[0]}")
    driver = "asyncpg" if for_driver in ("asyncpg", "postgres", "") else for_driver
    return f"postgresql+{driver}://{m.group(1)}"


def parse_url_for_probe(url: str) -> tuple[str, int] | None:
    """Return (host, port) for TCP reachability probes, or None if the URL is
    local (unix socket / file). No credentials are ever retained."""
    m = re.match(r"^\w+(?:\+\w+)?://(?:[^@/]*@)?([^/:?#]*)(?::(\d+))?", url)
    if not m or not m.group(1):
        return None  # no host component (sqlite:///file, unix sockets)
    host, port = m.group(1), m.group(2)
    default = 5432 if url.startswith(("postgres",)) else 6379
    return host, int(port or default)
