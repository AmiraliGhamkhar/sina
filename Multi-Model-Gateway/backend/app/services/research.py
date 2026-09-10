"""Deep-research orchestration: plan then search then read then synthesize, run by
the arq worker (see app/worker.py).

Progress is published to Redis pub/sub channel "research:{job_id}" as JSON
events. The SSE endpoint in routers/research.py subscribes and forwards
them. The job row in postgres is updated at each stage so plain polling
works too, and survives worker restarts.

Events:
  {"type":"progress","stage","progress","message"}
  {"type":"done","status":"complete","result","sources"}
  {"type":"done","status":"cancelled"}
  {"type":"error","message"}

Cancellation: the cancel endpoint sets Redis key "research:cancel:{job_id}";
the worker checks it between steps and aborts cleanly.
"""
import json
import logging

from sqlalchemy import select

from app.core.config import settings, get_openrouter_api_key
from app.core.redis import get_redis
from app.db import AsyncSessionLocal
from app.models.research_jobs import ResearchJob
from app.services.provider_registry import get_default_provider, row_to_provider
from app.services.providers import LLMProvider, OpenAICompatProvider, OpenRouterProvider
from app.services.search import fetch_page, search

logger = logging.getLogger(__name__)

CHANNEL = "research:{job_id}"
CANCEL_KEY = "research:cancel:{job_id}"

PLAN_PROMPT = """You are a research planner. Given a research question, produce
up to {max_queries} distinct web search queries that together cover the question.
Respond with ONLY a JSON array of strings, e.g. ["query one", "query two"].
No explanations, no markdown."""

SYNTH_PROMPT = """You are a research analyst. Answer the user's research question
using ONLY the numbered sources below. Rules:
- Cite sources inline using their bracketed number, e.g. [1] or [2], after each claim.
- If sources conflict, say so and cite both.
- If the sources are insufficient for part of the question, state that openly.
- End with a "Sources" section listing each number (e.g. [1]) with its title and URL.

{sources_block}"""


class ResearchCancelled(Exception):
    pass


async def resolve_research_model(provider_arg: str | None, user_id: str, db) -> tuple[str, str] | None:
    """Best (provider label, model) pair deep research will run on, resolved at
    submit. The first tuple element is the API provider label — "openrouter" |
    "local" — the same vocabulary as ResearchRequest.provider, so the router can
    store it verbatim on the job row and _pick_provider can route on it.

    Internally the "cloud"/"local" role vocabulary is used for get_default_provider
    lookups; the returned label converts cloud → "openrouter". Mirrors
    _pick_provider's precedence EXACTLY without constructing a provider:
    provider_arg "openrouter"→cloud, "local"→local, else cloud when
    OPENROUTER_DEFAULT_MODEL is configured. A user-configured row for the role
    wins; with no rows the legacy env-var fallback applies (cloud needs an
    OpenRouter key; local uses LM_CHAT_MODEL or LM_DEFAULT_MODEL). Returns
    None when no model string can be resolved — the router rejects the job
    with 503 so research never silently runs on a random model.
    """
    if provider_arg == "openrouter":
        role = "cloud"
    elif provider_arg == "local":
        role = "local"
    else:  # auto / None: cloud preferred when configured
        role = "cloud" if settings.OPENROUTER_DEFAULT_MODEL else "local"

    # R4: the returned first element must be the API provider label ("openrouter"
    # | "local"), not the internal role — the router stores it on job.provider
    # and _pick_provider routes on it. cloud → "openrouter".
    label = "openrouter" if role == "cloud" else "local"

    row = await get_default_provider(db, user_id, role)
    if row is not None:
        if role == "local":
            fallback_model = settings.LM_CHAT_MODEL or settings.LM_DEFAULT_MODEL
        else:
            fallback_model = settings.OPENROUTER_DEFAULT_MODEL
        model = row.default_model or fallback_model
        return (label, model) if model else None

    # legacy env-var fallback: no configured rows for this role
    if role == "cloud":
        key = get_openrouter_api_key()
        if not key:
            return None  # no cloud model without a key — do NOT fall through to local
        model = settings.OPENROUTER_DEFAULT_MODEL
        return (label, model) if model else None
    model = settings.LM_CHAT_MODEL or settings.LM_DEFAULT_MODEL
    return (label, model) if model else None


async def _pick_provider(job, db) -> tuple[LLMProvider, str]:
    """Research favours the cloud model (long context, many sources) unless
    the job pinned a provider. User-configured rows win; with no rows we fall
    back to the env-var clients. OpenRouter is only used when a key exists —
    without one the fallback lands on local (never raises)."""
    if job.provider == "openrouter":
        role = "cloud"
    elif job.provider == "local":
        role = "local"
    else:  # auto / None: cloud preferred when configured
        role = "cloud" if settings.OPENROUTER_DEFAULT_MODEL else "local"

    row = await get_default_provider(db, str(job.user_id), role)
    if row is not None:
        provider = row_to_provider(row)
        if role == "local":
            fallback_model = settings.LM_CHAT_MODEL or settings.LM_DEFAULT_MODEL
        else:
            fallback_model = settings.OPENROUTER_DEFAULT_MODEL
        model = job.model if job.model and job.model != "auto" else (row.default_model or fallback_model)
        return provider, model

    # legacy env-var fallback: no configured rows for this role
    if role == "cloud":
        key = get_openrouter_api_key()
        if key:
            provider = OpenRouterProvider(api_key=key, default_model=settings.OPENROUTER_DEFAULT_MODEL)
            model = job.model if job.model and job.model != "auto" else provider.default_model
            return provider, model
        # no key → fall through to local, do NOT raise
    provider = OpenAICompatProvider(
        base_url=settings.LM_URL,
        api_key="LM-STUDIO",
        default_model=settings.LM_CHAT_MODEL or settings.LM_DEFAULT_MODEL,
    )
    model = job.model if job.model and job.model != "auto" else provider.default_model
    return provider, model


