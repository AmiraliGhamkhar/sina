# Phase 1 Assessment — MedicalScribe

Date: 2026-09-10 · Scope: reference inspection + Phase 1 implementation plan
(see `docs/SOURCE_MAP.md` for the file-level reference index).

## 1. Current state (before this phase)

The repository contained only scaffolding: empty `backend/`, `client/`, `ai/`,
`tests/`, `infrastructure/`, `models/`, `third_party/` directories with
`.gitkeep` files, a `.gitignore` (Python/.NET/secrets/models exclusions),
`docs/SOURCE_MAP.md`, and the four reference checkouts at root. **No first-party
code existed.** All Phase 1 work below was written against the target stack
specification, not by porting reference code wholesale.

Environment note: development happens on Linux (CI/sandbox) while the client is
Windows-only WPF. There is no .NET SDK in the sandbox and WPF cannot build or
run on Linux, so Phase 1 verifies the **Python side end-to-end with tests**
(65 tests, all green) and delivers the WPF shell as compile-on-Windows code
(`EnableWindowsTargeting` allows `dotnet build` for CI later). A build check on
Windows/CI is the remaining verification gap (see §6 and README "Known gaps").

## 2. Reusable concepts per reference (patterns, not code)

### SpeakType (MIT) — used for the Windows client only
- `AudioRecorder.cs`: NAudio `WaveInEvent` at **16 kHz mono PCM16**, RMS audio
  level events, device auto-selection heuristics → adopted as the contract in
  `client/.../Audio/AudioContracts.cs` (implementation Phase 2). Its
  write-WAV-to-disk model is **dropped**: audio must stream to FastAPI, STT is
  server-side by requirement.
