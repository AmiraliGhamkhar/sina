"""Report endpoints (Phase 4 draft + Phase 6 lifecycle, spec §5/§10).

Draft generation:
- grounded, strict-JSON with one repair round-trip (Phase 4);
- transcript arrives inline OR assembled from a live/finished session
  (markers → paragraphs/sections; terminology-normalized view for the
  prompt, original stored — Phase 6);
- template resolved server-side by key, inline spec, or the default;
- full clinical validation (numbers/doses/units, laterality, negation,
  dates, identifiers, anatomy) — warnings never auto-edit (spec §9);
- the draft is STORED server-side and enters the lifecycle.

Lifecycle (every step an explicit clinician action, spec §5 steps 9-12):
- PATCH   /reports/{id}            edit sections (draft/finalized only)
- POST    /reports/{id}/acknowledge  record warning justification
- POST    /reports/{id}/finalize     draft → finalized (criticals acked)
- POST    /reports/{id}/approve      finalized → approved (immutable after)
- POST    /reports/{id}/reopen       finalized → draft
- POST    /reports/{id}/amend        approved → new linked draft

Privacy notes:
- routing honors the same wall as transcription (privacy_default=true →
  LOCAL providers only);
- even when cloud IS allowed, identifier-shaped strings are scrubbed from
  the model input first (redact_phi_for_cloud, default on) and validation
  runs against the scrubbed text so redaction can never masquerade as
  fabrication.
"""
from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, Request

from ai.base import PrivacyClass, ProviderError, ProviderKind
from ai.router import NoEligibleProviderError, route
from api.auth.deps import OptionalPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.reports import (
    AcknowledgeRequest,
    DraftSectionOut,
    ReportDraftRequest,
    ReportDraftResponse,
    ReportListOut,
    ReportOut,
    ReportSectionEditRequest,
    UsageOut,
    WarningOut,
)
from api.services.ai_bridge import build_candidates, llm_route_request
from api.services.note_prompt import (
    MISSING_TOKEN,
    build_messages,
    parse_model_json,
    reconcile,
    redact_phi,
    repair_messages,
    resolve_sections,
)
from api.services.report_store import ReportStateError, ReportStore
from api.services.templates import TemplateService
from api.services.validation import ValidationWarning, validate_sections

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])

_ENC_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _report_out(report) -> ReportOut:
    summary = report.summary()
    return _full_out(report, summary)


def _full_out(report, summary) -> ReportOut:
    return ReportOut(
        report_id=report.report_id,
        draft_id=report.report_id,
        encounter_id=report.encounter_id,
        session_id=report.session_id,
        status=report.status,
        template_key=report.template_key,
        template_name=report.template_name,
        language=report.language,
        provider=report.provider,
        model=report.model,
        routing_reason=report.routing_reason,
        privacy_override_applied=report.privacy_override_applied,
        phi_redaction_applied=report.phi_redaction_applied,
        terminology_substitutions=report.terminology_substitutions,
        sections=[
            DraftSectionOut(
                id=sid,
                title=report.section_titles.get(sid, sid),
                markdown=text,
                missing=text.strip() == MISSING_TOKEN,
            )
            for sid, text in report.sections.items()
        ],
        warnings=[WarningOut(**w) for w in report.warning_dtos()],
        critical_warnings=summary["critical_warnings"],
        blocking_warnings=summary["blocking_warnings"],
        created_by=report.created_by,
        created_at=report.created_at,
        updated_at=report.updated_at,
        amended_from=report.amended_from,
    )


def _resolve_template_sections(app, body: ReportDraftRequest):
    """template_key → server template; else inline spec; else default."""
    service: TemplateService = app.state.template_service
    if body.template_key:
        template = service.get(body.template_key)
        if template is None:
            raise ApiError(
                404, ErrorCode.NOT_FOUND, f"template '{body.template_key}' not found"
            )
        return (
            [(s.id, s.title, s.instruction) for s in template.sections],
            template.key,
            template.name,
        )
    if body.template and body.template.sections:
        specs = resolve_sections([s.model_dump() for s in body.template.sections])
        return (
            [(s.id, s.title, s.instruction) for s in specs],
            None,
            body.template.name,
        )
    specs = resolve_sections(None)
    return ([(s.id, s.title, s.instruction) for s in specs], None, None)


def _session_transcript(app, session_id: str):
    """Assemble the stored session transcript (markers + terminology view)."""
    store = app.state.transcript_store
    assembled = store.assemble(session_id, normalizer=app.state.terminology)
    if assembled is None:
        raise ApiError(
            404, ErrorCode.NOT_FOUND, f"session '{session_id}' not found or expired"
        )
    if not assembled["text"].strip():
        raise ApiError(
            422, ErrorCode.VALIDATION, "session transcript is empty — nothing to draft from"
        )
    return assembled


