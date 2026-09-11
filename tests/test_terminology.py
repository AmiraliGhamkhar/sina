"""Terminology normalization (Phase 6, spec §6): bilingual canonicalization,
reversibility, embedded-English preservation, and the safety guarantee that
normalization can never touch validation-critical tokens."""
from __future__ import annotations

import pytest
from api.services.terminology import TerminologyNormalizer, normalize_script
from api.services.terminology_data import DEFAULT_CATALOG


@pytest.fixture()
def normalizer() -> TerminologyNormalizer:
    return TerminologyNormalizer()


def test_catalog_data_is_safe():
    problems = DEFAULT_CATALOG.validate_entries()
    assert problems == [], problems


def test_engine_refuses_unsafe_entries():
    from api.services.terminology_data import TermEntry, TerminologyCatalog

    bad = TerminologyCatalog(
        entries=(TermEntry(canonical="X", category="test", variants=("بدون تب",)),)
    )
    with pytest.raises(ValueError, match="protected token"):
        TerminologyNormalizer(bad)
    bad2 = TerminologyCatalog(
        entries=(TermEntry(canonical="X2", category="test", variants=("دوز 10",)),)
    )
    with pytest.raises(ValueError, match="digits"):
        TerminologyNormalizer(bad2)


def test_transliterations_map_to_english_terms(normalizer):
    res = normalizer.normalize("ام آر آی مغز و سی تی گanstes انجام شد")
    assert "MRI" in res.normalized and "CT" in res.normalized


def test_persian_equivalent_maps_to_spec_listed_term(normalizer):
    res = normalizer.normalize("سابقه پرفشاری خون دارد")
    assert "hypertension" in res.normalized


def test_native_persian_terms_untouched(normalizer):
    text = "بیمار تب و سردرد و سونوگرافی درخواست شد"
    res = normalizer.normalize(text)
    assert res.normalized == normalize_script(text)
    assert res.count == 0


def test_numbers_doses_negation_laterality_survive(normalizer):
    text = "دوز 40 mg بدون تغییر. سمت چپ. 10 میلی گرم."
    res = normalizer.normalize(text)
    for token in ("40 mg", "بدون", "چپ", "10"):
        assert token in res.normalized
    assert res.count == 0  # nothing substituted at all


def test_reversibility(normalizer):
    text = "ام آر آی و پرفشاری خون و دیابت"
    res = normalizer.normalize(text)
    assert res.count == 3
    restored = res.restore()
    assert "ام آر آی" in restored and "پرفشاری خون" in restored and "دیابت" in restored
    assert "MRI" not in restored


def test_whole_term_matching_no_substring_corruption(normalizer):
    # "ECGs" must not leave a half-matched token behind
    res = normalizer.normalize("نوار قلب تکرار شد")
    assert res.normalized == "ECG تکرار شد"


def test_zwnj_and_digit_normalization():
    assert normalize_script("میلی‌گرم") == "میلی گرم"
    assert normalize_script("۴۰") == "40"
    assert normalize_script("يک كلید") == "یک کلید"  # Arabic ya/kaf → Persian


def test_already_canonical_text_is_untouched(normalizer):
    res = normalizer.normalize("MRI و CT و hypertension")
    assert res.normalized == "MRI و CT و hypertension"
    assert res.count == 0


def test_multi_word_variants(normalizer):
    res = normalizer.normalize("بیماری انسدادی مزمن ریه تشخیص داده شد")
    assert "COPD" in res.normalized


def test_search_api():
    hits = DEFAULT_CATALOG.search("فشار")
    assert any(e.canonical == "hypertension" for e in hits)
    assert DEFAULT_CATALOG.search("zzzz-nonexistent") == []
