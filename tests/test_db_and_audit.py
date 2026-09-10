"""db URL normalization + audit sanitization (data-hygiene guardrails)."""
from __future__ import annotations

import json

import pytest
from api.db.url import DatabaseUrlError, normalize_database_url, parse_url_for_probe
from api.services.audit import AuditLog


def test_postgres_urls_gain_asyncpg_driver():
    assert (
        normalize_database_url("postgres://u:p@db:5432/medicalscribe")
        == "postgresql+asyncpg://u:p@db:5432/medicalscribe"
    )
    assert (
        normalize_database_url("postgresql://localhost/medicalscribe")
        == "postgresql+asyncpg://localhost/medicalscribe"
    )
    # already-drivered URL untouched
    url = "postgresql+asyncpg://u@h/db"
    assert normalize_database_url(url) == url


def test_sqlite_switches_to_aiosqlite():
    assert (
        normalize_database_url("sqlite:///data/dev.db")
        == "sqlite+aiosqlite:///data/dev.db"
    )
    with pytest.raises(DatabaseUrlError):
        normalize_database_url("sqlite://data/dev.db")
    with pytest.raises(DatabaseUrlError):
        normalize_database_url("mysql://h/db")
    with pytest.raises(DatabaseUrlError):
        normalize_database_url("")


def test_probe_parser_extracts_host_port_only():
    assert parse_url_for_probe("postgresql+asyncpg://user:pw@db.internal:6543/ms") == ("db.internal", 6543)
    assert parse_url_for_probe("redis://cache:6379/0") == ("cache", 6379)
    assert parse_url_for_probe("redis://localhost") == ("localhost", 6379)
    assert parse_url_for_probe("sqlite+aiosqlite:///data/dev.db") is None


# -- audit hygiene -------------------------------------------------------------


def test_audit_never_persists_transcript_like_content(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.emit(
        "report_generated",
        session_id="s1",
        transcript="patient secret dictated text",
        provider="mock",
        note="x" * 4000,
        nested={"text": "inner secret", "count": 5},
    )
    record = json.loads((tmp_path / "audit.jsonl").read_text().strip())
    assert record["event"] == "report_generated"
    assert record["transcript"] == "[REDACTED]"
    assert record["nested"]["text"] == "[REDACTED]"
    assert record["nested"]["count"] == 5
    assert record["note"].startswith("[TRUNCATED len=4000]")
    assert "patient secret" not in (tmp_path / "audit.jsonl").read_text()


def test_audit_disabled_is_silent_no_raise(tmp_path):
    log = AuditLog(None, enabled=False)
    log.emit("noop", something=1)  # must not raise


def test_audit_io_failure_does_not_break_caller(tmp_path):
    # directory in place of a file → write error, swallowed
    broken = AuditLog(tmp_path)
    broken.emit("session_started", user_id="u")  # no exception escapes
