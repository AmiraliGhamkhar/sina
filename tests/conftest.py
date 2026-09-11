"""Shared fixtures. Explicit Settings instances (init kwargs outrank env in
pydantic-settings) so tests never depend on the developer's .env."""
from __future__ import annotations

import pytest
from api.config import Settings
from api.main import create_app
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "test-jwt-secret-0123456789abcdef-xyz"
TEST_DEV_TOKEN = "dev-token-1234567890"


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        server={"env": "dev", "log_level": "WARNING"},
        auth={"dev_token": TEST_DEV_TOKEN, "jwt_secret": TEST_JWT_SECRET},
        audit={"log_file": str(tmp_path / "audit.jsonl"), "enabled": True},
        websocket={"heartbeat_seconds": 2, "max_session_minutes": 1},
        stt={"mock_interim_delay_s": 0.0},
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app(settings):
    # routes read settings from app.state (see api.auth.deps.get_app_settings),
    # so no get_settings override is needed here
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {TEST_DEV_TOKEN}"}
