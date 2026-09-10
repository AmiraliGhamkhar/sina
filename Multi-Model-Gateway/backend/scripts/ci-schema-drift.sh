#!/usr/bin/env bash
#
# CI schema-drift guard
#
# Proves the SQLAlchemy models and the Alembic migration chain agree: it
# migrates a SCRATCH database to head, then asks alembic whether the models
# would autogenerate any further changes. A model edit without a matching
# migration fails the build instead of silently drifting.
#
# Usage (CI):
#   DATABASE_URL=postgresql://user:pass@localhost:5432/llmgateway_ci \
#     bash backend/scripts/ci-schema-drift.sh
#
# Requirements:
#   - DATABASE_URL points at a throwaway database; the script migrates it to
#     head (never touches your real DB) and diffs models against it.
#   - alembic and the app dependencies are installed (CI: pip install -r
#     requirements.txt). The DB image must include the pgvector extension
#     (pgvector/pgvector:pg16) — the memories migration CREATE EXTENSIONs it.
#   - `alembic check` needs alembic >= 1.9; older alembics fall back to an
#     autogenerate-and-grep diff (see below).
#
# Exit codes: 0 = no drift, 1 = drift detected (or any step failed).

set -euo pipefail

DATABASE_URL="${DATABASE_URL:?DATABASE_URL is required (scratch DB, e.g. postgresql://user:pass@localhost:5432/llmgateway_ci)}"
export DATABASE_URL

# alembic/env.py imports app.core.config, whose pydantic Settings require a few
# env vars even though alembic never uses them. Provide CI-safe dummies so the
# guard runs with only DATABASE_URL set.
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export SECRET_KEY="${SECRET_KEY:-ci-schema-drift-unused}"
export ALGORITHM="${ALGORITHM:-HS256}"
export LM_URL="${LM_URL:-http://localhost:1234}"
export LM_DEFAULT_MODEL="${LM_DEFAULT_MODEL:-ci-schema-drift-unused}"

# Run from the backend dir (this script lives in backend/scripts/).
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> alembic upgrade head ($DATABASE_URL)"
alembic upgrade head

if alembic check --help >/dev/null 2>&1; then
    # alembic >= 1.9: `alembic check` exits 0 when models and migrations agree.
    if alembic check; then
        echo "==> alembic check: no schema drift"
        exit 0
    fi
    echo "schema drift detected: models and migrations disagree" >&2
    exit 1
fi

# alembic < 1.9 fallback: autogenerate a throwaway revision and fail if it
# would change anything (an empty revision contains no `op.` calls).
echo "==> alembic check unavailable; falling back to autogenerate diff"
alembic revision --autogenerate -m ci_check --rev-id=ci_check

gen_file="$(ls alembic/versions/ci_check*.py 2>/dev/null | head -n 1 || true)"
if [ -z "$gen_file" ]; then
    echo "schema drift detection failed: autogenerate produced no revision" >&2
    exit 1
fi

if grep -q "op\." "$gen_file"; then
    rm -f "$gen_file"
    echo "schema drift detected: models and migrations disagree" >&2
    exit 1
fi

rm -f "$gen_file"
echo "==> autogenerate diff: no schema drift"
exit 0
