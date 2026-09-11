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
| GET | `/health/ready` | readiness: TCP-probes configured Postgres/Redis; unconfigured ⇒ `disabled`, not blocking |
| GET | `/api/v1/version` | `{name, api_version, ws_protocol, ws_protocol_min, phase}` |
| GET | `/api/v1/config/manifest` | **client policy manifest**: ws path/protocol, audio format, languages (fa/en/fa-en), feature flags, voice-command catalog, routing modes |

## Auth

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — | `{username, password, device_name?}` → `{access_token, refresh_token, token_type, expires_in}`. **Phase 1:** credential store is Phase 7 → `501 AUTH_NOT_IMPLEMENTED`, except dev-token logins (`dev` / `MS_AUTH__DEV_TOKEN`) which mint a real JWT. |
| POST | `/api/v1/auth/refresh` | — | `501` in Phase 1 (rotation lands P7) |
| POST | `/api/v1/auth/logout` | JWT | Phase 7 adds refresh-token revocation |
| GET | `/api/v1/auth/me` | JWT | `{user_id, role, is_dev}` — works today |

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

## Transcripts (live session store — landed in Phase 2)

In-memory only until Phase 7 persistence; last 200 ended sessions are kept
(LRU, ended sessions evicted first), current sessions readable while open.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/transcripts/{session_id}` | optional | `TranscriptResponse{session_id, provider, language, status(open\|completed), segment_count, audio_duration_ms, segments[]}` where each segment is `{segment_id, text, start_ms, end_ms, language, confidence, edited, revision, updated_at, kind (dictated\|paragraph\|section\|finalized_section\|repeat), meta (marker payload, e.g. section_title)}`. 404 `NOT_FOUND` for unknown sessions. |
| PATCH | `/api/v1/transcripts/{session_id}/segments/{segment_id}` | optional | body `{text}` (≤8000 chars) → `{segment_id, revision, edited, updated_at}`. Identical text is a no-op (revision unchanged). Audited without content. |

These expose what the WS stream finalized; interim frames are never stored.

## Planned (contract frozen in phase docs)

- `POST /api/v1/patients/search`, `POST /api/v1/encounters` — P7
- encounter-scoped transcript/report listing (`/api/v1/transcripts?encounter_id=…`) — P7 (session-scoped live API shipped in P2; report listing by encounter shipped in P6)
- PostgreSQL persistence for templates/reports/revisions (in-memory stores are the service seam) — P7
- `GET /api/v1/audit/...` (admin) — P7
- WS: see `docs/WEBSOCKET_PROTOCOL.md`

Versioning policy: breaking REST changes go to `/api/v2`; v1 additive fields
are allowed and clients must ignore unknown fields (that asymmetry is
deliberate: browsers/WPF tolerate additions, servers reject client drift).
