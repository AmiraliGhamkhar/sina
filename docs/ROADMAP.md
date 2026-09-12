# Roadmap — remaining phases with acceptance criteria

Each phase: implement → tests green (`pytest`, new client tests) → build →
docs updated → phase note appended to README table. Do not delete working
functionality to simplify (spec §19.13).

## Phase 2 — Live capture & transcript ✅ (done — this build)
- `client/.../Audio/NAudioCaptureService` implementing `IAudioCaptureService`
  (WASAPI, 16 kHz mono PCM16, 100 ms chunks, RMS level events; device
  hot-plug refresh).
- `WsTranscriptionClient` (ClientWebSocket): binary frames, `seq` tracking,
  reconnect w/ exponential backoff + resume semantics (new session; client
  marks gap), heartbeat/pong, `session.pause/resume/stop` wired to UI states.
- Backend: audio router in `api/services/transcription_hub.py` →
  `STTProvider.stream()` → `transcript.interim/final` frames; session segment
  counter; interim never persisted; final segments kept in in-memory
  encounter buffer (persisted in Phase 7).
- Mock STT provider drives CI + dev demo end-to-end (recorder → live text).
- Acceptance: dictation loop works against `mock` with zero external
  services; WPF shows interim/final, timestamps, editing; protocol tests
  extended for audio path; client xunit tests for capture lifecycle +
  reconnect state machine (run on Windows CI runner — first CI job to add).


Done notes (deviations recorded honestly):
- All items landed: `NAudioCaptureService` (WaveInEvent 16k mono, ~100 ms
  chunks from the driver buffer, RMS level events, virtual-device-aware
  auto-pick + `ListDevicesAsync` refresh), `WsTranscriptionClient`
  (ClientWebSocket, binary frames, bounded 16-frame send channel with
  drop-count, `ReconnectPolicy` 2ⁿ backoff capped 30 s × N attempts),
  `DictationSession` coordinator, backend `transcription_hub` +
  `TranscriptStore` + session transcript GET/PATCH endpoints, mock-STT e2e
  in `tests/test_ws_audio_flow.py` (72 backend tests total), and
  `client/MedicalScribe.WPF.Tests` (parser/policy/sink tests; must be run on
  a Windows CI runner — no SDK exists in this Linux sandbox).
- Device hot-plug: enumeration/refresh is on-demand (recorder loads devices
  at start + manual refresh); no active MMDevice notification callback.
- Reconnect semantics: new session id (server state is per-session), client
  surfaces the gap via status note + `DroppedAudioFrames` counter; no
  session-resume token (spec keeps sessions immutable).
- Heartbeat/pong: client `pong` reserved; server pings deferred to Phase 8
  (idle-timeout close 4408 already enforced).
- Audio `seq`: client tracks capture sequence in `AudioChunk`; WS frames are
  content-addressed by hub (protocol allows omitting seq on binary frames).

## Phase 3 — STT adapters ✅ (done — this build)
- `whisper-local` (whisper-server HTTP; windowed pseudo-streaming with VAD,
  buffer/interval/silence params; fa+en language + initial prompt hotwords).
- `qwen-asr` adapter (service HTTP/WS, fa-robust).
- `deepgram` (WS `prerecorded` live + keyword boosting incl. drug names,
  laterality terms) and `speechmatics` (WS, fa config, dictation + domain).
- Batch multipart endpoint `POST /api/v1/transcribe/batch`.
- Result adapters normalize to `TranscriptSegment`; confidence preserved where
  available; language mixing (English terms inside Persian) verified with
  fixture corpora (number/negation/laterality preservation tests).
- Acceptance: each adapter unit-tested with recorded fixtures/mocks;
  health-probed from `/api/v1/providers?probe=health`; registry listing shows
  `configured` correctly per env.


