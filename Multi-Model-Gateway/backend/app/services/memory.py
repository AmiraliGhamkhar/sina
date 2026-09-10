
import logging
import httpx

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text as sa_text
import uuid
from app.core.config import settings
from app.models.memories import Memory

logger = logging.getLogger(__name__)


# LM Studio's OpenAI-compatible embeddings endpoint (load an embedding model there).
EMBED_URL = f"{settings.LM_URL}/v1/embeddings"
EMBED_MODEL = settings.LM_EMBED_MODEL

async def get_embedding(content: str) -> list[float] | None:
    """Return an embedding vector, or None if the embedding service is
    unreachable / returns an unexpected response. Memory is an auxiliary
    feature, so callers degrade gracefully instead of failing the request."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                EMBED_URL,
                json={"model": EMBED_MODEL, "input": content},
            )
            response.raise_for_status()
            vector = response.json()["data"][0]["embedding"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        logger.warning("embedding request failed (%s); skipping memory", e)
        return None
    # Guard against an embedder whose dimension doesn't match the pgvector column
    # (e.g. EMBED_MODEL was changed). Inserting a mismatched vector would error;
    # fail loud here and skip instead of silently corrupting memory.
    if not isinstance(vector, list) or len(vector) != settings.EMBED_DIM:
        logger.warning(
            "embedding dimension mismatch (got %s, expected %d); skipping memory — "
            "did EMBED_MODEL change without a migrate+reindex?",
            len(vector) if isinstance(vector, list) else type(vector).__name__,
            settings.EMBED_DIM,
        )
        return None
    return vector


async def store_memory(conversation_id: str, role: str, content: str, db: AsyncSession):
    vector = await get_embedding(content)
    if vector is None:
        return  # if embedding is unavailable then skip persisting this memory
    memory = Memory(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        role=role,
        content=content,
        embedding=vector,
    )
    db.add(memory)
    await db.commit()


async def store_exchange_memories(conversation_id: str, user_content: str, assistant_content: str):
    """Embed + store both sides of an exchange on a fresh session.

    Called as a background task (see core/background.spawn) so the embedding
    round-trips never sit on the chat response's critical path.
    """
    from app.db import AsyncSessionLocal  # local import avoids a circular import at module load

    async with AsyncSessionLocal() as db:
        await store_memory(conversation_id, role="user", content=user_content, db=db)
        await store_memory(conversation_id, role="assistant", content=assistant_content, db=db)


async def retrieve_memories(conversation_id: str, query: str, db: AsyncSession):
    query_vector = await get_embedding(query)
    if query_vector is None:
        return []  # if embedding is unavailable then no memory context for this turn
    # memory is auxiliary: a failed retrieval must not take down the chat turn
    try:
        result = await db.execute(
            sa_text("SELECT content, role, created_at FROM memories WHERE conversation_id = :cid ORDER BY embedding <=> CAST(:emb AS vector) LIMIT 3"),
            {"cid": conversation_id, "emb": str(query_vector)},
        )
        rows = result.fetchall()
    except Exception as e:
        logger.warning("memory retrieval failed (%r); skipping memory context", e)
        await db.rollback()  # clear the failed transaction state so the session stays usable
        return []
    return [{"role": r.role, "content": r.content, "created_at": r.created_at} for r in rows]
