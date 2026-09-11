"""Voice-command catalog — data-driven, bilingual (Phase 6, spec §7).

Single source of truth for command ids, triggers and argument shapes:
- the client manifest (``api/schemas/manifest.py``) re-exports this so the
  WPF UI renders commands without hardcoding them;
- the parser (``parser.py``) matches normalized utterances against
  ``triggers``;
- the effects engine (``effects.py``) dispatches on ``command_id``.

Extending: add a ``CommandSpec`` here + a handler in ``effects.py``. No UI
or protocol change needed (``command.detected`` carries the id + args).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    command_id: str
    description: str
    #: trigger phrases (Persian + English); matched after script
    #: normalization, whole-utterance (exact) or whole-utterance-prefix
    #: (anchored) when ``takes_arg`` is set
    triggers: tuple[str, ...] = ()
    #: when True the trigger may be followed by a free-text argument
    #: (e.g. "درج بخش سابقه بیماری" → arg "سابقه بیماری")
    takes_arg: bool = False
    arg_name: str | None = None
    #: gate: "recording" = only meaningful while a dictation session is live
    gate: str = "always"
    #: False → the command is informational only (no transcript effect)
    mutates_transcript: bool = False


#: `finalize_section`/`insert_section` gate on an active session; the hub is
#: the only caller context that satisfies "recording" (REST never parses
#: commands), enforced in effects.py.
_CATALOG: tuple[CommandSpec, ...] = (
    CommandSpec(
        command_id="new_paragraph",
        description="Start a new paragraph in the transcript.",
        triggers=(
            "پاراگراف جدید",
            "جدید پاراگراف",
            "new paragraph",
            "start a new paragraph",
        ),
        mutates_transcript=True,
    ),
    CommandSpec(
        command_id="delete_last_sentence",
        description="Remove the last dictated sentence.",
        triggers=(
            "حذف جمله آخر",
            "پاک کردن جمله آخر",
            "delete last sentence",
            "remove last sentence",
        ),
        mutates_transcript=True,
    ),
    CommandSpec(
        command_id="pause_recording",
        description="Pause transcript capture (audio keeps flowing so the resume command is heard).",
        triggers=("توقف ضبط", "توقف", "مکث", "pause recording", "pause dictation"),
    ),
    CommandSpec(
        command_id="resume_recording",
        description="Resume a voice-paused capture session.",
        triggers=("ادامه ضبط", "ازسرگیری ضبط", "ادامه", "resume recording", "continue dictation"),
    ),
    CommandSpec(
        command_id="finalize_section",
        description="Mark the current section final (locks it out of further dictation).",
        triggers=("بستن بخش", "نهایی کردن بخش", "finalize section", "end section"),
        gate="recording",
        mutates_transcript=True,
    ),
    CommandSpec(
        command_id="insert_section",
        description="Insert a named report section marker.",
        triggers=("درج بخش", "افزودن بخش", "insert section"),
        takes_arg=True,
        arg_name="section_title",
        gate="recording",
        mutates_transcript=True,
    ),
    CommandSpec(
        command_id="undo_last",
        description="Undo the last transcript-mutating voice command.",
        triggers=("برگردان آخرین", "برگردان", "undo", "undo that", "undo last command"),
        mutates_transcript=True,
    ),
    CommandSpec(
        command_id="repeat_last",
        description="Re-insert the last dictated segment text.",
        triggers=("تکرار آخرین", "تکرار", "repeat", "repeat that"),
        mutates_transcript=True,
    ),
)


class VoiceCommandCatalog:
    def __init__(self, specs: tuple[CommandSpec, ...] = _CATALOG) -> None:
        self._specs = specs
        self._by_id = {s.command_id: s for s in specs}
        if len(self._by_id) != len(specs):
            raise ValueError("duplicate command_id in catalog")

    @property
    def specs(self) -> tuple[CommandSpec, ...]:
        return self._specs

    def get(self, command_id: str) -> CommandSpec | None:
        return self._by_id.get(command_id)

    def require(self, command_id: str) -> CommandSpec:
        spec = self._by_id.get(command_id)
        if spec is None:
            raise KeyError(f"unknown command id {command_id!r}")
        return spec


_default = VoiceCommandCatalog()


def default_catalog() -> VoiceCommandCatalog:
    return _default


#: explicit command-mode prefixes — everything after the prefix is matched
#: as a command even if it could read as clinical speech (spec §7
#: "commands must be distinguishable from normal clinical speech")
MODE_PREFIXES: tuple[str, ...] = ("فرمان", "command", "hey scribe")
