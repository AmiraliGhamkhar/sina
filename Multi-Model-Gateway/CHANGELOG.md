# Changelog

## 2026-09-06 — Roadmaps updated: backend Phase 6 (data/AI platform) + web-SPA polish-first

Docs-only change (no code).

- **`docs/backend-roadmap.md`:** new "Phase 6 — Data & AI platform upgrades (planned,
  JD-driven)" — P6.1 usage warehouse + DuckDB/dbt analytics pipeline, P6.2 eval harness +
  prompt experiments, P6.3 learned router (champion/challenger over the keyword rules),
  P6.4 governance pack (provenance completion, PII redaction before embedding, retention/
  erasure, permission audit log). Build order: P6.1 → P6.4(a) → P6.2 → P6.3.
- **`docs/frontend-roadmap.md`:** reframed — the React/Vite web SPA is the primary client
  for the JPMC application window; the Expo mobile plan is deferred (kept, not deleted).
  New Phase P0 (polish, from the 2026-09-06 web-interface-guidelines review: a11y, focus
  management, reduced motion, headings, copy, scale) runs BEFORE any new features; new
  Phase F1 (usage/governance dashboard, eval scorecards, governance views) consumes backend
  Phase 6 and starts only after P0. Stale assumption fixed: the web app does have vitest
  tests.

## 2026-09-06 — Installable from a fresh clone: cross-platform venv setup

The backend already had a pip-compile lockfile (`backend/requirements.txt`), but a fresh
venv install was broken on Windows and the non-Docker path was undocumented.

- **`backend/requirements.txt`:** `uvloop` (pulled by `uvicorn[standard]`, Unix-only) was
  pinned **without** its `sys_platform != "win32"` marker — the compile step dropped it —
  so `pip install` died trying to build uvloop on Windows. The marker is now on the pin.
  `requirements.in` warns that regeneration must preserve it.
- **Root `requirements.txt` shim** (`-r backend/requirements.txt`) so the canonical
  `python -m venv .venv && pip install -r requirements.txt` works from a fresh clone.
- **README:** new "Running the Backend Without Docker (venv)" section — venv + pip,
  `backend/.env` with the required keys (use `localhost`, not `host.docker.internal` —
  no container network on the bare-metal path), pgvector/Redis reachability (compose
  publishes no host ports; override-file snippet included), alembic + uvicorn + arq
  commands, offline test command.
- **Verified:** fresh venv on Python 3.13 / Windows installs the full lockfile and
  `import app.main` succeeds (previously failed at uvloop).
- `.gitignore`: `.venv/` and `venv/`.

## 2026-08-29 — Sandbox egress isolation (last OPEN item)

The sandbox ran on the default Docker bridge, so a model-driven `bash` could reach **every** internal service (postgres, redis, backend, worker, searxng) and the **host network** via `host.docker.internal`. `SANDBOX_ALLOWLIST` was dead config (read by nothing) — can't fix egress by pattern-matching `cmd`.

- **Network segmentation in `docker-compose.yml`:** added a `sandboxnet` user-defined network. The **sandbox sits on it alone** → its only compose peer is the backend (`/exec`), and it has **no route** to postgres/redis/backend-internals/worker/searxng. The backend joins `[default, sandboxnet]` so `http://sandbox:8001` still resolves. A user-defined bridge still provides outbound NAT, so pip/npm/git builds keep working.
- **Removed `host.docker.internal` from the sandbox** — the sandbox never calls LM Studio/ComfyUI/host (the backend does); exposing the host network was added attack surface. The backend keeps its host-gateway.
- **Scope:** segmentation (keep the shell off internal services + host), not a full domain allowlist (that would need an egress proxy). `SANDBOX_ALLOWLIST` remains documented as aspirational dead config.
- **Verification:** YAML validated + network-attachment consistency checked by inspection (no docker on this host). Live check: `docker compose exec sandbox bash -lc 'curl -s http://postgres:5432'` should time out while `pip install` still works.

## 2026-08-29 — Per-tenant workspace confinement (F1/C2): bash can no longer cross tenants

The last CRITICAL security item is now enforced, not just promised. `bash -lc` runs as a **distinct OS UID** in a workspace directory `chmod 700`'d to that UID, so it cannot read/write another tenant's workspace on the shared `workspaces` volume.

- **New `sandbox/uid_alloc.py`** (deep module): owner-as-registry UID allocation — the workspace directory's `st_uid` IS the record (no side table, collision-free, self-healing). Range 10000–65000, clear of system accounts and `nobody`.
- **`sandbox/app.py`** allocates on first exec (`chown -R (:uid:gid)`, `chmod 700` the top dir), then spawns `bash` with `Popen(user=<uid>, group=<uid>, start_new_session=True)`. The kernel clears ALL of the child's capabilities when it drops to a nonzero UID, so the bash child is fully unprivileged. `HOME` is set to the workspace so pip/npm/git work; `TMPDIR` points at the tmpfs. (Also fixed a latent `BaseModel` `NameError` — it worked only via FastAPI's accidental re-export.)
- **`docker-compose.yml`:** the sandbox controller uses a **minimal cap allow-list** (`SETUID`, `SETGID`, `CHOWN`, `FOWNER`, `DAC_OVERRIDE`, `KILL`) instead of `cap_drop: ALL` — `chown`/`setuid` need those caps even as root. Everything else stays dropped, `no-new-privileges`/`read_only` remain; the bash child is still capability-less.
- **`backend/Dockerfile`:** the prod target no longer runs as `appuser` — the trusted backend must be root to write any tenant's 700 workspace.
- **Tests:** `tests/test_uid_alloc.py` (6 offline cases — range bounds, next-free selection, collision-free by construction, discoverable-used scan). The chown/setuid path runs only in the Linux sandbox container (no docker on this host, so `docker compose up` should be run before flipping `ENABLE_CODE_EXECUTION=true`).
- **ADR-0002** updated with the confinement design + the cap allow-list consequence.

