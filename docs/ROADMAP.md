# Roadmap — remaining phases with acceptance criteria

Each phase: implement → tests green (`pytest`, new client tests) → build →
docs updated → phase note appended to README table. Do not delete working
functionality to simplify (spec §19.13).

## Phase 2 — Live capture & transcript (next)
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

## Phase 3 — STT adapters
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

## Phase 4 — LLM adapters & report prompts
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

## Phase 5 — Router hardening
- HealthTracker fed by real task outcomes (not just probes); Redis-backed
  shared health + queue depth; latency EWMA per provider.
- Runtime fallback: on `ProviderError(retryable)` → next in
  `RouteDecision.fallbacks`, transparent to session (one `PROVIDER_FALLBACK`
  warning frame); no-fallback path for batch tasks returns 502 with tried list.
- Cost budget guard (per-day token budget, soft-stop → local-only).
- Acceptance: fault-injection tests prove fallback never crosses the privacy
  wall; health demotion observable via stats endpoint.

## Phase 6 — Commands, templates, validation
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
