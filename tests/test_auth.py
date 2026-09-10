"""Auth contract tests: Phase 7 stub behavior + working dev paths."""
from __future__ import annotations

import pytest
from api.auth.deps import resolve_principal
from api.auth.tokens import TokenError, create_token, decode_token
from api.errors import ErrorCode


def test_login_returns_501_with_stable_code_when_not_dev(client):
    resp = client.post("/api/v1/auth/login", json={"username": "doc", "password": "pw"})
    assert resp.status_code == 501
    body = resp.json()
    assert body["error"]["code"] == ErrorCode.AUTH_NOT_IMPLEMENTED
    assert "Phase 7" in body["error"]["message"]


def test_login_rejects_malformed_body(client):
    resp = client.post("/api/v1/auth/login", json={"username": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == ErrorCode.VALIDATION
    # never echo submitted values back
    assert "pw" not in resp.text


def test_dev_token_login_returns_usable_jwt_pair(client):
    resp = client.post("/api/v1/auth/login", json={"username": "dev", "password": "dev-token-1234567890"})
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 30 * 60
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    body = me.json()
    # a token minted by login is a normal JWT principal (is_dev marks the raw
    # static dev-token path, not issued credentials)
    assert body["user_id"] == "dev" and body["role"] == "admin"
    assert body["is_dev"] is False


def test_refresh_is_501(client, auth_headers):
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "x"})
    assert resp.status_code == 501


def test_me_requires_auth(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


def test_me_with_dev_bearer(client, auth_headers):
    resp = client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["is_dev"] is True


def test_token_roundtrip_and_expiry():
    token, jti = create_token("secret-x", sub="u1", role="physician", ttl_minutes=5)
    principal = decode_token("secret-x", token)
    assert principal.user_id == "u1" and principal.role == "physician"
    expired, _ = create_token("secret-x", sub="u1", role="physician", ttl_minutes=-1)
    with pytest.raises(TokenError):
        decode_token("secret-x", expired)
    with pytest.raises(TokenError):
        decode_token("wrong-secret", token)


def test_refresh_token_not_accepted_as_access():
    refresh, _ = create_token("s", sub="u", role="admin", ttl_minutes=1, typ="refresh")
    with pytest.raises(TokenError):
        decode_token("s", refresh, expected_typ="access")


def test_resolve_principal_guards(settings):
    # garbage bearer → UNAUTHENTICATED, not silent anon
    from api.errors import ApiError
    from starlette.datastructures import Headers, QueryParams

    with pytest.raises(ApiError):
        resolve_principal(Headers({"authorization": "Bearer bogus"}), QueryParams(), settings)
    # dev token header works in dev env
    p = resolve_principal(
        Headers({"authorization": "Bearer dev-token-1234567890"}), QueryParams(), settings
    )
    assert p is not None and p.is_dev
