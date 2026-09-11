"""Medical-safety validation pack (Phase 6, spec §9 + §18).

The hallucination-sensitive cases the spec calls out by name:
- 10 mg must not become 100 mg
- right must not become left
- "no effusion" must not become "effusion"
- missing information must stay missing
plus units (mg vs mcg), bilingual unit equivalence, dates (Jalali +
Gregorian), identifiers, anatomy grounding and terminology-canonical
grounding.
"""
from __future__ import annotations

from api.services.validation import (
    extract_dates,
    extract_quantities,
    jalali_to_gregorian,
    validate_sections,
)

TRANSCRIPT = (
    "بیمار با درد قفسه سینه مراجعه کرد. ECG بدون تغییر است. "
    "سابقه hypertension. دوز 40 mg furosemide. کد ملی 1234567890."
)


def _codes(result) -> set[str]:
    return {w.code for w in result.warnings}


# ---- spec §18 named cases -------------------------------------------------------


def test_10mg_must_not_become_100mg():
    result = validate_sections("دوز 10 mg شروع شد.", {"plan": "Start 100 mg daily."})
    warnings = [w for w in result.warnings if w.code == "unverified_number"]
    assert warnings, "dose alteration not flagged"
    assert warnings[0].severity == "critical"
    assert "10 mg" in warnings[0].message  # near-miss evidence quoted


def test_right_must_not_become_left():
    result = validate_sections("زانوی راست متورم است.", {"exam": "زانوی چپ متورم است."})
    assert "laterality_mismatch" in _codes(result)
    mismatch = next(w for w in result.warnings if w.code == "laterality_mismatch")
    assert mismatch.severity == "critical"


def test_no_effusion_must_not_become_effusion():
    result = validate_sections("بدون افیوژن پلور.", {"findings": "pleural effusion present."})
    assert "negation_mismatch" in _codes(result)
    mismatch = next(w for w in result.warnings if w.code == "negation_mismatch")
    assert mismatch.severity == "critical"


def test_missing_information_stays_missing():
    result = validate_sections(TRANSCRIPT, {"history": "[[MISSING]]", "plan": "[[MISSING]]"})
    assert result.missing_sections == ["history", "plan"]
    assert result.warnings == []


def test_missing_section_never_filled_from_knowledge():
    # transcript has no vitals — a draft inventing them must be flagged
    result = validate_sections("درد شکم دارد.", {"exam": "BP 120/80, afebrile."})
    assert "unverified_number" in _codes(result)


# ---- numbers & units ---------------------------------------------------------------


def test_verbatim_numbers_pass():
    result = validate_sections(TRANSCRIPT, {"cc": TRANSCRIPT})
    assert result.warnings == []
    assert result.numbers_verified >= 2


def test_bilingual_unit_equivalence():
    result = validate_sections(
        "۴۰ میلی‌گرم فوروسماید تجویز شد.", {"plan": "furosemide 40 mg daily."}
    )
    assert result.warnings == []
    assert result.numbers_verified == 1


def test_mg_vs_mcg_is_unit_mismatch():
    result = validate_sections("دوز 100 mcg روزانه.", {"plan": "100 mg daily."})
    assert "unit_mismatch" in _codes(result)
    unit = next(w for w in result.warnings if w.code == "unit_mismatch")
    assert unit.severity == "critical"


def test_persian_digits_and_decimal_separator():
    q = extract_quantities("۰.۵ میلی گرم")
    assert len(q) == 1 and q[0].value == 0.5 and q[0].unit == "mg"
    q3 = extract_quantities("1٫5 mg")  # Arabic decimal separator
    assert q3[0].value == 1.5


def test_number_near_drug_is_critical_even_without_dose_unit():
    result = validate_sections("متفورمین 1000 مصرف می‌کند.", {"plan": "metformin 500."})
    nums = [w for w in result.warnings if w.code == "unverified_number"]
    assert nums and nums[0].severity == "critical"


# ---- dates -------------------------------------------------------------------------


def test_jalali_gregorian_conversion():
    assert jalali_to_gregorian(1403, 5, 12) == (2024, 8, 2)
    assert jalali_to_gregorian(1403, 1, 1) == (2024, 3, 20)


