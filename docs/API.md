# REST API — v1

Base: `https://<gateway>/api/v1` (dev: `http://localhost:8000/api/v1`).
Auth: `Authorization: Bearer <jwt>` — required where noted; non-production
builds also accept the dev login (`dev` / `MS_AUTH__DEV_TOKEN`).

Error envelope (all non-2xx):

```json
{ "error": { "code": "STABLE_CODE", "message": "human text", "details": null } }
```

Stable codes live in `backend/api/errors.py::ErrorCode`. Validation failures
(422) report field `loc`s and rule types, **never submitted values** (patient
data must not echo back through error paths).

Versioning policy: breaking REST changes go to `/api/v2`; v1 additive fields
are allowed and clients must ignore unknown fields (deliberate asymmetry:
clients tolerate additions, the server rejects client drift).

## Public (no auth)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness: `{status, version, phase}` |
| GET | `/health/ready` | readiness: TCP probe of Postgres/Redis from configured URLs; unconfigured components report `disabled`, never blocking |
| GET | `/metrics` | Prometheus text exposition (no auth — internal scrape only; the nginx edge does not proxy this path). Series: `medicalscribe_http_responses_total{status}`, `medicalscribe_http_request_duration_seconds{route,quantile}` (p50/p95/p99 + exact sum/count), WS/STT/LLM counters, `medicalscribe_provider_healthy{provider}`, cost-budget gauges, `db_mode`, `build_info`. Never contains transcripts, usernames, or secrets. |
| GET | `/api/v1/version` | `{name, api_version, ws_protocol, ws_protocol_min, phase}` |
| GET | `/api/v1/config/manifest` | **client policy manifest**: WS path/protocol, audio format, languages (fa/en/fa-en), feature flags, voice-command catalog, routing modes |

## Auth

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — (rate-limited) | `{username, password, device_name?}` → `{access_token, refresh_token, token_type, expires_in}`. Argon2id verification against `users`; 5 consecutive failures lock the account for `MS_AUTH__LOCKOUT_SECONDS` (429 with retry hint). Requires `MS_DATABASE__URL` (else `501 AUTH_NOT_IMPLEMENTED`). Dev-token logins still mint a real JWT in non-production. |
| POST | `/api/v1/auth/refresh` | — (rate-limited) | `{refresh_token}` → new pair. **One-time tokens**: the presented token is consumed; presenting a consumed token again (theft assumption) revokes ALL of that user's refresh tokens and returns 401. |
| POST | `/api/v1/auth/logout` | JWT | optional body `{refresh_token?}` — the presented refresh token is revoked |
| GET | `/api/v1/auth/me` | JWT | `{user_id, role, is_dev}` |

## Patients & encounters (DB-backed)

Without `MS_DATABASE__URL` these answer `501 DB_NOT_CONFIGURED` (honest
capability boundary, not silent degradation).

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/patients` | JWT | `{full_name, mrn?, date_of_birth?, sex?, notes?}` → patient row (id `pat_…`). Audited without content. |
| GET | `/api/v1/patients?q=…&limit=` | JWT | case-insensitive partial match on name/MRN |
| GET | `/api/v1/patients/{patient_id}` | JWT | patient + their encounters |
| POST | `/api/v1/encounters` | JWT | `{patient_id?, encounter_date?, privacy_required, note}` → encounter row (id `enc_…`); unknown patient ⇒ 404. Link a WS session via `session.start.encounter_id`. |
| GET | `/api/v1/encounters/{encounter_id}` | JWT | encounter + linked reports + transcript session ids |

## Admin (admin role required)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/admin/users` | list users (no password hashes, ever) |
| POST | `/api/v1/admin/users` | `{username, password ≥8, role, display_name?}`; duplicate ⇒ 422 |
| PATCH | `/api/v1/admin/users/{user_id}` | `{is_active}` — deactivation blocks login (403) |
| GET | `/api/v1/admin/audit?event=&user_id=&limit=&offset=` | durable audit query (payloads deny-listed; usernames/passwords never recorded) |
| PUT | `/api/v1/admin/providers/{kind}/{name}/secret` (kind = stt or llm) | `{secret}` — stored Fernet-encrypted (`MS_SECURITY__SECRET_ENCRYPTION_KEY`); 501 when the vault key is unset. The raw secret is never returned by any endpoint. |
| DELETE | `/api/v1/admin/providers/{kind}/{name}/secret` (kind = stt or llm) | clears the stored secret |
| GET | `/api/v1/admin/ai-requests?kind=&limit=` | AI usage ledger (provider, task, tokens, latency, status) |

## AI (client-safe view; never secrets)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/providers?kind=stt\|llm&probe=health` | optional | registry listing: `{name, kind, description, configured, capabilities{privacy_class,...}, health?}` |
| GET | `/api/v1/observability/stats` | optional | process metrics snapshot — counters/gauges/latency plus `provider_health` (HealthTracker snapshot incl. EWMA latency + demotion state) and `cost` (daily token budget ledger) |

