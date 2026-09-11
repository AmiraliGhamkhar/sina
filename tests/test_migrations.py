"""Phase 8 — Alembic migration round-trip (docs/RELEASE.md checklist item,
automated): ``upgrade head`` → ``downgrade base`` → ``upgrade head`` against a
throwaway sqlite database via the same env.py path production uses
(``-x db_url`` override; no env vars, no editable-install dependency).
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
EXPECTED_TABLES = {
    "users", "roles", "refresh_tokens", "patients", "encounters",
    "transcripts", "transcript_segments", "report_templates", "reports",
    "report_revisions", "ai_providers", "ai_requests", "audit_log", "settings",
}


def _alembic(db_url: str, *args: str) -> None:
    result = subprocess.run(
        # -x is a global flag: it must precede the subcommand
        [sys.executable, "-m", "alembic", "-x", f"db_url={db_url}", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )


def _tables(db_path: Path) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        return {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        con.close()


def test_migration_roundtrip_upgrade_downgrade_upgrade(tmp_path):
    db_path = tmp_path / "roundtrip.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    _alembic(url, "upgrade", "head")
    tables = _tables(db_path)
    assert EXPECTED_TABLES <= tables, f"missing after upgrade: {EXPECTED_TABLES - tables}"

    _alembic(url, "downgrade", "base")
    tables = _tables(db_path)
    assert not (EXPECTED_TABLES & tables), f"left behind after downgrade: {EXPECTED_TABLES & tables}"

    # re-upgrade on the SAME file: the downgraded state must be re-creatable
    _alembic(url, "upgrade", "head")
    assert EXPECTED_TABLES <= _tables(db_path)


def test_orm_metadata_matches_migrated_schema(tmp_path):
    """create_all and the migration must produce the same table set (the
    dev convenience path and the production path cannot drift)."""
    db_path = tmp_path / "migrated.db"
    _alembic(f"sqlite+aiosqlite:///{db_path}", "upgrade", "head")
    migrated = _tables(db_path)

    import asyncio

    from api.db.engine import Database

    created_path = tmp_path / "created.db"

    async def _create() -> None:
        db = Database(f"sqlite+aiosqlite:///{created_path}")
        await db.create_all()
        await db.aclose()

    asyncio.run(_create())
    created = _tables(created_path) - {"sqlite_stat1"}

    assert migrated - {"alembic_version"} == created - {"alembic_version"}, (
        "schema drift between alembic migration and ORM metadata:\n"
        f"only-migrated: {migrated - created}\nonly-created: {created - migrated}"
    )
