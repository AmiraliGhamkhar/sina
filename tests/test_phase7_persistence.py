"""Phase 7 — persistence, credential auth, vault, audit dual-write.

Runs the whole app against a real sqlite+aiosqlite database (same engine
code path as PostgreSQL minus the server) through the public HTTP + WS
surface, plus direct repo assertions for things the API must never expose.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests.conftest import TEST_DEV_TOKEN, TEST_JWT_SECRET

ADMIN_PW = "bootstrap-admin-pw-12345"
FERNET_KEY = Fernet.generate_key().decode()


def make_db_settings(tmp_path, **overrides) -> Settings:  # noqa: F821
    base = dict(
        server={"env": "dev", "log_level": "WARNING"},
        auth={
            "dev_token": TEST_DEV_TOKEN,
            "jwt_secret": TEST_JWT_SECRET,
            "bootstrap_admin_password": ADMIN_PW,
            "lockout_seconds": 1,  # fast tests
        },
        audit={"log_file": str(tmp_path / "audit.jsonl"), "enabled": True},
        websocket={"heartbeat_seconds": 2, "max_session_minutes": 1},
        stt={"mock_interim_delay_s": 0.0},
        database={"url": f"sqlite+aiosqlite:///{tmp_path}/ms.db", "auto_create": True},
        security={"secret_encryption_key": FERNET_KEY},
        # login-flow tests do many requests; keep the auth bucket generous here
        rate_limit={"auth_per_minute": 100},
    )
    base.update(overrides)
    from api.config import Settings

    return Settings(**base)


@pytest.fixture
def db_app(tmp_path):
    from api.main import create_app

    return create_app(make_db_settings(tmp_path))


@pytest.fixture
def db_client(db_app):
    with TestClient(db_app) as c:
        yield c


def _login(client, username: str, password: str):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def _admin_headers(client) -> dict:
    resp = _login(client, "admin", ADMIN_PW)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _run(coro):
    """Run a repo coroutine in a fresh loop (aiosqlite/NullPool is per-op)."""
    return asyncio.run(coro)


def _poll(predicate, timeout_s: float = 5.0, interval: float = 0.1):
    """Background audit writer is asynchronous — poll until visible."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


# -- boot + seed ----------------------------------------------------------------


def test_db_boot_seeds_roles_templates_admin(db_client):
    templates = db_client.get("/api/v1/report-templates").json()["templates"]
    assert len(templates) >= 6  # built-ins materialized as durable rows
    resp = _login(db_client, "admin", ADMIN_PW)
    assert resp.status_code == 200
    me = db_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"}
    )
    assert me.json() == {"user_id": me.json()["user_id"], "role": "admin", "is_dev": False}


def test_seed_is_idempotent_across_restarts(tmp_path, db_client):
    # second app instance on the same database file must not duplicate
    from api.main import create_app

    with TestClient(create_app(make_db_settings(tmp_path))) as c2:
        templates = c2.get("/api/v1/report-templates").json()["templates"]
        keys = [t["key"] for t in templates]
        assert len(keys) == len(set(keys))
        # bootstrap skipped when the admin already exists
        assert _login(c2, "admin", ADMIN_PW).status_code == 200


def test_memory_mode_reports_capability_boundary(client, auth_headers):
    """Patients/encounters answer 501 DB_NOT_CONFIGURED without a database."""
    resp = client.get("/api/v1/patients", headers=auth_headers)
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "DB_NOT_CONFIGURED"


# -- credential auth -----------------------------------------------------------------


def test_login_lockout_after_repeated_failures(db_client):
    for _ in range(4):
        assert _login(db_client, "admin", "wrong-password").status_code == 401
    # 5th failure trips the lockout (429), and even the correct password is
    # refused while locked
    assert _login(db_client, "admin", "wrong-password").status_code == 429
    assert _login(db_client, "admin", ADMIN_PW).status_code == 429
    # ...until the (short, test-tuned) lockout window passes
    time.sleep(1.2)
    assert _login(db_client, "admin", ADMIN_PW).status_code == 200


