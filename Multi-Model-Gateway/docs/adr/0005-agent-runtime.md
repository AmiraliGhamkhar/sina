# 0005 — AgentRuntime: the deep module behind the agent loop

The agent loop lives behind one method on one class:
`AgentRuntime.run(ctx: AgentRuntimeCtx) → AsyncIterator[AgentEvent]`. The adapter
(`services/agent/agent.py`) resolves the conversation, history, provider,
allowed-tool view, memory context, and system prompt, packs them into an
`AgentRuntimeCtx`, then just yields `_sse(event)` for each event the runtime
emits. The runtime owns the loop, token budget, prune, tool dispatch, and the
ordering/typing of every `tool_call | tool_result | token | error | done` event.

`AgentRuntimeCtx` carries everything the loop needs but **no DB handle** — the
runtime never imports `AsyncSession`, never holds the request's connection, and
per-tool work uses a fresh `AsyncSessionLocal` (R1). The router releases the
request DB before streaming (`await db.close()`); the runtime opens its own
short-lived sessions for each tool and the final save.

## Why split this out (vs. one big `run_agent`)

The loop is where three distinct kinds of failure cluster and where they would
otherwise smear across the router and the chat path:

- **Budget/prune** — `AGENT_TOKEN_BUDGET` / `AGENT_MAX_ITERATIONS`; `_prune_old_tool_rounds`
  drops whole tool-call rounds (an assistant message and its tool results together) so the
  "tool follows the assistant that called it" API invariant survives whatever remains.
- **Context-overflow degrade** — a provider `APIError` that reads like a context-window
  error (`_is_context_error`) demotes the remaining loop to a single tool-less final round
  instead of dying; the run still completes with a `done` event and `truncated: true`.
- **Error contract** — every failure surfaces as `error` + `done` events; the runtime never
  raises past the stream. The adapter separately handles errors that occur *before* the
  runtime starts (resolve-agent 404/403, provider unavailable), guarded by the
  `entered_runtime` flag so it releases the stream slot exactly once.

## Alternatives considered

- **Keep the loop in `services/agent.py`** — rejected. That file was a ~600-line God
  function mixing DB resolution, loop state, budget, pruning, metrics, curation, and SSE.
  The deletion test is the tell: removing this seam scatters loop/prune/budget/SSE-ordering
  bugs into every caller.
- **A callback/`ToolExecutor` secondary seam** — rejected. The tool dispatch (per-tool
  `AsyncSessionLocal`, timeout, string-return-on-failure, memory-write path capture) is
  internal to the loop; exposing it as a second seam would widen the interface for no caller
  need. One method, one seam.

Consequence: two adapters justify the seam — the real `LLMProvider`
(OpenAI-compat / OpenRouter) and an in-memory fake in `tests/test_agent_runtime.py`,
so the loop is testable without a DB or network.

See `services/agent/runtime.py` and `services/agent/agent.py` (`run_agent` becomes the thin
adapter that frames SSE).
