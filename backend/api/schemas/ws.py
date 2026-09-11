"""WebSocket protocol v1 — message schemas (spec §16, docs/WEBSOCKET_PROTOCOL.md).

Rules:
- every frame is a UTF-8 JSON object with ``v`` (int protocol version) and
  ``type`` (str). Audio is a binary frame (16 kHz mono PCM16) on the same
  socket; ``audio.chunk`` JSON (base64) remains for tests/tools only.
- inbound models use ``extra="forbid"`` (protocol strictness; a client that
  sends unknown fields gets PROTOCOL_FRAME_INVALID, not silent acceptance).
- ``session.start`` must be the first client frame; protocol negotiation
  failure closes with code 4409.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WS_PATH = "/ws/v1/transcribe"
INBOUND_TYPES = {
    "session.start",
    "audio.chunk",
    "session.pause",
    "session.resume",
    "session.stop",
}


class _Inbound(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = Field(ge=1, le=1, description="protocol version (v1 only)")
    session_id: str | None = None


class SessionStart(_Inbound):
    type: Literal["session.start"]
    language: str | None = Field(default=None, description="fa | en | fa-en | null=auto")
    provider: str | None = Field(default=None, description="explicit STT provider name")
    mode: Literal["local", "cloud", "hybrid", "auto"] | None = None
    privacy_required: bool | None = Field(
        default=None, description="absent → server policy default"
    )
    encounter_id: str | None = None
    audio: dict[str, int | str] = Field(default_factory=dict)
    #: client capability declarations; unknown keys rejected by extra=forbid
    client: dict[str, Any] | None = None


class _SessionCtl(_Inbound):
    type: Literal["session.pause", "session.resume", "session.stop"]
    reason: str | None = None


class AudioChunk(_Inbound):
    type: Literal["audio.chunk"]
    seq: int = Field(ge=0)
    ts_ms: int = Field(ge=0, description="client monotonic capture timestamp")
    pcm16_b64: str = Field(description="base64 PCM16 mono; binary frames preferred")


ClientMessage = Annotated[
    SessionStart | _SessionCtl | AudioChunk, Field(discriminator="type")
]


# -- server → client ---------------------------------------------------------


class _Server(BaseModel):
    v: int = 1
    type: str
    session_id: str | None = None


class SessionStarted(_Server):
    type: Literal["session.started"] = "session.started"
    protocol: int = 1
    provider: str | None = None
    mode: str | None = None
    language: str | None = None
    server_time_ms: int = 0


class TranscriptInterim(_Server):
    type: Literal["transcript.interim"] = "transcript.interim"
    text: str
    segment_id: str
    start_ms: int


class TranscriptFinal(_Server):
    type: Literal["transcript.final"] = "transcript.final"
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    language: str | None = None
    confidence: float | None = None


class CommandDetected(_Server):
    type: Literal["command.detected"] = "command.detected"
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    utterance_text: str = ""
    segment_id: str | None = None


class WarningFrame(_Server):
    type: Literal["warning"] = "warning"
    code: str
    message: str
    segment_id: str | None = None


class ErrorFrame(_Server):
    type: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool = True


class SessionCompleted(_Server):
    type: Literal["session.completed"] = "session.completed"
    segment_count: int = 0
    duration_ms: int = 0
    provider: str | None = None


ServerMessage = Annotated[
    SessionStarted | TranscriptInterim | TranscriptFinal | CommandDetected | WarningFrame | ErrorFrame | SessionCompleted,
    Field(discriminator="type"),
]
