"""File tools for the workspace — registered as deny-by-default, gated by the agent filter."""

import json

from app.services.tools.registry import Tool, ToolContext, register
from app.services.workspace.store import get_workspace_store


async def _list_files(args: dict, ctx: ToolContext) -> str:
    store = get_workspace_store()
    agent_id = getattr(ctx, "agent_id", None)
    if not agent_id:
        return "Error: no workspace (no agent selected)"
    path = args.get("path", ".") or "."
    try:
        files = store.list_files(str(ctx.user_id), str(agent_id), str(path))
        return json.dumps(files)
    except Exception as e:
        return f"Error: {e}"


async def _read_file(args: dict, ctx: ToolContext) -> str:
    store = get_workspace_store()
    agent_id = getattr(ctx, "agent_id", None)
    if not agent_id:
        return "Error: no workspace (no agent selected)"
    path = args.get("path")
    if not path:
        return "Error: path is required"
    try:
        data = store.read_file(str(ctx.user_id), str(agent_id), str(path))
        # Return content + per-line hashes so the model can do hashline edits
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"


async def _write_file(args: dict, ctx: ToolContext) -> str:
    store = get_workspace_store()
    agent_id = getattr(ctx, "agent_id", None)
    if not agent_id:
        return "Error: no workspace (no agent selected)"
    path = args.get("path")
    content = args.get("content")
    if not path:
        return "Error: path is required"
    if content is None:
        return "Error: content is required"
    expected = args.get("expected_hashes")
    try:
        res = await store.write_file(
            str(ctx.user_id), str(agent_id), str(path), str(content),
            expected_hashes=expected,
            tool_call_id=getattr(ctx, "tool_call_id", None),
            db=getattr(ctx, "db", None),
        )
        return json.dumps(res)
    except Exception as e:
        return f"Error: {e}"


async def _edit_patch(args: dict, ctx: ToolContext) -> str:
    store = get_workspace_store()
    agent_id = getattr(ctx, "agent_id", None)
    if not agent_id:
        return "Error: no workspace (no agent selected)"
    path = args.get("path")
    patch = args.get("patch")
    if not path or not patch:
        return "Error: path and patch are required"
    expected = args.get("expected_hashes")
    try:
        res = await store.apply_patch(
            str(ctx.user_id), str(agent_id), str(path), str(patch),
            expected_hashes=expected,
            tool_call_id=getattr(ctx, "tool_call_id", None),
            db=getattr(ctx, "db", None),
        )
        return json.dumps(res)
    except Exception as e:
        return f"Error: {e}"


async def _edit_lines(args: dict, ctx: ToolContext) -> str:
    store = get_workspace_store()
    agent_id = getattr(ctx, "agent_id", None)
    if not agent_id:
        return "Error: no workspace (no agent selected)"
    path = args.get("path")
    old_hashes = args.get("old_hashes")
    new_content = args.get("new_content")
    if not path or not old_hashes or new_content is None:
        return "Error: path, old_hashes, and new_content are required"
    try:
        res = await store.edit_lines(
            str(ctx.user_id), str(agent_id), str(path), list(old_hashes), str(new_content),
            tool_call_id=getattr(ctx, "tool_call_id", None),
            db=getattr(ctx, "db", None),
        )
        return json.dumps(res)
    except Exception as e:
        return f"Error: {e}"


# Register — all file tools are deny-by-default (first_party=False) so the
# agent's allowed_tools + per-user ToolPermission + ENABLE_CODE_EXECUTION ceiling applies.
for _tool in [
    Tool(
        name="list_files",
        description="List files in the agent's workspace (per-user-per-agent git-backed folder).",
        parameters={"type": "object", "properties": {"path": {"type": "string", "description": "relative path, default '.'"}}, "required": []},
        handler=_list_files,
        first_party=False,
    ),
    Tool(
        name="read_file",
        description="Read a file from the agent's workspace. Returns {content, lines:[{n, hash, text}]} with per-line sha1 hashes for hashline edits.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler=_read_file,
        first_party=False,
    ),
    Tool(
        name="write_file",
        description="Create or overwrite a file in the workspace. Validates expected_hashes against current hashes when provided (409 on mismatch).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "expected_hashes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path", "content"],
        },
        handler=_write_file,
        first_party=False,
    ),
    Tool(
        name="edit_patch",
        description="Apply a unified diff patch to a file in the workspace. Requires a valid unified diff in `patch`. Validates expected_hashes when provided.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "patch": {"type": "string", "description": "unified diff (---/+++/@@)"},
                "expected_hashes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path", "patch"],
        },
        handler=_edit_patch,
        first_party=False,
    ),
    Tool(
        name="edit_lines",
        description="Hashline edit: replace lines identified by old_hashes (per-line sha1 from read_file) with new_content. All old_hashes must be present or the call fails with 'file changed, re-read'.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_hashes": {"type": "array", "items": {"type": "string"}},
                "new_content": {"type": "string"},
            },
            "required": ["path", "old_hashes", "new_content"],
        },
        handler=_edit_lines,
        first_party=False,
    ),
]:
    register(_tool)