def test_refresh_rotation_and_reuse_revokes_all_sessions(db_client):
    first = _login(db_client, "admin", ADMIN_PW).json()
    second = db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]
    # reuse of the consumed token = assumed theft → all sessions revoked
    reuse = db_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert reuse.status_code == 401
    # the legitimate successor was revoked too
    assert (
        db_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": second.json()["refresh_token"]}
        ).status_code
        == 401
    )


def test_logout_consumes_refresh_token(db_client):
    pair = _login(db_client, "admin", ADMIN_PW).json()
    out = db_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {pair['access_token']}"},
        json={"refresh_token": pair["refresh_token"]},
    )
    assert out.status_code == 200
    assert (
        db_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )


def test_admin_can_create_and_disable_users(db_client):
    admin = _admin_headers(db_client)
    created = db_client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"username": "dr-rahimi", "password": "physician-pw-123", "role": "physician"},
    )
    assert created.status_code == 201, created.text
    # duplicate rejected
    dup = db_client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"username": "dr-rahimi", "password": "physician-pw-456", "role": "physician"},
    )
    assert dup.status_code == 422
    # new user can log in and is NOT admin
    login = _login(db_client, "dr-rahimi", "physician-pw-123")
    assert login.status_code == 200
    clinician = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert db_client.get("/api/v1/admin/users", headers=clinician).status_code == 403
    # deactivation blocks login
    user_id = created.json()["user_id"]
    patch = db_client.patch(
        f"/api/v1/admin/users/{user_id}", headers=admin, json={"is_active": False}
    )
    assert patch.status_code == 200
    assert _login(db_client, "dr-rahimi", "physician-pw-123").status_code == 403


# -- patients / encounters -------------------------------------------------------


def test_patient_and_encounter_lifecycle(db_client):
    admin = _admin_headers(db_client)
    patient = db_client.post(
        "/api/v1/patients",
        headers=admin,
        json={"full_name": "مریم احمدی", "mrn": "MRN-1001", "sex": "female"},
    )
    assert patient.status_code == 201, patient.text
    pid = patient.json()["patient_id"]
    encounter = db_client.post(
        "/api/v1/encounters",
        headers=admin,
        json={"patient_id": pid, "encounter_date": "2026-09-11", "privacy_required": True},
    )
    assert encounter.status_code == 201
    # search finds it by MRN (case-insensitive) — PHI reads stay authenticated
    unauth = db_client.get("/api/v1/patients", params={"q": "MRN-1001"})
    assert unauth.status_code == 401
    hits = db_client.get(
        "/api/v1/patients", params={"q": "mrn-1001"}, headers=admin
    ).json()
    assert hits["total"] == 1 and hits["patients"][0]["patient_id"] == pid
    # encounter view carries linked reports (none yet)
    view = db_client.get(
        f"/api/v1/encounters/{encounter.json()['encounter_id']}", headers=admin
    )
    assert view.status_code == 200 and view.json()["reports"] == []
    # unknown patient on encounter create → 404, not a dangling reference
    assert (
        db_client.post(
            "/api/v1/encounters", headers=admin, json={"patient_id": "pat_missing"}
        ).status_code
        == 404
    )


# -- transcript + report persistence ------------------------------------------------


def test_ws_session_flushes_transcript_to_db(db_client, db_app):
    from ai.stt.mock import DEFAULT_SCRIPT

    with db_client.websocket_connect(
        "/ws/v1/transcribe?token=dev-token-1234567890"
    ) as ws:
        ws.send_json({"v": 1, "type": "session.start"})
        started = ws.receive_json()
        assert started["type"] == "session.started"
        session_id = started["session_id"]
        for _ in range(len(DEFAULT_SCRIPT)):
            frame = ws.receive_json()
            while frame["type"] != "transcript.final":
                frame = ws.receive_json()
        ws.send_json({"v": 1, "type": "session.stop"})
        completed = ws.receive_json()
        assert completed["type"] == "session.completed"

    repo = db_app.state.transcript_repo

    def row():
        return _run(repo.get(session_id))

    transcript = _poll(row)
    assert transcript is not None
    assert transcript.status == "completed"
    assert transcript.user_id == "dev"
    # the durable snapshot must serve the same REST shape after eviction
    db_app.state.transcript_store._by_session.clear()  # noqa: SLF001 — eviction
    snapshot = _run(repo.snapshot(session_id))
    assert snapshot is not None and snapshot["segment_count"] == len(DEFAULT_SCRIPT)


