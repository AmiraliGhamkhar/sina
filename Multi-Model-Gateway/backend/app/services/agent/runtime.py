"""AgentRuntime — the deep module behind the Agent loop (deepening #1).

External seam: one method on one class — `run(ctx) → AsyncIterable[AgentEvent]`
where AgentEvent is a typed dict. Callers cross this seam only; they inject
the LLMProvider adapter, the allowed-tool view, and the already-built message
list. No `AsyncSession` handle crosses the seam — the runtime never receives a
session from the caller; it opens its own short-lived `AsyncSessionLocal` per
tool call and for the final save (R1).

The loop helpers (`_estimate_tokens`, `_is_context_error`, `_prune_old_tool_rounds`)
and `_MEMORY_WRITE_TOOLS` are defined HERE — the module that owns the loop — and
re-exported by `__init__.py`, so the adapter (`agent.py`) and the tests share
the exact code the loop runs.

Two adapters justify the seam: the prod LLMProvider (OpenAI/compat, OpenRouter)
and an in-memory fake used by `tests/test_agent_runtime.py`. The deletion test
passes: deleting this module scatters loop / prune / budget / SSE-ordering bugs
across routers and chat paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Any

from openai import APIError

from app.core.config import settings
from app.core.exceptions import AppError
from app.db import AsyncSessionLocal
from app.models.presets import DEFAULT_TEMPERATURE
from app.services.convo import save_messages
from app.services.memory import store_exchange_memories
from app.services.memory_curation import enqueue_curation
from app.core.background import spawn
from app.core.metrics import record_metrics
from app.services.tools import registry as tool_registry

logger = logging.getLogger(__name__)

_MEMORY_WRITE_TOOLS = ("memory_write", "memory_str_replace", "memory_append", "memory_delete")


def _estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += len(str(m.get("content") or "")) // 4 + 4
    return total


_CONTEXT_ERROR_HINTS = (
    "context_length",
    "context window",
    "maximum context",
    "too many tokens",
    "token limit",
    "input is too long",
    "context overflow",
    "prompt is too long",
)


def _is_context_error(e: BaseException) -> bool:
    text = str(e).lower()
    return any(h in text for h in _CONTEXT_ERROR_HINTS)


def _prune_old_tool_rounds(messages: list[dict], max_tokens: int, drop_all: bool = False) -> list[dict]:
    essential_end = 0
    for msg in messages:
        if msg.get("role") in ("system", "user"):
            essential_end += 1
            if msg.get("role") == "user":
                break
        else:
            break
    while drop_all or _estimate_tokens(messages) > max_tokens:
        victim = None
        for i in range(essential_end, len(messages)):
            if "tool_calls" in messages[i]:
                victim = i
                break
        if victim is None:
            break
        end = victim + 1
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
        del messages[victim:end]
    return messages


async def _execute_tool(tool: tool_registry.Tool, raw_args: str, ctx: tool_registry.ToolContext) -> str:
    try:
        args = json.loads(raw_args) if raw_args else {}
    except ValueError:
        return "Error: tool arguments were not valid JSON"
    if not isinstance(args, dict):
        return "Error: tool arguments must be a JSON object"
    try:
        result = await asyncio.wait_for(tool.handler(args, ctx), timeout=settings.TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return f"Error: tool '{tool.name}' timed out after {settings.TOOL_TIMEOUT_SECONDS}s"
    except Exception as e:
        # Log the detail server-side but return a generic string: exception text
        # can leak internal topology to the model (e.g. SSRF guard messages with
        # internal IPs/hostnames — sweep F11).
        logger.warning("tool '%s' failed: %r", tool.name, e)
        return f"Error: tool '{tool.name}' failed"
    if len(result) > settings.TOOL_RESULT_MAX_CHARS:
        result = result[: settings.TOOL_RESULT_MAX_CHARS] + "\n[truncated]"
    return result


# ── Public types ─────────────────────────────────────────────────────────

@dataclass
class AgentRuntimeCtx:
    """Everything the runtime needs, pre-resolved by the caller (no DB).

    The router resolves agent, provider, preset, memory, history, and
    allowed-tool view, then hands a filled ctx to the runtime. The runtime
    owns only the loop, budget, prune, dispatch, and SSE event ordering.
    """

    conversation_id: str
    user_id: str
    user_content: str
    messages: list[dict]  # already includes system prompt + memory + history + user
    provider: Any  # LLMProvider adapter (has is_cloud, chat_with_tools, complete)
    model: str
    resolved_provider: str  # "local" | "openrouter" — for metrics
    is_cloud: bool
    preset: Any | None
    agent_id: str | None
    agent_version: int | None
    agent_version_obj: Any | None  # the Agent row for version fallback on done
    allowed_tools: list[tool_registry.Tool]
    tool_context: tool_registry.ToolContext  # carries user_id, conversation_id, agent_id; db is swapped per tool
    is_private: bool = False
    extra_sampling: dict | None = None


AgentEvent = dict[str, Any]  # {type: tool_call|tool_result|token|error|done, ...}


class AgentRuntime:
    """Deep module: the Agent loop. Small interface, rich implementation.

    Callers do:
        runtime = AgentRuntime()
        async for event in runtime.run(ctx):
            yield _sse(event)  # router frames SSE

    All errors surface as `error` + `done` events — the runtime never raises
    past the stream.
    """

    async def run(self, ctx: AgentRuntimeCtx) -> AsyncIterator[AgentEvent]:
        # Work on a mutable copy so caller's list is not surprise-mutated
        messages: list[dict] = list(ctx.messages)
        tools_by_name = {t.name: t for t in ctx.allowed_tools}
        tool_schemas = [tool_registry.openai_schema(t) for t in ctx.allowed_tools]

        start_time = time.time()
        prompt_tok = 0
        completion_tok = 0
        final_answer = ""
        truncated = False
        written_paths: list[str] = []

        try:
            for iteration in range(settings.AGENT_MAX_ITERATIONS + 1):
                over_budget = (prompt_tok + completion_tok) > settings.AGENT_TOKEN_BUDGET
                offer_tools = bool(tool_schemas) and not over_budget and iteration < settings.AGENT_MAX_ITERATIONS

                if offer_tools:
                    _prune_old_tool_rounds(messages, int(0.6 * settings.AGENT_TOKEN_BUDGET))
                    try:
                        resp = await ctx.provider.chat_with_tools(
                            messages=messages,
                            model=ctx.model,
                            temperature=ctx.preset.temperature if ctx.preset else DEFAULT_TEMPERATURE,
                            tools=tool_schemas,
                            tool_choice="auto",
                            max_tokens=None,
                            extra_sampling=ctx.extra_sampling if not ctx.is_cloud else None,
                        )
                    except APIError as e:
                        if not _is_context_error(e):
                            raise
                        logger.warning("context overflow mid-loop; degrading to tool-less final round: %s", repr(e))
                        truncated = True
                        _prune_old_tool_rounds(messages, settings.AGENT_TOKEN_BUDGET, drop_all=True)
                        final_answer = await ctx.provider.complete(
                            messages=messages,
                            model=ctx.model,
                            temperature=ctx.preset.temperature if ctx.preset else DEFAULT_TEMPERATURE,
                            max_tokens=None,
                        )
                        break
                    if resp.prompt_tokens is not None:
                        prompt_tok += resp.prompt_tokens
                        completion_tok += resp.completion_tokens or 0
                    tool_calls = resp.tool_calls or []
                    if not tool_calls:
                        final_answer = resp.content or ""
                        break
                else:
                    _prune_old_tool_rounds(messages, settings.AGENT_TOKEN_BUDGET)
                    final_answer = await ctx.provider.complete(
                        messages=messages,
                        model=ctx.model,
                        temperature=ctx.preset.temperature if ctx.preset else DEFAULT_TEMPERATURE,
                        max_tokens=None,
                    )
                    truncated = bool(tool_schemas) and not offer_tools
                    break

                # Avoid referencing `resp` when we took the tool-less `else` branch
                # (resp is defined only in the offer_tools=True path above).
                resp_content = resp.content if offer_tools else ""  # type: ignore[possibly-undefined]
                messages.append(
                    {
                        "role": "assistant",
                        "content": resp_content,
                        "tool_calls": [
                            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                            for tc in tool_calls  # type: ignore[possibly-undefined]
                        ],
                    }
                )

                for tc in tool_calls:  # type: ignore[possibly-undefined]
                    name = tc.name
                    yield {"type": "tool_call", "id": tc.id, "name": name, "arguments": tc.arguments}

                    if name in _MEMORY_WRITE_TOOLS:
                        try:
                            targs = json.loads(tc.arguments or "{}")
                        except ValueError:
                            targs = {}
                        if isinstance(targs, dict) and targs.get("path"):
                            written_paths.append(str(targs["path"]))

                    tool = tools_by_name.get(name)
                    if tool is None:
                        result = f"Error: unknown or unauthorised tool '{name}'"
                    else:
                        async with AsyncSessionLocal() as tool_db:
                            ctx.tool_context.db = tool_db  # type: ignore[attr-defined]
                            ctx.tool_context.tool_call_id = tc.id  # audit linkage for file tools
                            result = await _execute_tool(tool, tc.arguments, ctx.tool_context)
                        ctx.tool_context.tool_call_id = None

                    yield {"type": "tool_result", "id": tc.id, "name": name, "content": result}
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            if final_answer:
                yield {"type": "token", "content": final_answer}

            elapsed = time.time() - start_time
            provenance = "exact" if prompt_tok > 0 else "chunk_count"
            async with AsyncSessionLocal() as save_db:
                await save_messages(
                    ctx.conversation_id, ctx.user_content, final_answer, ctx.model,
                    prompt_tok, completion_tok, save_db, token_provenance=provenance,
                )
            spawn(store_exchange_memories(ctx.conversation_id, ctx.user_content, final_answer))
            spawn(
                asyncio.to_thread(
                    record_metrics, ctx.resolved_provider, ctx.model, elapsed,
                    prompt_tok, completion_tok, messages, final_answer,
                    ctx.conversation_id, not ctx.is_private,
                )
            )
            spawn(enqueue_curation(str(ctx.user_id), str(ctx.conversation_id), written_paths, private=ctx.is_private))

            done_evt: dict[str, Any] = {"type": "done", "conversation_id": ctx.conversation_id, "truncated": truncated}
            if ctx.agent_id:
                done_evt["agent_id"] = ctx.agent_id
                av = ctx.agent_version if ctx.agent_version is not None else (
                    ctx.agent_version_obj.version if ctx.agent_version_obj else None
                )
                done_evt["agent_version"] = av
            yield done_evt

        except APIError as e:
            logger.error("Provider API error in agent run: %s", repr(e))
            yield {"type": "error", "message": "upstream model provider error"}
            yield {"type": "done", "conversation_id": ctx.conversation_id}
        except AppError as e:
            yield {"type": "error", "message": e.detail}
            yield {"type": "done", "conversation_id": ctx.conversation_id}
        except RuntimeError as e:
            logger.error("Tool-calling error in agent run: %s", repr(e))
            yield {"type": "error", "message": str(e)}
            yield {"type": "done", "conversation_id": ctx.conversation_id}
        except Exception as e:
            logger.error("Unexpected error in agent run: %s", repr(e))
            yield {"type": "error", "message": "internal server error"}
            yield {"type": "done", "conversation_id": ctx.conversation_id}
