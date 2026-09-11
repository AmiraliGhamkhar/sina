"""Terminology endpoints (Phase 6, spec §6).

- ``GET  /api/v1/terminology`` — search the bilingual catalog (editor UI)
- ``POST /api/v1/terminology/normalize`` — reversible normalization preview

The stored transcript is NEVER rewritten by these endpoints; normalization
is a derived view the caller may render or discard.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api.auth.deps import OptionalPrincipal
from api.schemas.terminology import (
    NormalizeRequest,
    NormalizeResponse,
    SubstitutionOut,
    TerminologySearchResponse,
    TermOut,
)

router = APIRouter(prefix="/terminology", tags=["terminology"])


@router.get("", response_model=TerminologySearchResponse)
async def search_terms(
    request: Request,
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=25, ge=1, le=100),
    principal: OptionalPrincipal = None,
) -> TerminologySearchResponse:
    catalog = request.app.state.terminology.catalog
    hits = catalog.search(q, limit=limit)
    return TerminologySearchResponse(
        terms=[
            TermOut(canonical=e.canonical, category=e.category, variants=list(e.variants),
                    note=e.note)
            for e in hits
        ],
        total=len(hits),
    )


@router.post("/normalize", response_model=NormalizeResponse)
async def normalize_text(
    request: Request, body: NormalizeRequest, principal: OptionalPrincipal = None
) -> NormalizeResponse:
    result = request.app.state.terminology.normalize(body.text)
    return NormalizeResponse(
        normalized=result.normalized,
        substitutions=[
            SubstitutionOut(original=s.original, replacement=s.replacement, category=s.category)
            for s in result.substitutions
        ],
    )
