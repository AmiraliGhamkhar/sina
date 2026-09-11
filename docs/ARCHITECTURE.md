# MedicalScribe Architecture

Status: Phase 8 complete (all 8 phases implemented).
Target: production-oriented medical STT + clinical documentation platform.

> `spec §N` refers to the external target-stack specification (not in this
> repository). Section numbers are kept for traceability.

## 1. System shape

```
┌──────────────────────────────┐        ┌─────────────────────────────────────────────┐
│ WPF client (Windows)         │  HTTPS │ FastAPI backend  (backend/api)              │
│  Views ↔ ViewModels (MVVM)   │◄──────►│  routes → services → repositories → DB      │
│  Audio capture (NAudio)      │   WS   │  /ws/v1/transcribe (protocol v1)            │
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
3. Secrets (`SecretStr` settings, Fernet-encrypted provider rows) are read
   only inside provider factories in `ai/`. API responses expose names,
   capabilities, configured booleans, and probe results — never config values.
4. Persistence: PostgreSQL via SQLAlchemy. `ai/` has no DB access;
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
| WPF `Services` | `Infrastructure.ApiClient`, models | business rules that belong to the backend (validation, routing) |

## 3. Provider contracts (`ai/base.py`)

- `STTProvider`: `transcribe(STTRequest) -> list[TranscriptSegment]` and
  `stream(AsyncIterator[bytes]) -> AsyncIterator[TranscriptSegment]`
  (interim ⇒ `is_final=False`; one final per utterance).
- `LLMProvider`: `complete(messages, ..., json_mode)` + `stream` token deltas;
  usage metadata surfaces for cost/observability.
- `BaseProvider.capabilities` is **static metadata** (privacy class, streaming,
  languages, latency/cost hints) so routing/validation never instantiates
  providers. `health()` is cheap, separate from task execution.
- Errors: `ProviderError(retryable)` / `ProviderUnavailableError` — the
  router/health tracker consume the flags, never message strings.

**Registry membership** (one register + one module each).

STT (all shipped):

| Provider | Implementation |
|---|---|
| `mock` | scripted stream; keeps the full pipeline CI-testable without keys |
| `whisper-local` | whisper-server HTTP + VAD-windowed pseudo-stream |
| `qwen-asr` | OpenAI-audio-compatible service |
| `shenava` | in-process Persian STT (sherpa-onnx, `local-ai` extra) |
| `speechmatics` | WS + batch job API, fa configured |
| `deepgram` | WS + prerecorded, keyword boost for drug names/laterality words |

Cloud WS traffic goes through the `ai/stt/ws_transport.py::WsTransport` seam —
tests run the exact protocol against scripted transports (zero network).

LLM: `llama-server` (external service per spec §12, with `/props` capability
discovery), `openai` (shared OpenAI-compat transport), `anthropic` (native
Messages wire), `gemini` (generateContent + SSE), `mock`. All httpx only, no
SDKs; usage is metered into the shared counters.

PHI scrubbing guards every cloud LLM call (setting
`MS_LLM__CLOUD__REDACT_PHI_FOR_CLOUD`, default on; regex + optional NER via
`backend/api/services/pii_ner.py::RedactionService`). The privacy wall (below)
remains the actual guarantee.

## 4. AI routing (`ai/router`)

`route(RouteRequest, candidates) -> RouteDecision` (`ai/router/router.py`) is
a pure function. Precedence, in order:

1. **Privacy wall** — `privacy_required=True` (encounter flag or
   `MS_ROUTING__PRIVACY_DEFAULT`) filters out *every* CLOUD candidate before
   anything else. No mode, cost, or explicit pick can defeat it; a violating
   preference surfaces as an audit-logged downgrade note.
2. Explicit `preferred` provider if eligible (`strict_preference` → error).
3. Mode policy (LOCAL/CLOUD/HYBRID/AUTO) over the remaining set.
4. Ranking: healthy first (from `HealthTracker` — threshold + half-open
   cooldown; live latency EWMA + Redis queue depth), then latency hint, then
   cost hint, then name (stability).

`RouteDecision{provider, fallbacks, reason, privacy_override_applied,
considered}` is recorded verbatim in the audit log on every session start
(spec §11: "record the selected provider for auditing").

Runtime fallback: a retryable `ProviderError` mid-session transparently
switches to the next name in `RouteDecision.fallbacks` (one
`PROVIDER_FALLBACK` warning per switch; session identity untouched). The chain
is privacy-filtered at route time, so fallback can never cross the wall.
Kill switch: `MS_ROUTING__FALLBACK_ENABLED=false`.

## 5. Data flow (dictation session)

```
mic (NAudio WASAPI, 16k mono PCM16)
  → WS binary frames / audio.chunk                     → api/routes/ws
  → transcription_hub queue → ai_bridge route() → STTProvider.stream()
  → transcript.interim/final frames                    → live transcript view
  → final segments in TranscriptStore (in-mem LRU)
  → Transcript/Segment tables (durable write-through)
  → final segment → voice-command parser (whole-utterance exact/anchored
    match; ambiguous triggers stay in the text + COMMAND_AMBIGUOUS)
  → effects: paragraph/section markers, delete-last-sentence (journaled
    undo), repeat, voice pause/resume  → TranscriptStore
  → edits (clinician) — revision-tracked
  → POST /reports/{enc}/draft: LLMProvider via prompt built from the
    marker-aware, terminology-normalized session view (or inline text) +
    template + context ONLY (grounding, §6)
  → full validation pass (doses/units/laterality/negation/dates/
    identifiers/anatomy; bilingual; Jalali↔Gregorian)
    → severity-tagged warnings on the stored draft → clinician review
  → acknowledge (recorded justification) → explicit finalize → explicit
    approve → audit + immutable version → amendments are linked drafts
