"""POST /api/v1/reports/{encounter}/draft — mock-LLM e2e, grounded output,
missing-stays-missing, hallucination flagging, repair round-trip, privacy
wall + PHI redaction, token metering into /stats."""
from __future__ import annotations

import json
from collections.abc import Sequence

from fastapi.testclient import TestClient

from ai.base import (
    LLMCompletion,
    LLMMessage,
    LLMProvider,
    PrivacyClass,
    ProviderCapabilities,
    ProviderError,
)
from ai.registry import ProviderDescriptor

TRANSCRIPT = (
    "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است. "
    "سابقه hypertension. دوز 40 mg furosemide. کد ملی 1234567890."
)


def _post_draft(client: TestClient, encounter: str = "enc-42", **overrides):
    body = {"transcript": TRANSCRIPT, **overrides}
    return client.post(f"/api/v1/reports/{encounter}/draft", json=body)


class _ScriptedLlm(LLMProvider):
    """Returns queued strings in order (last one repeats)."""

    def __init__(self, outputs: list[str], privacy=PrivacyClass.LOCAL):
        self._outputs = outputs
        self.calls = 0
        self._caps = ProviderCapabilities(
            privacy=privacy, supports_streaming=False, latency_hint_ms=10
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        out = self._outputs[min(self.calls, len(self._outputs) - 1)]
        self.calls += 1
        if out is None:
            raise ProviderError("scripted outage", provider="scripted", retryable=True)
        return LLMCompletion(
            text=out,
            model="scripted-1",
            usage=None,
            meta={"json_mode": json_mode},
        )


def _register_llm(client: TestClient, name: str, provider: _ScriptedLlm) -> None:
    from ai.base import ProviderKind

    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name=name,
            kind=ProviderKind.LLM,
            capabilities=provider.capabilities,
            factory=lambda cfg, _p=provider: _p,
            configured=lambda cfg: True,
        )
    )


