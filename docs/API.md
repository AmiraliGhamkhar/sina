# REST API — v1 (Phase 1 surface)

Base: `https://<gateway>/api/v1` (dev: `http://localhost:8000/api/v1`).
Auth: `Authorization: Bearer <jwt>` — required where noted; dev builds accept
`MS_AUTH__DEV_TOKEN`.

Error envelope (all non-2xx):

```json
{ "error": { "code": "STABLE_CODE", "message": "human text", "details": null } }
```

Stable codes live in `backend/api/errors.py::ErrorCode`. Validation failures
(422) report field `loc`s and rule types, **never submitted values** (patient
data must not echo back through error paths).

## Public (no auth)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness: `{status, version, phase}` |
| GET | `/health/ready` | readiness: real engine ping for Postgres once Phase 7 owns the engine (TCP probe fallback), TCP for Redis; unconfigured ⇒ `disabled`, not blocking |
| GET | `/api/v1/version` | `{name, api_version, ws_protocol, ws_protocol_min, phase}` |
| GET | `/api/v1/config/manifest` | **client policy manifest**: ws path/protocol, audio format, languages (fa/en/fa-en), feature flags, voice-command catalog, routing modes |

## Auth

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — (rate-limited) | `{username, password, device_name?}` → `{access_token, refresh_token, token_type, expires_in}`. **Phase 7:** argon2id verification against the users table; 5 consecutive failures lock the account for `MS_AUTH__LOCKOUT_SECONDS` (429 with retry hint); requires `MS_DATABASE__URL` (else `501 AUTH_NOT_IMPLEMENTED`). Dev-token logins (`dev` / `MS_AUTH__DEV_TOKEN`) still mint a real JWT in non-prod. |
| POST | `/api/v1/auth/refresh` | — (rate-limited) | `{refresh_token}` → new pair. **One-time tokens**: the presented token is consumed; presenting a consumed token again (theft assumption) revokes ALL of that user's refresh tokens and returns 401. |
| POST | `/api/v1/auth/logout` | JWT | optional body `{refresh_token?}` — the presented refresh token is revoked. |
| GET | `/api/v1/auth/me` | JWT | `{user_id, role, is_dev}` |

## Patients & encounters (Phase 7 — persistence-backed)

DB-backed clinical entities; without `MS_DATABASE__URL` these answer
`501 DB_NOT_CONFIGURED` (honest capability boundary, not silent degradation).

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/patients` | JWT | `{full_name, mrn?, date_of_birth?, sex?, notes?}` → patient row (id `pat_…`). Audited without content. |
| GET | `/api/v1/patients?q=…&limit=` | JWT | case-insensitive partial match on name/MRN. |
| GET | `/api/v1/patients/{id}` | JWT | patient + their encounters. |
| POST | `/api/v1/encounters` | JWT | `{patient_id?, encounter_date?, privacy_required, note}` → encounter row (id `enc_…`); unknown patient ⇒ 404. Link a WS session via `session.start.encounter_id`. |
| GET | `/api/v1/encounters/{id}` | JWT | encounter + linked reports + transcript session ids. |

## Admin (Phase 7 — admin role required)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/admin/users` | list users (no password hashes, ever). |
| POST | `/api/v1/admin/users` | `{username, password ≥8, role, display_name?}`; duplicate ⇒ 422. |
| PATCH | `/api/v1/admin/users/{id}` | `{is_active}` — deactivation blocks login (403). |
| GET | `/api/v1/admin/audit?event=&user_id=&limit=&offset=` | durable audit query (payloads are deny-listed; usernames/passwords never recorded). |
| PUT | `/api/v1/admin/providers/{stt\|llm}/{name}/secret` | `{secret}` — stored Fernet-encrypted (`MS_SECURITY__SECRET_ENCRYPTION_KEY`); 501 when the vault key is unset. The raw secret is never returned by any endpoint. |
| DELETE | `/api/v1/admin/providers/{stt\|llm}/{name}/secret` | clears the stored secret. |
| GET | `/api/v1/admin/ai-requests?kind=&limit=` | AI usage ledger (provider, task, tokens, latency, status). |

## AI (client-safe view; never secrets)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/providers?kind=stt|llm&probe=health` | optional | registry listing: `{name, kind, description, configured, capabilities{privacy_class,...}, health?}` |
| GET | `/api/v1/observability/stats` | optional | process metrics snapshot — counters/gauges/latency plus **Phase 5**: `provider_health` (HealthTracker snapshot incl. EWMA latency + demotion state) and `cost` (daily token budget ledger). Prometheus endpoint: P8 |

