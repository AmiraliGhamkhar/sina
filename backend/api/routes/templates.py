"""Report-template CRUD + LLM-assisted extraction (Phase 6, spec §10).

Data-driven templates: the WPF Templates screen renders exactly what these
endpoints return — no template identity is hard-coded client-side. Built-ins
(radiology/us/ct/mri/soap/general) are immutable; clinicians fork or create
custom ones.

``POST /report-templates/extract`` (Phlox concept, adapted): analyze an
example note with the routed LLM and PROPOSE section structure — the result
is never persisted automatically; the clinician reviews and explicitly
creates it. The privacy wall applies like every LLM call.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Request

from ai.base import PrivacyClass, ProviderError, ProviderKind
from ai.router import NoEligibleProviderError
from api.auth.deps import OptionalPrincipal
from api.errors import ApiError, ErrorCode
from api.schemas.templates import (
    TemplateCreateRequest,
    TemplateExtractRequest,
    TemplateExtractResponse,
    TemplateForkRequest,
    TemplateListOut,
    TemplateOut,
    TemplateSectionOut,
    TemplateUpdateRequest,
)
from api.services.ai_bridge import build_candidates, llm_route_request
from api.services.note_prompt import parse_model_json, redact_phi
from api.services.templates import TemplateError, TemplateService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/report-templates", tags=["templates"])


def _service(request: Request) -> TemplateService:
    return request.app.state.template_service


def _to_out(t) -> TemplateOut:
    return TemplateOut(**{k: v for k, v in t.as_dict().items() if k != "deleted"})


@router.get("", response_model=TemplateListOut)
async def list_templates(request: Request, principal: OptionalPrincipal = None) -> TemplateListOut:
    templates = _service(request).list()
    return TemplateListOut(
        templates=[_to_out(t) for t in templates], total=len(templates)
    )


@router.get("/{key}", response_model=TemplateOut)
async def get_template(request: Request, key: str, principal: OptionalPrincipal = None) -> TemplateOut:
    template = _service(request).get(key)
    if template is None:
        raise ApiError(404, ErrorCode.NOT_FOUND, f"template '{key}' not found")
    return _to_out(template)


@router.post("", response_model=TemplateOut, status_code=201)
async def create_template(
    request: Request, body: TemplateCreateRequest, principal: OptionalPrincipal = None
) -> TemplateOut:
    try:
        template = _service(request).create(
            key=body.key,
            name=body.name,
            category=body.category,
            description=body.description,
            sections=[s.model_dump() for s in body.sections],
        )
    except TemplateError as exc:
        raise ApiError(422, ErrorCode.VALIDATION, str(exc)) from exc
    request.app.state.audit.emit(
        "template_created",
        template_key=template.key,
        section_count=len(template.sections),
        user_id=principal.user_id if principal else None,
    )
    return _to_out(template)


@router.patch("/{key}", response_model=TemplateOut)
async def update_template(
    request: Request,
    key: str,
    body: TemplateUpdateRequest,
    principal: OptionalPrincipal = None,
) -> TemplateOut:
    try:
        template = _service(request).update(
            key,
            name=body.name,
            description=body.description,
            sections=[s.model_dump() for s in body.sections] if body.sections is not None else None,
        )
    except TemplateError as exc:
        raise ApiError(422, ErrorCode.VALIDATION, str(exc)) from exc
    request.app.state.audit.emit(
        "template_updated", template_key=key, version=template.version,
        user_id=principal.user_id if principal else None,
    )
    return _to_out(template)


@router.delete("/{key}", status_code=204)
async def delete_template(
    request: Request, key: str, principal: OptionalPrincipal = None
) -> None:
    try:
        _service(request).delete(key)
    except TemplateError as exc:
        raise ApiError(422, ErrorCode.VALIDATION, str(exc)) from exc
    request.app.state.audit.emit(
        "template_deleted", template_key=key, user_id=principal.user_id if principal else None
    )


@router.post("/{key}/fork", response_model=TemplateOut, status_code=201)
async def fork_template(
    request: Request, key: str, body: TemplateForkRequest, principal: OptionalPrincipal = None
) -> TemplateOut:
    try:
        template = _service(request).fork(key, new_key=body.new_key, new_name=body.new_name)
    except TemplateError as exc:
        raise ApiError(422, ErrorCode.VALIDATION, str(exc)) from exc
    request.app.state.audit.emit(
        "template_forked", from_key=key, to_key=template.key,
        user_id=principal.user_id if principal else None,
    )
    return _to_out(template)


# -- LLM-assisted extraction (Phlox concept, adapted) ---------------------------

_EXTRACT_SYSTEM = """You are a clinical documentation structure analyst.
Analyze the EXAMPLE NOTE and propose a report template section list.
Rules:
1. Propose ONLY sections that are actually present as headings/paragraph
   blocks in the note — never invent sections the note does not contain.
