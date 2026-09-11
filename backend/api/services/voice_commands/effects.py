"""Voice-command effects engine (Phase 6, spec §7).

Applies parsed commands against the transcript store with a per-session
undo journal. Called exclusively from the transcription hub (live sessions)
— REST never parses free text for commands.

Safety rules:
- a command utterance never enters the transcript as dictated text;
- every mutation is journaled so ``undo_last`` can reverse it exactly
  (pause/resume excepted — their inverse is the opposite command);
- gate failures and no-op targets are reported honestly via
  ``warning_code`` so the UI can surface them (never silently ignored);
- the engine never touches segments' clinical content beyond the explicit
  command semantics (delete-last-sentence removes dictation the physician
  just asked to remove — the prior text stays in the journal).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from api.services.transcript_store import (
    KIND_FINALIZED_SECTION,
    KIND_PARAGRAPH,
    KIND_REPEAT,
    KIND_SECTION,
    JournalEntry,
    StoredSegment,
    TranscriptStore,
)
from api.services.voice_commands.catalog import VoiceCommandCatalog, default_catalog
from api.services.voice_commands.parser import ParsedCommand, VoiceCommandParser


@dataclass
class CommandRuntime:
    """Per-call context the hub provides (the effects engine stays stateless
    and testable without sockets)."""

    store: TranscriptStore
    session_id: str
    #: True while the dictation session is live (gates section commands)
    recording: bool = True
    #: True while voice-paused (resume is the only accepted command then)
    voice_paused: bool = False
    pause: Callable[[], None] | None = None
    resume: Callable[[], None] | None = None


@dataclass
class CommandEffect:
    """Outcome of processing one finalized utterance.

    ``executed`` True  → the utterance WAS a command and it applied (or was
                          a deliberate no-op with a warning, e.g. nothing to
                          delete) — the hub must NOT store the utterance as
                          dictated text;
    ``executed`` False → ambiguity/gate rejection — the hub KEEPS the text.
    """

    executed: bool
    command_id: str = ""
    args: dict[str, str] = field(default_factory=dict)
    utterance_text: str = ""
    note: str = ""
    warning_code: str | None = None
    warning_message: str | None = None


class VoiceCommandService:
    def __init__(self, catalog: VoiceCommandCatalog | None = None) -> None:
        self._catalog = catalog or default_catalog()
        self._parser = VoiceCommandParser(self._catalog)

    @property
    def catalog(self) -> VoiceCommandCatalog:
        return self._catalog

    # -- entry point ------------------------------------------------------------

    def process_final(self, text: str, runtime: CommandRuntime) -> CommandEffect | None:
        """Parse + apply one final segment. Returns None when the text is
        plain clinical speech (no trigger anywhere) — caller stores it."""
        parsed = self._parser.parse(text)
        if parsed is None:
            return None
        if parsed.ambiguous_trigger is not None:
            return CommandEffect(
                executed=False,
                note="ambiguous",
                warning_code="COMMAND_AMBIGUOUS",
                warning_message=(
                    f"'{parsed.utterance_text}' contains the command trigger "
                    f"'{parsed.ambiguous_trigger}' inside speech — kept as transcript text; "
                    f"say the command alone or prefix it with 'فرمان' to execute it"
                ),
            )
        return self._apply(parsed, runtime)

    # -- dispatch --------------------------------------------------------------

    def _apply(self, parsed: ParsedCommand, runtime: CommandRuntime) -> CommandEffect:
        spec = self._catalog.require(parsed.command_id)

        # gates -----------------------------------------------------------------
        if runtime.voice_paused and parsed.command_id != "resume_recording":
            # while voice-paused only the resume command acts; anything else
            # the physician says is off-record by their own request
            return CommandEffect(
                executed=True,  # it IS a command — never stored as transcript
                command_id=parsed.command_id,
                args=parsed.args,
                utterance_text=parsed.utterance_text,
                note="ignored while voice-paused",
                warning_code="COMMAND_IGNORED_PAUSED",
                warning_message=(
                    f"'{parsed.command_id}' ignored while capture is paused by voice "
                    "— say 'ادامه ضبط' to resume first"
                ),
            )
        if spec.gate == "recording" and not runtime.recording:
            return CommandEffect(
                executed=False,
                warning_code="COMMAND_GATE",
                warning_message=f"'{spec.command_id}' is only available while recording",
            )
        if parsed.command_id == "pause_recording" and runtime.voice_paused:
            return CommandEffect(
                executed=True,  # consume the utterance; already paused
                command_id=parsed.command_id,
                args=parsed.args,
                utterance_text=parsed.utterance_text,
                note="already paused",
                warning_code="COMMAND_ALREADY_PAUSED",
                warning_message="capture is already paused",
            )
        if parsed.command_id == "resume_recording" and not runtime.voice_paused:
            return CommandEffect(
                executed=True,
                command_id=parsed.command_id,
                args=parsed.args,
                utterance_text=parsed.utterance_text,
                note="not paused",
                warning_code="COMMAND_NOT_PAUSED",
                warning_message="capture is not paused",
            )

        handler = getattr(self, f"_cmd_{parsed.command_id}", None)
        if handler is None:  # pragma: no cover — catalog/handler drift guard
            return CommandEffect(
                executed=False,
                warning_code="COMMAND_UNKNOWN",
                warning_message=f"command '{parsed.command_id}' has no handler",
            )
        effect: CommandEffect = handler(parsed, runtime)
        if not effect.command_id:
            effect.command_id = parsed.command_id
        if not effect.args:
            effect.args = parsed.args
        if not effect.utterance_text:
            effect.utterance_text = parsed.utterance_text
        return effect

    # -- handlers (one per catalog command; registry by name) ---------------------

    def _cmd_new_paragraph(self, parsed: ParsedCommand, runtime: CommandRuntime) -> CommandEffect:
        marker = runtime.store.append_marker(runtime.session_id, KIND_PARAGRAPH)
        if marker is None:
            return self._no_transcript(parsed)
        self._journal_remove(runtime, marker)
        return CommandEffect(executed=True, note="paragraph break inserted")

    def _cmd_delete_last_sentence(
        self, parsed: ParsedCommand, runtime: CommandRuntime
    ) -> CommandEffect:
        info = runtime.store.delete_last_sentence(runtime.session_id)
        if info is None:
            return CommandEffect(
                executed=True,
                note="nothing to delete",
                warning_code="COMMAND_NO_TARGET",
                warning_message="no dictated sentence to delete",
            )
        if info["segment_removed"]:
            runtime.store.push_journal(
                runtime.session_id,
                JournalEntry(
                    command_id="delete_last_sentence",
                    undo_op="append_segment",
                    payload={
                        "index": info["index"],
                        "segment": StoredSegment(
                            segment_id=info["segment_id"],
                            text=info["prior_text"],
                            start_ms=info["start_ms"],
                            end_ms=info["end_ms"],
                            kind=info.get("kind") or "dictated",
                        ),
                    },
                ),
            )
        else:
            runtime.store.push_journal(
                runtime.session_id,
                JournalEntry(
                    command_id="delete_last_sentence",
                    undo_op="restore_text",
                    payload={
                        "segment_id": info["segment_id"],
                        "text": info["prior_text"],
                    },
                ),
            )
        return CommandEffect(executed=True, note=f"removed: {info['removed_sentence'][:80]}")

    def _cmd_pause_recording(self, parsed: ParsedCommand, runtime: CommandRuntime) -> CommandEffect:
        if runtime.pause is not None:
            runtime.pause()
        return CommandEffect(executed=True, note="capture paused by voice")

    def _cmd_resume_recording(self, parsed: ParsedCommand, runtime: CommandRuntime) -> CommandEffect:
        if runtime.resume is not None:
            runtime.resume()
        return CommandEffect(executed=True, note="capture resumed by voice")

    def _cmd_finalize_section(
        self, parsed: ParsedCommand, runtime: CommandRuntime
    ) -> CommandEffect:
        current = self._current_section_title(runtime)
        marker = runtime.store.append_marker(
            runtime.session_id, KIND_FINALIZED_SECTION, {"section_title": current}
        )
        if marker is None:
            return self._no_transcript(parsed)
        self._journal_remove(runtime, marker)
        return CommandEffect(
            executed=True, note=f"section finalized: {current or '(untitled)'}"
        )

    def _cmd_insert_section(self, parsed: ParsedCommand, runtime: CommandRuntime) -> CommandEffect:
        title = (parsed.args.get("section_title") or "").strip()
        if not title:
            return CommandEffect(
                executed=True,
                note="missing section title",
                warning_code="COMMAND_MISSING_ARG",
                warning_message="insert_section needs a section name (e.g. 'درج بخش سابقه بیماری')",
            )
        marker = runtime.store.append_marker(runtime.session_id, KIND_SECTION, {"section_title": title})
        if marker is None:
            return self._no_transcript(parsed)
        self._journal_remove(runtime, marker)
        return CommandEffect(executed=True, args={"section_title": title}, note=f"section: {title}")

    def _cmd_undo_last(self, parsed: ParsedCommand, runtime: CommandRuntime) -> CommandEffect:
        entry = runtime.store.pop_journal(runtime.session_id)
        if entry is None:
            return CommandEffect(
                executed=True,
                note="nothing to undo",
                warning_code="COMMAND_NO_TARGET",
                warning_message="no reversible voice command to undo",
            )
        undone = self._undo_entry(runtime, entry)
        return CommandEffect(executed=True, note=f"undid {entry.command_id}: {undone}")

    def _cmd_repeat_last(self, parsed: ParsedCommand, runtime: CommandRuntime) -> CommandEffect:
        last = runtime.store.last_text_segment(runtime.session_id)
        if last is None or not last.text.strip():
            return CommandEffect(
                executed=True,
                note="nothing to repeat",
                warning_code="COMMAND_NO_TARGET",
                warning_message="no dictated segment to repeat",
            )
        marker = runtime.store.append_marker(
            runtime.session_id, KIND_REPEAT, {"text": last.text.strip()}
        )
        if marker is None:
            return self._no_transcript(parsed)
        self._journal_remove(runtime, marker)
        return CommandEffect(executed=True, note="repeated last segment")

    # -- undo machinery -----------------------------------------------------------

    def _undo_entry(self, runtime: CommandRuntime, entry: JournalEntry) -> str:
        payload: dict[str, Any] = entry.payload
        if entry.undo_op == "remove_segment":
            seg = runtime.store.remove_segment(runtime.session_id, payload["segment_id"])
            return f"removed marker {payload['segment_id']}" if seg else "marker already gone"
        if entry.undo_op == "restore_text":
            updated = runtime.store.edit_segment_by_command(
                runtime.session_id, payload["segment_id"], payload["text"], "undo_last"
            )
            return f"restored {payload['segment_id']}" if updated else "segment gone"
        if entry.undo_op == "append_segment":
            runtime.store.insert_segment_at(
                runtime.session_id, payload["index"], payload["segment"]
            )
            return f"restored segment {payload['segment'].segment_id}"
        return "unknown undo op"  # pragma: no cover — journal is engine-written

    def _journal_remove(self, runtime: CommandRuntime, marker: StoredSegment) -> None:
        runtime.store.push_journal(
            runtime.session_id,
            JournalEntry(
                command_id=marker.kind,
                undo_op="remove_segment",
                payload={"segment_id": marker.segment_id},
            ),
        )

    # -- helpers -------------------------------------------------------------------

    @staticmethod
    def _current_section_title(runtime: CommandRuntime) -> str:
        t = runtime.store.get(runtime.session_id)
        if t is None:
            return ""
        title = ""
        for s in t.segments:
            if s.kind == KIND_SECTION:
                title = (s.meta or {}).get("section_title", "")
        return title

    @staticmethod
    def _no_transcript(parsed: ParsedCommand) -> CommandEffect:
        return CommandEffect(
            executed=True,
            note="session transcript not found",
            warning_code="COMMAND_NO_TARGET",
            warning_message="no transcript buffer for this session",
        )
