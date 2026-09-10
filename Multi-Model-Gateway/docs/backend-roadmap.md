# Backend Roadmap — Security & Functionality

A complete review of the FastAPI backend (`backend/app/`), verified against the code as it
stands now — not the `docs/issues.md` registry, which is stale. The product: use local LLMs
(LM Studio) and local image gen (ComfyUI) from a phone over Tailscale, with OpenRouter as an
optional cloud provider. Single-user, personal, tailnet-exposed.

## `docs/issues.md` is stale

The registry header says a 2026-06-13 pass "now fixed" many items, but the body still lists them
as 🔴 Open. Verified against code, these are **actually fixed** — do not re-touch:

- **CR-1 SSRF** — `services/search.py` `_assert_public_host` resolves the host and rejects
  private/loopback/link-local/reserved/multicast/unspecified + metadata IP; `fetch_page` uses
  `follow_redirects=False` and re-validates every redirect hop (`_MAX_REDIRECTS=5`).
- **CR-2 private→Langfuse** — `core/metrics.py` `record_metrics(..., record_content=...)`;
  chat/agent pass `not request.private`; private chats record metadata only.
- **CR-3/4/5 connection held / embeddings / Langfuse on path** — `routers/chat.py` does
  `await db.close()` before streaming and writes via a fresh `AsyncSessionLocal`;
  `store_exchange_memories` + `record_metrics` run via `core/background.spawn`.
- **CR-6 resolved provider** — both paths set `resolved_provider` from `client.base_url`.
- **CR-8 truncated signal** — agent `done` event carries `"truncated": bool`.
- **CR-9 `messages.index`** — `models/messages.py` `nullable=False` + `UniqueConstraint`;
  migration `a1f4e7c8d92b`.
- **CR-13 research idempotency** — `routers/research.py` dedups on
  `(user_id, query, status in (queued,running))`.
- **CR-12 embed dim** — `EMBED_DIM` config + length validation in `services/memory.py`.
- **HIGH-5 pool sizing** — `db.py` `pool_size=DB_POOL_SIZE(20)`, `max_overflow(10)`, `pool_pre_ping=True`.
- **MED-6 ChatMessage length** — `services/router.py` `content: str = Field(max_length=100_000)`.
- **MED-1 refresh rotation** — `routers/auth.py` `/auth/refresh` delete+insert + independent
  `expires_at` check.
- **HIGH-2/4/7/8, MED-4/5/8/9/10/11, DEV-1..12** — all confirmed fixed in code.

Items issues.md lists as open that are **genuinely still open** (carried into the plan below):
CR-7, CR-10, CR-11, CR-14, CR-15, CR-16, MED-2, MED-3, MED-7, HIGH-1, HIGH-3, HIGH-9,
CR-17/18/19/20/21/22/23.

## Issues

### Security

#### S1 — Backend won't boot without an OpenRouter key (breaks the primary use case) · HIGH
- **Where:** `core/config.py` `OPENROUTER_API_KEY = get_secret("openrouter_api_key")` ran at
  module import.
- **Why:** `get_secret` raises `RuntimeError` if neither `/run/secrets/openrouter_api_key` nor
  `$OPENROUTER_API_KEY` exists. README marks the key as *not required* and the local-only setup
  (LM Studio + ComfyUI over Tailscale) is the stated goal. The app could not start without a
  dummy key.
- **Status: DONE** — the key is now lazy and optional. `core/config.py` adds
  `get_secret_or_none(name)` (non-raising; returns `None` when absent) and
  `get_openrouter_api_key() -> str | None`; the eager module-level `get_secret(...)` call is
  gone. Every OpenRouter call site routes through `get_openrouter_api_key()` and guards on a
  non-None key: `services/router.py::get_openrouter_client`, `services/research.py::_pick_client`,
  `routers/models.py::list_openrouter_models`, `provider_router.py`, `provider_registry.py`,
  `memory_curation.py` — each degrades (returns `None`/502/skips) when the key is unset. The
  backend now boots with no OpenRouter key at all.

#### S2 — `/metrics` unauthenticated and the backend port is host-published · HIGH
- **Where:** `main.py:36` `Instrumentator().instrument(app).expose(app)` (no auth);
  `docker-compose.yml:19` `ports: - "2727:8000"`.
- **Why:** `/metrics` exposes model names, per-provider request/token volumes, conversation
  counts to anyone on the tailnet; the published port bypasses Caddy (so S5 also bites).
- **Fix:** remove `ports: 2727:8000` from `docker-compose.yml` (Caddy `:80` is the only
  ingress). Gate `/metrics` behind a `METRICS_TOKEN` env var + `Authorization: Bearer` check,
  or scrape `backend:8000/metrics` only on the internal Docker network.
- **Status: DONE** — `/metrics` is gated behind `METRICS_TOKEN` (empty token ⇒ 404, fail-closed;
  missing/wrong `Authorization: Bearer` header ⇒ 401) and the backend port is bound to
  `127.0.0.1` only. Prometheus authenticates via the mounted `monitoring/metrics_token`
  credentials file; `setup.ps1`/`setup.sh` generate/sync the token. See "Security fixes
  implemented (S2 + S3)" below.

#### S3 — ComfyUI `/view*` proxied unauthenticated · HIGH
- **Where:** `caddy/Caddyfile:19-21` `handle /view* { reverse_proxy host.docker.internal:8188 }`.
- **Why:** any tailnet user can fetch any ComfyUI output image by filename; ComfyUI's `/view`
  has a history of path-traversal in `subfolder`/`filename`.
- **Fix:** remove the open Caddy `/view*` proxy. Add a backend route
  `GET /v1/images/file?filename=&subfolder=&type=` that requires auth, validates ownership
  (extend the `imgjob:*` Redis map), and rejects `..`/absolute paths. Caddy proxies
  `/api/v1/images/file` only.
- **Status: DONE** — the open Caddy `/view*` block is removed; `GET /v1/images/file` requires
  a valid access token plus a Redis `imgfile:{filename}` ownership entry (set on job
  completion, TTL `IMAGE_FILE_TTL_SECONDS`), and `services/image_security.py` rejects
  `..`/path separators/bad chars. Status URLs are now relative `/v1/images/file?...`. See
  "Security fixes implemented (S2 + S3)" below.

#### S4 — Register endpoint allows email enumeration · MEDIUM
- **Where:** `routers/auth.py:64` `raise HTTPException(400, "user already exists")`.
- **Fix:** respond identically to a successful registration
  (`200 {"message":"user created successfully"}`) for both existing and new emails, so a
  register attempt can't reveal whether an address is taken.
- **Status: DONE** — `/auth/register` returns the same `200 {"message":"user created
  successfully"}` for existing and new emails, and the duplicate-email `IntegrityError`
  race still maps to the same body. See "Security fixes implemented (S4 + S5 + S7)" below.

#### S5 — Rate limiter trusts `X-Forwarded-For` unconditionally · MEDIUM
- **Where:** `middleware/ratelimit.py:90-92` `_client_ip` takes the first XFF hop always.
- **Fix:** only trust XFF when the direct peer is in a `TRUSTED_PROXIES` set
  (comma-separated CIDRs, default `172.16.0.0/12`); else use `request.client.host`.
- **Status: DONE** — `_client_ip` resolves through `core/trusted_proxies.py::resolve_client_ip`,
  which trusts the first `X-Forwarded-For` hop only when the direct peer falls inside
  `settings.trusted_proxy_networks`; an empty `TRUSTED_PROXIES` disables XFF trust entirely.
  See "Security fixes implemented (S4 + S5 + S7)" below.

#### S6 — MCP_SERVERS stdio spawns arbitrary commands · LOW (trust boundary)
- **Where:** `services/mcp_client.py:57-62`. Operator-configured via env (RCE-by-config, not
  remote). Document in `.env.example`; never let user input reach `MCP_SERVERS`.
- **Status: DONE** — README (env-var table row + "MCP trust boundary" note) and `.env.example`
  now warn that `MCP_SERVERS` is operator-configured, can spawn arbitrary commands (a stdio
  entry runs whatever `command` names), and must never be derived from user input.

#### S7 — Open registration on a tailnet-exposed app · LOW
- **Where:** `routers/auth.py` `/auth/register` is open.
- **Fix:** add `REGISTRATION_ENABLED` bool (default true); 403 when false.
- **Status: DONE** — `REGISTRATION_ENABLED` (default true) gates `/auth/register` with
  `403 {"detail":"registration disabled"}` when false. See "Security fixes implemented
  (S4 + S5 + S7)" below.

### Reliability / correctness

#### R1 — Agent loop holds the DB connection for the whole multi-round run · HIGH
- **Where:** `services/agent.py::run_agent` — the request's `db` is used for `load_history`,
  every tool execution, and `save_messages`. No `db.close()` before the loop (unlike chat).
- **Fix:** mirror the chat path: load history up front, `await db.close()`, run the loop holding
  no connection; give each tool its own short-lived `AsyncSessionLocal`; fresh session for
  `save_messages`.
- **Status: DONE** — `run_agent` closes the request session after the up-front reads and before
  the loop; each tool runs on a short-lived `AsyncSessionLocal`; `save_messages` uses its own
  fresh session. See "Reliability fixes implemented (R1 + R2)" below.

#### R2 — Agent `messages` array grows unbounded → context overflow mid-loop · HIGH (CR-7)
- **Where:** `services/agent.py` — tool results (8k each) appended every round; only
  `AGENT_TOKEN_BUDGET` gates tool offering, not array size.
- **Fix:** track running prompt tokens; when `> 0.6 * AGENT_TOKEN_BUDGET`, drop oldest
  tool-call/tool-result pairs. Catch `APIError` context-length indicators → degrade to a
  tool-less final synthesis round with `truncated=True`.
- **Status: DONE** — messages are pruned to `0.6 × AGENT_TOKEN_BUDGET` before every tool round
  (oldest assistant-with-tool_calls + its tool results go first; the system/user prefix is
  never touched) and a context-length `APIError` mid-loop degrades to a tool-less final answer
  with `truncated=True`. See "Reliability fixes implemented (R1 + R2)" below.

#### R3 — Local token counts are fiction · MEDIUM (CR-10)
- **Where:** `routers/chat.py:155-160` — local path omits `stream_options`, so `prompt_tok=0`,
  `completion_tok=chunk count`.
- **Fix:** after the local stream, call LM Studio `/v1/tokenize/encode` for the prompt (off the
  response path via `spawn`). Store `token_provenance` (`exact`|`chunk_count`|`null`) on
  `messages`.
- **Status: DONE** — `messages.token_provenance` (`exact` | `chunk_count` | null, migration
  `f3a9c1d5e7b2`) tags how `tokens_used` was derived; the chat path computes it from whether
  the provider reported usage and spawns `services/tokenize.py::sync_local_token_counts` on
  the local path to overwrite the last exchange with exact LM Studio counts; the agent path
  tags the same provenance from provider usage. See "Reliability fixes implemented (R3–R6)"
  below.

#### R4 — Deep research silently uses the SDXL rewrite model · MEDIUM (CR-14)
- **Where:** `services/research.py::_pick_client` — falls back to `LM_DEFAULT_MODEL` when no
  OpenRouter and no `LM_CHAT_MODEL`. `job.model` stored as `"auto"`.
- **Fix:** require a capable chat model: reject at submit (503) if none configured. Resolve the
  model in the router and store it on `job.model`.
- **Status: DONE** — `services/research.py::resolve_research_model` computes the `(role,
  model)` pair research will run on (mirroring `_pick_provider` without constructing
  providers); `routers/research.py` stores the resolved pair on the job and returns 503
  "no capable chat model configured for research" when nothing resolves. See "Reliability
  fixes implemented (R3–R6)" below.

#### R5 — ComfyUI node anchor matched by substring · MEDIUM (CR-15)
- **Where:** `services/comfy.py::_find_node` — `if class_substr in node.get("class_type","")`.
- **Fix:** for critical anchors (`KSampler`, `ResolutionSelector`, `*LatentImage`), refuse to
  guess if >1 match; require `param_map` for ambiguous anchors. Error at workflow upload.
- **Status: DONE** — `validate_workflow_anchors` rejects ambiguous workflows at upload/update
  (422) unless `param_map` pins the intended node; `inject_params` auto-detects with exact
  matching and skips (never guesses) when an anchor is missing or ambiguous. See
  "Reliability fixes implemented (R3–R6)" below.

