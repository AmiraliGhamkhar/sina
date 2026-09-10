"""Smart Suggest — draft an Agent config from a goal string (deep module).

External seam: one async function `suggest(body, user_id, db) → Suggest` where
Suggest carries the drafted name/description/system_prompt/tools/model. The
router (`routers/agents.py`) is a thin POST handler that translates the service
outcome to HTTP.

Internally: build a meta-prompt from the goal, call the provider cloud-then-
local (free-model aware), parse the model's JSON output, and sanitize it against
the registered tools. Two adapters justify the seam: the real LLMProvider via
`ProviderRouter` and an in-memory fake in tests.

Deletion test: deleting this scatters the meta-prompt, the JSOON parse/sanitize,
and the cloud→local fallback back into the router file.

The service never raises HTTPException — on failure it raises `SuggestError`
(domain error), which the router translates to a 502 with a useful hint.
"""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.tools import registry
from app.services.provider_router import ProviderRouter
from app.services.router import ChatRequest, Provider


class SuggestError(Exception):
    """Raised when no tier can produce a usable suggestion. Carries a user-facing detail."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Suggest:
    name: str
    description: str
    system_prompt: str
    suggested_tools: list[str]
    suggested_model: str | None


logger = logging.getLogger(__name__)


def _looks_like_auth_error(msg: str) -> bool:
    low = (msg or "").lower()
    return (
        "user not found" in low
        or "invalid api key" in low
        or "authentication" in low
        or ("401" in low and "unauthorized" in low)
    )


def _parse_suggest_json(raw: str) -> dict | None:
    """Best-effort JSON parse of the model's raw output (may be wrapped in prose)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        obj = _json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = _json.loads(raw[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            return None
    return None


def _build_suggest(obj: dict, goal: str, known_tools: list[str]) -> Suggest:
    name = str(obj.get("name") or goal[:60]).strip()[:128] or goal[:60]
    description = str(obj.get("description") or "").strip()[:512]
    system_prompt = str(obj.get("system_prompt") or "").strip()[:4000]
    suggested_tools = obj.get("suggested_tools") or []
    if not isinstance(suggested_tools, list):
        suggested_tools = []
    known_set = set(known_tools)
    suggested_tools = [str(t) for t in suggested_tools if str(t) in known_set][:10]
    suggested_model = obj.get("suggested_model")
    if suggested_model is not None:
        suggested_model = str(suggested_model).strip()[:128] or None
    return Suggest(
        name=name,
        description=description or f"Agent for: {goal[:120]}",
        system_prompt=system_prompt or f"You are a helpful assistant focused on: {goal}",
        suggested_tools=suggested_tools,
        suggested_model=suggested_model,
    )


def _cloud_candidates() -> list[str]:
    """Ordered cloud model candidates: explicit SUGGEST_CLOUD_MODEL, then free fallbacks."""
    out: list[str] = []
    if settings.SUGGEST_CLOUD_MODEL.strip():
        out.append(settings.SUGGEST_CLOUD_MODEL.strip())
    out.extend(m.strip() for m in settings.SUGGEST_CLOUD_FALLBACK_MODELS.split(",") if m.strip())
    return out


def build_meta_prompt(goal: str, description: str | None, known_tools: list[str]) -> list[dict]:
    """Turn a goal string into the meta-prompt messages for the configurator model."""
    tool_list_hint = ", ".join(known_tools[:20]) if known_tools else "(none registered)"
    meta_prompt = (
        "You are an agent configurator for llm-gateway. Given the user's goal, "
        "draft a JSON object with exactly these keys: "
        '{"name": string (short, <=60 chars), '
        '"description": string (one sentence), '
        '"system_prompt": string (2-4 sentences, imperative instructions for the agent), '
        '"suggested_tools": string[] (subset of available tools), '
        '"suggested_model": string|null}.\n'
        f"Available tools: {tool_list_hint}.\n"
        "Return ONLY valid JSON, no prose, no markdown.\n\n"
        f"Goal: {goal}\n"
        + (f"Context: {description}\n" if description else "")
    )
    return [{"role": "user", "content": meta_prompt}]


async def _try_cloud(
    messages: list[dict], user_id: str, db: AsyncSession, known_tools: list[str], goal: str
) -> tuple[Suggest | None, str | None]:
    """Try OpenRouter with ordered :free candidates.

    Returns (Suggest, None) on success, else (None, last_error). `last_error`
    is the last failure message so `suggest()` can build a useful hint when both
    tiers fail.
    """
    try:
        req = ChatRequest(messages=messages, model="auto", provider=Provider.openrouter)  # type: ignore[arg-type]
        resolved = await ProviderRouter().resolve(req, user_id, db)
    except Exception as e:  # noqa: BLE001 — no cloud configured is expected
        msg = str(e)
        logger.warning("suggest cloud unavailable: %s — falling back to local", msg)
        return None, msg

    candidates: list[str] = []
    if resolved.model and resolved.model.strip().endswith(":free"):
        candidates.append(resolved.model.strip())
    seen = set(candidates)
    for m in _cloud_candidates():
        if m not in seen:
            candidates.append(m)
            seen.add(m)

    last_error: str | None = None
    for cand in candidates:
        try:
            text = await resolved.provider.complete(messages=messages, model=cand, temperature=0.7)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            last_error = msg
            logger.warning("suggest cloud failed on %s: %s", cand, msg)
            continue
        obj = _parse_suggest_json(text)
        if obj is not None:
            logger.info("suggest served from cloud %s", cand)
            return _build_suggest(obj, goal, known_tools), None
        logger.warning("suggest cloud %s returned non-JSON, trying next candidate", cand)
        last_error = f"cloud {cand} returned non-JSON"

    logger.warning("suggest cloud exhausted: %s — falling back to local", last_error)
    return None, last_error


async def _try_local(messages: list[dict], user_id: str, db: AsyncSession, known_tools: list[str], goal: str) -> Suggest:
    """LM Studio / OpenAI-compatible fallback. Raises SuggestError when it can't produce JSON."""
    req = ChatRequest(messages=messages, model="auto", provider=Provider.local)  # type: ignore[arg-type]
    resolved = await ProviderRouter().resolve(req, user_id, db)
    text = await resolved.provider.complete(messages=messages, model=resolved.model, temperature=0.7)
    obj = _parse_suggest_json(text)
    if obj is None:
        raise SuggestError("suggest could not produce valid JSON")
    logger.info("suggest served from local %s", resolved.model)
    return _build_suggest(obj, goal, known_tools)


async def suggest(goal: str, description: str | None, user_id: str, db: AsyncSession) -> Suggest:
    """Draft an Agent config. Raises SuggestError when both tiers fail."""
    known_tools = [t.name for t in registry.all_tools()]
    messages = build_meta_prompt(goal, description, known_tools)

    cloud, cloud_error = await _try_cloud(messages, user_id, db, known_tools, goal)
    if cloud is not None:
        return cloud

    try:
        return await _try_local(messages, user_id, db, known_tools, goal)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        logger.warning("suggest local failed: %s", msg)
        if _looks_like_auth_error(cloud_error or ""):
            raise SuggestError(
                "suggest generation failed on cloud (401 User not found — check OpenRouter API key) "
                f"and local fallback failed: {msg}"
            ) from e
        if cloud_error:
            raise SuggestError(
                f"suggest generation failed (cloud: {cloud_error}; local: {msg})"
            ) from e
        raise SuggestError(f"suggest generation failed: {msg}") from e
