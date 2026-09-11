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
python -m venv .venv && .venv/bin/pip install -e ".[dev,db,cache,security]"
cp .env.example .env                     # dev mode needs no database; durable mode: set MS_DATABASE__URL
.venv/bin/python -m pytest               # 265 tests: + auth, persistence, vault, rate limiting
.venv/bin/python -m uvicorn api.main:app --app-dir backend --port 8000
curl localhost:8000/health
curl "localhost:8000/api/v1/providers?probe=health"
```

Durable mode (Postgres) without Docker: set `MS_DATABASE__URL`, create the
schema once with `MS_DATABASE__AUTO_CREATE=true` (dev) or
`cd backend && MS_DATABASE__URL=... alembic upgrade head` (production), then
set `MS_AUTH__BOOTSTRAP_ADMIN_PASSWORD` for the first boot to create the
admin user.

Docker: `docker compose up` (api + Postgres + Redis; `--profile llm` adds an
example llama-server container; it stays an external service — the app never
embeds llama.cpp).

WPF client (Windows, .NET 10 SDK):

```powershell
cd client
dotnet build MedicalScribe.sln
dotnet run --project MedicalScribe.WPF     # server URL is a User-Setting (stored in %APPDATA%\MedicalScribe\settings.json)
```

Login: real credentials against the users table (Phase 7; requires
`MS_DATABASE__URL` — bootstrap the admin with
`MS_AUTH__BOOTSTRAP_ADMIN_PASSWORD`, then create users via
`POST /api/v1/admin/users`). Without a database, dev builds still accept
user `dev` / the `MS_AUTH__DEV_TOKEN` value; credential login answers
501 + a stable `AUTH_NOT_IMPLEMENTED` code and the client shows that
honestly.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | References → assessment → WPF shell → FastAPI shell → REST connectivity (+ WS **control plane**, provider contracts, compose/nginx/env) | ✅ done (this build) |
| 2 | NAudio capture → WS audio → mock STT stream → live transcript (+ session transcript GET/PATCH) | ✅ done (this build) |
| 3 | STT adapters: local whisper-server, qwen-asr, Speechmatics, Deepgram (stream+batch) + batch REST endpoint | ✅ done (this build) |
| 4 | LLM adapters: llama-server + props, OpenAI/Anthropic/Gemini, grounded draft endpoint | ✅ done (this build) |
| 5 | Router hardening: health fed by task outcomes, latency EWMA ranking, runtime fallback chains (WS + batch + draft), daily token budget soft-stop, optional Redis health mirror | ✅ done |
| 6 | Voice commands (parser+effects+undo), bilingual terminology, data-driven templates + LLM extraction, report lifecycle (draft→finalized→approved with acknowledged warnings), full clinical validation (doses/units/laterality/negation/dates/identifiers/anatomy) | ✅ done (this build) |
| 7 | PostgreSQL + SQLAlchemy + Alembic (14 tables, write-through + restart reload), JWT + one-time refresh rotation (reuse ⇒ revoke all), argon2 login w/ lockout, Redis-or-in-process rate limiting (degrade-open) + per-user WS cap, Fernet-encrypted provider secrets, audit dual-write + admin API, patients/encounters | ✅ done (this build) |
| 8 | Prometheus `/metrics` (dependency-free) + optional OTel, Grafana dashboard, WS heartbeat keepalive, durable cost-budget backfill, CI on real Postgres/Redis + pip-audit + gitleaks, WS load-test harness (20 sessions verified), first-run wizard, MSIX templates + release/security runbooks | ✅ done (mock-path load numbers; MSIX signing is a documented Windows step — see honesty ledger) |

## Non-negotiables encoded in this codebase

- **Privacy wall**: `privacy_required` encounters route to LOCAL providers only —
  even under `mode=cloud` or an explicit cloud preference (tested).
- **Grounding**: prompt contract + validation service compare draft vs
  transcript for numbers/doses/units/laterality/negation/dates/identifiers
  (bilingual units, Jalali↔Gregorian dates, near-miss dose detection);
  mismatches produce severity-tagged clinician-review warnings, never silent
  edits. `10 mg`→`100 mg`, `right`→`left`, `no effusion`→`effusion` are all
  pinned critical by the test pack.
- **Two-step sign-off**: draft → finalized → approved only via explicit
  clinician actions (UI and API surface both enforce); critical warnings
  block finalize/approve until acknowledged with a recorded justification;
  approved reports are immutable — corrections are linked amendments.
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
   sandbox (no .NET SDK; network policy blocks installs) — the CI pipeline is
   the compiler: `client-linux` runs the full WPF build against the targeting
   pack and `client-windows` runs it natively with xunit. Runtime behavior
   (window chrome, hotkeys, NAudio device handling, the Phase 6 report/
   templates/command screens) still needs a human pass on a Windows machine.
2. Persistence landed in Phase 7 (SQLite/Postgres write-through + durable
   reads after eviction/restart, verified against a real server restart),
   but integration tests run on sqlite+aiosqlite, not a live Postgres — the
   compose-profile CI job with real postgres/redis is still open (Phase 8).
   Cost-budget counters are durable since Phase 8. The WPF client rotates
   tokens proactively (refresh timer fires 60 s before access-token expiry,
   retries on network blips, drops to login only when the rotation is
   rejected) — compile + unit-verified in CI; runtime behavior still needs
   the manual Windows pass (see #1).
3. Heartbeat frames are idle-timeout only (4408); explicit app-level pings +
   nginx read-timeout tuning are deferred to Phase 8.
4. Cloud STT/LLM adapters (Deepgram/Speechmatics/OpenAI/Anthropic/Gemini)
   are verified against recorded fixtures + scripted transports, never
   against live vendor accounts (no credentials in this environment); first
   credentialed deployment should smoke `GET /api/v1/providers?probe=health`
   and one batch transcribe + one draft per vendor. Template extraction is
   likewise scripted-LLM-verified only.
5. The Phase 6 terminology catalog is a curated starter set (~50 entries);
   clinically-driven extension (and its DB-backed storage) continues in P7.
   Validation is heuristic NLP (cue scopes, proximity windows) — deliberately
   advisory-with-critical-flags, never an editor; false negatives are possible
   and the two-step sign-off is the safety net.
6. Cost-budget counters are durable since Phase 8 (boot-time backfill from
   `ai_requests`; fail-open only while the DB is unreachable at boot).
7. Prometheus `/metrics` is live (Phase 8); OTel tracing requires the
   optional `observability` extra and is off by default.
8. The 20-session load number was measured against the **mock STT provider
   in a sandbox** (harness: `infrastructure/load/ws_loadtest.py`); the
   production-shape soak (4 GB VM + local llama-server, 2 h) still needs
   real hardware. MSIX packaging/signing is templated + documented
   (`client/packaging/`, `docs/RELEASE.md`) but not CI-automated — it needs
   one iteration on a Windows machine with the real certificate.
9. First-run wizard covers server URL + mic + hotkey notice; CA-thumbprint
   pinning review and the full mic-level test are manual Windows passes.
