"""Model hub API: catalog listing, verified downloads, delete.

- ``GET /models`` — catalog + live status (any authenticated principal; the
  Models screen is server-driven, like templates/terminology).
- ``GET /models/{id}`` — one model's status (progress polling).
- ``POST /models/{id}/download`` — admin; starts a verified background
  download (409 when already running, 204-equivalent no-op when installed).
- ``DELETE /models/{id}`` — admin; removes artifacts + cancels downloads.

Downloads stream from HuggingFace into MS_MODELS__DIR with size+sha256
verification; in-process providers (shenava STT, PII NER) auto-configure the
moment files land. External services (whisper.cpp, llama-server) get their
exact restart arguments in ``operator_note`` — the API never spawns or
restarts external AI services (spec §12).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from api.auth.deps import CurrentPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.models import (
    ModelDeleted,
    ModelDownloadAccepted,
    ModelFileInfo,
    ModelInfo,
    ModelListResponse,
)
from api.services.model_manager import ModelDownloadError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["models"])


def _manager(request: Request):
    manager = getattr(request.app.state, "model_manager", None)
    if manager is None:  # pragma: no cover — composed in create_app
        raise ApiError(500, ErrorCode.INTERNAL, "model manager not configured")
    return manager


def _to_info(request: Request, spec) -> ModelInfo:
    manager = _manager(request)
    status = manager.status(spec)
    file_progress: dict[str, int] = status.get("file_progress", {})
    files = [
        ModelFileInfo(
            local_name=f.local_name,
            size_bytes=f.size_bytes,
            received_bytes=file_progress.get(f.local_name, 0),
        )
        for f in spec.files
    ]
    return ModelInfo(
        id=spec.id,
        name=spec.name,
        role=spec.role.value,
        description=spec.description,
        license=spec.license,
        license_url=spec.license_url,
        source_repo=spec.source_repo,
        runtime=spec.runtime,
        providers=list(spec.providers),
        state=status["state"],
        progress=status.get("progress"),
        received_bytes=status.get("received_bytes", 0),
        total_bytes=spec.total_bytes,
        current_file=status.get("current_file"),
        error=status.get("error"),
        installed_at=status.get("installed_at"),
        auto_configured=spec.auto_configured,
        operator_note=spec.operator_note,
        files=files,
        install_dir=f"{getattr(request.app.state.settings.models, 'dir', 'models')}/{spec.id}",
    )


@router.get("", response_model=ModelListResponse)
async def list_models(request: Request, principal: CurrentPrincipal) -> ModelListResponse:
    return ModelListResponse(
        models=[_to_info(request, spec) for spec in _manager(request).specs()]
    )


@router.get("/{model_id}", response_model=ModelInfo)
async def get_model(model_id: str, request: Request, principal: CurrentPrincipal) -> ModelInfo:
    spec = _manager(request).spec(model_id)
    if spec is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"unknown model '{model_id}'")
    return _to_info(request, spec)


@router.post("/{model_id}/download", response_model=ModelDownloadAccepted, status_code=202)
async def download_model(
    model_id: str, request: Request, principal: CurrentPrincipal
) -> ModelDownloadAccepted:
    if principal.role != "admin":
        raise ApiError(403, ErrorCode.FORBIDDEN, "admin role required to manage models")
    spec = _manager(request).spec(model_id)
    if spec is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"unknown model '{model_id}'")
    manager = _manager(request)
    try:
        started = await manager.start_download(spec.id)
    except ModelDownloadError as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    status = manager.status(spec)
    if not started and status["state"] == "installed":
        return ModelDownloadAccepted(
            model_id=spec.id, state="installed", detail="already installed"
        )
    return ModelDownloadAccepted(
        model_id=spec.id,
        state="downloading",
        detail="download started; poll GET /api/v1/models/{id} for progress",
    )


@router.delete("/{model_id}", response_model=ModelDeleted)
async def delete_model(
    model_id: str, request: Request, principal: CurrentPrincipal
) -> ModelDeleted:
    if principal.role != "admin":
        raise ApiError(403, ErrorCode.FORBIDDEN, "admin role required to manage models")
    spec = _manager(request).spec(model_id)
    if spec is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"unknown model '{model_id}'")
    manager = _manager(request)
    try:
        deleted = await manager.delete(spec.id)
    except ModelDownloadError as exc:
        raise ApiError(409, ErrorCode.CONFLICT, str(exc)) from exc
    return ModelDeleted(
        model_id=spec.id,
        deleted=deleted,
        detail="artifacts removed" if deleted else "nothing installed",
    )
