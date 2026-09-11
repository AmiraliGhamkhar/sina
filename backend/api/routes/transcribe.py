"""Batch transcription endpoint (Phase 3).

POST /api/v1/transcribe/batch  (multipart)

Accepts a WAV file (validated 16-bit; multi-channel rejected explicitly) or
raw mono PCM16 with an explicit ``sample_rate``. Routing goes through the
same privacy-walled router as live sessions (TaskKind.TRANSCRIBE_BATCH →
only ``supports_batch`` providers), so a privacy-required upload can never
be silently sent to a cloud provider. Audio bytes are processed and dropped:
nothing is stored, nothing is logged; only safe metadata reaches the audit.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, File, Form, Request, UploadFile

from ai.base import ProviderError, ProviderKind, STTRequest
from ai.router import NoEligibleProviderError, route
from api.auth.deps import OptionalPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.transcribe import BatchSegment, TranscribeBatchResponse
from api.services.ai_bridge import (
    batch_route_request,
    build_candidates,
    provider_config_with_secrets,
)

router = APIRouter(prefix="/transcribe", tags=["transcribe"])


def _parse_audio(raw: bytes, sample_rate_hint: int) -> tuple[bytes, int, int]:
    """Return (pcm16le_mono, sample_rate, channels) or raise ApiError."""
    if raw[:4] == b"RIFF":
        try:
            from ai.stt._common import wav_to_pcm

            pcm, rate, channels = wav_to_pcm(raw)
        except ValueError as exc:
            raise ApiError(400, ErrorCode.VALIDATION, f"wav rejected: {exc}") from exc
        if channels != 1:
            raise ApiError(
                400,
                ErrorCode.VALIDATION,
                f"only mono audio supported, got {channels} channels",
            )
        return pcm, rate, channels
    if sample_rate_hint not in (8000, 16000, 22050, 24000, 32000, 44100, 48000):
        raise ApiError(
            400, ErrorCode.VALIDATION, f"unsupported sample_rate {sample_rate_hint}"
        )
    return raw, sample_rate_hint, 1


@router.post("/batch", response_model=TranscribeBatchResponse)
async def transcribe_batch(
    request: Request,
    file: UploadFile = File(..., description="WAV (16-bit mono) or raw mono PCM16"),  # noqa: B008
    language: str | None = Form(default=None),
    mode: str | None = Form(default=None, pattern="^(local|cloud|hybrid|auto)$"),
    privacy_required: bool | None = Form(default=None),
    provider: str | None = Form(default=None),
    sample_rate: int = Form(default=16000),
    context_hints: str | None = Form(default=None, description="comma-separated hotwords"),
    principal: OptionalPrincipal = None,
) -> TranscribeBatchResponse:
    settings = request.app.state.settings
    raw = await file.read(settings.stt.batch_max_bytes + 1)
    if len(raw) > settings.stt.batch_max_bytes:
        raise ApiError(
            413,
            ErrorCode.VALIDATION,
            f"audio exceeds MS_STT__BATCH_MAX_BYTES ({settings.stt.batch_max_bytes})",
        )
    if not raw:
        raise ApiError(400, ErrorCode.VALIDATION, "empty upload")
    pcm, rate, channels = _parse_audio(raw, sample_rate)

    request_id = f"bat_{uuid.uuid4().hex[:16]}"
    candidates = await build_candidates(request.app, ProviderKind.STT)
    route_request = batch_route_request(
        request.app,
        language=language,
        mode=mode,
        privacy_required=privacy_required,
        provider=provider,
    )
    try:
        decision = route(route_request, candidates)
    except NoEligibleProviderError as exc:
        # never fall back past the privacy wall — surface the refusal
        raise ApiError(503, ErrorCode.NO_PROVIDER, str(exc)) from exc

    hints = tuple(h.strip() for h in (context_hints or "").split(",") if h.strip())
    stt_request = STTRequest(
        audio=pcm,
        encoding="pcm_s16le",
        sample_rate=rate,
        channels=channels,
        language=language,
        context_hints=hints,
    )

    # Phase 5: batch tasks get NO mid-flight transparency (single request),
    # but retryable provider failures may still walk the privacy-filtered
    # fallback chain; total failure surfaces 502 with the full tried list.
    chain = [decision.provider, *(decision.fallbacks if settings.routing.fallback_enabled else ())]
    registry = request.app.state.ai_registry
    segments = None
    used_provider = decision.provider
    attempts: list[dict] = []
    failure: ProviderError | None = None
    for attempt_name in chain:
        entry: dict = {"provider": attempt_name}
        try:
            stt = registry.create(
                ProviderKind.STT, attempt_name,
                await provider_config_with_secrets(request.app, "stt", attempt_name),
            )
        except ProviderError as exc:
            entry["error"] = f"initialization: {exc}"[:200]
            attempts.append(entry)
            failure = failure or exc
            continue
        try:
            started = time.perf_counter()
            segments = await stt.transcribe(stt_request)
        except ProviderError as exc:
            request.app.state.provider_health.record_failure(f"stt:{attempt_name}")
            entry["error"] = str(exc)[:200]
            attempts.append(entry)
            failure = failure or exc
            if exc.status_hint in (400, 401, 403, 404, 422) or not exc.retryable:
                break  # client-side/misuse verdict — other providers won't fix it
            continue
        used_provider = attempt_name
        break
    if segments is None:
        assert failure is not None
        status = failure.status_hint if failure.status_hint in (400, 401, 403, 404, 422) else 502
        raise ApiError(
            status,
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"batch transcription failed: {failure}",
            details={
                "provider": decision.provider,
                "retryable": failure.retryable,
                "tried": attempts,
            },
        )
    if used_provider != decision.provider:
        request.app.state.metrics.incr("stt_batch_fallbacks")
    latency_ms = int((time.perf_counter() - started) * 1000)
    request.app.state.metrics.observe_ms("stt_batch_latency_ms", float(latency_ms))
    request.app.state.metrics.incr("stt_batch_requests")
    request.app.state.provider_health.record_success(f"stt:{used_provider}", float(latency_ms))

    audio_duration_ms = len(pcm) * 1000 // (rate * channels * 2)
    payload = [
        BatchSegment(
            segment_id=f"{request_id}_{i:04d}",
            text=seg.text,
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            confidence=seg.confidence,
            language=seg.language,
        )
        for i, seg in enumerate(segments)
    ]
    # audit metadata only — file name is user data too; strip to extension
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()[:8]
    request.app.state.audit.emit(
        "transcribe_batch_completed",
        request_id=request_id,
        user_id=principal.user_id if principal else None,
        provider=used_provider,
        routed_provider=decision.provider,
        routing_reason=decision.reason,
        privacy_override=decision.privacy_override_applied,
        audio_bytes=len(raw),
        audio_extension=ext,
        segment_count=len(payload),
        duration_ms=latency_ms,
    )
    return TranscribeBatchResponse(
        request_id=request_id,
        provider=used_provider,
        mode=route_request.mode.value,
        privacy_override_applied=decision.privacy_override_applied,
        language=language,
        audio_duration_ms=audio_duration_ms,
        segment_count=len(payload),
        text="\n\n".join(s.text for s in payload),
        latency_ms=latency_ms,
        segments=payload,
    )
