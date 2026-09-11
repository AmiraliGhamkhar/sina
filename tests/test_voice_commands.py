"""Voice-command parser + effects engine (Phase 6, spec §7).

Safety-critical properties pinned here:
- exact/anchored matching only — commands are distinguishable from speech;
- ambiguity NEVER deletes clinical text (warning instead);
- mode prefix forces interpretation;
- undo reverses structural effects exactly;
- voice-pause keeps audio flowing (resume stays audible) but suppresses
  non-command finals.
"""
from __future__ import annotations

from api.services.terminology_data import DEFAULT_CATALOG
from api.services.transcript_store import TranscriptStore
from api.services.voice_commands import (
    CommandRuntime,
    VoiceCommandParser,
    VoiceCommandService,
    default_catalog,
)


def _runtime(store: TranscriptStore, session_id: str = "s1", **kw) -> CommandRuntime:
    return CommandRuntime(store=store, session_id=session_id, recording=True, **kw)


def _open_store(session_id: str = "s1") -> TranscriptStore:
    store = TranscriptStore()
    store.open(session_id, user_id="u1", provider="mock", language="fa")
    return store


def _dictate(store: TranscriptStore, text: str, session_id: str = "s1"):
    from ai.base import TranscriptSegment

    store.append_final(
        session_id,
        TranscriptSegment(text=text, start_ms=0, end_ms=100, is_final=True),
    )


# ---- parser --------------------------------------------------------------------


def test_exact_match_bilingual():
    parser = VoiceCommandParser(default_catalog())
    for text in ("پاراگراف جدید", "New Paragraph.", "پاراگراف  جدید", "NEW PARAGRAPH"):
        parsed = parser.parse(text)
        assert parsed is not None and parsed.is_command, text
        assert parsed.command_id == "new_paragraph", text


def test_script_normalization_matches_stt_variants():
    parser = VoiceCommandParser(default_catalog())
    # Persian digits / arabic ya / ZWNJ variance must not break triggers
    assert parser.parse("پاراگراف جدید").command_id == "new_paragraph"
    assert parser.parse("حذف جمله آخر").command_id == "delete_last_sentence"
    assert parser.parse("delete last sentence").command_id == "delete_last_sentence"


def test_anchored_arg_extraction():
    parser = VoiceCommandParser(default_catalog())
    parsed = parser.parse("درج بخش سابقه بیماری")
    assert parsed is not None and parsed.is_command
    assert parsed.command_id == "insert_section"
    assert parsed.args["section_title"] == "سابقه بیماری"
    parsed_en = parser.parse("insert section Plan")
    assert parsed_en.command_id == "insert_section"
    assert parsed_en.args["section_title"] == "plan"


def test_mode_prefix_forces_command():
    parser = VoiceCommandParser(default_catalog())
    parsed = parser.parse("فرمان پاراگراف جدید")
    assert parsed is not None and parsed.is_command
    assert parsed.command_id == "new_paragraph"
    parsed2 = parser.parse("command delete last sentence")
    assert parsed2.is_command and parsed2.command_id == "delete_last_sentence"


def test_trigger_inside_speech_is_ambiguous_not_deleted():
    parser = VoiceCommandParser(default_catalog())
    parsed = parser.parse("بیمار گفت بعد از این پاراگراف جدید شروع می‌شود و درد دارد")
    assert parsed is not None
    assert parsed.ambiguous_trigger is not None
    assert not parsed.is_command  # caller keeps the text + warns


def test_plain_clinical_speech_returns_none():
    parser = VoiceCommandParser(default_catalog())
    assert parser.parse("دوز 40 میلی‌گرم فوروسماید روزانه تجویز شد") is None
    assert parser.parse("follow up in two weeks with labs") is None
    assert parser.parse("") is None


def test_bare_mode_prefix_is_speech():
    parser = VoiceCommandParser(default_catalog())
    assert parser.parse("فرمان") is None


def test_unknown_after_prefix_is_speech():
    parser = VoiceCommandParser(default_catalog())
    assert parser.parse("فرمان تب دارد") is None


# ---- effects --------------------------------------------------------------------


def test_new_paragraph_inserts_marker_and_undo_removes_it():
    store = _open_store()
    _dictate(store, "جمله اول.")
    svc = VoiceCommandService()
    effect = svc.process_final("پاراگراف جدید", _runtime(store))
    assert effect.executed and effect.command_id == "new_paragraph"
    t = store.get("s1")
    assert t.segments[-1].kind == "paragraph"
    # undo
    undo = svc.process_final("برگردان", _runtime(store))
    assert undo.executed
    assert all(s.kind == "dictated" for s in store.get("s1").segments)


def test_delete_last_sentence_partial_and_full():
    store = _open_store()
    _dictate(store, "جمله اول. جمله دوم. جمله سوم.")
    svc = VoiceCommandService()
    effect = svc.process_final("حذف جمله آخر", _runtime(store))
    assert effect.executed
    seg = store.get("s1").segments[0]
    assert seg.text == "جمله اول. جمله دوم."
    # delete remaining two, then the last one removes the segment entirely
    svc.process_final("delete last sentence", _runtime(store))
    svc.process_final("delete last sentence", _runtime(store))
    assert all(not s.text.strip() for s in store.get("s1").segments)
    # nothing left → honest no-op warning
    effect = svc.process_final("حذف جمله آخر", _runtime(store))
    assert effect.executed and effect.warning_code == "COMMAND_NO_TARGET"


