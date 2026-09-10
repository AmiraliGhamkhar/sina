"""REST /providers listing: safe metadata only, config flags, optional probe."""
from __future__ import annotations


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
