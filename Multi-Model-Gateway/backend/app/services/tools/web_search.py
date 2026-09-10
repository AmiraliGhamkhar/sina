"""First-party tool: web search.

Thin wrapper over services/search.py (SearXNG when configured, otherwise a
keyless DuckDuckGo fallback). Returns a JSON list of {title, url, snippet}.
"""
import json

from app.services.search import search
from app.services.tools.registry import Tool, ToolContext, register


async def _web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: 'query' is required"

    results = await search(query)
    if not results:
        return "Search returned no results (degraded); answer from prior knowledge."
    return json.dumps(results, ensure_ascii=False)


register(Tool(
    name="web_search",
    description=(
        "Search the web and return the top results as a JSON list of "
        "{title, url, snippet}. Use for current events or facts you are unsure about."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
        },
        "required": ["query"],
    },
    handler=_web_search,
))
