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

## Phase 6 — Commands, templates, validation (next)
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
- Acceptance: medical-safety test pack (spec §18) green; UI shows warnings
  blocking approve until acknowledged (with recorded justification).

## Phase 7 — Persistence, auth, Redis
- SQLAlchemy models (all §13 entities) + Alembic baseline migration + seed
  script (roles, built-in templates, provider rows from env).
- Auth: argon2 hashing, login/refresh (rotation + revocation), roles/policies,
  WS token = access JWT (short TTL), lockout policy; rate limiting via Redis
  (degrade-open + auth-bucket, MMG shape) — with per-WS connection caps.
- AuditLog table (append-only, deny-listed payload) + admin query API;
  transcripts/segments/reports persistence, revisions table (clinician edits),
  audio policy: discard after finalize unless `MS_AUDIO__RETAIN_HOURS`.
- Acceptance: postgres/redis compose profile integration tests (testcontainers
  or compose-run CI), migration up/down round-trip, auth e2e incl. expired/
  revoked token paths.

## Phase 8 — Hardening & release
- Prometheus `/metrics` (+optional OTel traces), Grafana dashboard shipped in
  infrastructure; structured error budgets.
- Load test: 20 concurrent WS sessions on 4 GB VM w/ local llama-server —
  p50/p95 transcript latency recorded; soak 2 h.
- Windows installer: MSIX + signing, auto-update policy, first-run wizard
  (server URL, CA pin, mic test, hotkey); crash-telemetry with no payload
  capture.
- Security pass: dependency audit, CSP-ish hardening N/A (desktop) but TLS
  pinning review, secret-scan in CI, pen-test checklist for gateway.
- Acceptance: release checklist in docs; all spec §19 engineering rules
  re-verified; regulatory-boundary language reviewed (assistive-only).

## Standing engineering constraints (all phases)

- WPF never talks to providers; no secrets in client; REST/WS protocol v1
  additive-only until a v2 bump is justified.
- Provider logic lives in exactly one adapter module; registration in one
  place (`ai/registry`).
- New behavior arrives with tests; medical-safety pack is a merge gate.
- Reference repos remain read-only inspiration; attribution stays in
  docs/THIRD_PARTY_NOTICES.md.
