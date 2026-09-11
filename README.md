# MedicalScribe

Windows medical speech-to-text and AI clinical documentation platform.
Clinicians dictate (Persian, English, or mixed); the WPF desktop client streams
audio over HTTPS/WebSocket to a FastAPI backend; a privacy-aware router selects
server-side STT and LLM providers. Generated notes are **drafts** — a clinician
must review, edit, finalize, and approve them. AI output never becomes a
medical record on its own.

```
WPF client ──HTTPS/WS──► FastAPI ──► AI router ──► STT: whisper-local / shenava (in-proc)
 (no providers, no secrets)              │              / qwen-asr / speechmatics / deepgram / mock
                                         ├──────────────► LLM: llama-server (external) / openai /
                                         │                anthropic / gemini / mock
                                         ├─ PostgreSQL (system of record) · Redis (queues, limits, shared state)
                                         ├─ audit log + metrics (never raw transcripts)
                                         └─ model hub: sha256-verified downloads, auto-configure
                                            (docs/MODELS.md — weights never touch the client)
```

Instructions assume **Windows 11 + PowerShell**; Linux/macOS notes are at the end.

## Requirements

| Tool | Purpose | Install |
|---|---|---|
| Python 3.11+ (3.12 recommended) | backend + AI layer | [python.org/downloads](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"** |
| .NET 10 SDK | WPF client only | [dotnet.microsoft.com/download](https://dotnet.microsoft.com/download/dotnet/10.0) |
| Git | clone the repo | [git-scm.com](https://git-scm.com/download/win) |
| Docker Desktop (optional) | full stack in one command | [docker.com](https://www.docker.com/products/docker-desktop/) |

Postgres, Redis, and a GPU are **not** required to start: the dev default runs
in-memory with the mock STT provider.

```powershell
python --version      # or: py --version
git --version
dotnet --list-sdks    # only if you will run the desktop client
```

## Setup

### 1. Get the code

```powershell
git clone https://github.com/AmiraliGhamkhar/sina.git
cd sina
```

### 2. Backend (5 minutes)

Run each block in PowerShell, in order.

**a. Virtual environment**

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
```

If PowerShell blocks scripts (*"running scripts is disabled"*), run once in the
same window, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

**b. Dependencies**

```powershell
python -m pip install --upgrade pip
pip install -e ".[dev,db,cache,security]"
```

**c. Settings**

```powershell
Copy-Item .env.example .env
notepad .env
```

Set these two values (leave the rest as-is):

```ini
MS_AUTH__JWT_SECRET=<any long random string>
MS_AUTH__DEV_TOKEN=dev-local-token
```

Generate a random secret from PowerShell:

```powershell
[Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }))
```

**d. Start the API**

```powershell
python -m uvicorn api.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

Expected: `MedicalScribe API v0.1.0 (phase 8) starting env=dev ws_protocol=v1`.
Stop with **Ctrl+C**. In any new PowerShell window, re-activate the venv first.

**e. Verify** (new window)

```powershell
Invoke-RestMethod http://localhost:8000/health
# status version phase
# ok     0.1.0   8

Invoke-RestMethod http://localhost:8000/api/v1/providers
```

Interactive API docs: <http://localhost:8000/docs>.

Dev login — username `dev`, password = your `MS_AUTH__DEV_TOKEN`:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"dev","password":"dev-local-token"}'
```

On Windows PowerShell `curl` aliases `Invoke-WebRequest`; use `curl.exe` for
real curl (e.g. `curl.exe http://localhost:8000/health`).

### 3. Desktop client (WPF)

**Easiest:** download `MedicalScribe-win-x64.zip` from the latest
[GitHub Release](../../releases) (built by CI on every `v*` tag) —
self-contained single-file exe, no .NET install needed. Unzip and run
`MedicalScribe.WPF.exe`.

**From source** (backend still running, second window):

```powershell
cd sina\client
dotnet build MedicalScribe.sln
dotnet run --project MedicalScribe.WPF
```

- First launch shows a wizard (server URL, microphone, hotkey).
- Server URL defaults to `http://localhost:8000`; change it in app settings or
  `%APPDATA%\MedicalScribe\settings.json`.
- Default dictation hotkey: **Ctrl+Alt+Space**. Allow the firewall prompt on
  *private networks* if it appears.
- Log in as `dev` with your `MS_AUTH__DEV_TOKEN`, then dictate — the mock STT
  provider produces a live transcript with no models installed.

### 4. Tests

```powershell
python -m pytest                                        # 321 passed, 2 skipped
python -m ruff check ai backend tests                   # lint
```

Tests force in-memory mode. If you un-commented `MS_DATABASE__URL` in `.env`
but Postgres is not running, tests fail with connection errors — comment the
line back out or start Postgres (`docker compose up -d postgres`).

### 5. Full stack with Docker

No Python install needed (API + Postgres + Redis):

```powershell
Copy-Item .env.example .env      # set MS_AUTH__JWT_SECRET as in step 2c
docker compose up --build
```

- API on <http://localhost:8000>; `docker compose logs -f api` to watch,
  `docker compose down` to stop.
- `--profile monitoring` — Prometheus + Grafana.
- `--profile llm` — local llama-server (needs a downloaded `.gguf` in `models/`).
- `--profile stt` — whisper.cpp server (needs a downloaded `.gguf` in `models/`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window, then activate again |
| `python : The term 'python' is not recognized` | Reinstall Python with "Add python.exe to PATH", or use `py`, or call `.\.venv\Scripts\python.exe` directly |
| Login returns `501 AUTH_NOT_IMPLEMENTED` | You used a real username/password with no database configured. Use `dev` + `MS_AUTH__DEV_TOKEN`, or enable Postgres (section "Durable mode") |
| Login returns `401`, dev login refuses | `MS_AUTH__JWT_SECRET` and/or `MS_AUTH__DEV_TOKEN` are empty in `.env` — set both, restart the API |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Something uses 8000: `netstat -ano \| findstr :8000`, then `taskkill /PID <pid> /F` — or start with `--port 8001` and point the client at it |
| Postgres/Redis connection-refused warnings at startup | Expected in dev — those features are off and the app degrades honestly. Ignore, or `docker compose up -d postgres redis` |
| `error: Microsoft Visual C++ 14.0 or greater is required` during `pip install` | Use 64-bit Python 3.12+ (all dependencies ship prebuilt wheels), or use Docker |
| `dotnet : The term 'dotnet' is not recognized` | Install the .NET 10 SDK, reopen PowerShell |
| WPF window never connects | Backend must be running and the client's Server URL must match (`http://localhost:8000`) |
| Firewall prompt on first run | Allow on private networks, or keep the API bound to `127.0.0.1` |

## Everyday commands

| Task | Command |
|---|---|
| Start API (dev, auto-reload) | `python -m uvicorn api.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload` |
| Start API (LAN/clinic box) | `python -m uvicorn api.main:app --app-dir backend --host 0.0.0.0 --port 8000` |
| Run tests / lint | `python -m pytest` · `python -m ruff check ai backend tests` |
| Build client | `cd client; dotnet build MedicalScribe.sln` |
| Run client | `dotnet run --project MedicalScribe.WPF` |
| Docker stack | `docker compose up --build` · `docker compose down` |
| Health / providers | `curl.exe http://localhost:8000/health` · `curl.exe "http://localhost:8000/api/v1/providers?probe=health"` |

## Linux / macOS

Same flow as above; only the Windows-specific steps change:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,db,cache,security]" && cp .env.example .env
.venv/bin/python -m uvicorn api.main:app --app-dir backend --port 8000
```

The WPF client is Windows-only; use the Docker stack or a Windows machine for
the desktop client.

## Durable mode (Postgres + Redis)

For real users, patients, encounters, and durable audit rows. Un-comment in
`.env` and restart:

```ini
MS_DATABASE__URL=postgresql+asyncpg://medicalscribe:medicalscribe@localhost:5432/medicalscribe
MS_DATABASE__AUTO_CREATE=true
MS_REDIS__URL=redis://localhost:6379/0
MS_AUTH__BOOTSTRAP_ADMIN_PASSWORD=<first-admin-password>
```

Then: `docker compose up -d postgres redis` → start the API once (it creates
the schema and the bootstrap admin) → log in as admin → create staff via
`POST /api/v1/admin/users`. Production instead applies Alembic migrations and
boots with `MS_DATABASE__AUTO_CREATE=false` (docs/DEPLOYMENT.md).

## Real AI providers (all optional, all server-side)

- **Model hub (recommended):** admin downloads models from the client's
  **AI Models** screen (Shenava Persian STT, Persian PII NER, Whisper
  large-v3-turbo, MiniCPM5/Jibay LLMs). The server downloads + sha256-verifies
  and auto-configures in-process models; for external services it prints the
  exact start command (or use `docker compose --profile stt up` /
  `--profile llm up`). Catalog and licenses: `docs/MODELS.md`.
- **Local STT:** set `MS_STT__WHISPER_SERVER__URL` +
  `MS_STT__DEFAULT_PROVIDER=whisper-local` (whisper.cpp server), or
  `MS_STT__DEFAULT_PROVIDER=shenava` for in-process Persian STT
  (needs `pip install ".[local-ai]"` + the model from the AI Models screen).
- **Local LLM:** run llama.cpp's server, set
  `MS_LLM__LLAMA_SERVER__BASE_URL` (e.g. `http://127.0.0.1:8080`), or
  `docker compose --profile llm up` with a `.gguf` in `models/`.
- **Cloud STT/LLM:** set keys (`MS_STT__DEEPGRAM__API_KEY`,
  `MS_LLM__CLOUD__OPENAI_API_KEY`, …) directly or encrypted via the admin API.
  Check what is live: `Invoke-RestMethod "http://localhost:8000/api/v1/providers?probe=health"`.

## Repository layout

```
client/MedicalScribe.WPF/    WPF shell: Views/ViewModels/Audio/Hotkeys/Settings/Infrastructure
backend/api/                 FastAPI: routes, schemas, models, repositories, services, auth, db
ai/                          AI layer: base.py ABCs · registry · router/ · stt/ · llm/
infrastructure/              Dockerfile.api · nginx (TLS+WS) · prometheus · load tests
docs/                        ARCHITECTURE · API · WEBSOCKET_PROTOCOL · DEPLOYMENT · MODELS ·
                             SECURITY · RELEASE · ROADMAP · ASSESSMENT · SOURCE_MAP · THIRD_PARTY_NOTICES
tests/                       pytest suite (backend + AI layer, protocol conformance)
models/                      model weights (git-ignored; downloaded via the model hub)
third_party/                 reserved for vendored code (empty by design)
docker-compose.yml           dev stack (api + postgres + redis + profiles)
.env.example                 server configuration template
```

Reference checkouts (vendored upstream, read-only inspiration — see
`docs/SOURCE_MAP.md` and `docs/THIRD_PARTY_NOTICES.md`):

```
speaktype/ phlox/ open-medical-scribe/ Multi-Model-Gateway/
```

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | References → assessment → WPF shell → FastAPI shell → REST connectivity (+ WS control plane, provider contracts, compose/nginx/env) | ✅ |
| 2 | NAudio capture → WS audio → mock STT stream → live transcript | ✅ |
| 3 | STT adapters: whisper-server, qwen-asr, Speechmatics, Deepgram (stream + batch) + batch REST endpoint | ✅ |
| 4 | LLM adapters: llama-server, OpenAI/Anthropic/Gemini, grounded draft endpoint | ✅ |
| 5 | Router hardening: health from task outcomes, EWMA latency ranking, fallback chains, daily token budget, Redis health mirror | ✅ |
| 6 | Voice commands, bilingual terminology, templates, report lifecycle (draft→finalized→approved), full clinical validation | ✅ |
| 7 | PostgreSQL + Alembic (14 tables), JWT + refresh rotation, argon2 login + lockout, rate limiting, encrypted provider secrets, audit + admin API | ✅ |
| 8 | Prometheus `/metrics`, Grafana dashboard, WS heartbeat, CI on real Postgres/Redis, load-test harness, first-run wizard, MSIX templates | ✅ |

Per-phase acceptance criteria and completion notes: `docs/ROADMAP.md`.

## Non-negotiables (enforced in code + tests)

- **Privacy wall:** `privacy_required` encounters route to local providers
  only — even in `mode=cloud` or with an explicit cloud preference (tested).
- **Grounding:** drafts are compared against the transcript for numbers,
  doses, units, laterality, negation, dates, identifiers (bilingual units,
  Jalali↔Gregorian). Mismatches surface as severity-tagged clinician-review
  warnings — never silent edits. `10 mg`→`100 mg`, `right`→`left`,
  `no effusion`→`effusion` are pinned critical in the test pack.
- **Two-step sign-off:** draft → finalized → approved only via explicit
  clinician actions; critical warnings block sign-off until acknowledged with
  a recorded justification; approved reports are immutable (corrections are
  linked amendments).
- **Secrets stay server-side:** the client only sees provider names,
  capabilities, health booleans, and the policy manifest.
- **Data hygiene:** audit deny-list, JSON logs without query strings, and 422
  responses that never echo submitted values.

## Docs and known gaps

- Docs: `docs/ARCHITECTURE.md` (design) · `docs/API.md` (REST contract) ·
  `docs/WEBSOCKET_PROTOCOL.md` (WS v1) · `docs/DEPLOYMENT.md` ·
  `docs/MODELS.md` · `docs/SECURITY.md` · `docs/RELEASE.md` ·
  `docs/ROADMAP.md` (phase history) · `docs/ASSESSMENT.md` (Phase 1 reference
  analysis) · `docs/SOURCE_MAP.md` · `docs/THIRD_PARTY_NOTICES.md` (attribution)
- Honest caveats before a pilot:
  - The WPF app is compile-verified in CI (and the self-contained exe is built
    there) but still needs a human pass on a real Windows machine.
  - Cloud STT/LLM adapters are verified against recorded fixtures, not live
    vendor accounts.
  - The 20-session load number was measured against the mock STT provider.
  - Model downloads are verified against the pinned catalog, but model
    *quality* (WER/F1) is taken from publishers' cards, not re-benchmarked here.
  - MSIX packaging/signing is documented but not CI-automated.
