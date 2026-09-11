"""Client manifest: everything the WPF shell may know about server policy.

Deliberately excludes: provider secrets, internal URLs, environment config.
This is the *only* view of AI policy the client gets (docs/ARCHITECTURE.md).
Voice commands are data-driven so the client UI never hardcodes them
(spec §7) — the catalog lives in ``services/voice_commands/catalog.py`` and
is re-exported here for the wire.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from api.services.voice_commands.catalog import MODE_PREFIXES, default_catalog


class VoiceCommand(BaseModel):
    id: str
    description: str
    #: recognized trigger phrases, mixed-language; matched server-side by the
    #: command parser (Phase 6) — the client only renders this list
    triggers: list[str] = Field(default_factory=list)
    #: argument name when the command takes free text after the trigger
    args: list[str] = Field(default_factory=list)
    #: utterances prefixed with any of these are always parsed as commands
    mode_prefixes: list[str] = Field(default_factory=list)


def _voice_commands() -> list[VoiceCommand]:
    out: list[VoiceCommand] = []
    for spec in default_catalog().specs:
        args = [spec.arg_name] if spec.takes_arg and spec.arg_name else []
        out.append(
            VoiceCommand(
                id=spec.command_id,
                description=spec.description,
                triggers=list(spec.triggers),
                args=args,
                mode_prefixes=list(MODE_PREFIXES),
            )
        )
    return out


class LanguageOption(BaseModel):
    code: str
    label: str
    #: English medical terminology is preserved verbatim inside Persian text
    preserves_embedded_english_terms: bool = True


class FeatureFlags(BaseModel):
    live_transcription: bool = True
    voice_commands: bool = True  # Phase 6
    report_generation: bool = True  # Phase 6
    report_lifecycle: bool = True  # Phase 6 (draft → finalized → approved)
    editing_enabled: bool = True
    cloud_providers_enabled: bool = True
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
    voice_commands: list[VoiceCommand] = Field(default_factory=_voice_commands)
    routing_modes: list[str] = Field(
        default_factory=lambda: ["local", "cloud", "hybrid", "auto"]
    )
    #: report lifecycle states the client should model (spec §10)
    report_statuses: list[str] = Field(
        default_factory=lambda: ["draft", "finalized", "approved"]
    )
    report_template_keys: list[str] = Field(
        default_factory=lambda: [
            "general-clinical-note",
            "soap-note",
            "radiology-report",
            "ultrasound-report",
            "ct-report",
            "mri-report",
        ]
    )
