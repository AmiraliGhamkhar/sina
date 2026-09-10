from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openai import APIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import asyncio
import time
import logging

from app.db import get_db, AsyncSessionLocal

from app.core.security import get_current_user
from app.core.stream_guard import acquire_stream_slot, release_stream_slot
from app.core.metrics import record_metrics
from app.core.background import spawn
from app.core.exceptions import AppError

from app.services.convo import conversation, load_history, save_messages, get_last_exchanges, detect_recall_request
from app.services.memory import store_exchange_memories
from app.services.memory_curation import enqueue_curation
from app.services.memory_files import safe_build_memory_context
from app.services.router import ChatRequest
from app.services.provider_router import ProviderRouter
from app.services.tokenize import sync_local_token_counts

from app.models.presets import (
    Preset,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_MIN_P,
    DEFAULT_REPEAT_PENALTY,
)


router = APIRouter()

logger = logging.getLogger(__name__)

async def load_preset(preset_id, user_id, db: AsyncSession):
    if preset_id:
        result = await db.execute(
            select(Preset).where(Preset.id == preset_id, Preset.user_id == user_id)
        )
    else:
        # if no preset is chosen, we fall back to the user's "Default" preset that is created at
        # user reg. If preset is missing, we return None and stream_tokens func uses
        # the hardcoded safe defaults.
        result = await db.execute(
            select(Preset)
            .where(Preset.user_id == user_id, Preset.name == "Default")
            .order_by(Preset.created_at.asc())
            .limit(1)
        )
    return result.scalar_one_or_none()



async def stream_tokens(request: ChatRequest, user_id: str, db: AsyncSession):
    """Outer generator: guarantees the per-user stream slot acquired in the
    handler is released on normal completion, error, or client disconnect."""
    try:
        async for event in _stream_tokens_inner(request, user_id, db):
            yield event
    finally:
        await release_stream_slot(user_id)


