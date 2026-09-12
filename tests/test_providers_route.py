"""REST /providers listing: safe metadata only, config flags, optional probe,
and live model discovery for providers that declare it (9Router)."""
from __future__ import annotations

import pytest
from api.main import create_app
from fastapi.testclient import TestClient

from tests.conftest import make_settings


def test_provider_listing_exposes_names_and_capability_flags(client):
    resp = client.get("/api/v1/providers")
    assert resp.status_code == 200
    body = {p["name"] + ":" + p["kind"]: p for p in resp.json()}
    stt_mock = body["mock:stt"]
    assert stt_mock["configured"] is True
    assert stt_mock["capabilities"]["privacy_class"] == "local"
    assert stt_mock["health"] is None  # not probed by default
    llama = body["llama-server:llm"]
    # no MS_LLM__LLAMA_SERVER__BASE_URL in tests → visible but not routable
    assert llama["configured"] is False
    assert llama["health"] is None


def test_provider_listing_kind_filter(client):
    resp = client.get("/api/v1/providers", params={"kind": "llm"})
    assert resp.status_code == 200
    kinds = {p["kind"] for p in resp.json()}
    assert kinds == {"llm"}


def test_provider_health_probe_local_mock_ok(client):
    resp = client.get("/api/v1/providers", params={"kind": "stt", "probe": "health"})
    body = resp.json()
    mock = next(p for p in body if p["name"] == "mock")
    assert mock["health"]["ok"] is True


def test_provider_listing_never_leaks_secrets_or_config(client):
    resp = client.get("/api/v1/providers")
    text = resp.text.lower()
    for forbidden in ("api_key", "bearer", "secret", "jwt", "dev-token"):
        assert forbidden not in text


# ---- live model discovery (9Router) --------------------------------------------
#
# 9Router's catalog depends on which upstream accounts the operator connected,
# so it is discovered at request time. The adapter's own HTTP behaviour is
# covered in tests/test_nine_router.py; here we pin the *route* contract:
# descriptor lookup, configured-gate, error mapping and secret hygiene.

NR_BASE = "http://9router.test:20128"
NR_SECRET = "super-secret-9router-key"

LLM_CATALOG = [
    {
        "id": "claude/claude-sonnet-4",
        "kind": "llm",
        "owned_by": "claude",
        "context_length": 200000,
        "max_completion_tokens": 64000,
        "capabilities": {"tools": True},
    },
    {"id": "gemini/gemini-2.5-flash", "kind": "llm", "owned_by": "gemini"},
]
STT_CATALOG = [{"id": "groq/whisper-large-v3-turbo", "kind": "stt", "owned_by": "groq"}]


def _nr_app(tmp_path, **overrides):
    settings = make_settings(
        tmp_path,
        llm={
            "nine_router": {
                "base_url": NR_BASE,
                "model": "claude/claude-sonnet-4",
                "api_key": NR_SECRET,
            }
        },
        stt={
            "nine_router": {
                "base_url": NR_BASE,
                "model": "groq/whisper-large-v3-turbo",
                "api_key": NR_SECRET,
            }
        },
        **overrides,
    )
    return create_app(settings)


@pytest.fixture
def nr_client(tmp_path, monkeypatch):
    """A configured 9Router app whose catalog calls never touch the network."""
    import ai.llm.nine_router as llm_mod
    import ai.stt.nine_router as stt_mod

    async def fake_llm_fetch(client, base_url, *, kind="llm", **kwargs):
        assert base_url == f"{NR_BASE}/api"
        return LLM_CATALOG if kind == "llm" else [{"id": f"x/{kind}", "kind": kind}]

    async def fake_stt_fetch(client, base_url, *, kind="stt", **kwargs):
        assert base_url == f"{NR_BASE}/api"
        return STT_CATALOG

    monkeypatch.setattr(llm_mod, "fetch_models", fake_llm_fetch)
    monkeypatch.setattr(stt_mod, "fetch_models", fake_stt_fetch)
    with TestClient(_nr_app(tmp_path)) as c:
        yield c


