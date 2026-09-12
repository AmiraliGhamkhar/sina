# MedicalScribe Architecture

Status: Phase 7 implemented · Target: production-oriented medical STT +
clinical documentation platform.

## 1. System shape

```
┌──────────────────────────────┐        ┌─────────────────────────────────────────────┐
│ WPF client (Windows)         │  HTTPS │ FastAPI backend  (backend/api)              │
│  Views ↔ ViewModels (MVVM)   │◄──────►│  routes → services → repositories → DB      │
│  Audio capture (NAudio, P2)  │   WS   │  /ws/v1/transcribe (protocol v1)            │
│  Hotkeys, settings, theme    │        │  audit sink, metrics, manifest for clients  │
│  NO provider SDKs, NO SECRETS│        └───────────────┬─────────────────────────────┘
└──────────────────────────────┘                        │ ai_bridge
                                              ┌─────────▼───────────┐
                                              │ AI layer (ai/)      │
                                              │ registry → router   │
                                              │ stt/ llm/ adapters  │
                                              └───┬───────────┬─────┘
                                            HTTP/WS         OpenAI-compat
                                       ┌──────────▼──┐  ┌────▼─────────┐
                                       │ STT: whisper │  │ llama-server │  (external service)
                                       │ qwen, cloud  │  │ cloud LLMs   │
                                       └──────────────┘  └──────────────┘
                       PostgreSQL (source of truth) · Redis (queues, rate
                       limits, shared session/health state — never truth)
                       Nginx: TLS + WS upgrade · Prometheus/OTel: metrics
```

**Hard boundaries**

1. WPF ⇄ FastAPI only (REST for CRUD, WS for real-time audio/transcript). The
   client never resolves a provider, never holds an API key, and renders zero
   AI policy from constants — everything policy-shaped comes from
   `GET /api/v1/config/manifest`.
2. `backend/api` never imports provider SDKs directly; it goes through
   `ai/registry` + `ai/router` (`api/services/ai_bridge.py` is the single
   bridge module).
3. Secrets (`SecretStr` settings, Phase 7 encrypted provider rows) are read
   only inside `ai/*/provider` factories. API responses expose names,
   capabilities, configured-bool and probe results — never config values.
4. Persistence: PostgreSQL via SQLAlchemy (Phase 7). `ai/` has no DB access;
   repositories are the only modules holding sessions.

## 2. Layering rules

| Layer | May import | Must not |
|---|---|---|
| `api/routes` | schemas, services, deps | provider code, SQLAlchemy sessions |
| `api/services` | repositories, `ai.*` | HTTP types (except WS bridge dtos), fastapi |
| `api/repositories` | `api.models`, `api.db` | routes, providers |
| `ai/router` | `ai.base`, `ai.registry` descriptors | FastAPI, DB, network |
| `ai/stt`, `ai/llm` | `ai.base`, httpx | `api` package, each other |
| WPF `ViewModels` | `Services`, `Models` | `Views`, raw HTTP, provider URLs |
| WPF `Services` | `Infrastructure.ApiClient`, models | business rules that belong to backend (validation, routing) |

## 3. Provider contracts (`ai/base.py`)

- `STTProvider`: `transcribe(STTRequest) -> list[TranscriptSegment]` and
  `stream(AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]`
  (interim ⇒ `is_final=False`; one final per utterance).
- `LLMProvider`: `complete(messages, ..., json_mode)` + `stream` token deltas;
  usage metadata surfaces for cost/observability.
- `BaseProvider.capabilities` is **static metadata** (privacy class,
  streaming, languages, latency/cost hints) so routing/validation never
  instantiates providers. `health()` is cheap, separate from task execution.
- Errors: `ProviderError(retryable)` / `ProviderUnavailableError` — the
  router/health tracker consume the flags, never message strings.

**Registry membership** (one register + one module each). STT — all shipped:
`whisper-local` (whisper-server HTTP + VAD-windowed pseudo-stream, pattern
noted in docs/ASSESSMENT.md §2), `qwen-asr` (OpenAI-audio-compatible service),
`shenava` (in-process Persian ASR, `local-ai` extra),
`speechmatics` (realtime WS v2 + batch job API, fa configured), `deepgram` (WS +
prerecorded, keyword boost for drug names/laterality words) and
`9router` (Whisper-compatible `POST /api/v1/audio/transcriptions` against a
self-hosted proxy, pseudo-streamed through `VadSegmenter`). Cloud WS traffic
goes through the `ai/stt/ws_transport.WsTransport` seam — tests run the exact
protocol against scripted transports (zero network). LLM — `llama-server`
(shipped + `/props` capability discovery, §12 external-service rule), and
Phase 4 shipped `openai` (shared openai-compat transport), `anthropic`
(native Messages wire) and `gemini` (generateContent + SSE) — httpx only, no
SDKs, usage metered into the shared counters. Phase 9 added `9router`
(OpenAI-compatible + Anthropic `messages` wire, shared plumbing in
`ai/nine_router_client.py`). PHI scrub
(`llm.cloud.redact_phi_for_cloud`) guards every cloud prompt; the privacy
wall remains the actual guarantee. The two mocks keep the full pipeline
CI-testable without keys.