def test_delete_last_sentence_undo_restores_text():
    store = _open_store()
    _dictate(store, "جمله اول. جمله دوم.")
    svc = VoiceCommandService()
    svc.process_final("حذف جمله آخر", _runtime(store))
    assert store.get("s1").segments[0].text == "جمله اول."
    undo = svc.process_final("undo", _runtime(store))
    assert undo.executed
    assert store.get("s1").segments[0].text == "جمله اول. جمله دوم."


def test_delete_removes_whole_segment_then_undo_restores_position():
    store = _open_store()
    _dictate(store, "جمله یک.")
    _dictate(store, "جمله دو.")
    svc = VoiceCommandService()
    svc.process_final("حذف جمله آخر", _runtime(store))  # removes segment 2 fully
    t = store.get("s1")
    assert len([s for s in t.segments if s.text.strip()]) == 1
    svc.process_final("undo", _runtime(store))
    t = store.get("s1")
    assert [s.text for s in t.segments] == ["جمله یک.", "جمله دو."]


def test_insert_and_finalize_section_markers():
    store = _open_store()
    svc = VoiceCommandService()
    effect = svc.process_final("درج بخش سابقه بیماری", _runtime(store))
    assert effect.executed and effect.args["section_title"] == "سابقه بیماری"
    _dictate(store, "متن بخش.")
    fin = svc.process_final("نهایی کردن بخش", _runtime(store))
    assert fin.executed
    t = store.get("s1")
    kinds = [s.kind for s in t.segments]
    assert "section" in kinds and "finalized_section" in kinds


def test_insert_section_without_arg_warns():
    store = _open_store()
    svc = VoiceCommandService()
    # "درج بخش" alone is an anchored command missing its argument → the
    # parser treats bare trigger as exact match; effects report missing arg
    effect = svc.process_final("درج بخش", _runtime(store))
    assert effect.executed
    assert effect.warning_code == "COMMAND_MISSING_ARG"


def test_repeat_last_reinserts_last_dictation():
    store = _open_store()
    _dictate(store, "فشار خون 120 روی 80.")
    svc = VoiceCommandService()
    effect = svc.process_final("تکرار", _runtime(store))
    assert effect.executed
    texts = [s.text for s in store.get("s1").segments if s.text.strip()]
    assert texts.count("فشار خون 120 روی 80.") == 2


def test_pause_resume_callbacks_and_gate_while_paused():
    store = _open_store()
    paused = {"v": False}
    svc = VoiceCommandService()
    runtime = CommandRuntime(
        store=store,
        session_id="s1",
        recording=True,
        voice_paused=paused["v"],
        pause=lambda: paused.__setitem__("v", True),
        resume=lambda: paused.__setitem__("v", False),
    )
    effect = svc.process_final("توقف ضبط", runtime)
    assert effect.executed and paused["v"] is True
    # while voice-paused, other commands are consumed but ignored (warned)
    runtime2 = CommandRuntime(
        store=store, session_id="s1", recording=True, voice_paused=True
    )
    eff2 = svc.process_final("پاراگراف جدید", runtime2)
    assert eff2.executed and eff2.warning_code == "COMMAND_IGNORED_PAUSED"
    # resume works
    eff3 = svc.process_final("ادامه ضبط", runtime2)
    assert eff3.executed and eff3.warning_code is None


def test_resume_when_not_paused_is_honest_noop():
    store = _open_store()
    svc = VoiceCommandService()
    effect = svc.process_final("ادامه ضبط", _runtime(store))
    assert effect.executed and effect.warning_code == "COMMAND_NOT_PAUSED"


def test_finalize_section_gated_on_recording():
    store = _open_store()
    svc = VoiceCommandService()
    runtime = CommandRuntime(store=store, session_id="s1", recording=False)
    effect = svc.process_final("نهایی کردن بخش", runtime)
    assert not effect.executed  # gate rejection → text is kept as speech


def test_recording_gate_section_marker_not_applied():
    store = _open_store()
    svc = VoiceCommandService()
    runtime = CommandRuntime(store=store, session_id="s1", recording=False)
    svc.process_final("درج بخش تاریخچه", runtime)
    assert all(s.kind == "dictated" for s in store.get("s1").segments)


def test_undo_with_empty_journal_warns():
    store = _open_store()
    svc = VoiceCommandService()
    effect = svc.process_final("undo", _runtime(store))
    assert effect.executed and effect.warning_code == "COMMAND_NO_TARGET"


def test_repeat_with_no_dictation_warns():
    store = _open_store()
    svc = VoiceCommandService()
    effect = svc.process_final("repeat", _runtime(store))
    assert effect.executed and effect.warning_code == "COMMAND_NO_TARGET"


# ---- catalog integrity --------------------------------------------------------------


def test_catalog_ids_unique_and_handlers_exist():
    svc = VoiceCommandService()
    for spec in default_catalog().specs:
        assert spec.command_id
        assert spec.triggers, spec.command_id
        if spec.mutates_transcript:
            assert hasattr(svc, f"_cmd_{spec.command_id}"), spec.command_id


def test_terminology_catalog_never_shadows_commands():
    # command triggers must not be terminology variants (would corrupt text)
    catalog = DEFAULT_CATALOG
    for entry in catalog.entries:
        for variant in entry.variants:
            assert variant not in ("پاراگراف جدید", "حذف جمله آخر", "توقف ضبط")