## 2026-08-28 — Workspace disk quota enforced for bash (F9)

- **`.git` now counts toward the workspace quota** (`store.du_mb` no longer skips `.git`). A model-controlled `bash` could run `git clone`/`dd` and grow the workspace far past the quota (the file tools' `_check_quota` was the only gate, and it excluded `.git`).
- **The `bash` tool enforces the same quota after every exec.** With the workspace lock held, `store.du_mb(...) > store.quota_mb()` returns a structured `exit_code: 413` result ("workspace quota exceeded"), so bash is held to the same disk cap as the file tools. Added a public `store.quota_mb()` accessor.
- **Tests:** `test_workspace_store.py::DuQuotaTests` (`.git` counted; `quota_mb()` returns the setting) and `test_file_tools.py::BashToolTests::test_quota_exceeded_after_exec_returns_413`.

## 2026-08-28 — Workspace symlink-swap hardening (F8): O_NOFOLLOW text I/O

- **`services/workspace/store.py` reads and writes now refuse a final-component symlink via `O_NOFOLLOW`** (POSIX), closing the F8 symlink-swap TOCTOU: a model-controlled `bash` could race the file tools by swapping a workspace file for a symlink between `_resolveInside`'s check and the actual `open()/read`. `_resolveInside` already rejected symlink *escape* (a link pointing outside the workspace); `O_NOFOLLOW` closes the remaining final-component race. On platforms without `O_NOFOLLOW` (Windows) it degrades to a plain open — the compose volume is Linux, so the guard is effective where the workspace actually lives. Applied to `read_file`, `write_file`, `apply_patch`, and `edit_lines` via new `_read_text_fp`/`_write_text_fp` helpers.
- **Tests:** `test_workspace_store.py::NofollowIoTests` — read/write round-trip on all platforms; the actual symlink-refusal case runs on POSIX (it's skipped on Windows where `O_NOFOLLOW` is absent, but runs in the Linux backend container).

## 2026-08-28 — Sandbox: kill the whole process group on timeout (no orphaned grandchildren)

- `sandbox/app.py::exec_cmd` now runs bash with `start_new_session=True` and kills the **entire process group** on timeout (`os.killpg(proc.pid, 9)`) instead of just timing out. Previously `subprocess.run(timeout=30)` raised `TimeoutExpired` (→ 500) and orphaned detached grandchildren like `sleep 1000 &`, which kept running and consuming quota/CPU after the request returned. Also replaced the odd `os.getpgid(0)` no-op with a guarded `killpg(proc.pid, 9)`; a `Popen` launch failure now returns a bounded `exit_code=1` error string instead of a 500. Note: the sandbox is a separate Linux container not covered by the backend test discovery — this change is verified by inspection (no docker on this host).

## 2026-08-28 — Agent adapter de-duplication (D4): one `_execute_tool`, no divergent copy

- **Deleted a dead, divergent copy of `_execute_tool` in `services/agent/agent.py`.** The live loop (runtime.py) already owned `_execute_tool` and had the F11 fix (generic error strings, no internal-topology leak). But the adapter kept a second copy that was never called at runtime — yet re-echoed exception text (`f"...failed: {e}"`). This is the exact no-locality failure the report flagged: the F11 fix landed in one of two copies. The dead copy is gone; `_execute_tool` lives only in `runtime.py` and is imported by the tests from there. The test now asserts the **generic** message (`"Error: tool 'boom' failed"`, not `...failed: kaboom`), and cleans up the adapter's now-unused `asyncio`/`time`/`ToolPermission`-accrued imports.
- The adapter is reduced to setup + SSE framing (DB resolution, allowed-tools view, `AgentRuntimeCtx` build), per ADR-0005.

## 2026-08-28 — Memory injection policy (D3): default-deny writes, capped/delimited tier-1.5

- **Closes the F7 persistent prompt-injection chain at the tool gate.** The four *mutating* memory tools (`memory_write`, `memory_str_replace`, `memory_append`, `memory_delete`) are now `first_party=False` (deny-by-default), exactly like `bash` and the file tools. They were the only default-allowed mutators in the codebase: a fetched web page could say "write X to `/profile.md`", and unless a user explicitly granted it the page's content was injected verbatim into every future system prompt via tier-1.5. Now the model cannot write memory without an explicit `PUT /v1/agent/tools/{name}/permission` grant. `memory_read` stays default-allowed (reads don't persist injection). `memory_curation` is unaffected — it calls `memory_files` primitives directly, not the gated tool path.
- **Defense-in-depth on the read side:** `build_memory_context` now caps each tier-1.5 file's injected byte length (`MEMORY_TIER1_5_INJECT_CAP`, default 2000) and wraps it in `<memory_file path="...">`/`</memory_file>` delimiters instead of the old `--- path ---` header, so injected content can't be mistaken for prompt structure or dominate the context.
- **Tests:** `test_agent_adapter.py::MemoryToolDefaultDenyTests` (3 cases, run offline via `import_with_stubs`) — `memory_read` stays allowed, all four mutating tools are deny-by-default, and `get_allowed_tools` surfaces no mutating memory tool for a real user with no grants. `test_memory_files.py` tier-1.5 test updated to the new delimiter + a byte-cap assertion.

## 2026-08-28 — Egress seam (D1): one deep module owns every outbound HTTP call

New `services/egress.py` is the single place the backend reaches the network. It owns the SSRF guard and the response byte cap, so a contributor cannot (and does not) skip them:

- **Closes F5 (DNS-rebinding TOCTOU):** every outbound request now resolves its hostname ONCE, validates it, and connects to that exact validated IP while preserving the original hostname for the Host header and TLS SNI — verified for both HTTP and HTTPS (TLS cert + SNI survive). A DNS answer that changes between the resolve and the connect can no longer redirect a public URL into the internal network.
- **Closes F6 (response-buffer OOM):** the body is streamed through a byte cap that counts the *decoded* length, so a gzip-bomb (or a `Content-Length` lie) is aborted mid-stream instead of buffered into memory.
- **Policy tiers (the seam is real, >1 adapter):** `INTERNET` (public-only, default — used by `fetch_page`, DuckDuckGo), `INTERNAL` (no SSRF check for configured internal endpoints like SearXNG), `PRIVATE_ALLOWED` (public check, but permits private/loopback for locked-down deployments). `EgressError` carries a generic message — no internal IPs/hostnames reach the model (F11-consistent).
- **`search.py` delegates both backends + `fetch_page` to egress.** The local `_assert_public_host` / resolve / validate copy is deleted (it was the "one guarded caller" that every future fetch site would have copied). `fetch_page` maps refusals to `""`; research falls back to a snippet.
- **Tests:** `tests/test_egress.py` (12 cases) — SSRF refusal (private/loopback/link-local-metadata), pin-connect to the validated IP with Host-header preservation, byte-cap abort + under-cap pass, redirect re-validation to a hostile internal host, both policy tiers, and search-backend routing (GET+params, POST+form).

## 2026-08-28 — Security sweep fixes: sandbox secrets/egress, git-hook hardening, tool leaks

Whole-repo security sweep of the agent's ability to affect the host (two parallel audits + manual verification of every critical claim). Compose/app hardening plus code fixes:

- **CRITICAL C1/F2 — sandbox no longer inherits `.env`.** `docker-compose.yml` gave the sandbox `env_file: .env`, so a prompt-injected agent's `env`/`printenv` exfiltrated `SECRET_KEY` (→ forge any user's JWT + derive the Fernet provider-key encryption key per `core/crypto.py:38`), `DATABASE_URL`, `POSTGRES_PASSWORD`, etc. The sandbox needs none of it — `env_file` removed (the single `SANDBOX_SHARED_SECRET` env var is wired explicitly to both containers).
- **HIGH H3 — `/exec` now requires a shared secret, fail-closed.** The sandbox ran an unauthenticated root-bash endpoint on the shared bridge. `sandbox/app.py` rejects every request without a valid `X-Sandbox-Token` (timing-safe compare); with `SANDBOX_SHARED_SECRET` unset it refuses everything (fail closed). `HttpSandbox` sends the header on every call. **Action required: set a long random `SANDBOX_SHARED_SECRET` in `.env` before starting the stack.**
- **HIGH H4 — git-hook execution vector closed (verified end-to-end).** Bash can plant `.git/hooks/pre-commit` in any workspace; the backend's own `git commit` then executed it inside the backend container (which holds the OpenRouter secret and GPU access). Proven with a live experiment: planted hook fired on raw git, did NOT fire with the new hardening. All store git calls now go through `_git()` with `core.hooksPath=/dev/null`, `core.fsmonitor=false`, `credential.helper=` (command-line `-c` wins over the repo's own config). Regression test: `test_planted_git_hook_does_not_execute` (includes a control that proves the test isn't vacuous).
- **MEDIUM M1 — container hardening on the sandbox service.** `read_only` root fs + `/tmp` tmpfs, `cap_drop: ALL`, `no-new-privileges`, `mem_limit: 512m`, `pids_limit: 128` (previously: root, no caps dropped, no limits; `SANDBOX_MEMORY_MB` was dead config).
- **MEDIUM H2 — honest egress note.** The compose comment claimed "egress is allowlisted in the sandbox app itself"; no allowlist existed anywhere (`SANDBOX_ALLOWLIST` was read by nothing). Comment corrected; real egress restriction is still an open item (needs compose network policy — see roadmap).
- **LOW F10 — `generate_image` caps `negative_prompt`** at 4000 chars (only `prompt` was capped; a multi-MB negative prompt flowed into the ComfyUI graph).
- **LOW F11 — tool failures return generic strings.** `_execute_tool` no longer echoes exception text (e.g. SSRF-guard messages naming internal IPs/hostnames) to the model; details go to server logs only.
- **LOW F12 — `file_edits.tool_call_id` is now populated.** The runtime sets `ToolContext.tool_call_id` per dispatch, so audit rows link to the agent's tool call (was always NULL).
- **LOW F13 — `write_file` optimistic locking fixed.** The stale-hash check was `A and B`, letting stale `expected_hashes` slip through when the hashes existed elsewhere in the file (e.g. all-identical lines); now exact-prefix comparison.
- Full findings report (including OPEN items: per-exec workspace confinement F1/C2, DNS-rebinding TOCTOU F5, response-buffer OOM F6, memory-file default-allow chain F7, bash-quota bypass F9) in `docs/backend-roadmap.md`.

## 2026-08-28 — Workspace: validate patch-body target paths (defense-in-depth)

- Backend: `WorkspaceStore.apply_patch` now validates the unified diff's own `---`/`+++` header paths through the same `_resolveInside` rules as the `path` argument (`_validate_patch_targets`), before handing the patch to `patch -p1`. Previously only the `path` argument was checked — the diff body (what `patch` actually opens, cwd=workspace) was trusted. Closes the direct `../` header escape on `patch` builds that don't refuse it, and the symlink-planted-inside-the-workspace variant. Verified claim severity first: modern GNU patch (≥2.6, what Debian/Alpine images ship) already refuses `..` targets that escape cwd — so this is hardening restoring ADR-0008's "one 422 seam", not an open RCE. Tests are offline (rejection fires before the subprocess).

## 2026-08-28 — Agent stream lifecycle, capability gate, memory rollback (architecture review C1/C2/C4)

- **Backend:** fixed a critical stream-slot leak in the agent path. `services/agent/agent.py::run_agent` released the per-user stream slot only `if not entered_runtime`, so every successful (and every mid-run-failed) agent chat leaked a slot — after `MAX_CONCURRENT_STREAMS` runs the user was hard-429'd until backend restart. `run_agent`'s `finally` now releases unconditionally (mirroring `routers/chat.py`'s outer `stream_tokens` wrapper); `release_stream_slot` is already idempotent so this cannot double-free. The chat/research paths already released correctly; this was the one divergent lifecycle.
- **Backend:** closed a capability-gate bypass. The legacy global tool path (`get_allowed_tools`) did not apply the `ENABLE_CODE_EXECUTION` master-switch ceiling that `get_allowed_tools_for_agent` applies, so a user could self-grant `write_file`/`edit_patch`/`edit_lines` via `PUT /v1/agent/tools/{name}/permission` and then chat *without* an `agent_id` to get real filesystem writes with the switch off. A shared `_ceiling_allows()` helper now gates **both** allowlist paths (one source, can't drift).
- **Backend:** `safe_build_memory_context` (memory_files) now calls `await db.rollback()` on the swallow path, matching `memory.py::retrieve_memories`. A failed SQL statement poisons an asyncpg transaction; without the rollback the next query on the same session (which both chat.py and agent.py run after this call) raised `PendingRollbackError` — neither an `AppError` nor an `APIError` — crashing the chat stream / producing a generic 500 in the agent.
- **Frontend:** widened the agent SSE `done` event's `conversation_id` to `string | null` and relaxed `parseAgentEvent`'s guard (ADR-0007). The backend emits `{"type":"done","conversation_id":null}` when the agent 404s/403s before the runtime starts; the old `typeof === "string"` guard silently dropped that terminal frame, so clients never saw stream completion. `use-agent.ts:86` already null-coalesces (`?? convoIdRef.current`), so consumers are unaffected.
- **Tests:** backend `test_stream_guard.py` (unconditional-release / no-double-free stability) + `test_agent_adapter.py` (`get_allowed_tools` master-switch ceiling regression); frontend new `lib/agent-events.test.ts` (5 cases, incl. the null `conversation_id` done event).

## 2026-08-28 — Frontend: API facade is the seam; unified refresh; SSE codec + vitest

- Frontend: `api-endpoints.ts` no longer leaks. The transport singleton's two non-JSON capabilities — chat streaming and authed blob fetching — are now exposed through the facade as `chatApi.stream` and `imageApi.fetchBlob`, and `hooks/use-chat.ts` + `lib/authed-image.ts` moved off the raw transport. The low-level `apiClient` object is now imported in exactly one place.
- Frontend: the 401 → refresh → retry policy is a single shared `authFetch` primitive in `api-client.ts` used by both `request()` (JSON) and `fetchBlob()` (binary) — it was previously copy-pasted and had silently diverged (a retried-401 signed out in `fetchBlob` but not in `request`). Now a surviving 401 after a refresh attempt always treats the session as dead and signs the user out, while anonymous calls (no token attached) keep their "wrong credentials" 401 as a plain error. Refresh coalescing is unchanged (one in-flight refresh for concurrent 401s).
- Frontend: `request()` dropped its `stream` option and the `response as unknown as T` type lie — streaming now goes through `authFetch` directly in `streamChat`/`streamEvents`, which return a real `Response`.
- Frontend: added a `buildQuery()` helper in `api-endpoints.ts` to replace the identical `URLSearchParams` list-params builder that was copy-pasted 3× (the Hugging Face model query — a distinct construction — was left as-is); the `@` alias config lives in one place too.
- Frontend: wired up **vitest** (devDependency + `npm test` script + dedicated `vitest.config.ts` kept out of the build's `tsconfig.node.json` so it can't type-clash with the project's vite). Replaced the shallow "pairing" test with 10 behavior tests covering refresh-retry success, the surviving-401 sign-out (the drift fix), no-refresh-sign-out, anonymous-no-sign-out, refresh coalescing, `fetchBlob` sharing the policy, and SSE frame splitting across chunk boundaries for both the JSON stream and the plain-token chat stream.
- Frontend: moved SSE *interpretation* out of the transport into a new `lib/sse-events.ts` codec module — `interpretChatFrame` (the plain-token chat protocol) and `parseSseJson` (the JSON-object protocol). The transport keeps only the genuinely byte-level `\n\n` frame splitter (`_readSseChunks`); the typed agent-event union (`agent-events.ts`, ADR-0007) stays a layer above, consuming `parseSseJson`'s payload. Added 13 pure unit tests for the codec (token/newline/DONE/ERROR/legacy shapes, JSON parsing, malformed-JSON tolerance).

## 2026-08-27 — Test coverage: agent adapter (policy + tool dispatch)

- Tests: new `backend/tests/test_agent_adapter.py` (21 cases) covering the agent adapter's DB-free surface — `get_allowed_tools` (per-tenant policy), `get_allowed_tools_for_agent` (agent.allowed_tools ∩ per-user grant ∩ master switch; 404/403), `_resolve_agent` (404/403/public-read), `_ensure_conversation_agent_binding` (stamp / don't-overwrite / version default), and `_execute_tool` (invalid JSON, non-object args, timeout, handler exception-as-string, truncation). All run with a mocked DB + stubbed registry.
- Tests: extracted a shared `tests/agent_test_stubs.py` (`import_with_stubs`) used by the agent-package + file/bash/sandbox tests — it stubs asyncpg/pgvector/redis/prometheus/langfuse/arq only during import and restores them, so these coverage tests run on a bare host without polluting sibling tests. `test_agent.py`/`test_agent_runtime.py` now use it (so they run consistently, no longer skip-on-host via a stale-guard side effect).

## 2026-08-27 — Test coverage: workspace file + bash tools (T3)

- Tests: new `backend/tests/test_file_tools.py` (11 cases) covering the file tools (`list_files`/`read_file`/`write_file`/`edit_patch`/`edit_lines`) and `bash` inside a temp git workspace — write→read roundtrip with per-line hashes, list, missing-agent error, edit_lines replacement, hash-mismatch conflict, edit_patch diff, stale-expected-hashes conflict, and bash (missing cmd, too-long cmd, structured mock-sandbox output). Runs offline (stubs asyncpg/pgvector/redis/etc. only during import, then restores so sibling tests are unaffected).
- Tests: `test_memory_files.py` `MemoryToolsRegistryTests` now also skips when `memory_files` is unavailable (exposed a pre-existing ordering fragility when the tools package is pre-imported).

## 2026-08-27 — Test coverage: sandbox seam (T3 execution boundary)

- Tests: new `backend/tests/test_sandbox.py` (13 cases) covering the Sandbox seam — the `Sandbox` Protocol / `ExecResult` dataclass, `MockSandbox` (echo + fail-keyword + truncation, never touches the filesystem), `HttpSandbox` (POST-to-`/exec` body mapping, timeout → exit 124, HTTP error → exit 1, via a stubbed httpx client), and `get_sandbox()` backend selection (mock when disabled/no URL, HTTP when enabled+URL set).

## 2026-08-27 — Design-practice cleanup (frontend SSE transport + image composer)

- Frontend: `lib/api-client.ts` no longer has two SSE transport readers. A shared private `_readSseChunks` generator owns the `\n\n` boundary split + tail flush, and both `streamChat` (plain tokens) and `_readSseStream` (JSON objects) consume it — only the event interpreter differs. The chat token stream is unchanged.
- Frontend: extracted the image-composer state (~13 `useState` + aspect-ratio loading + the "New Image" nonce reset + `handleGenerate`/`onComplete`) out of `pages/images.tsx` into a new `hooks/use-image-composer.ts`. The page is now a pure render that binds the hook's state to UI.

## 2026-08-27 — Design-practice cleanup (GGUF parser split out of fit_score)

- Backend: the hand-rolled GGUF binary header walker (`_read_gguf_string`, `_read_gguf_value`, `_parse_gguf_header` plus `_GGUF_VALUE_FMT`/`_ARRAY_SKIPPED`/`_GGUF_SUPPORTED_VERSIONS`/`_GGUF_FILE_RE`/`_QUANT_RE`/`_SHARD_RE`) moved out of `services/fit_score.py` into a new pure `services/fit_score_gguf.py` module. `fit_score.py` re-exports it (including a `_parse_gguf_header` alias) so the rest of the app and tests are unchanged. This isolates the binary-format parsing domain from VRAM fit scoring and de-bloats the worst hotspot.
- Tests: new `backend/tests/test_fit_score_gguf.py` (12 cases) covering the string/value walkers, array skipping, header parsing, and the value-format table.

## 2026-08-27 — Design-practice cleanup (Smart Suggest extracted to a service)

- Backend: `Smart Suggest` (the ~200-line cloud→local LLM fallback chain) moved out of `routers/agents.py` into a new `services/agent_suggest.py` deep module. The router handler is now a thin `POST /agents/suggest` that calls `agent_suggest.suggest(goal, description, user_id, db)` and translates `SuggestError` → 502. The service raises a domain `SuggestError` (not `HTTPException`) on failure; the sugared response shape (`name`/`description`/`system_prompt`/`suggested_tools`/`suggested_model`) is unchanged.
- Tests: new `backend/tests/test_agent_suggest.py` covering the pure helpers (`_parse_suggest_json`, `_build_suggest`, `_looks_like_auth_error`).

## 2026-08-27 — Design-practice cleanup (frontend SSE contract single-sourced)

- Frontend: `AgentEvent` is no longer declared twice. `lib/types.ts` re-exports it from `lib/agent-events.ts` (which owns the runtime validators `parseAgentEvent`/`parseFileEditResult`), so the SSE contract has one source instead of drifting duplicates. The `undo` endpoint's response type in `lib/api-endpoints.ts` was corrected from `FileEdit` (the DB row) to `{ edit_id, undone, commit_sha }`, matching the backend `store.undo` return shape.

## 2026-08-27 — Design-practice cleanup (one agent-ownership helper)

- Backend: `routers/agents.py` re-ran the agent-ownership + install-eligibility query inline in 6 routes. Added two module-level helpers — `_get_owned_agent` (404 missing / 403 not-owner; used by update/delete/publish) and `_get_workspace_agent` (owner, installer, or public agent; used by the 4 workspace routes) — so the ownership policy lives once and matches the repo's 404-vs-403 convention.

## 2026-08-27 — Design-practice cleanup (agent-loop helpers one home)

- Backend: removed the duplicated `_estimate_tokens` / `_is_context_error` / `_prune_old_tool_rounds` / `_MEMORY_WRITE_TOOLS` copies from `services/agent/agent.py`. They are now defined once in `services/agent/runtime.py` (the module that owns the loop) and re-exported by `services/agent/__init__.py`, so the adapter and the tests share the exact helpers the live loop runs. Previously `test_agent.py` tested the dead `agent.py` copies while the live loop used divergent `runtime.py` copies.
- Tests: new `backend/tests/test_agent_runtime.py` (covers the runtime's live helpers + `_MEMORY_WRITE_TOOLS`); `test_agent.py` now imports the helpers from `app.services.agent.runtime` so it covers the code that actually runs.
- Docs: `runtime.py` module docstring corrected — it says no `AsyncSession` handle crosses the seam (the runtime opens its own per-tool sessions), and notes the loop helpers are canonical here.

## 2026-08-27 — Design-practice cleanup (ProviderRouter single entry point)

- Backend: removed the legacy `services/router.py` `get_provider` shim (a 10-line pass-through with one caller) and its dead duplicate `resolve_role` (zero callers). `ProviderRouter().resolve()` is now the single entry point for provider resolution; `services/agent/agent.py` calls it directly. `resolve_role` lives only in `services/provider_router.py` (pure, keyword-heuristic). `router.py` keeps the `Provider`/`ChatRequest`/`ChatMessage` facts and the client factories.
- Tests: `test_routing.py` now exercises `ProviderRouter.resolve` + `provider_router.resolve_role` directly (was mocking the removed legacy shim). `test_agents.py` uses the `resolve_role` keyword signature.
- Docs: `provider_router.py` module docstring notes it supersedes the shim.

## 2026-08-27 — Smart Suggest: cloud-then-local fallback + workspace test/de-nest

- Backend: `POST /v1/agents/suggest` now tries OpenRouter with an ordered list of `:free` models first, then falls back to LM Studio/local — so a free-only OpenRouter key no longer surfaces a raw `502 "suggest generation failed: ... User not found."`. A 401/`User not found.` is detected as an auth error and falls through to local; only when both tiers fail does it return a 502 with a hint instead of the raw provider payload. `suggest_agent` de-nested 5→1 by extracting `_suggest_cloud`/`_suggest_local`/`_parse_suggest_json`/`_build_suggest_response`/`_cloud_candidates` to module level.
- Config: new `SUGGEST_CLOUD_MODEL` (override the cloud model) and `SUGGEST_CLOUD_FALLBACK_MODELS` (comma-separated free-model candidates; default `meta-llama/llama-3.1-8b-instruct:free, google/gemma-2-9b-it:free, qwen/qwen-2-7b-instruct:free`).
- Fix: `AppError` now accepts optional `status_code` kwarg — `services/workspace/store.py` raised `AppError(status_code=422, ...)` in 27 places but the constructor only accepted `detail`, so every path-security rejection (the "single 422 seam" from ADR-0002/0003) raised `TypeError` (a 500) instead of the intended domain code. Backward compatible: positional `detail` and subclass `status_code` still work.
- Tests: new `backend/tests/test_workspace_store.py` (15 tests) covering `_resolveInside` (path-security 422 seam), `_line_hash`/`_file_hashes`, and the write→git-commit→audit→undo-by-commit_sha pipeline — offline (stubs `app.db` only during import, then restores it so sibling test modules are unaffected). Fixed stale `_validate_rel_path` reference in `test_agents.py` (renamed to `_resolveInside` in the deep-module refactor).
- ADRs: cut four for the deep-module seams — `docs/adr/0005-agent-runtime.md` (AgentRuntime loop seam), `0006-provider-router.md` (provider resolution seam), `0007-agent-events.md` (typed frontend SSE contract), `0008-workspace.md` (path-security + edit/undo deep module).

## 2026-08-13 — Agents T3 wiring: workspace panel + undo UI

- Frontend: finished T3-004 — `WorkspacePanel` now backed by `workspace-store` (`files/edits/fetchAll/undo`), `HistoryTimeline` (edits → Undo), `RightSidebar` Tools | Workspace tabs on `/agent`, `use-agent` extracts `edit_id` from every file-tool `tool_result` into `AgentStep.edit_id`; `DiffView` for file edits in `ToolStepCard` unchanged.
- Docs: `frontend-roadmap.md` "Workspace + File edits + Undo (T3)" contract section (endpoints + `FileEdit` shape + frontend notes).
- Fix: `WorkspacePanel` Button/Skeleton imports (`components\ui` → `components/ui`), `marketplace.tsx` unused `Upload`/`useAgentCatalogStore` imports, `tsconfig.app.json` excludes `api-client.test.ts` (`vitest` not in prod deps) so `npm run build` passes.

## 2026-08-13 — Fit scoring: total VRAM + RAM offload; hardware chip

- Backend: fit verdicts (local cookbook + HF browser) now score against TOTAL VRAM and factor RAM offload instead of free VRAM; `/v1/hardware` adds `ram_total_mb`. Frontend: the Models window hardware chip moved to the top tab bar showing "name · total VRAM · total RAM" (no free-VRAM metric / usage bar).

## 2026-08-13 — Unified "Models" window (Local | Cloud)

- Frontend: unified single "Models" window — Local | Cloud tabs, Cloud = two-pane HF browser (per-quant GGUF fit), Local = installed-model fit list, compact hardware box at bottom. Removed the separate Cookbook page.

## 2026-08-13 — HF model browser UI (two-pane)

- Web SPA — new `/models` two-pane HF model browser: catalog list (search + 10/25/50 dropdown) fed by `GET /v1/hf/models`, detail pane fed by `GET /v1/hf/models/{repo_id}` (stats, description, PARAMS/ARCH/DOMAIN/FORMAT metadata, capability pills, per-quant GGUF fit verdicts + rationale with a shared context-token selector). Download/install pipeline deferred — the Download affordance renders disabled with a "coming soon" hint. Cookbook HF rows navigate into the browser (`/models?repo=<id>`). Types `HfFitVerdict`/`HfQuantOption`/`HfModelDetail`/`HfModelSummary` + `hfApi.detail` added; route + sidebar "Models" nav item.

## 2026-08-13 — HF model browser follow-up (review fixes)

- Backend: GGUF header walker (`_read_gguf_value`) now skips ARRAY KV payloads in full — fixed-size scalar arrays jump `count × elem_size`, string arrays walk each element — instead of truncating at 4096 elements, which drifted every later key offset on large arrays. Array values are never materialized (the fit fields are all scalars/strings); a payload that overruns the buffer fails the parse instead of silently truncating.
- Tests: `backend/tests/test_hf_detail.py` +3 cases (large scalar array >4096 followed by scalar keys, uneven string array, truncated array payload → None). Full suite 179 tests (was 176).

## 2026-08-13 — HF model browser + GGUF-accurate fit (F1)

- Backend: new `GET /v1/hf/models/{repo_id}` (auth) — HF repo stats (downloads/likes/params/description), capability + format pills, and per-quant VRAM fits computed from each GGUF's own header (real n_layer/n_embd/n_head[_kv], exact KV formula, 10% safety margin; verdicts fits_fully / fits_cpu_offload / likely_too_large / cpu_only / unknown). Header reads are 4MB Range requests cached in-process (600s), capped at 12 quants.
- Backend: `gguf` dependency added (header parser; manual KV walker because GGUFReader needs a real path and reads all tensor data); `HF_TOKEN` / `KV_CACHE_BYTES_PER_ELEMENT` / `FIT_SAFETY_MARGIN` settings.
- Tests: `backend/tests/test_hf_detail.py` (18 cases: quant grouping/regex, fit arithmetic, header walker, mocked end-to-end detail). Full suite 176 tests (was 158).

## 2026-08-13 — Unified model-fit cookbook (Hugging Face tab)

- Backend: new `GET /v1/hf/models` (auth) — searches the Hugging Face Hub API (downloads-sorted, in-process 10-min TTL cache) and fit-scores the results with the same `estimate_fit`/verdict ranking as the local cookbook (`build_hf_cookbook` shares `_VERDICT_RANK`).
- Web SPA — Cookbook page: Local / Hugging Face source tabs; HF tab adds a search box + result-count dropdown and renders the same verdict table (params/downloads/likes sub-label, pipeline tag in Notes); recommendation banner stays local-only.
- Tests: `backend/tests/test_fit_score.py` (7 cases: safetensors parsing, model-id fallback, fetch-failure → [], cache hit, cookbook scoring/sorting, DEFAULT_BYTES_PER_PARAM). Full suite 158 tests.

## 2026-08-11 — Memory files (M2)

- Memory files M2: background curation pipeline (arq job, rule-based curation prompt, strict op parsing, versioned apply with retry-once, private chats excluded).

## 2026-08-11 — Memory files (M1)

- Memory files M1: per-user file store (`memory_files` table), 5 versioned memory tools (read/write/str_replace/append/delete), Tier-1 index + Tier-1.5 injection into chat/agent prompts.

## 2026-08-11 — New first-party agent tools

- Agent tools: added current_datetime, search_conversations, and generate_image (ComfyUI-backed, ownership-aware).
- Agent tool: safe calculate evaluator (AST whitelist — no eval/exec).

## 2026-08-11 — Maintainability (M1 + M4)

- Maintainability: pinned backend dependencies via pip-compile (requirements.in); added CI schema-drift guard + test workflow (M1, M4). The guard's first run uncovered pre-existing drift (ix_research_jobs_user_id / ix_tool_permissions_user_id) which was reconciled by restoring `index=True` on both `user_id` model columns — no new migration needed, the indexes already exist from the original migrations.

## 2026-08-11 — Auth hardening (INFO)

- Auth hardening (INFO): JWT iss/aud claims + validation, registration password policy, per-user SSE stream cap, Langfuse content documentation.
- Auth hardening (INFO): refresh tokens bound to a client device_id (replay protection); legacy tokens unaffected.
- SSE stream-slot leak fix (review HIGH): trainings/research stream setup failures (Redis, DB, cancellation) now release the reserved slot before re-raising, so acquire/release stays 1:1.
- Follow-up: trainings stream setup releases the slot unconditionally even when pubsub unsubscribe/close itself raises — cleanup is best-effort (swallowed + logged) and can no longer mask the release.

## 2026-08-11 — Config/infra hardening (S-B)

- Config/infra hardening: prometheus+grafana bound to loopback; Caddy security headers (CSP/X-Frame/etc.); DEBUG refused in production; provider base_url scheme validation + opt-in private-URL guard (S-B).

## 2026-08-11 — Backend auth hardening (S-A)

- Backend — consistent 401s for missing/invalid credentials, refresh-token sub cross-check, authed aspect-ratios, generic provider-test errors, trainings detail path leak removed, JWT iat.

## 2026-08-11 — Data integrity fixes (D4 + R7)

- Backend — optional limit/offset pagination on convo/presets/templates (D4); hourly expired refresh-token sweep via arq cron (R7).

## 2026-08-11 — Reliability fixes (R3–R6)

- Backend — honest local token counts via token_provenance + off-path tokenize sync (R3); research rejects without a capable model and stores the resolved model (R4); strict ComfyUI anchor detection + upload validation (R5); search degradation is surfaced via metric + honest tool text (R6).

## 2026-08-11 — Backend cleanup (D1–D3, M2–M3, S6)

- Backend cleanup: unified convo ownership checks (D1); OpenRouter models list timeout (D2); memories migration downgrade (D3); removed stale Gemini references (M2); parameterized POSTGRES_USER/POSTGRES_DB (M3); documented MCP_SERVERS operator-trust boundary (S6).

## 2026-08-11 — Security hardening (S2–S7)

- S2: /metrics gated behind METRICS_TOKEN (Bearer; 404 when unset); backend port bound to 127.0.0.1
- S3: removed unauthenticated Caddy /view* proxy; ComfyUI files served via authed GET /v1/images/file (ownership + traversal guards)
- S4: /auth/register no longer reveals whether an email is registered
- S5: rate limiter honors X-Forwarded-For only from TRUSTED_PROXIES (default Docker subnet 172.16.0.0/12)
- S7: REGISTRATION_ENABLED flag (false = signups disabled; register your account first)
- Web SPA — authed image loading (S3 compliance): images fetched via Authorization header as blob URLs (AuthedImage); removed obsolete ComfyUI host rewriting; register page handles generic signup errors + 'registration disabled' (S4/S7)
- Web SPA — Providers settings screen (BYO-key): list/create/edit/delete/test providers against /v1/providers; masked keys; role + type badges; default + enabled toggles.
- Web SPA — LoRA Training screen: dataset zip upload, base model/steps/LR/resolution, live SSE progress, cancel, artifact download, sample preview, and a trained-LoRA picker on image generation; backend adds authed GET /v1/trainings/{id}/sample.
- Review fixes: provider PATCH now enforces base_url for openai_compatible providers; fetchBlob treats every 401 as session expiry (incl. post-refresh retry); use-training-job state updaters guarded against stale/cancelled writes.

## 2026-08-11 — Reliability fixes (R1 + R2)

- Backend — agent loop no longer holds the DB connection for the whole run (R1); agent messages pruned before tool rounds and context-overflow degrades to a tool-less final answer with truncated=True (R2).

## Earlier work (summary)

- BYO-key provider system + provider routing (Phase 1/1b)
- Idempotent setup scripts (Phase 2)
- LoRA training pipeline with ai-toolkit worker (Phase 3) + SD1/SDXL support
- Docker data disk compaction (~57 GB → ~21 GB)