Done notes (deviations recorded honestly):
- All four adapters landed: `whisper-local` (whisper.cpp `/inference`, WAV
  container, hotwords via `initial_prompt`), `qwen-asr` (OpenAI-audio
  `transcriptions` endpoint, verbose_json), `deepgram` (prerecorded HTTP +
  live WS with keyword boosting incl. drug names/laterality) and
  `speechmatics` (batch job lifecycle + realtime WS, `fa` default,
  operating domain configurable).
- "Windowed pseudo-streaming with VAD" is real: shared `ai/stt/_common.py`
  energy VAD (buffer/interval/silence/pre-roll knobs) dispatches utterances
  as batch requests; long utterances re-dispatch for interims. Timelines are
  audio-time, not wall-clock → deterministic tests.
- Cloud WS adapters sit behind a `WsTransport` seam
  (`ai/stt/ws_transport.py`); production uses `websockets`, tests use
  scripted transports — CI is network-free by design.
- Acceptance met: 108 backend tests, fixtures under `tests/fixtures/`
  (medical corpora verify number/dose/negation/laterality/embedded-English
  preservation), `/api/v1/providers?probe=health` exercises each adapter's
  health probe, `configured` per env is asserted.
- Not covered honestly: no calls against the real Deepgram/Speechmatics
  services (no credentials in this environment) — protocol conformance is
  fixture-verified; first live-account run should re-verify against vendor
  sandbox. Qwen service shape assumed OpenAI-audio-compatible.
- `POST /api/v1/transcribe/batch` landed early (was P3 line): multipart
  WAV/PCM16 upload, routed with privacy wall enforced, 413/400 validated,
  audio never persisted, metadata-only audit.

## Phase 4 — LLM adapters & report prompts ✅ (done — this build)
- `openai`, `anthropic`, `gemini` adapters (native SDKs avoided: httpx only);
  usage/token accounting into metrics + `ai_requests`.
- Grounded note-prompt builder (transcript + patient context + template
  sections only), strict JSON schema + repair (Phlox pattern),
  PHI-redaction option pre-cloud.
- llama-server: already transport-complete; add capability discovery
  (`/props`), token metering.
- Acceptance: `POST /api/v1/reports/{encounter}/draft` works with mock LLM;
  hallucination-sensitive fixtures: missing info stays "missing"; numbers /
  laterality / negation preserved or flagged.


Done notes (deviations recorded honestly):
- `openai` (OpenAI-compat transport reuse), `anthropic` (native Messages
  wire: system extraction, merged same-role turns, required max_tokens,
  count_tokens health probe) and `gemini` (generateContent + SSE streaming,
  systemInstruction, x-goog-api-key header — key never in URLs) landed with
  httpx only; no SDKs. Usage/token accounting flows into `llm_requests:*` +
  `llm_tokens:<provider>:*` counters and `llm_latency_ms`; the `ai_requests`
  table (same fields) is Phase 7 backfill.
- Grounded prompt builder (`api/services/note_prompt.py`): sentinel-delimited
  TRANSCRIPT/CONTEXT/SECTIONS blocks, strict JSON schema in the system
  contract, one repair round-trip (`MS_LLM__REPAIR_ENABLED`), template-order
  reconciliation with `[[MISSING]]` semantics. Light fidelity check (numbers/
  laterality/negation vs. transcript — "preserved or flagged") ships now;
  the full validator + dosage dictionary is Phase 6 as planned.
- PHI: best-effort identifier scrub before ANY cloud call
  (`MS_LLM__CLOUD__REDACT_PHI_FOR_CLOUD`, default on) — the actual guarantee
  remains the privacy wall (privacy_required never routes cloud; tested).
- llama-server: `GET /props` capability discovery (cached; failure-tolerant)
  + usage metering. Mock LLM upgraded to obey the report-prompt contract
  (fills only the first section verbatim, everything else [[MISSING]]) so the
  grounding pipeline is testable without keys — legacy prompts keep the old
  canned shape.
