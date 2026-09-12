# MedicalScribe

Windows medical speech-to-text + AI clinical documentation platform. Physicians
dictate (Persian, English, or mixed fa/en); a WPF desktop app streams audio to a
FastAPI backend; a privacy-aware AI router picks server-side STT and LLM
providers. Generated notes are **drafts a clinician must review, edit, finalize
and approve** — AI output is never automatically a medical record.

```
WPF client ──HTTPS/WS──► FastAPI ──► AI Router ──► STT: whisper / shenava (in-proc) / qwen / speechmatics / deepgram / 9router / mock
 (no provider access,          │                └► LLM: llama-server / openai / anthropic / gemini / 9router / mock
  no secrets here)             ├─ PostgreSQL (system of record) · Redis (queues, limits, shared state)
                               └─ audit log + metrics (never raw transcripts by default)
                                            ┌─ model hub: sha256-verified downloads, auto-configure
                                            └─ (docs/MODELS.md — weights never touch the client)
```

Everything below is written for **Windows 11 + PowerShell**. Linux/macOS notes
are at the end.

---

## 1. What you need

| Tool | Why | Install |
|---|---|---|
| **Python 3.11+** (3.12 recommended) | backend + AI layer | [python.org/downloads](https://www.python.org/downloads/) — tick **"Add python.exe to PATH"** during setup |
| **.NET 10 SDK** | only for the WPF desktop client | [dotnet.microsoft.com/download](https://dotnet.microsoft.com/download/dotnet/10.0) |
| **Git** | clone the repo | [git-scm.com](https://git-scm.com/download/win) |
| **Docker Desktop** *(optional)* | run the whole stack with batteries included | [docker.com](https://www.docker.com/products/docker-desktop/) |

You do **not** need Postgres, Redis, or a GPU to start: the dev default runs
fully in-memory with a mock speech engine.

Check everything in a new PowerShell window:

```powershell
python --version      # or: py --version
git --version
dotnet --list-sdks    # only if you plan to run the desktop client
```

## 2. Get the code

```powershell
git clone https://github.com/AmiraliGhamkhar/sina.git
cd sina
```

## 3. Run the backend (5 minutes)

Copy each block into PowerShell, in order.

**a. Create a virtual environment**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> If PowerShell blocks the script with *"running scripts is disabled on this
> system"*, run this once in the same window and try again:
> ```powershell
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
> ```

**b. Install dependencies**

```powershell
python -m pip install --upgrade pip
pip install -e ".[dev,db,cache,security]"
```

**c. Create your settings file**

```powershell
Copy-Item .env.example .env
notepad .env
```

In `.env` set these two values (leave everything else as-is):

```ini
MS_AUTH__JWT_SECRET=<any long random string>
MS_AUTH__DEV_TOKEN=dev-local-token
```

Generate a random secret straight from PowerShell if you prefer:

```powershell
[Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }))
```

**d. Start the API**

```powershell
python -m uvicorn api.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

Leave this window running. You should see
`MedicalScribe API v0.1.0 (phase 8) starting env=dev`. Stop it later with
**Ctrl+C**. In any *new* PowerShell window, re-activate the venv first with
`.\.venv\Scripts\Activate.ps1`.

**e. Verify it (new PowerShell window)**

```powershell
Invoke-RestMethod http://localhost:8000/health
# status version phase
# ------ ------- -----
# ok     0.1.0   8

Invoke-RestMethod http://localhost:8000/api/v1/providers
```

Interactive API docs: **<http://localhost:8000/docs>**

That is a working backend. Log in with the dev account — username `dev`,
password = whatever you put in `MS_AUTH__DEV_TOKEN`:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"dev","password":"dev-local-token"}'
```

Prefer `curl`? On Windows PowerShell `curl` is an alias for `Invoke-WebRequest` —
use **`curl.exe`** for the real thing:

```powershell
curl.exe http://localhost:8000/health
```

## 4. Run the desktop client (WPF)

**Easiest:** download `MedicalScribe-win-x64.zip` from the latest
[GitHub Release](../../releases) (built by CI on every `v*` tag) — it's a
self-contained single-file exe, no .NET install needed. Unzip and run
`MedicalScribe.WPF.exe`.

**From source**, with the backend still running, open a second PowerShell
window:

```powershell
cd sina\client
dotnet build MedicalScribe.sln
dotnet run --project MedicalScribe.WPF
```

* First launch shows a short first-run wizard (server URL, microphone, hotkey).
* Server URL defaults to `http://localhost:8000`; change it in the app's
  settings screen, or edit `%APPDATA%\MedicalScribe\settings.json`.
* Default dictation hotkey: **Ctrl+Alt+Space**. Allow the Windows Firewall
  prompt on *private networks* if it appears.
* Log in as `dev` with your `MS_AUTH__DEV_TOKEN` value, then dictate — the mock
  STT provider produces a live transcript with no AI models installed.

## 5. Run the tests

```powershell
python -m pytest                                        # 321 tests
python -m ruff check ai backend tests                   # lint
```

> Tests force in-memory mode. If you un-commented `MS_DATABASE__URL` in `.env`
> but Postgres isn't running, tests fail with connection errors — comment the
> line back out or start Postgres (`docker compose up -d postgres`).

## 6. Or: run everything with Docker

Easiest path to a full stack (API + Postgres + Redis) — no Python install needed:

```powershell
Copy-Item .env.example .env      # edit MS_AUTH__JWT_SECRET like in step 3c
docker compose up --build        # add: --profile monitoring  for Prometheus + Grafana
                                 # add: --profile llm         for a local llama-server (needs models\model.gguf)
```

The API is then on <http://localhost:8000> — `docker compose logs -f api` to
watch it, `docker compose down` to stop.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window, then activate again |
| `python : The term 'python' is not recognized` | Reinstall Python with "Add python.exe to PATH" ticked, or use `py` instead of `python`, or call `.\.venv\Scripts\python.exe` directly |
| Login returns `501 AUTH_NOT_IMPLEMENTED` | You're logging in with a real username/password but no database is configured. Use `dev` + your `MS_AUTH__DEV_TOKEN`, or enable Postgres (section 8) |
| Login returns `401`, dev login refuses | `MS_AUTH__JWT_SECRET` and/or `MS_AUTH__DEV_TOKEN` are empty in `.env` — set both, restart the API |
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Something already uses 8000: `netstat -ano \| findstr :8000`, then `taskkill /PID <pid> /F` — or start with `--port 8001` (and point the client at it) |
| Warnings about Postgres/Redis connection refused at startup | Expected in dev — those features are off and the app degrades honestly. Ignore, or run `docker compose up -d postgres redis` |
| `error: Microsoft Visual C++ 14.0 or greater is required` during `pip install` | Use 64-bit Python 3.12+, where all dependencies ship prebuilt wheels; or just use Docker (section 6) |
| `dotnet : The term 'dotnet' is not recognized` | Install the .NET 10 SDK, then reopen PowerShell |
| WPF window never connects | Backend must be running and the client's Server URL must match (`http://localhost:8000`) |
| Firewall prompt on first run | Allow on private networks, or keep the API bound to `127.0.0.1` |

## 8. Next steps (optional)

**Durable mode (Postgres + Redis)** — real users, patients, encounters, audit
rows. Un-comment in `.env` and restart:

```ini
MS_DATABASE__URL=postgresql+asyncpg://medicalscribe:medicalscribe@localhost:5432/medicalscribe
MS_DATABASE__AUTO_CREATE=true
MS_REDIS__URL=redis://localhost:6379/0
MS_AUTH__BOOTSTRAP_ADMIN_PASSWORD=<first-admin-password>
```

Then `docker compose up -d postgres redis`, start the API once (it creates the
schema and the admin user), and log in with the admin account to create staff
via `POST /api/v1/admin/users`.

**Real AI providers** — all optional, all server-side:

* **Model hub (recommended)**: in the client's **AI Models** screen, an admin
  downloads local models (Persian Shenava STT, PII redaction NER, Whisper
  turbo, MiniCPM5 / Jibay LLMs). The server downloads + sha256-verifies and
  auto-configures in-process models; external services (whisper.cpp /
  llama-server) get their exact start command, or just run
  `docker compose --profile stt up` / `--profile llm up`. Details + licenses:
  `docs/MODELS.md`.
* Local speech: set `MS_STT__WHISPER_SERVER__URL` (whisper.cpp server) and
  `MS_STT__DEFAULT_PROVIDER=whisper-local`. Persian in-process STT:
  `MS_STT__DEFAULT_PROVIDER=shenava` (needs `pip install ".[local-ai]"` +
  the Shenava model downloaded from the AI Models screen).
* Local LLM: run llama.cpp's server, set `MS_LLM__LLAMA_SERVER__BASE_URL`
  (e.g. `http://127.0.0.1:8080`), or use `docker compose --profile llm up`
  with a `.gguf` file in `models/`.
* **9Router** — one local endpoint that fronts many upstream models
  ([decolua/9router](https://github.com/decolua/9router), MIT). Start it
  (default `http://127.0.0.1:20128`) and point either adapter at it:
  `MS_LLM__NINE_ROUTER__BASE_URL` for chat models,
  `MS_STT__NINE_ROUTER__BASE_URL` for Whisper-compatible transcription.
  Add `__API_KEY` **only** if you launched it with `REQUIRE_API_KEY=true`.
  A 9Router box normally sits on your own machine, so both adapters default
  to `LOCAL` and stay eligible under `privacy_required` — flip
  `__PRIVACY_CLASS=cloud` only for an instance relaying to cloud accounts.
  The model list it is currently serving is browsable from the client's
  **AI Settings → 9Router** card
  (`GET /api/v1/providers/9router/models?kind=llm|stt`).
* Cloud STT/LLM: put API keys in `MS_STT__DEEPGRAM__API_KEY`,
  `MS_LLM__CLOUD__OPENAI_API_KEY`, … — or store them encrypted through the
  admin API. Check what's live with
  `Invoke-RestMethod "http://localhost:8000/api/v1/providers?probe=health"`.

### Everyday commands

| Task | Command |
|---|---|
| Start API (dev, auto-reload) | `python -m uvicorn api.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload` |
| Start API (LAN/clinic box) | `python -m uvicorn api.main:app --app-dir backend --host 0.0.0.0 --port 8000` |
| Run tests / lint | `python -m pytest` · `python -m ruff check ai backend tests` |
| Build client | `cd client; dotnet build MedicalScribe.sln` |
| Run client | `dotnet run --project MedicalScribe.WPF` |
| Docker stack | `docker compose up --build` · `docker compose down` |
| Health / providers | `curl.exe http://localhost:8000/health` · `curl.exe "http://localhost:8000/api/v1/providers?probe=health"` |

### Linux / macOS

Identical flow; swap the two Windows-specific steps:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,db,cache,security]" && cp .env.example .env
.venv/bin/python -m uvicorn api.main:app --app-dir backend --port 8000
```

---

## Repository layout

```
client/MedicalScribe.WPF/    WPF shell: Views/ViewModels/Audio/Hotkeys/Settings/Infrastructure
backend/api/                 FastAPI: routes schemas models repositories services auth db
ai/                          provider layer: base.py ABCs · registry · router/ · stt/ · llm/
infrastructure/              Dockerfile.api · nginx (TLS+WS) · prometheus · load tests
docs/                        ARCHITECTURE · ASSESSMENT · API · WEBSOCKET_PROTOCOL · SOURCE_MAP · …
tests/                       pytest suite (backend + ai layer, protocol conformance)
models/ third_party/         model weights (never committed) / vendored third-party code
docker-compose.yml .env.example
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
| 9 | Provider expansion: **9Router** adapters (LLM + STT, LOCAL by default), Speechmatics realtime WS re-aligned to the current v2 protocol, `GET /providers/{name}/models` live catalog discovery, client STT-provider picker + 9Router model browser | ✅ |

## Non-negotiables encoded in this codebase

- **Privacy wall**: `privacy_required` encounters route to LOCAL providers only —
  even under `mode=cloud` or an explicit cloud preference (tested).
- **Grounding**: drafts are compared against the transcript for
  numbers/doses/units/laterality/negation/dates/identifiers (bilingual units,
  Jalali↔Gregorian); mismatches surface as severity-tagged clinician-review
  warnings, never silent edits. `10 mg`→`100 mg`, `right`→`left`,
  `no effusion`→`effusion` are pinned critical by the test pack.
- **Two-step sign-off**: draft → finalized → approved only via explicit clinician
  actions; critical warnings block sign-off until acknowledged with a recorded
  justification; approved reports are immutable (corrections are linked
  amendments).
- **Secrets stay server-side**: the client only ever sees provider names,
  capabilities, health booleans, and the policy manifest.
- **Data hygiene**: audit deny-list, JSON logs without query strings, and 422s
  that never echo submitted values.

## Docs & known gaps

- `docs/ASSESSMENT.md` · `ARCHITECTURE.md` · `API.md` · `WEBSOCKET_PROTOCOL.md` ·
  `DEPLOYMENT.md` · `MODELS.md` · `SECURITY.md` · `RELEASE.md` · `ROADMAP.md` ·
  `SOURCE_MAP.md` · `THIRD_PARTY_NOTICES.md`
- Honest caveats worth knowing before a pilot: the WPF app is compile-verified
  in CI (and the self-contained exe is built there) but still needs a human
  pass on a real Windows machine; cloud STT/LLM adapters are verified against
  recorded fixtures, not live vendor accounts; the 20-session load number was
  measured against the mock STT provider; model downloads are verified against
  the pinned catalog but model *quality* (WER / F1) is taken from the
  publishers' cards, not re-benchmarked here; MSIX packaging/signing is
  documented but not automated. `docs/ROADMAP.md` tracks the rest.
