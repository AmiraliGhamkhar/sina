"""First-party tools: per-user memory files (Claude-style file store).

Wraps services/memory_files.py so the agent can read, write, append,
string-replace, and delete its user's memory files. This is a FILE STORE read
via agentic tool calls — explicitly NOT embeddings, and unrelated to the
pgvector RAG in services/memory.py.

Versioning contract, spelled out in every description: read a file first,
every write takes if_version from a prior read, and __new__ creates a file.
Handlers never raise — failures come back as strings so the agent run
survives. The _safe wrapper is the backstop: anything that escapes a
handler's own validation/result handling (a database error, a bug) is
turned into an error string instead of crashing the agent loop.
"""
import functools
import json
import logging

from app.services import memory_files
from app.services.tools.registry import Tool, ToolContext, register

logger = logging.getLogger(__name__)


def _safe(handler):
    """Never-raise wrapper for memory tool handlers.

    Catches ANY exception (Exception, not BaseException), logs it, and
    returns an "Error: ..." string so the agent run survives a broken
    service or database call. The handler's own ValueError handling still
    runs first and produces its specific message; this is the safety net
    for everything else.
    """
    @functools.wraps(handler)
    async def wrapped(args: dict, ctx: ToolContext) -> str:
        try:
            return await handler(args, ctx)
        except Exception as exc:  # noqa: BLE001 — tool contract: never raise
            logger.warning("memory tool %s failed: %s",
                           handler.__name__, exc, exc_info=True)
            return f"Error: memory operation failed: {exc}"
    return wrapped


