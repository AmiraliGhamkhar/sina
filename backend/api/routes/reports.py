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
from ai.router import NoEligibleProviderError, route
from api.auth.deps import OptionalPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.reports import (
    DraftSectionOut,
    GroundingWarningOut,
    ReportDraftRequest,
    ReportDraftResponse,
    UsageOut,
)
from api.services.ai_bridge import build_candidates, llm_route_request
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
        route_request = llm_route_request(
            app,
            mode=body.mode,
            privacy_required=body.privacy_required,
            provider=body.provider,
        )
        decision = route(route_request, build_candidates(app, ProviderKind.LLM))
    except NoEligibleProviderError as exc:
        raise ApiError(503, ErrorCode.NO_PROVIDER, str(exc)) from exc

    settings_llm = settings.llm
    registry = app.state.ai_registry
    sections = resolve_sections(
        [s.model_dump() for s in body.template.sections] if body.template and body.template.sections else None
    )
    raw_context = body.patient_context.model_dump(exclude_none=True) if body.patient_context else None

    # Phase 5: walk the router's chain (primary + privacy-safe fallbacks);
    # redaction policy is re-derived per candidate because it is cloud-specific.
    used_provider: str | None = None
    completion = None
    section_map: dict[str, str] = {}
    repaired = False
    is_cloud = False
    redact = False
    output_error: str | None = None
    attempts: list[dict] = []
    last_retryable: bool | None = None

    chain = [decision.provider, *(decision.fallbacks if settings.routing.fallback_enabled else ())]
    for attempt_name in chain:
        attempts_entry: dict = {"provider": attempt_name}
        descriptor = registry.get_descriptor(ProviderKind.LLM, attempt_name)
        is_cloud = descriptor.capabilities.privacy is PrivacyClass.CLOUD
        redact = is_cloud and settings_llm.cloud.redact_phi_for_cloud

        transcript = redact_phi(body.transcript) if redact else body.transcript
        patient_context = dict(raw_context) if raw_context else None
        if redact and patient_context:
            patient_context = {k: redact_phi(str(v)) for k, v in patient_context.items()}
        messages = build_messages(
            transcript=transcript,
            sections=sections,
            patient_context=patient_context,
            language=body.language,
        )
        try:
            provider = registry.create(
                ProviderKind.LLM, attempt_name, settings.provider_config("llm", attempt_name)
            )
        except ProviderError as exc:
            app.state.provider_health.record_failure(f"llm:{attempt_name}")
            attempts_entry["error"] = f"initialization: {exc}"
            last_retryable = exc.retryable
            attempts.append(attempts_entry)
            if not exc.retryable:
                break
            continue

        attempt_started = time.perf_counter()
        attempt_repaired = False
        try:
            completion = await provider.complete(
                messages,
                temperature=settings_llm.report_temperature,
                max_tokens=settings_llm.report_max_tokens,
                json_mode=True,
            )
            try:
                section_map = reconcile(parse_model_json(completion.text), sections)
            except ValueError as parse_exc:  # malformed JSON / missing sections
                if not settings_llm.repair_enabled:
                    raise ApiError(
                        502,
                        ErrorCode.PROVIDER_UNAVAILABLE,
                        f"model output was not valid JSON ({type(parse_exc).__name__}); "
                        "repair disabled via MS_LLM__REPAIR_ENABLED",
                    ) from parse_exc
                attempt_repaired = True
                completion = await provider.complete(
                    repair_messages(messages, completion.text, sections),
                    temperature=0.0,
                    max_tokens=settings_llm.report_max_tokens,
                    json_mode=True,
                )
                try:
                    section_map = reconcile(parse_model_json(completion.text), sections)
                except ValueError:
                    output_error = "model output still not valid JSON after one repair round-trip"
                    attempts_entry["error"] = output_error
                    attempts.append(attempts_entry)
                    continue  # next provider may format better
        except ProviderError as exc:
            app.state.provider_health.record_failure(f"llm:{attempt_name}")
            metrics.incr(f"llm_errors:{attempt_name}")
            attempts_entry["error"] = str(exc)[:300]
            last_retryable = exc.retryable
            attempts.append(attempts_entry)
            if not exc.retryable:
                break
            output_error = f"draft generation failed: {exc}"
            continue

        used_provider = attempt_name
        repaired = attempt_repaired
        # transcript redacted for THIS provider is what fidelity checking must
        # compare against (redaction is not fabrication)
        grounded_against = transcript
        metrics.incr("llm_attempts", 1)
        metrics.observe_ms("llm_latency_ms", float((time.perf_counter() - attempt_started) * 1000))
        break
    else:
        used_provider = None

    if used_provider is None or completion is None:
        primary_fail = attempts[0] if attempts else {"error": "no attempt recorded"}
        raise ApiError(
            502,
            ErrorCode.PROVIDER_UNAVAILABLE,
            primary_fail.get("error", "draft generation failed")[:400],
            details={
                "provider": decision.provider,
                "retryable": True if last_retryable is None else last_retryable,
                "tried": attempts,
            },
        )

    latency_ms = int((time.perf_counter() - attempt_started) * 1000)
    fidelity = grounding_warnings(grounded_against, section_map)
    usage = completion.usage
    title_by_id = {s.id: s.title for s in sections}

    if used_provider != decision.provider:
        # honest disclosure: a fallback answered, not the routed primary
        fidelity.warnings.append(
            {
                "code": "provider_fallback",
                "message": (
                    f"'{decision.provider}' could not complete the task; "
                    f"draft generated by fallback '{used_provider}'"
                ),
            }
        )
        metrics.incr("llm_fallbacks")

    # token accounting → metrics + cost ledger (ai_requests table lands with
    # Phase 7; counters/ledger below are its future backfill source)
    metrics.incr("llm_requests")
    metrics.incr(f"llm_requests:{used_provider}")
    app.state.provider_health.record_success(f"llm:{used_provider}", float(latency_ms))
    ledger = getattr(app.state, "cost_ledger", None)
    if usage is not None:
        for field_name, value in (
            ("prompt", usage.prompt_tokens),
            ("completion", usage.completion_tokens),
            ("total", usage.total_tokens),
        ):
            if value:
                metrics.incr(f"llm_tokens:{used_provider}:{field_name}", int(value))
        if ledger is not None and usage.total_tokens:
            ledger.add_usage(used_provider, int(usage.total_tokens))

    app.state.audit.emit(
        "report_draft_generated",
        encounter_id=encounter_id,
        user_id=principal.user_id if principal else None,
        provider=used_provider,
        routed_provider=decision.provider,
        model=completion.model,
        routing_reason=decision.reason,
        privacy_override=decision.privacy_override_applied,
        budget_soft_stop=decision.budget_soft_stop,
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
        provider=used_provider,
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
