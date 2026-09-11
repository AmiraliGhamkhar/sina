"""Phase 8 — Prometheus /metrics exposition + cost-ledger backfill."""
from __future__ import annotations

import asyncio
import pathlib

from fastapi.testclient import TestClient

from tests.conftest import TEST_DEV_TOKEN, make_settings
from tests.test_phase7_persistence import make_db_settings


def test_metrics_exposes_prometheus_text(client):
    # generate some traffic first
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/version").status_code == 200

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text

    # build info + uptime
    assert 'medicalscribe_build_info{api_version="0.1.0",phase="8"' in body
    assert "medicalscribe_uptime_seconds" in body
    # HTTP counters grouped under one metric with status labels
    assert "# TYPE medicalscribe_http_responses_total counter" in body
    assert 'status="200"' in body
    # route latency as a summary with quantiles + labels (seconds domain)
    assert 'quantile="0.95"' in body
    assert 'medicalscribe_http_request_duration_seconds{quantile="0.95",route="/health"}' in body
    assert 'route="/health"' in body
    assert 'medicalscribe_http_request_duration_seconds_count{route="/health"}' in body
    # in-memory mode reports db_mode 0
    assert "medicalscribe_db_mode 0" in body


def test_metrics_are_not_rate_limited(client):
    """The scrape path is exempt from the request bucket (Prometheus hits it
    every 15s; a throttled scrape would break monitoring, not the app)."""
    for _ in range(5):
        assert client.get("/metrics").status_code == 200


def test_metrics_never_leak_secrets(tmp_path):
    from api.main import create_app

    settings = make_settings(
        tmp_path,
        auth={"dev_token": TEST_DEV_TOKEN, "jwt_secret": "super-secret-jwt-value-xyz"},
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/health").status_code == 200
        body = c.get("/metrics").text
        assert "super-secret-jwt-value-xyz" not in body
        assert TEST_DEV_TOKEN not in body


def test_metrics_durable_mode_and_cost(tmp_path):
    from api.main import create_app

    with TestClient(create_app(make_db_settings(tmp_path))) as c:
        assert c.get("/health").status_code == 200
        body = c.get("/metrics").text
        assert "medicalscribe_db_mode 1" in body
        assert "medicalscribe_cost_tokens_today" in body
        assert "medicalscribe_cost_budget_tokens_per_day" in body


def test_counter_label_grouping():
    from api.main import create_app
    from api.routes.metrics import render_metrics

    app = create_app(make_settings(pathlib.Path("/tmp")))
    app.state.metrics.incr("llm_requests:mock", 2)
    app.state.metrics.incr("llm_requests:mock", 1)
    app.state.metrics.incr("llm_errors:primary")
    app.state.metrics.incr("voice_command:new_paragraph")
    app.state.metrics.incr("llm_tokens:openai:completion_tokens", 120)
    body = render_metrics(app)
    assert "# TYPE medicalscribe_llm_requests_total counter" in body
    assert 'medicalscribe_llm_requests_total{provider="mock"} 3' in body
    assert 'medicalscribe_llm_errors_total{attempt="primary"} 1' in body
    assert 'medicalscribe_voice_commands_total{command="new_paragraph"} 1' in body
    assert (
        'medicalscribe_llm_tokens_total{field="completion_tokens",provider="openai"} 120'
        in body
    )


def test_cost_ledger_backfill_and_budget_survives_restart(tmp_path):
    """Phase 8: a mid-day restart no longer resets the day's token spend —
    the ledger is backfilled from the durable ai_requests table."""
    from api.main import create_app
    from api.repositories.ai import AIRequestRepository

    with TestClient(create_app(make_db_settings(tmp_path))) as c:
        app = c.app
        repo = AIRequestRepository(app.state.db.sessionmaker)
        asyncio.run(
            repo.record(
                user_id="dev", kind="llm", task="generate_note", provider="openai",
                status="ok", total_tokens=400, completion_tokens=100, prompt_tokens=300,
            )
        )
        # the durable row exists, but the process-local ledger has not seen
        # it (it counts at request time) — exactly the restart gap backfill closes
        before = app.state.cost_ledger.snapshot()["tokens_today"]
        assert before == 0

    # restart on the same database: backfill restores the day's total
    with TestClient(create_app(make_db_settings(tmp_path))) as c:
        assert c.app.state.cost_ledger.snapshot()["tokens_today"] == 400
        # budget interaction: at-budget already soft-stops
        c.app.state.cost_ledger.budget_tokens_per_day = 400
        assert c.app.state.cost_ledger.snapshot()["exhausted"] is True
        body = c.get("/metrics").text
        assert "medicalscribe_cost_budget_exhausted 1" in body
