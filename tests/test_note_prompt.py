"""Grounded prompt builder: block sentinels, PHI redaction, strict-JSON
reconciliation, repair message shape, and the light fidelity checks."""
from __future__ import annotations

import json

import pytest
from api.services.note_prompt import (
    MISSING_TOKEN,
    build_messages,
    grounding_warnings,
    parse_model_json,
    reconcile,
    redact_phi,
    repair_messages,
    resolve_sections,
)

TRANSCRIPT = "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است. دوز ۴۰ mg furosemide."


def _sections():
    return resolve_sections(None)


def test_prompt_contains_only_allowed_blocks():
    msgs = build_messages(
        transcript=TRANSCRIPT,
        sections=_sections(),
        patient_context={"name": "علی رضایی", "age": "54"},
    )
    user = msgs[1].content
    assert f"<<<TRANSCRIPT>>>\n{TRANSCRIPT}\n<<<END TRANSCRIPT>>>" in user
    assert "name: علی رضایی" in user and "age: 54" in user
    # no secrets, no unrelated text blocks
    assert "<<<" in user and user.count("<<<TRANSCRIPT>>>") == 1
    system = msgs[0].content
    assert MISSING_TOKEN in system
    for sid in ("chief_complaint", "plan"):
        assert sid in system
    # section specs serialized for the model
    specs = json.loads(user.split("<<<SECTIONS>>>")[1].split("<<<END SECTIONS>>>")[0])
    assert specs[0]["id"] == "chief_complaint"


def test_redaction_keeps_clinical_numbers_scrubs_identifiers():
    text = "کد ملی 1234567890 و موبایل 09123456789 و dose 40 mg و ۵ روز"
    out = redact_phi(text)
    assert "1234567890" not in out and "09123456789" not in out
    assert "REDACTED" in out
    assert "40 mg" in out and "۵ روز" in out  # clinical small numbers survive
    # +98 form
    assert "REDACTED" in redact_phi("تماس: 989121234567+")


def test_parse_model_json_tolerates_fences_and_prose():
    raw = 'Sure!\n```json\n{"sections": {"a": "text"}}\n```\nDone.'
    data = parse_model_json(raw)
    assert data["sections"]["a"] == "text"

    with pytest.raises(ValueError):
        parse_model_json("no json here at all")


def test_reconcile_orders_fills_and_drops():
    sections = resolve_sections(
        [{"id": "b", "title": "B"}, {"id": "a", "title": "A"}]
    )
    out = reconcile({"sections": {"a": " alpha ", "zz": "extra", "b": "  "}}, sections)
    assert list(out.keys()) == ["b", "a"]  # template order wins
    assert out["a"] == "alpha"
    assert out["b"] == MISSING_TOKEN  # blank → missing


def test_reconcile_requires_sections_object():
    with pytest.raises(ValueError):
        reconcile({"notes": {}}, _sections())


def test_repair_message_shape():
    msgs = build_messages(transcript="x", sections=_sections())
    rep = repair_messages(msgs, "not json", _sections())
    assert rep[2].role == "assistant" and rep[2].content == "not json"
    assert rep[3].role == "user"
    assert "chief_complaint" in rep[3].content


def test_grounding_warnings_fabricated_number_flagged():
    draft = {"chief_complaint": "دوز 80 mg furosemide داده شد.", "plan": MISSING_TOKEN}
    report = grounding_warnings(TRANSCRIPT, draft)
    codes = {w["code"] for w in report.warnings}
    assert "unverified_number" in codes
    assert any("80" in w["message"] for w in report.warnings)
    assert report.missing_sections == ["plan"]


def test_grounding_warnings_clean_verbatim_draft_passes():
    draft = {"chief_complaint": TRANSCRIPT, "history": MISSING_TOKEN}
    report = grounding_warnings(TRANSCRIPT, draft)
    assert report.warnings == []
    # persian digits normalized: ۴۰ in transcript, 40 written by model → ok
    draft2 = {"chief_complaint": TRANSCRIPT.replace("۴۰", "40"), "history": MISSING_TOKEN}
    assert grounding_warnings(TRANSCRIPT, draft2).warnings == []


def test_grounding_warnings_laterality_and_negation_tripwires():
    src = "درد سمت چپ است. ECG بدون تغییر."
    bad = {"exam": "درد سمت چپ و راست است. ECG بدون تغییر."}
    report = grounding_warnings(src, bad)
    assert any(w["code"] == "laterality_unverified" for w in report.warnings)

    dropped_negation = {"exam": "درد سمت چپ است. ECG تغییر دارد."}
    report2 = grounding_warnings(src, dropped_negation)
    assert any(w["code"] == "negation_shift_suspected" for w in report2.warnings)
