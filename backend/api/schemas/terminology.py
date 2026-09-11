"""Terminology wire schemas (Phase 6, spec §6)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TermOut(BaseModel):
    canonical: str
    category: str
    variants: list[str] = Field(default_factory=list)
    note: str = ""


class TerminologySearchResponse(BaseModel):
    terms: list[TermOut]
    total: int


class NormalizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=80_000)


class SubstitutionOut(BaseModel):
    original: str
    replacement: str
    category: str


class NormalizeResponse(BaseModel):
    normalized: str
    substitutions: list[SubstitutionOut] = Field(default_factory=list)
    #: stored originals are never modified — the caller holds them
    reversible: bool = True