async def _publish(redis, job_id: str, event: dict) -> None:
    await redis.publish(CHANNEL.format(job_id=job_id), json.dumps(event, ensure_ascii=False))


async def _check_cancelled(redis, job_id: str) -> None:
    if await redis.exists(CANCEL_KEY.format(job_id=job_id)):
        raise ResearchCancelled()


async def _set_stage(job, db, redis, stage: str, progress: int, message: str = "") -> None:
    job.stage = stage
    job.progress = progress
    await db.commit()
    await _publish(redis, str(job.id), {
        "type": "progress", "stage": stage, "progress": progress, "message": message,
    })


async def _complete(provider, model: str, system: str, user: str, temperature: float = 0.3) -> str:
    return await provider.complete(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=temperature,
    )


def _parse_queries(raw: str, fallback: str) -> list[str]:
    """Planner output should be a JSON array; tolerate fences and prose."""
    text = raw.strip()
    if "[" in text and "]" in text:
        text = text[text.index("["): text.rindex("]") + 1]
    try:
        queries = json.loads(text)
        if isinstance(queries, list):
            queries = [str(q).strip() for q in queries if str(q).strip()]
            if queries:
                return queries[: settings.RESEARCH_MAX_QUERIES]
    except ValueError:
        pass
    return [fallback]


async def _research(job, db, redis) -> tuple[str, list[dict]]:
    provider, model = await _pick_provider(job, db)
    job_id = str(job.id)

    # 1. plan
    await _set_stage(job, db, redis, "planning", 5, "generating search queries")
    try:
        raw_plan = await _complete(provider, model, PLAN_PROMPT.format(
            max_queries=settings.RESEARCH_MAX_QUERIES), job.query)
        queries = _parse_queries(raw_plan, job.query)
    except Exception as e:
        logger.warning("research %s: planner failed (%r); using the raw query", job_id, e)
        queries = [job.query]
    await _check_cancelled(redis, job_id)

    # 2. search n gather candidate sources, deduped by URL
    candidates: list[dict] = []
    seen: set[str] = set()
    for i, q in enumerate(queries):
        await _set_stage(job, db, redis, "searching",
                         10 + int(30 * i / len(queries)), f"searching: {q}")
        try:
            for r in await search(q, settings.RESEARCH_RESULTS_PER_QUERY):
                if r["url"] and r["url"] not in seen:
                    seen.add(r["url"])
                    candidates.append(r)
        except Exception as e:
            logger.warning("research %s: search '%s' failed: %r", job_id, q, e)
        await _check_cancelled(redis, job_id)

    if not candidates:
        raise RuntimeError("no search results found for any query")

    # 3. read n fetch the top pages
    sources: list[dict] = []
    to_read = candidates[: settings.RESEARCH_MAX_SOURCES]
    for i, c in enumerate(to_read):
        await _set_stage(job, db, redis, "reading",
                         40 + int(40 * i / len(to_read)), f"reading: {c['url']}")
        try:
            content = await fetch_page(c["url"])
        except Exception as e:
            logger.warning("research %s: fetch '%s' failed: %r", job_id, c["url"], e)
            content = c.get("snippet", "")  # fall back to the search snippet
        if content:
            sources.append({
                "n": len(sources) + 1,
                "title": c["title"],
                "url": c["url"],
                "content": content,
            })
        await _check_cancelled(redis, job_id)

    if not sources:
        raise RuntimeError("could not read any of the search results")

    # 4. synthesize with citations
    await _set_stage(job, db, redis, "synthesizing", 85, f"synthesizing from {len(sources)} sources")
    sources_block = "\n\n".join(
        f"[{s['n']}] {s['title']} — {s['url']}\n{s['content']}" for s in sources
    )
    answer = await _complete(
        provider, model, SYNTH_PROMPT.format(sources_block=sources_block), job.query,
    )

    # persist sources without page bodies (the answer carries the citations)
    slim_sources = [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in sources]
    return answer, slim_sources


async def run_research_job(job_id: str) -> None:
    """arq entry point: owns the job row's lifecycle from running → terminal."""
    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ResearchJob).where(ResearchJob.id == job_id))
        job = result.scalar_one_or_none()
        if job is None or job.status != "queued":
            logger.warning("research %s: missing or not queued; skipping", job_id)
            return

        job.status = "running"
        await db.commit()

        try:
            answer, sources = await _research(job, db, redis)
            job.status = "complete"
            job.progress = 100
            job.stage = "done"
            job.result = answer
            job.sources = sources
            await db.commit()
            await _publish(redis, job_id, {
                "type": "done", "status": "complete", "result": answer, "sources": sources,
            })
        except ResearchCancelled:
            job.status = "cancelled"
            await db.commit()
            await _publish(redis, job_id, {"type": "done", "status": "cancelled"})
        except Exception as e:
            logger.error("research %s failed: %r", job_id, e)
            job.status = "failed"
            job.error = str(e)
            await db.commit()
            await _publish(redis, job_id, {"type": "error", "message": str(e)})
        finally:
            await redis.delete(CANCEL_KEY.format(job_id=job_id))
