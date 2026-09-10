"""First-party tool: search the user's past conversations.

Matches this user's conversations by title OR message content (case-insensitive
ILIKE with metacharacters escaped) and returns a compact JSON list of
{id, title, snippet} so the model can recall what was discussed earlier.
"""
import json

from sqlalchemy import or_, select

from app.models.conversations import Conversation
from app.models.messages import Message
from app.services.tools.registry import Tool, ToolContext, register

DEFAULT_LIMIT = 5
MAX_LIMIT = 10
MAX_QUERY_CHARS = 200
SNIPPET_CHARS = 200


def _escape_like(s: str) -> str:
    r"""Escape ILIKE metacharacters (%, _, \) for use in a LIKE pattern.

    Backslash is replaced first: the escapes produced for % and _ must not be
    re-escaped by the subsequent passes.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_search_query(user_id: str, pattern: str, limit: int):
    """Build the conversation-search SELECT as a LEFT OUTER JOIN.

    The outer join keeps title-only conversations (zero messages) in the
    result set. Both predicates live in the WHERE clause — if the content
    predicate were on the join's ON clause the outer join would degenerate
    into an inner join and drop those rows.
    """
    return (
        select(Conversation)
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
        .where(or_(
            Conversation.title.ilike(pattern, escape="\\"),
            Message.content.ilike(pattern, escape="\\"),
        ))
        .distinct()
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    )


async def _search_conversations(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: 'query' is required"
    query = query[:MAX_QUERY_CHARS]

    try:
        limit = int(args.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    pattern = f"%{_escape_like(query)}%"

    result = await ctx.db.execute(_build_search_query(ctx.user_id, pattern, limit))
    conversations = result.scalars().all()
    if not conversations:
        return "No conversations matched."

    hits = []
    for convo in conversations:
        # snippet: most recent message in this conversation that matched
        snippet_result = await ctx.db.execute(
            select(Message.content)
            .where(Message.conversation_id == convo.id)
            .where(Message.content.ilike(pattern, escape="\\"))
            .order_by(Message.created_at.desc(), Message.index.desc())
            .limit(1)
        )
        content = snippet_result.scalar()
        if content:
            snippet = content if len(content) <= SNIPPET_CHARS else content[:SNIPPET_CHARS] + "…"
        else:
            snippet = "—"
        hits.append({"id": str(convo.id), "title": convo.title or "", "snippet": snippet})

    return json.dumps(hits, ensure_ascii=False)


register(Tool(
    name="search_conversations",
    description=(
        "Search the user's past conversations by title or message content and "
        "return a JSON list of {id, title, snippet}. Use to recall what was "
        "discussed earlier or find a specific topic."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text to search for in conversation titles and messages",
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum number of conversations to return (1-{MAX_LIMIT})",
                "minimum": 1,
                "maximum": MAX_LIMIT,
            },
        },
        "required": ["query"],
    },
    handler=_search_conversations,
))
