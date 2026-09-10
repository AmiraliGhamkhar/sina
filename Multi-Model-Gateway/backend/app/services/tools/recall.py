"""First-party tool: verbatim recall of recent exchanges.

Wraps the existing convo.get_last_exchanges so the model can pull earlier
turns on demand instead of relying on the regex-based detect_recall_request.
"""
from app.services.convo import MAX_RECALL_EXCHANGES, get_last_exchanges
from app.services.tools.registry import Tool, ToolContext, register

DEFAULT_N = 3


async def _recall(args: dict, ctx: ToolContext) -> str:
    try:
        n = int(args.get("n", DEFAULT_N))
    except (TypeError, ValueError):
        n = DEFAULT_N
    n = max(1, min(n, MAX_RECALL_EXCHANGES))

    exchanges = await get_last_exchanges(ctx.conversation_id, n, ctx.db)
    if not exchanges:
        return "No earlier exchanges in this conversation."
    return "\n".join(f"{m['role']}: {m['content']}" for m in exchanges)


register(Tool(
    name="recall_recent_exchanges",
    description=(
        "Fetch the last n exchanges (user/assistant message pairs) of this "
        "conversation verbatim, oldest first. Use when the user refers to "
        "something said earlier that may be outside your context window."
    ),
    parameters={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": f"Number of exchanges to recall (1-{MAX_RECALL_EXCHANGES})",
                "minimum": 1,
                "maximum": MAX_RECALL_EXCHANGES,
            },
        },
        "required": ["n"],
    },
    handler=_recall,
))