def test_same_day_jalali_vs_gregorian_matches():
    result = validate_sections(
        "تاریخ مراجعه 1403/05/12 بود.", {"history": "Visit date: 2024-08-02."}
    )
    assert result.warnings == []


def test_one_day_off_is_critical_mismatch():
    result = validate_sections(
        "تاریخ مراجعه 1403/05/12 بود.", {"history": "Visit date: 2024-08-03."}
    )
    assert "date_mismatch" in _codes(result)
    assert next(w for w in result.warnings if w.code == "date_mismatch").severity == "critical"


def test_unknown_date_is_warning():
    result = validate_sections("در سال گذشته مراجعه کرده.", {"history": "Seen 2019-01-01."})
    assert "unverified_date" in _codes(result)


def test_date_components_do_not_leak_into_number_checks():
    result = validate_sections("مراجعه در 2024-08-02.", {"h": "visit 2024-08-02."})
    assert result.warnings == []


def test_persian_month_name_dates():
    dates = extract_dates("در ۱۲ مرداد ۱۴۰۳")
    assert any(d.calendar == "jalali" and (d.y, d.m, d.d) == (1403, 5, 12) for d in dates)


def test_extract_dates_gregorian_month_names():
    dates = extract_dates("Follow-up August 2 2024.")
    assert any((d.y, d.m, d.d) == (2024, 8, 2) for d in dates)


# ---- identifiers ------------------------------------------------------------------------


def test_identifier_flip_is_critical():
    result = validate_sections("کد ملی 1234567890.", {"admin": "National ID 1234567891."})
    assert "unverified_identifier" in _codes(result)
    ident = next(w for w in result.warnings if w.code == "unverified_identifier")
    assert ident.severity == "critical"
    assert "1234567891" not in ident.message  # masked in message


def test_matching_identifier_passes():
    result = validate_sections(TRANSCRIPT, {"cc": TRANSCRIPT})
    assert "unverified_identifier" not in _codes(result)


def test_decimal_run_is_not_an_identifier():
    result = validate_sections("محاسبه 3.1415926535 انجام شد.", {"x": "value 3.1415926535"})
    assert "unverified_identifier" not in _codes(result)


# ---- anatomy ------------------------------------------------------------------------------


def test_draft_anatomy_absent_from_transcript_flags():
    result = validate_sections("درد زانو دارد.", {"exam": "Liver span normal."})
    assert "unverified_anatomy" in _codes(result)


def test_anatomy_synonym_bilingual_grounding():
    result = validate_sections("معاینه قلب طبیعی بود.", {"exam": "Cardiac exam normal."})
    assert "unverified_anatomy" not in _codes(result)


# ---- terminology-canonical grounding ---------------------------------------------------------


def test_draft_english_term_grounded_via_normalized_view():
    from api.services.terminology import TerminologyNormalizer

    transcript = "ام آر آی مغز انجام شد."
    normalized = TerminologyNormalizer().normalize(transcript).normalized
    result = validate_sections(
        transcript, {"findings": "Brain MRI unremarkable."}, normalized_transcript=normalized
    )
    assert result.warnings == []


def test_negation_added_by_draft_is_flagged():
    result = validate_sections("تب دارد.", {"hpi": "بدون تب."})
    assert "negation_added" in _codes(result)


def test_drug_denial_flip_is_critical():
    result = validate_sections("وارفارین مصرف نمی‌کند.", {"meds": "on warfarin."})
    assert "negation_mismatch" in _codes(result)


# ---- regression guards -------------------------------------------------------------------------


def test_clean_summaries_of_verbatim_text_do_not_flag():
    transcript = (
        "بیمار زن 54 ساله با تنگی نفس. صداهای ریه بدون وییز. فشار خون 130 mmHg. "
        "گلوبال 12 مرداد 1403. سه روز پیش سرفه شروع شد."
    )
    draft = {
        "subjective": "بیمار زن 54 ساله با تنگی نفس. سه روز پیش سرفه شروع شد.",
        "objective": "صداهای ریه بدون وییز. فشار خون 130 mmHg.",
    }
    result = validate_sections(transcript, draft)
    assert result.warnings == []
