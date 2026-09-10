import re

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.config import settings
from app.core.exceptions import NotFoundError, ForbiddenError
from app.models.conversations import Conversation
from app.models.messages import Message

from app.services.memory import retrieve_memories


async def conversation(request, user_id: str, db: AsyncSession) -> str:
    if request.conversation_id is None:
        title = " ".join(request.messages[-1].content.split()[:6])
        new_convo = Conversation(title=title, user_id=user_id)
        db.add(new_convo)
        await db.commit()
        return str(new_convo.id)
    else:
        query = await db.execute(
            select(Conversation).where(Conversation.id == request.conversation_id)
        )
        conversation = query.scalar_one_or_none()

        if conversation is None:
            raise NotFoundError("conversation not found")

        if str(conversation.user_id) != str(user_id):
            raise ForbiddenError("unauthorised user")

        return str(conversation.id)



# message retrieval 
    # by index:
async def get_message_by_index(conversation_id: str, index: int, db: AsyncSession):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.index == index)
        .where(Message.role == "user")
    )
    return result.scalar_one_or_none()


# Positional recall
# we detect last n exchanges and fetch the recent turns. 
# Fallback: normal RAG + history-window path.
MAX_RECALL_EXCHANGES = 20

_NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "couple": 2, "few": 3, "several": 5,
}
_RECALL_RE = re.compile(
    r"\b(?:recall|remember|repeat|bring up|go back to|what (?:were|was|did))\b"
    r"[^.?!]*?\b(?:last|previous|past|recent)\s+"
    r"(\d{1,2}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|couple|few|several)\s+"
    r"(?:of\s+)?(?:my\s+|our\s+)?"
    r"(?:messages?|exchanges?|turns?|replies|responses|prompts?|things)\b",
    re.IGNORECASE,
)


def detect_recall_request(text: str) -> int | None:
    """Return the requested exchange count for a positional recall message else None."""
    match = _RECALL_RE.search(text or "")
    if not match:
        return None
    token = match.group(1).lower()
    n = int(token) if token.isdigit() else _NUM_WORDS.get(token)
    if not n:
        return None
    return max(1, min(n, MAX_RECALL_EXCHANGES))


async def get_last_exchanges(conversation_id: str, n: int, db: AsyncSession) -> list[dict]:
    """Last `n` exchanges bw user & assistant in chronological order.
    An exchange spans two monotonic indices w user = k and assistant = k + 1)."""
    max_index = (await db.execute(
        select(func.max(Message.index)).where(Message.conversation_id == conversation_id)
    )).scalar() or 0
    cutoff = max_index - 2 * n
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.index > cutoff)
        .order_by(Message.index.asc())
    )
    return [{"role": m.role, "content": m.content} for m in result.scalars().all()]

    # by embedding:
async def get_memory_context(conversation_id: str, query: str, db: AsyncSession) -> dict | None:
    memories = await retrieve_memories(conversation_id=conversation_id, query=query, db=db)

    if not memories:
        return None

    memory_text = "\n".join([f"[{m['role']} at {m['created_at']}]: {m['content']}" for m in memories])
    
    return {
        "role": "system",
        "content": f"Relevant context from earlier in this conversation:\n{memory_text}"
    }



async def load_history(conversation_id: str, query: str, db: AsyncSession) -> list:
    # Window to the most recent N messages so the prompt can't grow unbounded;
    # older turns are still reachable through semantic memory and positional recall.
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.index.desc(), Message.created_at.desc())
        .limit(settings.MAX_HISTORY_MESSAGES)
    )
    rows = list(reversed(result.scalars().all()))
    recent = [{"role": m.role, "content": m.content} for m in rows]

    context = await get_memory_context(conversation_id, query, db)
    if context:
        return [context] + recent
    return recent


async def save_messages(conversation_id: str, user_content: str, assistant_content: str, model: str, prompt_tok, completion_tok, db: AsyncSession, token_provenance: str | None = None):
    # Lock the conversation row for the duration of the transaction so two
    # concurrent sends can't both read the same max(index) and allocate
    # colliding indices (which would corrupt the exchange invariant).
    convo_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    convo = convo_result.scalar_one_or_none()

    result = await db.execute(
        select(func.max(Message.index)).where(Message.conversation_id == conversation_id)
    )
    max_index = result.scalar() or 0
    user_index = int(max_index) + 1
    assistant_index = user_index + 1

    user_msg = Message(
        conversation_id=conversation_id,
        role="user",
        content=user_content,
        index= user_index
    )
    assistant_msg = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
        model_used=model,
        tokens_used= completion_tok,
        token_provenance=token_provenance,
        index= assistant_index
    )
    db.add(user_msg)
    db.add(assistant_msg)
    if convo:
        # count BOTH sides — completion-only undercounted real usage (issues.md CR-10)
        convo.token_count = (convo.token_count or 0) + (prompt_tok or 0) + (completion_tok or 0)
    # one commit: both messages + token count land together (and release the lock)
    await db.commit()
    # Embeddings are stored by the caller via background.spawn(store_exchange_memories(...))
    # so the Ollama round-trips never sit on the response's critical path.
    return user_msg, assistant_msg