- `HotkeyService.cs`: `RegisterHotKey` + `WM_HOTKEY` message hook → rewritten
  as `Win32HotkeyService` with multiple simultaneous registrations, gesture
  parsing from settings, `MOD_NOREPEAT`, correct ID range. (The reference used
  IDs outside `RegisterHotKey`'s documented 0x0000–0xBFFF range — fixed here.)
- `SettingsStore.cs`/`AppSettings.cs`: JSON file settings with corruption
  fallback → `JsonSettingsStore` in the client (client prefs only; no model
  paths — no local Whisper architecture is kept, per spec).
- Not used: its `WhisperTranscriber`, `InputSender`, `ClipboardInserter`,
  overlay UX (not in scope for the first phase; text injection into EMRs is a
  later product decision, and injection must respect the same review gate).

### Phlox (MIT) — FastAPI + medical-documentation concepts
- `server/server.py`: app factory + middleware stack ordering (security →
  auth → rate limit → audit) → `create_app()` composes middleware/state once.
- `api/templates.py` + `nlp_tools/templates.py`: **data-driven templates**,
  and the "extract a structured template from an example note" prompt shape →
  Phase 6 report-template service (schema frozen in `ReportTemplate` entity
  plan; UI never hardcodes sections).
- `llm_client/client.py`: unified OpenAI-compatible client + `repair_json`
  for brittle local models → `ai/llm/openai_compat.py` (JSON-mode requests +
  the repair step is planned where strict JSON output is required).
- `api/transcribe.py`: upload-multipart transcription endpoint pattern → will
  inform the Phase 3 *batch* endpoint. Its SQLite/local-config-manager state is
  deliberately not copied (PostgreSQL + SQLAlchemy + Alembic instead).

### Open Medical Scribe (MIT) — provider architecture + safety framing
- `providers/transcription/index.js` + `streamIndex.js`: batch vs **streaming**
  provider families with a factory per kind → `STTProvider.transcribe()` vs
  `STTProvider.stream()` on one ABC, registry-keyed by `(kind, name)`.
- `whisperStreamProvider.js`: buffer audio, run inference on VAD-ish windows,
  emit interim + finalized text, silence-timeout utterance end → this is the
  blueprint for `ai/stt/local_whisper.py` (Phase 3) since local Whisper servers
  are not natively streaming. Constants (interval, min samples, RMS silence
  threshold) are sensible starting values, to be retuned for fa/en.
- `resultAdapter.js`: provider-specific results → one canonical segment shape →
  `TranscriptSegment` in `ai/base.py`.
- `services/privacy.js`: redact-before-cloud (SSN/phone/email/MRN patterns) →
  Phase 4 redaction layer; note the sharper rule we adopt from the spec:
  **privacy forces local routing**; redaction is an extra option, not a
  license to cloud-route clinical speech.
- `services/scribeService.js` + `promptBuilder.js`: transcript→note pipeline
  shape, "do not invent facts… mark missing info as follow-up", warnings array,
  audit event per encounter → adopted as *contracts*: `warnings` on responses,
  grounded-only prompting (Phase 6), audit on every provider selection
  (implemented already in the WS handshake).
- `auditLogger.js`: append-only JSONL audit → `api/services/audit.py` with a
  **deny-list sanitizer** (reference had none; transcripts must never be
  persisted by the audit sink).
- `electron/llamaServer.js`: spawning/bundling llama.cpp → **rejected** for
  the server side per spec §12 (llama-server is an external service; the
  backend only probes/uses its HTTP API).
- Its Node/Express stack and JS idioms are not carried over; the backend is
  Python-native.

### Multi-Model-Gateway (MIT) — router/ops concepts
- `services/provider_router.py`: a *pure* decision function separated from IO
  ("heuristic `resolve_role` is pure and testable without a DB") → `ai.router.route()`
  is pure over a candidate list; tests cover it without any network.
- `services/provider_registry.py`: descriptors → adapter factories, `mask_key`
  for display → `ai/registry.py` (`ProviderDescriptor`, config projection,
  `configured` predicate).
- `core/redis.py` + `core/queue.py`: lazy Redis pools, arq queues → Phase 7
  (redis client + rate limiting) and Phase 5 (queue-depth routing input).
- `middleware/ratelimit.py`: sliding-window limiter that **degrades open when
  Redis dies** (documented tradeoff) and stricter buckets on auth paths →
  adopted shape in Phase 7 (with the same "redis outage ≠ 500 everywhere").
- `core/security.py`/crypto for at-rest provider secrets (Fernet, derived key
  with explicit override) → Phase 7 provider-secret storage design; the
  security lessons noted there (mask keys in responses, fail-closed metrics
  endpoint when untokenized, trusted-proxy CIDR list for X-Forwarded-For) are
  carried into docs and will gate Phase 7/8 review.

## 3. Conflicting designs (and resolutions)

| Conflict | Resolution |
|---|---|
| SpeakType embeds Whisper.cpp locally; spec says STT is server-side | Client only captures/streams audio; STT behind FastAPI (Phase 3 adapters). No local models in the client. |
| Open Medical Scribe is Node/Express/JS with npm provider SDKs | Only the *provider abstraction* transfers; backend is FastAPI/async httpx; provider SDKs avoided until needed (ruff rule 9: no unnecessary deps). |
| Phlox uses a JSON config manager + SQLite repositories; spec demands PostgreSQL+SQLAlchemy+migrations | Phlox patterns are limited to routes/prompt/template *logic*; persistence stack is ours (Phase 7). |
| MMG stores per-user provider rows in Postgres + Fernet keys | Kept as *design*, deferred: Phase 1 ships registry-from-settings; DB-backed AIProvider table (spec §13) in Phase 7. |
| OMS auto-inserts clipboard text / auto-applies generated notes | Rejected: spec §5/§8 — AI text never becomes a final record; explicit finalize/approve only. |
| MMG arq queue for chat jobs | Not needed for real-time audio; WS streams directly. Queues (Redis) reserved for batch transcription/report jobs (Phase 5/7). |
| Phlox local-mode "shared secret in query param" auth | WS token goes in a short-lived, revocable JWT; dev static token only in non-production, never logged. |

## 4. Missing components (what had to be designed from scratch)

1. **Provider contracts + registry** (`ai/base.py`, `ai/registry.py`) — the
   seam every reference solves differently; ours is one ABC pair + descriptor
   registry, capabilities-first so the router never instantiates to decide.
2. **Privacy hard-wall routing** — no reference treats "private ⇒ LOCAL only"
   as non-negotiable including vs an explicit user pick; spec §11 demands it;
   implemented + tested (`test_privacy_required_forces_local_even_in_cloud_mode`).
3. **WS protocol v1** (`api/schemas/ws.py`, `docs/WEBSOCKET_PROTOCOL.md`) —
   none of the references version their control protocol; ours is strict
   (extra-fields forbidden), discriminated-union validated, close-code
   disciplined, so Phase 2 only *fills in* `audio.chunk` processing behind an
   already-frozen schema.
4. **Error taxonomy shared by REST + WS** (`api/errors.py`) — stable codes so
   the WPF client switches on codes, not strings/messages.
5. **Audit with data-hygiene deny-list** (`api/services/audit.py`) — the
   references audit raw fields; ours structurally cannot persist
   transcript-shaped values.
6. **Client manifest** (`api/routes/meta.py` + `schemas/manifest.py`) —
   serverside-owned feature flags, voice-command catalog, audio format; kills
   UI-hardcoded policy (spec §7/§10) — the WPF shell already consumes it.
7. **The WPF app itself** — nothing existed; SpeakType gave shapes only.

## 5. Implementation order (spec phases vs reality)

Phase order stays as specified; Phase 1 pulled forward only the *contracts*
(ABCs, registry, router, WS schema, manifest) because every later phase depends
on them and freezing them now prevents WPF↔backend drift. Remaining sequence:

- **P2** audio capture (NAudio WASAPI) → WS binary frames → mock STT stream →
  live transcript (schemas already exist).
- **P3** local whisper-server + qwen-asr + speechmatics/deepgram adapters
  (stream + batch), VAD/segmentation tuning for fa/en.
- **P4** llama-server done at transport level already; P4 adds OpenAI/
  Anthropic/Gemini + `repair_json` + report prompts + redaction option.
- **P5** router upgrades: live latency from metrics, queue depth (Redis),
  runtime fallback on ProviderError w/ HealthTracker (in-process version shipped).
- **P6** command parser (extensible, context-gated), terminology normalization,
  template CRUD + generation, validation rules (numbers/units/laterality/
  negation/dates/IDs) producing `warning` frames, report lifecycle endpoints.
- **P7** Postgres schema + Alembic, users/roles/JWT+refresh+argon2, provider
  rows with encrypted secrets, rate limiting, DB audit, Redis session state.
- **P8** Prometheus /metrics + OTel traces, load testing, MSIX installer,
  production hardening pass.

## 6. Phase 1 completion state

- FastAPI shell: health/version/manifest/providers/observability endpoints,
  auth contract (dev-token path live, credential store Phase 7), WS control
  plane (handshake, router-selected provider, pause/resume/stop, audit),
  structured logging + counters, CORS, error envelope, settings via env.
- AI layer: STT/LLM ABCs, registry, mock STT/LLM, llama-server OpenAI-compat
  client (config, retries, SSE, health), pure privacy-first router, health
  tracker.
- WPF shell: DI composition root, ApiClient with token store, auth/status
  services, all 10 spec'd screens with MVVM (CommunityToolkit), hotkey
  manager, settings store, audio/hotkey contracts with honest Phase banners.
- Infra: compose (api/postgres/redis + profiles), nginx (TLS+WS), Dockerfile,
  prometheus config, `.env.example`.
- Tests: 65 backend/AI tests, `pip install .` verified, ruff clean.

Remaining limitations → README "Known gaps".