**9Router privacy class.** A 9Router instance normally listens on
`127.0.0.1:20128` on the operator's own machine, so both adapters classify it
`PrivacyClass.LOCAL` by default and it is eligible for `privacy_required`
encounters. `privacy_class: cloud` in config is an explicit opt-out for a
deployment that relays to cloud accounts; an unrecognised value logs a warning
and falls back to LOCAL (fail-safe, never fail-open).

**Dynamic catalogs.** `ProviderDescriptor.supports_model_discovery` marks
providers whose model list is not known at build time (9Router fronting 40+
upstreams). `GET /api/v1/providers/{name}/models?kind=llm|stt` calls the
adapter's `list_models()` under a 15 s timeout and returns `ProviderModelInfo`
rows — `id`, `owned_by`, `context_length`, `max_completion_tokens`, router
capability flags — plus `configured_model`, the id the server is currently set
to use. A model id is not a secret; nothing else from config is ever echoed.
It fails loudly instead of returning a blank list: `501` when the provider has
no catalog at all, `409` when the provider exists but is not configured
server-side (naming the env var to set), `502`/`504` when the upstream is
unreachable or slow. A descriptor that advertises discovery while its adapter
lacks `list_models()` is reported as `501` too — our bug, not the operator's.

## 4. AI routing (`ai/router`)

`route(RouteRequest, candidates) -> RouteDecision` is a pure function.
Precedence, in order:

1. **Privacy wall** — `privacy_required=True` (encounter flag or
   `MS_ROUTING__PRIVACY_DEFAULT`) filters out *every* CLOUD candidate before
   anything else. No mode, cost, or explicit pick can defeat it; violating
   preference surfaces as an audit-logged downgrade note.
2. Explicit `preferred` provider if eligible (`strict_preference` → error).
3. Mode policy (LOCAL/CLOUD/HYBRID/AUTO) over the remaining set.
4. Ranking: healthy first (from `HealthTracker`, threshold+half-open cooldown;
   Phase 5 adds live latency + Redis queue depth), then latency hint, then
   cost hint, then name (stability).

`RouteDecision{provider, fallbacks, reason, privacy_override_applied,
considered}` is recorded verbatim in the audit log on every session start —
spec §11 "record the selected provider for auditing".

## 5. Data flow (dictation session)

```
mic (NAudio WASAPI, 16k mono PCM16)                    [done P2]
  → WS binary frames / audio.chunk                     → api/routes/ws
  → transcription_hub queue → ai_bridge route() → STTProvider.stream()
  → transcript.interim/final frames                    → live transcript view
  → final segments in TranscriptStore (in-mem LRU)     [done P2]
  → Transcript/Segment tables                          [P7]
  → final segment → voice-command parser (whole-utterance exact/anchored
    match; ambiguous triggers stay in the text + COMMAND_AMBIGUOUS) [done P6]
  → effects: paragraph/section markers, delete-last-sentence (journaled
    undo), repeat, voice pause/resume  → TranscriptStore [done P6]
  → edits (clinician) — revision-tracked                                [done P2]
  → POST /reports/{enc}/draft: LLMProvider via prompt built from the
    marker-aware, terminology-normalized session view (or inline text) +
    template + context ONLY (grounding, §7)                            [done P4/P6]
  → full validation pass (doses/units/laterality/negation/dates/
    identifiers/anatomy; bilingual; Jalali↔Gregorian)                  [done P6]
    → severity-tagged warnings on the stored draft → clinician review
  → acknowledge (recorded justification) → explicit finalize → explicit
    approve → audit + immutable version → amendments are linked drafts
                                                                        [done P6; P7 persists]
```

AI output is *draft material* by construction: the Report state machine
(`draft → finalized → approved` in `api/services/report_store.py`) only
advances via explicit actions; finalize/approve are blocked while critical
validation warnings are unacknowledged, and approved reports are immutable —
corrections start amendment drafts linked to the approved version. There is
no code path that writes an approved report without a recorded user action.

## 6. Medical safety model (spec §8/§9)

- **Prompt contract** (built server-side): the model receives (a) the final
  transcript, (b) structured patient context, (c) the template — and is
  instructed that missing information must be emitted as
  `null`/`""`/`"missing"`, never invented. Temperature fixed low; JSON mode +
  repair (Phlox concept) for strict section output.
- **Post-generation validation service** (`api/services/validation.py`, done
  P6): extracts unit-aware quantities (۴۰ میلی‌گرم == 40 mg, cc≈ml), dose
  near-misses with drug proximity (10→100 mg = critical), laterality pairs
  with swap detection, term-level negation scopes (بدون/عدم/ندارد/نمی/منفی +
  English cues, cut at contrast conjunctions), dates (Gregorian + Jalali with
  conversion + month names), identifiers (phone/national-id/MRN) and anatomy
  grounding — from the transcript AND its terminology-normalized view
  (synonym union, never a fabrication source). Mismatches become
  severity-tagged warnings attached to the stored draft; the client surfaces
  them and the lifecycle gates on unacknowledged criticals. Validation
  compares transcript→draft; it never rewrites text automatically.