def test_reports_survive_memory_eviction_and_restart(tmp_path, db_client):
    """Draft → approve in app1; the durable row serves GET after the LRU
    cache is dropped AND after a full app restart."""
    from tests.test_reports_draft_api import _post_draft  # same mock LLM path

    draft = _post_draft(db_client)
    assert draft.status_code == 200, draft.text
    report_id = draft.json()["report_id"]
    encounter = draft.json()["encounter_id"]
    assert (
        db_client.post(f"/api/v1/reports/{report_id}/finalize", headers={}).status_code == 200
    )

    # LRU eviction: memory cache gone, DB answers
    db_client.app.state.report_store._by_id.clear()  # noqa: SLF001 — eviction
    evicted = db_client.get(f"/api/v1/reports/{report_id}")
    assert evicted.status_code == 200
    assert evicted.json()["status"] in ("finalized", "approved")

    # restart: brand-new app instance on the same database file
    from api.main import create_app

    with TestClient(create_app(make_db_settings(tmp_path))) as c2:
        reloaded = c2.get(f"/api/v1/reports/{report_id}")
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json()["status"] in ("finalized", "approved")
        assert reloaded.json()["encounter_id"] == encounter
        listed = c2.get(f"/api/v1/reports?encounter_id={encounter}").json()
        assert any(r["report_id"] == report_id for r in listed["reports"])


def test_custom_template_survives_restart(tmp_path, db_client):
    made = db_client.post(
        "/api/v1/report-templates",
        json={"key": "cardio_follow_up", "name": "Cardio Follow-Up",
              "sections": [{"id": "plan", "title": "Plan", "required": True}]},
    )
    assert made.status_code == 201, made.text
    from api.main import create_app

    with TestClient(create_app(make_db_settings(tmp_path))) as c2:
        keys = [t["key"] for t in c2.get("/api/v1/report-templates").json()["templates"]]
        assert made.json()["key"] in keys


def test_report_revisions_endpoint(db_client):
    from tests.test_reports_draft_api import _post_draft

    report_id = _post_draft(db_client).json()["report_id"]
    db_client.post(f"/api/v1/reports/{report_id}/finalize")
    revisions = db_client.get(f"/api/v1/reports/{report_id}/revisions")
    assert revisions.status_code == 200
    actions = [r["action"] for r in revisions.json()["revisions"]]
    assert "draft_generated" in actions and "finalized" in actions


# -- audit dual-write ----------------------------------------------------------------


def test_audit_dual_write_db_and_file(db_client, tmp_path):
    _login(db_client, "admin", ADMIN_PW)  # emits 'login' ok
    _login(db_client, "admin", "wrong")  # emits 'login' failure
    admin = _admin_headers(db_client)

    def db_rows():
        rows = db_client.get(
            "/api/v1/admin/audit", params={"event": "login", "limit": 10}, headers=admin
        ).json()["events"]
        return rows if len(rows) >= 2 else []

    rows = _poll(db_rows)
    assert len(rows) >= 2
    oks = [r for r in rows if r["payload"].get("ok") is True]
    fails = [r for r in rows if r["payload"].get("ok") is False]
    assert oks and fails
    # usernames/passwords never land in the audit payload
    assert all("admin" not in (r["payload"] or {}).get("reason", "") for r in rows)

    # JSONL mirror still written (file path from settings)
    import json
    import pathlib

    jsonl = pathlib.Path(tmp_path / "audit.jsonl")
    assert jsonl.exists()
    file_events = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    assert any(e["event"] == "login" for e in file_events)


def test_admin_audit_query_requires_admin(db_client):
    resp = db_client.get("/api/v1/admin/audit")
    assert resp.status_code == 401


# -- provider secret vault ------------------------------------------------------------


