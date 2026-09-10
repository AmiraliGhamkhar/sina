# llm-gateway

A self-hosted inference orchestration layer — a personal mini-OpenRouter that routes chat requests across local and cloud AI providers, with auth, conversation persistence, semantic memory, image generation, and full observability.

> **Status:** All core features built. Backend complete; frontend client in development.

---

## Quickstart (5 minutes)

**Prereqs:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or Docker Engine (Linux), and git. Optional — for local models — [LM Studio](https://lmstudio.ai/) (chat / prompt rewriting / embeddings) on port 1234, ComfyUI on 8188, and Ollama on 11434.

1. **Clone and run the setup script** — it generates `.env` from `.env.example` (random secrets, your local model ids), checks Docker, and starts the stack:
   ```bash
   git clone https://github.com/ishaab/llm-gateway
   cd llm-gateway
   .\setup.ps1        # Windows PowerShell
   # or
   ./setup.sh         # Linux / macOS / WSL
   ```
   Already have a `.env`? The script detects it and leaves it unchanged.

   The setup scripts accept two flags: `-SkipStart` / `--skip-start` prepares `.env` without starting Docker (`.\setup.ps1 -SkipStart`, `./setup.sh --skip-start`), and `-NonInteractive` / `--non-interactive` skips the prompts for CI/automation (`.\setup.ps1 -NonInteractive`, `./setup.sh --non-interactive`).

   > Run setup before `docker compose up` so `monitoring/metrics_token` exists — otherwise Docker creates it as a directory and Prometheus can't read the token.
2. **Open the API docs:** http://localhost:2727/docs (Swagger UI).
3. **Register an account** (`POST /auth/register`).
4. **Add your providers** — see [Adding providers](#adding-providers). A "Local (LM Studio)" provider row is seeded automatically when `LM_URL` is set.
5. **Start chatting.**

> From a phone over Tailscale, reach the app through Caddy on port 80 (e.g. `http://<tailscale-ip>`); keep port 2727 internal.

### Locking down signups

Registration is open by default (`REGISTRATION_ENABLED=true`). Once you've created your
account, close the door: set `REGISTRATION_ENABLED=false` in `.env` and restart the backend
(`docker compose up -d backend`). `POST /auth/register` then returns `403 {"detail":
"registration disabled"}` for everyone — including attempts to re-register your own email.

### Deployment hardening

- **Use the prod Dockerfile target for anything beyond dev** — the compose `backend` service
  builds `target: dev` by default (root user + `--reload`); the `prod` target runs as a
  non-root user with no reload (`docker compose -f docker-compose.yml -f docker-compose.prod.yml up`
  or a similar override).
- **Set `REGISTRATION_ENABLED=false` for a personal deployment** once your account exists —
  see [Locking down signups](#locking-down-signups).
- **The operator OpenRouter key is shared into every account by design** — the "OpenRouter"
  provider row is seeded per user from the same key, so only enable registration if you
  intend a multi-user deployment where every account is trusted with that key.
- **Monitoring is loopback-only** — Prometheus (`127.0.0.1:9090`) and Grafana
  (`127.0.0.1:3000`) are bound to the host; Caddy on `:80` is the only tailnet ingress.

---

## Architecture

![Architecture](arch-dia-4.png)


---

## Features

- **Provider Routing** — Automatically routes requests to the best provider based on privacy needs, task type, model name, and context length. Sensitive data stays local; coding and long tasks go to OpenRouter.
- **Semantic Memory (RAG)** — Every message is embedded (Ollama `nomic-embed-text`, 768-dim) and stored in pgvector. On each turn, the top-3 semantically similar past messages are injected as context.
- **Parameter Presets** — Save and reuse model parameter profiles (temperature, top_k, top_p, min_p, repeat_penalty, etc.). Default preset created on registration.
- **SDXL Prompt Templates** — Rewrite natural language prompts into structured SDXL tags using a dedicated LLM (Qwen 2.5 on LM Studio). User-definable template structures.
- **ComfyUI Image Generation** — Generate images via ComfyUI workflows with optional prompt rewriting. Poll job status and retrieve results.
- **JWT Authentication** — Access token (60 min) + refresh token (7 days, persisted) flow. Password hashing with bcrypt.
- **Rate Limiting** — Sliding-window rate limit per user via Redis sorted sets (30 req/min default, configurable).
- **Observability** — Prometheus metrics (request count, latency, tokens/sec by provider/model) + Langfuse Cloud LLM tracing.
- **Agent Loop (tool calling)** — Agent mode (`/v1/agent/chat`) with first-party tools (`recall_recent_exchanges`, `web_search`, `fetch_page`, `current_datetime`, `search_conversations`, `generate_image`, `calculate`, plus the `memory_read`/`memory_write`/`memory_str_replace`/`memory_append`/`memory_delete` memory tools) plus MCP tools, gated by per-user permissions. Code-execution tools (`bash`, `edit_patch`, `edit_lines`, `write_file`) are additionally gated by the `ENABLE_CODE_EXECUTION` master switch on **both** the agent and the plain-chat tool paths. When code execution is enabled, `bash` runs in a sandbox **confined per-tenant**: each `(user_id, agent_id)` gets a distinct OS UID and a `chmod 700` workspace, so a model-driven shell can't read or write another user's workspace on the shared volume (see `sandbox/uid_alloc.py`).
- **Memory Files (file store)** — Per-user, versioned memory files (a Claude-style file store, deliberately **not** embeddings/RAG) that the agent reads and edits through the `memory_*` tools — durable profile, preferences, and notes that persist across conversations. Every chat and agent prompt is prefixed with a Tier-1 index of the user's files (`- {path} — {description}`) plus full Tier-1.5 files (default `/profile.md`, `/preferences.md`) injected as delimited, byte-capped system context (each file wrapped in `<memory_file path="...">` and capped at `MEMORY_TIER1_5_INJECT_CAP`), so the agent knows what it can read before it calls a tool. The mutating `memory_*` tools are **deny-by-default** (`first_party=False`, like `bash` and the file tools) — the model can only write memory after the user explicitly grants it via `PUT /v1/agent/tools/{name}/permission`, which breaks the prompt-injection chain (a fetched page can't silently plant persistent memory). A **background curation pipeline** (arq job) runs after every chat/agent turn: it reads the last `MEMORY_CURATION_MAX_MESSAGES` transcript messages plus the current files (with versions), asks the batch model once to propose memory-file operations (create/write/append/str_replace/delete) under a strict rule set (one-month horizon, user-stated facts only, privacy exclusions, one file per subject, consolidation near the cap), and applies them through the same versioned primitives — private chats never feed memory, and paths the agent wrote in-turn are skipped.

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Async API gateway |
| Database | PostgreSQL 16 + pgvector | Relational data + vector embeddings |
| Cache | Redis 7 | Rate limiting |
| Auth | JWT (python-jose) + bcrypt (passlib) | Authentication |
| ORM | SQLAlchemy 2.x (async) + Alembic | Data layer + migrations |
| Providers | LM Studio, Ollama, OpenRouter, ComfyUI | Inference backends |
| Image Gen | ComfyUI + Qwen 2.5 (prompt rewriting) | Text-to-image |
| Observability | Prometheus + Grafana + Langfuse | Metrics, dashboards, LLM tracing |
| Reverse Proxy | Caddy 2 | HTTPS, path-based routing |
| Containerization | Docker + Docker Compose | Service orchestration |

---

## API Endpoints

### Auth

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/auth/register` | Register a new user | No |
| POST | `/auth/login` | Login, get access + refresh tokens | No |
| POST | `/auth/refresh` | Exchange refresh token for new access token | No |
| POST | `/auth/logout` | Invalidate a refresh token | Yes |

### Chat

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/v1/chat/completions` | SSE-streamed chat completion with auto-routing | Yes |

### Models

| Method | Path | Description | Auth |
|---|---|---|---|
| GET | `/v1/models` | List models loaded in LM Studio | No |
| GET | `/v1/openrouter/models` | List free OpenRouter models | No |

### Conversations

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/v1/convo` | Create conversation | Yes |
| GET | `/v1/convo` | List user's conversations | Yes |
| GET | `/v1/convo/{id}` | Get messages in a conversation | Yes |
| PATCH | `/v1/convo/{id}` | Rename conversation | Yes |
| DELETE | `/v1/convo/{id}` | Delete conversation | Yes |

### Presets

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/v1/presets` | Create a parameter preset | Yes |
| GET | `/v1/presets` | List user's presets | Yes |
| GET | `/v1/presets/{id}` | Get preset details | Yes |
| PATCH | `/v1/presets/{id}` | Update preset | Yes |
| DELETE | `/v1/presets/{id}` | Delete preset | Yes |

### Prompt Templates

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/v1/templates` | Create a prompt template | Yes |
| GET | `/v1/templates` | List user's templates | Yes |
| GET | `/v1/templates/{id}` | Get template details | Yes |
| PATCH | `/v1/templates/{id}` | Update template | Yes |
| DELETE | `/v1/templates/{id}` | Delete template | Yes |
| POST | `/v1/templates/rewrite` | Rewrite a prompt for SDXL | Yes |

### Images

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/v1/images/generate` | Generate image via ComfyUI | Yes |
| GET | `/v1/images/status/{prompt_id}` | Poll generation status | Yes |

### Cookbook / Hardware

| Method | Path | Description | Auth |
|---|---|---|---|
| GET | `/v1/hardware` | GPU/VRAM probe (pynvml or nvidia-smi); also returns `ram_total_mb` (total system RAM) | Yes |
| GET | `/v1/cookbook` | Fit-score the local LM Studio catalog against total VRAM + RAM offload | Yes |
| GET | `/v1/hf/models` | Search Hugging Face models (`search`, `limit` 1–50) fit-scored against total VRAM + RAM offload | Yes |
| GET | `/v1/hf/models/{repo_id}` | HF model detail + per-quant GGUF-accurate fit (`context_tokens` 512–262144) | Yes |

### Health

| Method | Path | Description | Auth |
|---|---|---|---|
| GET | `/health` | Health check | No |
| GET | `/metrics` | Prometheus metrics | Bearer token (`METRICS_TOKEN`) |

---

## Project Structure

```
llm-gateway/
  .env                          # Environment variables (gitignored)
  requirements.txt              # pip shim → backend/requirements.txt (venv installs)
  docker-compose.yml            # Orchestrates 6 services
  arch-dia.png                  # Architecture diagram
  backend/
    Dockerfile                  # Python 3.11-slim, uvicorn --reload
    requirements.txt            # Python dependencies
    alembic.ini                 # Database migration config
    alembic/                    # Migration versions (6 migrations)
    app/
      main.py                   # FastAPI entry point, middleware, router registration
      db.py                     # Async SQLAlchemy engine + session
      core/
        config.py               # Pydantic settings from .env
        metrics.py              # Prometheus + Langfuse observability
        redis.py                # Async Redis connection pool
        security.py             # JWT creation/verification, password hashing
      middleware/
        ratelimit.py            # Sliding-window rate limiter (Redis)
      models/
        users.py
        conversations.py
        messages.py
        refresh_tokens.py
        memories.py
        presets.py
        templates.py
      routers/
        auth.py                 # Registration, login, token refresh, logout
        chat.py                 # SSE streaming chat completion
        convo.py                # Conversation CRUD
        images.py               # ComfyUI image generation
        models.py               # Model listing (LM Studio + OpenRouter)
        presets.py              # Preset CRUD
        templates.py            # Prompt template CRUD + rewrite
      services/
        router.py               # Provider routing engine
        convo.py                # Conversation management + semantic memory
        memory.py               # pgvector embeddings + retrieval
        template.py             # SDXL prompt rewriting
        comfy.py                # ComfyUI image generation
  caddy/
    Caddyfile                   # Reverse proxy config
  monitoring/
    prometheus.yml              # Prometheus scrape config
  workflows/
    t2i-default.json            # Default ComfyUI workflow
```

---

## Running Locally

**Prerequisites:**
- Docker Desktop
- LM Studio running on port 1234 with at least one chat model loaded (chat + prompt rewriting), plus an embedding model matching `LM_EMBED_MODEL` (default `text-embedding-nomic-embed-text-v1.5`)
- ComfyUI running on port 8188 (optional, for image generation)
- Ollama on port 11434 (optional)

**Setup:**

1. Clone the repo:
   ```bash
   git clone https://github.com/ishaab/llm-gateway
   cd llm-gateway
   ```

2. Create a `.env` file (see [Environment Variables](#environment-variables)).

3. Start everything:
   ```bash
   docker compose up --build
   ```

4. Verify:
   - Health check: `http://localhost:2727/health`
   - API docs (Swagger UI): `http://localhost:2727/docs`
   - Prometheus: `http://localhost:9090` (loopback-only — not reachable from the tailnet)
   - Grafana: `http://localhost:3000` (loopback-only — not reachable from the tailnet; admin user `admin`, password from `GRAFANA_ADMIN_PASSWORD` in your generated `.env` — the setup script generates one if you don't set it)

---

## Running the Backend Without Docker (venv)

To work on the FastAPI backend directly from a virtualenv (debugging, IDE tooling, no
Docker for the app itself):

**Prerequisites:**
- Python 3.11+ (the lockfile is compiled on 3.11; 3.13 is verified to install too)
- A Postgres 16 server **with the `pgvector` extension** and a Redis server reachable
  from the host. The compose `postgres`/`redis` services publish **no host ports**, so
  either run your own (system packages / installers) or expose them via a
  `docker-compose.override.yml` (gitignored-safe, Docker-only):
  ```yaml
  services:
    postgres:
      ports: ["127.0.0.1:5432:5432"]
    redis:
      ports: ["127.0.0.1:6379:6379"]
  ```

**Setup:**

1. Create a venv at the repo root and install the pinned dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt    # root shim → backend/requirements.txt
   ```
   `uvloop` (pulled in by `uvicorn[standard]`) is skipped automatically on Windows via
   an environment marker — the app runs uvicorn's standard event loop there.

2. Create **`backend/.env`** — settings and alembic both load it from the directory you
   run from (`backend/`). Required keys:
   ```env
   LM_URL=http://localhost:1234/v1
   LM_DEFAULT_MODEL=<LM Studio model id>
   COMFY_URL=http://localhost:8188
   DATABASE_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/<db>
   REDIS_URL=redis://localhost:6379/0
   SECRET_KEY=<python -c "import secrets; print(secrets.token_hex(32))">
   ALGORITHM=HS256
   ```
   Use `localhost`, **not** `host.docker.internal` — there's no container network on
   this path. Provider API keys that Docker mounts as secrets (e.g. `OPENROUTER_API_KEY`)
   fall back to plain env vars via `get_secret()` — add them to the same `.env`. The app
   boots without OpenRouter (it's optional).

3. Migrate and run:
   ```bash
   cd backend
   alembic upgrade head
   uvicorn app.main:app --reload --port 8000
   ```

The arq worker (deep research) needs the same env — from `backend/` in a second shell:
`arq app.worker.WorkerSettings`. Backend unit tests run offline the same way:
`python -m unittest discover -s tests -p "test_*.py"`.

---

## Dependencies

Backend dependencies are pinned with [pip-tools](https://github.com/jazzband/pip-tools):

1. Edit `backend/requirements.in` — the editable source of truth (direct deps only, comments welcome).
2. Regenerate the lockfile inside the backend container:
   ```bash
   docker compose exec -T backend pip-compile --output-file /app/requirements.txt /app/requirements.in
   ```
3. Commit **both** files. `backend/requirements.txt` is generated — never hand-edit it.

CI (`.github/workflows/schema-drift.yml`) runs on every push and pull request: it installs
the pinned dependencies, migrates a scratch PostgreSQL database to head and fails when the
SQLAlchemy models and Alembic migrations disagree (schema-drift guard), then runs the
backend unit tests.

## Running the tests

Backend tests are stdlib `unittest` (no pytest dependency):

```bash
cd backend && python -m unittest discover -s tests -p "test_*.py"
```

Tests that touch Postgres/asyncio/pgvector/redis/arq/prometheus/langfuse run in the Docker
container (those deps). Offline tests — the workspace store and the agent/tools/sandbox
coverage — stub the optional runtime deps only during import via
`tests/agent_test_stubs.py::import_with_stubs`, so they run on a bare dev host without
polluting sibling test modules; see `AGENTS.md` → "Test conventions (backend)".

---

## Docker Services

| Service | Image | Port | Purpose |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | internal | Persistent storage + pgvector extension |
| `redis` | `redis:7-alpine` | internal | Rate limiting backend |
| `backend` | Build from `./backend/Dockerfile` | `127.0.0.1:2727:8000` (loopback-only) | FastAPI application |
| `worker` | Build from `./backend/Dockerfile` | internal | arq deep-research job worker (same image as backend) |
| `searxng` | `searxng/searxng` | internal | Optional self-hosted search for `web_search` + research (start with `--profile search`) |
| `prometheus` | `prom/prometheus` | `127.0.0.1:9090:9090` (loopback-only) | Metrics collection |
| `grafana` | `grafana/grafana` | `127.0.0.1:3000:3000` (loopback-only) | Dashboards (admin user `admin`; password = `GRAFANA_ADMIN_PASSWORD` in `.env`) |
| `caddy` | `caddy:2` | `80:80` | Reverse proxy (strips `/api` prefix) |

---

## Caddy Reverse Proxy

The Caddyfile routes:
- `http://<host>/api/*` -> strips `/api`, proxies to `backend:8000`
- `http://<host>/` (WebSocket) -> proxies to `host.docker.internal:6969`
- `http://<host>/` (non-WebSocket) -> proxies to `host.docker.internal:6969`

Auto HTTPS is disabled. The frontend should call `/api/v1/*` and Caddy will forward it as `/v1/*` to the backend.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LM_URL` | Yes | LM Studio base URL (chat + prompt rewriting + embeddings) |
| `LM_DEFAULT_MODEL` | Yes | LM Studio model for SDXL rewriting |
| `COMFY_URL` | No | ComfyUI base URL for image generation (default: `http://host.docker.internal:8188`) |
| `OPENROUTER_API_KEY` | No | OpenRouter API key |
| `OPENROUTER_DEFAULT_MODEL` | No | Default OpenRouter model |
| `SANDBOX_SHARED_SECRET` | **Yes for code-execution** | Shared secret for the sandbox `POST /exec` endpoint — compose injects it into both backend and sandbox; the sandbox fail-closes (refuses every exec) when unset. Use any long random string. Required whenever `ENABLE_CODE_EXECUTION=true` (code-execution is also **per-tenant confined**: each workspace runs as its own OS UID and is `chmod 700`'d, so a shell can't cross tenants; and **network-isolated**: the sandbox sits on its own network with the backend as its only peer, with outbound internet but no route to postgres/redis/backend/host) |
| `SUGGEST_CLOUD_MODEL` | No | Pin a specific cloud model for `POST /v1/agents/suggest` (empty = derive from the resolved provider model). Try a `:free` model if your key is free-only |
| `SUGGEST_CLOUD_FALLBACK_MODELS` | No | Comma-separated free-model candidates Smart Suggest tries after the primary cloud model, before falling back to local (default `meta-llama/llama-3.1-8b-instruct:free, google/gemma-2-9b-it:free, qwen/qwen-2-7b-instruct:free`) |
| `POSTGRES_USER` | Yes | PostgreSQL user, required by docker-compose (default `ishaab`; the setup scripts add it to `.env` if missing). Must match the user embedded in `DATABASE_URL` |
| `POSTGRES_DB` | Yes | PostgreSQL database name, required by docker-compose (default `llmgateway`; the setup scripts add it to `.env` if missing). Must match the database embedded in `DATABASE_URL` |
| `DATABASE_URL` | Yes | PostgreSQL connection string (user/db must match `POSTGRES_USER` / `POSTGRES_DB`) |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password, required by docker-compose (and embedded in `DATABASE_URL`) |
| `REDIS_URL` | Yes | Redis connection string |
| `SECRET_KEY` | Yes | JWT signing secret |
| `ALGORITHM` | Yes | JWT algorithm (`HS256`) |
| `ACCESS_TOKEN_EXPIRY_MINUTES` | Yes | Access token TTL (60) |
| `REFRESH_TOKEN_EXPIRY_DAYS` | Yes | Refresh token TTL (7) |
| `GRAFANA_ADMIN_PASSWORD` | Yes | Grafana admin login password, required by docker-compose; the setup script generates one if you don't set it |
| `METRICS_TOKEN` | No | Bearer token for `GET /metrics`; empty disables the endpoint (fail-closed). The setup scripts generate one and mirror it to `monitoring/metrics_token` for Prometheus |
| `TRUSTED_PROXIES` | No | Comma-separated CIDRs of reverse proxies whose `X-Forwarded-For` header the rate limiter trusts (default `172.16.0.0/12` — the Docker bridge Caddy sits on). Set to empty to ignore `X-Forwarded-For` entirely |
| `REGISTRATION_ENABLED` | No | Set `false` to disable open signups — `POST /auth/register` returns 403 "registration disabled" (default `true`) |
| `ALLOW_PRIVATE_PROVIDER_URLS` | No | Provider `base_url`s may point at private/loopback hosts (e.g. local LM Studio); set `false` to enforce public-only URLs — breaks local providers, use only on locked-down deployments (default `true`) |
| `MCP_SERVERS` | No | JSON list of MCP servers (stdio/SSE) the agent can call. **Operator-configured only — it can spawn arbitrary commands; never let user input reach this setting** |
| `MEMORY_FILE_CAP_BYTES` | No | Per-file cap for the user memory file store; writes at/over the cap are rejected, never truncated (default `32768`) |
| `MEMORY_TIER1_5_PATHS` | No | Comma-separated memory file paths always injected into chat/agent context as delimited system blocks (default `/profile.md,/preferences.md`) |
| `MEMORY_TIER1_5_INJECT_CAP` | No | Per-file byte cap on tier-1.5 context injection (defense-in-depth, default `2000`) |
| `MEMORY_CURATION_MAX_MESSAGES` | No | Transcript window (messages) the background curation pass feeds the batch model (default `20`) |
| `MEMORY_CURATION_MODEL_ROLE` | No | Which provider role the curation batch model prefers: `auto` (cloud when OpenRouter is configured, else local), `local`, or `cloud` (default `auto`) |
| `LANGFUSE_PUBLIC_KEY` | No | Langfuse Cloud public key |
| `LANGFUSE_SECRET_KEY` | No | Langfuse Cloud secret key |
| `LANGFUSE_BASE_URL` | No | Langfuse endpoint |

> **MCP trust boundary:** `MCP_SERVERS` is operator configuration — a stdio entry runs
> whatever `command` it names, so treat the setting like a Dockerfile: trusted config
> only. Never derive it from, or let it be influenced by, user input (a chat message
> must never be able to add or alter an MCP server).

---

## Provider Routing Logic

The routing engine (`services/router.py`) decides where to send each request using this priority:

1. **`private: true`** in request -> Always route to local LM Studio (privacy override)
2. **`provider: "local"`** in request -> Route to local LM Studio
3. **Model name contains `/`** (e.g. `openrouter/owl-alpha`) -> Route to OpenRouter
4. **`provider: "openrouter"`** in request -> Route to OpenRouter
5. **Coding keywords in last message** (e.g. `script`, `code`, `function`, `debug`, `python`, `c++`, `javascript`) -> Route to OpenRouter
6. **More than 80 messages in conversation** -> Route to OpenRouter (longer context windows)
7. **Default** -> Route to local LM Studio

The chat endpoint receives `provider`, `model`, `private` fields in the request body to control this behavior.

---

## Adding providers

The gateway is bring-your-own-key: after registering, create provider rows via `POST /v1/providers` (or the /docs UI). Supported types:

| `type` | Use for |
|---|---|
| `openai_compatible` | Any OpenAI-wire endpoint — LM Studio, Ollama, Groq, vLLM, OpenCode Go, ... |
| `openai` | OpenAI cloud |
| `anthropic` | Anthropic |
| `google` | Google Gemini |
| `openrouter` | OpenRouter |

Keys are stored encrypted (Fernet) and are **write-only** — responses only ever show a masked suffix (`api_key_masked`). Each role (`local` / `cloud`) can have one default provider; a request can also pin a specific provider by passing its `provider_id` in the chat/agent body (overrides every routing heuristic).

Example — an OpenAI-compatible endpoint (LM Studio, Ollama, Groq, ...):

```json
{
  "name": "LM Studio",
  "type": "openai_compatible",
  "role": "local",
  "base_url": "http://host.docker.internal:1234",
  "api_key": "",
  "default_model": "qwen2.5-7b-instruct",
  "is_default": true
}
```

`base_url` gets `/v1` appended automatically when it's missing (so `http://host:1234` and `http://host:1234/v1` both work). A "Local (LM Studio)" row is seeded for you when `LM_URL` is set, and an "OpenRouter" row only when an OpenRouter key is configured. If you never create rows, the gateway falls back to the legacy env-var configuration (`LM_URL`, `LM_CHAT_MODEL`, `OPENROUTER_API_KEY`, ...).

---

## Authentication Flow

1. **Register** (`POST /auth/register`) -> Creates user, default preset, default SDXL template.
2. **Login** (`POST /auth/login`) -> Returns `access_token` (60 min) + `refresh_token` (7 days).
3. **Use API** -> Send `Authorization: Bearer <access_token>` header on every protected endpoint.
4. **Refresh** (`POST /auth/refresh`) -> Exchange `refresh_token` for a new `access_token`.
5. **Logout** (`POST /auth/logout`) -> Invalidates the specific `refresh_token` in the database.

---

## Image Generation Flow

1. User sends `POST /v1/images/generate` with a natural language prompt.
2. If `rewrite: true`, the prompt is rewritten using LM Studio Qwen 2.5 into comma-separated SDXL tags via a template structure.
3. The rewritten prompt is submitted to ComfyUI (`host.docker.internal:8188`) as a KSampler workflow.
4. The endpoint returns a `prompt_id` for polling.
5. Client polls `GET /v1/images/status/{prompt_id}` until `status: "complete"`, then renders image URLs from the response.

---

## Semantic Memory (RAG)

- Every user and assistant message is embedded via Ollama `nomic-embed-text:latest` into a 768-dim vector.
- Embeddings are stored in the `memories` table via pgvector.
- On each new message in a conversation, the top 3 semantically similar past messages (lowest cosine distance `<=>`) are injected as a `system` context message before the conversation history.

---

## Presets

Presets are reusable parameter profiles for LLM generation. Each user has their own presets. The default preset has:

| Field | Default |
|---|---|
| `temperature` | 0.8 |
| `context_overflow` | `truncate_middle` |

Additional supported fields: `system_prompt`, `token_limit`, `stop_strings` (array), `top_k`, `top_p`, `min_p`, `repeat_penalty`.

Local provider parameters (`top_k`, `min_p`, `repeat_penalty`) are sent via `extra_body` in the OpenAI-compatible API call.

---

## Observability

- **Prometheus** scrapes `backend:8000/metrics` every 15s.
- Custom metrics: `chat_requests_total`, `chat_latency_seconds`, `tokens_per_second`, `prompt_tokens_total`, `active_conversations_total`.
- **Grafana** runs on port 3000.
- **Langfuse** traces every chat generation with input, output, model, provider, latency, and token metadata. Full chat content is sent to Langfuse unless the message is sent with `private: true` (those chats record metadata only). If you don't use Langfuse, remove the `LANGFUSE_*` keys from `.env`.

---

## Contributing

This is a personal project. Issues and PRs are welcome.

## License

MIT
