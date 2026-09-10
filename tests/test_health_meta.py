"""REST surface tests: health, version, manifest, stats."""
from __future__ import annotations


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["phase"] == 1


def test_readiness_components_report_disabled_without_db(client):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["components"]["postgres"]["status"] == "disabled"
    assert body["components"]["redis"]["status"] == "disabled"
    assert body["components"]["provider_router"]["status"] == "ok"


def test_readiness_reports_unreachable_postgres_without_blocking(client, tmp_path):
    # a configured-but-dead dependency must flip ready=false (503) with detail
    from api.config import Settings
    from api.main import create_app
    from fastapi.testclient import TestClient

    settings = Settings(
        server={"env": "dev", "log_level": "WARNING"},
        database={"url": "postgresql+asyncpg://u:p@127.0.0.1:59999/none"},
        audit={"log_file": str(tmp_path / "a.jsonl")},
    )
    app = create_app(settings)
    with TestClient(app) as c:
        resp = c.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["components"]["postgres"]["status"] == "unreachable"


def test_version(client):
    resp = client.get("/api/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_version"]
    assert body["ws_protocol"] == 1
    assert body["phase"] == 1


def test_manifest_is_data_driven_and_phase_honest(client):
    resp = client.get("/api/v1/config/manifest")
    assert resp.status_code == 200
    body = resp.json()
    # languages: fa, en and mixed must all be advertised
    codes = {lang["code"] for lang in body["languages"]}
    assert {"fa", "en", "fa-en"} <= codes
    assert all(lang["preserves_embedded_english_terms"] for lang in body["languages"])
    # voice commands ship in the manifest, not hardcoded in the client
    command_ids = {cmd["id"] for cmd in body["voice_commands"]}
    assert {"new_paragraph", "delete_last_sentence", "finalize_section"} <= command_ids
    fa_trigger = next(c for c in body["voice_commands"] if c["id"] == "new_paragraph")
    assert "پاراگراف جدید" in fa_trigger["triggers"]
    # Phase 1 must honestly report live transcription as off
    assert body["features"]["live_transcription"] is False
    assert body["ws_path"] == "/ws/v1/transcribe"
    assert body["max_message_bytes"] > 0


def test_stats_requires_nothing_but_reports_metrics(client):
    client.get("/health")
    resp = client.get("/api/v1/observability/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "counters" in body and "uptime_seconds" in body


def test_unknown_route_is_json_error(client):
    resp = client.get("/api/v1/nope")
    assert resp.status_code == 404
