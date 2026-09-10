# 0003 — Patch + hashline edits with DB audit and git undo

Every mutating file operation is validated against current per-line hashes. `read_file`
returns `{content, lines:[{n, hash: sha1(line)}]}`. Writers are two tools: `edit_patch`
(unified diff, multi-hunk) and `edit_lines` (hashline: `old_hashes → new_content` for
precise single-spot fixes). Both reject on hash mismatch with a "file changed, re-read"
conflict — the same pattern as `services/tools/memory_tools.py::_conflict_error` and
`if_version`. The model sees the error as a tool result and adapts.

Every write also inserts `file_edits(id, user_id, agent_id, store, path, patch,
before_hash, after_hash, tool_call_id, created_at)` and commits the workspace git repo.
`store` discriminates `workspace` vs `memory` (Q3 B), so the same audit covers the
existing DB-backed `memory_files` and the new git-backed workspace. Undo is
`POST /v1/agents/{id}/workspace/undo {edit_id}` → reverse patch + new git commit.
Frontend renders the `file_edits` timeline with `ToolStepCard` DiffView and an Undo
button; git is the mechanical revert underneath.

Why both diff and hashline: diff for whole-file refactors, hashline for safe single-spot
fixes without hunk-context fragility. Why DB + git: the table gives per-turn attribution
and a renderable history; git gives a trustworthy, linear revert without reimplementing
patch logic.
