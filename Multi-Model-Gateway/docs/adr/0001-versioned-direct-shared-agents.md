# 0001 — Versioned direct-shared instance marketplace

Public Agents are one row; `agent_installs(user_id, agent_id, pinned_version)` points to
it. Owner edits stage on the row, then publishes → `agents.version + 1` and
`agents.published_version` advances. Installers see `v3 → v4 available` and explicitly
`POST /v1/agents/{id}/install` to bump `pinned_version`. New chats use `pinned_version`;
history keeps `conversations.agent_version`.

Picked over live-propagate (owner edits apply immediately) because silent mutation breaks
trust — especially with code execution — and makes rollback impossible. Picked over
copy-on-fork because we want authors to improve one artifact everyone builds on. Pinning is
explicit (banner + upgrade button), not silent.

Consequence: the running user's `tool_permissions` remains the hard ceiling. A public Agent
can only *request* tools; it never grants itself a capability the user hasn't allowed.
See ADR-0002 (workspace/sandbox + capability gates).

Supersedes: `0001-direct-shared-agents.md` (removed in the same commit — same number,
versioned semantics).
