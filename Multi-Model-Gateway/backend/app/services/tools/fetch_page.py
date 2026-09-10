"""First-party tool: fetch a web page's text content.

Pairs with web_search: search returns URLs, this reads one of them.
"""
from app.services.search import fetch_page
from app.services.tools.registry import Tool, ToolContext, register


async def _fetch_page(args: dict, ctx: ToolContext) -> str:
    url = str(args.get("url", "")).strip()
    if not url.startswith(("http://", "https://")):
        return "Error: 'url' must be an http(s) URL"
    return await fetch_page(url)


register(Tool(
    name="fetch_page",
    description=(
        "Fetch a web page and return its visible text (truncated). "
        "Use after web_search to read a promising result in full."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The http(s) URL to fetch"},
        },
        "required": ["url"],
    },
    handler=_fetch_page,
))