#### R6 — DuckDuckGo scraper fails silently to empty · MEDIUM (CR-16)
- **Where:** `services/search.py::_duckduckgo` — 200 with no matches returns `[]` silently.
- **Fix:** on 200 + empty `titles`, log a warning + emit a `search_degraded_total` counter.
  Change the tool's empty-result text to "degraded; answer from prior knowledge."
- **Status: DONE** — `_duckduckgo` and `_searxng` log a warning and increment
  `search_degraded_total{source="duckduckgo"|"searxng"}` on a 200 with zero results; the
  `web_search` tool returns "Search returned no results (degraded); answer from prior
  knowledge." See "Reliability fixes implemented (R3–R6)" below.

#### R7 — No background sweep of expired refresh tokens · LOW (MED-2)
- **Fix:** arq cron job hourly: `DELETE FROM refresh_tokens WHERE expires_at < now()`.
- **Status: DONE** — `worker.py` schedules `sweep_expired_tokens` via arq `cron_jobs`
  (`cron(sweep_expired_tokens, minute=0)`, hourly at minute 0); the job deletes
  `refresh_tokens` rows whose `expires_at` is in the past on a fresh `AsyncSessionLocal`
  session. Worker restart required to pick up cron changes (arq does not hot-reload). See
  "Data integrity fixes implemented (D4 + R7)" below.

### Data integrity (minor)

- **D1 — non-owner returns 403, not 404 (enumeration).** `routers/convo.py` — route through
  `_get_owned_conversation` (which 404s).
  - **Status: DONE** — `messages_get`/`convo_rename`/`convo_delete` now all route through
    `_get_owned_conversation`, keeping the ownership invariant: 404 when the conversation is
    missing, 403 when it belongs to someone else (UUIDs are unguessable, so there is no
    enumeration leak).
- **D2 — `/openrouter/models` no timeout + `Bearer None`.** Add `timeout=10`; 503 when key
  unset.
  - **Status: DONE** — `httpx.AsyncClient(timeout=10)` added; the 503-when-key-unset guard is
    unchanged.
- **D3 — Memories migration can't downgrade.** `alembic/versions/4b602081a1e1` `downgrade()` is
  `pass` → `op.execute('DROP TABLE IF EXISTS memories CASCADE')`.
  - **Status: DONE** — downgrade drops the `memories` table
    (`DROP TABLE IF EXISTS memories CASCADE`), mirroring the upgrade's FK to
    `conversations.id` with `ondelete='CASCADE'`. Verified in `4b602081a1e1_add_memories_table.py`.
- **D4 — No pagination on list endpoints.** Add `limit`/`offset` to `GET /convo`, `/presets`,
  `/templates`.
  - **Status: DONE** — `GET /v1/convo`, `/v1/presets`, `/v1/templates` accept optional
    `limit` (1–200) and `offset` (≥0) query params; when `limit` is set the `select` gets
    `.limit(limit).offset(offset)` applied before execution (existing `order_by` preserved).
    `limit=None` keeps the previous no-limit behavior; response shapes unchanged. See
    "Data integrity fixes implemented (D4 + R7)" below.

### Maintainability

- **M1 — Dependencies unpinned (MED-7).** `pip-compile` and pin all versions.
  - **Status: DONE** — `requirements.in` (editable source) → pip-compile → pinned
    `requirements.txt` with the standard pip-compile header. The lock matches the running
    container's installed versions exactly (0 version diffs across all 80 app
    dependencies; verified via `pip freeze` comparison, `pip check`, the 88-test suite,
    and `import app.main`). See "Maintainability fixes implemented (M1 + M4)" below.
- **M2 — Stale Gemini references.** `.env.example` + README list `GEMINI_*` but no Gemini
  provider exists. Remove.
  - **Status: DONE** — `GEMINI_MODEL`/`GEMINI_API_KEY` removed from `.env.example`; a
    case-insensitive repo grep finds no `GEMINI_*` env references. The `google` BYO-key
    provider type (README "Adding providers") is a real adapter and is kept.
- **M3 — Hardcoded Postgres user/DB (HIGH-1).** `${POSTGRES_USER:?}` / `${POSTGRES_DB:?}` from
  `.env`.
  - **Status: DONE** — docker-compose reads `POSTGRES_USER`/`POSTGRES_DB` from `.env`
    (required); `setup.ps1`/`setup.sh` add the defaults (`ishaab`/`llmgateway`) when missing
    without overwriting existing values; README env-var table documents both.
- **M4 — No CI schema-drift guard (CR-22).** CI: `alembic upgrade head` on scratch DB, boot app,
  assert empty autogenerate diff.
  - **Status: DONE** — `backend/scripts/ci-schema-drift.sh` (migrate a scratch DB to head,
    then `alembic check`, with an autogenerate-and-grep fallback for alembic < 1.9) +
    `.github/workflows/schema-drift.yml` (push + PR: pgvector postgres service, install
    pinned deps, run the guard, run the unit tests). Verified locally on a scratch DB —
    the guard works and immediately uncovered a PRE-EXISTING drift, see the note in
    "Maintainability fixes implemented (M1 + M4)" below.

### Latent / by-design (no action now)
- **CR-17** orphaned conversation rows (hidden by `EXISTS` filter on `GET /v1/convo`).
- **CR-18** `get_redis()`/`get_queue()` lazy-init no lock (masked by lifespan warmup).
- **CR-19** rate limiter is `BaseHTTPMiddleware` (works today; never touches the body).
- **CR-20/21** no vector index / no `messages(conversation_id,index)` composite (premature at
  current scale).
- Image URLs from `comfy.get_job_status` are now relative `/v1/images/file?...` (authed, S3) —
  the SPA uses the returned `url` directly; no `/view` rewrite needed.

## Fix Plan (ordered)

### Phase 1 — Boot safely on the tailnet (security)
1. **S1** — optional OpenRouter key in `core/config.py` + guard all OpenRouter call sites.
2. **S2** — stop publishing the backend port + gate `/metrics`.
3. **S5** — trust XFF only from `TRUSTED_PROXIES`.
4. **S3** — authed ComfyUI file route; remove open `/view*` proxy.
5. **S4** — neutralize register enumeration message.
6. **S7** — `REGISTRATION_ENABLED` flag.

### Phase 2 — Reliability under load
7. **R1** — release the DB connection in the agent loop.
8. **R2** — bound the agent message array + context-overflow degradation.
9. **R4** — honest research model (reject at submit if none capable; store resolved model).
10. **R5** — strict ComfyUI anchors (refuse ambiguous; require `param_map`).
11. **R6** — surface DDG degradation (warn + metric).
12. **R3** — honest local token counts (`token_provenance` column + tokenize call).

### Phase 3 — Data integrity
13. **D1** — route conversation ownership through `_get_owned_conversation` (404 missing / 403 foreign — UUIDs unguessable, no enumeration leak).
14. **D4** — pagination on list endpoints.
15. **R7** — expired-token sweep (arq cron).
16. **D3** — memories migration downgrade.

### Phase 4 — Maintainability
17. **M1** — pin all dependencies.
18. **M2** — remove stale Gemini references.
19. **M3** — parameterize Postgres user/DB.
20. **M4** — CI schema-drift guard.

## Verification

- **Phase 1:** `docker compose up --build` with no `OPENROUTER_API_KEY` → backend starts,
  `/health` returns 200. From the tailnet: `/metrics` → 401/404; `:2727/metrics` → refused.
  Spoofed-IP rate-limit check: 15 logins with forged XFF from one real IP → 429 after 10.
- **Phase 2:** `POST /v1/agent/chat` with 4+ tool calls → completes without context error,
  `done.truncated` accurate; `pg_stat_activity` shows no idle connection held for the run.
  Research with no capable model → 503. ComfyUI workflow with `KSampler` + `KSamplerAdvanced`
  and no `param_map` → upload rejected.
- **Phase 3:** `alembic downgrade -1` from the memories migration succeeds. `GET /convo?limit=10`
  paginates.
- **Phase 4:** `pip freeze` matches pinned `requirements.txt`.

## Critical files
- `backend/app/core/config.py` — `get_openrouter_api_key()` / `get_secret_or_none()` (S1, DONE:
  the key is lazy and optional, no module-import fetch).
- `backend/app/core/trusted_proxies.py` — stdlib `resolve_client_ip` (S5): trusts
  `X-Forwarded-For` only from peers inside `trusted_proxy_networks`.
- `backend/app/services/agent.py::run_agent` — closes the request session before the loop (R1)
  and prunes/degrades on context overflow (R2) — both fixed, see "Reliability fixes
  implemented (R1 + R2)".
- `backend/app/services/tokenize.py::sync_local_token_counts` — off-path exact token counts
  for local exchanges via LM Studio `/v1/tokenize/encode` (R3); `messages.token_provenance`
  records how counts were derived (migration `f3a9c1d5e7b2`).
- `backend/app/services/research.py::resolve_research_model` — resolves the research
  (role, model) at submit; `routers/research.py` stores it on the job and 503s when no
  capable model exists (R4).
- `backend/app/services/comfy.py::validate_workflow_anchors` + `_find_node_exact` — ambiguous
  workflows rejected at upload, auto-injection never guesses (R5).
- `backend/app/services/search.py` — 200-with-zero-results logs a warning and increments
  `search_degraded_total` (R6).
- `backend/app/middleware/ratelimit.py::_client_ip` — XFF trusted only when the direct peer
  is in `settings.trusted_proxy_networks` (`TRUSTED_PROXIES`, default `172.16.0.0/12`) (S5).
- `backend/app/routers/auth.py` — identical register responses for existing/new emails (S4);
  `REGISTRATION_ENABLED` gate → `403 {"detail":"registration disabled"}` (S7).
- `backend/app/main.py:66` — `/metrics` gated behind `METRICS_TOKEN` (exact `Authorization:
  Bearer` header; empty token ⇒ 404 fail-closed) (S2).
- `docker-compose.yml:22` — backend port bound to `127.0.0.1` only (S2).
- `caddy/Caddyfile` — open `/view*` proxy removed; ComfyUI files served via authed
  `GET /v1/images/file` (S3).

## Assumptions
- **Single-user, personal, tailnet-only.** TLS is terminated by Tailscale; Caddy stays plain
  HTTP on :80 by design. If exposed beyond the tailnet, S2/S3 become release blockers and real
  TLS must be added at Caddy.
- **OpenRouter is optional** (per README + the local-only goal). S1 makes this true in code.
- **S4/S7 landed with the Phase 1 security pass** (see "Security fixes implemented (S4 + S5 +
  S7)"): register responses are identical for existing/new emails, and `REGISTRATION_ENABLED`
  defaults true (no behavior change until the operator flips it).
- **R3 adds one extra local HTTP call after each answer** (off the response path via `spawn`).
  Fallback: only add `token_provenance` and mark local counts `chunk_count` — honest, no extra
  call.
- **S3 changes frontend image rendering** (rewrite to `/api/v1/images/file`). Backend fix is in
  scope here; the frontend change is noted in the frontend roadmap.

## Phase 5 — BYO-key providers (in progress)

Users can now register their own model providers (their own API keys) instead of being
hard-wired to the env-configured LM Studio / OpenRouter pair. The routing engine
(`services/router.py`) and chat wiring are untouched in this phase — the provider
configuration surface and adapters are built and ready for a later phase to consume.

Implemented:

- **`providers` table** (`models/providers.py`, migration `b8e4f1a2c9d3`) — user-owned rows with
  name, type (`openai_compatible | openai | anthropic | google | openrouter`), role
  (`local | cloud`), optional base_url, `api_key_encrypted` (Fernet, never plaintext),
  `default_model`, `is_default`, `enabled`, `created_at`, and a unique `(user_id, name)`.
  FK to users is `ON DELETE CASCADE`.
- **Encrypted key storage** (`core/crypto.py`) — Fernet; key comes from `KEY_ENCRYPTION_KEY`
  (32 urlsafe base64 bytes) when set, else derived from `SECRET_KEY` via SHA-256 so existing
  deployments need no new env var. `encrypt_secret`/`decrypt_secret`; empty input and
  `InvalidToken` (rotated keys) raise `ValueError`. No plaintext keys are ever logged.
