"""bash tool — shell execution inside the workspace via the sandbox."""

import json

from app.services.sandbox.factory import get_sandbox
from app.services.tools.registry import Tool, ToolContext, register
from app.services.workspace.store import get_workspace_store


async def _bash(args: dict, ctx: ToolContext) -> str:
    agent_id = getattr(ctx, "agent_id", None)
    if not agent_id:
        return "Error: no workspace (no agent selected)"
    cmd = args.get("command") or args.get("cmd")
    if not cmd:
        return "Error: command is required"
    if len(str(cmd)) > 8000:
        return "Error: command too long"
    workdir = args.get("workdir", ".") or "."
    # Share the workspace lock (Q3) — bash must not interleave with file edits' git commits
    store = get_workspace_store()
    async with store.with_workspace_lock(str(ctx.user_id), str(agent_id)):
        sandbox = get_sandbox()
        res = await sandbox.exec(str(cmd), str(workdir), str(ctx.user_id), str(agent_id))
        # F9: bash runs arbitrary commands (git clone, dd) that grow the workspace
        # past the file-tool quota. Enforce the SAME quota after every exec (the
        # lock is held, so the du is consistent). .git is now counted by du_mb.
        if store.du_mb(str(ctx.user_id), str(agent_id)) > store.quota_mb():
            return json.dumps({
                "stdout": res.stdout,
                "stderr": "workspace quota exceeded (bash output may be truncated)",
                "exit_code": 413,
                "truncated": True,
            }, ensure_ascii=False)
    # Return structured JSON so the model can see exit code + streams
    out = {"stdout": res.stdout, "stderr": res.stderr, "exit_code": res.exit_code}
    if res.truncated:
        out["truncated"] = True
    return json.dumps(out, ensure_ascii=False)


register(
    Tool(
        name="bash",
        description="Execute a shell command inside the agent's workspace sandbox. Returns {stdout, stderr, exit_code}. One bash at a time per workspace (others queue).",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "shell command to run via bash -lc"},
                "workdir": {"type": "string", "description": "relative workdir inside workspace, default '.'"},
            },
            "required": ["command"],
        },
        handler=_bash,
        first_party=False,
    )
)