## Reports (draft generation Phase 4 · lifecycle Phase 6)

Every transition is an explicit clinician action; AI output enters as a
**draft** and never becomes a medical record on its own (spec §5/§8/§10).

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/reports/{encounter_id}/draft` | optional | JSON body: `{transcript (1..80k chars) **or** session_id (assembles the stored transcript: paragraph/section markers become structure, terminology-normalized view feeds the prompt), template_key (server template) **or** template?{name, sections[{id,title,instruction}]}, patient_context?{name,age,sex,mrn,encounter_date}, language, mode, privacy_required, provider}`. Grounded strict-JSON drafting with one repair round-trip; response carries `report_id` (stored server-side; `draft_id` is a compat alias), `sections[]` (template order), `missing_sections[]` (`[[MISSING]]` = no transcript evidence — never invented), `warnings[]` (see validation codes below; each has `id`, `severity` info/warning/critical, `evidence`, `acknowledged`), `terminology_substitutions`, `transcript_source`, `phi_redaction_applied` and token `usage`. Routing obeys the privacy wall; cloud providers receive PHI-scrubbed input by default. Phase 5 fallback chain semantics unchanged. Errors: 422 VALIDATION (neither transcript nor session; empty session), 502 PROVIDER_UNAVAILABLE, 503 NO_PROVIDER. |
| GET | `/api/v1/reports?encounter_id=…` | optional | list reports for an encounter (summaries with status + warning counts). |
| GET | `/api/v1/reports/{report_id}/revisions` | optional | **Phase 7**: revision history — `revisions[]{rev, action (draft_generated\|finalized\|approved\|amended\|acknowledged\|edited), user_id, detail, created_at}` from the durable `report_revisions` table (event-log fallback when no DB is configured). |
| GET | `/api/v1/reports/{report_id}` | optional | full report: sections, warnings (with acknowledgment state), lifecycle metadata. 404 for unknown/expired ids. |
| PATCH | `/api/v1/reports/{report_id}` | optional | body `{sections: {<id>: <markdown>}}` — clinician edits (revision-tracked, audited). Draft/finalized only; **approved is immutable** → 409 `SESSION_STATE` (amend instead). Unknown section id → 409. |
| POST | `/api/v1/reports/{report_id}/acknowledge` | optional | body `{warning_id, justification (3..1000 chars)}` — records the clinician's review justification against the warning (audit trail). 409 when the report is approved or the justification is too short; 404 for unknown warning ids. |
| POST | `/api/v1/reports/{report_id}/finalize` | optional | draft → finalized. **409 while critical warnings are unacknowledged** (each needs a recorded justification first — spec §18 acceptance). |
| POST | `/api/v1/reports/{report_id}/approve` | optional | finalized → approved (separate explicit action; cannot be reached from draft). Same critical-acknowledgment gate. Approved versions are immutable and pinned in the store. |
| POST | `/api/v1/reports/{report_id}/reopen` | optional | finalized → draft (clinician pull-back before approval). |
| POST | `/api/v1/reports/{report_id}/amend` | optional | approved → **new linked draft** (`amended_from` points at the approved version, which stays untouched). Corrections after approval always go through amendments. |

Validation warning codes (`api/services/validation.py`, spec §9):
`unverified_number` (incl. dose near-misses — "10 mg → 100 mg" is critical),
`unit_mismatch` (mg vs mcg), `laterality_mismatch` (right↔left, critical),
`laterality_unverified`, `negation_mismatch` ("no effusion"→"effusion",
critical), `negation_added`, `negation_shift_suspected`, `unverified_date`,
`date_mismatch` (critical, cross-calendar Jalali↔Gregorian comparison),
`unverified_identifier` (critical, masked in messages), `unverified_anatomy`,
`provider_fallback` (info). Validation NEVER rewrites content — warnings only.

## Report templates (Phase 6 — data-driven, spec §10)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/report-templates` | optional | catalog: built-ins (`general-clinical-note`, `soap-note`, `radiology-report`, `ultrasound-report`, `ct-report`, `mri-report`) + custom. Each: `{key, name, category, description, sections[{id,title,instruction,required,format_style}], builtin, version}`. |
| GET | `/api/v1/report-templates/{key}` | optional | single template; 404 unknown. |
| POST | `/api/v1/report-templates` | optional | create custom: `{key?, name, category?, description?, sections[1..20]}`. Section ids `[a-z0-9_]`; format_style ∈ narrative/bullets/numbered/lab_values/heading_with_bullets. 422 on duplicates/bad shapes. |
| PATCH | `/api/v1/report-templates/{key}` | optional | update custom (bumps `version`); built-ins immutable → 422. |
| DELETE | `/api/v1/report-templates/{key}` | optional | soft-delete custom (204); built-ins → 422. |
| POST | `/api/v1/report-templates/{key}/fork` | optional | copy any template into a custom one: `{new_key, new_name?}` → 201. |
| POST | `/api/v1/report-templates/extract` | optional | Phlox-concept LLM extraction from an example note: `{example_note (20..20k), suggested_name?, mode?, privacy_required?, provider?}` → proposed `{suggested_name, note_type, sections[]}`. **Never auto-persists** — the clinician reviews and explicitly POSTs. Privacy wall + PHI redaction apply like every LLM call. |

