"""Model-facing tool registry.

First-party tools register themselves at import time (see this package's
__init__); MCP-server tools are added by app.services.mcp_client at startup.
The agent loop reads the registry to advertise JSON schemas to the model and
to dispatch tool calls by name.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ToolContext:
    """Per-run state handed to every tool handler (MCP tools ignore it)."""
    user_id: str
    conversation_id: str
    db: AsyncSession
    agent_id: str | None = None  # set when the run is as an agent (ADR-0004)
    tool_call_id: str | None = None  # set per-dispatch by the runtime (file_edits audit linkage)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments the model must produce
    handler: Callable[[dict, ToolContext], Awaitable[str]]
    # First party tools are allowed by default; everything else (MCP) is
    # deny by default until the user grants it (see agent.get_allowed_tools).
    first_party: bool = True


_registry: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _registry[tool.name] = tool


def unregister(name: str) -> None:
    _registry.pop(name, None)


def get_tool(name: str) -> Tool | None:
    return _registry.get(name)


def all_tools() -> list[Tool]:
    return list(_registry.values())


def openai_schema(tool: Tool) -> dict:
    """OpenAI-style function-calling schema, accepted by LM Studio and OpenRouter."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
