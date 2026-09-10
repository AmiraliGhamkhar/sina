# agents.md — llm-gateway orientation for coding agents

This document tells an agent everything it needs to know before touching this repo: what the project is, what is already built, how the backend is organized, and the invariants you must not break. The frontend is out of scope here.

## Project snapshot

**llm-gateway** is a self-hosted AI inference gateway — a personal "mini-OpenRouter". A FastAPI backend sits in front of multiple AI providers (local LM Studio, cloud OpenRouter) and provides:

- JWT auth (access 60 min + rotating refresh 7 days, stored hashed)
- Provider routing (local vs cloud per request, with a `private` hard override)
- Conversation persistence + branching (monotonic per-conversation message index)
- Semantic memory (pgvector RAG, top-3 similar messages injected as context)
- Parameter presets + SDXL prompt templates
- ComfyUI image generation (incl. user-uploaded custom workflows)
- Agent loop with tool calling (first-party + MCP tools, per-user permissions)
- Deep research as background jobs (arq worker, SSE progress)
- Hardware probe + VRAM fit-score "cookbook"
- Observability: Prometheus metrics + Langfuse traces

**Status:** All core features are built. The backend is complete. The frontend client is in development (out of scope for this document).

## Authoritative docs (read these first)

| File | Purpose |
|---|---|
| `docs/revision.md` | The deep dive: every process, function, and design decision. THE proxy for reading the code. ~32 sections. |
| `docs/backend-roadmap.md` | The CURRENT authoritative issue/roadmap list. Notes that `docs/issues.md` is stale — read this one for what is actually still open. |
| `docs/issues.md` | Older security audit registry (HIGH/MED items). **Partially stale** — verify against code before acting; see `docs/backend-roadmap.md` for the corrected status of each item. |
| `README.md` | High-level overview + env var table. Some details are outdated (e.g. provider ports). |
| `docs/frontend-roadmap.md` | Frontend only — ignore for backend work. |

## Architecture overview

```
                         ┌───────────────────────────┐
                         │         caddy (:80)       │
                         │  serves frontend/dist SPA │
                         │  strips /api prefix       │
                         │  proxies /api/* → backend │
                         └────────────┬──────────────┘
                                      │ HTTP
                 ┌────────────────────▼────────────────────┐
                 │              backend (:8000)             │
                 │  FastAPI — app/main.py                    │
                 │  RateLimitMiddleware → CORS → routers     │
                 │  routers/ (11): auth chat convo presets   │
                 │    templates images models workflows      │
                 │    agent research hardware                │
                 │  services/: router convo memory template  │
                 │    comfy agent research search hardware   │
                 │    fit_score mcp_client tools/            │
                 └──┬────────┬──────────┬──────────────┬────┘
                    │        │          │              │
              ┌─────▼──┐ ┌──▼────┐ ┌────▼────┐   ┌─────▼─────┐
              │ postgres│ │ redis │ │  arq    │   │  external │
              │ pg16 +  │ │ :6379 │ │ worker  │   │  providers│
              │ pgvector│ │       │ │ (same   │   └───────────┘
              └────────┘ └───────┘ │  build)  │   LM Studio :1234
                                   └─────────┘   (chat, rewrite, embed)
                                                  OpenRouter (cloud)
                                                  ComfyUI :8188 (images)
                                                  SearXNG (opt-in search)
```

**Docker services** (`docker-compose.yml`): `postgres` (pgvector/pgvector:pg16), `redis` (redis:7-alpine), `backend` (build ./backend target `dev`, host port 2727→8000), `worker` (same build, command `arq app.worker.WorkerSettings`), `searxng` (opt-in via `--profile search`), `prometheus`, `grafana`, `caddy` (caddy:2 on :80).

Key infra facts:
- AI providers (LM Studio, ComfyUI) run on the HOST, reached from containers via `extra_hosts: host.docker.internal:host-gateway`.
- API keys for providers are Docker secrets mounted under `/run/secrets/` from `./secrets/*.txt` (read via `get_secret()` in `core/config.py`), NOT env vars.
- The backend gets `gpus: all` (for the hardware probe; degrades gracefully without it).
- Caddy is the only public ingress; the backend port 2727 is also published (a known issue, see roadmap S2).

## Backend layout