- **Adapter protocol** (`services/providers/`) — `LLMProvider` base with
  `stream_chat` (async iterator of `StreamChunk`) and `complete` (non-streamed). Concrete
  adapters: `openai_compat` (LM Studio/Ollama/Groq-style endpoints; `extra_body` for non-spec
  sampling, no `stream_options` to preserve LM Studio behavior), `openai`, `openrouter`
  (`stream_options={"include_usage": True}`, reads the final usage chunk), `anthropic` and
  `google` (SDKs lazy-imported; unavailable adapters raise `RuntimeError` on use).
- **Provider registry** (`services/provider_registry.py`) — `row_to_provider` (decrypts the
  key, dispatches to the right adapter), `list_providers`, `get_default_provider` (default
  flag first, else oldest enabled), `resolve_provider` (by id with 404/403 ownership checks,
  or by role default; 503 `NoProviderError` when a role has nothing configured),
  `test_provider` (one-token round-trip), `mask_key`.
- **CRUD router** (`routers/providers.py`, mounted at `/v1/providers`) — GET (seeds
  backward-compat rows first, then lists with `api_key_masked`), POST/PATCH (encrypts keys,
  keeps exactly one `is_default` per role), DELETE, and POST `/{id}/test`.
- **Backward-compat seeding** — `seed_default_providers` creates "Local (LM Studio)" from
  `LM_URL`/`LM_CHAT_MODEL` and "OpenRouter" from the (optional, S1) OpenRouter key on first
  list, so existing env-var behavior is preserved and keys migrate into the encrypted store.
- `KEY_ENCRYPTION_KEY` added to `core/config.py`; `cryptography` added to requirements
  (previously only transitive). `alembic/env.py` and `worker.py` import the new model.

Not in this phase (later): routing against these providers in `services/router.py`, chat/
agent wiring, per-provider param translation beyond what the adapters do.

## Security fixes implemented (S2 + S3)

The two tailnet-exposure fixes from Phase 1, landed together.

- **S2 — `/metrics` gated + backend port loopback-only.**
  - `app/main.py` no longer calls `Instrumentator().expose(app)`; a hand-written
    `GET /metrics` route returns **404** when `METRICS_TOKEN` is empty (fail-closed) and
    **401** without the exact `Authorization: Bearer <token>` header. The comparison lives
    in a pure module-level helper `_metrics_authorized` (unit-tested).
  - `docker-compose.yml` binds the backend to `127.0.0.1:2727:8000` — the tailnet reaches
    the API only through Caddy on `:80`; dev/docs stay available on the host.
  - Prometheus scrapes `backend:8000/metrics` with `authorization.type: Bearer` +
    `credentials_file: /etc/prometheus/metrics_token` (mounted from
    `monitoring/metrics_token`, gitignored).
  - `setup.ps1`/`setup.sh` generate `METRICS_TOKEN` in `.env` when missing (idempotent) and
    mirror the exact token into `monitoring/metrics_token` (no trailing whitespace — the
    backend compares the header string byte-for-byte).
- **S3 — authed image file endpoint, Caddy `/view*` proxy removed.**
  - `caddy/Caddyfile`: the `handle /view* { reverse_proxy host.docker.internal:8188 }` block
    is deleted; only `/api/*` → backend and the SPA remain.
  - New `GET /v1/images/file?filename=&subfolder=&type=` in `routers/images.py`: requires a
    valid access token, checks Redis `imgfile:{filename}` ownership (404 when absent/not
    yours), validates the reference via the new stdlib-only `services/image_security.py`
    (400 on violation), then proxies the file from ComfyUI's `/view` (502 on backend
    failure/non-200).
  - Ownership entries are written when a job completes: `job_status` sets
    `imgfile:{filename}` → user id with TTL `IMAGE_FILE_TTL_SECONDS` (default 7 days). The
    existing `imgjob:*` check is unchanged.
  - `comfy.get_job_status` now returns relative `/v1/images/file?...` URLs (built with
    `urlencode`); the JSON shape (`status`, `images[].filename`) is unchanged.

## Security fixes implemented (S4 + S5 + S7)

The tailnet-hardening follow-ups from Phase 1, landed together with the S2/S3 work.

- **S4 — no register enumeration.**
  - `app/routers/auth.py::register_user` returns the same
    `200 {"message":"user created successfully"}` whether the email already exists or was
    just created — the pre-insert `select` no longer raises a distinguishing 400, and the
    `IntegrityError` race path (two concurrent registers for the same address) returns the
    same body too. The SPA therefore cannot tell registered from unregistered addresses by
    response code or body.
- **S5 — rate limiter honors `X-Forwarded-For` only from trusted proxies.**
  - `app/middleware/ratelimit.py::_client_ip` now delegates to the stdlib-only helper
    `app/core/trusted_proxies.py::resolve_client_ip(peer, forwarded, trusted_networks)`:
    - peer is falsy or unparseable → `"unknown"` (an attacker can't forge a bucket key);
    - peer falls inside one of the configured `trusted_networks` **and** an
      `X-Forwarded-For` header is present → the first hop (the original client the trusted
      proxy saw), whitespace-stripped;
    - otherwise → the direct peer IP as-is, XFF ignored.
  - `TRUSTED_PROXIES` (comma-separated CIDRs, default `172.16.0.0/12` — the Docker bridge
    Caddy sits on) is parsed once per check in `core/config.py::trusted_proxy_networks`;
    invalid entries are skipped with a warning and an **empty** list means no proxy is
    trusted at all, so XFF is ignored everywhere.
  - New unit tests: `backend/tests/test_trusted_proxies.py` (11 cases: trusted/untrusted
    peers, missing/malformed peer, CIDR membership, empty trusted list).
- **S7 — `REGISTRATION_ENABLED` signup gate.**
  - `app/routers/auth.py::register_user` checks `settings.REGISTRATION_ENABLED` first and
    raises `403 {"detail":"registration disabled"}` when false. `core/config.py` adds the
    flag with default `true` (no behavior change out of the box).
  - Lock-down recipe: create your account, set `REGISTRATION_ENABLED=false` in `.env`,
    restart the backend (see README "Locking down signups").
- **Setup scripts** (`setup.ps1` Step 2c / `setup.sh` security-defaults block) add
  `TRUSTED_PROXIES=172.16.0.0/12` and `REGISTRATION_ENABLED=true` to `.env` only when
  missing — never overwriting a value the operator set deliberately. Both are idempotent:
  re-running changes nothing when the keys already exist.

## Security sweep fixes implemented (sandbox + tools, 2026-08-28)

Two-audit sweep of the agent's host-impact surface. Fixed in this commit; OPEN items listed after.

- **C1/F2 (was CRITICAL): sandbox inherited `.env`** — removed `env_file: .env` from the sandbox service. `SECRET_KEY` in a model-controlled root shell meant forge-any-JWT + Fernet-derive the provider-key encryption key (`core/crypto.py:38`); DB creds rode along. The sandbox needs no secrets.
- **H3 (was HIGH): `/exec` had zero auth** — shared-secret required (`X-Sandbox-Token` vs `SANDBOX_SHARED_SECRET`, hmac compare, fail-closed when unset); backend `HttpSandbox` sends it; compose injects the same value into both containers.
- **H4 (was CRITICAL F3): planted git hooks executed in the backend** — verified live (hook fired on raw git, not on hardened git). Store's git calls now route through `_git()` with `-c core.hooksPath=/dev/null -c core.fsmonitor=false -c credential.helper=`. Regression test with a raw-git control.
- **M1: sandbox container hardening** — `read_only`, `cap_drop: ALL`, `no-new-privileges`, `mem_limit: 512m`, `pids_limit: 128`, `/tmp` tmpfs.
- **H2 note-honesty:** compose no longer claims a nonexistent egress allowlist (`SANDBOX_ALLOWLIST` remains dead config — see OPEN).
- **F10/F11/F12/F13 small fixes:** negative_prompt cap; generic tool-error strings (no internal-topology leak); `tool_call_id` populated for file-edits audit; `write_file` expected_hashes exact-prefix semantics.
- Sweep-verified clean: no docker.sock/host binds/privileged anywhere; backend never runs model-supplied shell; registry dispatch has no bypass; `calculate`/`search_conversations`/patch-argv bounded and safe.

### OPEN (not fixed)

_None._ All priority security items from the sweep are closed (F1/C2, F5, F6, F7, F8, F9, F11). The sole remaining gap is a **domain-level** egress allowlist (sandbox can still reach any public internet host via NAT) — deliberately deferred as a defense-in-depth item; segmentation now keeps it off your internal services and host.

## Sandbox egress isolation (2026-08-29)

The sandbox ran on the default Docker bridge, so a model-driven `bash` could reach every internal service (postgres, redis, backend, worker, searxng) and the host network via `host.docker.internal`.

- **`docker-compose.yml`:** added a `sandboxnet` user-defined network; the **sandbox sits on it alone** (its only compose peer is the backend for `/exec`), with **no route** to postgres/redis/backend-internals/worker/searxng. The backend joins `[default, sandboxnet]`. A user-defined bridge still gives outbound NAT, so pip/npm/git keep working.
- **Removed `host.docker.internal` from the sandbox** (never needed it; exposes the host). Backend keeps its host-gateway.
- **Scope:** segmentation, not a domain allowlist (that needs an egress proxy). `SANDBOX_ALLOWLIST` stays aspirational dead config.
- Live checks: `docker compose exec sandbox bash -lc 'curl --max-time 3 http://postgres:5432'` must fail while `pip install` succeeds. Confirmed by inspection only (no docker on the host).

## Per-tenant workspace confinement (F1/C2 closed, 2026-08-29)

The last CRITICAL is now enforced, not promised. `bash` runs as a distinct OS UID in a
chmod-700 workspace, so it cannot read/write another tenant's workspace on the shared
volume.

- **A deep `sandbox/uid_alloc.py` module** owns per-tenant UID allocation with
  **owner-as-registry**: the workspace directory's `st_uid` IS the record. No side table,
  collision-free (each UID handed out once), self-healing. Range 10000–65000 (clear of
  system accounts and `nobody`).
- **`sandbox/app.py`** allocates the UID on first exec (`chown -R (:uid:gid)`, `chmod 700`
  the top dir), then spawns `bash` via `Popen(user=<uid>, group=<uid>, start_new_session)`.
  The kernel clears ALL of the child's capabilities when it drops to a nonzero UID, so the
  bash child is fully unprivileged. `HOME` is set to the workspace so pip/npm/git work;
  `TMPDIR` points at the tmpfs. The controller (root) keeps the process-group timeout kill.
- **Compose change (deliberate):** the sandbox controller uses a **minimal cap allow-list**
  (`SETUID`, `SETGID`, `CHOWN`, `FOWNER`, `DAC_OVERRIDE`, `KILL`) instead of `cap_drop:
  ALL` — `chown` and `setuid` need those caps even as root. Everything else stays dropped,
  `no-new-privileges`/`read_only` remain; the bash child is still capability-less.
- **Backend prod Dockerfile:** dropped `USER appuser` — the trusted backend must be root to
  write any tenant's 700 workspace (a non-root backend UID couldn't traverse them).
- **Limitation:** verified by inspection + offline unit tests only (no docker on the host).
  The allocator's pure logic (`test_uid_alloc.py`, 6 cases) runs offline; the chown/setuid
  path runs only in the Linux sandbox container. A live `docker compose up` should be run
  before flipping `ENABLE_CODE_EXECUTION=true`.

## Workspace disk quota enforced for bash (F9 closed, 2026-08-28)

- **`.git` counts toward the quota now** — `store.du_mb` previously skipped `.git`, so `git clone`/`dd` via bash could outgrow the quota (the file-tool `_check_quota` was the only gate). Added a public `store.quota_mb()` accessor.
- **The `bash` tool enforces the same quota after every exec** (lock held) — over-quota returns a structured `exit_code: 413` "workspace quota exceeded" result.
- Tests: `test_workspace_store.py::DuQuotaTests`; `test_file_tools.py::BashToolTests::test_quota_exceeded_after_exec_returns_413`.

## Workspace symlink-swap hardening (F8 closed, 2026-08-28)

