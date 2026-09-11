# MedicalScribe

Windows medical speech-to-text + AI clinical documentation platform.
Physicians dictate (Persian, English, or mixed fa/en); WPF streams audio to a
FastAPI backend; a privacy-aware AI router runs server-side STT and LLM
providers; generated notes are **drafts that a clinician must review, edit,
finalize and approve** — AI output is never automatically a medical record.

```
WPF client ──HTTPS/WS──► FastAPI ──► AI Router ──► STT providers (whisper/qwen/speechmatics/deepgram/mock)
   (no provider access,          │                └► LLM providers  (llama-server/openai/anthropic/gemini/mock)
    no secrets here)             ├─ PostgreSQL (system of record) · Redis (queues/limits/shared state)
                                 └─ audit log + metrics (never raw transcripts by default)
```

## Repository layout

```
client/MedicalScribe.WPF/    WPF shell: Views/ViewModels/Audio/Hotkeys/Settings/Infrastructure
backend/api/                 FastAPI: routes schemas models repositories services auth db
ai/                          provider layer: base.py ABCs · registry · router/ · stt/ · llm/
infrastructure/              Dockerfile.api · nginx (TLS+WS) · prometheus
docs/                        ARCHITECTURE · ASSESSMENT · API · WEBSOCKET_PROTOCOL · SOURCE_MAP · …
tests/                       pytest suite (backend + ai layer, protocol conformance)
models/ third_party/         model weights (never committed) / vendored third-party code
docker-compose.yml .env.example
```

## Quick start (dev)

Backend (Linux/macOS/Windows, Python 3.11+; production images use 3.12+):

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                     # set MS_AUTH__DEV_TOKEN, secrets optional in Phase 1
.venv/bin/python -m pytest               # 135 tests: + LLM adapters, grounded prompts, draft API
.venv/bin/python -m uvicorn api.main:app --app-dir backend --port 8000
curl localhost:8000/health
curl "localhost:8000/api/v1/providers?probe=health"
```

Docker: `docker compose up` (api + Postgres + Redis; `--profile llm` adds an
example llama-server container; it stays an external service — the app never
embeds llama.cpp).

WPF client (Windows, .NET 10 SDK):

```powershell
cd client
dotnet build MedicalScribe.sln
dotnet run --project MedicalScribe.WPF     # server URL is a User-Setting (stored in %APPDATA%\MedicalScribe\settings.json)
```

Login on dev builds: user `dev` / the `MS_AUTH__DEV_TOKEN` value (real
credentials arrive in Phase 7; the server answers 501 + a stable
`AUTH_NOT_IMPLEMENTED` code and the client shows that honestly).

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | References → assessment → WPF shell → FastAPI shell → REST connectivity (+ WS **control plane**, provider contracts, compose/nginx/env) | ✅ done (this build) |
| 2 | NAudio capture → WS audio → mock STT stream → live transcript (+ session transcript GET/PATCH) | ✅ done (this build) |
| 3 | STT adapters: local whisper-server, qwen-asr, Speechmatics, Deepgram (stream+batch) + batch REST endpoint | ✅ done (this build) |
| 4 | LLM adapters: llama-server + props, OpenAI/Anthropic/Gemini, grounded draft endpoint | ✅ done (this build) |
| 5 | Router: live health/latency/queue-depth, fallback chains (pure core + tracker shipped in P1) | partial |
| 6 | Voice commands, terminology, templates, report generation, validation warnings | planned (command catalog served by manifest) |
| 7 | PostgreSQL + SQLAlchemy + Alembic, JWT/refresh/argon2, Redis rate-limit, audit table, encrypted provider rows | planned (URL normalizer, JWT codec, JSONL audit shipped in P1) |
| 8 | Prometheus/OTel, load tests, MSIX packaging, production hardening | planned |

## Non-negotiables encoded in this codebase

- **Privacy wall**: `privacy_required` encounters route to LOCAL providers only —
  even under `mode=cloud` or an explicit cloud preference (tested).
- **Grounding**: prompt contract + validation service compare draft vs
  transcript for numbers/doses/units/laterality/negation/dates/identifiers;
  mismatches produce clinician-review warnings, never silent edits.
- **Two-step sign-off**: draft → finalized → approved only via explicit
  clinician actions (UI and API surface both enforce).
- **Secrets server-side**: registry/router/bridge are the only places config is
  projected; client sees names, capabilities, health booleans, and policy
  manifest.
- **Data hygiene**: audit deny-list + JSON logs without query strings +
  422s that never echo values.

## Test & lint

```bash
.venv/bin/python -m pytest        # backend + ai
.venv/bin/ruff check ai backend tests
```

## Docs

- `docs/ASSESSMENT.md` — reference analysis, conflicts, decisions (required reading before Phase 2)
- `docs/ARCHITECTURE.md` — boundaries, contracts, data flow, safety model
- `docs/API.md` · `docs/WEBSOCKET_PROTOCOL.md` — wire contracts
- `docs/DEPLOYMENT.md` — production notes
- `docs/SOURCE_MAP.md` · `docs/THIRD_PARTY_NOTICES.md` — references & attribution
- `docs/ROADMAP.md` — phase-by-phase remaining work with acceptance criteria

## Known gaps (honesty ledger)

1. WPF client + `MedicalScribe.WPF.Tests` cannot be compiled in this Linux
   sandbox (no .NET SDK; network policy blocks installs) — code targets
   net10.0-windows and must be built on Windows CI; XAML/ViewModel bindings
   and the NAudio capture loop are unverified there. First CI task:
   `dotnet build client/MedicalScribe.sln && dotnet test`.
2. The live transcript store is an in-memory LRU (last 200 ended sessions);
   server restart loses transcripts and encounter linkage/Persistence land in
   Phase 7. Logout still has no revocation store.
3. Heartbeat frames are idle-timeout only (4408); explicit app-level pings +
   nginx read-timeout tuning are deferred to Phase 8.
4. Cloud STT/LLM adapters (Deepgram/Speechmatics/OpenAI/Anthropic/Gemini)
   are verified against recorded fixtures + scripted transports, never
   against live vendor accounts (no credentials in this environment); first
   credentialed deployment should smoke `GET /api/v1/providers?probe=health`
   and one batch transcribe + one draft per vendor.
5. Report drafts are stateless (client-held): server-side draft storage,
   section PATCH and finalize/approve land in Phase 6/7. `ai_requests` usage
   rows are backfilled by the Phase 7 DB (counters carry it meanwhile).
5. Cloud LLM adapters (Phase 4) and the router's live latency/queue
   signals (Phase 5) are interface-ready but unimplemented.
6. Voice-command *parsing* (Phase 6) — the catalog + protocol frames exist,
   recognition is stubbed server-side.
7. Observability is process-local counters; Prometheus/OTel endpoints land in
   Phase 8.
