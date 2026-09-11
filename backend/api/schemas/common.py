"""Common response shapes."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str = Field(..., description="Stable machine code, e.g. NO_PROVIDER")
    message: str
    details: object | None = None


class ErrorEnvelope(BaseModel):
    """Every non-2xx REST response has this shape."""

    error: ErrorResponse


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    phase: int


class ReadinessResponse(BaseModel):
    ready: bool
    components: dict[str, dict] = Field(default_factory=dict)


class VersionResponse(BaseModel):
    name: str
    api_version: str
    ws_protocol: int
    ws_protocol_min: int
    phase: int