```
backend/
  Dockerfile               # base + dev target (--reload) + prod target
  requirements.txt
  alembic.ini
  alembic/                 # 13 migrations (see Data model)
  app/
    main.py                # FastAPI entry: lifespan (redis warm + MCP startup), CORS,
                           # RateLimitMiddleware, AppError handler, /health, /metrics
    db.py                  # async engine (pool sizing from config), AsyncSessionLocal, get_db
    worker.py              # arq bootstrap: WorkerSettings + run_research job
    core/
      config.py            # pydantic Settings singleton + get_secret() (Docker secrets)
      security.py          # bcrypt hashing, JWT create/decode, get_current_user
      redis.py             # lazy Redis pool
      queue.py             # lazy arq enqueue pool (API only enqueues)
      metrics.py           # Prometheus counters/histograms + Langfuse traces
      exceptions.py        # AppError / NotFoundError / ForbiddenError
      background.py        # spawn() for off-response-path work
    middleware/
      ratelimit.py         # Redis sliding-window, per-user/per-IP buckets
    models/                # SQLAlchemy models (10 tables)
    routers/               # auth, chat, convo, presets, templates, images,
                           # models, workflows, agent, research, hardware
    services/
      router.py            # ChatRequest schema + provider routing engine
      convo.py             # conversation find-or-create, history, atomic save, recall regex
      memory.py            # embeddings + pgvector insert/retrieve (graceful degradation)
      template.py          # SDXL prompt rewriting (LM Studio load/use/unload)
      comfy.py             # ComfyUI workflow injection + job status
      agent.py             # the tool-calling agent loop (structured SSE)
      research.py          # deep-research orchestrator (run by worker)
      search.py            # SearXNG/DDG search + fetch_page with SSRF guard
      hardware.py          # pynvml / nvidia-smi GPU probe
      fit_score.py         # VRAM fit-scoring of the local model catalog
      mcp_client.py        # connects MCP servers at startup, registers their tools
      tools/               # registry.py + first-party tools (recall, web_search, fetch_page)
```

## The three request shapes

Every backend interaction is one of these — knowing which is which makes any endpoint predictable:

