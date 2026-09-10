"""Client manifest: everything the WPF shell may know about server policy.

Deliberately excludes: provider secrets, internal URLs, environment config.
This is the *only* view of AI policy the client gets (docs/ARCHITECTURE.md).
Voice commands are data-driven so the client UI never hardcodes them
(spec §7).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class VoiceCommand(BaseModel):
    id: str
    description: str
    #: recognized trigger phrases, mixed-language; matched server-side by the
    #: command parser (Phase 6) — the client only renders this list.
    triggers: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)


#: canonical command catalog (backend is the source of truth, exposed here)
VOICE_COMMANDS: list[VoiceCommand] = [
    VoiceCommand(
        id="new_paragraph",
        description="Start a new paragraph in the transcript.",
        triggers=["پاراگراف جدید", "جدید پاراگراف", "new paragraph", "start a new paragraph"],
    ),
    VoiceCommand(
        id="delete_last_sentence",
        description="Remove the last dictated sentence.",
        triggers=["حذف جمله آخر", "پاک کردن جمله آخر", "delete last sentence", "remove last sentence"],
    ),
    VoiceCommand(
        id="pause_recording",
        description="Pause capture until resumed.",
        triggers=["توقف", "مکث", "pause recording", "pause dictation"],
    ),
    VoiceCommand(
        id="resume_recording",
        description="Resume a paused capture session.",
        triggers=["ادامه", "ازسرگیری", "resume recording", "continue dictation"],
    ),
    VoiceCommand(
        id="finalize_section",
        description="Mark the current section final (locks it for editing prompts).",
        triggers=["بستن بخش", "نهایی کردن بخش", "finalize section", "end section"],
    ),
    VoiceCommand(
        id="insert_section",
        description="Insert a named report section at the caret.",
        triggers=["درج بخش", "افزودن بخش"],
        args=["section_name"],  # e.g. "درج بخش سابقه بیماری"
    ),
    VoiceCommand(
        id="undo_last",
        description="Undo the last edit or command.",
        triggers=["برگردان", "undo", "undo that", "undo last command"],
    ),
    VoiceCommand(
        id="repeat_last",
        description="Re-read/re-insert the last finalized segment text.",
        triggers=["تکرار", "repeat", "repeat that"],
    ),
]


class LanguageOption(BaseModel):
    code: str
    label: str
    #: English medical terminology is preserved verbatim inside Persian text
    preserves_embedded_english_terms: bool = True


class FeatureFlags(BaseModel):
    live_transcription: bool = False  # enabled in Phase 2
    voice_commands: bool = False  # enabled in Phase 6
    report_generation: bool = False  # enabled in Phase 6
    editing_enabled: bool = True
    cloud_providers_enabled: bool = False  # enabled in Phase 3/4
    audit_enabled: bool = True


class ManifestResponse(BaseModel):
    server_version: str
    phase: int
    ws_protocol: int
    ws_path: str
    max_message_bytes: int
    audio_format: dict[str, int | str] = Field(
        default_factory=lambda: {
            "encoding": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
            "chunk_ms": 100,
        }
    )
    languages: list[LanguageOption] = Field(
        default_factory=lambda: [
            LanguageOption(code="fa", label="فارسی"),
            LanguageOption(code="en", label="English"),
            LanguageOption(code="fa-en", label="فارسی + اصطلاحات انگلیسی"),
        ]
    )
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    voice_commands: list[VoiceCommand] = Field(
        default_factory=lambda: list(VOICE_COMMANDS)
    )
    routing_modes: list[str] = Field(
        default_factory=lambda: ["local", "cloud", "hybrid", "auto"]
    )
