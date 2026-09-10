# Project Revision Guide — llm-gateway

A deep-dive into every process, every function, and the reasoning behind every design decision — from the foundation up. This document is the proxy for reading the code: if you understand everything here, you understand the project.

> **Last updated:** 2026-06-10 — covers the agent/MCP feature, deep research (async jobs), the hardware cookbook, custom ComfyUI workflows, conversation branching, and the hardening fixes (atomic registration, message-index locking, fail-open rate limiter, mid-stream error recovery).

---

## Table of Contents

1. [Project Foundation — What & Why](#1-project-foundation)
2. [Docker & Infrastructure Setup](#2-docker--infrastructure-setup)
3. [Configuration & Settings](#3-configuration--settings)
4. [Database Layer](#4-database-layer)
5. [Data Models & Migrations](#5-data-models--migrations)
6. [Domain Errors & Application Wiring](#6-domain-errors--application-wiring)
7. [Security Layer (JWT & Password Hashing)](#7-security-layer)
8. [Redis Connection](#8-redis-connection)
9. [Authentication Endpoints](#9-authentication-endpoints)
10. [Provider Routing Engine](#10-provider-routing-engine)
11. [Conversation Service](#11-conversation-service)
12. [Semantic Memory (pgvector RAG)](#12-semantic-memory)
13. [Chat Streaming Endpoint](#13-chat-streaming-endpoint)
14. [Conversation CRUD, Message Ops & Branching](#14-conversation-crud-message-ops--branching)
15. [Presets System](#15-presets-system)
16. [Prompt Templates & SDXL Rewriting](#16-prompt-templates--sdxl-rewriting)
17. [Image Generation & Custom Workflows (ComfyUI)](#17-image-generation--custom-workflows)
18. [Model Listing Endpoints](#18-model-listing-endpoints)
19. [Rate Limiting Middleware](#19-rate-limiting-middleware)
20. [Observability (Metrics & Tracing)](#20-observability)
21. [Agents & MCP Tools](#21-agents--mcp-tools)
22. [Deep Research (Async Job Queue)](#22-deep-research)
23. [Hardware Probe & Cookbook](#23-hardware-probe--cookbook)
24. [Caddy Reverse Proxy](#24-caddy-reverse-proxy)
25. [Frontend Architecture](#25-frontend-architecture)
26. [How All Files Fit Together](#26-how-all-files-fit-together)
27. [Key Design Decisions Summary](#27-key-design-decisions-summary)
28. [Interview Prep: Tough Questions & Answers](#28-interview-prep-tough-questions--answers)
29. [System Design Questions & Answers](#29-system-design-questions--answers)
30. [The Hostile Interview (Round 2)](#30-the-hostile-interview-round-2)
31. [The Hostile Interview (Round 3)](#31-the-hostile-interview-round-3)
32. [Issues & Fixes Log](#32-issues--fixes-log)

---

## 1. Project Foundation

### What is this project?

A **self-hosted AI inference gateway** — a personal mini-OpenRouter. A FastAPI backend sits between a React frontend and multiple AI providers, handling:

- **Authentication** — register/login with JWT access + rotating refresh tokens
- **Provider routing** — decides per-request whether a local model or a cloud model answers
- **Conversation persistence** — every message saved with a monotonic per-conversation index
- **Semantic memory (RAG)** — pgvector embeddings recall relevant past messages
- **Positional recall** — "recall my last 3 messages" detected by regex, answered verbatim
- **Parameter presets** — reusable sampling profiles (temperature, top_k, min_p…)
- **Prompt templates + image generation** — natural language → SDXL tags → ComfyUI, with user-uploadable custom workflows
- **Agents (MCP)** — a tool-calling loop: the model can call `web_search`, `fetch_page`, `recall_recent_exchanges`, and any tool from connected MCP servers, gated by per-user permissions
- **Deep research** — long-running background jobs (plan → search → read → synthesize with citations) on an arq/Redis queue, with live SSE progress
- **Cookbook** — GPU/VRAM probe + heuristic fit-scoring of the local model catalog
- **Observability** — Prometheus metrics, Grafana dashboards, Langfuse LLM traces

### Why build this?

1. **Privacy** — sensitive conversations never leave the local machine (`private: true` hard-routes local)
2. **Cost control** — free local models for everyday chat; cloud only for code/long-context
3. **Flexibility** — own the routing logic, presets, and tool permissions
4. **Ownership** — your data, your infra, your rules

### The three request shapes

Everything the backend does falls into one of three interaction patterns — knowing these makes every endpoint predictable:

1. **Plain JSON request/response** — CRUD on conversations, presets, templates, workflows, permissions; model listings; hardware probe. Standard FastAPI handlers.
2. **SSE stream held open for the duration of the work** — `/v1/chat/completions` (plain token stream) and `/v1/agent/chat` (structured JSON events). The HTTP request stays open while the model generates.
3. **Fire-and-forget background job + status/stream endpoints** — image generation (ComfyUI's own queue, polled) and deep research (arq worker, polled *or* streamed via a Redis pub/sub → SSE bridge). Used when work outlives a sensible HTTP request.

### High-level flow (chat)

```
User message in app
  → POST /api/v1/chat/completions      (Caddy strips /api, proxies to backend)
  → RateLimitMiddleware                (Redis sliding window, per-user)
  → get_current_user                   (JWT decode → user_id)
  → conversation()                     (find-or-create, ownership check)
  → load_history()                     (last 30 messages + top-3 semantic memories)
  → load_preset()                      (chosen preset, or the user's "Default")
  → detect_recall_request()            (regex; if hit, inject verbatim transcript)
  → get_provider()                     (local LM Studio vs OpenRouter)
  → client.chat.completions.create(stream=True)
  → tokens streamed back as SSE        (data: <token>\n\n)
  → save_messages()                    (row-locked, atomic index allocation)
  → store_memory()                     (embed + insert into pgvector, best-effort)
  → record_metrics()                   (Prometheus + Langfuse)
```

---

## 2. Docker & Infrastructure Setup

### File: `docker-compose.yml`

Orchestrates **8 services** (7 always-on + 1 opt-in):

| Service | Image / Build | Purpose |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | All persistent data + vector embeddings. `pgdata` named volume. |
| `redis` | `redis:7-alpine` | Rate limiting, image-job ownership, arq job broker, research pub/sub, cancellation flags. |
| `backend` | `./backend` (target `dev`) | The FastAPI app, host port **2727** → container 8000. |
| `worker` | same build as backend | **Deep-research job worker**: `arq app.worker.WorkerSettings`. Same code volume, env, and secrets as the backend. |
| `searxng` | `searxng/searxng` (profile `search`) | Optional self-hosted metasearch. Start with `docker compose --profile search up` and set `SEARXNG_URL=http://searxng:8080`; otherwise web search falls back to DuckDuckGo scraping. |
| `prometheus` | `prom/prometheus` | Scrapes `backend:8000/metrics` every 15s. |
| `grafana` | `grafana/grafana` | Dashboards on :3000, admin password from `.env`. |
| `caddy` | `caddy:2` | Reverse proxy on :80 — serves the built SPA, proxies `/api/*` and `/view*`. |

Key compose details:

- **Docker secrets** — `ollama_api_key` and `openrouter_api_key` are mounted as files under `/run/secrets/` (from `./secrets/*.txt`), not passed as env vars. Both `backend` and `worker` get them.
- **`extra_hosts: host.docker.internal:host-gateway`** — the AI providers (LM Studio :1234, Ollama :11434, ComfyUI :8188) run on the *host*, not in Docker. This DNS alias lets containers reach them.
- **`gpus: all` on backend** — gives the container GPU access (requires nvidia-container-toolkit) so `pynvml` inside `/v1/hardware` can report real VRAM. Without it, the cookbook degrades to `cpu_only` verdicts.
- **`volumes: ./backend:/app`** + uvicorn `--reload` — code edits on the host hot-reload the server. The worker shares the same volume, but **arq does not hot-reload** — restart the worker after changing research code.
- **Why a separate worker container** — research jobs run for minutes. Running them in the API process would tie up the event loop, die on reload, and couple API deployments to job lifetimes. The worker owns long-running jobs; Redis is the broker between them.

### File: `backend/Dockerfile`

```dockerfile
FROM python:3.11-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

FROM base AS dev          # hot-reload, root user (fine on a local/tailnet box)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS prod         # root user, no reload (the trusted backend must be root
                          # to write any tenant's chmod-700 workspace — F1/C2)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

One base stage, two **targets** (`dev`/`prod`) selected by compose (`build.target: dev`). The same image layers serve both the API (`uvicorn`) and the worker (compose overrides `command: arq app.worker.WorkerSettings`).

### File: `searxng/settings.yml`

Minimal SearXNG config: `use_default_settings: true`, a secret key, limiter off (internal-only service), and crucially `search.formats: [html, json]` — the JSON API is off by default and `services/search.py` consumes `GET /search?q=…&format=json`.

---

## 3. Configuration & Settings

### File: `backend/app/core/config.py`

A single pydantic-settings `Settings` class reads `.env` once at import; the `settings` singleton is imported everywhere. Grouped by feature:

```python
class Settings(BaseSettings):
    LM_URL: str                      # LM Studio base URL (chat + SDXL rewriting)
    LM_DEFAULT_MODEL: str            # rewrite model (Qwen 2.5)
    LM_CHAT_MODEL: str = ""          # chat model; empty => falls back to LM_DEFAULT_MODEL
    COMFY_URL: str = "http://host.docker.internal:8188"

    ENV: str = "dev"                 # "production" disables /docs, /redoc, /openapi.json
    DEBUG: bool = False              # echoes all SQL when True — never in prod

    RATE_LIMIT_PER_MINUTE: int = 30
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    AUTH_RATE_LIMIT_PER_MINUTE: int = 10   # stricter per-IP limit on login/register/refresh

    MAX_HISTORY_MESSAGES: int = 30   # prompt window; older turns reachable via RAG/recall
    ALLOWED_ORIGINS: str = "..."     # comma-separated; parsed by allowed_origins_list property
    OPENROUTER_DEFAULT_MODEL: str = ""

    DATABASE_URL: str
    REDIS_URL: str
    SECRET_KEY: str                  # JWT signing
    ALGORITHM: str                   # HS256
    ACCESS_TOKEN_EXPIRY_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRY_DAYS: int = 7

    LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL

    # ── Agent / MCP tools ──
    AGENT_MAX_ITERATIONS: int = 6    # hard cap on model⇄tool round-trips
    AGENT_TOKEN_BUDGET: int = 24000  # total prompt+completion tokens per agent run
    TOOL_TIMEOUT_SECONDS: int = 30   # per tool execution
    TOOL_RESULT_MAX_CHARS: int = 8000
    WEB_SEARCH_MAX_RESULTS: int = 5
    SEARXNG_URL: str = ""            # empty => DuckDuckGo HTML fallback
    MCP_SERVERS: str = ""            # JSON list of MCP server configs

    # ── Deep research ──
    RESEARCH_MAX_QUERIES: int = 4
    RESEARCH_RESULTS_PER_QUERY: int = 4
    RESEARCH_MAX_SOURCES: int = 6
    RESEARCH_PAGE_MAX_CHARS: int = 6000
    RESEARCH_JOB_TIMEOUT_SECONDS: int = 900

    # ── Ollama (embeddings + cookbook catalog) ──
    OLLAMA_URL: str = "http://host.docker.internal:11434"
    EMBED_MODEL: str = "nomic-embed-text:latest"
    COOKBOOK_CONTEXT_TOKENS: int = 8192
```

### Secrets: `get_secret()` / `get_secret_or_none()`

```python
def get_secret(name: str) -> str:
    path = f"/run/secrets/{name}"
    if os.path.exists(path):
        return open(path).read().strip()
    env_value = os.environ.get(name.upper())   # fallback for host-side runs
    if env_value:
        return env_value
    raise RuntimeError(...)

def get_secret_or_none(name: str) -> str | None:
    # like get_secret() but returns None instead of raising when absent —
    # used for OPTIONAL secrets the app must boot without (roadmap S1)
    ...

def get_openrouter_api_key() -> str | None:
    return get_secret_or_none("openrouter_api_key")
```

**Why files first:** Docker secrets aren't visible in `docker inspect` or process env listings. **Why the env fallback:** host-side Alembic runs, scripts, and tests would otherwise crash at import — the module-level `get_secret()` calls run the moment `config.py` is imported. **Why functions, not module-level constants:** the OpenRouter key is **optional** (S1, DONE) — it is fetched lazily via `get_openrouter_api_key()`, which returns `None` when unset instead of raising, so the backend boots with no key. Every OpenRouter call site guards on that `None`.

**Gotcha that bit us:** `DEBUG=True` makes SQLAlchemy echo every SQL statement — including message content — into logs. It exists for debugging only.

---

## 4. Database Layer

### File: `backend/app/db.py`

```python
DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(DATABASE_URL, echo=settings.DEBUG)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

- **Why async:** LLM calls take seconds to minutes; blocking DB calls would freeze the event loop. asyncpg + async SQLAlchemy keep the server responsive during waits.
- **URL rewrite:** `.env` keeps a plain `postgresql://` URL (Alembic and psql can use it directly); the app swaps in the `+asyncpg` driver.
- **`expire_on_commit=False`:** ORM objects stay usable after `commit()` without re-fetching — important in streaming generators that commit mid-stream and keep using the objects.
- **`get_db`** is the FastAPI dependency: one session per request, closed automatically after the handler (or after the streaming response finishes).
- **The worker** doesn't use `get_db` (no request scope) — `services/research.py` opens its own `AsyncSessionLocal()` context per job.

---

## 5. Data Models & Migrations

### The 11 tables (`backend/app/models/`)

| Table | Key columns | Purpose |
|---|---|---|
| `users` | email (unique, indexed), hashed_password, is_active, last_active | Accounts |
| `refresh_tokens` | **token_hash** (unique, indexed), user_id FK, expires_at | Sessions. Only the SHA-256 hash is stored — never the JWT itself. |
| `conversations` | user_id FK, title, token_count, **parent_id FK→conversations**, **branched_from_message_id FK→messages** | Chat threads. The two branch columns record lineage when a conversation is forked. |
| `messages` | conversation_id FK, role, content, **index** (monotonic int), model_used, tokens_used | The transcript. `index`: user = k, assistant = k+1 — an "exchange" is 2 indices. |
| `memories` | conversation_id FK, role, content, **embedding Vector(768)** | pgvector store for RAG. |
| `presets` | user_id FK, name, system_prompt, temperature, token_limit, stop_strings ARRAY, top_k/top_p/min_p/repeat_penalty | Sampling profiles. Defaults live as module constants (`DEFAULT_TEMPERATURE = 0.8` …) used by the column defaults, the API schema, the chat fallback, and registration seeding — one source of truth. |
| `prompt_templates` | user_id FK, name, description, structure | SDXL prompt category structures. |
| `workflows` | user_id FK, name, description, **graph JSONB**, **param_map JSONB** | User-uploaded ComfyUI API-format graphs + optional explicit parameter mappings. |
| `tool_permissions` | user_id FK, tool_name, allowed bool — **UNIQUE(user_id, tool_name)** | Per-tenant agent-tool grant/deny overrides. No row = default policy. |
| `research_jobs` | user_id FK, query, provider, model, status, stage, progress, result, sources JSONB, error | Deep-research job state. Lifecycle: queued → running → complete/failed/cancelled. |

All FKs to `users`/`conversations` use `ondelete="CASCADE"` — deleting a user wipes everything they own; deleting a conversation wipes its messages and memories. The branch FKs use `SET NULL` (deleting a parent doesn't destroy its branches). All timestamps are **naive UTC** (`datetime.utcnow`) — a deliberate project-wide convention; don't mix in tz-aware datetimes.

### Alembic (`backend/alembic/`)

`env.py` imports **every** model module so autogenerate sees the full metadata, and strips `+asyncpg` from the URL (Alembic runs sync). The migration chain, in order:

| # | Revision | Change |
|---|---|---|
| 1 | `dc602996fd85` | Initial schema: users, conversations, messages, refresh_tokens |
| 2 | `b65222cd7976` | `users.last_active` |
| 3 | `4b602081a1e1` | `memories` table + pgvector |
| 4 | `e0c9b829531e` | `messages.index` |
| 5 | `2db01124b25f` | `presets` |
| 6 | `129e95bdca11` | `prompt_templates` |
| 7 | `f71508e02cd3` | Hash refresh tokens (`token_hash`) |
| 8 | `3568a2a007d2` | `workflows` |
| 9 | `c999d3487714` | **Empty** (autogenerate produced nothing — see below) |
| 10 | `7d41aa30c5f2` | `tool_permissions` |
| 11 | `3fa8c20b911e` | `research_jobs` |
| 12 | `9b3d6f1c2a47` | `conversations.parent_id` + `branched_from_message_id` (the branch columns) |

**The cautionary tale of #9 and #12:** the branch columns were added to the `Conversation` *model*, but the migration generated at the time (#9) came out empty — so the model and the database silently diverged. Every `SELECT` on conversations then failed against a fresh database with `UndefinedColumnError: conversations.parent_id`. Migration #12 retro-fixed it. **Lesson: always diff an autogenerated migration against the model change you just made — an empty `upgrade()` after a model edit is a red flag.**

---

## 6. Domain Errors & Application Wiring

### File: `backend/app/core/exceptions.py`

```python
class AppError(Exception):
    status_code: int = 500
    detail: str = "internal server error"

class NotFoundError(AppError):  status_code = 404
class ForbiddenError(AppError): status_code = 403
```

**Why this exists:** services (e.g. `services/convo.py`, `services/comfy.py`) used to raise `fastapi.HTTPException` directly, which couples business logic to the web framework. With domain errors, the same service functions are callable from the **arq worker**, a CLI, or tests. One handler in `main.py` translates them to HTTP at the boundary, matching FastAPI's default `{"detail": ...}` shape so clients can't tell the difference.

**The streaming caveat:** the global handler only works for exceptions raised *before* the response starts. Inside an SSE generator the headers are already sent — so the agent loop catches `AppError` itself and converts it to an SSE `error` event instead.

### File: `backend/app/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_redis()            # warm the pool
    await mcp_manager.startup()  # connect MCP servers, register their tools
    yield
    await mcp_manager.shutdown()
    await close_queue()          # arq enqueue pool
    await close_redis()

app = FastAPI(docs_url=... if ENV != "production" else None, lifespan=lifespan)
Instrumentator().instrument(app).expose(app)        # /metrics
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins_list,
                   allow_credentials=False)         # bearer tokens, no cookies
app.add_middleware(RateLimitMiddleware)
# AppError → JSONResponse handler
# routers: chat, models, auth(/auth), convo, presets, templates, images,
#          workflows, agent, research, hardware  (all under /v1 except auth)
```

**Why `lifespan` instead of `@app.on_event`:** besides `on_event` being deprecated, the MCP SDK's stdio/SSE transports use anyio cancel scopes that must be **entered and exited in the same task**. Starlette runs the whole lifespan generator in one task; the old startup/shutdown events ran in different tasks and would crash MCP teardown.

**Middleware order matters:** middleware added *last* runs *first*, so the request path is RateLimit → CORS → router. Both run before any handler.

**CORS with `allow_credentials=False`:** auth is via the `Authorization: Bearer` header, never cookies — so the browser never needs `withCredentials`, and the CORS surface stays simple.

---

## 7. Security Layer

### File: `backend/app/core/security.py`

#### Password hashing — bcrypt via passlib

```python
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
hash_password(p)         # registration
verify_password(p, hash) # login
```

Bcrypt is slow by design (brute-force resistance) and salts every hash (same password → different hashes).

#### `DUMMY_PASSWORD_HASH` — timing-safe login

```python
DUMMY_PASSWORD_HASH = pwd_context.hash("not-a-real-password")
```

When a login email doesn't exist, the login handler still runs one full bcrypt verification against this dummy hash. Without it, "no such user" returns in microseconds while "wrong password" takes ~100ms of bcrypt — a timing oracle that reveals which emails are registered.

#### Token creation

```python
create_access_token(user_id)            # {"sub", "exp": +60min, "type": "access"}
create_refresh_token(user_id, expires)  # {"sub", "exp": +7d, "type": "refresh", "jti": uuid4().hex}
```

Both are HS256-signed JWTs. The **`jti` nonce** guarantees every refresh token is unique: two tokens minted in the same second for the same user would otherwise be byte-identical, and their SHA-256 hashes would collide on the DB's unique constraint.

#### `hash_token()` — SHA-256, not bcrypt

Refresh tokens are stored in the DB **only as SHA-256 hashes**. SHA-256 (not bcrypt) because: (a) tokens are high-entropy random strings, not guessable passwords, so slow hashing buys nothing; (b) SHA-256 is deterministic, so the DB lookup is a simple indexed equality on `token_hash` — bcrypt's per-hash salt makes lookup-by-hash impossible.

#### `get_current_user` — the auth dependency

Decodes the bearer JWT, rejects anything where `type != "access"` (so a long-lived refresh token can't be replayed as an access token), and returns `user_id` as a **string**. Every protected endpoint takes `user_id = Depends(get_current_user)`.

**Convention note:** `user_id` flows through the app as a `str`, while DB columns are UUIDs — hence the ubiquitous `str(row.user_id) != str(user_id)` ownership comparisons.

---

## 8. Redis Connection

### File: `backend/app/core/redis.py`

A lazily-created singleton connection pool (`get_redis()` / `close_redis()`), `decode_responses=True` so values come back as `str` not `bytes`.

**Everything Redis does in this system:**

| Use | Keys | Section |
|---|---|---|
| Rate limiting | `rate:user:{id}`, `rate:ip:{ip}`, `rate:auth:{ip}` (sorted sets) | §19 |
| Image job ownership | `imgjob:{prompt_id}` → user_id (TTL 1h) | §17 |
| Research job broker | arq's internal queues | §22 |
| Research live progress | pub/sub channel `research:{job_id}` | §22 |
| Research cancellation | `research:cancel:{job_id}` flag (TTL 1h) | §22 |

### File: `backend/app/core/queue.py`

A second, separate lazy singleton — the **arq enqueue pool** (`get_queue()` / `close_queue()`). arq needs its own `ArqRedis` client type for `enqueue_job()`; the API process only enqueues, the worker process consumes.

---

## 9. Authentication Endpoints

### File: `backend/app/routers/auth.py`

#### Input validation (registration only)

```python
class UserCreate(BaseModel):
    email: str      # regex-validated: r"[^@\s]+@[^@\s]+\.[^@\s]+"
    password: str   # min 8 chars

class UserLogin(BaseModel):   # NO validators
    email: str
    password: str
```

**Why two models:** validation applies to *new* credentials only. If the password validator ran at login, an existing account with a 6-char password could never log in again. (Regex instead of `EmailStr` to avoid the `email-validator` dependency.)

#### `POST /auth/register` — single transaction

```python
# fast-path duplicate check (friendly error) …
try:
    db.add(new_user)
    await db.flush()              # assigns new_user.id; surfaces duplicate email
    db.add(Preset(... name="Default" ...))          # default sampling profile
    db.add(PromptTemplate(... "Default SDXL Template" ...))
    await db.commit()             # all three rows land together
except IntegrityError:
    await db.rollback()
    raise HTTPException(400, "user already exists")
```

Three things in **one transaction**: the user, their default preset, their default SDXL template — a failure can't leave a half-initialized account. The `IntegrityError` catch covers the race two concurrent registrations can win past the select-check (email is `unique=True` in the DB, the constraint is the real guard). Defaults are seeded so chat and image generation work out of the box.

#### `POST /auth/login` — timing-safe

```python
hashed = user.hashed_password if user is not None else security.DUMMY_PASSWORD_HASH
password_ok = security.verify_password(user_data.password, hashed)
if user is not None and password_ok:
    # mint refresh JWT → store ONLY its SHA-256 hash in refresh_tokens
    # mint access JWT → return both
raise HTTPException(401, "invalid credentials")
```

Exactly **one** bcrypt verification happens on every login attempt, real hash or dummy — both code paths cost the same time. Note the structure: this is cleaner than verifying conditionally and adding a compensating dummy check.

#### `POST /auth/refresh` — rotation by delete + insert

1. Hash the presented token, look it up by `token_hash` (indexed, unique).
2. Check the **DB row's** `expires_at` independently of the JWT's own `exp` (a revoked-early policy can shorten lifetimes server-side); expired rows are deleted on sight.
3. Verify the JWT signature and `type == "refresh"`.
4. **Rotate: `db.delete(token_record)` and insert a brand-new row** with a fresh JWT/hash/expiry.
5. Return a new access + refresh pair.

**Why rotation:** a stolen refresh token works at most once. After either party uses it, the old hash is gone; whoever presents it second gets a 401. The legitimate user re-authenticates, which orphans the attacker's chain.

#### `POST /auth/logout`

Requires a valid *access* token (`get_current_user`) plus the refresh token in the body; deletes the matching `(token_hash, user_id)` row. The user_id filter stops user A from revoking user B's sessions.

---

## 10. Provider Routing Engine

### File: `backend/app/services/router.py`

#### Request schema

```python
class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None   # None => create new conversation
    preset_id: Optional[str] = None
    messages: List[ChatMessage] = Field(min_length=1)
    model: str = "auto"                     # "auto" => provider default
    stream: bool = True
    provider: Provider = Provider.auto      # auto | local | openrouter
    private: bool = False                   # hard local override
```

This same schema is reused by the **agent** route (`/v1/agent/chat`) — one request shape for both chat paths.

#### Clients

`get_local_client()` → `AsyncOpenAI(base_url=f"{LM_URL}/v1", api_key="LM-STUDIO")` and `get_openrouter_client()` → `AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)`. **One client class for both providers** because LM Studio and OpenRouter both speak the OpenAI wire protocol — routing is just swapping `base_url` + key.

#### `get_provider(request)` — the rules, top-to-bottom (`match/case`)

```python
local_model = request.model if request.model != 'auto' else (LM_CHAT_MODEL or LM_DEFAULT_MODEL)
or_model    = request.model if request.model != 'auto' else OPENROUTER_DEFAULT_MODEL

1. private=True                  → local        # privacy hard override
2. provider=local                → local        # explicit choice
3. provider=openrouter           → openrouter   # explicit choice
4. code keyword in last message  → openrouter   # "script","code","debug","python",…
5. len(messages) > 80            → openrouter   # long conversations need big context
6. default                      → local         # cost + privacy default
```

The first match wins — `match/case` makes the priority order visually explicit. The model fallback chain for local is `request.model` → `LM_CHAT_MODEL` → `LM_DEFAULT_MODEL` (the last being the small rewrite model, so set `LM_CHAT_MODEL` for real chat).

**Honest limitations** (also interview fodder): keyword routing is crude — "my code for the gym locker" routes to the cloud; the message-count threshold (80) is hardcoded; adding a 4th provider means another `case`. The refactor trigger is "provider #4" — a config-driven routing table (see §29 M1).

---

## 11. Conversation Service

### File: `backend/app/services/convo.py`

#### `conversation(request, user_id, db) → str`

Find-or-create. New conversations are auto-titled from the first 6 words of the message. Existing IDs are ownership-checked — `NotFoundError` (404) if absent, `ForbiddenError` (403) if another user's. These are **domain errors**, not HTTPExceptions (see §6).

#### Positional recall — `detect_recall_request(text) → int | None`

```python
_RECALL_RE = re.compile(
    r"\b(?:recall|remember|repeat|bring up|go back to|what (?:were|was|did))\b"
    r"[^.?!]*?\b(?:last|previous|past|recent)\s+"
    r"(\d{1,2}|one|two|…|couple|few|several)\s+"
    r"(?:of\s+)?(?:my\s+|our\s+)?(?:messages?|exchanges?|turns?|…)\b", re.IGNORECASE)
```

Detects "recall my last 3 exchanges"-style asks, converts number words via `_NUM_WORDS` ("couple"→2, "few"→3, "several"→5), clamps to `MAX_RECALL_EXCHANGES = 20`, returns `n` or `None`. The chat endpoint uses the result to inject a verbatim transcript (see §13). **Why regex and not the LLM:** zero latency, zero cost, deterministic — and in agent mode (§21) the model gets a real `recall_recent_exchanges` *tool* instead, making the regex a chat-mode-only heuristic.

#### `get_last_exchanges(conversation_id, n, db) → list[dict]`

```python
max_index = SELECT max(index) ...
cutoff = max_index - 2*n
SELECT ... WHERE index > cutoff ORDER BY index ASC
```

The monotonic index invariant (user = k, assistant = k+1) makes "last n exchanges" pure arithmetic — no datetime heuristics. Returns `[{"role", "content"}, …]` oldest-first.

#### `load_history(conversation_id, query, db) → list`

1. Load the most recent `MAX_HISTORY_MESSAGES` (30) messages — `ORDER BY index DESC LIMIT 30`, then reversed back to chronological. The window stops the prompt growing unboundedly; older turns remain reachable via semantic memory and positional recall.
2. Prepend a system message of the **top-3 semantically similar memories** (`get_memory_context`, §12) if any.

#### `save_messages(...)` — atomic index allocation

```python
# Lock the conversation row for the duration of the transaction
convo = await db.execute(
    select(Conversation).where(Conversation.id == cid).with_for_update())
max_index = SELECT max(index) WHERE conversation_id = cid
db.add(Message(role="user",      index=max_index+1, ...))
db.add(Message(role="assistant", index=max_index+2, tokens_used=..., model_used=...))
convo.token_count += token_count
await db.commit()    # one commit: messages + token count land together, lock releases
# then best-effort: store_memory() for both messages
```

**Why `with_for_update()`:** two concurrent sends in the same conversation would otherwise both read the same `max(index)` and allocate **colliding indices**, corrupting the exchange invariant that recall depends on. The row lock serializes them; the single commit means there's no window where messages exist but the token count doesn't.

---

## 12. Semantic Memory

### File: `backend/app/services/memory.py`

The RAG pipeline. **Design stance: memory is auxiliary — no memory failure may ever fail a chat turn.** Every function degrades gracefully.

#### `get_embedding(content) → list[float] | None`

POSTs to Ollama (`{OLLAMA_URL}/api/embeddings`, model `EMBED_MODEL` = nomic-embed-text, 768-dim). On any HTTP/parse error: logs a warning, returns `None`.

#### `store_memory(...)`

Embeds and inserts a `Memory` row. If the embedding came back `None` (Ollama down), it silently skips — the message itself is already saved in `messages`; only its *searchability* is lost.

#### `retrieve_memories(conversation_id, query, db) → list`

```python
sa_text("SELECT content, role, created_at FROM memories "
        "WHERE conversation_id = :cid "
        "ORDER BY embedding <=> CAST(:emb AS vector) LIMIT 3")
# wrapped in try/except → on failure: logger.warning, db.rollback(), return []
```

- `<=>` is pgvector's **cosine distance** operator; lowest distance = most similar.
- The **explicit `CAST(:emb AS vector)`** matters with asyncpg: the parameter arrives as text, and without the cast the `vector <=> text` operator doesn't exist.
- The **`db.rollback()` in the except** is subtle but critical: a failed statement poisons the transaction (`InFailedSQLTransaction`) — without the rollback, every later query *on the same request's session* (history load, message save) would also fail. The rollback restores the session so the chat turn proceeds without memory.

**Known scaling limits (deliberate for now):** no vector index (full scan is fine at <10k rows; add IVFFlat/HNSW when queries pass ~50ms) and no distance threshold (the top-3 are injected even if barely relevant).

---

## 13. Chat Streaming Endpoint

### File: `backend/app/routers/chat.py` — the core endpoint

#### `load_preset(preset_id, user_id, db)`

Loads the requested preset **scoped to the user** (someone else's preset_id just misses). With no `preset_id`, falls back to the user's `"Default"` preset (seeded at registration, `ORDER BY created_at ASC LIMIT 1`). Returns `None` if even that is gone — every parameter read then falls back to the `DEFAULT_*` constants. Three-level fallback: chosen preset → Default preset → hardcoded constants.

#### `stream_tokens(request, user_id, db)` — the async generator

**Phase 1 — prompt assembly:**
```python
conversation_id = await conversation(request, user_id, db)
history = await load_history(...)             # 30-msg window + memory context
messages = history + [current_user_message]

system_prefix = []
if preset.system_prompt:           system_prefix.append(system msg)
if (n := detect_recall_request(...)):
    exchanges = await get_last_exchanges(conversation_id, n, db)
    system_prefix.append("The user asked to recall the last N exchange(s)…\n<verbatim transcript>")
messages = system_prefix + messages
```

**Phase 2 — provider-specific parameters:**
```python
client, model = await get_provider(request)
is_cloud = "openrouter" in str(client.base_url).lower()
if is_cloud:
    extra_params = {"stream_options": {"include_usage": True}}   # accurate usage in final chunk
else:
    extra_params = {"extra_body": {"top_k": …, "min_p": …, "repeat_penalty": …}}
```

Two asymmetries worth knowing cold:
- **Sampling params** (`top_k`, `min_p`, `repeat_penalty`) are not part of the OpenAI spec — LM Studio accepts them via `extra_body`; OpenRouter would reject them.
- **`stream_options` is cloud-only** because some LM Studio builds stop streaming token-by-token when it's present. Local usage falls back to counting streamed chunks (≈ completion tokens, prompt tokens unknown → 0).

**Phase 3 — the call and the error envelope:**
```python
try:
    response = await client.chat.completions.create(model=model, messages=messages,
        stream=True, temperature=…, max_tokens=…, stop=…, top_p=…, **extra_params)
except APIError:    yield "data: [ERROR] upstream model provider error\n\n"; yield "data: [DONE]\n\n"; return
except Exception:   yield "data: [ERROR] internal server error\n\n";        yield "data: [DONE]\n\n"; return
```

**Phase 4 — streaming with mid-stream protection:**
```python
stream_error = False
try:
    async for chunk in response:
        if chunk.usage: usage = chunk.usage          # final usage-only chunk (cloud)
        content = chunk.choices[0].delta.content
        if content:
            full_response += content; token_count += 1
            yield f"data: {content}\n\n"
except Exception:
    stream_error = True      # provider died mid-generation
```

**Phase 5 — persist and close:**
```python
if full_response or not stream_error:        # nothing streamed + error => nothing to save
    await save_messages(...)                 # partial responses ARE saved
    record_metrics(...)
if stream_error: yield "data: [ERROR] stream interrupted\n\n"
yield "data: [DONE]\n\n"
```

If the provider dies after 200 tokens, those 200 tokens are saved as the assistant message, the client gets `[ERROR]` + `[DONE]`, and the conversation history stays consistent. (The remaining unhandled case: the *client* disconnecting cancels the generator and skips the save.)

#### The SSE wire format (chat route)

```
data: <raw token text>\n\n          ← one event per token, NOT JSON
data: [ERROR] <message>\n\n
data: [DONE]\n\n
```

This is the **plain-token contract** the frontend parser (`api-client.ts`) understands. Any richer event schema needs a coordinated frontend change — which is exactly why the agent route (§21) is a *separate* endpoint with its own JSON-event schema rather than a change to this one.

#### The route

```python
@router.post("/chat/completions")
... return StreamingResponse(stream_tokens(...), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

`X-Accel-Buffering: no` disables proxy buffering (nginx convention); Caddy is handled separately via `flush_interval -1` in the Caddyfile. Both exist so tokens reach the browser as they're generated, not in one buffered lump.

---

## 14. Conversation CRUD, Message Ops & Branching

### File: `backend/app/routers/convo.py`

Every endpoint takes `user_id = Depends(get_current_user)` and applies the ownership check (`404` if missing, `403` if another user's) before any mutation.

#### Conversation level

| Endpoint | Behaviour |
|---|---|
| `POST /v1/convo` | Create with explicit title. |
| `GET /v1/convo` | List the user's conversations, **newest first**, filtered to those that actually have messages (`WHERE EXISTS (...)`) — hides orphans left when a send failed before the model replied. |
| `GET /v1/convo/{id}` | All messages, **`ORDER BY index ASC, created_at ASC`**. The explicit ordering matters: Postgres guarantees nothing without it, and the UI must render in exchange order. |
| `PATCH /v1/convo/{id}` | Rename. |
| `DELETE /v1/convo/{id}` | Delete; `CASCADE` wipes messages and memories. |

#### Message level

- `PATCH /v1/convo/{cid}/messages/{mid}` — edit a message's content in place.
- `DELETE /v1/convo/{cid}/messages/{mid}` — delete one message. Indices are **not** rebalanced — they're monotonic, gaps are fine, `get_last_exchanges` still works on whatever remains.

Both go through two helpers (`_get_owned_conversation`, `_get_message`) so the ownership/existence checks live in one place.

#### Branching — `POST /v1/convo/{cid}/branch`

```python
target = message the user picked
cutoff = target.index
copy every message with index <= cutoff into a NEW conversation
  (titled "<source title> (branch)", same user)
db.flush()    # assign branch.id before inserting the copied messages
```

This is "fork the conversation at this point": the new conversation starts as an identical transcript up to the chosen message, then diverges. The `parent_id` / `branched_from_message_id` columns on `conversations` record the lineage. **Known gap:** `memories` rows are *not* copied, so a branch starts with an empty RAG store until new messages are added — positional recall and the visible history still work because the `messages` rows were copied with their original indices.

---

## 15. Presets System

### Files: `backend/app/models/presets.py`, `backend/app/routers/presets.py`

A preset is a named sampling profile: `system_prompt`, `temperature`, `token_limit`, `context_overflow`, `stop_strings[]`, `top_k`, `top_p`, `min_p`, `repeat_penalty`.

**The defaults pattern** — module constants in the model file:

```python
DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant."
DEFAULT_TEMPERATURE = 0.8 ; DEFAULT_TOP_P = 0.95 ; DEFAULT_TOP_K = 40
DEFAULT_MIN_P = 0.05 ; DEFAULT_REPEAT_PENALTY = 1.10
DEFAULT_CONTEXT_OVERFLOW = "truncate_middle"
```

Four consumers reference these same constants: the SQLAlchemy column defaults, the `PresetCreate` API schema defaults, the chat endpoint's no-preset fallbacks, and registration seeding. Change a default once, it changes everywhere.

**CRUD specifics:**
- `PresetCreate` has defaults on every field; `PresetUpdate` has everything `Optional[...] = None` and the handler applies `model_dump(exclude_none=True)` — the standard partial-update pattern (only fields actually sent get written).
- `create` uses `Preset(id=uuid4(), user_id=user_id, **request.model_dump())`.
- `get`/`patch`/`delete` do the 404-then-403 ownership dance.

---

## 16. Prompt Templates & SDXL Rewriting

### Files: `backend/app/routers/templates.py`, `backend/app/services/template.py`

A `PromptTemplate` is a *category-order skeleton* for SDXL prompts (quality → art style → camera → subject → clothes → pose → environment → lighting). CRUD mirrors presets exactly, plus `POST /v1/templates/rewrite` which calls the service directly (used by the frontend's "preview rewrite" feature).

#### `rewrite_prompt(prompt, template_id, db, user_id) → str`

1. **`get_template`** — load the user's template structure or `DEFAULT_STRUCTURE`.
2. **`load_rewrite_model()`** — POST `{LM_URL}/api/v1/models/load` with `LM_DEFAULT_MODEL`, 2048 ctx, flash attention. LM Studio JIT-loads the model and returns an `instance_id`.
3. **The rewrite call** — `{LM_URL}/v1/chat/completions`, non-streamed, temperature 0.2. The system prompt instructs: comma-separated SDXL tags following the template's category order, ≤3 words per tag, **weighted emphasis syntax `(tag:1.4)`** applied when the user signals importance ("make sure", "important", "focus on"…), weights capped, output only the prompt — no prose/JSON/markdown.
4. **`finally: unload_rewrite_model(instance_id)`** — always unload, even on failure.

**Why load/unload per request:** the rewrite model and the chat model compete for the same VRAM (a 6 GB laptop GPU). JIT-loading a small model for ~2s of work and releasing it keeps the main chat model resident. The `finally` guarantees no leaked instance even when the rewrite call throws.

---

## 17. Image Generation & Custom Workflows

### The flow

```
POST /v1/images/generate
  → (optional) rewrite_prompt()             natural language → SDXL tags
  → get_workflow()                          user's saved graph, or BASE_WORKFLOW
  → inject_params()                         prompt/steps/cfg/seed/aspect/batch into the graph
  → POST {COMFY_URL}/prompt                 → ComfyUI queues it, returns prompt_id
  → redis.set("imgjob:{prompt_id}", user_id, ex=3600)    job ownership
GET /v1/images/status/{prompt_id}           ← frontend polls every ~2s
  → redis ownership check (404 if not yours/expired)
  → GET {COMFY_URL}/history/{prompt_id}
  → "pending" | "failed" (+error) | "complete" (+image URLs)
```

### File: `backend/app/routers/images.py`

`ImageRequest` is heavily validated: prompt 1–4000 chars, steps 1–50, cfg 0–20, batch 1–8, `aspect_ratio` must be in `ASPECT_RATIOS` (custom validator), default negative prompt `"text, watermark, blurry, low quality"`, `rewrite: bool = True`, optional `template_id` and `workflow_id`.

**Job ownership via Redis:** ComfyUI has no concept of users — anyone who knows a `prompt_id` could poll it. The backend stores `imgjob:{prompt_id} → user_id` with a 1-hour TTL and 404s any status request that doesn't match (one response for "doesn't exist", "expired", and "not yours" — no information leak). Status polls also catch `httpx.HTTPError` → **502 "ComfyUI unavailable"** instead of a raw 500.

`GET /v1/images/aspect-ratios` serves the canonical `ASPECT_RATIOS` list + default so the frontend dropdown can't drift from what the backend validates.

### File: `backend/app/services/comfy.py`

- **`ASPECT_RATIOS`** is a list of display strings (`"9:16 (Portrait Widescreen)"`…) accepted by the **ResolutionSelector** custom node — the node itself maps them to pixel dimensions; the backend never deals in width/height.
- **`BASE_WORKFLOW`** — the default API-format graph: CheckpointLoader → CLIPTextEncode (+/-) → KSampler (lcm sampler, sgm_uniform scheduler) → VAEDecode → PreviewImage, with ResolutionSelector → EmptySD3LatentImage feeding the latent.
- **`get_workflow(workflow_id, user_id, db)`** — no id ⇒ `(BASE_WORKFLOW, None)`; otherwise the user's saved `Workflow` row ⇒ `(graph, param_map)`, `NotFoundError` if absent.
- **`inject_params(graph, param_map, *, prompt, …)`** — the interesting part. Works on a **`copy.deepcopy`** of the graph (a shared dict mutated by concurrent requests would leak one user's prompt into another's job). Auto-detects where each parameter lives:
  - find the `KSampler*` node → `steps`/`cfg` sit on it; seed key is `"noise_seed"` for KSamplerAdvanced else `"seed"`;
  - follow its `positive`/`negative` input links to the CLIPTextEncode nodes → their `"text"` input takes the prompt;
  - find `ResolutionSelector` → `aspect_ratio`; find `*LatentImage` → `batch_size`.
  - Then **`targets.update(param_map or {})`** — a workflow's explicit `param_map` (`{"positive": ["12", "text"], …}`) overrides auto-detection, supporting exotic graphs the heuristics can't read. Missing targets are skipped, never crash.
- **Seed** — `uuid4().int % 2**32` when not provided (ComfyUI wants a 32-bit uint).
- **`get_job_status`** — three states: not in history ⇒ `pending`; `status.status_str == "error"` ⇒ **`failed`** with the `execution_error` exception message extracted from `status.messages` (without this, a job that OOM'd would look "pending" forever and the client would poll eternally); otherwise collect `outputs[*].images` into `{filename, url}` where the URL points at ComfyUI's `/view` endpoint.

### File: `backend/app/routers/workflows.py` — bring-your-own-workflow

Full CRUD on user-uploaded ComfyUI graphs. The one non-obvious piece is **`_validate_graph`**: it requires the **API format** (`{node_id: {class_type, inputs}}`) and rejects the *UI* export format (`{"nodes": [...], "links": [...]}`) with a 422 telling the user to use ComfyUI's "Save (API Format)" — the single most common user mistake when uploading workflows.

---

## 18. Model Listing Endpoints

### File: `backend/app/routers/models.py` (both require auth)

- `GET /v1/models` — LM Studio's `/v1/models` via the OpenAI client. Wrapped in try/except → **502 "LM Studio unavailable"** when the host app isn't running (previously a raw 500).
- `GET /v1/openrouter/models` — fetches OpenRouter's catalog and **filters to free models** (`":free"` in the id, or prompt+completion pricing both "0"). Filtering server-side prevents ever surfacing a paid model in the picker — a guard against accidental billing.

---

## 19. Rate Limiting Middleware

### File: `backend/app/middleware/ratelimit.py`

Runs on **every** request before routing. Decision tree:

```
path in {/health, /metrics, /docs, /openapi.json} → skip entirely
valid access-token JWT in Authorization header     → key rate:user:{user_id},  limit 30/min
anonymous + path in {/auth/login, /auth/register, /auth/refresh}
                                                   → key rate:auth:{client_ip}, limit 10/min
anonymous, anything else                           → key rate:ip:{client_ip},   limit 30/min
```

- **Auth endpoints get the stricter bucket** to blunt brute force / credential stuffing / mass account creation — they're exactly the endpoints an attacker hits unauthenticated.
- **`_extract_user_id`** decodes the JWT itself (it can't use the `get_current_user` dependency — middleware runs before dependency injection) and ignores anything that isn't a valid `type=access` token (falls back to IP).
- **`_client_ip`** takes the first hop of `X-Forwarded-For` (set by Caddy), falling back to the socket peer. Trusting XFF is safe **only because** the backend sits behind Caddy; if port 2727 were exposed publicly, the header would be spoofable.

**The sliding window (Redis sorted set):**

```python
member = f"{now}:{uuid4().hex}"      # unique per request — same-second requests each count
pipe.zremrangebyscore(key, 0, now - window)   # evict outside the window
pipe.zadd(key, {member: now})                 # record this request
pipe.zcard(key)                               # count the window
pipe.expire(key, window)                      # idle keys clean themselves up
```

One pipeline = one network round trip. Score = timestamp, so eviction is a range delete. Over the limit ⇒ 429 with `Retry-After`.

**Fail-open:** the whole Redis block is wrapped in try/except — if Redis is down the middleware logs a warning and lets the request through. The alternative (fail-closed) turns a Redis outage into a total API outage; for a self-hosted gateway, losing rate limiting briefly is the lesser evil. This is a *policy choice* — a public multi-tenant API might decide the opposite.

---

## 20. Observability

### File: `backend/app/core/metrics.py`

**Prometheus** (scraped from `/metrics`, exposed by `prometheus-fastapi-instrumentator` which also auto-instruments every route for count/latency/status):

| Metric | Type | Labels |
|---|---|---|
| `chat_requests_total` | Counter | provider, model |
| `chat_latency_seconds` | Histogram | provider |
| `tokens_per_second` | Gauge | model |
| `prompt_tokens_total` / `completion_tokens_total` | Counter | provider, model |
| `active_conversations_total` | Gauge | — |

**Langfuse (v4 API):** `record_metrics()` opens `langfuse.start_as_current_observation(as_type="generation", …)` and records the full input messages, output text, and metadata (provider, conversation_id, latency, tps, token counts). This is the LLM-level trace — what was actually sent to the model and what came back — complementing Prometheus's aggregate counters.

**Token accounting honesty:** cloud requests get exact counts from `stream_options include_usage`; local requests report `prompt_tok=0` and `completion_tok=streamed chunk count` (LM Studio quirk, see §13). The metrics under-count local prompt tokens by design rather than guess.

---

## 21. Agents & MCP Tools

> Roadmap §1, implemented. The model can *act*: it emits tool calls, the backend executes them, feeds results back, and repeats until the model produces a final answer — each step streamed to the client as structured SSE.

### The pieces

```
services/tools/registry.py    Tool dataclass + in-process registry
services/tools/recall.py      first-party: recall_recent_exchanges(n)
services/tools/web_search.py  first-party: web_search(query)
services/tools/fetch_page.py  first-party: fetch_page(url)
services/mcp_client.py        MCPManager — connects MCP servers, registers their tools
services/agent.py             the agent loop (run_agent)
models/tool_permissions.py    per-user grant/deny rows
routers/agent.py              POST /v1/agent/chat, GET /v1/agent/tools, PUT …/permission
```

### The registry (`services/tools/registry.py`)

```python
@dataclass
class ToolContext:        # per-run state handed to every handler
    user_id: str; conversation_id: str; db: AsyncSession

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict      # JSON Schema the model must satisfy
    handler: Callable[[dict, ToolContext], Awaitable[str]]
    first_party: bool = True   # ← drives the default permission policy
```

A module-level `dict[str, Tool]` with `register / unregister / get_tool / all_tools / openai_schema`. First-party tools register **at import time** (importing the `tools` package runs `recall.py`, `web_search.py`, `fetch_page.py`); MCP tools are registered at startup by the manager. `openai_schema(tool)` emits the `{"type": "function", "function": {...}}` shape both LM Studio and OpenRouter accept — **one registry, one schema format, regardless of where the tool came from.** That uniformity is the whole point: the agent loop doesn't know or care whether a tool is local Python or a remote MCP server.

### The first-party tools

- **`recall_recent_exchanges(n)`** — wraps `get_last_exchanges` (§11) with clamping (1–20). What the regex heuristic does for plain chat, the model now does deliberately by calling a tool.
- **`web_search(query)`** — thin wrapper over `services/search.py` (§22); returns a JSON list of `{title, url, snippet}`.
- **`fetch_page(url)`** — fetches a URL and returns stripped visible text (truncated). Pairs with web_search: search finds URLs, fetch reads one.

### MCP integration (`services/mcp_client.py`)

`MCP_SERVERS` is a JSON list in `.env`:

```json
[{"name":"fs","transport":"stdio","command":"npx",
  "args":["-y","@modelcontextprotocol/server-filesystem","/data"]},
 {"name":"remote","transport":"sse","url":"http://mcp-host:8080/sse"}]
```

At lifespan startup, `MCPManager.startup()`:
1. Parses the config (invalid JSON ⇒ warn + disable, never crash).
2. For each server, opens the transport (**stdio** spawns the command inside the backend container — the binary, e.g. `npx`, must exist in the image; **sse** connects to a URL), creates a `ClientSession`, calls `initialize()`, then `list_tools()`.
3. Registers each discovered tool as **`mcp_<server>_<tool>`** (name sanitized to `[a-zA-Z0-9_-]{1,64}` — the function-calling APIs are strict), with `first_party=False` and a handler that proxies to `session.call_tool()` and joins the text content parts.
4. A server that fails to connect logs a warning and is skipped — **a broken MCP server must not take the app down.**

All connections live on one `AsyncExitStack` opened in the lifespan and closed at shutdown — same task, which the MCP SDK's anyio cancel scopes require (the reason `main.py` migrated to lifespan, §6). Shutdown also unregisters the MCP tool names.

### Per-tenant permissions

```python
# models/tool_permissions.py — UNIQUE(user_id, tool_name), allowed: bool
# the effective policy (services/agent.py):
explicit row exists      → row.allowed wins (grant or deny)
no row, first-party tool → allowed
no row, MCP tool         → denied
```

"Allow first-party, deny MCP until granted" is the sane default: first-party tools are code we wrote; MCP tools are arbitrary third-party capability. Two enforcement layers: tools the user can't use are **never advertised** to the model (not in the schemas), and `_execute_tool` re-checks at dispatch (defense in depth against hallucinated tool names). Management API: `GET /v1/agent/tools` (every tool + effective `allowed`), `PUT /v1/agent/tools/{name}/permission` (upsert).

### The agent loop (`services/agent.py → run_agent`)

```python
conversation/load_history/preset    # same prompt assembly as chat (§13)
allowed = get_allowed_tools(user_id, db)        # one query, overrides dict
tool_schemas = [openai_schema(t) for t in allowed]

for iteration in range(AGENT_MAX_ITERATIONS + 1):          # 6 + 1 final lap
    over_budget = prompt_tok + completion_tok > AGENT_TOKEN_BUDGET
    offer_tools = tool_schemas and not over_budget and iteration < AGENT_MAX_ITERATIONS
    response = await client.chat.completions.create(stream=False, ...,
                   **({"tools": tool_schemas, "tool_choice": "auto"} if offer_tools else {}))
    if no tool_calls (or tools weren't offered):
        final_answer = msg.content; break
    append assistant msg WITH its tool_calls           # the API requires this echo
    for tc in tool_calls:
        yield {"type":"tool_call", id, name, arguments}
        result = await _execute_tool(...)              # permission, JSON-parse, timeout
        yield {"type":"tool_result", id, name, content}
        append {"role":"tool", "tool_call_id": tc.id, "content": result}

yield {"type":"token", "content": final_answer}
save_messages(...); record_metrics(...)
yield {"type":"done", "conversation_id": ...}
```

**Safety rails, each with a reason:**
- **Max iterations (6) + token budget (24k)** — a confused model can loop tool calls forever; both caps force a final lap where tools are simply not offered, so the run *always* ends with an answer.
- **`_execute_tool` returns errors as strings** ("Error: tool 'x' timed out…") instead of raising — the model sees what went wrong and can adapt (retry differently, answer without the tool) rather than the whole run dying.
- **Per-tool `asyncio.wait_for` timeout (30s)** and **result truncation (8000 chars)** — one hung MCP server or a giant web page can't stall the loop or blow the context.
- **Tool-call arguments are model-generated JSON** — parsed defensively (invalid JSON, non-object ⇒ error string).

**The streaming trade-off (deliberate first cut):** tool-decision rounds are **non-streamed** — `tool_calls` arrive as deltas when streaming, and assembling them incrementally is the hard part. So each round completes, tool events stream out as they happen, and the final answer arrives as one `token` event. True token-streaming of the final answer is the documented later optimization.

### The agent SSE schema (one JSON object per `data:` line)

```json
{"type":"tool_call",   "id":"…", "name":"web_search", "arguments":"{\"query\":…}"}
{"type":"tool_result", "id":"…", "name":"web_search", "content":"[…]"}
{"type":"token",       "content":"final answer text"}
{"type":"error",       "message":"…"}
{"type":"done",        "conversation_id":"…"}
```

This is intentionally a **different endpoint** (`POST /v1/agent/chat`, same `ChatRequest` body) rather than a flag on `/chat/completions`: the existing frontend parser only understands plain tokens, and changing that contract in place would break it. Errors inside the stream (including `AppError`s — the global handler can't fire after headers are sent) become `error` events followed by `done`.

**What's not persisted (yet):** only the user message and final answer are saved via `save_messages`; the intermediate tool transcript exists only in the SSE stream.

---

## 22. Deep Research

> Roadmap §2, implemented. A research question becomes a **background job**: plan → search → read → synthesize with citations, surviving multi-minute runs without holding an HTTP request open. This feature is what justified real async-job infrastructure.

### Why a job queue at all

A research run makes ~6 model calls and ~10+ HTTP fetches — minutes of wall time. Holding an SSE request open that long fights timeouts, reloads, and navigation. Instead: `POST /v1/research` returns a `job_id` in milliseconds; a **worker container** does the work; the client polls *or* subscribes to live progress.

### The queue: arq

`worker.py` defines the arq entry point; the compose `worker` service runs `arq app.worker.WorkerSettings`.

```python
class WorkerSettings:
    functions = [run_research]                      # async def run_research(ctx, job_id)
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    job_timeout = RESEARCH_JOB_TIMEOUT_SECONDS      # 900s hard kill
    max_jobs = 2                                    # model+network heavy; keep concurrency low
    keep_result = 0                                 # job state lives in Postgres, not arq
```

**Why arq over RQ/Celery/Dramatiq** (the roadmap's suggestions): the entire codebase is async (asyncpg, httpx, async SQLAlchemy). RQ and Celery are sync-first — every job would need an `asyncio.run` wrapper and its own event loop. arq runs coroutines natively on one loop, reuses the existing Redis, and is a fraction of Celery's footprint. Same architecture, async-native implementation.

The API side enqueues via a separate lazy `ArqRedis` pool (`core/queue.py`): `await queue.enqueue_job("run_research", str(job.id))`.

### State model — Postgres is the source of truth

`research_jobs` row: `status` (queued → running → complete | failed | cancelled), `stage` (planning | searching | reading | synthesizing), `progress` (0–100), `result`, `sources` (JSONB `[{n,title,url}]`), `error`. The worker commits the row at **every stage transition**, so plain polling works and state survives worker restarts. Redis pub/sub is just the *live* view, never the record.

### The orchestration (`services/research.py → _research`)

```
1. PLAN (5%)        model generates up to 4 search queries as a JSON array.
                    _parse_queries tolerates fences/prose (slices first '[' to last ']');
                    planner failure ⇒ fall back to the raw question as the only query.
2. SEARCH (10–40%)  each query through services/search.py, RESULTS_PER_QUERY=4,
                    deduped by URL. A failed query logs and continues.
                    Zero results overall ⇒ job fails with a clear error.
3. READ (40–80%)    fetch_page() on the top RESEARCH_MAX_SOURCES=6 candidates,
                    each truncated to 6000 chars. A failed fetch falls back to the
                    search snippet. Sources are numbered [1..n] as collected.
4. SYNTHESIZE (85%) one model call: system prompt = numbered sources block + rules
                    (cite [n] inline, flag conflicts, admit gaps, end with a
                    Sources section). The user message is the original question.
→ persist: result text + slim sources (page bodies dropped — citations carry the content)
```

**Cancellation** is checked between every step: the cancel endpoint sets Redis key `research:cancel:{job_id}`; `_check_cancelled` raises `ResearchCancelled`, the job ends in status `cancelled`. Cooperative cancellation — a step in flight finishes first.

**Model choice** (`_pick_client`): research prefers OpenRouter when configured (long context, many sources stuffed into one synthesis prompt) unless the job pinned `provider: "local"`.

### The search service (`services/search.py`)

Shared by the research orchestrator **and** the agent's web_search/fetch_page tools:

- **`search(query, limit)`** — if `SEARXNG_URL` is set: `GET /search?format=json` against the self-hosted SearXNG (clean, reliable). Otherwise: **DuckDuckGo HTML scraping** — POST `html.duckduckgo.com/html/`, regex out `result__a` titles/hrefs and `result__snippet` spans, and decode DDG's redirect links (`//duckduckgo.com/l/?uddg=<urlencoded real url>`). Keyless and adequate, but scraping is inherently fragile — SearXNG is the robust path.
- **`fetch_page(url, max_chars)`** — GET with a browser-ish UA, strip `<script>/<style>/<head>` blocks then all tags, unescape entities, collapse whitespace, truncate. Non-HTML content types short-circuit. No JS rendering — SPAs yield thin text, accepted for now.

### Live progress: the Redis pub/sub → SSE bridge

The worker publishes JSON events to channel `research:{job_id}`:

```json
{"type":"progress","stage":"reading","progress":56,"message":"reading: https://…"}
{"type":"done","status":"complete","result":"…","sources":[…]}
{"type":"done","status":"cancelled"}
{"type":"error","message":"…"}
```

`GET /v1/research/{id}/stream` (routers/research.py) bridges that to SSE:

1. **Snapshot first** — emit the job's current DB state immediately, so a late subscriber (or a finished job) gets the answer without waiting for a pub/sub message that already fired. This snapshot-then-subscribe pattern is what makes the bridge race-free *enough* for a progress UI.
2. Subscribe to the channel; forward each message as a `data:` line.
3. `get_message(timeout=15)` returning `None` ⇒ emit an SSE **comment** (`: keepalive`) so Caddy/browsers don't kill the idle connection.
4. A `done`/`error` event ends the stream; `finally` unsubscribes and closes the pubsub.

(Known sliver: an event published between snapshot and subscribe can be missed — the next stage commit/event covers it, and the DB poll endpoint is always truthful.)

### The endpoints

| Endpoint | Behaviour |
|---|---|
| `POST /v1/research` | Validate (`query` 1–4000 chars, optional provider/model), insert `queued` row, enqueue, return `job_id`. |
| `GET /v1/research` | The user's last 50 jobs, newest first (summaries). |
| `GET /v1/research/{id}` | Full state incl. result/sources/error. **404 for missing *and* non-owned** — job ids can't be enumerated. |
| `POST /v1/research/{id}/cancel` | 409 if already terminal. Sets the Redis cancel flag; if still `queued`, cancels directly in the DB (the worker skips non-queued jobs — that check is what makes early cancellation race-safe). |
| `GET /v1/research/{id}/stream` | The SSE bridge above. |

---

## 23. Hardware Probe & Cookbook

> Roadmap §3, implemented. "Which of my local models actually fit my GPU?" — a hardware probe plus a VRAM-aware fit score over the local model catalog.

### `services/hardware.py` — the probe

```python
probe_hardware() → {"gpu_available": bool, "gpus": [{index, name, vram_total_mb, vram_free_mb}], "ram_total_mb": int | None}
```

Two attempts, then graceful surrender: **pynvml** (NVML bindings — needs the NVIDIA driver visible in the container, i.e. compose `gpus: all` + nvidia-container-toolkit) → **`nvidia-smi` subprocess** (`--query-gpu=name,memory.total,memory.free --format=csv`, 10s timeout) → `{"gpu_available": false}`. Every failure path is caught; the endpoint never 500s for "no GPU".

`ram_total_mb` is read from `/proc/meminfo` (`_probe_ram`).

### `services/fit_score.py` — the catalog and the heuristic

**Catalog** (`get_local_models`): LM Studio `GET /api/v0/models` (id, quantization, max context — no file size) + Ollama `GET /api/tags` (file size, `parameter_size` like "8.0B", quant level). Either being down logs a warning and just shrinks the catalog.

**The fit heuristic** (documented as a heuristic, not a promise):

```
weights_gb ≈ size_on_disk            (Ollama)  or  params_b × bytes/param[quant]  (LM Studio)
             quant table: q2≈0.40 … q4≈0.60 … q8≈1.10, f16=2.0  (unknown ⇒ q4-ish 0.60)
             params_b parsed from the model name ("…-7b-…" → 7.0)
kv_gb      ≈ ctx_tokens × 128 KB × (params_b / 7)        # GQA-era ballpark
need_gb    ≈ (weights + kv) × 1.1                        # runtime overhead
```

**Verdicts:** `fits_fully` (need ≤ total VRAM × (1−margin)) · `partial_offload` (overflows total VRAM but fits via RAM offload — weights ≤ RAM and need ≤ VRAM + RAM, slower) · `wont_fit` (too large even with offload) · `cpu_only` (no GPU) · `unknown` (couldn't size the model). Score = `min(100, 100 × total_vram/need)`. Every verdict carries a one-line human rationale ("~4.8 GB weights + ~1.5 GB KV cache (@8192 ctx) fits in 5.9 GB VRAM").

**`build_cookbook`** scores every model against the GPU with the most total VRAM (single-GPU assumption), factors `ram_total_mb` into RAM-offload verdicts, sorts by verdict rank then score, and recommends the top entry if it at least partially fits.

### `routers/hardware.py`

- `GET /v1/hardware` — the raw probe.
- `GET /v1/cookbook?context_tokens=8192` — probe + ranked catalog + recommendation. The `context_tokens` query param (512–262144) lets you ask "what if I want 32k context?" — KV cache estimates scale with it.

---

## 24. Caddy Reverse Proxy

### File: `caddy/Caddyfile`

```caddy
{  auto_https off  }          # Tailscale terminates TLS via `tailscale serve`

:80 {
    handle /api/* {
        uri strip_prefix /api
        reverse_proxy backend:8000 {
            flush_interval -1     # flush every write — SSE tokens must not buffer
        }
    }
    handle /view* {
        reverse_proxy host.docker.internal:8188   # ComfyUI image previews
    }
    handle {
        root * /srv               # the built SPA (frontend/dist mounted here)
        try_files {path} /index.html              # SPA fallback for deep links
        file_server
    }
}
```

Three routes, in priority order: **API** (`/api/*` → strip prefix → backend; `flush_interval -1` is what makes token-by-token SSE actually reach the browser), **ComfyUI previews** (`/view*` → host ComfyUI, so the SPA can use same-origin image URLs), **everything else** → static SPA with `index.html` fallback so React Router owns deep links. TLS is Tailscale's job (`tailscale serve`), which is also how the phone reaches the app.

---

## 25. Frontend Architecture

> Backend-first document — this section gives the structural map and the contracts the frontend must honor, not a line-by-line walkthrough.

**Stack:** React 18 + TypeScript + Vite · Zustand stores · React Router · Tailwind CSS.

```
frontend/src/
  App.tsx / main.tsx              entry, router, ProtectedRoute
  lib/
    api-client.ts                 fetch wrapper: Bearer auth, 401 → coalesced refresh
                                  → retry; SSE reader for the chat stream
    api-endpoints.ts              every backend path in one place
    types.ts / config.ts / utils.ts / image-history.ts
  stores/                         Zustand: auth, chat, model, preset, template,
                                  workflow, image, ui, layout
  hooks/
    use-chat.ts                   send/stream/cancel lifecycle for a conversation
    use-image.ts                  generate + poll lifecycle
  pages/                          login, register, chat, presets, templates,
                                  workflows, images, settings, not-found
  components/
    chat/                         ChatHeader, ChatInput, ConversationList,
                                  MessageList, MessageBubble, Markdown, ModelSelector
    settings/                     Preset/Template/Workflow panels + forms
    layout/                       AppShell, Sidebar (collapsible), RightSidebar,
                                  TwoPanel, AuthLayout, ProtectedRoute
    ui/                           Button, Input, Modal, Slider, Toggle, … (small kit)
```

**The contracts that matter (where frontend and backend meet):**

1. **Auth handshake** — access token in memory, refresh token persisted. On any 401, `api-client.ts` runs **one** coalesced `POST /auth/refresh` (concurrent 401s share the in-flight promise — vital because rotation invalidates the old token; two parallel refreshes would race each other out of a session) and retries the original request; refresh failure ⇒ logout.
2. **The chat SSE contract** — the parser understands exactly `data: <token>` / `data: [ERROR] …` / `data: [DONE]`. This is why the agent (`/v1/agent/chat`) and research (`/v1/research/{id}/stream`) endpoints — which emit *JSON event objects* per `data:` line — need their own parser before the UI can use them. **This is the standing frontend TODO for roadmap features §1–§2.** A cookbook page (§3) is likewise pending.
3. **Image URLs** — the backend returns ComfyUI `/view` URLs; the SPA rewrites them to same-origin so Caddy's `/view*` proxy serves them.
4. **Aspect ratios** come from `GET /v1/images/aspect-ratios` — the dropdown can't drift from backend validation.

---

## 26. How All Files Fit Together

### The backend layer cake

```
docker-compose.yml      8 services; secrets; GPU; worker + searxng for research
caddy/Caddyfile         :80 → SPA | /api/* → backend | /view* → ComfyUI
backend/app/
  main.py               lifespan (redis, MCP, queue), CORS, rate limit,
                        AppError handler, 11 routers
  db.py                 async engine + AsyncSessionLocal + get_db
  worker.py             arq WorkerSettings (run_research)
  core/
    config.py           Settings (.env) + get_secret (Docker secrets → env fallback)
    security.py         bcrypt, JWTs, hash_token, DUMMY_PASSWORD_HASH, get_current_user
    redis.py            singleton pool        queue.py   arq enqueue pool
    metrics.py          Prometheus + Langfuse     exceptions.py  AppError family
  middleware/ratelimit.py   sliding window, per-user/per-IP/auth buckets, fail-open
  models/               users, refresh_tokens, conversations (+branch cols), messages,
                        memories, presets, prompt_templates, workflows,
                        tool_permissions, research_jobs
  routers/              auth, chat, convo, presets, templates, images, workflows,
                        models, agent, research, hardware
  services/
    router.py           provider routing + ChatRequest + clients
    convo.py            find-or-create, recall regex, exchanges, history, locked save
    memory.py           embeddings + pgvector retrieval (graceful everywhere)
    template.py         SDXL rewrite (JIT load/unload on LM Studio)
    comfy.py            workflows, param injection, submit, status (incl. failed)
    search.py           SearXNG/DDG search + fetch_page (shared: tools + research)
    agent.py            the tool-calling loop over SSE
    research.py         plan → search → read → synthesize; pub/sub progress
    hardware.py         pynvml / nvidia-smi probe      fit_score.py  catalog + heuristic
    mcp_client.py       MCPManager (stdio/SSE servers → registry)
    tools/              registry + recall + web_search + fetch_page
```

### Call chain — chat (the spine)

```
Caddy /api/* → RateLimit(Redis) → get_current_user(JWT) → stream_tokens:
  conversation() → load_history(30 + RAG top-3) → load_preset()
  → detect_recall_request()? inject transcript
  → get_provider() → create(stream=True)
  → yield "data: <token>" per token   [mid-stream error ⇒ save partial + [ERROR]]
  → save_messages (row-locked, atomic) → store_memory ×2 (best-effort)
  → record_metrics (Prometheus + Langfuse) → "data: [DONE]"
```

### Call chain — agent

```
POST /v1/agent/chat → same prompt assembly → get_allowed_tools(user)
loop ≤6: create(stream=False, tools=…)
   tool_calls? → yield tool_call → execute (perm/timeout/truncate) → yield tool_result
              → append role:"tool" msg → re-call
   no tool_calls (or budget/iterations spent ⇒ tools withheld) → final answer
yield token(answer) → save_messages → yield done
        MCP servers (stdio/SSE, connected at lifespan) appear as mcp_<server>_<tool>
        in the SAME loop, deny-by-default until granted.
```

### Call chain — deep research

```
POST /v1/research → insert queued row → arq enqueue → return job_id
worker: run_research_job → status=running
  plan(model) → search×N(SearXNG|DDG) → fetch×≤6 → synthesize(model, citations)
  each stage: commit row + publish to redis channel research:{id}
  cancel flag checked between steps → status=cancelled
client: GET /research/{id}  (poll)    or
        GET /research/{id}/stream  →  snapshot from DB, then pub/sub→SSE relay,
                                      keepalive comments, ends on done/error
```

### Call chain — images

```
POST /v1/images/generate → rewrite? (JIT Qwen on LM Studio) → get_workflow(user|base)
  → inject_params(deepcopy + auto-detect + param_map) → ComfyUI /prompt → prompt_id
  → redis imgjob:{id}=user (1h)
GET /v1/images/status/{id} → ownership → ComfyUI /history → pending|failed|complete(urls)
  → SPA loads images via Caddy /view* proxy
```

---

## 27. Key Design Decisions Summary

| Decision | Why |
|---|---|
| **Async everything (FastAPI + asyncpg + httpx)** | LLM calls block for seconds–minutes; the event loop must stay free |
| **One `AsyncOpenAI` client for local + cloud** | LM Studio and OpenRouter both speak the OpenAI protocol; routing = swap base_url |
| **SSE over WebSocket** | Unidirectional token flow; plain HTTP; proxy-friendly; trivially parsed |
| **Plain-token SSE for chat, JSON-event SSE for agent — separate endpoints** | Never break an existing wire contract in place; new schema = new route |
| **Domain errors (`AppError`) instead of HTTPException in services** | Services callable from worker/CLI/tests; one translation point at the API boundary |
| **`lifespan` (not on_event)** | MCP's anyio cancel scopes must open/close in the same task; also the deprecation |
| **Refresh tokens: only SHA-256 hash stored; rotate by delete+insert** | DB breach yields no usable tokens; stolen token works at most once |
| **`jti` nonce in refresh JWTs** | Same-second tokens would otherwise hash-collide on the unique constraint |
| **`DUMMY_PASSWORD_HASH` (single unconditional verify)** | Constant-time login; no email enumeration via response timing |
| **Registration is one transaction (+ IntegrityError catch)** | No half-initialized accounts; the DB unique constraint beats the check-then-insert race |
| **Validation on register only (separate `UserLogin`)** | New rules must not lock out existing accounts |
| **Monotonic message indices (user=k, assistant=k+1)** | "Last n exchanges" is arithmetic; gaps from deletions are harmless |
| **`SELECT … FOR UPDATE` on the conversation in `save_messages`** | Concurrent sends can't allocate colliding indices |
| **30-message history window + RAG + positional recall** | Bounded prompt; older context reachable two other ways |
| **Memory is best-effort, with `rollback()` on failure** | RAG failure must never fail a chat turn — or poison the session transaction |
| **Explicit `CAST(:emb AS vector)`** | asyncpg sends params as text; the bare operator doesn't exist |
| **Rate limiter fails open** | Redis outage degrades to "no limiting", not "no API" — a policy choice |
| **Stricter per-IP bucket on /auth/*** | Brute force / stuffing hits exactly the unauthenticated endpoints |
| **Sliding window via Redis ZSET + pipeline, uuid members** | Accurate window; one round trip; same-second requests all count |
| **`stream_options` cloud-only; sampling via `extra_body` local-only** | LM Studio quirks; OpenRouter strictness — provider asymmetry isolated in one place |
| **Mid-stream failure ⇒ save partial + [ERROR] + [DONE]** | No lost exchanges, no hanging client |
| **Tool registry with `first_party` flag; MCP deny-by-default** | First-party is our code; MCP is third-party capability — opt-in per user |
| **Disallowed tools never advertised + re-checked at dispatch** | Defense in depth against prompt-level and hallucinated calls |
| **Agent: iteration cap + token budget force a tool-less final lap** | The loop always terminates with an answer |
| **Tool errors returned as strings to the model** | The model can adapt; one bad tool call doesn't kill the run |
| **Non-streamed tool rounds (first cut)** | Streaming tool_call deltas is the hard part; correctness first |
| **arq (not RQ/Celery) on the existing Redis** | Async-native jobs in an async codebase; no second broker |
| **Job state in Postgres; pub/sub only for live view; snapshot-then-subscribe** | Survives restarts; pollable; late subscribers race-safe |
| **Cooperative cancellation via Redis flag** | Worker checks between steps; queued jobs cancel instantly in DB |
| **SearXNG if configured, DDG scrape fallback** | Keyless default that works; robust self-hosted path when wanted |
| **ComfyUI graphs deep-copied + auto-detected params + `param_map` override** | Concurrent jobs can't cross-contaminate; exotic graphs stay supported |
| **API-format graph validation (reject UI export)** | Catches the #1 user error at upload time with an actionable 422 |
| **Image job ownership in Redis (TTL 1h), uniform 404** | ComfyUI is userless; the gateway adds tenancy; no information leak |
| **Fit scores are labeled heuristics with rationale strings** | Honest estimates beat false precision |
| **`get_secret`: Docker secrets → env fallback → loud failure** | Secure in compose; still runs host-side for Alembic/tests |
| **Naive UTC datetimes everywhere** | One convention, no tz-mixing bugs (deliberate project rule) |
| **Free-models-only OpenRouter listing** | A paid model can never appear in the picker by accident |


---

## 28. Interview Prep: Tough Questions & Answers

> **Context:** This section is written from the perspective of a hiring manager who has had a terrible day. Questions are deliberately harsh, nitpicky, and designed to probe every weak spot. Use these to prepare for a real senior-level technical interview.

---

### Q1: Why FastAPI over Django or Flask? Be specific about what three FastAPI features you actually used that justified the choice.

**A:** Three FastAPI features that were non-negotiable:

1. **Async-native request handling.** Every chat endpoint awaits LLM calls that take 10-60 seconds. Flask blocks the event loop; you would need Quart (a separate framework). Django got async support later, but FastAPI was designed for it from day one.

2. **Dependency injection with `Depends()`.** The `get_db()` and `get_current_user()` dependencies compose cleanly. FastAPI handles the dependency graph, scope, and teardown automatically. In Flask, you would use `@app.before_request` and `g` objects — manual, error-prone, not composable.

3. **Pydantic v2 integration.** The same `BaseModel` that validates request bodies is also our settings layer (`BaseSettings`). Validation errors are automatic, typed, and serialized to JSON without custom error handlers. Flask requires `marshmallow` or manual validation.

**What we did NOT use:** WebSocket support (we chose SSE instead), OpenAPI generation (we do not serve a docs page in production), background tasks (`BackgroundTasks` is synchronous, not useful for async).

Would I choose it again? Yes, but with a caveat: the middleware API (`BaseHTTPMiddleware`) is notoriously buggy with async generators (our rate limiter had subtle issues). For the next version, I would use ASGI middleware directly.

---

### Q2: The provider router has 6 hardcoded rules in a `match/case`. What happens when you add Anthropic, Google Gemini, or a custom vLLM endpoint? Does this scale?

**A:** It does not scale as written. The rule-chain router was designed for a 2-provider world (local LM Studio, OpenRouter). Adding Anthropic means either routing it *through* OpenRouter (works today: `provider: "openrouter"` + an `anthropic/...` model id) or adding a new `case` branch and client factory.

The scaling problem is that the routing logic is **linear priority** — every new provider needs to find its slot in the priority chain. Real production routers use a **model-based routing table** stored in a config file:

```yaml
routes:
  - pattern: "anthropic/*"
    provider: anthropic
    api_key: "${ANTHROPIC_KEY}"
    base_url: "https://api.anthropic.com/v1"
  - pattern: "*"
    provider: openrouter  # default
```

This is config-driven, not code-driven. You add a new provider by adding a config entry, not by modifying Python code.

**Why I have not refactored it yet:** The `match/case` with 6 rules is fast (O(1) dispatch, compile-time optimized), easy to reason about, and covers 100% of current use cases. Premature abstraction would add complexity without benefit. The refactoring trigger is "adding provider #4."

---

### Q3: The memory service silently returns `None` on embedding failure. What about the reverse — Ollama comes back mid-session? Is there a retry or circuit breaker?

**A:** There is not, and that is a genuine gap. If a user exchanges 20 messages while Ollama is down, none of those messages get embeddings. When Ollama comes back, `retrieve_memories()` will not find anything related to those 20 messages. Permanent semantic memory gap.

**What should exist:**

1. **Circuit breaker** — Track consecutive failures. After 3 failures, stop trying for 60 seconds (fast-fail). Reset on first success. Prevents hammering a down service with 30-second timeout calls.

2. **Background retry** — A lightweight background task (`asyncio.create_task`, not a full job queue) that re-attempts failed embeddings. Queue failed message IDs to a Redis list, process them when Ollama is healthy.

3. **Stale memory indicator** — The frontend should show a subtle indicator like "Semantic memory unavailable" when the circuit is open, so the user knows recall will not work.

---

### Q4: SSE vs. WebSocket — WebSocket seems strictly better for bidirectional streaming. What is the actual trade-off you made, and was it the right call?

**A:** SSE over WebSocket was the right call for this project, but not for every project. The actual trade-offs:

| Factor | SSE | WebSocket |
|---|---|---|
| Connection model | Unidirectional (server to client) | Bidirectional |
| Transport | HTTP (standard) | Upgrade handshake, then custom protocol |
| Auto-reconnect | Built-in (EventSource API) | Manual (must implement) |
| Proxy compatibility | Excellent (passes through any HTTP proxy) | Breaks on many proxies, requires sticky sessions |
| Message framing | Text-only, delimiter is double-newline | Binary or text, built-in framing |
| Scaling | Stateless (any backend instance can serve) | Stateful (must route client to same instance) |

**Why SSE won:** Our system is *mostly* unidirectional. The client sends a POST request (standard HTTP), and the server streams the response back. The only server-to-client data is the token stream and `[DONE]` signal. We do not need client-to-server streaming.

**The one case where WebSocket would be better:** Image generation. Currently the frontend polls every 2 seconds. A WebSocket could push the "complete" event instantly. But polling at 2s is good enough for a 10-60s operation — the latency difference is about 1 second, which the user will not notice.

**What I would change:** For the next iteration, use SSE for chat (simpler and more robust) but add a WebSocket endpoint for image status events. Hybrid approach is common in production.

---

### Q5: `DUMMY_PASSWORD_HASH` prevents timing-based email enumeration on login. But what about registration? Is there a timing leak there too?

**A:** Yes, but it is a different class of timing leak and the risk is lower.

**Registration:** `db.execute(select(User).where(User.email == ...))` — whether the user exists or not, the query executes in roughly the same time (indexed lookup by email). The difference is in microseconds, not milliseconds (bcrypt). This is not practically measurable over a network.

**Why login is different:** The bcrypt verification takes about 100ms. If you skip it for non-existent users, the difference from an instant return is visible even on a noisy network. That is why `DUMMY_PASSWORD_HASH` exists.

**The historical registration vulnerability (since fixed):** registration used to be excluded from rate limiting entirely. Today `/auth/register`, `/auth/login`, and `/auth/refresh` share a stricter per-IP bucket (`AUTH_RATE_LIMIT_PER_MINUTE`, default 10/min), which curbs junk-account floods and email probing. Registration now also validates email format and a minimum password length, and runs in a single transaction with an `IntegrityError` catch so concurrent duplicate registrations can't 500 or half-initialize an account.

---

### Q6: Walk me through what's stored for a refresh session. If my database is dumped, what does the attacker get?

**A:** The `refresh_tokens` row stores **only** `token_hash` (SHA-256 of the JWT), `user_id`, `created_at`, and `expires_at`. The JWT itself never touches the database — the client holds the only copy, presents it on refresh, the server hashes it and does an indexed equality lookup.

**A DB dump therefore yields no usable tokens.** SHA-256 is one-way; reconstructing a valid JWT from its hash means either reversing SHA-256 or forging an HS256 signature — and forging requires `SECRET_KEY`, at which point the attacker can mint arbitrary tokens anyway and the refresh table is moot. The hashes are also useless for *future* sessions because rotation deletes the row on every use.

**History worth knowing:** migration 7 (`f71508e02cd3`) introduced hashing — the original design stored plaintext JWTs in the DB, which an audit flagged. The interim "store both, look up by hash" phase was later completed; the model today has no `token` column at all. Rotation is implemented as **delete + insert** (not update-in-place), so a used token's hash genuinely ceases to exist.

---

### Q7: How are the auth endpoints rate-limited, given there's no JWT to key on?

**A:** They get their own, *stricter* bucket. The middleware's decision tree: a valid access token ⇒ per-user key at 30/min; no token and the path is in `AUTH_PATHS` (`/auth/login`, `/auth/register`, `/auth/refresh`) ⇒ `rate:auth:{client_ip}` at `AUTH_RATE_LIMIT_PER_MINUTE` (10/min); any other anonymous request ⇒ `rate:ip:{client_ip}` at 30/min. Only genuinely unlimitable paths (`/health`, `/metrics`, `/docs`, `/openapi.json`) skip the limiter. (Historically login/register were excluded entirely — an audit finding that has since been fixed.)

**What's still missing:** exponential backoff per credential pair — a Redis key `failed_login:{email}` whose TTL doubles on each failure (1s, 2s, 4s … 5min). That throttles credential stuffing on a *target-account* dimension, which per-IP limits can't see when the attacker rotates IPs.

**One more nuance:** the IP comes from the first hop of `X-Forwarded-For`, which is only trustworthy because Caddy sets it. If the backend port were ever exposed directly, that header is attacker-controlled and the IP buckets become spoofable.

---

### Q8: The messages table uses a dual-index system (user=N, assistant=N+1). What happens when a user deletes a message mid-conversation? Do the indices get rebalanced?

**A:** They do not get rebalanced, and that is fine. The dual-index system is **monotonically increasing** — indices only ever go up. When a message is deleted, that index value is gone forever:

```
User: "Hello"          → index 1
Assistant: "Hi there"  → index 2
User: "Write code"     → index 3
Assistant: "Done"      → index 4
DELETE messages with index >= 3 (user regenerates)
Assistant: "Here is new code" → index 5 (not index 3!)
```

`get_last_exchanges(2)` computes: `max_index = 5, cutoff = 5 - 4 = 1`. Returns messages with index >= 1, which includes the original "Hello / Hi there" plus the new exchange. Correct — it returns exactly the last 2 exchanges.

**Related machinery that now exists:** message edit (`PATCH /v1/convo/{cid}/messages/{mid}`), message delete, and **conversation branching** (`POST /v1/convo/{cid}/branch`) — branching copies every message with `index <= target.index` into a new conversation and records lineage via `parent_id`/`branched_from_message_id`. The copied messages keep their original indices, which works *precisely because* indices are monotonic and gaps are harmless — a rebalancing scheme would have made branching far messier.

---

### Q9: No pagination on the conversations list endpoint. What happens at 10,000 conversations?

**A:** The query is `select(Conversation).where(User.id == ...).all()`. No ORDER BY, no LIMIT, no OFFSET. Returns every conversation as a Python list.

**What breaks:**
1. **Memory:** 10,000 conversations at about 200 bytes each = 2MB of Python objects. Not terrible but unnecessary.
2. **Network:** 2MB JSON payload on every page load.
3. **Frontend:** Rendering 10,000 items in the sidebar causes DOM jank.

**The fix — offset-based pagination:** Add `limit` and `offset` query params with a sensible default (50). Include a `has_more` flag by fetching `limit + 1` rows. Simple, standard, and effective.

**Why it has not been done:** The project has fewer than 100 conversations total. Pagination is premature optimization at this scale.

---

### Q10: The pgvector `<=>` operator — what is the query performance without an index on 768-dim vectors?

**A:** No index exists on `embedding`. The query is a full table scan with cosine distance computation on every row.

- 100 memories: ~2ms (fine)
- 1,000 memories: ~15ms (fine)
- 10,000 memories: ~200ms (noticeable)
- 100,000 memories: ~2s (unacceptable)

**The fix — IVFFlat or HNSW index:**

```sql
CREATE INDEX idx_memories_embedding ON memories
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

IVFFlat partitions the vector space into 100 clusters and only searches the nearest ones at query time. Approximate but fast.

Better: HNSW (Hierarchical Navigable Small World) — faster and more accurate but uses more memory and takes longer to build. For <100K memories, HNSW is the right choice.

**Why no index exists yet:** The dataset is tiny (<500 memories). Full scan is sub-millisecond. Adding an index would add INSERT overhead with no benefit. Trigger to add one: when queries exceed 50ms.

---

### Q11: Access token in memory only, but refresh token in localStorage. If XSS executes, the attacker reads the refresh token. What did you actually protect against?

**A:** We protected against a **brief XSS** (e.g., reflected XSS in a log message that triggers once). The attacker gets a snapshot of localStorage and calls `/auth/refresh`, getting exactly ONE access token. The refresh token is then rotated — the victims next request fails, they are logged out, and the attackers token is invalid.

**We did NOT protect against** persistent XSS (stored XSS that re-executes on every page load). The attacker reads the refresh token on every page load, calls `/auth/refresh` before the victim does, and maintains a continuous session. Rotation race.

**The real fix for production:** httpOnly cookies for the refresh token. An httpOnly cookie is inaccessible to JavaScript entirely. The backend sets `Set-Cookie: refresh_token=<jwt>; HttpOnly; Secure; SameSite=Strict; Path=/auth/refresh`. The `/auth/refresh` endpoint reads the cookie, not the request body. XSS is completely blind to it.

**Why we did not use cookies:** Faster initial implementation without needing CORS cookie configuration (`withCredentials`, `Access-Control-Allow-Credentials`).

---

### Q12: The API client has refresh token coalescence. What if the refresh itself returns a 401? Is there retry logic, or does it immediately log out?

**A:** There is no retry logic. A single 401 from `/auth/refresh` causes an immediate logout for ALL pending requests (since they share the same `refreshingPromise`). A 200ms network blip causes a full session loss.

**The fix — retry with backoff:**

```typescript
const MAX_RETRIES = 2;
for (let i = 0; i < MAX_RETRIES; i++) {
  try {
    const response = await fetch("/auth/refresh", { ... });
    if (response.ok) { /* success */ return; }
  } catch {
    if (i === MAX_RETRIES - 1) throw err;
    await new Promise(r => setTimeout(r, 1000 * (i + 1)));
  }
}
```

With 2 retries and 1-2 second backoff, the user survives brief network hiccups. Only sustained failures cause logout.

---

### Q13: Optimistic updates make the UI fast. But what if the server rejects the message? How does the UI recover?

**A:** It does not — and that is a bug. Current flow:

1. User sends message → optimistically adds to UI
2. Server returns 429 (rate limit)
3. Error is caught, toast is shown
4. The user message **stays in the UI** as if it was sent

If the user refreshes, the phantom message disappears (never saved to DB). The user has no way to distinguish "sent" from "rejected."

**The fix:** On error, remove both the optimistically-added user message and the placeholder assistant message from the array. Show a clear error toast with the specific reason.

```typescript
catch (err) {
  setMessages((prev) => {
    let updated = prev.slice(0, -1); // remove assistant placeholder
    const last = updated[updated.length - 1];
    if (last?.role === "user") updated = updated.slice(0, -1); // remove user msg
    return updated;
  });
  toast.error(`Failed: ${err.message}`);
}
```

---

### Q14: The Docker setup uses `--reload`. Could a partial write cause the server to restart with inconsistent state?

**A:** Yes. Uvicorn watches the filesystem for change events. When an editor saves a file non-atomically, Uvicorn restarts while the file is half-written. The first restart after the crash is guaranteed to fail with ImportError or SyntaxError.

**Mitigations:**
1. `--reload-delay 0.5` — Wait 500ms after the last file change before restarting. Batches rapid writes.
2. **Editor config** — Ensure atomic saves. VS Code does this by default.
3. **Production** — Use `--no-reload` and a proper CI/CD pipeline for restarts.

---

### Q15: The Caddyfile strips `/api`. What happens behind another reverse proxy that ALSO strips prefixes?

**A:** If nginx passes `/api/v1/chat/completions` without stripping, Caddy strips `/api` and sends `/v1/chat/completions` to backend. Works fine.

If nginx strips `/api` and sends `/v1/chat/completions`, Caddy's `@api path /api/*` does not match, falling through to SPA serving. Returns `index.html` for API requests. Silent failure.

**The fix:** Make the prefix configurable via environment variable: `@api path {env.API_PREFIX}/*` with a default of `/api`. When deploying behind another proxy, set `API_PREFIX` to empty string.

---

### Q16: `stream_tokens` is a single monolithic generator. If streaming fails midway, are partial messages saved?

**A:** Yes — this used to be a real data-loss bug and is now handled. The token loop is wrapped:

```python
stream_error = False
try:
    async for chunk in response:
        ...
        yield f"data: {content}\n\n"
except Exception as e:
    logger.error("Stream interrupted mid-generation: %s", repr(e))
    stream_error = True
...
if full_response or not stream_error:
    await save_messages(...)        # partial responses ARE saved
if stream_error:
    yield "data: [ERROR] stream interrupted\n\n"
yield "data: [DONE]\n\n"
```

If the provider dies after 50 tokens, those 50 tokens are persisted as the assistant message, the client gets an explicit `[ERROR]` event followed by `[DONE]`, and the conversation history stays consistent with what the user saw. If the failure happens before *any* token arrived, nothing is saved (no point persisting an empty exchange) but the error/done events still close the stream cleanly.

**The remaining gap:** a *client* disconnect (not a provider failure) cancels the generator task mid-flight, and the partial exchange is not saved. Handling that needs a `CancelledError`/`GeneratorExit` path that can still run an await reliably — trickier, and deliberately deferred.

---

### Q17: The ComfyUI workflow is deep-copied. What about `ASPECT_RATIOS`? Is that also deep-copied?

**A:** `ASPECT_RATIOS` is a dict of strings to dicts of ints: `{"1:1": {"width": 1024, "height": 1024}}`. It is immutable at runtime (never modified after import). Since the values are only read, not written, sharing is safe.

**Why deep copy for workflow but not aspect ratios:** The workflow dict is mutated — we set `workflow["6"]["inputs"]["text"] = prompt`. If two requests share the same workflow dict, the second request overwrites the first's prompt. `ASPECT_RATIOS` is never mutated, only read.

**Defensive improvement:** Use `types.MappingProxyType` (read-only dict wrapper) to prevent accidental mutation:

```python
from types import MappingProxyType
_ASPECT_RATIOS = { ... }
ASPECT_RATIOS = MappingProxyType(_ASPECT_RATIOS)
```

Any attempt to mutate raises `TypeError`.

---

### Q18: No background task queue. What happens when 5 users generate images simultaneously?

**A:** ComfyUI has its own internal queue — it processes prompts sequentially on a single GPU. Each returns a `prompt_id` immediately; status is `pending` until ComfyUI gets to it.

**The actual bottleneck is the rewrite model, not ComfyUI:** All 5 requests trigger the SDXL rewrite phase simultaneously, which loads Qwen 2.5 on LM Studio 5 times concurrently. LM Studio crashes or times out.

**What should exist — a rewrite queue:**

1. User submits image request
2. Push to Redis list: `LPUSH rewrite_queue <data>`
3. Background worker pops: `BRPOP rewrite_queue`
4. Worker loads Qwen, rewrites, submits to ComfyUI, stores result
5. Frontend polls status — backend checks Redis for result

This serializes rewrite requests. ComfyUI's own queue handles image generation serialization.

**What exists now:** No queue. Acceptable for single-user self-hosted. Fails under concurrent load.

---

### Q19: `get_embedding` has a 30-second timeout. What happens when embedding takes 31 seconds? Circuit breaker?

**A:** `httpx.AsyncClient(timeout=30)` raises `httpx.TimeoutException` after 30s. The `except Exception` catches it and returns `None`. No circuit breaker, no differentiated logging.

**Two failure modes look identical:**
- Ollama is down (connection refused, should stop trying)
- 10,000-word message takes 31s (should truncate and retry)

Both return `None` silently.

**Proper fix — circuit breaker with truncation retry:**

```python
if len(content) > 5000:
    content = content[:5000]
try:
    response = await client.post(...)
    self.consecutive_failures = 0
    return response.json()["embedding"]
except httpx.TimeoutException:
    if len(content) > 1000:
        return await self.get_embedding(content[:1000])  # truncate retry
    self._record_failure()
    return None
except Exception:
    self._record_failure()
    return None
```

This distinguishes between timeouts (retry with truncation) and connection failures (circuit breaker).

---

### Q20: `update_preset` uses `model_dump(exclude_none=True)`. What happens when a user sets `temperature` to 0? `0` is falsy in Python but valid. Does `exclude_none` handle this?

**A:** Yes. `exclude_none=True` only excludes values that are literally `None`. `0` is an integer, not `None`, so it is included. The pattern works correctly.

**The actual bug:** If the user sends `{}` (empty object), `model_dump(exclude_none=True)` returns `{}`, the loop does nothing, and the preset is "updated" without changes. Silent no-op. A better pattern returns 400 if no updatable fields are provided.

---

### Q21: Logout requires both JWT access token AND refresh token body. Why both?

**A:** The access token (`Depends(get_current_user)`) authenticates "I am user X." The refresh token identifies WHICH session to terminate. Together they prove: "User X wants to end this specific session."

**The better pattern (most OAuth2 implementations):** Two endpoints:
1. `POST /auth/logout` — Logout ALL sessions. Requires access token. Deletes ALL refresh tokens for the user.
2. `POST /auth/logout/session` — Logout a specific session. Requires the refresh token itself as proof of possession.

Our current implementation requires both, which is redundant.

---

### Q22: `initializeAuth()` reads the refresh token from localStorage and calls `/auth/refresh`. What happens if the token is expired?

**A:** `/auth/refresh` returns 401. `refreshAuth()` catches this and calls `logout()`, clearing tokens and setting `isAuthenticated = false`. `ProtectedRoute` redirects to `/login`.

**The user sees:** A flash of the app (before `initializeAuth` completes) followed by a redirect to login. No toast or error message. If the user had unsent text, it is lost.

**The UX fix:** Add a loading state during `initializeAuth`:

```typescript
function App() {
  const { isInitialized } = useAuthStore();
  if (!isInitialized) return <LoadingScreen />;
  return (/* router */);
}
```

Show a loading spinner while initializing. On failure, show a "Session expired, please log in" message instead of a silent redirect.

---

### Q23: The `messages.index` column — what type of index? B-tree? Is there a missing composite index?

**A:** Standard B-tree index (created by migration 4). The query pattern:

```sql
SELECT MAX(index) FROM messages WHERE conversation_id = $1
SELECT * FROM messages WHERE conversation_id = $1 AND index >= $2 ORDER BY index ASC
```

Both queries filter by `conversation_id` first, then `index`. A **composite index** on `(conversation_id, index)` would be significantly faster because the B-tree can seek to the exact conversation first, then range-scan within it.

**Even better — covering index:**

```sql
CREATE INDEX idx_messages_convo_index_covering
ON messages (conversation_id, index)
INCLUDE (role, content, model_used, tokens_used);
```

This stores the selected columns in the index itself — **index-only scan**, the fastest possible read path.

**Why it does not exist yet:** For <10K messages per conversation, the performance difference is negligible (<1ms). Pre-optimization at this stage.

---

### Q24: The frontend and backend both need the list of valid aspect ratios. How do you keep them in sync?

**A:** By making the backend the only definition. `ASPECT_RATIOS` lives once, in `services/comfy.py`, as the list of display strings the ComfyUI ResolutionSelector node accepts. Three consumers reference that single list: the `ImageRequest` validator (rejects anything not in it with a 422), **`GET /v1/images/aspect-ratios`** (serves the list + default as JSON), and the frontend dropdown (populated from that endpoint, never hardcoded). Adding a ratio is a one-line change that propagates everywhere.

**The design lesson:** whenever a constant is validated on one side and displayed on the other, serve it over the API — duplicating it is a slow-motion desync bug. (This was originally duplicated and got fixed exactly that way.)

---

### Q25: Walk me through the schema evolution across the migrations.

Twelve migrations (the full table with revision IDs is in §5). The narrative arc:

1. **MVP (1):** `users`, `conversations`, `messages`, `refresh_tokens` — auth + chat.
2. **Feature accretion (2–6):** `last_active`, the pgvector `memories` table, the monotonic `messages.index`, `presets`, `prompt_templates`.
3. **Security retrofit (7):** refresh tokens hashed after an audit found plaintext JWTs in the DB.
4. **Image power-features (8):** user-uploaded ComfyUI `workflows` (JSONB graph + param_map).
5. **The incident (9 + 12):** migration 9 was generated **empty** while the model gained the conversation-branching columns — model and DB silently diverged, and every conversation query failed on a fresh database (`UndefinedColumnError: conversations.parent_id`). Migration 12 retro-added `parent_id` and `branched_from_message_id`. The lesson: an autogenerated migration with an empty `upgrade()` right after a model change is a red flag — always read the diff.
6. **Agent & research infrastructure (10, 11):** `tool_permissions` (per-tenant tool grants, UNIQUE(user_id, tool_name)) and `research_jobs` (background-job state).

**Still missing by choice:** a composite index on `messages(conversation_id, index)` (premature at current scale) and a vector index on `memories.embedding` (full scan is sub-ms until ~10k rows).

---

### Q26: Your agent tool permissions are "allow first-party, deny MCP by default." Defend that policy — and where exactly is it enforced?

**A:** First-party tools (`recall_recent_exchanges`, `web_search`, `fetch_page`) are code we wrote, reviewed, and bounded; an MCP server is **arbitrary third-party capability** — a filesystem server can read files, a shell server can run commands. Opt-in per user is the only sane default for capability you didn't author.

Enforcement is layered, deliberately: (1) the permission resolution itself — an explicit `tool_permissions` row wins, otherwise `tool.first_party` decides; (2) **schema withholding** — tools the user can't use are never advertised to the model, so the model doesn't even know they exist; (3) **dispatch re-check** — `_execute_tool` only dispatches tools found in the allowed map, so a hallucinated or prompt-injected tool name gets an error string back, never execution. The reason for both (2) and (3): (2) is the cheap path that shapes model behavior; (3) is the actual security boundary — never trust the model's output to respect the menu it was shown.

---

### Q27: Why does the agent loop call the model non-streamed, and what does the client lose?

**A:** When you stream with `tools=[...]`, tool calls arrive as **fragmented deltas** — the function name in one chunk, the arguments JSON split across many — and you must reassemble them per `tool_call.index` before you can execute anything. That's pure bookkeeping complexity with real correctness risk (truncated JSON args), and it buys nothing for the tool rounds themselves since you can't execute a half-received call anyway.

So the first cut trades latency for correctness: each round completes non-streamed; `tool_call`/`tool_result` events stream out between rounds (the UI still shows live progress); the final answer arrives as one `token` event instead of token-by-token. The user loses time-to-first-token on the final answer only. The documented optimization path: keep tool rounds non-streamed, but once a round returns no tool calls, that response *is* the answer — the upgrade is streaming that last round with delta-reassembly, isolated to one place.

---

### Q28: Walk me through the Redis pub/sub → SSE bridge for research progress. What race conditions did you have to think about?

**A:** The worker publishes JSON events to `research:{job_id}` at every stage; the API's stream endpoint subscribes and forwards each message as a `data:` line, emitting an SSE comment (`: keepalive`) every 15s of silence so proxies don't kill the idle connection.

Races considered: (1) **Late subscriber** — the client connects after the job finished; pub/sub messages are fire-and-forget, so they'd wait forever. Fix: *snapshot-then-subscribe* — emit the job's current Postgres state first, and if it's already terminal, emit the final event and return without subscribing at all. (2) **Snapshot/subscribe gap** — an event published in the sliver between the DB read and the SUBSCRIBE is lost; tolerated because the next stage commit produces another event and the poll endpoint is always truthful (Postgres is the source of truth, pub/sub is only the live view). (3) **Cancel vs. pickup** — cancelling a `queued` job races the worker dequeuing it; the cancel endpoint flips the row to `cancelled` directly, and the worker's first action is to skip any job whose status isn't `queued`. The general principle: **state lives in Postgres; Redis only accelerates visibility.**

---

### Q29: Why arq for the research queue when Celery is the industry standard?

**A:** The codebase is async end-to-end — asyncpg, httpx, async SQLAlchemy. Celery and RQ are sync-first: every job would wrap its coroutine in `asyncio.run`, creating a fresh event loop per job, with two mental models for I/O in one codebase. arq runs coroutines natively on one loop, uses the Redis that's already deployed (no RabbitMQ), and its entire config is a `WorkerSettings` class (`functions`, `job_timeout=900`, `max_jobs=2`, `keep_result=0` because job state lives in Postgres, not arq's result backend). Celery earns its complexity at the scale of routing topologies, rate-limited queues, and beat schedules — none of which a single-box research feature needs. The trade-off accepted: arq's ecosystem is smaller (fewer monitoring tools, no Flower equivalent), mitigated by keeping all observable state in our own DB.

---

### Q30: Your cookbook claims a model "needs 6.2 GB VRAM." Defend that number.

**A:** I won't defend the number — I'll defend the *labeling*. It's an explicit heuristic: weights from file size when Ollama reports it, else `params × bytes/param` from a quantization table (q4≈0.60, q8≈1.10, f16=2.0 bytes/param, params parsed from the model name); KV cache as `ctx_tokens × 128KB × (params/7B)` — a GQA-era ballpark; ×1.1 for runtime overhead. Real usage varies with attention implementation, GQA head counts, context actually used, and runtime allocator behavior.

The design choices that make a rough number useful: verdict **bands** (`fits_fully` / `partial_offload` / `wont_fit`) rather than false precision; a `?context_tokens=` knob because KV cache dominates at long context and users should see that; every verdict carries a human-readable rationale showing the arithmetic; and unknown-size models are labeled `unknown` instead of guessed. When the heuristic misclassifies a borderline model, the bands fail soft — `partial_offload` (weights ≤ RAM and need ≤ VRAM + RAM) still runs, just slower.

---

### Q31: A fresh `docker compose up` once had every conversation query failing with `UndefinedColumnError`. Diagnose it.

**A:** Model/schema drift: the `Conversation` model had gained `parent_id` and `branched_from_message_id`, but the Alembic migration generated alongside that change was **empty** — `upgrade(): pass`. On the developer's long-lived database the columns may as well have existed (or the path was never exercised), so nothing failed locally; on any *fresh* database, SQLAlchemy's `select(Conversation)` listed all mapped columns and Postgres rejected the unknown ones. Every endpoint touching conversations 500'd.

Why autogenerate produced nothing is the interesting part — likely the dev DB already had the columns (added manually during experimentation), so the diff was empty. Fixes and takeaways: a retroactive migration added the columns; **always read an autogenerated migration's diff against the model change you just made**; and the real systemic guard is CI that runs `alembic upgrade head` against a scratch database and boots the app — schema drift then fails the build, not the demo.

---

### Round 2 — The Bad-Day Grilling (Q32–Q47)

> Same hiring manager. Worse day. These questions go

| Priority | Issue | Impact |
|---|---|---|
| **High** | Frontend has no parser/UI for the agent + research SSE schemas (and no cookbook page) | Roadmap features unusable from the UI |
| **High** | Client disconnect mid-stream skips `save_messages` | Lost exchange on tab close during generation |
| **Medium** | No retry on failed token refresh (frontend) | Single network blip causes logout |
| **Medium** | Optimistic updates do not roll back on error (frontend) | Phantom messages in UI |
| **Medium** | No circuit breaker / backfill for embedding outages | Permanent semantic-memory gaps while Ollama is down |
| **Medium** | Agent tool transcript not persisted | Tool steps exist only in the live stream |
| **Medium** | `memories` not copied on conversation branch | Branches start with empty RAG context |
| **Medium** | No pagination anywhere (convo list, messages, presets…) | Breaks at 10K+ rows |
| **Medium** | `workflows.user_id` still nullable | Ownership not DB-enforced (planned migration) |
| **Low** | No composite index on `(conversation_id, index)`; no vector index on embeddings | Query overhead at scale (deliberate until measured) |
| **Low** | Image bytes live on ComfyUI's filesystem via `/view` URLs | Planned move to S3/MinIO + metadata rows |
| **Low** | No queue for SDXL rewrite model loads | Concurrent image requests can thrash LM Studio |
| **Low** | DDG HTML scraping as default search | Fragile; SearXNG profile is the robust path |

---

## 29. System Design Questions & Answers

> **Context:** 30 system design questions (10 easy, 10 medium, 10 hard) focused on the llm-gateway project. Each answer covers architecture decisions, trade-offs, and concrete implementation patterns.

---

### 29.1 Easy Questions

#### E1: Design a multi-tenant version where different teams share infrastructure but cannot see each other data.

**A:** Add an `organization_id` column to every tenant-scoped table plus a middleware layer that injects the tenant filter into every query. Use PostgreSQL Row-Level Security as defense-in-depth:

```sql
CREATE POLICY org_isolation ON conversations
USING (org_id = current_setting('app.current_org_id')::UUID);
```

**Schema additions:**
- `organizations` table: id, name, slug, settings JSONB
- `org_members` table: user_id, org_id, role (admin/member/viewer)
- Every tenant-scoped table: `+ org_id` column

**Isolation models — pick based on compliance needs:**
- **Silo (DB per tenant):** Best isolation, worst ops overhead (healthcare/finance)
- **Pool (same DB, separate schema):** Good for large tenants
- **Bridge (shared schema, row filter):** Best resource utilization, use RLS as safety net

**Trade-off:** The row-level approach requires discipline — any query that forgets `org_id` leaks data. RLS catches those leaks automatically.

---

#### E2: How would you deploy across multiple regions for low latency?

**A:** Multi-region requires solving data locality, routing, and state synchronization.

**Architecture per region:** Caddy → FastAPI (auto-scaled) → PostgreSQL (read replica) + Redis → Local AI providers (Ollama, LM Studio, ComfyUI)

**PostgreSQL — active/passive streaming replication.** One primary (write) per region, replicas globally. Use `pgcat` for connection routing: writes to primary, reads to nearest replica.

**Redis — CRDT-based active-active.** Rate limit counters using conflict-free replicated data types. Accept that rate limits are slightly different per region.

**Global load balancer — DNS-based (Cloudflare/Route53).** Route users to nearest region via latency-based routing.

**User data locality — sticky routing.** Store user→home_region mapping. Route all requests for a user to their home region. A traveling user's first request in a new region is slower (cross-region DB read), subsequent requests hit local cache.

**Token refresh — region-prefixed tokens** (`us_east_<jti>`) so rotations are always local, avoiding cross-region race conditions.

---

#### E3: Design a comprehensive logging and monitoring system.

**A:** Three pillars — structured logging, distributed tracing, and metrics with alerting.

**Pillar 1 — Structured Logging (ELK):** FastAPI → Filebeat → Logstash → Elasticsearch → Kibana. Each log entry is JSON with: request_id, user_id, conversation_id, provider, model, latency_ms, token_count, error traceback.

**Pillar 2 — Distributed Tracing (OpenTelemetry + Jaeger):** Trace every request through the call chain: frontend → Caddy → FastAPI → Provider API → DB. Use `traceparent` header propagation. Waterfall view in Jaeger for debugging slow requests.

**Pillar 3 — Metrics & Alerting (Prometheus + Alertmanager + PagerDuty):**

```yaml
alerts:
  - p99_latency > 30s for 5m          → Critical
  - error_rate > 5% for 5m            → Critical
  - embedding_failure_rate > 5%       → Warning (Ollama issue)
  - refresh_token_failure_rate > 10%  → Warning (potential bug)
```

**Integration with existing project:** Extend `record_metrics()` to cover ALL endpoints, not just chat. Add `loguru` for structured JSON logging.

---

#### E4: Design a file upload and attachment system for chat.

**A:** Allow users to upload images, PDFs, and text files as message attachments.

**Upload flow:** Frontend drag-and-drop → multipart POST `/v1/files/upload` → validate type/size (10MB max) → store to S3/filesystem → return file_id. Include file_id in chat message.

**Processing in chat pipeline:**
- Image: send to vision model (GPT-4o, LLaVA) or caption for text-only models
- PDF: extract text via PyMuPDF, truncate to 3000 tokens
- Text: read as-is, truncate to 3000 tokens
- Prepend as system context: `[Attached: report.pdf]\n{extracted_text}`

**Storage abstraction:** `StorageBackend` interface — local filesystem for dev, S3 for production. Use `asyncio.to_thread` for blocking file I/O.

**Thread-safety:** Offload file writes to thread pool to avoid blocking the event loop.

---

#### E5: Design a webhook system that notifies external services on conversation events.

**A:** Users register webhook URLs that receive POST requests with response payloads.

**Schema:** `webhooks` table: id, user_id, url, secret (HMAC key), events[] (message.created, image.complete), is_active, retry_count.

**Dispatch flow:**

```python
async def dispatch_webhooks(event_type, payload, user_id, db):
    webhooks = await db.execute(select(Webhook).where(...)).scalars().all()
    for webhook in webhooks:
        asyncio.create_task(send_webhook(webhook, event_type, payload))

async def send_webhook(webhook, event_type, payload):
    body = json.dumps({"event": event_type, "data": payload})
    sig = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    for attempt in range(webhook.retry_count):
        try:
            await httpx.AsyncClient(timeout=10).post(webhook.url, content=body,
                headers={"X-Webhook-Signature": sig, "X-Webhook-Event": event_type})
            return
        except Exception:
            await asyncio.sleep(2 ** attempt)
    logger.error(f"Webhook {webhook.id} failed after {webhook.retry_count} attempts")
```

**Security:** HMAC-SHA256 signature in `X-Webhook-Signature` header lets receivers verify payload authenticity.

**Production reliability:** Push events to Redis list (BRPOP) instead of `asyncio.create_task`. Background worker processes queue with persistence across restarts.

---

#### E6: Design a response caching system to reduce API costs for repeated identical queries.

**A:** Cache LLM responses for identical (model, messages, temperature, seed) combinations using a **cache-aside** pattern.

**Cache key:** SHA-256 hash of canonical JSON: `{model, messages (sorted), temperature (rounded), seed}`.

**Flow:**
```python
cache_key = f"llm_cache:{hashlib.sha256(canonical.encode()).hexdigest()}"
cached = await redis.get(cache_key)
if cached:
    for token in json.loads(cached): yield token
    return
# Stream fresh, collect, cache
tokens = []; async for t in stream(): tokens.append(t); yield t
await redis.setex(cache_key, 3600, json.dumps(tokens))
```

**When caching helps:** Deterministic responses (temperature=0), retry after failure, shared system prompts. Does NOT help: temperature > 0, unique long prompts, time-sensitive queries.

**Cache invalidation:** TTL-based (1 hour default) + manual endpoint `POST /v1/cache/invalidate` + auto-invalidation on preset update.

**Storage:** Redis with maxmemory policy `allkeys-lru`. Dedicated Redis instance for cache at scale.

**Cache hit rate target:** 15-20% of requests (retries + common prompts). At scale, a 20% hit rate on 100M tokens/day saves ~$200/day.

---

#### E7: Design a health check and readiness probe system for zero-downtime deployment.

**A:** Three levels — liveness, readiness, deep health.

**Level 1 — Liveness (is the process alive?):** `GET /healthz` — returns 200 if process is running. No external dependencies. Used by Docker HEALTHCHECK.

**Level 2 — Readiness (can it serve traffic?):** `GET /readyz` — checks PostgreSQL and Redis connectivity. Returns 503 if dependencies are down. Used by load balancer target groups to stop routing traffic to unhealthy instances.

**Level 3 — Deep Health (can it serve REAL requests?):** `GET /health/detail` — probes Ollama, ComfyUI, OpenRouter, checks memory usage, concurrent request count. Requires auth. Used by operators and dashboards.

**Zero-downtime deployment flow:**
1. New container starts → liveness passes
2. Readiness fails (no DB yet) → container waits
3. Connects to DB, runs migrations → readiness passes
4. Load balancer routes traffic to new instance
5. Old container receives SIGTERM → finishes in-flight requests
6. Readiness fails → load balancer drains connections
7. Old container exits

---

#### E8: Design a conversation export feature (JSON, Markdown, PDF).

**A:** Allow users to export individual conversations or bulk-export all data for portability.

**Endpoints:**
- `GET /v1/convo/{id}/export?format=json|md|txt|pdf` — single conversation
- `POST /v1/user/export` — background task: zip all conversations + presets + templates

**Markdown formatter:**
```python
def format_markdown(convo, messages):
    lines = [f"# {convo.title}", f"*Exported: {datetime.utcnow().isoformat()}*", ""]
    for msg in messages:
        role = "**You**" if msg.role == "user" else f"**{msg.model_used}**"
        lines.append(f"### {role}\n{msg.content}\n")
    return "\n".join(lines)
```

**Bulk export flow:** POST returns task_id. Background worker generates archive, stores in temp storage. Frontend polls `GET /v1/user/export/{task_id}` for status. Download link expires after 24 hours.

**Data portability:** JSON export mirrors the database schema so users can re-import into another instance. Include schema version field for forward compatibility.

---

#### E9: Design a "Delete Account" feature with cascading data removal.

**A:** Soft delete with a grace period, plus a hard delete worker.

**Soft delete flow:**
1. `POST /auth/delete-account/request` — verify password, set `deletion_scheduled_at = now + 7 days`
2. Send confirmation email with cancel link
3. `POST /auth/delete-account/cancel` — reset `deletion_scheduled_at` to NULL

**Hard delete worker (cron):**
```python
async def process_pending_deletions():
    users = await db.execute(select(User).where(
        User.deletion_scheduled_at <= datetime.utcnow()
    )).scalars().all()
    for user in users:
        await db.execute(delete(Conversation).where(Conversation.user_id == user.id))
        await db.execute(delete(Preset).where(Preset.user_id == user.id))
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
        await db.execute(delete(User).where(User.id == user.id))
    await db.commit()
```

**In-flight request handling:** Set `user_deleted` flag on user record. Middleware rejects new requests with 410 Gone. In-flight SSE streams complete normally (DB session holds reference).

**Data retention:** Configurable grace period (7 days) + anonymized backup retention (30 days) before permanent purge.

---

#### E10: Design an admin dashboard with usage statistics and user management.

**A:** Admin-only route section with read-only access to system-wide metrics and user management.

**Admin middleware:** Check user email against `ADMIN_EMAILS` env var (configurable). Return 403 for non-admins.

**Usage stats endpoint:** `GET /admin/stats` — returns total_users, active_today, total_conversations, tokens_by_provider, requests_by_hour, average_latency, error_rate, top_models.

**User management:** `GET /admin/users` (paginated, searchable), `DELETE /admin/users/{id}` (cascade delete).

**Frontend — Admin page tabs:**
- **Overview:** Cards with key metrics (users, requests, latency)
- **Users:** Table with search, sort by usage, bulk actions
- **Providers:** Cost breakdown by provider
- **Models:** Usage heatmap, peak hours
- **Logs:** Recent errors with severity/endpoint/user filters

**Security:** Admin routes behind VPN/SSH tunnel in production. Audit log every admin action. Rate limit admin endpoints separately.

---

### 29.2 Medium Questions

#### M1: Design a multi-provider model routing system that scales to 20+ providers with cost optimization.

**A:** Replace the 6-rule `match/case` with a config-driven routing table evaluated at runtime. Providers are defined in YAML, not Python code.

**Provider config (providers.yaml):**
```yaml
providers:
  - id: openai
    base_url: "https://api.openai.com/v1"
    api_key_env: "OPENAI_API_KEY"
    models: ["gpt-4o", "gpt-4o-mini"]
    pricing: {prompt: 0.00001, completion: 0.00003}
    capabilities: [text, vision, function_calling]
    weight: 100
  - id: local-ollama
    base_url: "http://host.docker.internal:11434/v1"
    api_key: "ollama"
    models: ["llama3:*", "mistral:*"]
    pricing: {prompt: 0, completion: 0}
    capabilities: [text, embeddings]
    weight: 200  # Highest weight = prefer local by default
```

**Routing engine phases:**
1. **Explicit match** — Does the requested model name match a provider?
2. **Capability-based routing** — What capabilities does this request need (text, vision, code)?
3. **Cost-aware selection** — Pick the cheapest healthy provider from candidates

**Cost optimization strategies:**
- Cheapest first: route to lowest-cost provider meeting capability requirements
- Fallback chain: if cheapest fails, try next cheapest
- Context-aware: short conversations → cheap models; complex code → powerful models
- Latency budget: user sets max latency; engine picks fastest provider within budget

**Provider health tracking:**
```python
class ProviderHealth:
    def record_failure(self, provider_id):
        self.failures[provider_id] += 1
        if self.failures[provider_id] >= 5:
            self.circuit_breakers[provider_id] = time.time() + 60  # Open for 60s
```
The routing engine skips unhealthy providers unless they are the only option.

---

#### M2: Design a token usage tracking and billing system.

**A:** Track every token consumed by every user, aggregate by time period, optionally charge.

**Schema:**
```sql
CREATE TABLE token_usage (
    id UUID PK, user_id UUID FK, conversation_id UUID FK,
    provider VARCHAR(64), model VARCHAR(128),
    prompt_tokens INT, completion_tokens INT,
    total_tokens GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,
    cost DECIMAL(12,8), created_at TIMESTAMP DEFAULT NOW()
);
```

**Batch inserts:** Buffer 100 records in memory, flush to DB in one `add_all()` call for performance.

**Billing tiers:**
```python
TIERS = {
    "free": {"monthly_token_limit": 100_000, "price": 0},
    "pro":  {"monthly_token_limit": 1_000_000, "price": 9.99},
    "team": {"monthly_token_limit": 10_000_000, "price": 49.99}
}
```

**Hard limit enforcement:** Check `get_monthly_usage(user_id)` before each chat request. Return 429 if over limit.

**Frontend dashboard:** Current period usage (progress bar), daily breakdown (bar chart), cost by model (pie chart), cost by conversation (table), projected monthly cost.

---

#### M3: Design a conversation search feature combining full-text and semantic search.

**A:** Hybrid search — PostgreSQL full-text (tsvector) + pgvector (semantic). Results fused by Reciprocal Rank Fusion (RRF).

**Full-text (PostgreSQL GIN index):**
```sql
ALTER TABLE messages ADD COLUMN search_vector tsvector
GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;
CREATE INDEX idx_messages_search ON messages USING GIN (search_vector);
```

**Semantic (existing pgvector):** Use `get_embedding()` to vectorize the query, `<=>` operator for cosine distance.

**RRF fusion:**
```python
def fuse_results(fts_results, semantic_results, k=60):
    scores = defaultdict(float)
    for rank, r in enumerate(fts_results):     scores[r.id] += 1.0 / (k + rank + 1)
    for rank, r in enumerate(semantic_results): scores[r.id] += 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)
```

**Performance targets:** Full-text <10ms (GIN index), Semantic <50ms (IVFFlat index), Fusion <1ms.

**UI:** Ctrl+K search dialog, results grouped by conversation with highlighted snippets, infinite scroll.

---

#### M4: Design a real-time collaboration feature (multiple users in same conversation).

**A:** Multiple users view and contribute to the same conversation simultaneously via Redis Pub/Sub + SSE.

**Schema additions:**
```sql
CREATE TABLE conversation_participants (
    conversation_id UUID, user_id UUID, role VARCHAR(16),
    PRIMARY KEY (conversation_id, user_id)
);
```

**Events dispatched via Redis Pub/Sub:**
- `new_message` — user sends a message (streaming tokens)
- `typing` — user is typing
- `presence` — cursor position, scroll position

**Conflict resolution — last-writer-wins:** Messages are never edited/deleted by other users. Ordering by `created_at ASC, user_id ASC` for deterministic tie-breaking.

**SSE channel:** Each subscribed user receives events from the conversation channel. Typing indicators show "User B is typing..." Cursor markers show where others are reading.

**Scope limits:** 5 concurrent participants per conversation. Use WebSocket instead of SSE for lower latency (bidirectional typing events).

---

#### M5: Design a model fallback and retry system for provider failures.

**A:** Classify failures, retry transient ones, fall back to alternative providers.

**Failure classification:**
- **TRANSIENT** (timeout, 429, 503) — retry 3x with exponential backoff (1s, 2s, 4s)
- **PROVIDER_DOWN** (connection refused) — try fallback provider immediately
- **PERMANENT** (400, 401) — do not retry

**Fallback chain:**
```python
async def execute_with_fallback(request):
    preferred = await resolve_provider(request)
    fallback_chain = [preferred] + get_alternative_providers(request)
    for provider, model in fallback_chain:
        try:
            return await stream_from_provider(provider, model)
        except TransientFailure:
            await asyncio.sleep(backoff[attempt])
            continue
        except PermanentFailure:
            raise
    raise HTTPException(503, "All providers unavailable")
```

**Streaming-aware retry (client-side):**
```typescript
async function connectWithRetry() {
    while (reconnectAttempts < 3) {
        try {
            for await (const token of streamChat()) { appendToken(token); }
            return;
        } catch {
            await new Promise(r => setTimeout(r, 1000 * 2^attempt));
        }
    }
}
```

**Monitoring:** Track provider error rates in Prometheus. Alert when >5%. Auto-disable providers with >20% error rate over 5 minutes.

---

#### M6: Design a prompt caching layer to reduce API costs for shared system prompts.

**A:** When multiple conversations share the same system prompt + initial messages, the provider may cache the prefix and charge only for the new suffix tokens.

**Anthropic-style explicit caching:**
```python
def build_messages_with_cache(messages, system_prompt):
    result = []
    result.append({"role": "system", "content": [{"type": "text", "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}}]})
    # History from messages[:-1] also gets cache_control
    for msg in messages[:-1]:
        result.append({"role": msg["role"], "content": msg["content"]})
    result[-1]["cache_control"] = {"type": "ephemeral"}
    # Current message is NOT cached (changes every time)
    result.append({"role": "user", "content": messages[-1]["content"]})
    return result
```

**Design for max cache hits:** Consistent system prompts (use presets), deterministic history ordering (chronological), preserve cache across retries.

**Provider support:** OpenAI has automatic prefix caching (discounted cached tokens). Anthropic requires explicit `cache_control` blocks. OpenRouter passes through to upstream.

**Cache hit tracking:**
```python
async def record_cache_result(provider, model, was_hit, cached_tokens):
    key = f"cache_stats:{provider}:{model}:{date.today()}"
    pipe.hincrby(key, "hits" if was_hit else "misses", 1)
    pipe.expire(key, 86400 * 7)
```

**Limitation:** Cache is per-provider, per-model. Switching providers invalidates. TTL is provider-dependent (OpenAI: 5-10 min inactivity).

---

#### M7: Design a distributed rate limiting system across multiple backend instances.

**A:** Sliding window log with Redis, optimized for high throughput with local caching.

**Sliding window algorithm:**
```python
async def is_allowed(self, key: str) -> bool:
    now = int(time.time() * 1000)
    window_start = now - self.window_ms
    pipe = self.redis.pipeline()
    pipe.zremrangebyscore(f"rate:{key}", 0, window_start)
    pipe.zcard(f"rate:{key}")
    pipe.zadd(f"rate:{key}", {str(now): now})
    pipe.expire(f"rate:{key}", self.window_ms // 1000 + 1)
    results = await pipe.execute()
    return results[1] <= self.max_requests
```

**Optimization — sliding window counter (less memory):** Split window into 1-second buckets. Increment current bucket, sum all buckets in window. Uses 1/60th the memory of the sorted set approach.

**Multi-tier rate limiting:**
```python
RULES = [
    ("anon_ip:{ip}",              10,   60),  # Anonymous IPs
    ("user:{user_id}",             30,  60),  # Authenticated users
    ("user:{user_id}:chat",        20,  60),  # Chat specific
    ("user:{user_id}:images",       3,  60),  # Image generation (expensive)
    ("global:chat",              1000,  60),  # Global chat rate
]
```

**Scaling to 10M+ req/day:** Shard Redis keys by user ID hash. Cache rate limit decisions in-memory for 100ms (absorbs write spikes, small inaccuracy window). Use Redis pipeline for atomic operations (single round-trip).

---

#### M8: Design an image gallery system with albums, sharing, and thumbnails.

**A:** Extend the image generation feature with persistent storage, organization, and sharing.

**Schema additions:** `images` (prompt, parameters, storage_paths, is_public), `albums` (name, cover_image), `album_images` (order), `shares` (share_token, expires_at, max_views).

**Thumbnail generation:**
```python
async def generate_thumbnail(image_path, size=(256, 256)):
    def _resize():
        img = Image.open(image_path)
        img.thumbnail(size, Image.LANCZOS)
        img.save(image_path.replace("/images/", "/thumbnails/"), "WEBP", quality=85)
    await asyncio.to_thread(_resize)  # Offload to thread pool
```

**Endpoints:**
- `GET /v1/images` — paginated list with filters (date, model, prompt)
- `POST /v1/albums` — create album, add images
- `POST /v1/shares` — generate shareable link with expiry + view limit
- `GET /s/{share_token}` — public share page (no auth)

**Public share page:** No auth required. Returns image + prompt + parameters. View counter increments. Expires when `view_count >= max_views` or `expires_at` passes.

**Storage cleanup:** Background task deletes orphaned files (image records deleted but files remaining). Runs daily.

---

#### M9: Design a plugin/extension system for custom tools.

**A:** Plugins hook into the chat pipeline at defined stages. Each plugin has a manifest, executes in a sandboxed environment.

**Plugin manifest (plugin.yaml):**
```yaml
hooks:
  on_message_send:      priority: 10, handler: process_input
  on_response_token:    priority: 5,  handler: process_token
  on_response_complete: priority: 20, handler: post_process
```

**Sandbox execution (RestrictedPython):**
- ALLOWED_MODULES: json, re, math, textwrap only
- MAX_EXECUTION_TIME: 1 second
- MAX_MEMORY: 50MB
- No filesystem or network access

**Hook registry:**
```python
class PluginRegistry:
    async def execute_hook(self, hook_name, context):
        for hook in sorted(self.hooks[hook_name], key=lambda h: h.priority):
            context = await hook.plugin.execute(hook.handler, context)
        return context
```

**Pipeline integration:**
- Before AI call: `context = await registry.execute_hook("on_message_send", context)`
- During streaming: `context = await registry.execute_hook("on_response_token", context)`
- After completion: `context = await registry.execute_hook("on_response_complete", context)`

**Plugin store:** `POST /v1/plugins/install` downloads manifest + code from URL, validates, registers. Hot-reload without server restart.

---

#### M10: Design a message edit and version history system.

**A:** Allow users to edit past messages and view the edit history without losing context.

**Schema:**
```sql
ALTER TABLE messages ADD COLUMN current_version INT DEFAULT 1;
CREATE TABLE message_versions (
    id UUID PK, message_id UUID FK, version INT,
    content TEXT, edited_by UUID, edited_at TIMESTAMP,
    edit_reason VARCHAR(64),  -- correction, clarification, regeneration
    UNIQUE (message_id, version)
);
```

**Edit endpoint:**
```python
@router.patch("/v1/convo/{convo_id}/messages/{msg_id}")
async def edit_message(convo_id, msg_id, edit, user_id, db):
    msg = await db.execute(select(Message).where(Message.id == msg_id)).scalar_one()
    db.add(MessageVersion(message_id=msg.id, version=msg.current_version,
           content=msg.content, edited_by=user_id, edit_reason=edit.reason))
    msg.content = edit.content
    msg.current_version += 1
    await db.commit()
```

**Reversion:** `POST .../revert/{version}` — save current as new version, restore old content.

**UI:** Right-click → "View edit history" → side panel with versions, timestamps, diffs, "Restore this version" button.

**Regeneration version chain:** Regeneration deletes the old assistant message (or versiones it) and creates a new one with higher index. The user message stays unchanged.

---

### 29.3 Hard Questions

#### H1: Design a system that serves 1M concurrent users with sub-500ms p99 chat latency.

**A:** 1M concurrent users ≈ 50K req/s. Sub-500ms p99 requires eliminating every bottleneck across 5 layers.

**1. Regional architecture:** Global DNS-based LB (Cloudflare Anycast) → Region (US/EU/APAC) → L7 LB → API Gateway (Kong) → Auto-scaled FastAPI (separate deployments for chat, images, auth) → Provider Proxy Pool.

**2. Pre-warmed provider connections:** Maintain persistent HTTP/2 connection pools to each provider. TLS handshake (50-200ms) happens at deployment time, not request time.

```python
clients = {
    "openai": httpx.AsyncClient(limits=Limits(max_connections=200)),
    "anthropic": httpx.AsyncClient(limits=Limits(max_connections=200)),
}
```

**3. Speculative execution:** Parallelize history loading + embedding generation + provider connection setup:

```python
history_task = asyncio.create_task(load_history(...))
embedding_task = asyncio.create_task(get_embedding(query))
history, embedding = await asyncio.gather(history_task, embedding_task)
```

**4. Read-through cache:** Cache recent conversations in Redis (5 min TTL). Cache hit: <5ms. Cache miss: load from DB + populate cache.

**5. Connection pooling for PostgreSQL:** Use pgbouncer in transaction pooling mode. Each instance keeps min(CPU_cores * 2, 20) connections.

**6. GPU-accelerated embedding:** Offload `nomic-embed-text` to GPU (CUDA). CPU: 5-10ms. GPU: <1ms.

**7. Request-based autoscaling (Kubernetes HPA):** Scale by `active_sse_connections` metric, not CPU. Target 50 connections per pod.

**Expected latencies:** p50 < 200ms (cached/local), p95 < 400ms (OpenRouter fast models), p99 < 500ms (pre-warmed + speculative execution). p99.9 > 2s (provider degradation — unavoidable).

---

#### H2: Design a distributed conversation store with vector search across regions.

**A:** Use CockroachDB (distributed SQL, automatic sharding, multi-region) for the primary store with region-local vector search.

**Why CockroachDB instead of standard PostgreSQL replication:**
- Automatic sharding — no manual partitioning
- Global tables survive region failure
- PostgreSQL-compatible (same queries, same ORM)
- Global tables with fast local reads

**Vector search — hierarchical:**

```python
class GlobalVectorSearch:
    async def search_global(self, query_vector, user_id, top_k=10):
        # Phase 1: Local region (<10ms)
        local = await self._search_region(get_current_region(), ...)
        # Phase 2: Remote regions (background parallel)
        remote = await asyncio.gather(*[
            self._search_region(r, ...) for r in user_regions if r != current_region
        ])
        # Phase 3: Merge and rerank
        return self._rerank(local + [r for res in remote for r in res], top_k)
```

**Consistency model:**
| Operation | Consistency | Why |
|---|---|---|
| Write message | LOCAL (wait for local ack) | User sees their message instantly |
| Read conversation | LOCAL | Always read from nearest replica |
| Vector search | EVENTUALLY (~3s) | Fresh embeddings not critical |
| User settings | STRONG (global consensus) | Must be consistent everywhere |

**Vector index — pgvectorscale DiskANN:** More memory-efficient than HNSW, supports concurrent reads across replicas. Built for large-scale distributed vector search.

**Failure modes:** Region isolation falls back to nearest available region. Vector search degrades to local-only with user indicator. Replication lag >5s triggers replica promotion.

---

#### H3: Design a real-time model health monitoring and auto-failover system.

**A:** Continuously probe every provider/model combination, detect degradation, reroute traffic within 35 seconds.

**Probe design — realistic health check:**
```python
async def probe(provider, model):
    start = time.time()
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Say: health_ok"}],
        max_tokens=5, timeout=10.0
    )
    latency = (time.time() - start) * 1000
    is_correct = "health_ok" in response.choices[0].message.content
    return HealthResult(status="healthy" if is_correct else "degraded", latency_ms=latency)
```

**Health aggregator — sliding window:** Evaluate last 10 probes per provider. Success rate > 70% = healthy. 40-70% = degraded. <40% = unhealthy.

**Auto-failover routing table:**
```python
def compute_routes(health_statuses):
    for capability in ["text", "vision", "code"]:
        candidates = [(p, m) for (p, m), s in health_statuses.items()
                      if s in (HEALTHY, DEGRADED) and supports(p, m, capability)]
        routes[capability] = candidates or all_available  # Degraded fallback
    return routes
```

**Incident response:**
1. UNHEALTHY → Remove from routing, alert PagerDuty, warm fallback providers, create incident
2. DEGRADED → Alert warning, monitor for 2 more probe cycles

**P99 restoration SLA:** Auto-failover detects degradation within 30 seconds (1 probe interval + evaluation) and reroutes within 5 seconds. Total: <35 seconds.

---

#### H4: Design a multi-modal conversation system (text, images, audio, video).

**A:** Support all content types as first-class message parts. Different models handle different modalities.

**Multi-part message format:**
```python
class ContentPart(BaseModel):
    type: str  # text, image_url, audio_url, video_url, file
    text: Optional[str] = None
    image_url: Optional[str] = None
```

**Processing pipeline:**
1. **Image:** Validate (20MB max, JPEG/PNG/WebP), generate thumbnail, store in S3. For vision models: base64 data URI. For text-only models: caption via LLaVA/Whisper, insert as text.
2. **Audio:** Transcribe with Whisper (local or API). Insert transcription as text context. Keep audio file for reference.
3. **Video:** Extract keyframes (1 per 5s), transcribe audio track. Send keyframes with timestamps to vision model.

**Vision model routing:**
```python
VISION_CAPABLE = {"openai/gpt-4o": True, "anthropic/claude-3-opus": True, "local/llava": True}
if VISION_CAPABLE.get(f"{provider}/{model}", False):
    return encode_media_for_api(messages, provider)  # Send media directly
else:
    return caption_and_replace(messages, db)  # Fallback: caption as text
```

**Frontend:** Drag-and-drop zone, microphone button (MediaRecorder API), camera capture, preview thumbnails, upload progress bar.

**Cost:** Vision API calls are 5-10x more expensive than text. Whisper transcription: $0.006/min (API) or free (local). Video capped at 30s for local processing.

---

#### H5: Design a system for fine-tuning models on user conversation data.

**A:** Allow users to fine-tune open-source models (Llama, Mistral) on their conversation data, creating custom models that match their communication style.

**Data pipeline:**
```python
class FineTuneDataPipeline:
    def prepare_dataset(self, user_id, db, format="chatml"):
        messages = db.execute(select(Message).join(Conversation)
            .where(Conversation.user_id == user_id)).scalars().all()
        dataset = []
        for msg in messages:
            current_turn.append({"role": msg.role, "content": msg.content})
            if msg.role == "assistant":
                dataset.append({"messages": current_turn})
                current_turn = []
        # 90/10 train/val split, save as JSONL
```

**Training (QLoRA via Unsloth):** Base model (e.g., Llama 3.2 8B) + LoRA adapters. Runs on a single RTX 4090, ~2-4 hours. LoRA adapter is ~50MB.

**Serving fine-tuned models (vLLM):**
- Load on demand per user
- Auto-unload after 1 hour of inactivity
- Route chat requests to user's fine-tuned model if one exists

**Privacy:** Data NEVER leaves the machine. Base model downloaded once. Fine-tuned weights stay local. No data sent to external APIs.

**Routing:** If user has a fine-tuned model, route to vLLM local endpoint. Otherwise, normal routing.

**Validation:** Minimum 100 messages required to fine-tune. Evaluate on holdout set before deploying.

---

#### H6: Design a global rate limiting and abuse detection system.

**A:** Three-layer defense: Edge (CDN/WAF), Stateless (per-IP/user/endpoint limits), Stateful (ML-based behavioral analysis).

**Layer 1 — Edge (Cloudflare/AWS Shield):** IP reputation, DDoS mitigation, geographic blocking.

**Layer 2 — Stateless multi-dimensional limits:**
```python
checks = [
    ip_based(request),        # 100 req/min per IP
    user_based(request),      # 30 req/min per user
    endpoint_based(request),  # 5 req/min for /auth/*
    global_based(),           # 5000 req/min system-wide
]
results = await asyncio.gather(*checks)
return all(results)
```

**Layer 3 — Behavioral ML detection:**
```python
features = {
    "requests_per_second": ..., "error_rate": ...,
    "unique_endpoints": ..., "time_since_first_seen": ...,
    "is_datacenter_ip": ..., "concurrent_sessions": ...,
}
score = onnx_model.predict([list(features.values())])[0]
```

**Prompt injection detection:** Regex patterns (DAN, system prompt override, role change) + ML-based for novel patterns.

**Credential stuffing detection:**
```python
if same_email_from_different_ips > 5:      # Password spray → block
if different_emails_from_same_ip > 20:     # Credential stuffing → block
if login_failures_for_same_email > 5:      # Brute force → block
```

**Graduated responses:**
| Score | Action |
|---|---|
| 0.3-0.5 | Add 500ms delay |
| 0.5-0.7 | Tighten rate limit |
| 0.7-0.9 | CAPTCHA challenge |
| >0.9 | Block (1 hour) |

**False positive handling:** Clear error messages, appeal endpoint, rate limit overrides for whitelisted users, weekly review of blocked entities.

---

#### H7: Design a consistent caching layer for LLM responses across geo-distributed regions.

**A:** Two-tier cache: local Redis (fast, small, region-local) + global DynamoDB/ScyllaDB (slower, large, cross-region). Cache keys based on exact match and semantic similarity.

**Cache key design:**
```python
def make_exact_key(model, messages, temperature):
    canonical = json.dumps({model, messages, round(temperature, 2)}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()

def make_semantic_key(model, query_embedding, temperature):
    quantized = [round(v, 4) for v in query_embedding[:64]]
    return f"sem:{hashlib.md5(json.dumps({model, quantized}).encode()).hexdigest()}"
```

**Two-tier get/set:**
1. Check local Redis (instant, <1ms)
2. If miss, check global DB (cross-region, ~50ms)
3. If global hit, asynchronously populate local cache for next request
4. If global miss, call provider, populate both caches

**Semantic cache — approximate nearest neighbor:**
```python
class SemanticCache:
    SIMILARITY_THRESHOLD = 0.92  # Cosine similarity
    async def search(self, model, query_embedding):
        candidates = await self.redis.zrevrange(f"sem_cache:{model}", 0, 100)
        for cache_key in candidates:
            cached_emb = json.loads(await self.redis.get(f"sem_emb:{cache_key}"))
            if cosine_similarity(query_embedding, cached_emb) >= SIMILARITY_THRESHOLD:
                return await self.redis.get(f"sem_resp:{cache_key}")
```

**Cross-region invalidation:**
| Event | Scope | Propagation |
|---|---|---|
| TTL expiry | Local | Lazy (read removes expired) |
| User edits message | Local only | No global impact |
| Model deprecated | Global | Pub/sub broadcast |
| Manual flush | Global | Immediate broadcast |

**Cache hit rate targets:** Exact match 15-20% (retries, shared prompts). Semantic match 5-10% (common questions). Total: 20-30%.

---

#### H8: Design a real-time streaming architecture with exactly-once message delivery semantics.

**A:** SSE streams are inherently at-least-once. Exactly-once requires idempotency keys, sequence number checkpointing, and deduplication.

**Checkpointed streaming:**
```python
class CheckpointedStream:
    async def emit(self, token):
        self.sequence += 1
        await self.redis.setex(f"stream:{self.id}:{self.sequence}", 300, token)
        await self.redis.setex(f"stream:{self.id}:seq", 300, self.sequence)
        yield f"data: {json.dumps({'seq': self.sequence, 'content': token})}\\n\\n"

    async def resume_from(self, last_seq):
        # Replay buffered tokens after last_seq, return next sequence to read
        while True:
            token = await self.redis.get(f"stream:{self.id}:{missed}")
            if not token: break
            yield token; missed += 1
        return missed
```

**Client-side deduplication:**
```typescript
class ReliableSSEClient {
    async connect(messages) {
        const response = await fetch("/v1/chat/completions", {
            headers: { "X-Last-Seq": String(this.lastSeq) }
        });
        for await (const token of this.readStream(response)) {
            if (token.seq <= this.lastSeq) continue; // Deduplicate
            this.lastSeq = token.seq;
            this.onToken(token.content);
        }
    }
}
```

**Idempotent provider calls:** Use idempotency key to ensure the provider API is called exactly once. Check Redis for existing completion before calling.

**Exactly-once guarantees in practice:**
- Provider call: Exactly once (idempotency key prevents duplicate charges)
- Token delivery to client: At least once (client deduplicates by seq#)
- Message save to DB: Exactly once (DB upsert with stream_id + seq as unique key)

**Trade-off:** Exactly-once adds latency (checkpoint writes on every token). For a self-hosted tool, at-least-once with client-side deduplication is sufficient.

---

#### H9: Design a cost-optimized multi-cloud AI gateway with spot instance failover.

**A:** Run AI inference across AWS, GCP, and Azure using spot instances for 50-70% cost savings. Handle preemption with graceful drain and automatic failover.

**Spot instance lifecycle:**
```python
class SpotManager:
    async def launch_inference_cluster(self, model_id, min_instances=2):
        for provider in [AWSProvider(), GCPProvider(), AzureProvider()]:
            try:
                instance = await provider.launch_spot(
                    instance_type="g5.xlarge", model_id=model_id,
                    max_price=0.30  # 30% of on-demand price
                )
            except SpotRequestFailed:
                continue  # Try next cloud
```

**Preemption handling (2-minute warning):**
1. Mark instance as draining (no new requests)
2. Wait up to 90s for active SSE streams to complete
3. Force-migrate remaining streams to on-demand fallback
4. Allow termination

**Cost optimization engine:**
```python
def select_instance(request):
    for instance in available:
        priority = 0
        priority += 100 if instance.type == "spot" else 0
        priority += int(1000 - instance.current_price * 100)
        priority += int(50 - instance.utilization * 10)
        if instance.termination_probability > 0.5: priority -= 500
    return sorted(candidates, reverse=True)[0]
```

**Pricing comparison (1x A10G GPU):**
| Cloud | On-Demand | Spot | Savings |
|---|---|---|---|
| AWS g5.xlarge | $1.006/hr | $0.302/hr | 70% |
| GCP L4 | $0.760/hr | $0.228/hr | 70% |
| Azure NC6s | $0.864/hr | $0.259/hr | 70% |

**Fallback chain:** Spot (cheapest region) → Spot (other cloud) → On-demand (cheapest) → 503 with estimated retry time.

**Real-world savings:** 50-70% cost reduction with <1% p99 latency increase from failover events.

---

#### H10: Design an A/B testing system for models and routing strategies.

**A:** Run experiments where different user segments get different model configurations. Measure quality, latency, cost, and engagement.

**Experiment framework:**
```python
class Experiment:
    variants: dict  # {variant_id: routing_config}
    traffic_split: dict  # {variant_id: percentage}
    assignment: str  # "user" (sticky) or "request" (random)
    metrics: list[str]  # response_time_p50, cost, thumbs_up_rate, retention
```

**Experiment config (experiments.yaml):**
```yaml
- id: "exp_001"
  name: "OpenRouter vs Local for Code"
  traffic_split: {control: 50, treatment: 50}
  assignment: "user"  # Consistent per user
  variants:
    control:  {routing: "default_6_rules"}
    treatment: {routing: {rules: [{condition: "contains(code_keywords)", action: "claude-3-sonnet"}]}}
```

**Sticky assignment — consistent bucket hashing:**
```python
hash_val = md5(f"{user_id}:{experiment_id}".encode()).hexdigest()
hash_int = int(hash_val[:8], 16) % 100
cumulative = 0
for variant_id, percentage in experiment.traffic_split.items():
    cumulative += percentage
    if hash_int < cumulative:
        return variant_id  # Cached for 24 hours (stickiness)
```

**Metrics collection:**
```python
async def record(experiment_id, variant_id, user_id, metrics):
    pipe = self.redis.pipeline()
    for metric_name, value in metrics.items():
        pipe.lpush(f"exp:{experiment_id}:{variant_id}:{metric_name}",
                   json.dumps({"user_id": user_id, "value": value}))
        pipe.ltrim(..., 0, 9999)  # Keep last 10K points
```

**Auto-promotion of winning variant:**
```python
def evaluate(experiment_id):
    p_value = mann_whitney_u(control_values, treatment_values)
    if p_value < 0.05 and treatment_mean > control_mean:
        promote_to_default(treatment_config)  # Winner!
```

**Metrics to track:** response_time (p50/p95), token cost per request, thumbs up/down rate, user retention (next-day), engagement (messages per session).

**Safety guards:** Minimum sample size (1000 users per variant), maximum experiment duration (14 days), automatic rollback if primary metric degrades >5%, manual override to stop any experiment immediately.

---

## 30. The Hostile Interview (Round 2)

> **Context:** A second pass from an interviewer who has had a genuinely terrible day, read the actual source before the meeting, and is in no mood for hand-waving. Every question below was verified against the real code first — none are strawmen. The tone is deliberately harsh; the *answers* are honest, because pretending a flaw doesn't exist is how you actually fail these. Where the right answer is "yes, that's a bug," it says so.

---

### HQ1: Your Prometheus metrics label everything by provider. I'm looking at `record_metrics(request.provider.value, ...)`. The request comes in with `provider="auto"`, your router resolves it to OpenRouter, and you record the label as... "auto." So your entire "cost by provider" dashboard is a lie. Explain yourself.

**A:** You're right, and it's a real bug — not a defensible trade-off. `get_provider()` resolves `auto` to a concrete client + model, but `stream_tokens` passes `request.provider.value` (the *requested* value) into `record_metrics`, not the resolved provider. Every auto-routed request — which is the default path — gets bucketed under the `auto` label. The `chat_requests_total`, `chat_latency_seconds`, and `*_tokens_total` series are therefore useless for the provider dimension exactly when it matters most.

The fix is small and I'd make it immediately: `get_provider` should return the provider identity alongside the client and model (it already computes `is_cloud` downstream from `client.base_url` — that derivation is the tell that the information exists but is thrown away), and that resolved identity is what gets recorded. The `model` label is correct (it's the resolved model), which actually makes the bug worse: you can see *gpt-4o-mini* ran but it's filed under provider *auto*, so the labels disagree with each other. One source of truth — resolve once, pass the resolved tuple to both the API call and the metrics.

---

### HQ2: `private: true` is your headline privacy feature. "Sensitive data stays local." Then `record_metrics` ships the full message list and the complete response to Langfuse. Walk me through how that's not a direct contradiction of the one promise your README leads with.

**A:** It is a contradiction, and it's the most serious issue you could have found. `private=True` correctly forces local routing in `get_provider`, so the message never reaches OpenRouter — that half works. But the `record_metrics` call at the end of `stream_tokens` runs unconditionally and passes `messages` and `full_response` into `langfuse.start_as_current_observation(...).update(input=..., output=...)`. If `LANGFUSE_*` is configured to Langfuse Cloud (the README's own example), a "private" conversation's full content is shipped to a third-party SaaS. The privacy guarantee is silently broken for the exact conversations a user marked as needing it.

The fix has to thread `private` (or better, a derived `record_content: bool`) down into `record_metrics` and, when set, skip the Langfuse trace entirely — or record only non-content metadata (latency, token counts, model) with input/output omitted. Prometheus is fine to keep: it stores counters and histograms, no message text. The deeper lesson is that "privacy" can't be a property of one function (the router); it has to be a property that travels with the request through *every* side-effecting call — routing, tracing, caching, logging. I enforced it in one place and left three other exits open. For a feature whose entire value proposition is the guarantee, that's a failing grade, and I wouldn't argue otherwise.

---

### HQ3: `get_redis()` is a lazy singleton with `if _redis_client is None`. No lock. It's the first thing your rate-limit middleware touches on every request. Cold start, a hundred concurrent requests — what happens, and don't tell me "it's probably fine."

**A:** Under a cold-start thundering herd, multiple coroutines all see `_redis_client is None`, all call `await aioredis.from_url(...)`, and the last assignment wins — the earlier pools are orphaned (created, never closed, garbage-collected eventually, their connections dangling until the server closes them). It's a connection leak, bounded by the concurrency of that first instant, not unbounded — which is exactly why it has never shown up in practice and never will at single-box scale.

But "probably fine" isn't the same as "correct," and the honest answer is that the `async def` with no `await` between the check and the assignment makes this *less* dangerous than it looks: within a single event loop, `if _redis_client is None` and the subsequent `await` mean the coroutine *can* yield at the `await aioredis.from_url`, so two coroutines genuinely can interleave here. The correct fix is an `asyncio.Lock` around the init, double-checked inside the lock, or — better — create the pool once in the `lifespan` startup (which already calls `await get_redis()` to warm it) and treat the lazy path as a fallback that effectively never runs concurrently because warmup happened before the server accepted traffic. The warmup is actually why this is a non-issue today: by the time request #1 arrives, `_redis_client` is already set. So it's a latent bug masked by startup ordering, not a live one — but I shouldn't rely on an accident of ordering for correctness. Same pattern exists in `core/queue.py`'s `get_queue()`.

---

### HQ4: You create the conversation row in `conversation()` *before* you call the model. The model call throws — provider down, bad request, whatever. Now walk me to that orphaned row and tell me why your conversation list isn't full of empty ghost threads.

**A:** The orphan absolutely gets created: `conversation()` does `db.add(new_convo); await db.commit()` and returns the id before `get_provider()` or the model call ever runs. If the completion then fails, the `conversations` row persists with zero messages. You found the create; the *mitigation* is on the read side — `GET /v1/convo` filters with `WHERE EXISTS (SELECT FROM messages WHERE conversation_id = conversations.id)`, so message-less conversations never appear in the list. The ghost exists in the table but is invisible to the UI.

I'll defend the *outcome* but not the *design*. The outcome is fine: no user-visible ghosts, and a later successful message in the same conversation id would light it up. But it's a cleanup liability — orphans accumulate forever, and any query that doesn't replicate the `EXISTS` filter (analytics, an admin view, a `COUNT(*)` on conversations) sees inflated numbers. The cleaner design is to **not commit the conversation until the first message succeeds**: create it in the same transaction as `save_messages`, or create it lazily and roll back on failure. The reason it's structured this way is that the conversation id has to exist *before* streaming starts (the SSE response references it, and `load_history`/memory queries key on it), so committing early was the path of least resistance. The right version assigns the id with `flush()` (no commit) and only commits at `save_messages` — but that means holding a transaction open across a multi-second stream, which has its own cost. So it's a genuine trade-off: orphan rows vs. long-held transactions. I chose orphans + a read filter; I'd flag it as debt, not call it solved.

---

### HQ5: `save_messages` does `SELECT ... FOR UPDATE` on the conversation, then `SELECT max(index)`. You told me in the doc this prevents colliding indices. Prove it actually holds under two simultaneous requests, because I think you're hand-waving about what that lock covers.

**A:** The proof rests on the lock being on the *conversation* row, not the messages, and on both writers taking it. Request A and B both enter `save_messages` for the same conversation. A executes `SELECT ... FOR UPDATE` on `conversations WHERE id = cid` and acquires the row lock. B executes the same statement and **blocks** — Postgres holds it at that line until A's transaction commits or rolls back. A then reads `max(index)`, inserts user=k+1/assistant=k+2, updates token_count, and commits, releasing the lock. B unblocks, *now* reads `max(index)` (which reflects A's committed inserts because B's read happens after A committed), and allocates k+3/k+4. No collision.

The subtlety that makes it correct — and the part that would be broken if I'd done it naively — is that the `FOR UPDATE` is on a row B also locks *before* B reads `max(index)`. If the lock and the read were on different rows, or if B read `max(index)` before taking the lock, the serialization would be defeated. Two failure modes I'll concede: (1) if a conversation row somehow didn't exist (it always does here, but defensively), `FOR UPDATE` locks nothing and the guard evaporates — the real belt-and-suspenders fix is a `UNIQUE(conversation_id, index)` constraint so the DB rejects a collision even if the lock logic is wrong; I rely on the lock alone, which is one mechanism, not two. (2) The lock serializes writers but also *serializes them* — two rapid messages to the same conversation are now strictly sequential, which is correct but not free. For a chat app where a single conversation has one human typing, that contention is irrelevant. I'd add the unique constraint to make it provably correct rather than argued-correct.

---

### HQ6: The agent loop appends the assistant's `tool_calls` message, then appends tool results. If the model emits three tool calls and your second tool times out, what exactly is in the message array you send back to the model, and will the provider even accept it?

**A:** All three results go back, and the timeout is one of them — as a string. The loop appends the assistant message echoing all three `tool_calls`, then iterates: for each call it yields the SSE event and appends a `{"role": "tool", "tool_call_id": tc.id, "content": result}` message. For the timed-out one, `_execute_tool` caught the `asyncio.TimeoutError` and returned `"Error: tool 'x' timed out after 30s"` — so its tool message has that error string as content, not a missing entry. Critically, **every** `tool_call_id` from the assistant message gets a matching tool message, which is the invariant the OpenAI-protocol providers enforce: a tool-calls turn must be followed by exactly one tool message per id, or the next request 400s with "tool_call_ids did not have response messages."

That invariant is the reason `_execute_tool` returns errors as strings instead of raising — if a tool failure propagated as an exception and skipped appending its tool message, the array would have three tool_calls but two tool responses, and the *next* model call would be rejected by the provider, killing the run with an opaque 400. So the error-as-string design isn't just "let the model adapt" (the framing in the doc) — it's load-bearing for protocol correctness. The model sees three results, one of which says it timed out, and can retry it, route around it, or answer without it. Where I'd push back on myself: I truncate results to 8000 chars but I don't cap the *number* of tool calls per round, so a model that emits twenty calls in one turn executes all twenty sequentially within the iteration — the per-tool timeout bounds each, but twenty × 30s is ten minutes against the 900s-irrelevant chat path. A per-round fan-out cap would bound that.

---

### HQ7: Your DDG fallback scrapes HTML with three regexes. The day DuckDuckGo changes their markup — and they will — what does `web_search` return, how does the agent react, and how long until you even notice?

**A:** It returns `"No results found."` — silently and plausibly. When the markup changes, `_DDG_TITLE_RE` matches nothing, `titles` is empty, the list comprehension yields `[]`, and `search()` returns an empty list, which the tool reports as "No results found." That string goes back to the model, which reasonably concludes there were no results for the query and either answers from its own knowledge or tries a different search — it has no way to distinguish "the web genuinely has nothing" from "my scraper is broken." The user gets a confident answer with no web grounding and no error.

Time-to-notice is the damning part: **never, automatically.** There's no health check on the scraper, no alert on a sustained zero-result rate, no distinction in the logs between "empty results" and "parse returned nothing." I'd find out when a user complained that research stopped citing sources. The mitigations, in order of value: (1) the real answer is **SearXNG is the supported path** and DDG is explicitly labeled a fragile fallback — set `SEARXNG_URL` and the JSON API replaces the scraper entirely, which is why the fallback's fragility is documented as a known limitation rather than hidden; (2) failing that, the parser should distinguish "fetched a page but extracted nothing" (likely a markup change — log a warning, ideally emit a metric) from "the page legitimately had no results," because a 200 response with unparseable body is a different event from an empty result set; (3) a canary query ("wikipedia") run periodically that asserts non-empty would catch markup drift within the canary interval. I shipped the keyless default for zero-config convenience and accepted the fragility consciously — but "silently returns empty and the model hallucinates around it" is a worse failure mode than "errors loudly," and for a research feature whose entire value is *grounding in sources*, failing open to ungrounded answers is the wrong default. SearXNG should arguably be required for the research feature, with DDG only for the casual agent tool.

---

### HQ8: No connection pool sizing on your async engine. `create_async_engine(url, echo=...)` and nothing else. You've got a streaming endpoint that holds a DB session open for the entire duration of a 60-second model response. Do the math on what happens at 30 concurrent chats.

**A:** SQLAlchemy's async engine defaults to `pool_size=5` with `max_overflow=10` — so 15 connections before the 16th waits, and it waits up to `pool_timeout` (30s default) before raising `TimeoutError`. The killer is exactly what you point at: `get_db` yields one session per request, and for a streaming chat that session is held from the moment the handler starts until the generator finishes — across the *entire* model response, because `save_messages` runs at the end and needs the session live. So a 60-second stream pins a connection for 60 seconds. At 30 concurrent chats, 15 get connections, 15 queue, and the queued ones start timing out at 30s — half your users get a 500 from a pool timeout, not from anything model-related.

It's worse than the raw numbers because the connection is *idle* for most of those 60 seconds — the session does a few queries up front (history, memory, preset), then sits unused while tokens stream, then does the final writes. It's holding a scarce resource to do nothing. Three fixes, escalating: (1) size the pool deliberately — `pool_size=20, max_overflow=10, pool_timeout=…` tuned to expected concurrency and Postgres's `max_connections`; necessary but it only moves the wall. (2) The real fix is to **not hold the session across the stream** — load everything needed up front, close the session, stream tokens holding no DB connection, then open a *fresh* short-lived session at the end for `save_messages`. Connection-held time drops from 60s to milliseconds, and the pool serves orders of magnitude more concurrent streams. (3) At real scale, put pgbouncer in transaction-pooling mode in front of Postgres so the app's pool and Postgres's connection limit are decoupled. I did none of these — the engine runs on defaults — which is fine for the single-user reality of this project and a latent outage the moment it's more than a few people. The streaming-session-lifetime issue is the one I'd fix first because it's a design flaw, not just a tuning number.

---

### HQ9: `detect_recall_request` is a regex. I type "can you remember to be more concise from now on" — does your system dump a verbatim transcript of the last N exchanges into the prompt because it pattern-matched "remember"? Show me why it does or doesn't.

**A:** It doesn't, and the reason is the regex requires more than just "remember." Look at the pattern: after the verb group `(?:recall|remember|repeat|...)` it requires `[^.?!]*?` then a **quantity-bearing clause** — `\b(?:last|previous|past|recent)\s+(\d+|one|two|...)\s+...(?:messages?|exchanges?|turns?|...)`. "Remember to be more concise" has the verb but no `last/previous/past/recent`, no number, and no `messages/exchanges/turns` noun, so the match fails and `detect_recall_request` returns `None`. No transcript injected. The capture group that returns the count is mandatory — there's no path that fires on the verb alone.

So the false-positive you're fishing for is guarded against by construction: the regex is deliberately narrow, requiring verb + recency-word + count + plural-noun all in sequence. The trade-off is the opposite failure — **false negatives**. "What did we discuss earlier?" doesn't match (no count), "go back two messages" matches but "scroll up a bit" doesn't, and anything phrased outside the template is missed. That's the conscious choice: a narrow regex that occasionally misses a genuine recall request is far better than a loose one that dumps 20 exchanges of transcript into the prompt every time someone says "remember." A spurious injection wastes context and confuses the model; a miss just means the user rephrases or relies on normal RAG. And the real escape hatch is agent mode — there, `recall_recent_exchanges` is a *tool* the model invokes by understanding intent, with no regex at all, which is the proper solution. The chat-mode regex is a cheap deterministic heuristic for the common explicit case, scoped tight precisely so it can't misfire the way you're describing.

---

### HQ10: Your worker and your API both call `get_redis()`, but they're separate processes with separate module state. The research endpoint publishes a cancel flag; the worker reads it. Convince me there's no process-boundary bug in how those two share Redis state.

**A:** There's no bug because they share *Redis*, not module state — and that's the whole point of using Redis as the boundary. The two processes have independent `_redis_client` singletons (separate memory, separate pools), but both connect to the *same Redis server* via `REDIS_URL`. The cancel flag is a key in that server: the API does `redis.set("research:cancel:{id}", "1", ex=3600)`, the worker does `redis.exists("research:cancel:{id}")`. The key lives in Redis, visible to any process that connects. Module-level singletons being per-process is correct and intended — each process pools its own connections; they're not trying to share Python objects across the boundary, which would be the actual bug.

Where the process boundary *does* introduce subtlety, and what I'd want you to actually probe: (1) **Visibility timing** — the flag is only checked *between* steps (`_check_cancelled` at stage boundaries), so cancellation is cooperative, not preemptive; a cancel set during a 40-second synthesis call isn't seen until that call returns. That's a latency property, not a correctness bug — the job still ends `cancelled`, just not instantly. (2) **The queued-job race** — cancelling a job the worker is simultaneously dequeuing: the cancel endpoint flips the row to `cancelled` *and* sets the flag, while the worker's first action is `if job.status != "queued": skip`. Whichever commits first wins deterministically — if the API commits `cancelled` first, the worker skips; if the worker flips it to `running` first, the API's flag is caught at the next checkpoint. Both orderings end correctly because the status transition and the flag are two independent signals and the worker honors both. (3) The thing that would genuinely break across the boundary — and doesn't, because I avoided it — is putting job state in worker memory. State is in Postgres, the broker and signals are in Redis, and *nothing* correctness-critical lives in either process's heap. That's the discipline that makes the two-process design sound: the processes are stateless, the shared truth is in shared infrastructure.

---

### HQ11: `inject_params` does substring matching on `class_type` to find nodes — `_find_node(g, "KSampler")`. I upload a workflow with a custom node called `KSamplerWithFancyExtras` and also a real `KSampler`. Which one does your code inject into, and is that what I wanted?

**A:** It injects into whichever the dict iteration hits first, which is **insertion order of the graph JSON** — and no, that's very likely not what you wanted. `_find_node` loops `for node_id, node in graph.items()` and returns the first node whose `class_type` *contains* the substring. `"KSampler" in "KSamplerWithFancyExtras"` is `True`, and `"KSampler" in "KSampler"` is `True`, so both match; the winner is just whichever key appears first in the JSON object. ComfyUI's API export order isn't semantically meaningful, so the choice is effectively arbitrary from the user's perspective. If your fancy node came first, steps/cfg/seed get injected there and your real KSampler runs with its defaults — silently wrong output, no error.

This is a genuine sharp edge of the auto-detection heuristic, and the design *has* an escape hatch but doesn't force you onto it: the `param_map` on the workflow lets you specify exact `{param: [node_id, input_key]}` targets, and `targets.update(param_map or {})` makes explicit mappings override auto-detection. So the correct usage for an ambiguous graph is to provide a `param_map` — but nothing *requires* it, and a user uploading a graph with two KSampler-ish nodes gets no warning that auto-detection is ambiguous. The robustness improvement I'd make: when `_find_node` finds *more than one* match for a critical anchor, refuse to guess — either error at upload time ("ambiguous KSampler nodes; provide a param_map") or, since the sampler's prompt links are followed structurally (`s_inputs["positive"][0]`), prefer the node that's actually wired into the graph's output chain over a dangling one. Substring-contains was chosen to handle the real variation (`KSampler` vs `KSamplerAdvanced` vs `KSampler //Inspire`) with one rule, and it does — but "first substring match wins" trades correctness for simplicity exactly when the graph is non-trivial, which is when people upload custom workflows in the first place. Auto-detect should be a convenience for simple graphs and explicitly defer to `param_map` the moment it's ambiguous, loudly.

---

### HQ12: You cap the agent at 6 iterations and a 24k token budget. A user asks a question that genuinely needs 8 tool calls. Your loop hits the cap, forces a tool-less final round, and the model answers with incomplete information. How does the user know the answer is half-baked?

**A:** They don't, and that's a real UX gap. When `iteration` hits `AGENT_MAX_ITERATIONS`, `offer_tools` goes false, the model is called one last time *without* tools, and it produces a final answer from whatever it gathered in 6 rounds — which for an 8-call question is partial. That answer streams out as a normal `token` event followed by `done`. There's no signal in the SSE stream that the loop terminated by hitting the cap versus terminating because the model was genuinely finished. From the client's side, a complete answer and a truncated-by-budget answer look identical.

What I'd change: the loop knows *why* it ended — it can distinguish "model emitted no tool calls" (natural completion) from "we withheld tools because `iteration >= MAX` or `over_budget`" (forced termination). That distinction should surface as a field on the `done` event — `{"type": "done", "truncated": true, "reason": "max_iterations"}` — so the UI can show a "this answer may be incomplete; the assistant ran out of research steps" banner and offer a "continue" button that resumes with a fresh budget. Even better, the final-round system prompt could be augmented when forced: "You've reached your tool limit; answer with what you have and explicitly note what you couldn't verify" — so the model itself flags the gaps in its prose. Right now the caps exist purely as safety rails (prevent infinite loops and runaway cost), which they do correctly, but they fail *silently* — the user can't tell a confident complete answer from a confident truncated one. Caps that change the answer's completeness must be visible in the response contract, and mine aren't. That's the fix I'd prioritize, because a wrong-because-truncated answer that looks authoritative is worse than an error.

---

### HQ13: `fetch_page` pulls a URL the model chose, inside your Docker network, with `follow_redirects=True`. The model decides to fetch `http://169.254.169.254/latest/meta-data/` or `http://postgres:5432`. What stops it? Walk me through your SSRF protection.

**A:** Nothing stops it, and that's the most serious finding in this batch — it's a textbook SSRF. `fetch_page` validates only that the URL starts with `http://` or `https://`, then does `httpx.get(url, follow_redirects=True)` from inside the Docker network. The model — or a prompt injection riding in a web page that an earlier `fetch_page` pulled — can target the cloud metadata endpoint (`169.254.169.254`), internal service names that resolve on the Docker network (`postgres:5432`, `redis:6379`, `searxng:8080`), `localhost`, or RFC1918 addresses. `follow_redirects=True` makes it worse: a benign-looking public URL can 302 to `http://169.254.169.254/...` and httpx follows it, so even an allowlist on the *initial* host is bypassable via redirect unless every hop is re-checked. The fact that this is reachable by *prompt injection* (fetch a malicious page → it instructs the model to fetch an internal URL → exfiltrate via the next search query) makes it a real attack chain, not a theoretical one.

The protection that *should* exist, and which I'd implement before exposing this beyond a trusted single user: (1) **resolve the hostname and reject private/loopback/link-local/metadata IP ranges** *before* connecting — `ipaddress.ip_address(resolved).is_private/is_loopback/is_link_local` plus an explicit `169.254.169.254` block; (2) **re-validate on every redirect hop**, which means disabling httpx's auto-follow and handling redirects manually so each `Location` is re-checked against the denylist (auto-follow + SSRF protection are incompatible); (3) **bind the fetch to egress-only** — run the agent's outbound fetches through a proxy or a network namespace that has no route to internal services, so even a validation bug can't reach `postgres`; (4) optionally a domain allowlist for the truly locked-down deployment. I shipped `fetch_page` with scheme-validation only because the threat model in my head was "single trusted user on a tailnet," but the moment this tool exists, the *model* is an untrusted actor that can be steered by any page it reads — and I gave it an unguarded HTTP client with a route to my database. That's not a tuning gap, it's a missing security control, and I'd treat it as a release blocker for any multi-user or internet-exposed deployment. The honest grade on this one is that I built a capability before I built its guardrails.

---

### HQ14: Langfuse is called synchronously inside `record_metrics`, which is inside your request path, right before you yield `[DONE]`. Langfuse Cloud has a slow day. What happens to the user's chat response?

**A:** It depends on whether the Langfuse client flushes synchronously, and the honest answer is I need to be precise about what blocks. `record_metrics` runs after the token loop, before the final `yield "data: [DONE]"`. The Prometheus updates are in-memory and instant. The Langfuse v4 call — `start_as_current_observation(...)` with `.update(...)` — *enqueues* a trace; the SDK is designed to flush asynchronously on a background thread, so in the normal case it returns quickly and `[DONE]` follows immediately. The user has already received every content token by this point (streaming finished in the loop above), so even if `record_metrics` blocked, the *visible answer* is already on their screen — what they'd lose is the prompt `[DONE]` terminator, which the frontend uses to flip out of the loading state.

But "designed to be async" isn't the same as "guaranteed never to block," and that's the gap. If the SDK's background queue is full, or a flush is triggered inline, or initialization is lazy and happens on first call against a slow endpoint, `record_metrics` can stall — and because it's `await`-free synchronous code inside the async generator, a stall there blocks the event loop coroutine until it returns, delaying `[DONE]` and, worse, holding the DB session (HQ8) and the generator open. The structural problem is that **observability is in the critical path of the response.** Metrics and tracing should never be able to delay or fail a user's request. The fix: wrap the Langfuse call so it can't throw into the request path (a failed trace must not error a successful chat), and push it off the response path entirely — `asyncio.create_task` for the trace, or a fire-and-forget queue, so `[DONE]` is yielded the instant tokens finish and tracing happens out-of-band. Right now a tracing backend's bad day can degrade chat latency, which is exactly backwards — the telemetry should suffer for the user, never the reverse. I'd also confirm, not assume, the SDK's flush behavior under backpressure, because "I think it's async" is not something I'd want to be wrong about in production.

---

### HQ15: Two migrations ago you had an empty migration that silently diverged your model from your schema and broke every conversation query on a fresh DB. You "fixed" it with another migration. What in your process *guarantees* this doesn't happen again? And "I'll be more careful" is not an answer.

**A:** Nothing in the current process guarantees it, and "I'll be careful" is exactly the non-answer you're rejecting — careful is what failed the first time. The empty migration #9 passed every check I had because I have no checks: migrations are hand-generated, eyeballed, and committed; nothing executes them against a clean database before they land. The model and the schema are two representations of the same truth with no automated reconciliation, so they can drift silently and only blow up when a fresh DB exercises the missing column. The fix has to be mechanical, not behavioral.

Concretely, three guards, in order of impact: (1) **CI that runs `alembic upgrade head` against a throwaway Postgres and then boots the app and hits one endpoint per table.** This single check would have caught #9 — a fresh DB plus a `SELECT` on conversations is exactly the path that failed, and it'd fail the build instead of the demo. It's the highest-value guard and the cheapest. (2) **An autogenerate drift check in CI**: run `alembic revision --autogenerate` against the migrated schema and assert it produces an *empty* diff — if the models and the migration chain disagree, autogenerate wants to emit a change, and a non-empty diff fails CI. That catches the inverse of #9 (model changed, migration didn't) directly. (3) **A pre-merge review rule that an empty `upgrade()` body is never valid alongside a models change** — enforceable as a lint that flags `def upgrade(): pass`. The meta-point: the bug wasn't carelessness, it was the *absence of a feedback loop* — I was the only thing standing between a broken migration and main, and humans are exactly the wrong mechanism for "did these two schemas match." The process fix is to make the machine prove they match on every push. Until that CI exists, I'd treat every migration as suspect, which is itself an argument for building the CI before writing the next one. I'd rather show you the failing-build screenshot than promise vigilance.

---

### HQ16: Last one, and I want a number. Your whole design rests on "this is a single-user, self-hosted, tailnet tool." Every gap I've found, you've waved away with that. So tell me precisely: which of these flaws becomes a production incident *first* the day this gets ten users instead of one, and which is the one you'd actually be paged for at 3am?

**A:** First to break at ten users: **the connection pool (HQ8).** It's not a security subtlety or an edge case — it's arithmetic. Default pool is 15 connections, a streaming chat holds one for the full response duration, and ten people chatting concurrently with any conversation depth saturates it almost immediately; the 16th request queues and times out at 30 seconds with a 500 that has nothing to do with the model. It needs no malice and no unusual input — just ten people using the product as intended at the same time. That's the day-one scaling wall, and it's the cheapest to hit because concurrency is the *normal* state of a multi-user system. So that's the first incident: not dramatic, just an outage the moment "single-user" stops being true.

The one I'd be **paged for at 3am**, though, is different — it's the SSRF in `fetch_page` (HQ13), and the privacy leak (HQ2) is a close second. The pool exhaustion is a loud, obvious, fix-the-config-and-redeploy incident; embarrassing but bounded. The SSRF is the one that ends with a postmortem and possibly a disclosure: a prompt injection in a fetched page steers the model to read cloud metadata or hit an internal service, and now it's not a degraded-latency page, it's "did someone exfiltrate credentials through my agent." The privacy leak is the same category of bad — "private" conversations sitting in a third-party SaaS is the kind of thing that's silent until someone audits Langfuse and finds content that was promised to never leave the box. Those two are worse than the pool not because they break first — they don't, they need an adversary or an audit — but because the pool failure is *recoverable and visible* while the security failures are *quiet and reputational*. If you're asking what I'd fix in what order before letting a second user near this: SSRF and the privacy leak first, because they're security controls that are simply missing and a release blocker; then the pool, because it's the first thing that falls over under honest load; then everything else. The single-user framing was a real scoping decision, not pure hand-waving — but you've correctly pinned that it's load-bearing for the *entire* threat and scaling model, and the day it stops being true, three of these go from "documented debt" to "incident" fast.

---

> **Closing note (out of character):** every flaw in this section is real and present in the code as of this writing — the SSRF (HQ13), the `private`-bypasses-Langfuse leak (HQ2), the provider-label bug (HQ1), the unguarded pool (HQ8), and the rest. They're catalogued here as interview prep, but several (HQ2, HQ8, HQ13 especially) are worth fixing in the actual codebase before this is more than a personal tool. The honest answer to a hostile interviewer is not to defend a flaw — it's to show you understand exactly why it's a flaw, what it costs, and what the fix is. That's what separates "I wrote this" from "I understand this."

---

## 31. The Hostile Interview (Round 3)

> **Context:** The interviewer came back from a coffee that was somehow also a bad decision, re-read the parts of the codebase Round 2 didn't touch, and found more. Same rules: every question verified against the real source first, every answer honest about what's a bug versus what's a defensible trade-off. Numbering continues from Round 2 (HQ17+).

---

### HQ17: You stream the answer, the user reads it, and *then* — before you send `[DONE]` — `save_messages` makes you wait on `store_memory` twice, and each one is a 30-second-timeout HTTP call to Ollama. So if Ollama is having a slow day, the user stares at a finished answer with a spinner that won't die. Defend putting embedding generation on the response critical path.

**A:** I can't defend it — it's a latency bug, and you've described the symptom precisely. `save_messages` commits the messages, then does `await store_memory(user)` and `await store_memory(assistant)` sequentially, and each `store_memory` calls `get_embedding`, which is an `httpx.post` to Ollama with `timeout=30`. Both awaits sit between the last content token and the `yield "data: [DONE]"`. The user's *answer* is already fully on screen (streaming finished in the loop above), but the frontend keys its loading state off `[DONE]`, so a slow embedder means a finished answer under a stuck spinner — and in the worst case two stacked 30-second timeouts, 60 seconds of dead air after the answer completed. Worse, that whole time the request is holding its DB connection (compounding HQ8) and the session is open.

The structural mistake is that **memory persistence is a side effect that has no business blocking the user's response.** Embedding is for *future* recall; nothing in the current turn depends on it completing before `[DONE]`. The fix is to get it off the critical path: fire the two `store_memory` calls as a background task (`asyncio.create_task`, or better an arq job now that the queue exists) after committing the messages, and yield `[DONE]` immediately. The messages are already durably saved — only their *searchability* lags, which is exactly the kind of thing that's allowed to be eventually-consistent. The reason it's inline is that it was the simplest correct-looking code: save everything in one function, in order. But "simplest to write" put a 60-second-worst-case network dependency between the user and the end of their request, for work the user doesn't wait on. If I move one thing off the response path, it's this — and the queue to do it properly already exists, so there's no excuse.

---

### HQ18: Your agent loop has a 24k "token budget." But I read it — the budget only decides whether you *offer tools*. It never trims the `messages` array. Tool results are capped at 8k chars each and you append every one, every round. Walk me to the iteration where you hand the model a context it can't accept, and tell me what the user sees.

**A:** You've found the real hole in the budget logic. The `messages` array only grows: each round appends the assistant's tool-call message plus one tool-result message per call (each result up to `TOOL_RESULT_MAX_CHARS` = 8000 chars), and the *entire* array is re-sent on every iteration. The `AGENT_TOKEN_BUDGET` check (`prompt_tok + completion_tok > budget`) gates `offer_tools` — it stops *new* tool calls — but it does nothing to shrink what's already accumulated. So picture a model making two ~8k-char tool calls per round across several rounds: by iteration 4–5 the array carries 60–80k characters of tool output plus the original history. Hand that to a model with an 8k or 16k context window and the provider rejects the request outright.

What the user sees depends on where it blows up. The `create()` call is inside the loop's `try`-less region within `run_agent`'s outer `try/except`, so a context-length error from the provider surfaces as an `APIError`, which the loop catches and converts to `{"type":"error","message":"upstream model provider error"}` followed by `done`. So: a generic "provider error," no partial answer, after the user already watched several tool calls scroll by — maximally frustrating because it *looks* like progress was being made and then it just dies. The budget gave a false sense of safety: it bounds *cost* (no more tool rounds once you've spent enough) but not *context size*, and those are different failure modes. The fixes: (1) actually trim — once the array exceeds a token threshold, summarize or drop the oldest tool results (they've usually served their purpose), keeping the system prompt, the original question, and recent results; (2) catch context-length errors specifically and degrade to a final tool-less synthesis from whatever's salvageable rather than a blank error; (3) count *characters/tokens of the message array* as its own guard, separate from the cost budget. Right now I conflated "spent enough money" with "context is safe," and they're not the same number.

---

### HQ19: Your rate limiter is a `BaseHTTPMiddleware`. Your headline feature is token-by-token SSE streaming. `BaseHTTPMiddleware` is *infamous* for buffering streaming responses. So tell me — does a chat response actually stream through your middleware token by token, or have you been demoing something that buffers and you never noticed?

**A:** It streams, but you're right to be suspicious and right about the history — and I had to think carefully about *why* it's okay here. `BaseHTTPMiddleware` wraps the response by consuming `call_next` and historically broke streaming because it buffered the response body before passing it on. The reason my SSE survives it: my middleware doesn't touch the response at all on the success path — it does its Redis work *before* `call_next`, and on the allowed path it `return await call_next(request)` and returns that response object unmodified. Modern Starlette implements `BaseHTTPMiddleware` over an internal stream bridge that preserves streaming as long as the middleware doesn't materialize the body, and mine never reads `response.body` or wraps the iterator. So tokens do pass through one at a time — which I can confirm from the demo behavior (text appears incrementally, not in one lump) and from Caddy's `flush_interval -1` only mattering if bytes are actually arriving incrementally at the proxy.

Where your suspicion is *correct* and I'll concede the risk: `BaseHTTPMiddleware` is the wrong tool and a latent trap. The moment someone adds a middleware that *does* inspect or modify the response (say, to add a header based on body, or to log response size), it'll buffer the SSE stream and silently turn token-streaming into "spinner, then the whole message at once" — and nobody will get a stack trace, just a degraded feel. The documented-correct approach for a streaming app is **pure ASGI middleware** (operate on the `scope`/`receive`/`send` primitives), which has no buffering failure mode because it never assembles a response object. My old interview notes even flag `BaseHTTPMiddleware`'s async-generator bugs as a known FastAPI sharp edge — so I knew the risk and used it anyway because it's ergonomic and my specific middleware happens to stay on the safe side of it. "Happens to be safe" is one careless future middleware away from a regression, and for a streaming-first product the rate limiter should be ASGI-level. That's a refactor I'd do before adding any second middleware.

---

### HQ20: The column your *entire* exchange-pair invariant depends on — `messages.index` — is `nullable=True`. And `load_history` orders by `index DESC`. Postgres sorts NULLs first in DESC. So explain what happens to conversation ordering the day a single message lands with a NULL index, and why that column was ever nullable.

**A:** If a NULL-index message ever exists, it sorts to the *front* of `index DESC` (Postgres defaults to NULLS FIRST on descending), so `load_history` — which takes the top `MAX_HISTORY_MESSAGES` of that DESC ordering and then reverses — would treat the NULL row as the *most recent* message, pulling it into the window and reversing it to the *end* of the prompt as if it were the latest turn. The transcript handed to the model would be scrambled: a row with no position jammed into the newest slot. The `max(index)` logic in `save_messages` is safe from it (SQL `max` ignores NULLs), so index *allocation* wouldn't collide, but *ordering* would be wrong, and positional recall (`get_last_exchanges`, which orders by index) would mis-slice. The whole exchange invariant assumes a total order, and a NULL is the absence of one.

Why is it nullable? Honestly: migration history, not intent. `index` was added to `messages` in a later migration (`e0c9b829531e`) *after* rows already existed, and adding a `NOT NULL` column to a populated table without a default fails — so it went in nullable to make the migration succeed, with the intent to backfill and tighten later, and the "later" never happened. Every code path that *writes* a message sets `index` explicitly (`save_messages`, the branch copy), so in practice no NULL is ever produced and the bug is latent, not live — which is exactly why it survived. But "no current code path produces it" is a property that holds until someone adds an `INSERT` that forgets, or a manual data fix, or an import. The correct state is `nullable=False` with a backfill migration (assign indices to any legacy NULL rows by `created_at` order first, then alter the column) — and ideally a `UNIQUE(conversation_id, index)` alongside it (see HQ5) so the database enforces the invariant my application logic assumes. A core invariant should be guaranteed by the schema, not by the discipline of every future writer.

---

### HQ21: Your default routing path is local. For local, you said yourself you can't get real token counts, so you store the *streamed chunk count* as `tokens_used` and add it to `conversations.token_count`. So every usage number in your database, for the default path, is fiction. Now imagine I built a billing or quota feature on top of this. What did I just build?

**A:** You built a billing system on a number that isn't tokens. For the local path, the streamed-chunk count is *approximately* the completion-token count for a typical token-per-chunk model, but it's not guaranteed — a provider can emit multiple tokens per chunk or split a token across chunks — and, more damningly, it's **completion-only**: `save_messages` receives `completion_tok` and stores that as `tokens_used`, and `conversations.token_count` accumulates only that. The prompt side — which for a long-context chat is usually the *majority* of the tokens and the cost — is never counted on the local path at all (prompt_tok is set to 0 when there's no usage object). So a quota built on `conversations.token_count` would undercount real usage by potentially the larger half, and inconsistently, because the *cloud* path (which does get real `usage`) would count accurately. You'd have a quota that's roughly right for OpenRouter traffic and silently lenient for local traffic, with no flag telling you which rows are which.

The honest framing is that these numbers are fine for their *actual* purpose — rough Prometheus throughput trends and a "this conversation is getting large" signal — and unfit for anything that needs to be *correct*, like billing or hard quotas. If I needed real accounting I'd: stop conflating chunk count with token count (run a real tokenizer over the prompt and completion, e.g. tiktoken-style, so local and cloud are measured the same way); count prompt *and* completion separately and store both; and label the source of the count (`exact` from provider usage vs `estimated` from a local tokenizer) so downstream code knows the confidence. Right now the schema has a single `tokens_used` integer with no provenance, which is the tell that it was built for a dashboard, not a ledger. Building billing on it would mean billing on a number that's the wrong half of the cost, estimated by a proxy, with no way to audit which rows are trustworthy.

---

### HQ22: `/metrics` is exposed by the Prometheus instrumentator with no auth, and it's in your rate-limiter's `EXCLUDED_PATHS`, and your backend port is published on the host. So anyone who can reach the host — your whole tailnet — can scrape model names, conversation counts, and token volumes without logging in. Justify that.

**A:** I'll justify the *scrape-by-Prometheus* design and concede the *exposure*. `/metrics` has to be unauthenticated for the standard reason: Prometheus scrapes it on a timer with no credentials, and it's excluded from rate limiting because a scrape every 15 seconds would otherwise eat into a bucket — that part is conventional and correct. The problem is the blast radius. Caddy only proxies `/api/*` and `/view*`, so `/metrics` is *not* reachable through the public :80 path — but the backend container publishes `2727:8000` on the host, so anything that can reach the host on 2727 hits `/metrics` directly, bypassing Caddy entirely. On a tailnet that's every device on the tailnet. What leaks isn't catastrophic — it's operational metadata: model label names, request counts, latency histograms, total token volumes, active-conversation gauge. No message content, no credentials. But it's still information disclosure: an attacker (or a curious tailnet guest) learns what models I run, how much I use them, and my traffic patterns, which is reconnaissance.

The fix is straightforward and layered: (1) **don't publish the backend port on the host at all** — let Caddy be the only ingress, so the only reachable surface is `/api/*` and `/view*`; the `2727:8000` mapping exists for dev convenience (hitting the API and `/docs` directly) and shouldn't be in a hardened compose. (2) Bind metrics to an internal-only interface or a separate port that's on the Docker network but not host-published, so only Prometheus (also on that network) reaches it. (3) If it must be host-reachable, put it behind a scrape credential or an IP allowlist for the Prometheus container. The root cause is the same single-user assumption as everything else: "it's my tailnet, everyone on it is me." The moment that's not true — a shared tailnet, a compromised device — unauthenticated metrics on a host port is a free recon endpoint. Low severity, real, and fixed by simply not publishing the port.

---

### HQ23: `Vector(768)` is hardcoded in your model. `EMBED_MODEL` is a config string. These two facts are not connected by anything. What happens the day I change the embedding model in `.env` to something that isn't 768-dimensional, and how long until you find out?

**A:** The dimension is hardcoded into the `memories.embedding` column as `Vector(768)` (sized for `nomic-embed-text`), and `EMBED_MODEL` is a free string in settings with no relationship to that 768. Change the model to a 1024-dim embedder and the next `store_memory` tries to insert a 1024-element vector into a `vector(768)` column, and pgvector rejects it at write time with a dimension-mismatch error. Because `store_memory` is wrapped in the graceful-degradation pattern (embedding failures are caught and the memory is silently skipped), the *insert failure* would surface — but the *retrieval* side and the broader behavior is where it gets ugly: you'd simply stop storing memories, silently, while chat keeps working, so semantic recall quietly dies and nothing alerts. If somehow mixed-dimension rows did get in (they can't here, but if the column were ever widened), the `<=>` distance operator requires matching dimensions and would error on query. Either way the failure is silent-degradation, and time-to-notice is "when a user says recall stopped working," same as the DDG scraper problem (HQ7) — no health signal distinguishes "embedder returned nothing" from "embedder returned the wrong shape."

The deeper issue is that **the embedding model and the column dimension are a single invariant split across two files with nothing enforcing it.** Changing the model is a schema change in disguise — it requires a migration to resize the column *and* a re-embedding of every existing memory (old 768-dim vectors are meaningless in a new model's space; you can't compare across embedding spaces even at the same dimension). So the honest answer is that `EMBED_MODEL` shouldn't be casually swappable at all; it's load-bearing on the data, not just the runtime. The mitigations: derive the column dimension from a single constant that also pins the model (so they can't drift), validate the returned embedding's length against the expected dimension in `get_embedding` and log loudly on mismatch instead of failing silent, and document that changing the embedder is a migrate-and-reindex operation, not an env tweak. Right now it looks like a config knob and is actually a schema commitment, which is the trap.

---

### HQ24: This is almost funny. You wrote this entire `revision.md` — the document you told me is your "single source of understanding," your interview lifeline — and `docs/` is in your `.gitignore`. So the most important document in this project isn't in version control. Explain how that happened and what it says about the rest of your process.

**A:** It happened because `docs/` got blanket-ignored early — probably to keep generated or scratch notes out of the repo — and then this document was written *into* that ignored directory without anyone reconsidering the rule. So `revision.md`, `roadmap.md`, everything under `docs/`, is untracked: not in history, not pushed, not backed up by the remote, gone the instant this working copy is lost or someone runs a clean checkout elsewhere. For a file I've described as the proxy for understanding the whole system, that's the single highest-irony finding across all three rounds — the safety net isn't attached to anything.

What it says about process is the uncomfortable-but-fair part: it's the same root cause as the empty migration (HQ from Round 2) and the model/schema dimension split (HQ23) — **no automated check enforces an invariant that I assumed held.** I assumed "important docs are in the repo" the way I assumed "the migration matches the model" and "the embedding dimension matches the column," and in each case nothing verified the assumption, so it silently wasn't true. A human-discipline process fails exactly these quiet, no-error-thrown ways. The fixes are trivial mechanically (`!docs/` or a narrower ignore so `docs/*.md` is tracked; better, a negation that ignores only generated artifacts) but the *systemic* fix is the recurring theme: a pre-commit or CI check that fails when expected-tracked files are ignored, the same way CI should run `alembic upgrade head` on a clean DB and assert an empty autogenerate diff. The pattern across all my findings isn't that I write careless code — most of the code is careful — it's that I lean on "I'll remember to" where I should lean on "the machine won't let me forget." An ungitignored doc is the most embarrassing instance because it's so cheap to get right, and it slipped for the same reason the others did: nothing was watching.

---

### HQ25: No idempotency anywhere. I double-click "research" because your UI didn't disable the button fast enough. Two identical jobs, each making six model calls and a dozen fetches, both enqueued. Same story for image generation. Walk me through the cost and why there's no dedup.

**A:** Double-submit enqueues duplicate work with no guard. `POST /research` unconditionally inserts a new `research_jobs` row and `enqueue_job`s it — two clicks, two rows, two job ids, two workers (or one worker twice, given `max_jobs=2`) each running the full plan→search→read→synthesize pipeline: ~6 model calls and ~10+ HTTP fetches *each*. On a metered provider that's double the cost and double the rate-limit pressure for output the user only wanted once; the two jobs even race to publish on different channels so the UI, if it subscribed to one, just shows one and the other burns resources invisibly. `POST /images/generate` has the same shape — two submissions, two ComfyUI jobs, two GPU renders, and two `imgjob:` ownership keys. Nothing dedups because every create endpoint is a straight `insert + enqueue` with no concept of "have I just seen this exact request."

The reason it's absent is the familiar one — single-user, and the frontend is *supposed* to disable the button — but "the client prevents it" is not a server guarantee, and a dropped connection that retries, a flaky tap on mobile, or any non-browser client defeats it. The standard fix is an **idempotency key**: the client sends a unique key per logical submission (or the server derives one from `user_id + hash(query + params)` within a short window), and the create endpoint does a `SET key NX EX 60` in Redis — if the key already exists, return the *existing* job id instead of enqueuing a new one. That collapses double-clicks into one job and is exactly the kind of thing Redis is already there for. For research specifically I'd also consider a "you have an identical job running" check against the `research_jobs` table (same user, same query, status in queued/running) and return that job rather than starting a twin. The cost of omitting it scales with how expensive the work is, and research is the most expensive thing in the system — which makes it the worst place to have no dedup and the first place I'd add it.

---

### HQ26: When OpenRouter isn't configured, your research feature's `_pick_client` silently falls back to the local model — which by default is your tiny *SDXL-rewrite* model. So a user asks for a deep research synthesis of six sources and it gets handed to a model that can barely write a paragraph. No error, no warning. Defend the silent downgrade.

**A:** I can't fully defend it — the silent part is the problem. `_pick_client` prefers OpenRouter only when `OPENROUTER_DEFAULT_MODEL` *and* `OPENROUTER_API_KEY` are both set; otherwise it falls to local with `LM_CHAT_MODEL or LM_DEFAULT_MODEL`. If the user never set `LM_CHAT_MODEL` (it defaults to empty), that resolves to `LM_DEFAULT_MODEL` — which the README and config comments describe as the *SDXL prompt-rewrite* model (a small Qwen tuned for terse tag output). Handing that model a synthesis prompt stuffed with six pages of source text and asking for a cited multi-paragraph analysis is a category error: it'll produce something, but it'll be short, likely ignore the citation instructions, and quietly be far below what "deep research" promises. And there's no signal — the job completes `complete`, the user gets a thin answer, and nothing indicates it ran on a model unsuited to the task.

The defensible part is the *intent*: research should still function without a cloud key rather than hard-fail, and falling back to local is the right instinct. The mistake is falling back *blindly* to whatever `LM_DEFAULT_MODEL` happens to be, when that variable's documented job is prompt rewriting, not chat. Better behavior: (1) research should require a *capable* chat model and validate that at submit time — if neither a configured OpenRouter model nor a real local chat model (`LM_CHAT_MODEL`) is available, reject the job with a clear "no model configured for research" error instead of silently using the rewrite model; (2) at minimum, record which model actually ran on the job row (the schema has `model`) and surface it in the result, so a thin answer is at least explainable; (3) separate the *concept* of "rewrite model" from "default chat model" in config so a fallback never lands on the former by accident. The root cause is that `LM_DEFAULT_MODEL` is overloaded — it's the rewrite model *and* the last-resort chat fallback — so an unconfigured deployment routes serious work to a model picked for a trivial task. Silent capability downgrades are worse than errors because the user can't tell a weak answer from a wrong setup; this one should fail loud at submission.

---

### HQ27: `conversation()` commits a new conversation row, returns its id, and *then* you assemble the prompt and call the model. I asked about the orphan row in Round 2. Different question now: that early commit is in the *same* `db` session the streaming generator uses for everything else. Streaming runs for 60 seconds. What's that session and its transaction doing the whole time, and is that a problem?

**A:** It's the connection-lifetime problem from HQ8 viewed from the transaction angle, and yes, it's a problem. The flow: `conversation()` does `db.add + db.commit` (ending that transaction), returns the id, then `load_history`/`load_preset`/`get_last_exchanges` run more queries on the same session, then the model streams for up to 60 seconds, then `save_messages` runs more queries and commits again. Between the early commit and `save_messages`, the session is idle-but-open: after a `commit`, SQLAlchemy's session is in a clean state and asyncpg isn't holding an explicit transaction open, but it *is* still holding its connection checked out from the pool for the entire life of the request — the whole 60-second stream — because the `get_db` dependency doesn't release until the generator finishes. So the answer to "what's the transaction doing" is "nothing, and that's the waste": no open transaction (good — it's not holding row locks or bloating, so no VACUUM starvation), but a pinned connection doing nothing for a minute.

So it's not a *correctness* hazard the way a long-open transaction would be (no held locks, no MVCC snapshot pinned, no `idle in transaction` problems) — it's a *throughput* hazard: one connection per concurrent stream, held mostly idle, which is precisely the pool-exhaustion math from HQ8. The thing that *would* make it a correctness problem — and which I deliberately don't do — is holding a single transaction open across the stream (e.g., if I'd created the conversation with `flush()` and not committed until `save_messages`); that would pin an MVCC snapshot and a row lock for 60 seconds and start causing `idle in transaction` timeouts and bloat. I avoided that by committing early, which trades the orphan row (HQ from Round 2) for a clean transaction state. So the early commit is actually the *lesser* evil on the transaction axis — the real fix is the same as HQ8: don't keep the session/connection checked out across the stream at all. Load what I need, return the connection, stream holding nothing, then grab a fresh short-lived session for the final write. That fixes the orphan, the idle connection, and the pool pressure in one move, which is why it's the single highest-value refactor in the backend.

---

### HQ28: We're done. Three rounds, twenty-eight questions. You've conceded an SSRF, a privacy leak, fictional usage metrics, a latency bomb on the response path, a context-overflow hole, and a document that isn't even in git. Every single time, your shield was "it's a single-user personal tool." So the final question is simple: at what point does "it's just a personal project" stop being an engineering scope decision and start being an excuse for not finishing the job?

**A:** The line is exactly where I stop being able to *name* the gap. "It's a personal tool" is a legitimate scope decision when it's a conscious, articulated trade-off — "I chose orphan rows over long-held transactions because at single-user scale the orphans are free and the transaction cost isn't, and here's the migration path when that flips." That's engineering: a decision with a stated cost and a known trigger for revisiting it. It becomes an excuse the moment it's used to *avoid knowing* — when "it's just personal" means "I never thought about what happens at ten users" rather than "I thought about it and consciously deferred it." The tell is whether I can tell you the failure mode, the cost, and the fix. For every flaw in these three rounds, I could — which means they're deferred work, not blind spots. That's the difference I'd want you to weigh.

But I won't hide behind that either, because you've correctly exposed its limit: **some of these aren't scope decisions, they're missing fundamentals, and scope doesn't excuse them.** The SSRF (HQ13) and the privacy leak (HQ2) are not "fine at single-user scale" — they're a security control and a broken core promise that are wrong at *any* scale, including one user, because the threat isn't concurrency, it's an adversarial input or a misplaced trust boundary, and those exist for one user too. Calling those "personal tool" trade-offs *would* be the excuse you're describing, and I'm not going to. The honest accounting is: most of what you found is real, deferred, and correctly scoped debt that I can defend as a sequence of conscious trade-offs — and a few things (SSRF, the private-data leak, arguably the fictional metrics if anyone trusts them) are not debt, they're defects I should fix before calling this done regardless of how many users it has. The maturity you're probing for isn't "did you build something flawless" — nobody does — it's "do you know exactly where the bodies are buried, can you rank them by severity, and are you honest about which ones 'it's just personal' actually covers." I'd rather walk you through twenty-eight real flaws I understand cold than show you a project I claim has none. The ones that scope genuinely covers, it covers. The two or three it doesn't, I just told you it doesn't. That's the line.

---

> **Closing note (out of character):** Round 3's findings are, like Round 2's, all verified against the current code — the inline embedding latency (HQ17), the agent context-growth hole (HQ18), the nullable `index` invariant gap (HQ20), local-path token-count fiction (HQ21), unauthenticated host-published `/metrics` (HQ22), the hardcoded `Vector(768)` vs. configurable `EMBED_MODEL` mismatch (HQ23), `docs/` being gitignored (HQ24), missing idempotency (HQ25), and the research silent-downgrade to the rewrite model (HQ26). None are strawmen. Highest-value fixes across both rounds, ranked: (1) get embedding + Langfuse off the chat response path and stop holding the DB connection across the stream — one refactor, fixes HQ8/HQ14/HQ17/HQ27; (2) the SSRF guard on `fetch_page` (HQ13); (3) honor `private` in `record_metrics` (HQ2); (4) `nullable=False` + `UNIQUE(conversation_id, index)` on messages (HQ5/HQ20); (5) un-gitignore the docs (HQ24). The recurring root cause, stated plainly: too many invariants enforced by discipline instead of by the machine. The fix for *that* is CI, constraints, and background tasks — not vigilance.


---

## 32. Issues & Fixes Log

> A running record of real bugs found and fixed in this repo — the kind of "what broke and why" that's worth being able to recite. Each entry is **symptom → root cause → fix**. Several were caught and fixed live in this session (and verified against a running stack); others were earlier hardening. Grouped by category.

### 32.1 Correctness & data-integrity (backend)

- **Schema drift — the empty migration that broke every conversation query.** *Symptom:* on a fresh database, every `/v1/convo*` request 500'd with `UndefinedColumnError: conversations.parent_id`. *Root cause:* the `Conversation` model gained `parent_id` / `branched_from_message_id` (for branching), but the migration generated alongside that change came out **empty** (`upgrade(): pass`) — model and schema silently diverged, and only a clean DB exercised the missing columns. *Fix:* added migration `9b3d6f1c2a47` to create the two columns + their `SET NULL` FKs; applied and verified live (`/v1/convo` → 200). *Lesson:* an empty `upgrade()` right after a model edit is a red flag; the real guard is CI running `alembic upgrade head` against a scratch DB.

- **Message-index race → corrupted exchange invariant.** *Symptom:* two near-simultaneous sends in one conversation could allocate colliding `index` values, breaking the user=k/assistant=k+1 invariant that positional recall depends on. *Root cause:* `save_messages` read `max(index)` and wrote the new rows non-atomically. *Fix:* `SELECT … FOR UPDATE` on the conversation row for the duration of the transaction, plus a single commit so messages and token count land together. (Belt-and-suspenders `UNIQUE(conversation_id, index)` still recommended — see §31 HQ5.)

- **Unordered reads.** *Symptom:* chat history and the conversation sidebar could render out of order. *Root cause:* `GET /v1/convo/{id}` and `GET /v1/convo` had no `ORDER BY` — Postgres guarantees nothing without one. *Fix:* messages now `ORDER BY index ASC, created_at ASC`; conversation list `ORDER BY created_at DESC`.

- **Mid-stream failure lost the whole exchange.** *Symptom:* if the provider died after streaming 50 tokens, the user saw a half-answer, never got `[DONE]`, and the turn vanished from history. *Root cause:* the token loop wasn't wrapped — an exception escaped the generator before `save_messages` ran. *Fix:* the loop is wrapped; on mid-stream failure the partial response is persisted, the client gets `[ERROR] stream interrupted` then `[DONE]`, and history stays consistent.

- **Registration race / half-initialized accounts.** *Symptom:* concurrent duplicate registrations could 500, and a failure between the user insert and the default-preset/template seeding left a half-set-up account. *Root cause:* check-then-insert across two commits, no constraint catch. *Fix:* user + default preset + default SDXL template in one transaction with an `IntegrityError` catch (the DB unique constraint is the real guard); added email-format and min-password-length validation on register only (a separate `UserLogin` model so existing accounts aren't locked out).

- **`seed = 0` got randomized.** *Symptom:* seed `0` — a perfectly valid seed — could never be reproduced; each run re-randomized it. *Root cause:* `seed or random()` treats `0` as falsy. *Fix:* `seed if seed is not None else …`.

### 32.2 Robustness & graceful degradation

- **Rate limiter failed *closed* on a Redis blip.** *Symptom:* a Redis outage turned every request in the app into a 500. *Root cause:* the limiter's Redis pipeline was unguarded. *Fix:* wrapped in try/except — a Redis failure now logs a warning and **fails open** (request allowed), the right call for a self-hosted gateway.

- **Memory retrieval could kill the chat turn — and poison the session.** *Symptom:* a hiccup in the pgvector query failed the whole chat request, against the stated "memory is auxiliary" design. *Root cause:* `retrieve_memories` had no error handling, and a failed statement poisons the transaction so *every later query on the same session* also fails. *Fix:* try/except that logs, calls `db.rollback()` (restoring the session so the turn proceeds memory-less), and returns `[]`; also made the vector cast explicit (`CAST(:emb AS vector)`) and moved the Ollama URL/model into settings.

- **ComfyUI failures looked like eternal "pending".** *Symptom:* a failed image job (OOM, missing checkpoint) never appeared in outputs, so the client polled forever. *Root cause:* `get_job_status` only knew pending/complete. *Fix:* it now reads ComfyUI's `status.status_str == "error"` and returns `{"status": "failed", "error": …}` with the extracted exception message; status polls also return **502** when ComfyUI is unreachable instead of a raw 500.

- **Provider-down endpoints returned raw 500s.** *Symptom:* `GET /v1/models` crashed when LM Studio wasn't running. *Fix:* wrapped → **502 "LM Studio unavailable"**, matching the OpenRouter twin's behavior.

- **Secrets hard-required at import.** *Symptom:* the backend couldn't even import outside Docker (host-side Alembic, scripts, tests) because `get_secret` raised unless `/run/secrets/*` existed. *Fix:* `get_secret` now falls back to an env var (`NAME.upper()`) before raising — secure in compose, still runnable on the host. Also removed the unused `LOCAL_URL`/`LOCAL_DEFAULT_MODEL` settings.

### 32.3 Frontend UX

- **Pause wiped your message.** *Symptom:* cancelling a stream reverted to the welcome screen (on the first message) or silently dropped your prompt (on later messages). *Root cause:* the frontend cleared the optimistic turn and refetched, but the backend never persists a *cancelled* turn — so the refetch returned a conversation without it. *Fix:* pausing now **freezes the turn in place** instead of clearing and refetching, so the partial answer and your prompt stay on screen.

- **Rename/Delete menu click-through.** *Symptom:* clicks passed straight through the popover to the chat rows underneath, and the backdrop only ever covered one chat. *Root cause (the sneaky one):* `animate-slide-up` leaves a permanent `transform` on each row, and a `transform` establishes a containing block that **traps `position: fixed`** inside that row — so the full-screen backdrop was scoped to a single row. *Fix:* switched the row to an **opacity-only** animation (no lingering transform), restoring the fixed backdrop's full coverage.

### 32.4 Code health / single source of truth

- **`DEFAULT_STRUCTURE` drift.** *Symptom:* new users' seeded "Default SDXL Template" didn't match the real default structure. *Root cause:* `auth.py` seeded it from a **hand-typed, mis-indented copy** that had already diverged from the canonical structure in `services/template.py`. *Fix:* `auth.py` now imports the canonical `DEFAULT_STRUCTURE` — one definition.

- **Duplicated magic values everywhere.** The negative-prompt string (×4), the preset/sampling defaults (scattered across 4 files), and a hardcoded `COMFY_URL`. *Fix:* collapsed each to a single source of truth — sampling defaults live as `DEFAULT_*` constants in `models/presets.py` (referenced by the column defaults, the API schema, the chat fallback, and registration seeding); `COMFY_URL` reads from config.

- **Service layer coupled to HTTP.** *Symptom/root cause:* `comfy.py` / `convo.py` raised `fastapi.HTTPException` directly, coupling business logic to the web framework (and making the services un-callable from the worker, a CLI, or tests). *Fix:* introduced domain errors (`AppError` → `NotFoundError` / `ForbiddenError`) raised by services, with **one boundary handler** in `main.py` translating them to HTTP — preserving FastAPI's `{"detail": …}` shape so clients are unaffected. This is exactly what later let the same service code run inside the arq research worker.

---

> **Note:** This log covers issues that were *fixed*. The open issues — the ones surfaced but not yet patched — live in §31's three rounds of hostile-interview findings and the "Summary of Gaps" table in §28. The highest-value unfixed items: getting embedding + Langfuse off the chat response path (HQ8/HQ14/HQ17/HQ27), the `fetch_page` SSRF guard (HQ13), honoring `private` in `record_metrics` (HQ2), and `nullable=False` + a unique constraint on `messages.index` (HQ5/HQ20).