1. **Plain JSON request/response** — CRUD (conversations, presets, templates, workflows, permissions), model listings, hardware probe.
2. **SSE stream held open for the work duration** — `/v1/chat/completions` (plain token stream) and `/v1/agent/chat` (structured JSON events). The HTTP request stays open while the model generates.
3. **Fire-and-forget job + status/stream endpoints** — image generation (ComfyUI's own queue, polled) and deep research (arq worker; polled or streamed via Redis pub/sub → SSE). Used when work outlives a sensible HTTP request.

The SSE wire formats are intentionally different per route — do NOT unify them without a coordinated frontend change:
- chat: `data: <raw token text>\n\n`, `data: [ERROR] ...\n\n`, `data: [DONE]\n\n`
- agent: one JSON object per `data:` line — `{"type":"tool_call"|"tool_result"|"token"|"error"|"done", ...}`
- research: `{"type":"progress"|"done"|"error", ...}` relayed from Redis pub/sub

## Chat request lifecycle (the core path)

```
POST /api/v1/chat/completions   (Caddy strips /api)
→ RateLimitMiddleware           (Redis sliding window; skip for /health /metrics /docs)
→ get_current_user              (JWT decode → user_id str)
→ conversation()                (find-or-create; ownership check; auto-title)
→ load_history()                (last 30 messages + top-3 semantic memories as system ctx)
→ load_preset()                 (chosen preset → "Default" → DEFAULT_* constants)
→ detect_recall_request()       (regex; inject verbatim transcript if hit)
→ get_provider()                (routing rules below)
→ client.chat.completions.create(stream=True)
→ tokens streamed back as SSE
→ save_messages()               (SELECT … FOR UPDATE, atomic index allocation)
→ store_exchange_memories()     (embed + insert pgvector; best-effort, off-path via spawn)
→ record_metrics()              (Prometheus + Langfuse; off-path via spawn)
```

The DB connection is CLOSED before streaming begins (chat path) to free the pool; a fresh session is opened for the final save. The agent path currently holds its connection for the whole run — a known issue (roadmap R1).

## Provider routing rules (services/router.py)

Top-to-bottom, first match wins (`match/case`):

1. `private: true` in the request → always local (privacy hard override)
2. `provider: "local"` → local
3. `provider: "openrouter"` → openrouter
4. model name contains `/` (e.g. `openrouter/owl-alpha`) → openrouter
5. coding keywords in the last message (script, code, function, debug, python, c++, javascript …) → openrouter
6. more than 80 messages in the conversation → openrouter (longer context)
7. default → local

Model fallback for local: `request.model` → `LM_CHAT_MODEL` → `LM_DEFAULT_MODEL`. One OpenAI client class is used for both providers (LM Studio and OpenRouter both speak the OpenAI wire protocol) — routing is just swapping `base_url` + key.

**Provider-specific parameter asymmetry (critical):**
- Sampling params `top_k`, `min_p`, `repeat_penalty` are NOT OpenAI spec — LM Studio accepts them via `extra_body`; OpenRouter would reject them. Only send them on the local path.
- `stream_options={"include_usage": True}` is cloud-only — some LM Studio builds stop streaming token-by-token when it's present. Local usage falls back to counting streamed chunks (prompt tokens reported as 0).

## Auth & security model

- Passwords: bcrypt via passlib. Login is timing-safe: a `DUMMY_PASSWORD_HASH` bcrypt verification runs even when the email doesn't exist.
- Tokens: HS256 JWTs. Access = 60 min, `type: "access"`. Refresh = 7 days, `type: "refresh"`, includes a `jti` nonce.
- Refresh tokens are stored in the DB **only as SHA-256 hashes** (`token_hash`, unique, indexed) — never the JWT itself. SHA-256 (not bcrypt) because tokens are high-entropy and the lookup must be a plain indexed equality.
- `/auth/refresh` ROTATES the token (delete old row + insert new) and checks the DB row's `expires_at` independently of the JWT's `exp`.
- Register creates user + default preset + default SDXL template in ONE transaction (IntegrityError catch handles races).
- `get_current_user` rejects any token where `type != "access"` (a refresh token can't be replayed as an access token).
- CORS: `allow_credentials=False`, auth via `Authorization: Bearer` only — never cookies.
- Rate limiter: valid access token → `rate:user:{id}` @ 30/min; anonymous auth endpoints → `rate:auth:{ip}` @ 10/min; anonymous others → `rate:ip:{ip}` @ 30/min. Fail-open if Redis is down (policy choice).
- Domain errors (`AppError` subclasses) are raised in services and translated to HTTP in one handler at the boundary. Inside SSE generators the global handler doesn't run (headers already sent) — those paths catch `AppError` themselves and emit an SSE `error` event.

## Data model

10 tables in `backend/app/models/`, all FKs to users/conversations are `ondelete="CASCADE"` (branch FKs use `SET NULL`):

| Table | Purpose |
|---|---|
| `users` | email (unique, indexed), hashed_password, is_active, last_active |
| `refresh_tokens` | token_hash (unique, indexed), user_id FK, expires_at |
| `conversations` | user_id FK, title, token_count, parent_id + branched_from_message_id (branch lineage) |
| `messages` | conversation_id FK, role, content, **index** (monotonic int), model_used, tokens_used |
| `memories` | conversation_id FK, role, content, **embedding Vector(768)** — pgvector RAG store |
| `presets` | user_id FK, system_prompt, temperature, token_limit, stop_strings ARRAY, top_k/top_p/min_p/repeat_penalty |
| `prompt_templates` | user_id FK, name, description, structure (SDXL category order) |
| `workflows` | user_id FK, graph JSONB, param_map JSONB (ComfyUI API-format graphs) |
| `tool_permissions` | user_id FK, tool_name, allowed, UNIQUE(user_id, tool_name) |
| `research_jobs` | user_id FK, query, provider, model, status, stage, progress, result, sources JSONB, error |

Migrations: 13 Alembic revisions in `backend/alembic/versions/`. `env.py` imports every model module and strips `+asyncpg` from the URL (Alembic runs sync).

## Critical invariants & conventions (do not break these)

1. **`user_id` flows as a `str` while DB columns are UUIDs** — always compare `str(row.user_id) != str(user_id)`. This pattern is everywhere; keep it.
2. **Naive UTC timestamps everywhere** (`datetime.utcnow`). Do NOT mix in tz-aware datetimes.
3. **Message `index` is monotonic per conversation: user = k, assistant = k+1.** "Last n exchanges" is pure arithmetic on this invariant. `save_messages` uses `SELECT … FOR UPDATE` on the conversation row to serialize concurrent index allocation. Indices are never rebalanced (gaps are fine).
4. **Memory is auxiliary — no memory failure may fail a chat turn.** Every memory/embedding function degrades gracefully (returns `None`/`[]`, logs a warning). Note: a failed SQL statement poisons the asyncpg transaction — a `db.rollback()` is required before the same session can be reused.
5. **Default preset constants are the single source of truth** (in `models/presets.py`): used by column defaults, API schema defaults, chat fallbacks, and registration seeding. Change them once, everywhere.
6. **SSE formats are per-route contracts.** Don't change the event schema of an existing stream without a coordinated frontend change. New richer streams must be NEW endpoints (this is why `/agent/chat` is separate from `/chat/completions`).
7. **ComfyUI graphs are deep-copied before parameter injection** (`copy.deepcopy`) — never mutate a workflow dict in place across concurrent requests.
8. **Ownership checks return 404 for "not found" and 403 for "someone else's".** (Except where a 403/404 distinction itself leaks — research jobs 404 for both.)
9. **Background work goes through `core/background.spawn()`** so it doesn't run on the request's DB session or block the response.
10. **The worker does not hot-reload** (arq). Restart the worker container after changing research code.
11. **OpenRouter is OPTIONAL** (local-only is the goal). The key is lazily read via
    `core.config.get_openrouter_api_key()` (non-raising, returns `None` when unset); it is no
    longer fetched at module import, so the backend boots with no OpenRouter key (roadmap S1,
    DONE). Every OpenRouter call site guards on a `None` key and degrades.

## Documentation convention

Every fix or feature must be documented in the same commit as the code — no code-only
changes. The checklist:

- **`CHANGELOG.md`** (repo root) — add a dated entry under the current section (or start a
  new one) summarizing the change in one line.
- **`docs/backend-roadmap.md`** — update the issue item's status to DONE with a one-line
  summary; add/refresh a "Security fixes implemented (...)" section when the change is a
  security fix. Fix stale lines elsewhere that describe the old behavior.
- **`README.md`** — new/renamed env vars go in the Environment Variables table; new
  endpoints go in the API Endpoints tables; behavior notes (e.g. "Locking down signups")
  go near the flow they affect.
- **`docs/frontend-roadmap.md`** — add a note in the "Backend contract additions" section
  when a backend change affects the API contract the SPA/mobile app consumes (new fields,
  changed status codes, response-shape changes, new endpoints).

## Agents, MCP, and deep research

- **Agent loop** (`services/agent.py`): model emits tool calls → backend executes via `tools/registry.py` → results appended as `role:"tool"` messages → repeat until the model answers, the iteration cap (`AGENT_MAX_ITERATIONS`=6) is hit, or the token budget (`AGENT_TOKEN_BUDGET`=24000) is spent. Tool failures return as strings to the model (the run survives). Streamed as structured JSON SSE.
- **Tools**: first-party (`recall_recent_exchanges`, `web_search`, `fetch_page`) are allowed by default; MCP tools (`mcp_<server>_<tool>`) are deny-by-default. Per-user grants/denials live in `tool_permissions` (no row = default policy).
- **MCP** (`services/mcp_client.py`): connects to servers configured in `MCP_SERVERS` (JSON list env var) at app startup inside the FastAPI lifespan (anyio cancel-scope requirement). Broken servers log a warning and are skipped.
- **Deep research** (`services/research.py`): run by the arq worker. plan → search (SearXNG or DDG) → read top pages → synthesize with `[n]` citations. Progress published to Redis pub/sub `research:{job_id}`, streamed via `/research/{id}/stream`. Cancellation via `research:cancel:{job_id}` flag.
- **Search safety**: `fetch_page` resolves and validates every host (initial + each redirect hop, `follow_redirects=False`, `_MAX_REDIRECTS=5`) and refuses private/loopback/link-local/reserved/cloud-metadata IPs (SSRF guard).

## Image generation

```
POST /v1/images/generate  → (optional) rewrite_prompt() → inject_params() into graph
                           → POST {COMFY_URL}/prompt → prompt_id
                           → redis set imgjob:{prompt_id} → user_id (TTL 1h)
GET /v1/images/status/{prompt_id}  → redis ownership check → ComfyUI /history
                           → pending | failed | complete (+ image URLs)
```

ComfyUI has no user concept, so ownership is enforced via the Redis `imgjob:*` map (404 for missing/expired/not-yours — no info leak). `inject_params` auto-detects KSampler/CLIPTextEncode/ResolutionSelector/*LatentImage nodes; a workflow's explicit `param_map` overrides auto-detection. Workflows must be ComfyUI API-format (`{node_id: {class_type, inputs}}`) — the UI format is rejected with a 422.

## Config & environment (core/config.py)

Key settings: `LM_URL` / `LM_DEFAULT_MODEL` / `LM_CHAT_MODEL` (LM Studio), `LM_EMBED_MODEL` (768-dim embeddings), `COMFY_URL`, `OPENROUTER_DEFAULT_MODEL`, `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ALGORITHM` (HS256), token expiries, rate limits, `ALLOWED_ORIGINS`, agent/research tunables, `MCP_SERVERS`, `SEARXNG_URL`, `ENV` (production disables /docs), `DEBUG` (echoes all SQL — never in prod). Provider keys come from Docker secrets via `get_secret()`.

## Known issues / what's left (authoritative: docs/backend-roadmap.md)

All priority items from the security sweep and the roadmap's Phase 1–4 are **closed**. Every
finding (S1–S7, R1–R7, D1/D3/D4, M1–M4, plus the working-tree F1/C2, F5–F9, F11 sweeps) has a
commit and a `Status: DONE` line in `docs/backend-roadmap.md`. The never-re-touch list is:
CR-1, CR-2, CR-3/4/5, CR-6, CR-8, CR-9, CR-13, CR-12, HIGH-5, MED-6, MED-1, HIGH-2/4/7/8,
MED-4/5/8/9/10/11, DEV-1..12, S1, S2, S3, S4, S5, S7, R1–R7, D1, D3, D4, R7, M1, M2, M3, M4.

Two deliberate, documented gaps remain:

- **Detection/infra verification:** the sandbox confinement (F1/C2), egress isolation, and the
  "boots with no OpenRouter key" (S1) change have been verified by inspection + offline unit
  tests only — **no docker on this host**. Before flipping `ENABLE_CODE_EXECUTION=true`, run
  `docker compose up --build` and the live checks in `docs/backend-roadmap.md`.
- **Defense-in-depth (deferred):** sandbox can still reach any *public* internet host via NAT.
  Segmentation keeps it off your internal services/host; a domain allowlist would need an
  egress proxy.

Read `docs/backend-roadmap.md` in full before starting any fix — it contains the exact fix plan,
verification steps, and the list of issues.md items that are already fixed (do NOT re-touch the
items listed above).

## How to run & verify

```bash
# full stack (requires LM Studio + ComfyUI running on the host)
docker compose up --build
# health + interactive docs
curl http://localhost:2727/health          # {"status":"ok"}
# docs (dev only): http://localhost:2727/docs

# migrations (host-side, uses DATABASE_URL from .env)
cd backend && alembic upgrade head
# worker (deep research) — separate container; restart after research code changes
arq app.worker.WorkerSettings

# backend unit tests (stdlib unittest, no pytest)
cd backend && python -m unittest discover -s tests -p "test_*.py"
```

## Test conventions (backend)

- **Stdlib `unittest` only** — no pytest dependency. `Discover` from `backend/`:
  `python -m unittest discover -s tests -p "test_*.py"`.
- Tests that need Postgres/asyncpg/pgvector/redis/arq/prometheus/langfuse run in the
  **Docker container** (they have those deps); on a bare host many modules import a chain of
  those optional deps and **skip** cleanly via a `try/except` import guard (the
  `_IMPORT_ERROR` / `setUpClass` skip pattern).
- **Offline tests** (`services/workspace/store.py`, the agent-package/tools/sandbox tests)
  import against stubbed optional deps. Use the shared helper
  `tests/agent_test_stubs.py::import_with_stubs(import_fn)`: it installs lightweight
  `app.db`/`pgvector`/`redis`/`prometheus_client`/`langfuse`/`arq` stubs only during the
  import, then restores the real modules so sibling test files aren't polluted. A test file
  that does this must not leak the stubs globally.
- When you add a test for a module under `app.services`, prefer pushing it to run offline
  (stub the optional deps via `import_with_stubs`) rather than relying on the
  skip-on-host guard, so the coverage actually executes on a dev box.
- Line-range / path references in `docs/frontend-roadmap.md` (e.g. "port from
  `api-client.ts:177-262`") can drift when the file is refactored — re-check them after
  touching the referenced module.

Warnings for agents working in this repo:
- Do NOT commit `.env`, `secrets/`, or anything containing real keys. `frontend/dist` is build output.
- The codebase mixes Python 3.11/3.13 artifacts in `__pycache__`; ignore them.
- Prefer the existing domain-error pattern over raising `HTTPException` from services.
- Preserve the plain-vs-JSON SSE distinction; never change a stream contract unilaterally.
