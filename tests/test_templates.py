"""Report-template service + API (Phase 6, spec §10): data-driven CRUD,
immutable built-ins, fork, LLM-assisted extraction (mock-verified)."""
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


def test_builtin_templates_are_seeded_as_data(client: TestClient):
    resp = client.get("/api/v1/report-templates")
    assert resp.status_code == 200
    body = resp.json()
    keys = {t["key"] for t in body["templates"]}
    assert {
        "general-clinical-note", "soap-note", "radiology-report",
        "ultrasound-report", "ct-report", "mri-report",
    } <= keys
    for t in body["templates"]:
        if t["builtin"]:
            assert t["sections"], t["key"]
    soap = next(t for t in body["templates"] if t["key"] == "soap-note")
    assert [s["id"] for s in soap["sections"]] == [
        "subjective", "objective", "assessment", "plan"
    ]


def test_get_single_template(client: TestClient):
    resp = client.get("/api/v1/report-templates/radiology-report")
    assert resp.status_code == 200
    assert resp.json()["category"] == "radiology"
    assert resp.json()["builtin"] is True


def test_create_update_delete_custom_template(client: TestClient):
    create = client.post(
        "/api/v1/report-templates",
        json={
            "key": "cardiology-clinic",
            "name": "کلینیک قلب",
            "sections": [
                {"id": "hpi", "title": "شرح حال", "instruction": "onset/duration"},
                {"id": "plan", "title": "طرح", "required": True, "format_style": "numbered"},
            ],
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["builtin"] is False and body["version"] == 1

    # duplicate key rejected
    dup = client.post(
        "/api/v1/report-templates",
        json={"key": "cardiology-clinic", "name": "x", "sections": [{"id": "a", "title": "A"}]},
    )
    assert dup.status_code == 422

    upd = client.patch(
        "/api/v1/report-templates/cardiology-clinic",
        json={"name": "کلینیک قلب (ویرایش شده)"},
    )
    assert upd.status_code == 200
    assert upd.json()["version"] == 2

    deleted = client.delete("/api/v1/report-templates/cardiology-clinic")
    assert deleted.status_code == 204
    assert client.get("/api/v1/report-templates/cardiology-clinic").status_code == 404


def test_builtin_templates_are_immutable(client: TestClient):
    upd = client.patch(
        "/api/v1/report-templates/soap-note", json={"name": "hacked"}
    )
    assert upd.status_code == 422
    assert "immutable" in upd.json()["error"]["message"]
    assert client.delete("/api/v1/report-templates/soap-note").status_code == 422


def test_fork_builtin_into_custom(client: TestClient):
    resp = client.post(
        "/api/v1/report-templates/ct-report/fork",
        json={"new_key": "ct-er-protocol", "new_name": "CT ER"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["builtin"] is False
    assert [s["id"] for s in body["sections"]] == [
        "clinical_history", "technique", "findings", "impression", "recommendations"
    ]


def test_section_validation_rejects_bad_ids(client: TestClient):
    resp = client.post(
        "/api/v1/report-templates",
        json={"key": "bad-one", "name": "x", "sections": [{"id": "Bad ID!", "title": "x"}]},
    )
    assert resp.status_code == 422
    resp2 = client.post(
        "/api/v1/report-templates",
        json={"key": "bad-two", "name": "x", "sections": []},
    )
    assert resp2.status_code == 422


def test_unknown_template_404(client: TestClient):
    assert client.get("/api/v1/report-templates/nope").status_code == 404


# ---- LLM extraction (Phlox concept) --------------------------------------------


class _MockExtractLlm(LLMProvider):
    def __init__(self, text: str):
        self._text = text
        self._caps = ProviderCapabilities(privacy=PrivacyClass.LOCAL)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    async def complete(self, messages, *, model=None, temperature=0.2, max_tokens=None,
                       json_mode=False):
        return LLMCompletion(text=self._text, model="extract-mock", usage=None, meta={})


EXTRACT_OUTPUT = json.dumps(
    {
        "suggested_name": "رادیولوژی اورژانس",
        "note_type": "radiology",
        "sections": [
            {"id": "clinical history", "title": "شرح حال", "instruction": "indication",
             "required": True, "format_style": "narrative"},
            {"id": "findings", "title": "یافته‌ها", "instruction": "", "required": False,
             "format_style": "bullets"},
        ],
    },
    ensure_ascii=False,
)


def test_extract_returns_unsaved_draft_template(client: TestClient):
    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name="extractor",
            kind=ProviderKind.LLM,
            capabilities=_MockExtractLlm(EXTRACT_OUTPUT).capabilities,
            factory=lambda cfg: _MockExtractLlm(EXTRACT_OUTPUT),
            configured=lambda cfg: True,
        )
    )
    resp = client.post(
        "/api/v1/report-templates/extract",
        json={"example_note": "شرح حال: ..." + "x" * 100, "provider": "extractor"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "extractor"
    assert body["edited_by_clinician_required"] is True
    ids = [s["id"] for s in body["sections"]]
    assert ids == ["clinical_history", "findings"]  # id sanitized
    assert body["sections"][0]["required"] is True
    # nothing was persisted
    keys = {t["key"] for t in client.get("/api/v1/report-templates").json()["templates"]}
    assert "رادیولوژی-اورژانس" not in keys
    # clinician can then explicitly create it
    create = client.post(
        "/api/v1/report-templates",
        json={
            "key": "er-radiology",
            "name": body["suggested_name"],
            "sections": body["sections"],
        },
    )
    assert create.status_code == 201


def test_extract_garbage_output_falls_back_to_mock_provider_chain(client: TestClient):
    client.app.state.ai_registry.register(
        ProviderDescriptor(
            name="garbled",
            kind=ProviderKind.LLM,
            capabilities=_MockExtractLlm("I am not JSON at all").capabilities,
            factory=lambda cfg: _MockExtractLlm("I am not JSON at all"),
            configured=lambda cfg: True,
        )
    )
    resp = client.post(
        "/api/v1/report-templates/extract",
        json={"example_note": "note " * 30, "provider": "garbled"},
    )
    # garbled fails, chain falls through to the built-in mock LLM which
    # answers the report-prompt contract (sections dict, not a list) → 502
    assert resp.status_code in (200, 502)