## Terminology (Phase 6 — bilingual fa↔en canonicalization, spec §6)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/terminology?q=&limit=` | optional | search the catalog (`canonical`, `category`, `variants`). |
| POST | `/api/v1/terminology/normalize` | optional | `{text}` → `{normalized, substitutions[{original,replacement,category}], reversible: true}`. Derived view only — the stored transcript is never rewritten. Catalog policy: Persian transliterations → English terms (ام‌آرآی → MRI, پرفشاری خون → hypertension per spec §6); native Persian clinical terms (تب، سونوگرافی) stay Persian. Entries can never contain digits, dose units, negation or laterality words (engine-enforced). |

## Batch transcription (landed in Phase 3)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/transcribe/batch` | optional | multipart: `file` (WAV 16-bit mono **or** raw mono PCM16 with `sample_rate`), optional form fields `language`, `mode`, `privacy_required`, `provider`, `context_hints` (comma-separated hotwords). Routes through the same privacy-walled router (`TRANSCRIBE_BATCH` → only `supports_batch` providers). Returns `{request_id, provider, mode, privacy_override_applied, language, audio_duration_ms, segment_count, text, latency_ms, segments[]}`. Errors: 400 `VALIDATION` (bad/empty/stereo/non-16-bit audio), 413 (over `MS_STT__BATCH_MAX_BYTES`), 502 `PROVIDER_UNAVAILABLE` (`details.tried[]` = per-provider failures across the fallback chain; retryable failures transparently try the next routed provider, 4xx verdicts do not), 503 `NO_PROVIDER`. Audio is processed and dropped — nothing stored, audit records metadata only. |

## Transcripts (live session store — landed in Phase 2; durable since Phase 7)

In-memory LRU (last 200 ended sessions, ended evicted first) + **Phase 7
write-through**: when a WS session ends (stop *or* disconnect) the whole
session is upserted to `transcripts`/`transcript_segments` idempotently.
After eviction/restart `GET` serves the same REST shape from durable rows.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/transcripts/{session_id}` | optional | `TranscriptResponse{session_id, provider, language, status(open\|completed), segment_count, audio_duration_ms, segments[]}` where each segment is `{segment_id, text, start_ms, end_ms, language, confidence, edited, revision, updated_at, kind (dictated\|paragraph\|section\|finalized_section\|repeat), meta (marker payload, e.g. section_title)}`. 404 `NOT_FOUND` for unknown sessions. |
| PATCH | `/api/v1/transcripts/{session_id}/segments/{segment_id}` | optional | body `{text}` (≤8000 chars) → `{segment_id, revision, edited, updated_at}`. Identical text is a no-op (revision unchanged). Audited without content. |

These expose what the WS stream finalized; interim frames are never stored.

## Planned (contract frozen in phase docs)

- ~~`POST /api/v1/patients/search`, `POST /api/v1/encounters`~~ — shipped in P7 (see Patients & encounters)
- encounter-scoped transcript listing (`/api/v1/transcripts?encounter_id=…`) — open (session-scoped live API shipped in P2; report listing by encounter shipped in P6)
- ~~PostgreSQL persistence for templates/reports/revisions~~ — shipped in P7
- ~~`GET /api/v1/audit/...` (admin)~~ — shipped in P7 (`/api/v1/admin/audit`)
- Prometheus `/metrics` — P8
- WS: see `docs/WEBSOCKET_PROTOCOL.md`

Versioning policy: breaking REST changes go to `/api/v2`; v1 additive fields
are allowed and clients must ignore unknown fields (that asymmetry is
deliberate: browsers/WPF tolerate additions, servers reject client drift).