## Reports (grounded drafting + lifecycle)

Every transition is an explicit clinician action; AI output enters as a
**draft** and never becomes a medical record on its own (spec §5/§8/§10).

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/reports/{encounter_id}/draft` | optional | JSON body: `{transcript (1..80k chars) **or** session_id (assembles the stored transcript: paragraph/section markers become structure, terminology-normalized view feeds the prompt), template_key (server template) **or** template?{name, sections[{id,title,instruction}]}, patient_context?{name,age,sex,mrn,encounter_date}, language, mode, privacy_required, provider}`. Grounded strict-JSON drafting with one repair round-trip; response carries `report_id` (stored server-side; `draft_id` is a compat alias), `sections[]` (template order), `missing_sections[]` (`[[MISSING]]` = no transcript evidence — never invented), `warnings[]` (see validation codes below; each has `id`, `severity` info/warning/critical, `evidence`, `acknowledged`), `terminology_substitutions`, `transcript_source`, `phi_redaction_applied` and token `usage`. Routing obeys the privacy wall; cloud providers receive PHI-scrubbed input by default. Errors: 422 VALIDATION (neither transcript nor session; empty session), 502 PROVIDER_UNAVAILABLE, 503 NO_PROVIDER. |
| GET | `/api/v1/reports?encounter_id=…` | optional | list reports for an encounter (summaries with status + warning counts) |
| GET | `/api/v1/reports/{report_id}` | optional | full report: sections, warnings (with acknowledgment state), lifecycle metadata. 404 for unknown/expired ids. |
| GET | `/api/v1/reports/{report_id}/revisions` | optional | revision history — `revisions[]{rev, action (draft_generated\|finalized\|approved\|amended\|acknowledged\|edited), user_id, detail, created_at}` from the durable `report_revisions` table (event-log fallback when no DB is configured). |
| PATCH | `/api/v1/reports/{report_id}` | optional | body `{sections: {<id>: <markdown>}}` — clinician edits (revision-tracked, audited). Draft/finalized only; **approved is immutable** → 409 `SESSION_STATE` (amend instead). Unknown section id → 409. |
| POST | `/api/v1/reports/{report_id}/acknowledge` | optional | body `{warning_id, justification (3..1000 chars)}` — records the clinician's review justification against the warning (audit trail). 409 when the report is approved or the justification is too short; 404 for unknown warning ids. |
| POST | `/api/v1/reports/{report_id}/finalize` | optional | draft → finalized. **409 while critical warnings are unacknowledged** (each needs a recorded justification first — spec §18 acceptance). |
| POST | `/api/v1/reports/{report_id}/approve` | optional | finalized → approved (separate explicit action; cannot be reached from draft). Same critical-acknowledgment gate. Approved versions are immutable and pinned in the store. |
| POST | `/api/v1/reports/{report_id}/reopen` | optional | finalized → draft (clinician pull-back before approval) |
| POST | `/api/v1/reports/{report_id}/amend` | optional | approved → **new linked draft** (`amended_from` points at the approved version, which stays untouched). Corrections after approval always go through amendments. |

Validation warning codes (`backend/api/services/validation.py`, spec §9):
`unverified_number` (incl. dose near-misses — "10 mg → 100 mg" is critical),
`unit_mismatch` (mg vs mcg), `laterality_mismatch` (right↔left, critical),
`laterality_unverified`, `negation_mismatch` ("no effusion"→"effusion",
critical), `negation_added`, `negation_shift_suspected`, `unverified_date`,
`date_mismatch` (critical, cross-calendar Jalali↔Gregorian comparison),
`unverified_identifier` (critical, masked in messages), `unverified_anatomy`,
`provider_fallback` (info). Validation NEVER rewrites content — warnings only.

## Report templates (data-driven, spec §10)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/report-templates` | optional | catalog: built-ins (`general-clinical-note`, `soap-note`, `radiology-report`, `ultrasound-report`, `ct-report`, `mri-report`) + custom. Each: `{key, name, category, description, sections[{id,title,instruction,required,format_style}], builtin, version}`. |
| GET | `/api/v1/report-templates/{key}` | optional | single template; 404 unknown |
| POST | `/api/v1/report-templates` | optional | create custom: `{key?, name, category?, description?, sections[1..20]}`. Section ids `[a-z0-9_]`; format_style ∈ narrative/bullets/numbered/lab_values/heading_with_bullets. 422 on duplicates/bad shapes. |
| PATCH | `/api/v1/report-templates/{key}` | optional | update custom (bumps `version`); built-ins immutable → 422 |
| DELETE | `/api/v1/report-templates/{key}` | optional | soft-delete custom (204); built-ins → 422 |
| POST | `/api/v1/report-templates/{key}/fork` | optional | copy any template into a custom one: `{new_key, new_name?}` → 201 |
| POST | `/api/v1/report-templates/extract` | optional | LLM extraction from an example note: `{example_note (20..20k), suggested_name?, mode?, privacy_required?, provider?}` → proposed `{suggested_name, note_type, sections[]}`. **Never auto-persists** — the clinician reviews and explicitly POSTs. Privacy wall + PHI redaction apply like every LLM call. |