- Acceptance met: `POST /api/v1/reports/{encounter}/draft` works against the
  mock with 135 backend tests; hallucination fixture (fabricated 80 mg)
  produces `unverified_number`, unsupported sections stay missing; invalid-
  JSON repair succeeds once then fails closed with 502.
- Not covered honestly: no live-account calls to OpenAI/Anthropic/Gemini
  (wire conformance is MockTransport-fixture verified); report storage,
  editing endpoints and finalize/approve land with P6/P7.

## Phase 5 — Router hardening ✅ (this build)
- HealthTracker is fed by real task outcomes: STT streams record per-provider
  success/failure inside the hub (keyed `stt:{name}`), LLM drafts record
  `llm:{name}` incl. latency; demotion = `failure_threshold` consecutive
  failures with half-open cooldown. Ranking now uses a measured **latency EWMA**
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
- Not covered honestly: queue-depth is per-worker sum (no global max yet);
  budget counters are in-process (restart resets the day; durable spend lives
  with the P7 `ai_requests` table); `absorb` is an average, not CRDT-merged.

## Phase 6 — Commands, templates, validation ✅ (done — this build)
- Extensible command parser (`backend/api/services/voice_commands/`):
  registry of command handlers w/ bilingual triggers, exact/anchored match
  policy + "command mode" (only strips speech that couldn't be clinical
  content; ambiguity → keep text + `warning`), context gates (e.g.
  `finalize_section` only while recording).
- Terminology normalization dictionary (fa↔en medical terms; display-time vs
  stored-original split; reversible).
- Template service: CRUD, JSON-schema sections, format styles, LLM
  extraction-from-example (Phlox concept), radiology/us/ct/mri/soap/general
  built-ins seeded as data; client renders dynamically from server lists.
- Report generator + validator: numbers/doses/units, laterality, negation,
  dates (Jalali+Gregorian), identifiers; emits `ValidationWarning[]`;
  draft/finalize/approve endpoints + immutability of approved versions.

Done notes (deviations recorded honestly):
- All items landed. Voice commands: `api/services/voice_commands/` package
  (catalog → parser → effects) — data-driven, bilingual, whole-utterance
  exact/anchored matching, `فرمان`/"command" mode prefixes, ambiguity keeps
  text + `COMMAND_AMBIGUOUS` warning (never deletes clinical speech). Effects:
  paragraph/section/finalized-section markers, delete-last-sentence with
  journal-backed undo, repeat, voice pause/resume (audio keeps flowing so the
  resume command stays audible — distinct from control-frame pause which
  buffers). Hub integration: command utterances never enter the transcript;
  per-command metrics; `command.detected` frames carry args + utterance.
- Terminology: `terminology_data.py` (curated catalog: transliterations →
  English terms incl. spec-listed MRI/CT/ECG/hypertension; native Persian
  terms stay Persian) + `terminology.py` engine (whole-term longest-match,
  reversible substitutions, engine REFUSES entries containing digits/units/
  negation/laterality). `GET /terminology`, `POST /terminology/normalize`.
  The draft prompt uses the normalized view; validation grounds against
  raw + normalized (union = synonyms, never fabrications).
- Templates: `templates.py` service + full CRUD API; built-ins seeded as data
  (general/soap/radiology/us/ct/mri), immutable; fork; LLM extraction from
  example note (Phlox concept) returns an UNSAVED proposal. WPF Templates
  screen is now server-driven + fork.
- Reports: server-side `report_store.py` with draft → finalized → approved;
  PATCH sections (revision events), acknowledge-with-justification, finalize
  and approve both blocked by unacknowledged **critical** warnings, reopen,
  amend (approved immutable, amendments linked). Draft endpoint accepts
  `session_id` (marker-aware assembly + terminology view) or inline text.
