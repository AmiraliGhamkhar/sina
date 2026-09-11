"""Model hub schemas (catalog + download status for the WPF Models screen)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ModelFileInfo(BaseModel):
    local_name: str
    size_bytes: int
    #: live during a download; equals size once the file is in place
    received_bytes: int = 0


class ModelInfo(BaseModel):
    id: str
    name: str
    role: str  # stt-shenava | stt-whisper | llm-llama | pii-redaction
    description: str = ""
    license: str = ""
    license_url: str | None = None
    source_repo: str = ""
    runtime: str = ""
    providers: list[str] = Field(default_factory=list)
    #: not_installed | downloading | installed | error
    state: str = "not_installed"
    progress: float | None = None  # 0..1 (None when unknown)
    received_bytes: int = 0
    total_bytes: int = 0
    current_file: str | None = None
    error: str | None = None
    installed_at: str | None = None
    #: backend picks the model up with no restart (in-process providers)
    auto_configured: bool = False
    #: exact operator action for external-service models (whisper/llama args)
    operator_note: str | None = None
    files: list[ModelFileInfo] = Field(default_factory=list)
    #: where artifacts live (relative server path; display only)
    install_dir: str = ""


class ModelListResponse(BaseModel):
    models: list[ModelInfo]


class ModelDownloadAccepted(BaseModel):
    model_id: str
    state: str
    detail: str = ""


class ModelDeleted(BaseModel):
    model_id: str
    deleted: bool
    detail: str = ""
