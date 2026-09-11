# Roadmap — phase history and acceptance criteria

All 8 phases are complete. Each phase follows: implement → tests green
(`pytest`, new client tests) → build → docs updated → README phase table
updated. Constraint: do not delete working functionality to simplify
(spec §19.13).

## Phase 1 — Contracts & shells ✅

Frozen the cross-cutting contracts first (ABCs, registry, router, WS schema,
client manifest) because every later phase depends on them. Shipped: FastAPI
shell (health/version/manifest/providers/observability, auth dev-token path,
WS control plane, structured logging, CORS, error envelope), AI layer
(STT/LLM ABCs, registry, mocks, llama-server client, pure privacy-first
router, health tracker), WPF shell (DI composition root, ApiClient, all spec'd
screens with MVVM, hotkey manager, settings store), infra (compose, nginx,
Dockerfile, Prometheus config, `.env.example`). Reference analysis and
conflict resolutions: `docs/ASSESSMENT.md`.

## Phase 2 — Live capture & transcript ✅

**Delivered**

- `client/.../Audio/NAudioCaptureService` implementing `IAudioCaptureService`
  (WASAPI, 16 kHz mono PCM16, 100 ms chunks, RMS level events; device
  hot-plug refresh).
- `WsTranscriptionClient` (ClientWebSocket): binary frames, `seq` tracking,
  reconnect with exponential backoff, heartbeat/pong, `session.pause/resume/stop`
  wired to UI states.
- Backend: audio router in `api/services/transcription_hub.py` →
  `STTProvider.stream()` → `transcript.interim/final` frames; session segment
  counter; interim never persisted; final segments kept in in-memory
  encounter buffer.
- Mock STT provider drives CI + dev demo end-to-end.

**Acceptance (met):** dictation loop works against `mock` with zero external
services; WPF shows interim/final, timestamps, editing; protocol tests
extended for the audio path; client xunit tests for capture lifecycle +
reconnect state machine.

**Deviations (honest):**

- Device hot-plug: enumeration/refresh is on-demand (recorder loads devices
  at start + manual refresh); no active MMDevice notification callback.
- Reconnect semantics: new session id (server state is per-session); client
  surfaces the gap via status note + `DroppedAudioFrames` counter; no
  session-resume token (sessions are immutable by design).
- Audio `seq`: client tracks capture sequence in `AudioChunk`; WS frames are
  content-addressed by the hub (the protocol allows omitting seq on binary
  frames).

## Phase 3 — STT adapters ✅

**Delivered**

- `whisper-local` (whisper-server HTTP; VAD windowed pseudo-streaming,
  buffer/interval/silence params; fa+en language + `initial_prompt` hotwords).
- `qwen-asr` adapter (OpenAI-audio-compatible service, fa-robust).
- `deepgram` (WS prerecorded live + keyword boosting incl. drug names,
  laterality terms) and `speechmatics` (WS, fa config, dictation + domain).
- Batch multipart endpoint `POST /api/v1/transcribe/batch`.
- Result adapters normalize to `TranscriptSegment`; confidence preserved where
  available; language mixing (English terms inside Persian) verified with
  fixture corpora.

**Acceptance (met):** each adapter unit-tested with recorded fixtures/mocks;
health-probed from `/api/v1/providers?probe=health`; registry listing shows
`configured` correctly per env.

**Deviations (honest):**

- "Windowed pseudo-streaming with VAD" is real: shared `ai/stt/_common.py`
  energy VAD (buffer/interval/silence/pre-roll knobs) dispatches utterances as
  batch requests; long utterances re-dispatch for interims. Timelines are
  audio-time, not wall-clock → deterministic tests.
- Cloud WS adapters sit behind a `WsTransport` seam
  (`ai/stt/ws_transport.py`); production uses `websockets`, tests use
  scripted transports — CI is network-free by design.
- No calls against the real Deepgram/Speechmatics services (no credentials in
  this environment) — protocol conformance is fixture-verified; first
  live-account run should re-verify against vendor sandbox. Qwen service
  shape assumed OpenAI-audio-compatible.
- `POST /api/v1/transcribe/batch` landed early (was a later line): multipart
  WAV/PCM16 upload, routed with privacy wall enforced, 413/400 validated,
  audio never persisted, metadata-only audit.

## Phase 4 — LLM adapters & report prompts ✅

**Delivered**

- `openai`, `anthropic`, `gemini` adapters (native SDKs avoided: httpx only);
  usage/token accounting into metrics + `ai_requests` ledger.
- Grounded note-prompt builder (`api/services/note_prompt.py`): sentinel-
  delimited TRANSCRIPT/CONTEXT/SECTIONS blocks, strict JSON schema + one
  repair round-trip (`MS_LLM__REPAIR_ENABLED`), template-order reconciliation
  with `[[MISSING]]` semantics.
- PHI redaction option pre-cloud (regex + optional NER).
- llama-server: capability discovery (`/props`, cached, failure-tolerant) +
  token metering.