## Terminology (bilingual fa↔en canonicalization, spec §6)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/terminology?q=&limit=` | optional | search the catalog (`canonical`, `category`, `variants`) |
| POST | `/api/v1/terminology/normalize` | optional | `{text}` → `{normalized, substitutions[{original,replacement,category}], reversible: true}`. Derived view only — the stored transcript is never rewritten. Catalog policy: Persian transliterations → English terms (ام‌آرآی → MRI, پرفشاری خون → hypertension per spec §6); native Persian clinical terms (تب، سونوگرافی) stay Persian. Entries can never contain digits, dose units, negation or laterality words (engine-enforced). |

## Batch transcription

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/transcribe/batch` | optional | multipart: `file` (WAV 16-bit mono **or** raw mono PCM16 with `sample_rate`), optional form fields `language`, `mode`, `privacy_required`, `provider`, `context_hints` (comma-separated hotwords). Routes through the same privacy-walled router (`TRANSCRIBE_BATCH` → only `supports_batch` providers). Returns `{request_id, provider, mode, privacy_override_applied, language, audio_duration_ms, segment_count, text, latency_ms, segments[]}`. Errors: 400 `VALIDATION` (bad/empty/stereo/non-16-bit audio), 413 (over `MS_STT__BATCH_MAX_BYTES`), 502 `PROVIDER_UNAVAILABLE` (`details.tried[]` = per-provider failures across the fallback chain; retryable failures transparently try the next routed provider, 4xx verdicts do not), 503 `NO_PROVIDER`. Audio is processed and dropped — nothing stored; audit records metadata only. |

## Transcripts (live session store + durable write-through)

In-memory LRU (last 200 ended sessions, ended evicted first) + durable
write-through: when a WS session ends (stop *or* disconnect) the whole session
is upserted to `transcripts`/`transcript_segments` idempotently. After
eviction/restart, `GET` serves the same REST shape from durable rows.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/transcripts?encounter_id=…&limit=` | optional | encounter-scoped listing — live in-memory sessions first, durable rows merged + deduped (works in memory-only mode with live sessions). Light summaries: `{session_id, provider, language, status, segment_count, audio_duration_ms, started_at?, ended_at?}` — no segment payloads. |
| GET | `/api/v1/transcripts/{session_id}` | optional | `TranscriptResponse{session_id, provider, language, status(open\|completed), segment_count, audio_duration_ms, segments[]}` where each segment is `{segment_id, text, start_ms, end_ms, language, confidence, edited, revision, updated_at, kind (dictated\|paragraph\|section\|finalized_section\|repeat), meta (marker payload, e.g. section_title)}`. 404 `NOT_FOUND` for unknown sessions. |
| PATCH | `/api/v1/transcripts/{session_id}/segments/{segment_id}` | optional | body `{text}` (≤8000 chars) → `{segment_id, revision, edited, updated_at}`. Identical text is a no-op (revision unchanged). Audited without content. |

These expose what the WS stream finalized; interim frames are never stored.
WS protocol: `docs/WEBSOCKET_PROTOCOL.md`.

## Models (model hub — verified downloads + auto-configure)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/models` | any principal | server-driven catalog + live install status: `{models: [{id, name, role, description, license, license_url, source_repo, runtime, providers[], state (not_installed\|downloading\|installed\|error), progress, received_bytes, total_bytes, current_file, error, installed_at, auto_configured, operator_note, install_dir, files[{local_name, size_bytes, received_bytes}]}]}`. |
| GET | `/api/v1/models/{model_id}` | any principal | single model, same shape (progress polling). 404 `NOT_FOUND` for unknown ids. |
| POST | `/api/v1/models/{model_id}/download` | admin | start a verified background download → `202 {model_id, state, detail}`. 409 when one is already running (idempotent no-op `202 state=installed` when already installed), 503 on unreachable upstream. |
| DELETE | `/api/v1/models/{model_id}` | admin | remove artifacts + cancel in-flight download → `{model_id, deleted, detail}`; idempotent. |

Downloads stream from the pinned HuggingFace sources into `MS_MODELS__DIR`
with sha256/size verification; in-process models (Shenava STT, PII NER)
auto-configure on completion. Audited metadata-only
(`MODEL_DOWNLOADED`/`MODEL_DELETED`); metrics counters on `/metrics`.
Catalog, licenses, and external-service wiring: `docs/MODELS.md`.