# --------------------------------------------------------------------------
# draft generation
# --------------------------------------------------------------------------


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

    # -- transcript source ----------------------------------------------------
    terminology_substitutions = 0
    if body.session_id is not None:
        assembled = _session_transcript(app, body.session_id)
        raw_transcript = _reconstruct_raw(app, body.session_id)
        terminology_substitutions = len(assembled.get("substitutions") or [])
        transcript_source = "session"
    elif body.transcript:
        raw_transcript = body.transcript
        normalized_view = app.state.terminology.normalize(body.transcript)
        terminology_substitutions = normalized_view.count
        assembled = {"text": normalized_view.normalized, "substitutions": []}
        transcript_source = "inline"
    else:
        raise ApiError(
            422, ErrorCode.VALIDATION, "provide either 'transcript' or 'session_id'"
        )

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
    spec_tuples, template_key, template_name = _resolve_template_sections(app, body)
    sections = resolve_sections(
        [{"id": i, "title": t, "instruction": ins} for i, t, ins in spec_tuples]
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
    grounded_against = ""
    attempts: list[dict] = []
    last_retryable: bool | None = None
    attempt_started = 0.0

    chain = [decision.provider, *(decision.fallbacks if settings.routing.fallback_enabled else ())]
    for attempt_name in chain:
        attempts_entry: dict = {"provider": attempt_name}
        descriptor = registry.get_descriptor(ProviderKind.LLM, attempt_name)
        is_cloud = descriptor.capabilities.privacy is PrivacyClass.CLOUD
        redact = is_cloud and settings_llm.cloud.redact_phi_for_cloud

        # prompt transcript: terminology-normalized view (canonical lexicon),
        # then PHI redaction for cloud candidates
        transcript = assembled["text"]
        if redact:
            transcript = redact_phi(transcript)
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
                    attempts_entry["error"] = "model output still not valid JSON after one repair round-trip"
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
            continue

        used_provider = attempt_name
        repaired = attempt_repaired
        # transcript redacted for THIS provider is what validation must
        # compare against (redaction is not fabrication)
        grounded_against = transcript
        metrics.incr("llm_attempts", 1)
        metrics.observe_ms("llm_latency_ms", float((time.perf_counter() - attempt_started) * 1000))
        break

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

    # -- Phase 6 validation (grounded against raw + normalized evidence) --------
    fidelity = validate_sections(
        raw_transcript if not redact else grounded_against,
        section_map,
        normalized_transcript=grounded_against,
    )
    warnings: list[ValidationWarning] = list(fidelity.warnings)
    if used_provider != decision.provider:
        # honest disclosure: a fallback answered, not the routed primary
        warnings.append(
            ValidationWarning(
                code="provider_fallback",
                severity="info",
                message=(
                    f"'{decision.provider}' could not complete the task; "
                    f"draft generated by fallback '{used_provider}'"
                ),
            )
        )
        metrics.incr("llm_fallbacks")

    # token accounting → metrics + cost ledger (ai_requests table lands with
    # Phase 7; counters/ledger below are its future backfill source)
    usage = completion.usage
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

    title_by_id = {s.id: s.title for s in sections}
    report = app.state.report_store.create_draft(
        encounter_id=encounter_id,
        session_id=body.session_id,
        template_key=template_key,
        template_name=template_name,
        language=body.language,
        sections=section_map,
        section_titles=title_by_id,
        warnings=warnings,
        provider=used_provider,
        model=completion.model,
        routing_reason=decision.reason,
        privacy_override_applied=decision.privacy_override_applied,
        phi_redaction_applied=bool(redact),
        terminology_substitutions=terminology_substitutions,
        created_by=principal.user_id if principal else None,
    )

    app.state.audit.emit(
        "report_draft_generated",
        report_id=report.report_id,
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
        warning_count=len(warnings),
        critical_warning_count=sum(1 for w in warnings if w.severity == "critical"),
        terminology_substitutions=terminology_substitutions,
        transcript_source=transcript_source,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        latency_ms=latency_ms,
    )

    missing_sections = fidelity.missing_sections
    return ReportDraftResponse(
        report_id=report.report_id,
        draft_id=report.report_id,
        encounter_id=encounter_id,
        session_id=body.session_id,
        status=report.status,
        provider=used_provider,
        model=completion.model,
        routing_reason=decision.reason,
        privacy_override_applied=decision.privacy_override_applied,
        phi_redaction_applied=bool(redact),
        language=body.language,
        template_key=template_key,
        template_name=template_name,
        sections=[
            DraftSectionOut(
                id=sid,
                title=title_by_id.get(sid, sid),
                markdown=text,
                missing=text.strip() == MISSING_TOKEN,
            )
            for sid, text in section_map.items()
        ],
        missing_sections=missing_sections,
        warnings=[WarningOut(**w) for w in report.warning_dtos()],
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
        terminology_substitutions=terminology_substitutions,
        transcript_source=transcript_source,
    )


def _reconstruct_raw(app, session_id: str) -> str:
    """Raw (un-normalized) transcript text for validation evidence — the
    stored segments joined with marker structure, original wording intact."""
    store = app.state.transcript_store
    t = store.get(session_id)
    if t is None:
        return ""
    parts: list[str] = []
    for s in t.segments:
        if s.kind == "paragraph":
            parts.append("\n\n")
        elif s.kind == "section":
            parts.append(f"\n\n## {(s.meta or {}).get('section_title', '')}\n")
        elif s.kind == "finalized_section":
            parts.append("\n\n[section finalized]\n")
        elif s.text.strip():
            parts.append(s.text.strip())
            parts.append(" ")
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


@router.get("", response_model=ReportListOut)
async def list_reports(
    request: Request,
    encounter_id: str | None = None,
    principal: OptionalPrincipal = None,
) -> ReportListOut:
    store: ReportStore = request.app.state.report_store
    reports = store.list_by_encounter(encounter_id) if encounter_id else []
    return ReportListOut(
        reports=[_report_out(r) for r in reports], total=len(reports)
    )


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(
    request: Request, report_id: str, principal: OptionalPrincipal = None
) -> ReportOut:
    report = request.app.state.report_store.get(report_id)
    if report is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"report '{report_id}' not found or expired")
    return _report_out(report)