- Validation (`validation.py`): unit-aware bilingual quantities (۴۰ میلی‌گرم
  == 40 mg; cc≈ml), dose near-miss detection (10→100 mg critical, drug
  proximity), laterality pairs + swap detection (fa+en), term-level negation
  scope analysis (بدون/عدم/ندارد/نمی/no/without/denies…, contrast-conjunction
  scope cuts), dates incl. Jalali↔Gregorian conversion + month names,
  identifiers (phone/national-id/MRN, decimal-safe), anatomy grounding.
  Old light-check codes preserved (`unverified_number`,
  `laterality_unverified`, `negation_shift_suspected`) for wire compat.
- Acceptance: medical-safety pack green (`tests/test_medical_validation.py`:
  10mg→100mg critical, right→left critical, no-effusion→effusion critical,
  missing-stays-missing, Jalali date equality/one-day-off, identifier flips,
  bilingual unit equivalence). WPF report screen shows severity-colored
  warnings, acknowledgment with justification, finalize/approve blocked by
  the server (409) and surfaced honestly. 243 backend tests.
- Not covered honestly: templates/reports/transcripts are in-memory (P7
  persistence is the seam — service APIs are repository-shaped); template
  extraction verified against scripted LLMs only; the WPF client compiles in
  CI (no SDK in the dev sandbox) — command projection + lifecycle logic is
  unit-tested headlessly, window-level behavior needs the Windows runner.

## Phase 7 — Persistence, auth, Redis ✅ (done — this build)
- SQLAlchemy 2.0 async models for all §13 entities (`backend/api/models/orm.py`,
  14 tables) + Alembic baseline migration (`backend/alembic`, URL from
  `MS_DATABASE__URL`) + idempotent seed (roles, built-in templates as rows,
  provider metadata mirror, admin bootstrap only with an explicit password).
- Auth: argon2id hashing (dummy-hash timing equalizer), credential
  login/refresh/logout, one-time refresh tokens with rotation — reuse of a
  consumed token revokes ALL of that user's sessions; per-username lockout
  (5 failures → 300 s default); dev-token path unchanged for non-prod.
