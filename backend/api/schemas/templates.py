"""Report-template wire schemas (Phase 6, spec §10 — data-driven templates)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TemplateSectionIn(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    title: str = Field(default="", max_length=160)
    instruction: str = Field(default="", max_length=500)
    required: bool = False
    format_style: str = Field(default="narrative", pattern=r"^(narrative|bullets|numbered|lab_values|heading_with_bullets)$")


class TemplateSectionOut(TemplateSectionIn):
    pass


class TemplateCreateRequest(BaseModel):
    key: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="custom", max_length=32)
    description: str = Field(default="", max_length=500)
    sections: list[TemplateSectionIn] = Field(min_length=1, max_length=20)


class TemplateUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    sections: list[TemplateSectionIn] | None = Field(default=None, min_length=1, max_length=20)


class TemplateForkRequest(BaseModel):
    new_key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]+$")
    new_name: str | None = Field(default=None, max_length=120)


class TemplateOut(BaseModel):
    key: str
    name: str
    category: str
    description: str = ""
    sections: list[TemplateSectionOut] = Field(default_factory=list)
    builtin: bool = False
    version: int = 1
    created_at: str = ""
    updated_at: str = ""


class TemplateListOut(BaseModel):
    templates: list[TemplateOut]
    total: int


class TemplateExtractRequest(BaseModel):
    """LLM-assisted template extraction from an example note (Phlox concept).
    Returns an UNSAVED draft — the clinician reviews and explicitly creates."""

    example_note: str = Field(min_length=20, max_length=20_000)
    suggested_name: str | None = Field(default=None, max_length=120)
    mode: str | None = None
    privacy_required: bool | None = None
    provider: str | None = Field(default=None, max_length=64)


class TemplateExtractResponse(BaseModel):
    suggested_name: str
    note_type: str = ""
    sections: list[TemplateSectionOut]
    provider: str
    model: str | None = None
    edited_by_clinician_required: bool = True