def test_provider_secret_roundtrip_and_isolation(db_client, db_app):
    admin = _admin_headers(db_client)
    raw = "dg-live-key-abcdef0123456789"
    put = db_client.put(
        "/api/v1/admin/providers/stt/deepgram/secret", headers=admin, json={"secret": raw}
    )
    assert put.status_code == 204, put.text

    # the stored value is Fernet ciphertext, never the raw key
    ciphertext = _run(db_app.state.provider_repo.get_secret("stt", "deepgram"))
    assert ciphertext and ciphertext != raw
    assert db_app.state.secret_vault.decrypt(ciphertext) == raw

    # vault secret makes the provider report configured without env config
    def _stt_providers() -> list:
        return db_client.get("/api/v1/providers", params={"kind": "stt"}).json()

    deepgram = next(p for p in _stt_providers() if p["name"] == "deepgram")
    assert deepgram["configured"] is True

    # the raw secret must never appear in any API response body
    assert raw not in db_client.get("/api/v1/providers", params={"kind": "stt"}).text

    # clearing deconfigures again
    assert (
        db_client.delete("/api/v1/admin/providers/stt/deepgram/secret", headers=admin).status_code
        == 204
    )
    deepgram = next(p for p in _stt_providers() if p["name"] == "deepgram")
    assert deepgram["configured"] is False


def test_secret_storage_requires_vault_key(tmp_path):
    from api.main import create_app

    settings = make_db_settings(tmp_path, security={"secret_encryption_key": None})
    with TestClient(create_app(settings)) as c:
        admin = _admin_headers(c)
        resp = c.put(
            "/api/v1/admin/providers/stt/deepgram/secret",
            headers=admin,
            json={"secret": "dg-live-key-abcdef0123456789"},
        )
        assert resp.status_code == 501
        assert resp.json()["error"]["code"] == "DB_NOT_CONFIGURED"


def test_ai_request_ledger_records_drafts(db_client):
    from tests.test_reports_draft_api import _post_draft

    assert _post_draft(db_client).status_code == 200
    admin = _admin_headers(db_client)

    def rows():
        return db_client.get(
            "/api/v1/admin/ai-requests", params={"kind": "llm"}, headers=admin
        ).json()["requests"]

    assert _poll(rows)


# -- rate limiting ----------------------------------------------------------------


def test_auth_rate_limit_429_with_retry_after(tmp_path):
    from api.main import create_app

    settings = make_db_settings(tmp_path, rate_limit={"auth_per_minute": 3, "enabled": True})
    with TestClient(create_app(settings)) as c:
        for _ in range(3):
            assert _login(c, "admin", ADMIN_PW).status_code == 200
        limited = _login(c, "admin", ADMIN_PW)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert "Retry-After" in limited.headers
        # non-auth paths use the wider general bucket — still fine
        assert c.get("/api/v1/report-templates").status_code == 200


def test_rate_limiter_degrades_open_on_redis_failure():
    from api.services.rate_limit import InProcessBackend, RateLimiter

    class BrokenBackend:
        async def hit(self, key, window_s, limit):
            raise ConnectionError("redis down")

    limiter = RateLimiter(BrokenBackend(), enabled=True)
    # must NOT raise — degrade open (lockout guard remains as second layer)
    import asyncio as _a

    _a.run(
        limiter.check_request(
            path="/api/v1/auth/login",
            client_ip="1.2.3.4",
            user_id=None,
            per_minute=10,
            auth_per_minute=10,
            window_s=60,
        )
    )
    assert isinstance(limiter._backend, BrokenBackend) or isinstance(
        limiter._backend, InProcessBackend
    )


def test_ws_per_user_session_cap(db_client):
    """Concurrent WS sessions per user are capped (spec §14)."""
    import contextlib

    cap = db_client.app.state.settings.rate_limit.ws_sessions_per_user
    with contextlib.ExitStack() as stack:
        for _ in range(cap):
            ws = stack.enter_context(
                db_client.websocket_connect("/ws/v1/transcribe?token=dev-token-1234567890")
            )
            ws.send_json({"v": 1, "type": "session.start"})
            assert ws.receive_json()["type"] == "session.started"
        with db_client.websocket_connect(
            "/ws/v1/transcribe?token=dev-token-1234567890"
        ) as over:
            over.send_json({"v": 1, "type": "session.start"})
            frame = over.receive_json()
            assert frame["type"] == "error"
            assert frame["code"] == "RATE_LIMITED"
