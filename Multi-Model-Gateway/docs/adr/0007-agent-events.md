# 0007 — AgentEvents: the typed frontend contract for the agent SSE stream

`frontend/src/lib/agent-events.ts` is the single place that names the
`POST /v1/agent/chat` stream shape. It binds a discriminated union to the raw
SSE `data:` payloads and extracts the File-Edit sub-shape so components stop
re-parsing JSON by hand.

The union is exactly the events the runtime emits (`tool_call`, `tool_result`,
`token`, `error`, `done`), with `done` carrying optional `truncated`, `agent_id`,
and `agent_version`. The File-Edit result (`FileEditResult`) is the `{edit_id,
path, patch?, commit_sha?}` shape returned by the `edit_patch` / `edit_lines` /
`write_file` tools; `parseFileEditResult` validates it, and `extractEditId` /
`shouldRenderDiff` consume it.

## Why this module (vs. parsing JSON in components)

Before this, `use-agent` and `ToolStepCard` each re-parsed the `tool_result`
content and each fell back to stripping an `"ok "` prefix. That duplicated the
shape knowledge in two places and drifted — a `FileEditResult` missing `edit_id`
was silently treated as a plain string. This module centralizes the parsing into
one tested surface:

- `isFileEditTool(name)` — which tool names are file edits.
- `parseFileEditResult(content)` → `FileEditResult | null` (rejects non-object or
  missing `edit_id`/`path`).
- `extractEditId(content)` → `string | undefined` (used by `use-agent` to stamp
  `AgentStep.edit_id`).
- `shouldRenderDiff(name, content)` — render a DiffView for a file-edit tool only
  when the result is a well-formed `FileEditResult`.

The transport (`api-client`'s `_parseSseData`) stays generic and knows nothing
about agent semantics; consuming components stay dumb about the wire.

## Alternatives considered

- **Parse inline in `ToolStepCard`/`use-agent`** — rejected; it was already living in
  two places and drifting.
- **Single monolithic event parser also owned by the backend** — rejected; the SSE
  wire is a per-route contract (see ADR-0004), and the frontend owns the shape it
  renders. The backend emits, the frontend interprets.

The deletion test is the tell: deleting this module scatters `FileEditResult`
parsing back across `use-agent`, `ToolStepCard`, and `HistoryTimeline`.

See `frontend/src/lib/agent-events.ts`, `use-agent.ts`, and
`components/agent/ToolStepCard.tsx`.
