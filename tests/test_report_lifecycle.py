"""Report lifecycle (Phase 6, spec §5 steps 9-12 + §10): draft → finalized →
approved, warning acknowledgment gating, immutability of approved versions,
session-sourced drafting, terminology integration."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ai.base import (
    LLMCompletion,
    LLMProvider,
    PrivacyClass,
    ProviderCapabilities,
    ProviderKind,
)
from ai.registry import ProviderDescriptor

TRANSCRIPT = (
    "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است. "
    "دوز 40 mg furosemide."
)


def _draft(client: TestClient, encounter: str = "enc-1", **overrides):
    body = {"transcript": TRANSCRIPT, **overrides}
    return client.post(f"/api/v1/reports/{encounter}/draft", json=body)


def _hallucination_llm(text: str):
    class _L(LLMProvider):
        @property
        def capabilities(self):
            return ProviderCapabilities(privacy=PrivacyClass.LOCAL)

        async def complete(self, messages, *, model=None, temperature=0.2,
                           max_tokens=None, json_mode=False):
            return LLMCompletion(text=text, model="scripted", usage=None, meta={})

    return _L()


def _register(client: TestClient, name: str, text: str):
    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name=name,
            kind=ProviderKind.LLM,
            capabilities=ProviderCapabilities(privacy=PrivacyClass.LOCAL),
            factory=lambda cfg: _hallucination_llm(text),
            configured=lambda cfg: True,
        )
    )


def test_draft_is_stored_and_lifecycle_flows_to_approved(client: TestClient):
    resp = _draft(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rid = body["report_id"]
    assert rid.startswith("rpt_")
    assert body["status"] == "draft"
    assert body["report_id"] == body["draft_id"]  # compat alias

    # GET it back
    got = client.get(f"/api/v1/reports/{rid}")
    assert got.status_code == 200
    assert got.json()["status"] == "draft"
    assert got.json()["sections"][0]["id"] == "chief_complaint"

    # edit a section while draft
    patched = client.patch(
        f"/api/v1/reports/{rid}",
        json={"sections": {"chief_complaint": "ویرایش پزشک: درد قفسه سینه."}},
    )
    assert patched.status_code == 200
    assert patched.json()["sections"][0]["markdown"].startswith("ویرایش پزشک")

    # finalize → approve
    fin = client.post(f"/api/v1/reports/{rid}/finalize")
    assert fin.status_code == 200
    assert fin.json()["status"] == "finalized"
    app = client.post(f"/api/v1/reports/{rid}/approve")
    assert app.status_code == 200
    assert app.json()["status"] == "approved"

    # list by encounter
    listing = client.get("/api/v1/reports?encounter_id=enc-1")
    assert listing.status_code == 200
    assert any(r["report_id"] == rid for r in listing.json()["reports"])


def test_approved_report_is_immutable(client: TestClient):
    rid = _draft(client, "enc-2").json()["report_id"]
    client.post(f"/api/v1/reports/{rid}/finalize")
    client.post(f"/api/v1/reports/{rid}/approve")

    patched = client.patch(
        f"/api/v1/reports/{rid}", json={"sections": {"chief_complaint": "tamper"}}
    )
    assert patched.status_code == 409
    assert "immutable" in patched.json()["error"]["message"]

    # approve-again / finalize-again are illegal transitions
    assert client.post(f"/api/v1/reports/{rid}/approve").status_code == 409
    assert client.post(f"/api/v1/reports/{rid}/finalize").status_code == 409
    assert client.post(f"/api/v1/reports/{rid}/reopen").status_code == 409


def test_amendment_forks_new_draft_from_approved(client: TestClient):
    rid = _draft(client, "enc-3").json()["report_id"]
    client.post(f"/api/v1/reports/{rid}/finalize")
    client.post(f"/api/v1/reports/{rid}/approve")
    amend = client.post(f"/api/v1/reports/{rid}/amend")
    assert amend.status_code == 201
    new = amend.json()
    assert new["report_id"] != rid
    assert new["status"] == "draft"
    assert new["amended_from"] == rid
    # original untouched
    assert client.get(f"/api/v1/reports/{rid}").json()["status"] == "approved"


def test_cannot_skip_finalize(client: TestClient):
    rid = _draft(client, "enc-4").json()["report_id"]
    resp = client.post(f"/api/v1/reports/{rid}/approve")
    assert resp.status_code == 409
    assert "finalize first" in resp.json()["error"]["message"]


def test_reopen_finalized_to_draft(client: TestClient):
    rid = _draft(client, "enc-5").json()["report_id"]
    assert client.post(f"/api/v1/reports/{rid}/finalize").status_code == 200
    assert client.post(f"/api/v1/reports/{rid}/reopen").status_code == 200
    assert client.get(f"/api/v1/reports/{rid}").json()["status"] == "draft"


def test_unknown_report_404(client: TestClient):
    assert client.get("/api/v1/reports/rpt_missing").status_code == 404
    assert client.post("/api/v1/reports/rpt_missing/finalize").status_code == 404


def test_unknown_section_edit_rejected(client: TestClient):
    rid = _draft(client, "enc-6").json()["report_id"]
    resp = client.patch(f"/api/v1/reports/{rid}", json={"sections": {"nonexistent": "x"}})
    assert resp.status_code == 409


def _critical_draft(client: TestClient, encounter: str) -> str:
    hallucinated = json.dumps(
        {
            "sections": {
                "chief_complaint": "دوز 100 mg furosemide تجویز شد.",
                "history": "[[MISSING]]", "exam": "[[MISSING]",
                "assessment": "[[MISSING]]", "plan": "[[MISSING]]",
            }
        },
        ensure_ascii=False,
    )
    _register(client, "halluc2", hallucinated)
    resp = _draft(client, encounter, provider="halluc2")
    assert resp.status_code == 200
    body = resp.json()
    criticals = [w for w in body["warnings"] if w["severity"] == "critical"]
    assert criticals, "fabricated dose must produce a critical warning"
    return body["report_id"]


def test_critical_warnings_block_finalize_until_acknowledged(client: TestClient):
    rid = _critical_draft(client, "enc-7")
    blocked = client.post(f"/api/v1/reports/{rid}/finalize")
    assert blocked.status_code == 409
    assert "unacknowledged critical" in blocked.json()["error"]["message"]

    # find the critical warning id and acknowledge without justification → 409
    warnings = client.get(f"/api/v1/reports/{rid}").json()["warnings"]
    critical = next(w for w in warnings if w["severity"] == "critical")
    bad = client.post(
        f"/api/v1/reports/{rid}/acknowledge",
        json={"warning_id": critical["id"], "justification": "ok"},
    )
    assert bad.status_code in (409, 422)  # too short (store or schema gate)

    good = client.post(
        f"/api/v1/reports/{rid}/acknowledge",
        json={"warning_id": critical["id"], "justification": "checked chart, dose is correct"},
    )
    assert good.status_code == 200
    acked = [w for w in good.json()["warnings"] if w["id"] == critical["id"]][0]
    assert acked["acknowledged"] is True
    assert "checked chart" in acked["acknowledgment_justification"]

    # all criticals acked → finalize works, then approve
    remaining = [
        w for w in good.json()["warnings"]
        if w["severity"] == "critical" and not w["acknowledged"]
    ]
    for w in remaining:
        client.post(
            f"/api/v1/reports/{rid}/acknowledge",
            json={"warning_id": w["id"], "justification": "reviewed against dictation"},
        )
    assert client.post(f"/api/v1/reports/{rid}/finalize").status_code == 200
    assert client.post(f"/api/v1/reports/{rid}/approve").status_code == 200


def test_acknowledge_unknown_warning_rejected(client: TestClient):
    rid = _draft(client, "enc-8").json()["report_id"]
    resp = client.post(
        f"/api/v1/reports/{rid}/acknowledge",
        json={"warning_id": f"{rid}:w99", "justification": "does not exist"},
    )
    assert resp.status_code in (404, 409)  # unknown warning id


def test_draft_from_session_transcript(client: TestClient):
    """Session-sourced drafting: markers become structure, terminology view
    feeds the prompt, the report links to the session."""
    store = client.app.state.transcript_store
    from ai.base import TranscriptSegment

    store.open("sess-42", user_id="u1", provider="mock", language="fa")
    store.append_final(
        "sess-42",
        TranscriptSegment(text="بیمار با ام آر آی مغز مراجعه کرد.", start_ms=0, end_ms=500,
                          is_final=True),
    )
    store.append_marker("sess-42", "paragraph")
    store.append_final(
        "sess-42",
        TranscriptSegment(text="دوز 40 mg furosemide.", start_ms=600, end_ms=900,
                          is_final=True),
    )
    resp = _draft(client, "enc-9", session_id="sess-42")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transcript_source"] == "session"
    assert body["session_id"] == "sess-42"
    assert body["terminology_substitutions"] >= 1  # ام آر آی → MRI
    first = body["sections"][0]["markdown"]
    assert "MRI" in first  # normalized view reached the prompt (mock echoes)
    assert "\n\n" in first  # paragraph marker became a break


def test_draft_from_empty_session_rejected(client: TestClient):
    client.app.state.transcript_store.open(
        "sess-empty", user_id="u1", provider="mock", language="fa"
    )
    resp = _draft(client, "enc-10", session_id="sess-empty")
    assert resp.status_code == 422


def test_draft_from_missing_session_404(client: TestClient):
    resp = _draft(client, "enc-11", session_id="sess-nope")
    assert resp.status_code == 404


def test_draft_requires_transcript_or_session(client: TestClient):
    resp = client.post("/api/v1/reports/enc-12/draft", json={})
    assert resp.status_code == 422


def test_draft_with_server_template_key(client: TestClient):
    resp = _draft(client, "enc-13", template_key="soap-note")
    assert resp.status_code == 200
    body = resp.json()
    assert body["template_key"] == "soap-note"
    assert [s["id"] for s in body["sections"]] == [
        "subjective", "objective", "assessment", "plan"
    ]


def test_draft_with_unknown_template_key_404(client: TestClient):
    resp = _draft(client, "enc-14", template_key="nope-template")
    assert resp.status_code == 404


def test_draft_reports_carry_severity_in_warnings(client: TestClient):
    body = _draft(client, "enc-15").json()
    for w in body["warnings"]:
        assert "severity" in w and "id" in w and "acknowledged" in w
