"""Auth contract tests: Phase 7 stub behavior + working dev paths."""
from __future__ import annotations

import pytest
from api.auth.deps import resolve_principal
from api.auth.tokens import TokenError, create_token, decode_token
from api.errors import ErrorCode


def test_login_without_db_is_501_with_stable_code(client):
    """Credential auth is implemented (Phase 7) but needs a database; the
    in-memory dev mode reports the capability boundary honestly."""
    resp = client.post("/api/v1/auth/login", json={"username": "doc", "password": "pw"})
    assert resp.status_code == 501
    body = resp.json()
    assert body["error"]["code"] == ErrorCode.AUTH_NOT_IMPLEMENTED
    assert "database" in body["error"]["message"].lower()


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


def test_refresh_with_garbage_token_is_401(client):
    """Phase 7: rotation is live even in dev mode (memory refresh store);
    an unknown token is simply unauthenticated, not a capability gap."""
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "x"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED


def test_dev_login_refresh_rotation_in_memory(client):
    """Dev-mode pair rotates: old refresh token is single-use."""
    login = client.post(
        "/api/v1/auth/login", json={"username": "dev", "password": "dev-token-1234567890"}
    )
    assert login.status_code == 200, login.text
    first = login.json()
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    # reuse of the consumed token is rejected
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reuse.status_code == 401


def test_logout_revokes_presented_refresh_token(client):
    login = client.post(
        "/api/v1/auth/login", json={"username": "dev", "password": "dev-token-1234567890"}
    )
    pair = login.json()
    out = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {pair['access_token']}"},
        json={"refresh_token": pair["refresh_token"]},
    )
    assert out.status_code == 200
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert reuse.status_code == 401


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


def test_production_fail_closed_on_weak_jwt_secret():
    """Security invariant: production Settings refuse to construct without a
    >=32-char JWT secret — on the MODEL, so no construction path can bypass
    it (the loader check is redundant belt-and-braces)."""
    import pydantic
    import pytest
    from api.config import Settings

    for bad in (None, "too-short"):
        with pytest.raises(pydantic.ValidationError):
            Settings(server={"env": "production"}, auth={"jwt_secret": bad})
    # strong secret + dev env are unaffected
    Settings(server={"env": "production"}, auth={"jwt_secret": "x" * 48})
    Settings(server={"env": "dev"})