async def _stream_tokens_inner(request: ChatRequest, user_id: str, db: AsyncSession):
    conversation_id = await conversation(request, user_id, db)
    history = await load_history(conversation_id, request.messages[-1].content, db)
    current = {"role": "user", "content": request.messages[-1].content}
    messages = history + [current]

    preset = await load_preset(request.preset_id, user_id, db)

    system_prefix = []
    if preset and preset.system_prompt:
        system_prefix.append({"role": "system", "content": preset.system_prompt})

    # Positional recall: if the user asked to recall the last N exchanges, fetch them verbatim 
    recall_n = detect_recall_request(request.messages[-1].content)
    if recall_n:
        exchanges = await get_last_exchanges(conversation_id, recall_n, db)
        if exchanges:
            transcript = "\n".join(f"{m['role']}: {m['content']}" for m in exchanges)
            system_prefix.append({
                "role": "system",
                "content": f"The user asked to recall the last {recall_n} exchange(s). "
                           f"Here they are, oldest first:\n{transcript}",
            })

    if system_prefix:
        messages = system_prefix + messages

    # Memory-file index injection (Tier 1 index + Tier 1.5 full files) — built
    # BEFORE the connection is released below; best-effort, a memory failure
    # must never fail the request. A separate leading system message preserves
    # the preset prompt exactly as-is.
    memory_context = await safe_build_memory_context(db, user_id)
    if memory_context:
        messages = [{"role": "system", "content": memory_context}] + messages

    try:
        resolved = await ProviderRouter().resolve(request, user_id, db)
        provider, model = resolved.provider, resolved.model
    except AppError as exc:
        logger.error("Provider resolution failed: %s", exc.detail)
        yield f"data: [ERROR] {exc.detail}\n\n"
        yield "data: [DONE]\n\n"
        return
    is_cloud = getattr(provider, "is_cloud", False)
    resolved_provider = "openrouter" if is_cloud else "local"

    # Release the DB connection back to the pool BEFORE the (up-to-minute) stream —
    # all reads are done, and holding an idle connection across the stream is what
    # exhausts the pool under concurrency (issues.md CR-3). The final write uses a
    # fresh short-lived session. get_db's own close() afterwards is then a no-op.
    user_content = request.messages[-1].content
    await db.close()

    start_time = time.time()
    token_count = 0
    full_response = ""


    # LM Studio has sampling params via extra_body; cloud providers reject them.
    extra_sampling = {}
    if not is_cloud:
        # we do NOT send stream_options as some LM Studio builds stop streaming
        # token by token when it's present (usage then falls back to the
        # streamed chunk count, handled inside the adapter).
        extra_sampling = {
            "top_k": preset.top_k if (preset and preset.top_k is not None) else DEFAULT_TOP_K,
            "min_p": preset.min_p if preset else DEFAULT_MIN_P,
            "repeat_penalty": preset.repeat_penalty if preset and preset.repeat_penalty and preset.repeat_penalty > 0 else DEFAULT_REPEAT_PENALTY
        }

    prompt_tok = 0
    completion_tok = 0
    stream_error = False
    try:
        async for chunk in provider.stream_chat(
            messages, model=model, temperature=preset.temperature if preset else DEFAULT_TEMPERATURE,
            max_tokens=preset.token_limit if preset and preset.token_limit and preset.token_limit > 0 else None,
            stop=preset.stop_strings if preset else None,
            top_p=preset.top_p if preset else None,
            extra_sampling=extra_sampling,
        ):
            if chunk.prompt_tokens is not None:
                prompt_tok = chunk.prompt_tokens
                completion_tok = chunk.completion_tokens or 0
            if chunk.content:
                full_response += chunk.content
                token_count += 1
                yield f"data: {chunk.content}\n\n"
    except APIError as e:
        # the OpenAI SDK raises APIError on iteration, not on create — so this
        # is always a MID-stream failure and some tokens may already have been
        # streamed. Do NOT return: keep the partial exchange (revision.md
        # contract: partial responses ARE saved) and fall through to the save
        # block + the standard [ERROR] stream interrupted tail below. An
        # APIError on the very first chunk (the create-call failure) lands here
        # too and simply saves nothing, which save_messages handles via the
        # `if full_response or not stream_error` guard.
        logger.error("Provider API error: %s", repr(e))
        stream_error = True
    except Exception as e:
        # provider died mid gen: keep whatever streamed so far so the
        # exchange isn't lost, and end the stream cleanly instead of crashing
        # without [DONE]
        logger.error("Stream interrupted mid-generation: %s", repr(e))
        stream_error = True

    elapsed = time.time() - start_time

    # Prefer the provider's reported token usage; fall back to the streamed count.
    if prompt_tok == 0 and completion_tok == 0:
        completion_tok = token_count

    # prompt_tok > 0 means the provider reported usage (cloud); the local
    # fallback leaves it 0, so those counts are honest chunk counts. The local
    # path syncs to exact counts off-path afterwards (see tokenize.py).
    provenance = "exact" if prompt_tok > 0 else "chunk_count"

    # on a mid-stream failure with nothing generated there is no exchange to save
    if full_response or not stream_error:
        # fresh short-lived session — the request's own connection was released above
        async with AsyncSessionLocal() as save_db:
            user_msg, assistant_msg = await save_messages(conversation_id, user_content, full_response, model, prompt_tok, completion_tok, save_db, token_provenance=provenance)
        # off the response path: embeddings (slow Ollama round-trips) + tracing.
        # record_content=False for private chats so message text never reaches Langfuse (CR-2).
        spawn(store_exchange_memories(conversation_id, user_content, full_response))
        if not is_cloud:
            # local counts are chunk_count until LM Studio reports the real
            # numbers; the sync targets the exact rows saved above and is
            # best-effort — it never fails the request
            spawn(sync_local_token_counts(
                conversation_id,
                str(user_msg.id),
                str(assistant_msg.id),
                user_content,
                full_response,
            ))
        spawn(asyncio.to_thread(
            record_metrics, resolved_provider, model, elapsed, prompt_tok, completion_tok,
            messages, full_response, conversation_id, not request.private,
        ))
        # M2: background memory curation — off-path, best-effort; private chats
        # are excluded inside enqueue_curation so they never feed memory.
        spawn(enqueue_curation(str(user_id), str(conversation_id), [],
                               private=request.private))

    if stream_error:
        yield "data: [ERROR] stream interrupted\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/completions")
async def chat_completions(request: ChatRequest, db: AsyncSession = Depends(get_db), user_id=Depends(get_current_user)):
    await acquire_stream_slot(user_id)
    return StreamingResponse(
        stream_tokens(request, user_id, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable proxy buffering (nginx) as Caddy uses flush_interval
        },
    )