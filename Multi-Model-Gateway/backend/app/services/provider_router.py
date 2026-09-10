"""ProviderRouter — one decision for chat/agent/research provider resolution (#3).

External seam: `resolve(request, user_id, db) → Resolved` where Resolved is
(provider adapter, model, role). Internally: pinned → default-for-role → env
fallback. Heuristic `resolve_role(text, provider, is_private, message_count)`
is pure and testable without a DB. Two depths: the router hides the fallback
chain; the LLMProvider adapters hide wire differences. Two adapters justify each
seam.

This is the SINGLE entry point for provider resolution. The legacy
`services/router.py::get_provider` shim and its duplicate `resolve_role` were
removed; `routers/chat.py`, `services/agent/agent.py`, `routers/agents.py`, and
Smart Suggest all call `ProviderRouter().resolve()` directly.

Deletion test: deleting this scatters the pinned/default/fallback chain and
heuristic keywords back into agent.py + chat.py + research.py + suggest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings, get_openrouter_api_key
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.providers import Provider as ProviderRow
from app.services.provider_registry import (
    ProviderConfigError,
    get_default_provider,
    row_to_provider,
)
from app.services.providers import LLMProvider, OpenAICompatProvider, OpenRouterProvider
from app.services.router import ChatRequest, Provider as ProviderChoice


def resolve_role(
    *,
    text: str,
    provider_choice: str,
    is_private: bool,
    message_count: int,
) -> str:
    """Pure heuristic: which role handles this request. No DB, no IO.

    Mirrors the legacy match/case in router.resolve_role exactly.
    """
    lower = text.lower()
    code = ["script", "code", "function", "debug", "bug", "python", "c++", "java", "javascript", "typescript"]

    if is_private:
        return "local"
    if provider_choice == ProviderChoice.local:
        return "local"
    if provider_choice == ProviderChoice.openrouter:
        return "cloud"
    if any(k in lower for k in code):
        return "cloud"
    if message_count > 80:
        return "cloud"
    return "local"


@dataclass(frozen=True)
class Resolved:
    provider: LLMProvider
    model: str
    role: str  # "local" | "cloud"


async def _resolve_pinned(request: ChatRequest, user_id: str, db: AsyncSession) -> Resolved | None:
    if not request.provider_id:
        return None
    result = await db.execute(select(ProviderRow).where(ProviderRow.id == request.provider_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"provider {request.provider_id} not found")
    if str(row.user_id) != str(user_id):
        raise ForbiddenError("unauthorised")
    if request.model == "auto" and row.default_model is None:
        raise ProviderConfigError(
            f"provider '{row.name}' has no default model configured; set a default model or pass an explicit model"
        )
    provider = row_to_provider(row)
    model = request.model if request.model != "auto" else row.default_model  # type: ignore[assignment]
    return Resolved(provider=provider, model=model, role=row.role)


async def _resolve_default_for_role(request: ChatRequest, user_id: str, role: str, db: AsyncSession) -> Resolved | None:
    row = await get_default_provider(db, user_id, role)
    if row is None:
        return None
    if request.model == "auto" and row.default_model is None:
        raise ProviderConfigError(
            f"provider '{row.name}' has no default model configured; set a default model or pass an explicit model"
        )
    provider = row_to_provider(row)
    model = request.model if request.model != "auto" else row.default_model  # type: ignore[assignment]
    return Resolved(provider=provider, model=model, role=role)


def _fallback(role: str, request: ChatRequest) -> Resolved:
    if role == "local":
        provider = OpenAICompatProvider(
            base_url=settings.LM_URL,
            api_key="LM-STUDIO",
            default_model=settings.LM_CHAT_MODEL or settings.LM_DEFAULT_MODEL,
        )
        model = request.model if request.model != "auto" else provider.default_model
        return Resolved(provider=provider, model=model, role=role)
    key = get_openrouter_api_key()
    if not key:
        raise RuntimeError("OpenRouter is not configured (no API key)")
    provider = OpenRouterProvider(api_key=key, default_model=settings.OPENROUTER_DEFAULT_MODEL)
    model = request.model if request.model != "auto" else provider.default_model
    return Resolved(provider=provider, model=model, role=role)


class ProviderRouter:
    """Deep router: one method, one error contract, hidden fallback chain."""

    async def resolve(self, request: ChatRequest, user_id: str, db: AsyncSession) -> Resolved:
        pinned = await _resolve_pinned(request, user_id, db)
        if pinned is not None:
            return pinned
        role = resolve_role(
            text=request.messages[-1].content,
            provider_choice=request.provider,
            is_private=bool(request.private),
            message_count=len(request.messages),
        )
        via_default = await _resolve_default_for_role(request, user_id, role, db)
        if via_default is not None:
            return via_default
        return _fallback(role, request)