**Acceptance (met):** `POST /api/v1/reports/{encounter}/draft` works with the
mock LLM; hallucination-sensitive fixtures: missing info stays "missing";
numbers/laterality/negation preserved or flagged; fabricated 80 mg produces
`unverified_number`; invalid-JSON repair succeeds once then fails closed with
502.

**Deviations (honest):**

- `anthropic` uses the native Messages wire (system extraction, merged
  same-role turns, required `max_tokens`, `count_tokens` health probe);
  `gemini` uses generateContent + SSE (systemInstruction, `x-goog-api-key`
  header — key never in URLs).
- No live-account calls to OpenAI/Anthropic/Gemini (wire conformance is
  MockTransport-fixture verified).
- The mock LLM obeys the report-prompt contract (fills only the first section
  verbatim, everything else `[[MISSING]]`) so the grounding pipeline is
  testable without keys.

## Phase 5 — Router hardening ✅

**Delivered**

- HealthTracker fed by real task outcomes: STT streams record per-provider
  success/failure inside the hub (keyed `stt:{name}`), LLM drafts record
  `llm:{name}` incl. latency; demotion = `failure_threshold` consecutive
  failures with half-open cooldown. Ranking uses a measured **latency EWMA**
  (`LATENCY_EWMA_ALPHA=0.3`) that outranks the static per-provider hint.
- Runtime fallback: a retryable `ProviderError` mid-session transparently
  switches to the next name in `RouteDecision.fallbacks` — one
  `PROVIDER_FALLBACK` warning frame per switch, session identity/URL/lock
  untouched, transcripts continue in the same store. The chain is
  privacy-filtered **at route() time**, so fallback can never cross the wall
  (test-pinned). Batch tasks walk the same chain without transparency; total
  failure = 502 with the full `tried[]` list. Kill switch:
  `MS_ROUTING__FALLBACK_ENABLED=false`.
- Cost budget guard: `MS_ROUTING__BUDGET_TOKENS_PER_DAY` (0 = off). On
  exhaustion the router sets `cloud_excluded` — a soft stop to local-only;
  `GET /api/v1/observability/stats` exposes `cost.{tokens_today, exhausted}`
  and the whole `provider_health` snapshot.
- Redis shared view (optional): `HealthMirror` publishes tracker snapshot +
  aggregate audio-queue depth and hydrates from peers on startup
  (`MS_REDIS__URL` + `MS_ROUTING__HEALTH_MIRROR_INTERVAL_S>0`). Lazy import —
  no redis driver needed in single-worker dev/CI; every redis error degrades
  to local-only, never a crash.

**Deviations (honest):**

- Queue depth is a per-worker sum (no global max yet); `absorb` is an average,
  not CRDT-merged.
- Budget counters were in-process at P5 (restart reset the day); durable
  backfill from `ai_requests` landed in Phase 8.

## Phase 6 — Commands, templates, validation ✅

**Delivered**

- Extensible command parser (`backend/api/services/voice_commands/`):
  registry of command handlers with bilingual triggers, exact/anchored match
  policy + "command mode" (only strips speech that couldn't be clinical
  content; ambiguity → keep text + `warning`), context gates (e.g.
  `finalize_section` only while recording).
- Terminology normalization (`terminology_data.py` catalog + `terminology.py`
  engine): fa↔en medical terms, whole-term longest-match, reversible
  substitutions, engine REFUSES entries containing digits/units/negation/
  laterality. Endpoints: `GET /api/v1/terminology`,
  `POST /api/v1/terminology/normalize`.
- Template service (`api/services/templates.py`): CRUD, JSON-schema sections,
  format styles, LLM extraction-from-example (returns an UNSAVED proposal),
  radiology/us/ct/mri/soap/general built-ins seeded as data; client renders
  dynamically from server lists (fork supported).
- Report generator + validator (`validation.py`): numbers/doses/units,
  laterality, negation, dates (Jalali + Gregorian), identifiers, anatomy;
  emits `ValidationWarning[]`; draft/finalize/approve endpoints +
  immutability of approved versions.

**Acceptance (met):** medical-safety pack green
(`tests/test_medical_validation.py`: 10mg→100mg critical, right→left
critical, no-effusion→effusion critical, missing-stays-missing, Jalali date
equality/one-day-off, identifier flips, bilingual unit equivalence). WPF
report screen shows severity-colored warnings, acknowledgment with
justification, finalize/approve blocked by the server (409) and surfaced
honestly.

**Deviations (honest):**

- Validation compares transcript→draft; the normalized view is a synonym
  union, never a fabrication source. Old light-check codes
  (`unverified_number`, `laterality_unverified`, `negation_shift_suspected`)
  preserved for wire compatibility.
- Templates/reports/transcripts were in-memory at P6 (service APIs are
  repository-shaped); P7 added persistence. Template extraction verified
  against scripted LLMs only.

## Phase 7 — Persistence, auth, Redis ✅

**Delivered**

- SQLAlchemy 2.0 async models for all entities (`backend/api/models/orm.py`,
  14 tables) + Alembic baseline migration (`backend/alembic/`, URL from
  `MS_DATABASE__URL`) + idempotent seed (roles, built-in templates as rows,
  provider metadata mirror, admin bootstrap only with an explicit password).
