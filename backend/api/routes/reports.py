"""Report drafting endpoint (Phase 4).

POST /api/v1/reports/{encounter_id}/draft

Grounded, strict-JSON note drafting with one repair round-trip and a light
fidelity check (numbers / laterality / negation — "preserved or flagged").
Full persistence + finalize/approve land in Phase 6/7; drafts are stateless
here: the response IS the draft (the client keeps it until Phase 7 saves it).

Privacy notes:
- routing honors the same wall as transcription (privacy_default=true →
  LOCAL providers only);
- even when cloud IS allowed, identifier-shaped strings are scrubbed from
  the model input first (settings.llm.cloud.redact_phi_for_cloud, default on)
  and the fidelity check runs against the scrubbed text so redaction can
  never masquerade as fabrication.
"""
from __future__ import annotations

import re
import time
import uuid

from fastapi import APIRouter, Request

from ai.base import PrivacyClass, ProviderError, ProviderKind
from ai.router import NoEligibleProviderError
from api.auth.deps import OptionalPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.reports import (
    DraftSectionOut,
    GroundingWarningOut,
    ReportDraftRequest,
    ReportDraftResponse,
    UsageOut,
)
from api.services.ai_bridge import llm_route_request, select_llm_provider
from api.services.note_prompt import (
    MISSING_TOKEN,
    build_messages,
    grounding_warnings,
    parse_model_json,
    reconcile,
    redact_phi,
    repair_messages,
    resolve_sections,
)

router = APIRouter(prefix="/reports", tags=["reports"])

_ENC_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@router.post("/{encounter_id}/draft", response_model=ReportDraftResponse)
async def create_draft(
    request: Request,
    encounter_id: str,
    body: ReportDraftRequest,
    principal: OptionalPrincipal = None,
) -> ReportDraftResponse:
    if not _ENC_RE.match(encounter_id):
        raise ApiError(422, ErrorCode.VALIDATION, "invalid encounter_id")
    app = request.app
    settings = app.state.settings
    metrics = app.state.metrics

    try:
        decision, provider = await select_llm_provider(
            app,
            llm_route_request(
                app,
                mode=body.mode,
                privacy_required=body.privacy_required,
                provider=body.provider,
            ),
        )
    except NoEligibleProviderError as exc:
        raise ApiError(503, ErrorCode.NO_PROVIDER, str(exc)) from exc
    except ProviderError as exc:
        raise ApiError(
            502,
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"llm provider '{decision.provider}' failed to initialize: {exc}",
        ) from exc

    descriptor = app.state.ai_registry.get_descriptor(ProviderKind.LLM, decision.provider)
    is_cloud = descriptor.capabilities.privacy is PrivacyClass.CLOUD
    redact = is_cloud and settings.llm.cloud.redact_phi_for_cloud

    sections = resolve_sections(
        [s.model_dump() for s in body.template.sections] if body.template and body.template.sections else None
    )
    transcript = redact_phi(body.transcript) if redact else body.transcript
    patient_context = (
        body.patient_context.model_dump(exclude_none=True) if body.patient_context else None
    )
    if redact and patient_context:
        patient_context = {k: redact_phi(str(v)) for k, v in patient_context.items()}
    messages = build_messages(
        transcript=transcript,
        sections=sections,
        patient_context=patient_context,
        language=body.language,
    )

    started = time.perf_counter()
    repaired = False
    try:
        completion = await provider.complete(
            messages,
            temperature=settings.llm.report_temperature,
            max_tokens=settings.llm.report_max_tokens,
            json_mode=True,
        )
        try:
            section_map = reconcile(parse_model_json(completion.text), sections)
        except ValueError as parse_exc:  # malformed JSON / missing sections
            if not settings.llm.repair_enabled:
                raise ApiError(
                    502,
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    f"model output was not valid JSON ({type(parse_exc).__name__}); "
                    "repair disabled via MS_LLM__REPAIR_ENABLED",
                ) from parse_exc
            repaired = True
            completion = await provider.complete(
                repair_messages(messages, completion.text, sections),
                temperature=0.0,
                max_tokens=settings.llm.report_max_tokens,
                json_mode=True,
            )
            try:
                section_map = reconcile(parse_model_json(completion.text), sections)
            except ValueError as parse_exc2:
                raise ApiError(
                    502,
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "model output still not valid JSON after one repair round-trip",
                ) from parse_exc2
    except ProviderError as exc:
        app.state.provider_health.record_failure(f"llm:{decision.provider}")
        metrics.incr(f"llm_errors:{decision.provider}")
        raise ApiError(
            502,
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"draft generation failed: {exc}",
            details={"provider": decision.provider, "retryable": exc.retryable},
        ) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    fidelity = grounding_warnings(transcript, section_map)
    usage = completion.usage
    title_by_id = {s.id: s.title for s in sections}

    # token accounting → metrics (ai_requests table lands with Phase 7; the
    # counters below are its future backfill source)
    metrics.incr("llm_requests")
    metrics.incr(f"llm_requests:{decision.provider}")
    metrics.observe_ms("llm_latency_ms", float(latency_ms))
    app.state.provider_health.record_success(f"llm:{decision.provider}", float(latency_ms))
    if usage is not None:
        for field_name, value in (
            ("prompt", usage.prompt_tokens),
            ("completion", usage.completion_tokens),
            ("total", usage.total_tokens),
        ):
            if value:
                metrics.incr(f"llm_tokens:{decision.provider}:{field_name}", int(value))

    app.state.audit.emit(
        "report_draft_generated",
        encounter_id=encounter_id,
        user_id=principal.user_id if principal else None,
        provider=decision.provider,
        model=completion.model,
        routing_reason=decision.reason,
        privacy_override=decision.privacy_override_applied,
        phi_redaction=redact,
        repaired=repaired,
        section_count=len(sections),
        warning_count=len(fidelity.warnings),
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        latency_ms=latency_ms,
    )

    return ReportDraftResponse(
        draft_id=f"drw_{uuid.uuid4().hex[:16]}",
        encounter_id=encounter_id,
        provider=decision.provider,
        model=completion.model,
        routing_reason=decision.reason,
        privacy_override_applied=decision.privacy_override_applied,
        phi_redaction_applied=bool(redact),
        language=body.language,
        template_name=body.template.name if body.template else None,
        sections=[
            DraftSectionOut(
                id=sid,
                title=title_by_id.get(sid, sid),
                markdown=text,
                missing=text.strip() == MISSING_TOKEN,
            )
            for sid, text in section_map.items()
        ],
        missing_sections=fidelity.missing_sections,
        warnings=[GroundingWarningOut(**w) for w in fidelity.warnings],
        usage=(
            UsageOut(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            )
            if usage
            else None
        ),
        latency_ms=latency_ms,
    )