def test_draft_with_mock_llm_is_grounded_and_missing_stays_missing(client):
    resp = _post_draft(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "mock"
    assert body["draft_id"].startswith("drw_")
    assert body["encounter_id"] == "enc-42"
    ids = [s["id"] for s in body["sections"]]
    assert ids == ["chief_complaint", "history", "exam", "assessment", "plan"]
    first = body["sections"][0]
    assert TRANSCRIPT.strip() in first["markdown"]
    assert first["missing"] is False
    # the mock has no evidence for other sections → they stay marked missing
    assert body["missing_sections"] == ["history", "exam", "assessment", "plan"]
    assert all(s["missing"] for s in body["sections"][1:])
    assert body["warnings"] == []
    assert body["usage"] and body["usage"]["total_tokens"] > 0
    assert body["phi_redaction_applied"] is False


def test_draft_flags_fabricated_number(client):
    hallucinated = json.dumps(
        {"sections": {"chief_complaint": "دوز 80 mg furosemide تجویز شد.",
                      "history": "[[MISSING]]", "exam": "[[MISSING]]",
                      "assessment": "[[MISSING]]", "plan": "[[MISSING]]"}},
        ensure_ascii=False,
    )
    fake = _ScriptedLlm([hallucinated])
    _register_llm(client, "halluc", fake)
    resp = _post_draft(client, provider="halluc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "halluc"
    codes = {(w["code"], w["section_id"]) for w in body["warnings"]}
    assert ("unverified_number", "chief_complaint") in codes
    assert any("80" in w["message"] for w in body["warnings"])


def test_draft_repairs_invalid_json_once(client):
    valid = json.dumps(
        {"sections": {sid: "[[MISSING]]" for sid in
                      ("chief_complaint", "history", "exam", "assessment", "plan")}},
        ensure_ascii=False,
    )
    fake = _ScriptedLlm(["I refuse to be a JSON machine 🙃", valid])
    _register_llm(client, "repairy", fake)
    resp = _post_draft(client, provider="repairy")
    assert resp.status_code == 200, resp.text
    assert fake.calls == 2  # exactly one repair round-trip
    body = resp.json()
    assert body["missing_sections"] == [
        "chief_complaint", "history", "exam", "assessment", "plan"
    ]
    assert all(w["code"] != "unverified_number" for w in body["warnings"])


def test_draft_fails_after_second_invalid_json(client):
    fake = _ScriptedLlm(["junk one", "junk two"])
    _register_llm(client, "alwaysbad", fake)
    resp = _post_draft(client, provider="alwaysbad")
    assert resp.status_code == 502
    assert "repair" in resp.json()["error"]["message"]
    assert fake.calls == 2


def test_draft_provider_outage_maps_502_with_retry_hint(client):
    fake = _ScriptedLlm([None])
    _register_llm(client, "outage", fake)
    resp = _post_draft(client, provider="outage")
    assert resp.status_code == 502
    err = resp.json()["error"]
    assert err["code"] == "PROVIDER_UNAVAILABLE"
    assert err["details"]["retryable"] is True


def test_cloud_provider_gets_redacted_transcript_and_privacy_wall_holds(client):
    good = json.dumps(
        {"sections": {sid: "[[MISSING]]" for sid in
                      ("chief_complaint", "history", "exam", "assessment", "plan")}},
        ensure_ascii=False,
    )
    fake = _ScriptedLlm([good], privacy=PrivacyClass.CLOUD)
    _register_llm(client, "cloudllm", fake)

    # explicit cloud mode + privacy off → cloud allowed, PHI scrubbed first
    resp = _post_draft(client, provider="cloudllm", mode="cloud", privacy_required="false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "cloudllm"
    assert body["phi_redaction_applied"] is True
    # the fidelity check ran against the REDACTED transcript, so a draft that
    # quotes clinical numbers still passes while identifiers are gone

    # privacy ON: cloud is refused even with explicit preference
    resp2 = _post_draft(client, provider="cloudllm", mode="cloud", privacy_required="true")
    assert resp2.status_code == 200
    assert resp2.json()["provider"] == "mock"  # wall pins to LOCAL
    assert resp2.json()["phi_redaction_applied"] is False


def test_draft_audit_and_token_metering(client, tmp_path):
    resp = _post_draft(client)
    assert resp.status_code == 200
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "report_draft_generated" in audit
    for forbidden in ("furosemide", "ECG", "1234567890", TRANSCRIPT[:20]):
        assert forbidden not in audit  # content never enters audit
    stats = client.get("/api/v1/observability/stats").json()
    assert stats["counters"].get("llm_tokens:mock:total", 0) > 0
    assert stats["counters"].get("llm_requests:mock", 0) >= 1
    assert stats["latency"]["llm_latency_ms"]["count"] >= 1


def test_draft_validation_errors(client):
    bad = client.post("/api/v1/reports/bad id!/draft", json={"transcript": "x"})
    assert bad.status_code == 422
    empty = client.post("/api/v1/reports/enc/draft", json={"transcript": ""})
    assert empty.status_code == 422  # pydantic min_length
    huge = client.post(
        "/api/v1/reports/enc/draft", json={"transcript": "a" * 80_001}
    )
    assert huge.status_code == 422


def test_draft_custom_template_and_unknown_provider_fallback(client):
    resp = _post_draft(
        client,
        provider="no-such-llm",
        template={
            "name": "radiology",
            "sections": [
                {"id": "findings", "title": "یافته‌ها"},
                {"id": "impression", "title": "تصویربرداری نهایی"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock"  # non-strict preference falls back
    assert body["template_name"] == "radiology"
    assert [s["id"] for s in body["sections"]] == ["findings", "impression"]
    assert body["sections"][0]["markdown"] == TRANSCRIPT.strip()


def test_llm_adapters_visible_in_provider_listing(client):
    resp = client.get("/api/v1/providers", params={"kind": "llm"})
    entries = {p["name"]: p for p in resp.json()}
    for name in ("openai", "anthropic", "gemini"):
        assert entries[name]["configured"] is False
        assert entries[name]["capabilities"]["privacy_class"] == "cloud"
    assert entries["mock"]["configured"] is True