- **Terminology normalization** (`api/services/terminology*.py`, done P6) is
  a bounded, engine-guarded dictionary step (Persian ↔ English medical
  lexicon, e.g. "ام ار آی" → "MRI"): reversible substitutions, whole-term
  matching, and the engine refuses catalog entries containing digits, dose
  units, negation or laterality words — applied to the *derived* prompt/
  validation view, never to stored dictation truth.
- **Voice commands** (`api/services/voice_commands/`, done P6): commands are
  distinguishable from clinical speech by whole-utterance matching; a trigger
  inside longer speech is ambiguity (kept + warned), never a deletion. The
  catalog is data (manifest re-exports it); effects are journaled and
  reversible via `undo_last`; voice-pause keeps audio flowing so the resume
  command stays audible.
- **Report templates** (`api/services/templates.py`, done P6): sections are
  server data (built-ins seeded, custom via CRUD/fork); the WPF UI renders
  whatever the server returns. LLM extraction (Phlox concept) proposes only —
  persistence is an explicit clinician action.
- Language policy: fa, en, fa-en mixed; providers must keep embedded English
  medical terms verbatim (mock corpus + tests encode this expectation).

## 7. Persistence model (Phase 7 blueprint)

Tables (asyncpg/SQLAlchemy 2.0, Alembic migrations):
`users`, `roles`, `patients`, `encounters` (flagged private, links
patient↔episodes), `transcripts`, `transcript_segments` (immutable originals +
editable current text + revision rows), `reports` (draft/finalized/approved,
versioned), `report_sections`, `report_templates` (JSON schema sections —
data-driven), `ai_providers` (name, kind, base_url, encrypted key,
capabilities, enabled, default), `ai_requests` (task, provider selected,
mode, latency, tokens, outcome, encounter FK), `audit_logs` (append-only),
`settings` (per-user/server KV).

Rules: no audio/model blobs in Postgres — captured audio (if retained at all
— default is discard-after-finalize) goes to object storage/`storage/` path
with lifecycle policy; `models/` holds GGUF/model files referenced by path
config only. Redis holds rate-limit buckets, queues, transient session state
and provider health (shared across workers); anything durable is
Postgres-first, Redis never the source of truth.

## 8. AuthN/AuthZ (implemented in Phase 7)

- JWT access (30 min default) + **one-time** refresh tokens (14 d): hash-stored
  in `refresh_tokens`, consumed atomically on rotation; presenting a consumed
  token again (theft assumption) revokes ALL of that user's sessions. Argon2id
  password hashing with a dummy-hash timing equalizer for unknown users;
  per-username lockout (5 failures → 300 s). `Role`-based authorization:
  admin routes require role `admin` (403 otherwise); clinicians read/write
  clinical data; auditors read-only.
- WS auth = same access JWT (query param `token`, Authorization header)
  validated before the protocol handshake; per-user concurrent session cap
  (`MS_RATE_LIMIT__WS_SESSIONS_PER_USER`, default 5) enforced before provider
  work begins. Dev static token is non-production only.
- Provider secrets: Fernet-encrypted at rest
  (`ai_providers.secret_ciphertext`, key from
  `MS_SECURITY__SECRET_ENCRYPTION_KEY`) — stored via the admin API, decrypted
  in exactly ONE place (`ai_bridge.provider_config_with_secrets`, right
  before a provider factory consumes the key), and never returned by any
  endpoint, query, or log line.

## 9. Observability

- Structured JSON logs (uvicorn access log suppressed in favor of our own
  with **route templates, no query strings**, never transcript bodies).
- In-process `Metrics` counters (`api/telemetry.py`) → Prometheus text
  endpoint in Phase 8: `http_requests_total{route,status}`,
  `ws_connections_active`, `stt_latency_ms`, `llm_latency_ms`,
  `provider_errors_total{provider}`, `provider_queue_depth`,
  `tokens_total{provider,model}`, `transcription_audio_seconds_total`,
  and (P2) `ws_audio_frames`, `stt_segments_final`, `stt_first_segment_ms`,
  `stt_streams_started/stopped`, `stt_audio_dropped_bytes_frames`.
- Every AI request is written to `ai_requests` (P7) so latency/cost/provider
  audits are queryable, not just observable.

## 10. WebSocket protocol

Versioned envelope (v1), strict inbound validation, control-plane implemented
in Phase 1, audio pipeline + transcript store in Phase 2. Normative spec:
`docs/WEBSOCKET_PROTOCOL.md`; schemas: `backend/api/schemas/ws.py`;
conformance tests: `tests/test_ws_protocol.py`.

## 11. Deliberate deviations from the references

- No Electron/Node artifacts, no clipboard auto-insert, no local model files
  on the client, no bundled llama.cpp management — per spec §2/§12 and
  medical-safety posture (see assessment §3 table).
- SpeakType's "record → local whisper → paste" loop is replaced end-to-end,
  while its audio/hotkey/settings engineering is kept.
- Open Medical Scribe's privacy=redaction-for-cloud stance is *tightened* to
  privacy=local-routing with optional redaction on top.