def test_provider_listing_advertises_model_discovery(client, nr_client):
    """The flag lets the client offer a model picker only where one exists."""
    body = {(p["kind"], p["name"]): p for p in nr_client.get("/api/v1/providers").json()}
    assert body[("llm", "9router")]["supports_model_discovery"] is True
    assert body[("stt", "9router")]["supports_model_discovery"] is True
    assert body[("llm", "mock")]["supports_model_discovery"] is False
    assert body[("stt", "speechmatics")]["supports_model_discovery"] is False
    # unconfigured app still advertises the capability (it is static metadata)
    default = {p["name"]: p for p in client.get("/api/v1/providers", params={"kind": "llm"}).json()}
    assert default["9router"]["supports_model_discovery"] is True
    assert default["9router"]["configured"] is False


def test_provider_models_returns_llm_catalog(nr_client):
    resp = nr_client.get("/api/v1/providers/9router/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "9router"
    assert body["kind"] == "llm"
    # the configured model is echoed so the client can mark the active choice
    assert body["configured_model"] == "claude/claude-sonnet-4"
    ids = [m["id"] for m in body["models"]]
    assert ids == ["claude/claude-sonnet-4", "gemini/gemini-2.5-flash"]
    first = body["models"][0]
    assert first["owned_by"] == "claude"
    assert first["context_length"] == 200000
    assert first["max_completion_tokens"] == 64000
    assert first["capabilities"] == {"tools": True}
    assert body["models"][1].get("context_length") is None


def test_provider_models_stt_kind_uses_stt_adapter(nr_client):
    resp = nr_client.get("/api/v1/providers/9router/models", params={"kind": "stt"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "stt"
    assert body["configured_model"] == "groq/whisper-large-v3-turbo"
    assert [m["id"] for m in body["models"]] == ["groq/whisper-large-v3-turbo"]


def test_provider_models_rejects_unknown_kind(nr_client):
    assert nr_client.get("/api/v1/providers/9router/models", params={"kind": "video"}).status_code == 422


def test_provider_models_404_for_unknown_provider(nr_client):
    resp = nr_client.get("/api/v1/providers/nope/models")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_provider_models_501_when_provider_has_no_catalog(nr_client):
    """A provider without discovery fails loudly rather than returning []."""
    resp = nr_client.get("/api/v1/providers/mock/models")
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "CAPABILITY_NOT_IMPLEMENTED"


def test_provider_models_409_when_not_configured(client):
    """Default test app has no MS_LLM__NINE_ROUTER__BASE_URL."""
    listing = {p["name"]: p for p in client.get("/api/v1/providers", params={"kind": "llm"}).json()}
    assert listing["9router"]["configured"] is False

    resp = client.get("/api/v1/providers/9router/models")
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "PROVIDER_UNAVAILABLE"
    assert "MS_LLM__NINE_ROUTER__BASE_URL" in body["message"]


def test_provider_models_maps_provider_error_to_502(tmp_path, monkeypatch):
    """An unreachable router / bad key surfaces as 502 with the adapter's
    message, never a stack trace and never the credential."""
    import ai.llm.nine_router as llm_mod
    from ai.base import ProviderError

    async def boom(client, base_url, **kwargs):
        raise ProviderError("9router rejected the API key while listing llm models", provider="9router")

    monkeypatch.setattr(llm_mod, "fetch_models", boom)
    with TestClient(_nr_app(tmp_path)) as c:
        resp = c.get("/api/v1/providers/9router/models")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert NR_SECRET not in resp.text


def test_provider_models_never_leaks_secrets(nr_client):
    for path in ("/api/v1/providers", "/api/v1/providers/9router/models"):
        for params in (None, {"kind": "stt"}):
            resp = nr_client.get(path, params=params)
            assert resp.status_code == 200
            text = resp.text.lower()
            assert NR_SECRET.lower() not in text
            for forbidden in ("api_key", "bearer", "jwt_secret", "dev-token"):
                assert forbidden not in text, f"{path} leaked {forbidden}"
