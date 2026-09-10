"""Persistence plumbing.

Phase 1 ships URL normalization + engine bootstrap contract only. ORM models,
repositories and Alembic migrations land in Phase 7 against PostgreSQL.
SQLite remains supported for tests/dev via the same engine factory.
"""