@router.patch("/{report_id}", response_model=ReportOut)
async def edit_report(
    request: Request,
    report_id: str,
    body: ReportSectionEditRequest,
    principal: OptionalPrincipal = None,
) -> ReportOut:
    try:
        report = request.app.state.report_store.edit_sections(
            report_id, body.sections, user_id=principal.user_id if principal else None
        )
    except ReportStateError as exc:
        raise _state_error(exc) from exc
    request.app.state.audit.emit(
        "report_sections_edited",
        report_id=report_id,
        section_ids=sorted(body.sections),
        user_id=principal.user_id if principal else None,
    )
    return _report_out(report)


@router.post("/{report_id}/acknowledge", response_model=ReportOut)
async def acknowledge_warning(
    request: Request,
    report_id: str,
    body: AcknowledgeRequest,
    principal: OptionalPrincipal = None,
) -> ReportOut:
    try:
        report = request.app.state.report_store.acknowledge(
            report_id,
            body.warning_id,
            body.justification,
            user_id=principal.user_id if principal else None,
        )
    except ReportStateError as exc:
        raise _state_error(exc) from exc
    request.app.state.audit.emit(
        "warning_acknowledged",
        report_id=report_id,
        warning_id=body.warning_id,
        user_id=principal.user_id if principal else None,
    )
    return _report_out(report)


@router.post("/{report_id}/finalize", response_model=ReportOut)
async def finalize_report(
    request: Request, report_id: str, principal: OptionalPrincipal = None
) -> ReportOut:
    try:
        report = request.app.state.report_store.finalize(
            report_id, user_id=principal.user_id if principal else None
        )
    except ReportStateError as exc:
        raise _state_error(exc) from exc
    request.app.state.audit.emit(
        "report_finalized", report_id=report_id, user_id=principal.user_id if principal else None
    )
    return _report_out(report)


@router.post("/{report_id}/approve", response_model=ReportOut)
async def approve_report(
    request: Request, report_id: str, principal: OptionalPrincipal = None
) -> ReportOut:
    try:
        report = request.app.state.report_store.approve(
            report_id, user_id=principal.user_id if principal else None
        )
    except ReportStateError as exc:
        raise _state_error(exc) from exc
    request.app.state.audit.emit(
        "report_approved", report_id=report_id, user_id=principal.user_id if principal else None
    )
    return _report_out(report)


@router.post("/{report_id}/reopen", response_model=ReportOut)
async def reopen_report(
    request: Request, report_id: str, principal: OptionalPrincipal = None
) -> ReportOut:
    try:
        report = request.app.state.report_store.reopen(
            report_id, user_id=principal.user_id if principal else None
        )
    except ReportStateError as exc:
        raise _state_error(exc) from exc
    request.app.state.audit.emit(
        "report_reopened", report_id=report_id, user_id=principal.user_id if principal else None
    )
    return _report_out(report)


@router.post("/{report_id}/amend", response_model=ReportOut, status_code=201)
async def amend_report(
    request: Request, report_id: str, principal: OptionalPrincipal = None
) -> ReportOut:
    try:
        report = request.app.state.report_store.amend(
            report_id, user_id=principal.user_id if principal else None
        )
    except ReportStateError as exc:
        raise _state_error(exc) from exc
    request.app.state.audit.emit(
        "report_amendment_created",
        report_id=report.report_id,
        amended_from=report_id,
        user_id=principal.user_id if principal else None,
    )
    return _report_out(report)


def _state_error(exc: ReportStateError) -> ApiError:
    if "not found" in exc.reason:
        return ApiError(404, ErrorCode.NOT_FOUND, exc.reason)
    return ApiError(409, ErrorCode.SESSION_STATE, exc.reason)