- Rate limiting: Redis backend when `MS_REDIS__URL` is set, in-process
  otherwise, degrade-open on backend failure (lockout guard remains as the
  auth bucket's second layer); tighter auth bucket; per-user concurrent WS
  session cap (`MS_RATE_LIMIT__WS_SESSIONS_PER_USER`).
- Audit dual-write: JSONL file (unchanged) + `audit_log` rows via a bounded
  background writer (drops to file-only when the queue is full); admin query
  API `GET /api/v1/admin/audit`.
- Persistence write-through: transcripts (whole-session idempotent upsert on
  WS session end, shielded from transport-teardown cancellation), reports +
  `report_revisions` rows, template catalog reload, AI request ledger; memory
  caches stay authoritative on DB lag (write failures log + count, never
  break the clinical flow); `GET` falls back to durable rows after LRU
  eviction or restart.
- Provider secrets: Fernet-encrypted at rest (`ai_providers.secret_ciphertext`),
  admin PUT/DELETE API, decrypted in exactly one place
  (`ai_bridge.provider_config_with_secrets`); secrets never appear in
  queries, listings, or logs.
- New surface: `POST /patients`, `GET /patients`, `GET /patients/{id}`,
  `POST /encounters`, `GET /encounters/{id}`, `GET /reports/{id}/revisions`,
  admin users/audit/secrets/ai-requests endpoints.
- In-memory dev mode is unchanged and fully functional when
  `MS_DATABASE__URL` is unset; DB-backed routes answer 501
  `DB_NOT_CONFIGURED` instead of pretending.
- Done-notes (honesty ledger): integration tests run on sqlite+aiosqlite
  (same engine code path as Postgres minus the server) — a real
  postgres/redis compose-profile CI job is still open (Phase 8 hardening);
  audio retention policy (`MS_AUDIO__RETAIN_HOURS`) not implemented — audio
  is never persisted in the first place (only transcripts/reports); WPF
  client stores the refresh token in memory and sends it on logout, but has
  no background auto-refresh timer yet (token TTL 30 min; re-login required
  after expiry in the current client).

## Phase 8 — Hardening & release ✅ (done in this build — see honesty ledger)
- Prometheus `/metrics` live (dependency-free text exposition,
  `medicalscribe_*` series, route/status labels, latency quantiles p50/p95/p99
  + exact sum/count); optional OTel tracing (`observability` extra +
  `MS_OBSERVABILITY__OTEL_ENABLED`, OTLP gRPC); Grafana dashboard shipped
  (`infrastructure/prometheus/grafana-dashboard.json`, compose
  `--profile monitoring`).
- WS app-level keepalive: server sends `heartbeat.ping` on idle (one
  heartbeat interval), client auto-pongs; 3 silent intervals still close
  4408 (nginx already tuned to 3600 s read/send timeouts).
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
  `docs/RELEASE.md`; the actual MSIX build needs a Windows machine
  (makeappx/SignTool are Windows-only) — NOT yet automated on the CI Windows
  runner.
- Security: `docs/SECURITY.md` (env checklist, data-hygiene invariants,
  gateway pen-test checklist, accepted risks); release runbook
  `docs/RELEASE.md` incl. regulatory-boundary review step.
- Done-notes (honesty ledger): the 20-session number is on the **mock STT
  path in a sandbox** — the roadmap's 4 GB VM + local llama-server p50/p95
  soak (2 h) still needs real hardware; MSIX build/sign is a documented
  manual Windows step, not CI-automated; CA-thumbprint pinning in the WPF
  client settings remains a manual review item (no global TLS-bypass flags
  exist — verified).

## Phase 9 — Provider expansion ✅ (done in this build — see honesty ledger)

Requested scope: add **9Router** (LLM *and* STT) to backend + client, and fix
the **Speechmatics realtime** WebSocket path against the current published
protocol. Explicit client-side configuration, not discovery alone.

- Shared plumbing `ai/nine_router_client.py`: base-URL normalization
  (accepts `http://host:20128`, `…/api`, `…/api/v1`), optional auth — 9Router
  accepts `Authorization: Bearer` or `x-api-key`, we send Bearer and only when
  a key is configured, since a keyless instance is a valid setup —
  `resolve_model_id` (a bare model name is *our* config error, reported as
  such rather than passed through as a 400 from the proxy), and
  `parse_models_payload` (reads `data` / `models` / `results` or a bare list;
  entries as objects with `id` or as plain strings; unknown shapes degrade to
  an empty list, because discovery is advisory and never on the audio path).
- `ai/llm/nine_router.py` — `NineRouterProvider`: OpenAI-compatible
  `POST {base}/api/v1/chat/completions` with streaming SSE, implemented as a
  thin configuration of the existing `OpenAICompatClient` (one transport, one
  retry policy, one data-hygiene story) rather than a second HTTP stack.
  Health + catalog via `GET {base}/api/v1/models`; an instance with no
  connected accounts answers 200 with an empty list, reported as *healthy but
  unusable* so AI Settings can explain why drafting fails.
- `ai/stt/nine_router.py` — `NineRouterSttProvider`: Whisper-compatible
  `POST {base}/api/v1/audio/transcriptions` multipart (file + `model`,
  `response_format`, `temperature`, optional `language` and a medical-hotword
  `prompt`), parses upstream `verbose_json` timed segments or falls back to
  `{text}` as one span; `stream()` slices through the shared `VadSegmenter`
  (9Router exposes no realtime WS). Health via
  `GET {base}/api/v1/models/stt`.
- Both registered `PrivacyClass.LOCAL` by default (a 9Router box normally
  listens on `127.0.0.1:20128` on the operator's machine) so they stay
  eligible under `privacy_required`; `privacy_class: cloud` is an explicit
  opt-out, an unrecognised value warns and falls back to LOCAL.
- `ai/stt/speechmatics.py` realtime rewritten to the current v2 protocol:
  `StartRecognition` → `RecognitionStarted` → binary `AddAudio` →
  `AddPartialTranscript`/`AddTranscript` → `EndOfStream{last_seq_no}` →
  `EndOfTranscript`; `transcription_config` carries `language`, `max_delay`
  (0.7–4), `enable_partials`, optional `domain`/`diarization`/
  `additional_vocab`; regions `global`/`eu`/`us`/`au` plus `eu1`/`us1`/`au1`
  and `eu2`/`us2` aliases, `max_delay` clamped into the documented [0.7, 4].
  Two real bugs fixed on the way: a literal `"None"` leaking into the domain
  field, and `attaches_to` glue breaking "both" and hyphenated words.
  Close-code mapping: 4001/4003 → `ProviderError`; 1011/4005/4013 →
  `ProviderUnavailableError` (retryable, handshake-retry friendly).
- Live catalog discovery: `ProviderDescriptor.supports_model_discovery`,
  `GET /api/v1/providers/{name}/models?kind=llm|stt`, `ProviderModelCatalog` /
  `ProviderModelInfo` schemas. Loud failures (404/501/409/502/504) instead of
  a silently blank dropdown; never echoes config beyond the model id.
- Config + `.env.example`: `MS_LLM__NINE_ROUTER__*`, `MS_STT__NINE_ROUTER__*`
  and Speechmatics realtime knobs.
- **WPF client (explicit UI, secret-free by design)**: `PreferredSttProvider`
  in `AppSettings` → sent as `session.start.provider`; `session.started`'s
  provider is surfaced in the status note so the clinician sees what the
  *server* actually chose; AI Settings gained an STT-provider picker
  (configured + streaming providers only, a saved-but-unavailable preference
  falls back with a warning and is left intact rather than silently
  overwritten) and a **9Router** card that lists the live LLM/STT catalogs.
  `GetProviderModelsAsync` on `IApiClient`/`ApiClient`; DTOs in
  `ServerModels.cs`.
- Tests: `tests/test_nine_router.py`, rewritten `tests/test_stt_speechmatics.py`
  (documented protocol), `tests/test_providers_route.py` (catalog endpoint
  contract); client-side `AiSettingsViewModelTests.cs` (~17) plus
  started-frame assertions in `DictationProjectionTests.cs`. Backend suite
  green, `ruff` clean.
- Done-notes (honesty ledger):
  - **No adapter was exercised against a live service.** 9Router was not
    installed and no Speechmatics account was used — both are implemented from
    the projects' published API shapes and pinned by network-free fixture
    tests (`ScriptedTransport`/`MockTransport`). A first real-world run may
    still surface wire-level surprises (exact `verbose_json` shape per
    upstream, 9Router auth header preference).
  - **The Speechmatics *batch* job API was deliberately left untouched** in
    this pass, per scope — it remains on its older shape and is now the known
    gap in that adapter.
  - **The WPF client was not compiled in this sandbox**: no .NET SDK is
    installed and the `dotnet-install.sh` download is blocked, so CI
    (`client-linux` compile gate + `client-windows` build/xunit) is the
    authority on these C#/XAML changes. They were reviewed and cross-checked
    by hand (interface-member parity, XAML well-formedness, every `{Binding}`
    root resolving to a ViewModel member) but not built locally.
  - 9Router's STT `list_models()` requires the per-kind route
    `GET /api/v1/models/stt`. There is **no** fallback that filters
    `/api/v1/models` — a build without that route yields a loud `ProviderError`
    (surfaced as `502` from the catalog endpoint) instead of a plausible-looking
    but wrong LLM-only list.

## Standing engineering constraints (all phases)

- WPF never talks to providers; no secrets in client; REST/WS protocol v1
  additive-only until a v2 bump is justified.
- Provider logic lives in exactly one adapter module; registration in one
  place (`ai/registry`).
- New behavior arrives with tests; medical-safety pack is a merge gate.
- Reference repos remain read-only inspiration; attribution stays in
  docs/THIRD_PARTY_NOTICES.md.
