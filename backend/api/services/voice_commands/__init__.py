"""Voice-command subsystem (Phase 6, spec §7).

Package layout:
- ``catalog``  — data-driven bilingual command catalog (single source of
  truth; the client manifest re-exports it so the UI never hardcodes
  commands);
- ``parser``   — anchored/exact utterance matching with a strict
  ambiguity policy (never silently delete clinical speech);
- ``effects``  — effect application against the transcript store with a
  per-session undo journal.

Design rules (spec §7):
- commands are distinguishable from clinical speech: an utterance is a
  command only when the WHOLE utterance matches a trigger (exact match) or
  a trigger + argument prefix (anchored match, arg-taking commands only);
- a trigger embedded mid-speech is ambiguous → text is kept verbatim and a
  ``COMMAND_AMBIGUOUS`` warning is emitted;
- an explicit mode prefix (``فرمان`` / "command") forces command
  interpretation for edge cases;
- handlers are registered data, not UI code.
"""
from api.services.voice_commands.catalog import (
    CommandSpec,
    VoiceCommandCatalog,
    default_catalog,
)
from api.services.voice_commands.effects import (
    CommandEffect,
    CommandRuntime,
    VoiceCommandService,
)
from api.services.voice_commands.parser import (
    MatchKind,
    ParsedCommand,
    VoiceCommandParser,
)

__all__ = [
    "CommandEffect",
    "CommandRuntime",
    "CommandSpec",
    "MatchKind",
    "ParsedCommand",
    "VoiceCommandCatalog",
    "VoiceCommandParser",
    "VoiceCommandService",
    "default_catalog",
]
