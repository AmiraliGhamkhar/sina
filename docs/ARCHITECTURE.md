# MedicalScribe Architecture

Status: Phase 1 implemented · Target: production-oriented medical STT +
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

**Planned providers** (registered in `build_default_registry`, one register +
one module each): STT `whisper-local` (whisper-server HTTP + windowed stream,
pattern noted in docs/ASSESSMENT.md §2), `qwen-asr`, `speechmatics`
(WS + context for fa), `deepgram` (WS + keyword boost: drug names, laterality
words); LLM `llama-server` (shipped, §12 external-service rule), `openai`,
`anthropic`, `gemini`, plus the two mocks (already shipped) that keep the full
pipeline CI-testable without keys.

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
  → edits (clinician) / voice commands (parser, P6)
  → POST report draft: LLMProvider via prompt built from
    transcript + template + context ONLY (grounding, §7)
  → validation pass (numbers/units/laterality/negation/dates/IDs, P6)
    → warnings frames → clinician review
  → explicit finalize → explicit approve → audit + immutable version  [P6/P7]
```

AI output is *draft material* by construction: the Report state machine
(`draft → finalized → approved`) only advances via explicit authenticated
user actions; there is no code path that writes an approved report without a
recorded clinician identity.

## 6. Medical safety model (spec §8/§9)

- **Prompt contract** (built server-side): the model receives (a) the final
  transcript, (b) structured patient context, (c) the template — and is
  instructed that missing information must be emitted as
  `null`/`""`/`"missing"`, never invented. Temperature fixed low; JSON mode +
  repair (Phlox concept) for strict section output.
- **Post-generation validation service** (`api/services/validation.py`, P6):
  extracts numeric+dose+unit tokens, laterality words, negation scopes,
  dates, identifiers from the transcript, and checks each survives in the
  draft *unchanged*; mismatches or dropped negatives become
  `VALIDATION_WARNING` records attached to the draft (and `warning` WS frames
  when produced interactively). Validation compares transcript→draft; it
  never rewrites text automatically.
- **Terminology normalization** is a bounded dictionary step (Persian ↔
  English medical lexicon, e.g. "ام ار آی" → "MRI") applied to *transcript
  display/analytics*, never to stored dictation truth.
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

## 8. AuthN/AuthZ

- JWT access (short TTL) + opaque-stored refresh rotation (Phase 7), argon2
  password hashing, `Role`-based authorization with policies: clinicians
  read/write own encounters; auditors read-only; admins manage providers.
- WS auth = same JWT (query param `token` for browsers, Authorization header
  for WPF) validated before protocol handshake; dev static token is
  non-production only.
- Provider secrets: `ai_providers.key_encrypted` (Fernet key from
  `MS_SECURITY__KEY_ENCRYPTION_KEY`, MMG concept) — masked as `****last4` in
  every response (registry `description` fields carry no values).

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