2. Section ids: lowercase ascii [a-z0-9_] only, derived from the heading.
3. Keep the note's own language for titles; instructions in one short line.
4. format_style is one of: narrative, bullets, numbered, lab_values,
   heading_with_bullets — match what the note actually uses.
5. Output STRICT JSON only: {"suggested_name": str, "note_type": str,
   "sections": [{"id": str, "title": str, "instruction": str,
   "required": bool, "format_style": str}]} — no prose, no fences."""

_SECTION_ID_CLEAN = re.compile(r"[^a-z0-9_]+")


def _clean_section_id(raw: str, index: int) -> str:
    sid = _SECTION_ID_CLEAN.sub("_", (raw or "").strip().lower()).strip("_")
    if not sid:
        sid = f"section_{index}"
    return sid[:64]


@router.post("/extract", response_model=TemplateExtractResponse)
async def extract_template(
    request: Request, body: TemplateExtractRequest, principal: OptionalPrincipal = None
) -> TemplateExtractResponse:
    app = request.app
    settings = app.state.settings
    try:
        decision = route_llm(app, body)
    except NoEligibleProviderError as exc:
        raise ApiError(503, ErrorCode.NO_PROVIDER, str(exc)) from exc

    descriptor = app.state.ai_registry.get_descriptor(ProviderKind.LLM, decision.provider)
    is_cloud = descriptor.capabilities.privacy is PrivacyClass.CLOUD
    redact = is_cloud and settings.llm.cloud.redact_phi_for_cloud
    note_text = redact_phi(body.example_note) if redact else body.example_note

    user_msg = (
        "<<<EXAMPLE_NOTE>>>\n"
        + note_text.strip()[:15_000]
        + "\n<<<END EXAMPLE_NOTE>>>\n"
        + (f"Suggested name: {body.suggested_name}\n" if body.suggested_name else "")
        + "Return the strict JSON object now."
    )
    from ai.base import LLMMessage

    messages = [
        LLMMessage(role="system", content=_EXTRACT_SYSTEM),
        LLMMessage(role="user", content=user_msg),
    ]
    for attempt_name in [decision.provider, *decision.fallbacks]:
        try:
            provider = app.state.ai_registry.create(
                ProviderKind.LLM, attempt_name, settings.provider_config("llm", attempt_name)
            )
            completion = await provider.complete(
                messages, temperature=0.0, max_tokens=1200, json_mode=True
            )
            data = parse_model_json(completion.text)
        except (ProviderError, ValueError) as exc:
            app.state.provider_health.record_failure(f"llm:{attempt_name}")
            logger.debug("template extraction failed on %s: %s", attempt_name, exc)
            continue
        app.state.provider_health.record_success(f"llm:{attempt_name}", 0.0)
        app.state.metrics.incr("llm_requests")
        app.state.metrics.incr(f"llm_requests:{attempt_name}")
        app.state.audit.emit(
            "template_extraction",
            provider=attempt_name,
            user_id=principal.user_id if principal else None,
            section_count=len(data.get("sections") or []),
            phi_redaction=redact,
        )
        sections_out: list[TemplateSectionOut] = []
        for i, raw in enumerate(data.get("sections") or []):
            if not isinstance(raw, dict):
                continue
            style = str(raw.get("format_style") or "narrative")
            if style not in ("narrative", "bullets", "numbered", "lab_values",
                             "heading_with_bullets"):
                style = "narrative"
            sections_out.append(
                TemplateSectionOut(
                    id=_clean_section_id(str(raw.get("id") or ""), i),
                    title=str(raw.get("title") or raw.get("id") or "")[:160],
                    instruction=str(raw.get("instruction") or "")[:500],
                    required=bool(raw.get("required")),
                    format_style=style,
                )
            )
        if not sections_out:
            continue  # unparseable structure — try next provider
        return TemplateExtractResponse(
            suggested_name=str(data.get("suggested_name") or body.suggested_name or "Custom template")[:120],
            note_type=str(data.get("note_type") or "")[:80],
            sections=sections_out[:20],
            provider=attempt_name,
            model=completion.model,
        )
    raise ApiError(
        502,
        ErrorCode.PROVIDER_UNAVAILABLE,
        "template extraction failed on all routed providers",
        details={"provider": decision.provider, "tried": [decision.provider, *decision.fallbacks]},
    )


def route_llm(app, body: TemplateExtractRequest):
    from ai.router import route

    return route(
        llm_route_request(
            app, mode=body.mode, privacy_required=body.privacy_required, provider=body.provider
        ),
        build_candidates(app, ProviderKind.LLM),
    )