- **Reads/writes now use `O_NOFOLLOW` on the final component** (`_read_text_fp`/`_write_text_fp`). `_resolveInside` already rejected symlink *escape*; this closes the TOCTOU where `bash` swaps a file for a symlink between the resolve check and the open. POSIX-only guard (degrades to plain open on Windows); the compose volume is Linux so it's effective in production. Applied to `read_file`, `write_file`, `apply_patch`, `edit_lines`. Tests: `test_workspace_store.py::NofollowIoTests`.

## Sandbox timeout hardening (2026-08-28)

- **`sandbox/app.py::exec_cmd` now kills the whole process group on timeout.** Ran bash with `start_new_session=True`; on `TimeoutExpired` it `os.killpg(proc.pid, 9)`s the group so detached grandchildren (`sleep 1000 &`) don't survive the parent's death. Previously `subprocess.run(timeout=30)` raised `TimeoutExpired` (→ 500) and orphaned grandchildren that kept consuming quota/CPU. A `Popen` launch failure now returns a bounded error string rather than a 500. Verified by inspection only (sandbox is a separate Linux container; no docker on this host).

## Agent adapter de-duplication (D4, 2026-08-28)

- **Deleted a dead, divergent `_execute_tool` in `services/agent/agent.py`.** The runtime owns the live loop and the single `_execute_tool` (which carries the F11 fix — generic error strings, no internal-topology leak). The adapter kept a second copy that was never invoked but still echoed exception text (`"failed: {e}"`) — the exact no-locality bug the architecture review flagged (the F11 fix landed in one of two copies). Now there is one implementation, in `runtime.py`, and the tests import it from there and assert the generic message. Removed the adapter's now-unused imports.

## Memory injection policy implemented (F7 closed, 2026-08-28)