def _default_description(path: str) -> str:
    """description fallback: the path's last segment, underscores -> spaces."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name.replace("_", " ") or path


def _conflict_error(result: dict) -> str:
    current = result.get("current")
    return (
        "Error: version conflict — current content/version: "
        + json.dumps(current, ensure_ascii=False)
        + " (merge and retry with the new version, or use if_version=__new__ to create)"
    )


@_safe
async def _memory_read(args: dict, ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    try:
        path = memory_files._validate_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    current = await memory_files.memory_read(ctx.db, ctx.user_id, path)
    if current is None:
        return f"Error: memory file not found at {path}"
    return json.dumps({
        "path": current["path"],
        "content": current["content"],
        "version": current["version"],
        "description": current["description"],
        "aliases": current["aliases"],
    }, ensure_ascii=False)


@_safe
async def _memory_write(args: dict, ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    content = args.get("content")
    if_version = args.get("if_version")
    description = args.get("description")
    aliases = args.get("aliases")

    if not path:
        return "Error: 'path' is required"
    try:
        path = memory_files._validate_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if content is None:
        return "Error: 'content' is required"
    if if_version is None:
        return "Error: 'if_version' is required (use __new__ to create)"
    if aliases is not None:
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            return "Error: 'aliases' must be a list of strings"
    else:
        aliases = []

    try:
        result = await memory_files.memory_write(
            ctx.db, ctx.user_id, path, str(content), str(if_version),
            description=str(description) if description else _default_description(path),
            aliases=aliases,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    if result["ok"]:
        return json.dumps({"path": path, "version": result["version"]}, ensure_ascii=False)
    if result["reason"] == "size_cap":
        return f"Error: {result['message']}"
    if result["reason"] == "conflict":
        return _conflict_error(result)
    return f"Error: memory write failed: {result}"


@_safe
async def _memory_str_replace(args: dict, ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    old_str = args.get("old_str")
    new_str = args.get("new_str")
    if_version = args.get("if_version")

    if not path:
        return "Error: 'path' is required"
    try:
        path = memory_files._validate_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if old_str is None or new_str is None:
        return "Error: 'old_str' and 'new_str' are required"
    if if_version is None:
        return "Error: 'if_version' is required (use __new__ to create)"

    try:
        result = await memory_files.memory_str_replace(
            ctx.db, ctx.user_id, path, str(old_str), str(new_str), str(if_version)
        )
    except ValueError as exc:
        return f"Error: {exc}"
    if result["ok"]:
        return json.dumps({"path": path, "version": result["version"]}, ensure_ascii=False)
    if result["reason"] == "not_found":
        return f"Error: {result.get('message', 'memory file not found at ' + path)}"
    if result["reason"] == "ambiguous":
        return f"Error: {result['message']}"
    if result["reason"] == "conflict":
        return _conflict_error(result)
    return f"Error: memory replace failed: {result}"


@_safe
async def _memory_append(args: dict, ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    content = args.get("content")
    if_version = args.get("if_version")

    if not path:
        return "Error: 'path' is required"
    try:
        path = memory_files._validate_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if content is None:
        return "Error: 'content' is required"
    if if_version is None:
        return "Error: 'if_version' is required (use __new__ to create)"

    try:
        result = await memory_files.memory_append(
            ctx.db, ctx.user_id, path, str(content), str(if_version)
        )
    except ValueError as exc:
        return f"Error: {exc}"
    if result["ok"]:
        return json.dumps({"path": path, "version": result["version"]}, ensure_ascii=False)
    if result["reason"] == "not_found":
        return f"Error: memory file not found at {path}"
    if result["reason"] == "conflict":
        return _conflict_error(result)
    return f"Error: memory append failed: {result}"


@_safe
async def _memory_delete(args: dict, ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    if_version = args.get("if_version")

    if not path:
        return "Error: 'path' is required"
    try:
        path = memory_files._validate_path(path)
    except ValueError as exc:
        return f"Error: {exc}"
    if if_version is None:
        return "Error: 'if_version' is required (use __new__ to create)"

    try:
        result = await memory_files.memory_delete(
            ctx.db, ctx.user_id, path, str(if_version)
        )
    except ValueError as exc:
        return f"Error: {exc}"
    if result["ok"]:
        return json.dumps({"path": path, "deleted": True}, ensure_ascii=False)
    if result["reason"] == "not_found":
        return f"Error: memory file not found at {path}"
    if result["reason"] == "conflict":
        return _conflict_error(result)
    return f"Error: memory delete failed: {result}"


register(Tool(
    name="memory_read",
    description=(
        "Read a per-user memory file. The result is JSON with path, content, "
        "version, description, aliases. ALWAYS read before writing anything — "
        "every memory write takes if_version from a prior read."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Memory file path, e.g. /notes.md"},
        },
        "required": ["path"],
    },
    handler=_memory_read,
))

# The mutating memory_* tools are deny-by-default (first_party=False), exactly like
# bash and the file tools. A fetched web page can say "write X to /profile.md";
# if memory_write were default-allowed the page could plant content that
# build_memory_context injects verbatim into every future system prompt — the F7
# persistent prompt-injection chain. Under deny-by-default the model can only write
# memory if the user explicitly grants it (PUT /v1/agent/tools/{name}/permission),
# which degrades the chain to "user has already opted into this tool".
register(Tool(
    name="memory_write",
    description=(
        "Create or overwrite a per-user memory file. if_version=__new__ creates "
        "the file (fails with a conflict if it already exists); otherwise pass the "
        "current version from a prior memory_read. Returns {\"path\", \"version\"}. "
        "A stale if_version is rejected with the current content/version so you can "
        "merge and retry. description defaults to the filename with underscores "
        "replaced by spaces; aliases (optional) are extra names the file is known by."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Memory file path, e.g. /preferences.md"},
            "content": {"type": "string", "description": "Full file content"},
            "if_version": {"type": "string", "description": "Current version from memory_read, or __new__ to create"},
            "description": {"type": "string", "description": "One-line description of what the file holds"},
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional aliases for the file",
            },
        },
        "required": ["path", "content", "if_version"],
    },
    handler=_memory_write,
    first_party=False,
))

register(Tool(
    name="memory_str_replace",
    description=(
        "Replace EXACTLY ONE occurrence of old_str in a memory file. Fails when "
        "old_str occurs 0 times (not_found) or more than once (ambiguous — give "
        "more surrounding context). Pass the current version from memory_read as "
        "if_version; a stale version is rejected with the current state."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Memory file path, e.g. /profile.md"},
            "old_str": {"type": "string", "description": "The exact text to find (must occur exactly once)"},
            "new_str": {"type": "string", "description": "The replacement text"},
            "if_version": {"type": "string", "description": "Current version from memory_read"},
        },
        "required": ["path", "old_str", "new_str", "if_version"],
    },
    handler=_memory_str_replace,
    first_party=False,
))

register(Tool(
    name="memory_append",
    description=(
        "Append content to a memory file (a blank line separates it from the "
        "existing content). Pass the current version from memory_read as "
        "if_version; a stale version is rejected with the current state."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Memory file path, e.g. /notes.md"},
            "content": {"type": "string", "description": "Text to append"},
            "if_version": {"type": "string", "description": "Current version from memory_read"},
        },
        "required": ["path", "content", "if_version"],
    },
    handler=_memory_append,
    first_party=False,
))

register(Tool(
    name="memory_delete",
    description=(
        "Delete a memory file. Pass the current version from memory_read as "
        "if_version; a stale version is rejected and the file is NOT deleted."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Memory file path to delete"},
            "if_version": {"type": "string", "description": "Current version from memory_read"},
        },
        "required": ["path", "if_version"],
    },
    handler=_memory_delete,
    first_party=False,
))