```

AI output is *draft material* by construction: the Report state machine
(`draft → finalized → approved` in `api/services/report_store.py`) only
advances via explicit actions; finalize/approve are blocked while critical
validation warnings are unacknowledged, and approved reports are immutable —
corrections start amendment drafts linked to the approved version. There is no
code path that writes an approved report without a recorded user action.

## 6. Medical safety model (spec §8/§9)

- **Prompt contract** (built server-side, `api/services/note_prompt.py`): the
  model receives (a) the final transcript, (b) structured patient context,
  (c) the template — and is instructed that missing information must be
  emitted as `null`/`""`/`"missing"`, never invented. Temperature fixed low;
  JSON mode + one repair round-trip for strict section output.
- **Post-generation validation** (`api/services/validation.py`): extracts
  unit-aware quantities (۴۰ میلی‌گرم == 40 mg, cc≈ml), dose near-misses with
  drug proximity (10→100 mg = critical), laterality pairs with swap
  detection, term-level negation scopes (Persian + English cues, cut at
  contrast conjunctions), dates (Gregorian + Jalali with conversion + month
  names), identifiers (phone/national-id/MRN), and anatomy grounding — from
  the transcript AND its terminology-normalized view (synonym union, never a
  fabrication source). Mismatches become severity-tagged warnings attached to
  the stored draft; the client surfaces them and the lifecycle gates on
  unacknowledged criticals. Validation compares transcript→draft; it never
  rewrites text automatically.
- **Terminology normalization** (`api/services/terminology*.py`): a bounded,
  engine-guarded dictionary step (Persian ↔ English medical lexicon, e.g.
  "ام ار آی" → "MRI"): reversible substitutions, whole-term matching, and the
  engine refuses catalog entries containing digits, dose units, negation or
  laterality words. Applied to the *derived* prompt/validation view, never to
  stored dictation truth.
- **Voice commands** (`api/services/voice_commands/`): commands are
  distinguishable from clinical speech by whole-utterance matching; a trigger
  inside longer speech is ambiguity (kept + warned), never a deletion. The
  catalog is data (manifest re-exports it); effects are journaled and
  reversible via `undo_last`; voice-pause keeps audio flowing so the resume
  command stays audible.
- **Report templates** (`api/services/templates.py`): sections are server
  data (built-ins seeded, custom via CRUD/fork); the WPF UI renders whatever
  the server returns. LLM extraction proposes only — persistence is an
  explicit clinician action.
- **Language policy:** fa, en, fa-en mixed; providers must keep embedded
  English medical terms verbatim (mock corpus + tests encode this).

## 7. Persistence model

14 tables (asyncpg/SQLAlchemy 2.0, Alembic migrations —
`backend/api/models/orm.py`, `backend/alembic/`):

| Table | Contents |
|---|---|
| `roles` | admin / clinician / auditor |
| `users` | argon2id password hashes, role, active flag |
| `refresh_tokens` | SHA-256 hashes, single-use, rotated on use |
| `patients` | clinical entities (id `pat_…`) |
| `encounters` | flagged private, links patient↔episodes (id `enc_…`) |
| `transcripts` | one row per WS session |
| `transcript_segments` | immutable originals + editable current text |
| `reports` | draft/finalized/approved, versioned |
| `report_revisions` | event log (draft_generated, finalized, approved, amended, acknowledged, edited) |
| `report_templates` | JSON-schema sections, data-driven; built-ins + custom |
| `ai_providers` | name, kind, base_url, Fernet-encrypted secret, capabilities, enabled |
| `ai_requests` | task, provider selected, mode, latency, tokens, outcome, encounter FK |
| `audit_log` | append-only audit rows |
| `settings` | per-user/server KV |

Rules:

- No audio or model blobs in Postgres. Captured audio is not persisted by
  default (discard after finalize); `models/` holds model files referenced by
  path config only.
- Redis holds rate-limit buckets, queues, transient session state, and
  provider health (shared across workers). Anything durable is
  Postgres-first; Redis is never the source of truth.

## 8. AuthN/AuthZ

- JWT access (default 30 min, `MS_AUTH__ACCESS_TTL_MINUTES`) + **one-time**
  refresh tokens (default 14 d, `MS_AUTH__REFRESH_TTL_DAYS`): hash-stored in
  `refresh_tokens`, consumed atomically on rotation; presenting a consumed
  token again (theft assumption) revokes ALL of that user's sessions.
- Argon2id password hashing with a dummy-hash timing equalizer for unknown
  users; per-username lockout (5 failures → 300 s default).
- Role-based authorization: admin routes require role `admin` (403
  otherwise); clinicians read/write clinical data; auditors read-only.
- WS auth = same access JWT (query param `token` or Authorization header),
  validated before the protocol handshake; per-user concurrent session cap
  (`MS_RATE_LIMIT__WS_SESSIONS_PER_USER`, default 5) enforced before provider
  work begins. The dev static token is non-production only.
- Provider secrets: Fernet-encrypted at rest (`ai_providers.secret_ciphertext`,
  key from `MS_SECURITY__SECRET_ENCRYPTION_KEY`) — stored via the admin API,
  decrypted in exactly ONE place
  (`api/services/ai_bridge.py::provider_config_with_secrets`, right before a
  provider factory consumes the key), and never returned by any endpoint,
  query, or log line.

## 9. Observability

- Structured JSON logs (uvicorn access log suppressed in favor of our own):
  route templates only, **no query strings**, never transcript bodies.
- In-process `Metrics` counters (`backend/api/telemetry.py`) exposed as
  Prometheus text at `GET /metrics` (series prefixed `medicalscribe_*`;
  `http_requests_total{status}`, `http_request_duration_seconds{route,quantile}`
  p50/p95/p99, WS/STT/LLM counters, `provider_healthy{provider}`, cost-budget
  gauges, `db_mode`, `build_info`). Never contains transcripts, usernames, or
  secrets. Optional OTel tracing: `observability` extra +
  `MS_OBSERVABILITY__OTEL_ENABLED`.
- Every AI request is written to `ai_requests` so latency/cost/provider
  audits are queryable, not just observable.

## 10. WebSocket protocol

Versioned envelope (v1), strict inbound validation, full state machine
implemented. Normative spec: `docs/WEBSOCKET_PROTOCOL.md`; schemas:
`backend/api/schemas/ws.py`; conformance tests: `tests/test_ws_protocol.py`.

## 11. Deliberate deviations from the references

- No Electron/Node artifacts, no clipboard auto-insert, no local model files
  on the client, no bundled llama.cpp management — per spec §2/§12 and the
  medical-safety posture (docs/ASSESSMENT.md §3).
- SpeakType's "record → local whisper → paste" loop is replaced end-to-end,
  while its audio/hotkey/settings engineering is kept.
- Open Medical Scribe's privacy=redaction-for-cloud stance is *tightened* to
  privacy=local-routing with optional redaction on top.
