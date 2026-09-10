# 0004 — Wire shape, suggest flow, and seam placement

`ChatRequest` gains optional `agent_id` and `agent_version`. If `agent_id` is present,
the backend loads that `agents` row at the pinned version, uses its instructions and
its `allowed_tools` snapshot, and ignores client `preset_id`/`model` (single source of
truth). The SSE `done` event gains `agent_id` and `agent_version`; without `agent_id`
the path is unchanged — zero break on `frontend/src/pages/agent.tsx` and
`frontend/src/hooks/use-agent.ts` (backward compat).

Smart suggest is a single endpoint `POST /v1/agents/suggest {goal, description?}` →
same LLM (`get_provider`) returns `{name, description, system_prompt,
suggested_tools[], suggested_model}` drawn from `registry.all_tools()`. The form
prefills editable fields and pre-checks tools in `AgentToolsPanel`. One call, no new
store layer.

Seams are three narrow interfaces plus pure tables:

- `backend/app/services/workspace/` — `WorkspaceStore` (create/open on the named volume,
  `git init`, `read_file` → `{content, lines:[{n,hash}]}`, `apply_patch`/`edit_lines`,
  `du` quota, `undo(edit_id)`). Owns the volume and the per-workspace `asyncio.Lock`.
- `backend/app/services/sandbox/` — `Sandbox` protocol (`exec(cmd, workdir) →
  {stdout,stderr,exit}`), `MockSandbox` for tests/dev (`ENV=dev`), `HttpSandbox` (httpx →
  `sandbox:8001/exec`). Knows nothing about agents.
- `backend/app/models/agents.py`, `agent_installs.py`, `file_edits.py` — pure tables.
- `backend/app/routers/agents.py` owns CRUD + publish/suggest/install; `routers/agent.py`
  only adds `agent_id/version` handling and delegates to
  `services/agent.py:get_allowed_tools_for_agent(agent_id, version, user_id, db)` which is
  `agent.allowed_tools_at(version) ∩ tool_permissions ∩ capability gate` (master switch +
  per-user).

Why: keeps `services/agent.py:run_agent` a loop, not a 600-line God function; each seam
is deep (much behavior, small interface), independently testable with fakes (no Docker in
unit tests), and respects the existing patterns (`registry.openai_schema`,
`_conflict_error`/`if_version`, `stream_guard`, `AppError → SSE error`).