- **F7 (HIGH, integrity) closed at the tool gate.** The four mutating memory tools (`memory_write`, `memory_str_replace`, `memory_append`, `memory_delete`) flipped to deny-by-default (`first_party=False`), matching `bash` and the file tools. They were the only default-allowed mutators in the codebase — a fetched web page could say "write X to `/profile.md`" and the content would be injected verbatim into every future system prompt via tier-1.5. Now the model cannot write memory without an explicit user grant (`PUT /v1/agent/tools/{name}/permission`). `memory_read` stays allowed (reads don't persist injection); `memory_curation` calls the `memory_files` primitives directly and is unaffected.
- **Read-side defense-in-depth:** `build_memory_context` caps each tier-1.5 file at `MEMORY_TIER1_5_INJECT_CAP` (2000) bytes and wraps it in `<memory_file ...>`/`</memory_file>` delimiters (was `--- path ---`), so injected content can't be mistaken for prompt structure.
- Tests: `test_agent_adapter.py::MemoryToolDefaultDenyTests` (offline via `import_with_stubs`); `test_memory_files.py` tier-1.5 delimiter + cap.

## Egress seam implemented (F5/F6 closed, 2026-08-28)

New deep module `services/egress.py` is now the ONLY way the backend makes an outbound HTTP request. It owns the SSRF guard and byte cap, so a future caller cannot skip them:

- **F5 (MED-HIGH): DNS-rebinding TOCTOU closed** — `fetch_page` resolved the host, validated it, then re-resolved at connect time. Egress resolves ONCE, validates, and **pins the connect to that validated IP** while preserving the original hostname for the Host header and TLS SNI (verified for both HTTP and HTTPS: TLS cert + SNI survive). A DNS answer that changes between resolve and connect can no longer funnel a public URL into the internal network.
- **F6 (MEDIUM): response-buffer OOM closed** — `fetch_page` buffered the whole body before truncating; a gzip-bomb could OOM the worker. Egress streams the body through a byte cap (decoded length, so `Content-Length` lies are caught too) and aborts past the cap.
- **Two policy tiers (the seam is real, >1 adapter):** `INTERNET` (public-only, default — used by `fetch_page`, DuckDuckGo), `INTERNAL` (no SSRF check for configured internal endpoints like SearXNG), and `PRIVATE_ALLOWED` (public check but permits private/loopback for locked-down deployments pointing SearXNG internal).
- **`search.py` now delegates both its backends and `fetch_page` to egress** — the local `_assert_public_host`/resolve/validate copy is deleted. Egress raises `EgressError` (generic message, F11-consistent: no internal IPs/hostnames leak to the model); `fetch_page` maps refusals to `""` and callers (research) fall back to a snippet.
- **Tests:** `tests/test_egress.py` (12 cases) proving SSRF refusal (private/loopback/link-local-metadata), pin-connect to the validated IP with Host-header preservation, byte-cap abort + under-cap pass, redirect re-validation to a hostile internal host, both policy tiers, and search-backend routing (GET+params / POST+form).

## Security/correctness fixes implemented (workspace store)

- **`apply_patch` diff-header validation (patch-body trust boundary).** The `path` *argument*
  was always validated through `_resolveInside`, but the diff *body's* own `---`/`+++` header
  paths are what `patch -p1` actually opens (cwd=wp) — they went to the subprocess unvalidated.
  New pure helpers `_patch_header_paths` + `_validate_patch_targets` extract every header target
  (handling git `a/`/`b/` prefixes, timestamp suffixes, `/dev/null` add/remove hunks), strip the
  `-p1` level, and run each through the same `_resolveInside` rules before the subprocess runs.
  This closes the direct `../` escape on builds whose `patch` doesn't refuse it, and the symlink
  variant (a symlink planted inside the workspace by another tool pointing outside). Degenerate
  `--- a/` headers are skipped (an empty filename makes `patch` fail later anyway) but never
  bypass validation of the sibling headers. Regression tests in `tests/test_file_tools.py` run
  offline (the 422 fires before the subprocess).

- **`AppError(status_code=...)` now works.** `services/workspace/store.py` raised
  `AppError(status_code=422, detail=...)` in 27 places (the single path-security seam from
  ADR-0002/0003), but `core/exceptions.py::AppError.__init__` only accepted `detail` — so
  every rejection (bad `..` segment, absolute path, control char, symlink escape, quota,
  hash mismatch) raised `TypeError` at the boundary, which mapped to a **500** instead of the
  intended 422/404/413/409. `AppError` now takes an optional `status_code` keyword
  (backward compatible: positional `detail` and subclass `status_code` both still work).
- **Workspace store now has tests.** New `backend/tests/test_workspace_store.py` (15 cases)
  covers `_resolveInside` (the 422 seam: absolute / `..` / control-char / escape rejection,
  valid-relative + `.` acceptance), `_line_hash`/`_file_hashes`, and the write → git-commit →
  `file_edits` audit → undo-by-`commit_sha` pipeline. Tests are offline (stub `app.db` only
  during import, then restore it) so sibling test modules are unaffected.
- **Test hygiene:** `test_agents.py` referenced the pre-refactor `_validate_rel_path` (now
  `_resolveInside`); the stale import would re-raise instead of skipping on envs without
  asyncpg. Updated to the current symbol.

## Design-practice cleanup (ProviderRouter single entry point)

- Removed the legacy `services/router.py::get_provider` shim — a 10-line pass-through with
  exactly one caller (`services/agent/agent.py`). `routers/chat.py` and `routers/agents.py`
  already called `ProviderRouter().resolve()` directly, so `agent.py` was the lone straggler.
  Now `ProviderRouter` is the single entry point for provider resolution.
- Removed the dead duplicate `services/router.py::resolve_role` (zero callers). The pure
  keyword heuristic lives only in `services/provider_router.py::resolve_role`; `router.py`
  keeps the `Provider`/`ChatRequest`/`ChatMessage` facts and the client factories.
- `test_routing.py` now tests `ProviderRouter.resolve` + `provider_router.resolve_role`
  directly instead of mocking the removed legacy shim. Backward compatible — the agent chat
  and plain chat request shapes are unchanged, so the API contract is untouched.

## Design-practice cleanup (agent-loop helpers one home)

- The agent-loop helpers (`_estimate_tokens`, `_is_context_error`, `_prune_old_tool_rounds`)
  and `_MEMORY_WRITE_TOOLS` were duplicated byte-for-byte between `services/agent/agent.py`
  (the adapter) and `services/agent/runtime.py` (the module that owns the loop). This was the
  highest-value bug: `test_agent.py` tested the *adapter's* copies while the live loop ran the
  *runtime's* copies, so the shipped loop was untested.
- Deleted the duplicates from `agent.py`; the helpers now live once in `runtime.py` and are
  re-exported by `services/agent/__init__.py` (so `from app.services.agent import …` still
  works). `test_agent.py` now imports from `app.services.agent.runtime` (the live module) and
  a new `test_agent_runtime.py` covers them.
- Also corrected the `runtime.py` module docstring: it previously claimed the runtime "never
  imports AsyncSession", but it opens its own `AsyncSessionLocal` per tool call and for the
  final save (R1). The docstring now states no `AsyncSession` handle crosses the seam.

## Design-practice cleanup (one agent-ownership helper)

- `routers/agents.py` repeated the agent-ownership + install-eligibility query (select Agent →
  404 missing → 403 not-owner) inline in six routes. Added two module-level helpers so the
  ownership policy lives once and matches the repo's 404-vs-403 convention:
  - `_get_owned_agent(db, agent_id, user_id)` — owner-only lookup (404 missing, 403 not-owner);
    used by `update_agent`, `delete_agent`, `publish_agent`.
  - `_get_workspace_agent(db, agent_id, user_id)` — allows the owner, an installer, or a public
    agent; used by the four workspace routes (files / file / edits / undo). The
    installer-eligibility check mirrors `_resolve_agent` in `services/agent/agent.py`.
  - `_load_agent(db, agent_id)` — a bare fetch without any ownership check.

## Design-practice cleanup (Smart Suggest extracted to a service)

- The Smart Suggest LLM orchestration (~200 lines of cloud→local fallback with JSON
  parsing) lived in `routers/agents.py`, which also owns agent CRUD, marketplace, and the
  workspace routes. Moved it into a new `services/agent_suggest.py` deep module: one async
  `suggest(goal, description, user_id, db) → Suggest` seam, plus the pure helpers
  (`_parse_suggest_json`, `_build_suggest`, `_looks_like_auth_error`, `_cloud_candidates`).
- The router `POST /agents/suggest` is now a thin handler that calls the service and
  translates the domain `SuggestError` → 502. The response shape is unchanged, so the API
  contract is untouched. New `backend/tests/test_agent_suggest.py` covers the pure helpers.

## Design-practice cleanup (GGUF parser split out of fit_score)

- `services/fit_score.py` was the report's worst hotspot — a god file mixing VRAM fit scoring
  with a hand-rolled GGUF binary header parser. The binary-format parsing is a different
  domain (byte layout vs. VRAM math), so the walker (`_read_gguf_string`, `_read_gguf_value`,
  `parse_gguf_header`) plus its constants moved into a new pure module
  `services/fit_score_gguf.py`.
- `fit_score.py` re-exports the walker (with a `_parse_gguf_header` alias) so the rest of the
  app and the existing `test_hf_detail.py` references are unchanged. The walker is pure
  (only `struct` + `gguf`, no I/O, no config), so it unit-tests in isolation — new
  `backend/tests/test_fit_score_gguf.py` (12 cases).

## Test coverage: sandbox seam (T3 execution boundary)

- `services/sandbox/*` (protocol, mock, http, factory) had zero tests despite being the
  execution seam behind the whole T3 code-execution story. New
  `backend/tests/test_sandbox.py` (13 cases) covers:
  - `ExecResult` dataclass defaults + fields, and that both adapters implement the same
    `exec` surface (the `Sandbox` Protocol is a real seam here).
  - `MockSandbox` — echoes command + workdir, never touches the filesystem, `fail` keyword →
    exit 1, long output truncated by `TOOL_RESULT_MAX_CHARS`.
  - `HttpSandbox` — POST body carries cmd/workdir/user_id/agent_id and maps the response,
    `httpx.TimeoutException → exit 124`, non-2xx → exit 1 (stubbed httpx client, no network).
  - `get_sandbox()` selection — Mock when `ENABLE_CODE_EXECUTION` is off or `SANDBOX_URL` is
    empty; Http when both are set.

## Test coverage: workspace file + bash tools (T3)

- `services/tools/files.py` and `services/tools/bash_tool.py` had no direct tests. New
  `backend/tests/test_file_tools.py` (11 cases) exercises the handlers against a real
  git-backed temp workspace (setUp creates a throwaway root; `_workspace_path` is patched to
  it; a fake DB is passed through `ToolContext`):
  - `list_files` / `read_file` (content + per-line `{n, hash, text}`) / `write_file` (commit +
    `edit_id` + `commit_sha`) roundtrip; `edit_lines` hashline replacement;
    hash-mismatch → "Error: … changed" conflict; `edit_patch` unified-diff application;
    stale-`expected_hashes` conflict; missing-`agent_id` → "no workspace".
  - `bash` — missing/too-long command, and structured `{stdout, stderr, exit_code}` output via
    the mock sandbox (which shares the workspace lock).
- Offline, like `test_workspace_store`: stub asyncpg/pgvector/redis/prometheus/langfuse/arq
  only during import then restore them, so sibling test modules aren't affected.
- Fixed a pre-existing ordering fragility in `test_memory_files.py`'s `MemoryToolsRegistryTests`
  — it now also skips when `memory_files` is unavailable (the tools registry can import on a
  host that can't import `memory_files`, which previously ran the handler test with `None`).

## Test coverage: agent adapter (policy + tool dispatch)

- `services/agent/agent.py`'s policy helpers had no coverage beyond the pure R2 helpers. New
  `backend/tests/test_agent_adapter.py` (21 cases) exercises the DB-free surface against a
  mocked DB session + stubbed tool registry:
  - `get_allowed_tools` — per-tenant policy: an explicit grant/deny row wins, otherwise
    first-party allowed.
  - `get_allowed_tools_for_agent` — the safety-ceiling intersection (agent.allowed_tools ∩
    per-user grant ∩ ENABLE_CODE_EXECUTION master switch), plus 404 (missing) / 403 (private
    not-owner).
  - `_resolve_agent` — (None,None,None) when no agent_id; resolves the agent; 404/403;
    public-read for anyone.
  - `_ensure_conversation_agent_binding` — stamps an unbound conversation; defaults
    `agent_version` to the row's version when not supplied; never overwrites an existing
    binding.
  - `_execute_tool` — invalid JSON → string, non-object args → string, timeout,
    handler-exception-as-string (run survives), long-result truncation.
- Shared stub helper `tests/agent_test_stubs.py` (`import_with_stubs`) so the agent-package,
  file/bash, and sandbox tests run offline on a bare host without leaving stubs behind.
  `test_agent.py` / `test_agent_runtime.py` now consistently run (they previously could skip
  via a stale-guard ordering side effect).

## Agent stream lifecycle, capability gate, memory rollback (architecture review C1/C2/C4)

A review pass (repowise + cluster audits) surfaced three defects beyond the tracked roadmap items; all fixed in one commit.

- **Agent stream-slot leak.** `services/agent/agent.py::run_agent` released the per-user stream slot only `if not entered_runtime`, so every successful (and mid-run-failed) agent chat leaked a slot — after `MAX_CONCURRENT_STREAMS` runs a user was hard-429'd until backend restart. The `finally` now releases unconditionally, matching `routers/chat.py`'s outer `stream_tokens` wrapper and `routers/research.py`'s router-level try/finally. `release_stream_slot` is idempotent (never negative), so an unconditional release can't double-free. The runtime never imports `release_stream_slot` — the adapter owns the lifecycle.
- **Capability-gate bypass on the global tool path.** CONTEXT.md defines a Capability as requiring both a master switch and a per-user grant. The legacy `get_allowed_tools` (used when no `agent_id`) applied only the permission row, while `get_allowed_tools_for_agent` added the `ENABLE_CODE_EXECUTION` ceiling. A user could `PUT /v1/agent/tools/{name}/permission` for `write_file`/`edit_patch`/`edit_lines` then chat **without** `agent_id` to get real filesystem writes with the switch off (file tools aren't sandbox-mediated). A shared `_ceiling_allows()` helper now gates both allowlist paths. The self-grant endpoint (`routers/agent.py`) still accepts any registered tool name — that's tracked separately (capability-class screening on the Tool schema is a future C2 follow-up).
- **`safe_build_memory_context` didn't roll back.** On the swallow path it logged and returned `""` without `await db.rollback()`. A failed SQL statement poisons an asyncpg transaction, and both chat.py and agent.py run further queries on that same session after this call → `PendingRollbackError` (neither `AppError`/`APIError`), crashing the chat stream / generic 500 in the agent. Now matches `memory.py::retrieve_memories`'s `await db.rollback()`.
- **Agent SSE `done.conversation_id` drift.** The backend emits `{"type":"done","conversation_id":null}` on pre-runtime 404/403; the frontend `agent-events.ts` guard (`typeof === "string"`) dropped that terminal frame. Union widened to `string | null`; consumers already null-coalesce.

## Reliability fixes implemented (R1 + R2)

The agent-loop reliability pass from Phase 2, landed together.

- **R1 — the agent loop no longer holds the request's DB connection.**
  - `services/agent.py::run_agent` closes the request session after the up-front reads
    (`conversation()`, `load_history()`, `get_allowed_tools()`, `get_provider()`) and before
    the multi-round loop, mirroring the chat path. No idle connection is held for the run.
  - Each tool execution gets its own short-lived `AsyncSessionLocal` (`ctx.db` is swapped per
    call); the unknown/unauthorised-tool branch needs no session. `save_messages` uses a fresh
    `AsyncSessionLocal` after the loop; `get_db`'s own close afterwards is a no-op.
- **R2 — messages are bounded and context overflow degrades.**
  - New pure helpers in `services/agent/runtime.py`: `_estimate_tokens` (≈ 4 tokens/message + 1 per 4
    chars), `_is_context_error` (provider-error string sniffing), `_prune_old_tool_rounds`
    (drops the oldest assistant-with-tool_calls message plus its tool results until the
    estimate fits; leading system messages + the first user message are never touched).
  - The loop prunes to `0.6 × AGENT_TOKEN_BUDGET` before every tool round, and to the full
    budget before the final tool-less round. A context-length `APIError` mid-loop logs a
    warning, drops all tool rounds, and synthesizes a tool-less final answer with
    `truncated=True` — the SSE event sequence and done-event shape are unchanged.
  - New unit tests: `backend/tests/test_agent_runtime.py` (covers the estimate, the error
    sniffing, and pruning structure against the runtime's live helpers).

## Reliability fixes implemented (R3–R6)

The Phase 2 reliability follow-ups (CR-10/14/15/16), landed together.

- **R3 — honest local token counts.**
  - `models/messages.py` adds `token_provenance` (`String(16)`, nullable — `exact` |
    `chunk_count` | `null`; migration `f3a9c1d5e7b2`), recording how `tokens_used` was
    derived. `save_messages` takes a `token_provenance` keyword and sets it on the assistant
    message.
  - `routers/chat.py::stream_tokens` computes `provenance = "exact" if prompt_tok > 0 else
    "chunk_count"` (cloud reports usage; local leaves it 0) and, on the LOCAL path only,
    spawns `services/tokenize.py::sync_local_token_counts(conversation_id, user_content,
    full_response)` after saving. The sync POSTs the user + assistant texts to
    `{LM_URL}/v1/tokenize/encode` (timeout 15, tolerant of `{"count": N}` and `{"length": N}`
    responses), then on a fresh session overwrites the last two messages — assistant
    `tokens_used` set to the completion count, both `token_provenance = "exact"`. Any error
    logs a warning and leaves the chunk_count values in place (counts are auxiliary, never a
    request failure).
  - `services/agent.py::run_agent` computes the same provenance from whether any round
    reported usage and passes it to `save_messages`.
- **R4 — honest research model.**
  - New `services/research.py::resolve_research_model(provider_arg, user_id, db)` computes
    the `(role, model)` pair research will run on, mirroring `_pick_provider`'s precedence
    without constructing providers: `"openrouter"`→cloud, `"local"`→local, else cloud when
    `OPENROUTER_DEFAULT_MODEL` is set; a configured default row wins; the legacy env fallback
    needs an OpenRouter key for cloud and uses `LM_CHAT_MODEL or LM_DEFAULT_MODEL` for local.
    Returns `None` when no model string resolves.
  - `routers/research.py::create_research` resolves before enqueueing: `None` → 503
    "no capable chat model configured for research"; otherwise the job is stored with
    `provider = resolved_role` and `model = resolved_model` (the worker's `_pick_provider`
    then just uses the stored model). `request.model` no longer lands as `"auto"` on the job.
- **R5 — strict ComfyUI anchors.**
  - `services/comfy.py` adds `_find_node_exact` (exact `class_type` equality) and
    `validate_workflow_anchors(graph, param_map)`: raises `ValueError` when a critical anchor
    pattern (KSampler/KSamplerAdvanced family, `ResolutionSelector`, `*LatentImage`) has more
    than one match and `param_map` doesn't target one of the matched node ids.
  - `inject_params` auto-detection now requires EXACTLY one exact match per anchor
    (`KSampler` equality, `ResolutionSelector` equality, `endswith("LatentImage")`); 0 or >1
    matches means that param is left unset unless `param_map` overrides it — no guessing.
    `param_map` priority is unchanged (applied after auto-detect).
  - `routers/workflows.py` create + update call `validate_workflow_anchors` and translate
    `ValueError` → `HTTPException(422, str(e))`.
- **R6 — search degradation is surfaced.**
  - `core/metrics.py` adds `search_degraded_total` (Counter, `["source"]`). `_duckduckgo`
    and `_searxng` log a warning and increment it (source `"duckduckgo"`/`"searxng"`) when a
    200 response yields zero results.
  - `services/tools/web_search.py` empty-result text is now "Search returned no results
    (degraded); answer from prior knowledge." — the model is told the search degraded instead
    of pretending it found nothing.
- **Tests:** `backend/tests/test_comfy.py` grows 8 cases: base workflow passes validation;
  KSampler + KSamplerAdvanced raises without `param_map` and passes with it; multiple latent
  nodes raise; `inject_params` leaves `batch_size` unset on ambiguous latent graphs; `param_map` still beats auto-detect.

## Data integrity fixes implemented (D4 + R7)

The Phase 3 data-integrity leftovers (D4 + R7), landed together.

- **D4 — optional pagination on list endpoints.**
  - `GET /v1/convo`, `GET /v1/presets`, `GET /v1/templates` now accept two optional query
    params: `limit: int | None` (`ge=1, le=200`, default `None` = no limit, previous
    behavior) and `offset: int` (`ge=0`, default `0`). When `limit` is set, `.limit(limit)
    .offset(offset)` is chained onto the existing `select` before execution, keeping each
    endpoint's `order_by` untouched (convo sorts `created_at.desc()`, presets/templates have
    no explicit order). Response shapes are unchanged — `{"data": [...]}` for
    presets/templates, a bare array for convo.
- **R7 — hourly expired refresh-token sweep.**
  - `worker.py` adds `sweep_expired_tokens(ctx)`, which on a fresh `AsyncSessionLocal`
    session runs `delete(RefreshToken).where(RefreshToken.expires_at < datetime.utcnow())`
    and commits — no orphaned expired `refresh_tokens` rows accumulate.
  - `WorkerSettings` gains `cron_jobs = [cron(sweep_expired_tokens, minute=0)]` (arq 0.25,
    the `from arq import cron` API), firing hourly at minute 0. Everything else in
    `WorkerSettings` is unchanged. **The worker container must be restarted for cron changes
    to take effect** — arq does not hot-reload.
- **Tests:** `backend/tests/test_worker.py` asserts `WorkerSettings.cron_jobs` is non-empty,
  `WorkerSettings.functions` still includes `run_research`, and `sweep_expired_tokens` is
  callable (offline; the sweep itself is never invoked).

## Auth hardening (S-A)

A defensive pass over the auth/API surface, landed together.

- **Consistent 401s for missing credentials.** `core/security.py` introduces `_AuthBearer`
  (an `HTTPBearer` subclass with `auto_error=False`): the parent returns `None` instead of
  raising when the `Authorization` header is absent (or isn't a Bearer scheme), and the
  subclass raises `401 {"detail":"not authenticated"}` in that case. `get_current_user` now
  depends on `Depends(_AuthBearer())`, so every authed route answers **401** for a missing
  header instead of FastAPI's stock 403 — invalid/expired tokens keep their existing 401s.
- **Refresh-token `sub` cross-check.** `routers/auth.py` `/auth/refresh` now verifies
  `str(token_record.user_id) == str(payload.get("sub"))` after the DB expiry check and JWT
  decode, before minting the rotated token — a mismatched row/claim pair can't be rotated.
- **`GET /v1/images/aspect-ratios` now authed.** The static config endpoint takes
  `user_id: str = Depends(get_current_user)` like every other `/v1` route; response shape
  unchanged. The SPA already sends the bearer token on every request, so no client change
  was needed beyond the roadmap note.
- **Provider test errors genericized.** `services/provider_registry.py::test_provider` logs
  the real exception server-side (`logger.warning`) and returns
  `{"ok": false, "error": "provider test failed"}` instead of leaking the raw exception
  string (URLs, key fragments, SDK tracebacks) to the client. The `{ok, model}` success
  path and the `"no default model set"` result are unchanged.
- **JWTs carry `iat`.** Both `create_access_token` and `create_refresh_token` add
  `"iat": int(time.time())` to the payload.

## Config / infra hardening (S-B)

A configuration + infra hardening batch, landed together.

- **Loopback monitoring ports.** `docker-compose.yml` binds Prometheus to
  `127.0.0.1:9090:9090` and Grafana to `127.0.0.1:3000:3000` (previously `9090:9090` /
  `3000:3000`). Dashboards remain reachable on the host for dev but are no longer exposed
  to the tailnet — Caddy on `:80` is the only ingress (same policy as the backend port, S2).
- **Caddy security headers.** The `:80` site block sets a site-level `header` directive
  (applies to every handler): `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and a CSP (`default-src 'self'`, `img-src 'self' blob:
  data:`, `style-src 'self' 'unsafe-inline'`, `connect-src 'self' http://localhost:2727
  http://localhost:5173`, `script-src 'self'`). A comment documents the two CSP constraints:
  `img-src` must include `blob:` (the SPA renders authed images as blob URLs via
  AuthedImage) and `connect-src` must cover the API origins.
- **DEBUG refused in production.** `core/config.py` raises `RuntimeError` at import when
  `DEBUG=True` and `ENV=production` — the app fails boot on a bad config instead of echoing
  SQL in prod.
- **Provider `base_url` scheme validation + private-URL guard.** `routers/providers.py`
  adds `_validate_provider_base_url`: `base_url` must be http(s) with a hostname, and — when
  `ALLOW_PRIVATE_PROVIDER_URLS` is False — must resolve to a public (non-private/loopback/
  link-local/reserved) address (mirrors `services/search.py::_assert_public_host`; resolution
  failure skips the check). It runs on both create (ProviderCreate `model_validator`) and
  update (the effective-state review-fix block), raising 422 on violation. The new
  `ALLOW_PRIVATE_PROVIDER_URLS` flag defaults to `true` (local LM Studio-style providers keep
  working); set `false` only on locked-down deployments.

## Auth hardening — claims + policy (audit INFO)

Audit INFO items, landed together.

- **JWT `iss`/`aud` claims + validation.** Both `create_access_token` and
  `create_refresh_token` now stamp `"iss": settings.JWT_ISSUER` (`"llm-gateway"`) and
  `"aud": settings.JWT_AUDIENCE` (`"llm-gateway-api"`) onto the payload (next to the
  existing `iat`). `get_current_user` and `/auth/refresh` decode with
  `issuer=settings.JWT_ISSUER, audience=settings.JWT_AUDIENCE`, so tokens from another
  issuer (or for another audience) are rejected with the standard 401s — same try/except
  behavior, no new error paths. **Existing sessions are invalidated once:** previously-issued
  tokens lack the claims, so every client re-logs in a single time after deploy; nothing
  else changes (tokens refresh normally afterwards).
- **Registration password policy.** `routers/auth.py` `UserCreate._password_length` now
  requires `len >= 8` **and** at least one letter **and** at least one digit; violation
  returns 422 `"password must be at least 8 characters with at least one letter and one
  number"`. Login is untouched — it still accepts whatever credentials existing accounts
  were created with (only `UserCreate` validates).
- **Per-user SSE concurrency cap.** New `core/stream_guard.py`: an in-memory per-user
  counter guarded by an `asyncio.Lock`, capped at `settings.MAX_CONCURRENT_STREAMS` (default
  4). `acquire_stream_slot(user_id)` runs in each router handler **before** the
  `StreamingResponse` is created (a 429 "too many concurrent streams" is a real HTTP
  response, not an in-stream SSE error); `release_stream_slot(user_id)` runs in each
  generator's `finally` (fires on normal completion, error, and client disconnect). Wired
  into `/v1/chat/completions`, `/v1/agent/chat`, and `/v1/research/{id}/stream`. In-memory is correct because the backend is a single
  container; a horizontally-scaled deployment would need a shared counter. Acquire/release
  stays 1:1 even when the stream never starts: stream handlers wrap their ownership checks in
  `try/except BaseException` that releases the slot before re-raising (pubsub setup
  lives inside the generator, already covered by the relay `finally`).
- **Langfuse content documentation.** `README.md` notes that full chat content is sent to
  Langfuse unless a message is sent with `private: true` (metadata-only), and that
  deployments not using Langfuse should remove the `LANGFUSE_*` keys from `.env`.
- **Refresh tokens bound to an optional client `device_id` (replay protection).**
  `refresh_tokens.device_id` (`String(128)`, nullable — migration `b83db11a1dc0`) stores the
  client-generated device id the token was minted for. `/auth/login` and `/auth/refresh`
  accept an optional `device_id`; the rotated token carries the SAME binding as the row it
  replaced. `/auth/refresh` rejects with 401 "invalid refresh token" when a bound row is
  presented without an exact `device_id` match (missing or mismatched — both are 401).
   Legacy rows with `device_id IS NULL` accept any caller, so pre-existing tokens keep
   working. The `sub` cross-check, DB expiry check, JWT decode, and rotation behavior are
   unchanged.

## Maintainability fixes implemented (M1 + M4)

The Phase 4 maintainability items, landed together.

- **M1 — dependencies pinned via pip-tools.**
  - `backend/requirements.in` is the editable source of truth (the same direct deps the
    unpinned `requirements.txt` listed, one per line, comments preserved where useful,
    including the `bcrypt==4.0.1` pin). The header documents the workflow: edit
    `requirements.in` → pip-compile → commit both files.
  - `backend/requirements.txt` is now a generated lockfile (standard pip-compile header,
    `# via` annotations, no hashes). Resolved inside the backend container. Because PyPI
    had moved past the installed versions (openai 2.53.0 → 3.0.0, arq 0.25.0 → 0.28.0,
    sqlalchemy 2.0.51 → 2.0.52, starlette 1.5.1 → 1.6.0, langfuse 4.14.3 → 4.14.4, plus a
    redis 8.1.0 → 5.3.1 *downgrade* under the newer resolution graph), the lock was first
    generated with the installed versions fed in as constraints, then re-run plain so the
    committed header stays clean. Final pins match `pip freeze` of the running container
    exactly — 0 version diffs across all 80 app dependencies — so the next build
    reproduces the current environment (the running containers were NOT rebuilt; the next
    trainer build picks up the pins via the shared base target while keeping its own
    torch/ai-toolkit pins).
  - Verified in the container: `pip check` clean, 88/88 unit tests pass, `import
    app.main` boots.
- **M4 — CI schema-drift guard.**
  - `backend/scripts/ci-schema-drift.sh` (bash, `set -euo pipefail`, executable at commit
    time via `git add --chmod=+x`): requires `DATABASE_URL` pointing at a scratch DB and
    provides CI-safe dummies for the other pydantic-required settings
    (REDIS_URL/SECRET_KEY/ALGORITHM/LM_URL/LM_DEFAULT_MODEL) that `alembic/env.py`'s
    import of `app.core.config` demands. Runs `alembic upgrade head`, then `alembic
    check` (alembic ≥ 1.9; installed is 1.19.1), with a fallback to `alembic revision
    --autogenerate -m ci_check --rev-id=ci_check` + `grep -q "op\."` + file cleanup for
    older alembics. Exits 1 with "schema drift detected: models and migrations disagree"
    on any drift or failure.
  - `.github/workflows/schema-drift.yml` — runs on push + pull_request: checkout →
    setup-python 3.11 (pip cache keyed on the pinned lockfile) → `pip install -r
    backend/requirements.txt` → the guard against a `pgvector/pgvector:pg16` service
    (NOT stock `postgres:16` — the memories migration runs `CREATE EXTENSION vector`,
    which the stock image doesn't ship) → `python -m unittest discover -s tests` in the
    same job. YAML validated with PyYAML; the `on` key is quoted for strict YAML 1.1
    parsers.
  - **Local verification (scratch DB on the running postgres):** created
    `llmgateway_ci`, ran the script inside the backend container with
    `DATABASE_URL=postgresql://ishaab:ishaab27@postgres/llmgateway_ci`, then dropped the
    DB (verified gone). The guard correctly failed (exit 1) against the pre-existing
    drift described below, then passed clean (exit 0, "==> alembic check: no schema
    drift") once the drift was reconciled.
  - **Pre-existing drift uncovered and reconciled:** `alembic check` reported two
    `remove_index` operations — `ix_research_jobs_user_id` and
    `ix_tool_permissions_user_id`. Migrations `3fa8c20b911e` / `7d41aa30c5f2` created
    those indexes explicitly, but the models (`models/research_jobs.py`,
    `models/tool_permissions.py`) didn't declare `index=True` on `user_id`, so alembic
    wanted to autogenerate drops for them — and no follow-up migration had ever
    reconciled the mismatch. This drift predated M4. Reconciled by restoring
    `index=True` on the `user_id` ForeignKey column in BOTH models. No new migration
    was needed: the indexes already exist in the database from the original migrations,
    so the models now match the schema and `alembic check` sees no diff.

## New agent tools (current_datetime, search_conversations, generate_image, calculate)

Four first-party tools added to the agent toolchain, following the existing
`recall.py` / `web_search.py` / `fetch_page.py` pattern: each module registers a
`Tool` at import time (the `tools/__init__.py` import list), and handler failures
return strings — never raise, so the agent run survives.

- **`current_datetime`** (`services/tools/current_time.py` — module named so it doesn't
  shadow stdlib `datetime`) — no args; returns a naive-UTC timestamp with the weekday,
  e.g. `2026-08-11 06:15 UTC (Tuesday)`, plus a one-line note that the user's local time
  may differ. Uses `datetime.utcnow` per the repo convention.
- **`search_conversations`** (`services/tools/search_conversations.py`) — `query`
  (required, capped at 200 chars) + optional `limit` (1–10, default 5). Searches THIS
  user's conversations by title OR message content with case-insensitive `ILIKE`,
  metacharacters (`%`, `_`, `\`) escaped via the module-level `_escape_like` helper,
  `DISTINCT` on conversations, ordered by `created_at desc`. The `messages` join is a
  LEFT OUTER JOIN with both predicates in the WHERE clause, so title-only conversations
  (zero messages) also match when the title hits. Each result carries a snippet: the
  most recent matching message truncated to ~200 chars, or `—` when only the title
  matched. Returns a compact JSON array `[{id, title, snippet}]`, or
  "No conversations matched." when nothing hits.
- **`generate_image`** (`services/tools/generate_image.py`) — wraps
  `services/comfy.generate_image` + `get_job_status` with `workflow_id=None` (base
  workflow) and `batch_size=1`. Args: `prompt` (1–4000), `negative_prompt` (default
  `"text, watermark, blurry, low quality"`), `steps` (1–50, default 10), `cfg` (0–20,
  default 1.2), `aspect_ratio` (validated against `ASPECT_RATIOS`; a mismatch returns an
  error listing the valid options), `seed` (optional);

  - **Ownership registration:** after submission the handler sets
    `imgjob:{prompt_id} → user_id` (TTL 1h) — the same key the images router uses — so
    the user can poll `/v1/images/status/{prompt_id}` and fetch the result.
  - **Polling:** one immediate `get_job_status`, then 4 × 5s `asyncio.sleep` checks
    (~20s total, under `TOOL_TIMEOUT_SECONDS` = 30s). On `complete` each image's
    `imgfile:{filename} → user_id` key is set (TTL `IMAGE_FILE_TTL_SECONDS`) and the
    handler returns `{"prompt_id", "images"}` where `images` carry the relative
    `/v1/images/file` URLs from `get_job_status`. On `failed` it returns the ComfyUI
    error string.
  - **Timeout:** still rendering after ~20s, it returns "Image generation started
    (prompt_id …) and is still rendering; check the Images tab shortly." — the agent run
    is not failed; the job stays visible via the Images tab.
  - **Failures are strings:** any unexpected exception is caught and returned as
    "Error: image generation failed: {e}".
- **`calculate`** (`services/tools/calculate.py`) — safe arithmetic evaluator.
  `expression` (required, capped at 500 chars). It is an **AST-whitelist
  evaluator — never `eval`/`exec`**: the expression is parsed with
  `ast.parse(expr, mode="eval")` and evaluated by a recursive walker that admits
  only numeric literals, `+ - * / // % **`, unary `+`/`-`, parentheses, the
  constants `pi`/`e`, and the functions `sqrt abs round min max pow exp log
  log10 floor ceil sin cos tan` (implemented via `math`). Everything else —
  attribute access, indexing, collections, comprehensions, strings, imports —
  returns `Error: expression contains unsupported syntax`. Guard rails: AST depth
  capped at 40 (tracked during the walk), arithmetic errors
  (`ZeroDivisionError`/`OverflowError`/`ValueError`/`TypeError`) and non-finite
  results return `Error: ...` strings. Results format integral values as ints and
  floats to 10 significant digits.
- **Tests:** `backend/tests/test_tools.py` (stdlib unittest, skip-import pattern like
  `test_comfy.py`) — datetime format regex, `_escape_like` escaping (`%`/`_`/`\`),
  compiled-SQL assertions that the search query uses a LEFT OUTER JOIN with both
  predicates in the WHERE clause, a patched-redis `generate_image` error-path check,
  and registry assertions that all three new names plus the pre-existing first-party
  tools are registered. The full container suite now runs 112 tests (was 99).
- **Tests:** `backend/tests/test_calculate.py` (stdlib unittest, skip-import pattern like
  `test_comfy.py`) — arithmetic/operator/function/constant happy paths plus
  rejection cases (imports, attribute access, unknown names, division by zero,
  math domain, over-length, over-deep AST) and a registry assertion.

## Memory files (Claude-style)

**Phase M1 — DONE** (storage layer, 5 tools, read-path injection).

Per-user memory files, read and edited through agentic tool calls — a
Claude-style file store. This is **explicitly NOT embeddings**: it is a plain
versioned file store read via `memory_*` tools, entirely distinct from the
pgvector RAG in `services/memory.py` (which remains untouched).

- **`memory_files` table** (`models/memory_files.py`, migration
  `a1b2c3d4e5f6`, down_revision `b83db11a1dc0` — NOT applied to the live DB,
  code-review only; `create_table(..., if_not_exists=True)` so a later
  `alembic upgrade head` is a no-op where the M1 tests pre-created the table
  from the model): `user_id` FK (CASCADE), `path` (String 512),
  `description` (String 512), `aliases` (String[]), `content` (Text),
  `version` (Integer, default 1), `size_bytes`, `sources` (String 64),
  `updated_at` (server `now()`), unique `(user_id, path)`. Versioned: every
  mutating operation takes an `if_version` and a stale write is rejected with
  the current content/version instead of silently clobbering.
- **Storage layer** (`services/memory_files.py`, stdlib + SQLAlchemy only):
  `_validate_path` (must start with `/`, ≤512 chars, no control chars, no
  `..` segments), `memory_index` / `memory_read`, the single versioned-write
  primitive `_apply_write` (cap enforcement via
  `settings.MEMORY_FILE_CAP_BYTES` = 32768 — at/over the cap is rejected,
  never truncated; `NEW_SENTINEL = "__new__"` creates; a versioned UPDATE
  bumps `version + 1` only when the row still matches), and the derived ops
  `memory_write`, `memory_append` (newline-separated), `memory_str_replace`
  (refuses 0 or >1 matches), `memory_delete` (distinct not_found vs
  conflict). `if_version` is coerced to int from the tools' string args.
- **Five first-party tools** (`services/tools/memory_tools.py`, registered via
  `tools/__init__.py`): `memory_read`, `memory_write`, `memory_str_replace`,
  `memory_append`, `memory_delete`. All handlers return strings, never raise,
  and scope by `ctx.user_id` (identity never comes from args). Descriptions
  make the versioning contract explicit (read before write; `if_version` from
  a prior read; `__new__` to create; str_replace needs exactly one
  occurrence). All five are first-party (allowed by default); they are listed
  in the dynamic agent tool list like every other tool.
- **Read-path injection** — `routers/chat.py` and `services/agent.py` build
  the memory context BEFORE their `db.close()` and prepend/merge it into the
  system prompt: chat appends a leading system message (preset prompt stays
  untouched), agent merges after the preset `system_prompt` with `"\n\n"` (or
  adds a leading system message when there is no preset prompt). The context
  is the Tier-1 index (one line per file, path-ordered:
  `- {path} — {description}` + aliases) plus Tier-1.5 full-file blocks for
  `MEMORY_TIER1_5_PATHS` (`/profile.md,/preferences.md`), prefixed
  "User memory files:". Injection is best-effort:
  `safe_build_memory_context` catches/logs and returns `""` so a memory
  failure never fails a chat/agent request. The SSE wire formats are
  unchanged.
- **Tests** — `backend/tests/test_memory_files.py` (stdlib unittest, runs in
  the container against the real DB with a throwaway user row per test):
  versioned create/update, stale-version conflict with current
  content/version, create-when-exists conflict, read miss, str_replace
  0/2/1-match outcomes, append + version advance, delete + read-None, size
  cap rejection (settings patched low), `build_memory_context` empty/index/
  tier-1.5, path validation, and registry presence of all five tools. The
  test ensures the `memory_files` table exists (creates it from the model
  when the migration hasn't been applied).

**Phase M2 — DONE** (background curation pipeline).

After every chat/agent turn, an arq job reads the transcript and asks the batch
model to propose memory-file operations — so memory files grow from what the
user says over time, not only from explicit `memory_*` tool edits.

- **`services/memory_curation.py`** (new):
  - **`CURATION_PROMPT`** — the rule set for the batch model: a one-month
    horizon test (session-local task state fails; stable facts about the
    person/responsibilities/preferences pass); provenance discipline (file
    only what the USER stated, never assistant inference/recommendation);
    dedup against the index/existing content; a hard privacy-exclusion list
    (health/medical, sexual orientation, immigration status, government ID /
    payment numbers, home address, family member NAMES — relationship words
    instead, and non-sensitive remainders only, no placeholders); one file per
    subject (`/profile.md`, `/topics/…`, `/areas/…`, `/people/…`); size
    discipline (consolidate near the cap instead of appending); exclusion of
    instructions that degrade future behavior ("always agree with me", "stop
    giving critical feedback", "pretend to be a persona across sessions");
    and a strict JSON-array output contract (create/write/append/str_replace/
    delete with exact versions from the file list, `__new__` for create,
    exactly-one-match for old_str, ≤10 ops).
  - **`parse_ops(raw)`** — pure, defensive parser: strips markdown fences,
    extracts the JSON array from surrounding prose, validates every op
    (allowed kind, path via `memory_files._validate_path`, per-kind required
    fields, create's `__new__` sentinel), logs and drops invalid ops, caps at
    10; unparseable input → `[]`.
  - **`run_curation_pass(user_id, conversation_id, written_paths)`** — the arq
    entry point: opens its own `AsyncSessionLocal`, fetches the last
    `MEMORY_CURATION_MAX_MESSAGES` (20) messages ordered by index (reversed
    to chronological; <2 messages → return), builds the "current files" block
    from `memory_index` + full `memory_read` contents (smallest-first, capped
    at ~48KB of content with the index always included), picks the batch model
    mirroring `research._pick_provider`'s precedence (see below), makes ONE
    `provider.complete` call at temperature 0.2, then applies the parsed ops
    through the versioned primitives. The whole pass is wrapped in a top-level
    try/except — the job never crashes.
  - **`_fetch_transcript(db, user_id, conversation_id)`** — the transcript
    fetch is ownership-scoped IN THE QUERY: it JOINs `conversations` and
    requires both `Message.conversation_id == conversation_id` AND
    `Conversation.user_id == user_id`, so a missing or foreign conversation
    returns `[]` and the pass returns early with a log — a curation pass can
    never read (or feed to the batch model) another user's transcript.
  - **`apply_ops(db, user_id, ops, written_paths)`** — the testable apply loop:
    skips paths the agent wrote this turn, dispatches each op to the
    versioned primitives (`memory_write`/`memory_append`/`memory_str_replace`/
    `memory_delete`, `source="curation"`), and applies the retry-once conflict
    policy: on `conflict` it re-reads and re-derives against the fresh version
    (write/append/str_replace/delete), drops create conflicts and str_replace
    ops whose `old_str` no longer matches exactly once, and drops the op on a
    second conflict; `not_found`/`ambiguous`/`size_cap`/`invalid_path` log and
    continue; each op is independently wrapped so one bad op can't kill the
    pass. Returns log lines for observability and tests.
  - **`should_skip_curation(request_private, has_memory_files)`** — returns
    `request_private`: private chats never feed memory; an empty index is NOT
    a skip (the first-ever pass is what creates files).
  - **`enqueue_curation(user_id, conversation_id, written_paths, private)`** —
    off-path, best-effort enqueue of `run_memory_curation`; private → return;
    any exception logs a warning and never fails the response.
- **Batch model resolution** — `_pick_batch_model` mirrors
  `research._pick_provider`'s precedence, simplified (no job pinning): role =
  `cloud` when `MEMORY_CURATION_MODEL_ROLE == "cloud"`, else `cloud` when
  `OPENROUTER_DEFAULT_MODEL` is configured, else `local`. A configured default
  provider row wins; with no rows the legacy env-var clients apply (cloud
  needs an OpenRouter key, otherwise falls through to local, never raises).
  Nothing resolving → log + skip the pass. Config: `MEMORY_CURATION_MAX_MESSAGES`
  (20, transcript window) and `MEMORY_CURATION_MODEL_ROLE` (auto, the
  preferred role).
- **Wiring** — `worker.py` registers `run_memory_curation` in
  `WorkerSettings.functions` (the worker must be restarted to pick it up —
  arq does not hot-reload). `routers/chat.py` spawns
  `enqueue_curation(..., private=request.private)` after `save_messages`;
  `services/agent.py` captures paths written by the `memory_write`/
  `memory_str_replace`/`memory_append`/`memory_delete` tools in-turn and
  passes them to the enqueue so the pass never clobbers an in-turn write.
  Both enqueues run via `core/background.spawn` — off the response path.
- **Tests** — `backend/tests/test_memory_curation.py` (stdlib unittest, the
  test_memory_files.py pattern): parse_ops (valid/prose-wrapped/unparseable/
  invalid op names/missing create description/missing old_str/path
  validation/`__new__` enforcement/10-op cap), the DB-backed apply flow
  (create+append+str_replace → expected final state; ops on `written_paths`
  skipped; stale write conflict retried against the fresh version; second
  conflict drops the op and leaves the file intact), `should_skip_curation`,
  enqueue wiring (private never enqueues, enqueue failure is logged not
  raised), and the transcript-ownership regression suite (two users; a foreign
  conversation → `_fetch_transcript` returns `[]` and `run_curation_pass`
  returns early so the batch model is never called). Full suite: 151 tests
  pass (was 129).

## Verification (memory files M1)

- `docker compose exec -T backend python -m unittest discover -s /app/tests -v` — full suite passes (112 existing + new memory-file tests).
- `python -m py_compile` on all new/changed files.
- `docker compose exec -T backend python -c "from app.services.tools import registry; print(sorted(t.name for t in registry.all_tools()))"` — includes the 5 `memory_*` tools.
- `docker compose exec -T backend python -c "import app.main"` — boots.

## Verification (memory files M2)

- `docker compose exec -T backend python -m unittest discover -s /app/tests -v` — full suite passes (151 tests: 129 existing + 22 curation).
- `python -m py_compile` on all new/changed files.
- `docker compose exec -T backend python -c "import app.worker; print([f.__name__ for f in app.worker.WorkerSettings.functions])"` — includes `run_memory_curation`.
- `docker compose exec -T backend python -c "import app.main"` — boots; `/health` 200.

## Unified model-fit cookbook (Hugging Face) — DONE

The Cookbook page can now score **Hugging Face** models against the same
VRAM heuristic as the local LM Studio catalog, so the two tables rank
apples-to-apples.

- **`services/fit_score.py`** — new `get_hf_models(search, limit)` queries
  `https://huggingface.co/api/models` (`limit` capped at 50, `sort=downloads`,
  10s timeout) and builds catalog entries from each model's
  `safetensors`/`pytorch` block (`parameters` → `params_b`, `total` bytes →
  `size_bytes`) plus `downloads`, `likes`, `lastModified`, `pipeline_tag`,
  `library_name`. When no weights block exists, `params_b` falls back to
  `_parse_params_b` on the model id. Any failure logs a warning and returns
  `[]` — the endpoint never raises. Results are cached in-process for 600s
  (`_hf_cache` keyed by `(search, limit)`, `_cache_get`/`_cache_set`).
- **`build_hf_cookbook(hardware, context_tokens, search, limit)`** — same
  VRAM logic as `build_cookbook` (max total VRAM across GPUs + `ram_total_mb`
  for RAM-offload verdicts when `gpu_available`), runs each entry through
  `estimate_fit`, and sorts with
  the same verdict order. The rank dict is factored to a module constant
  `_VERDICT_RANK` used by BOTH cookbook builders. Response:
  `{hardware, context_tokens, search, models, count}` (no recommendation —
  that stays local-only).
- **`routers/hf.py`** — `GET /v1/hf/models?search=&limit=&context_tokens=`
  (auth via `get_current_user`; `search` ≤200 chars, `limit` 1–50,
  `context_tokens` 512–262144), mounted at `/v1` in `main.py`. Probes
  hardware, then `build_hf_cookbook(hw, context_tokens or
  settings.COOKBOOK_CONTEXT_TOKENS, search, limit)`.
- **Frontend** — `cookbook.tsx` gains Local / Hugging Face tabs. The HF tab
  has a search box (Enter/Search button) + result-count dropdown (10/25/50),
  loads on submit / tab switch / context change, and reuses the shared
  verdict table (HF sub-label `{params_b}B · HF · {downloads} downloads ·
  {likes} likes`, quant column `—`, `pipeline_tag` appended to Notes).
  `HfCookbookResponse`/`HfModelEntry` types + `hfApi.models` added to the
  contract layer.
- **Tests** — `backend/tests/test_fit_score.py` (stdlib unittest, skip-import
  pattern; 7 cases): safetensors → `params_b`/`size_bytes`, model-id fallback
  (`qwen2.5-7b-instruct` → 7.0), fetch failure → `[]`, cache hit skips the
  second fetch, `build_hf_cookbook` scores/sorts entries, and
  `estimate_fit` with no quant uses `DEFAULT_BYTES_PER_PARAM`. Full suite:
  158 tests pass (was 151).

## HF model browser + GGUF-accurate fit (F1) — DONE

The Cookbook's HF tab gains a per-model browser: `GET /v1/hf/models/{repo_id}`
returns one HF repo's stats plus a per-quant VRAM fit computed from the GGUF
files' **own headers** — the KV estimate stops being the 128KB-per-7B ballpark
and becomes exact (real `n_layer`/`n_embd`/`n_head[_kv]`).

- **Config** (`core/config.py`) — `HF_TOKEN` (optional; empty = anonymous,
  enables gated repos + higher rate limits), `KV_CACHE_BYTES_PER_ELEMENT`
  (2.0 = f16 default), `FIT_SAFETY_MARGIN` (0.10).
- **GGUF header read** (`services/fit_score.py`) — `read_gguf_metadata`
  Range-requests the first `_GGUF_RANGE_BYTES` (4 MB) of a resolve URL
  (`Authorization: Bearer <HF_TOKEN>` when set, 15s timeout) and walks the
  header: magic/version/tensor+KV counts, then the KV section
  (`general.architecture`, `<arch>.block_count`, `.embedding_length`,
  `.attention.head_count[_kv]`, `.context_length`; `n_kv_head` defaults to
  `n_head`). The walker uses `gguf`'s constants (`GGUF_MAGIC`,
  `GGUFValueType`, supported versions [2, 3]) instead of `GGUFReader`,
  because GGUFReader needs a real file path (it `np.memmap`s) and eagerly
  reads every tensor's data — on a partial 4 MB body it raises in
  `_build_tensors` before `fields` is reachable. Any failure returns None
  (never raises); results cached in-process per URL for 600s.
- **KV formula** (exact) —
  `kv_bytes = 2 × n_layer × ctx × n_embd × KV_CACHE_BYTES_PER_ELEMENT × (n_kv_head / n_head)`;
  `need_gb = weights_gb + kv_gb` with **no** extra 1.1 multiplier (weights
  are the exact file size, KV is exact; the 10% safety margin lives in the
  `fits_fully` threshold only).
- **Verdict taxonomy** — `fits_fully` (need ≤ total VRAM × (1 − margin)),
  `fits_cpu_offload` (need overflows total VRAM but fits via RAM offload —
  weights ≤ RAM and need ≤ VRAM + RAM; note: "partial GPU offload — expect
  reduced speed"), `likely_too_large`, `cpu_only` (no GPU detected),
  `unknown` (header parse failed — never a wrong verdict).
  `score = min(100, 100 × total_vram/need)`. Verdicts now factor RAM offload:
  scored against total VRAM + `ram_total_mb` instead of free VRAM.
- **Quant grouping** — `group_gguf_quants` collapses a repo's sibling files
  into one entry per quant token (`_QUANT_RE`, e.g. "Q4_K_M"/"Q8_0"/"Q1_0"),
  summing shard sizes (`_SHARD_RE` `-00001-of-00003.gguf`), ignoring
  non-GGUF files, sorted by size ascending; the header-read cap
  (`_QUANT_READ_CAP` = 12) bounds metadata fetches to the cheapest quants.
- **Capability/format pills** — best-effort tag scrape: Vision
  (`image-text-to-text`, `visual-question-answering`, `image-to-text`,
  `any-to-text`, `multimodal`, `vision`, or "vl" in the repo id), Tool Use
  (`function-calling`/`tool-calling`/`tools` tags), Reasoning (tag /
  description / "reason" in id); formats GGUF (any `.gguf` file) and MLX
  (`.mlx`/`-mlx` file). Often empty — the API's tags are thin.
- **Router** (`routers/hf.py`) — `GET /v1/hf/models/{repo_id:path}` (auth via
  `get_current_user`; `context_tokens` 512–262144, default
  `COOKBOOK_CONTEXT_TOKENS`; `{repo_id:path}` because HF ids contain a `/`;
  the static `/hf/models` list route is matched first — no conflict). Detail
  or hardware failure → `404 "model not found or unavailable"`.
- **Deferred** — no download/install: the browser only reads headers over
  Range requests and shows fits. Installing a quant from the UI is out of
  scope for F1.
- **Tests** — `backend/tests/test_hf_detail.py` (stdlib unittest, skip-import
  pattern; 21 cases): quant grouping (singles/shards/non-GGUF/sort), quant
  regex extraction, `estimate_gguf_fit` arithmetic (4.7 GB + GQA 32/8 @ 8k on
  6 GB → `fits_cpu_offload`, 29 GB → `likely_too_large`, meta-None →
  `unknown`, no-GPU → `cpu_only`), the header walker (real layout + bad magic
  + truncation + missing `n_kv_head` default + array-skip: a >4096-element
  scalar array and an uneven string array before the required scalar keys,
  plus a truncated array payload → None), `read_gguf_metadata` (Range
  header sent, failures cached), and `build_hf_model_detail` end-to-end
  (mocked Hub API + mocked header reads → quants with per-quant fit,
  capabilities, formats). Full suite: 184 tests pass (was 179; the +5 are the design-practice
  cleanup test files: `test_agent_runtime`, `test_agent_suggest`, `test_fit_score_gguf`,
  `test_sandbox`, `test_agent_adapter`, `test_file_tools`, `test_workspace_store` — and
  `test_memory_files` gained a guard).
- **Review fix (array offset drift)** — `_read_gguf_value` previously walked
  only the first `min(count, 4096)` array elements and left the offset short
  of the rest of the payload, so any KV key following a large array was read
  from the wrong bytes (a real repo with a >4096-element array misparsed
  `context_length`). The walker now skips the whole payload: fixed-size
  scalar arrays jump `count × elem_size` in one bounds-checked step, string
  arrays walk each element (`u64` length prefix + bytes), and a payload that
  overruns the buffer fails the parse (None) instead of silently truncating.
  Array values are never stored — the fit fields are all scalars/strings.

## Phase 6 — Data & AI platform upgrades (planned — JD-driven, 2026-09-06)

Priority reframe for the 2027 JPMC Data & AI Program application window: the backend's
GenAI half (agents, RAG memory, presets, sandboxed tools, deep research) is already strong;
the "Data &" half — pipelines, predictive modeling, experimentation, governance — is where
the gaps are. Four upgrades, each traceable to a specific JD line, in build order. Nothing
implemented yet.

#### P6.1 — Usage warehouse + analytics pipeline · HIGH
- **Where:** operational Postgres only. Prometheus counters are live aggregates; there is no
  per-request analytical store, and `messages.token_provenance` / `model_used` are written
  but never analyzed.
- **Why (JD):** "design scalable data platforms and pipelines… integrate diverse datasets"
  and "data platforms, pipelines, models, taxonomies, metadata, lineage".
- **Fix:** arq cron ETL from Postgres into a columnar store (DuckDB + Parquet):
  `fact_generation` (user, conversation, model, provider, tokens in/out, latency, cost,
  routed provider, preset, provenance) + `dim_model` / `dim_user` / `dim_provider`.
  Transforms via dbt (lineage graphs + data tests for free). Read-only analytics endpoints
  feed the SPA dashboard (frontend roadmap Phase F1).
- **Status: TODO**

#### P6.2 — Eval harness + prompt experiments · HIGH
- **Where:** no evaluation code anywhere in `backend/app`; prompt quality is unmeasured;
  presets are swapped by feel.
- **Why (JD):** "prompt engineering" (preferred qualification), "run experiments tied to key
  performance indicators", "translate business objectives into testable hypotheses".
- **Fix:** golden-set evals per task type; deterministic checks + LLM-as-judge scoring;
  per-preset scorecards persisted in Postgres; regression gate on prompt/model change; A/B
  mode for two presets reporting uplift with a significance test (bootstrap or t-test).
- **Status: TODO**

#### P6.3 — Learned router (champion/challenger) · MEDIUM
- **Where:** `services/provider_router.py::resolve_role` is a keyword heuristic; routing
  decisions and outcomes are not logged.
- **Why (JD):** "develop predictive models, test hypotheses… apply data, statistics, and
  modeling to solve complex problems".
- **Fix:** log routing features + outcomes (P6.1 provides the store), train a logistic
  regression / GBM "needs cloud?" predictor, ship behind a flag with the keyword rules as
  champion. Decide cost-vs-quality from the warehouse. The label proxy must be defensible
  in an interview (e.g. downstream eval scores from P6.2 — not vibes).
- **Status: TODO**

#### P6.4 — Governance pack · MEDIUM
- **Where:** provenance is partial (`model_used`, `tokens_used`, `token_provenance` exist —
  no preset/template version, no retrieved-memory ids, no tool-call linkage on messages);
  pgvector `memories` accept raw content with no PII screen; no retention/erasure; no
  audit trail for `tool_permissions` grants.
- **Why (JD):** "support data governance, risk management, and data standards… metadata,
  lineage, privacy, and regulatory compliance… implement controls, improve data quality".
- **Fix:** (a) complete the provenance chain so any assistant response is reconstructable;
  (b) PII detection + redaction before embedding into `memories`; (c) retention policy +
  hard-delete cascade (GDPR-style erasure) on users/conversations/memories; (d) audit log
  for tool-permission grants/denials. Item (a) is cheap and lands first — it feeds P6.1's
  fact table.
- **Status: TODO**

Build order: **P6.1 → P6.4(a) → P6.2 → P6.3** (everything consumes the warehouse; the
router's training labels come from evals). The SPA dashboard that consumes P6.1 is tracked
in `docs/frontend-roadmap.md` (Phase F1) and starts only after the polish phase (Phase P0)
there.