- Auth: argon2id hashing (dummy-hash timing equalizer), credential
  login/refresh/logout, one-time refresh tokens with rotation — reuse of a
  consumed token revokes ALL of that user's sessions; per-username lockout
  (5 failures → 300 s default); dev-token path unchanged for non-prod.
- Rate limiting: Redis backend when `MS_REDIS__URL` is set, in-process
  otherwise, degrade-open on backend failure (lockout guard remains the auth
  bucket's second layer); tighter auth bucket; per-user concurrent WS session
  cap (`MS_RATE_LIMIT__WS_SESSIONS_PER_USER`).
- Audit dual-write: JSONL file + `audit_log` rows via a bounded background
  writer (drops to file-only when the queue is full); admin query API
  `GET /api/v1/admin/audit`.
- Persistence write-through: transcripts (whole-session idempotent upsert on
  WS session end, shielded from transport-teardown cancellation), reports +
  `report_revisions` rows, template catalog reload, AI request ledger; memory
  caches stay authoritative on DB lag (write failures log + count, never
  break the clinical flow); `GET` falls back to durable rows after LRU
  eviction or restart.
- Provider secrets: Fernet-encrypted at rest
  (`ai_providers.secret_ciphertext`), admin PUT/DELETE API, decrypted in
  exactly one place (`ai_bridge.provider_config_with_secrets`); secrets never
  appear in queries, listings, or logs.
- New surface: `POST /api/v1/patients`, `GET /api/v1/patients`,
  `GET /api/v1/patients/{id}`, `POST /api/v1/encounters`,
  `GET /api/v1/encounters/{id}`, `GET /api/v1/reports/{id}/revisions`, admin
  users/audit/secrets/ai-requests endpoints.
- In-memory dev mode is unchanged and fully functional when
  `MS_DATABASE__URL` is unset; DB-backed routes answer 501
  `DB_NOT_CONFIGURED` instead of pretending.

**Deviations (honest):**

- Audio retention policy (`MS_AUDIO__RETAIN_HOURS`) not implemented — audio
  is never persisted in the first place (only transcripts/reports are).
- WPF client stores the refresh token in memory and sends it on logout, but
  has no background auto-refresh timer yet (token TTL 30 min; re-login
  required after expiry in the current client).

## Phase 8 — Hardening & release ✅

**Delivered**

- Prometheus `/metrics` live (dependency-free text exposition,
  `medicalscribe_*` series, route/status labels, latency quantiles p50/p95/p99
  + exact sum/count); optional OTel tracing (`observability` extra +
  `MS_OBSERVABILITY__OTEL_ENABLED`, OTLP gRPC); Grafana dashboard shipped
  (`infrastructure/prometheus/grafana-dashboard.json`, compose
  `--profile monitoring`).
- WS app-level keepalive: server sends `heartbeat.ping` on idle (one
  heartbeat interval), client auto-pongs; 3 silent intervals still close 4408
  (nginx read/send timeouts tuned to 3600 s).
- Cost-budget counters are now durable: the ledger backfills today's tokens
  from `ai_requests` at boot — a mid-day restart no longer resets the budget.
- CI hardening: backend tests run against **real PostgreSQL 16 + Redis 7
  services** (fresh DB per test); `pip-audit`; `gitleaks` full-history secret
  scan (`.gitleaks.toml` allowlists synthetic test fixtures only).
- Load test: `infrastructure/load/ws_loadtest.py` — 20 concurrent sessions ×
  15 s verified against the mock STT (20/20 completed, 0 errors, handshake
  p95 = 14 ms, per-final arrival p50 ≈ 1.1 s on the sandbox CPU).
- First-run wizard in the WPF client (server URL + connectivity probe,
  microphone selection, hotkey notice; runs before DI so the saved URL is
  what gets wired).
- MSIX: manifest + `.appinstaller` auto-update templates
  (`client/packaging/`) + full packaging/signing runbook in
  `docs/RELEASE.md`.
- Security: `docs/SECURITY.md` (env checklist, data-hygiene invariants,
  gateway pen-test checklist, accepted risks); release runbook
  `docs/RELEASE.md` incl. regulatory-boundary review step.

**Deviations (honest):**

- The 20-session number is on the **mock STT path in a sandbox** — the
  4 GB VM + local llama-server p50/p95 soak (2 h) still needs real hardware.
- MSIX build/sign is a documented manual Windows step (makeappx/SignTool are
  Windows-only), not CI-automated.
- CA-thumbprint pinning in the WPF client settings remains a manual review
  item (no global TLS-bypass flags exist — verified).

## Standing engineering constraints (all phases)

- WPF never talks to providers; no secrets in the client; REST/WS protocol v1
  additive-only until a v2 bump is justified.
- Provider logic lives in exactly one adapter module; registration in one
  place (`ai/registry.py`).
- New behavior arrives with tests; the medical-safety pack
  (`tests/test_medical_validation.py`) is a merge gate.
- Reference checkouts remain read-only inspiration; attribution stays in
  `docs/THIRD_PARTY_NOTICES.md`.
