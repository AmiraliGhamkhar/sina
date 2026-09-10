"""Best-effort exact token counts for local (LM Studio) exchanges.

The chat path can't ask LM Studio for usage mid-stream (stream_options breaks
some builds), so completion tokens are a chunk count and prompt tokens are 0 —
honest but coarse. This module syncs the last exchange to real counts via LM
Studio's /v1/tokenize/encode endpoint, off the response path (spawned from
routers/chat.py). Everything here degrades gracefully: token counts are
auxiliary and must never fail a request.
"""
import logging

import httpx

from app.core.config import settings
from app.db import AsyncSessionLocal
from app.models.messages import Message

logger = logging.getLogger(__name__)

TOKENIZE_URL = f"{settings.LM_URL.rstrip('/')}/v1/tokenize/encode"


def _count_from_response(data) -> int | None:
    """LM Studio returns {"count": N}; older builds say {"length": N}. Any
    other shape means we can't trust the number — return None."""
    if not isinstance(data, dict):
        return None
    count = data.get("count", data.get("length"))
    if isinstance(count, int) and count >= 0:
        return count
    return None


async def _encode(input_text: str) -> int | None:
    """One tokenize call; None on any error or unexpected response."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(TOKENIZE_URL, json={"input": input_text})
            response.raise_for_status()
            return _count_from_response(response.json())
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("local tokenize failed (%s); keeping chunk_count provenance", e)
        return None


async def sync_local_token_counts(
    conversation_id: str,
    user_msg_id: str,
    assistant_msg_id: str,
    user_content: str,
    full_response: str,
) -> None:
    """Overwrite the exact saved exchange's token counts with real LM Studio counts.

    Called as a background task (spawn) after the local chat path saved an
    exchange with chunk_count provenance. The message ids pinpoint the rows —
    no "latest two" guessing. Runs best-effort: the ENTIRE body is wrapped so
    no exception can escape, and the existing chunk_count values are left in
    place on any failure.
    """
    try:
        if not full_response:
            return  # nothing to count — the exchange was empty or the stream died
        prompt_count = await _encode(user_content)
        completion_count = await _encode(full_response)
        if prompt_count is None or completion_count is None:
            return

        async with AsyncSessionLocal() as db:
            user_msg = await db.get(Message, user_msg_id)
            assistant_msg = await db.get(Message, assistant_msg_id)
            if user_msg is None or assistant_msg is None:
                logger.warning(
                    "local token count sync: message row(s) missing "
                    "(user=%s, assistant=%s); keeping chunk_count provenance",
                    user_msg_id,
                    assistant_msg_id,
                )
                return
            if assistant_msg.role != "assistant":
                logger.warning(
                    "local token count sync: row %s is not an assistant message; "
                    "keeping chunk_count provenance",
                    assistant_msg_id,
                )
                return
            assistant_msg.tokens_used = completion_count
            assistant_msg.token_provenance = "exact"
            user_msg.token_provenance = "exact"
            await db.commit()
    except Exception as e:  # noqa: BLE001 — a background sync must stay contained
        logger.warning("local token count sync failed (%r); keeping chunk_count provenance", e)
