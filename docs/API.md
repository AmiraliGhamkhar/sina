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
| GET | `/api/v1/observability/stats` | optional | process metrics snapshot (Prometheus endpoint: P8) |

## Batch transcription (landed in Phase 3)

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/v1/transcribe/batch` | optional | multipart: `file` (WAV 16-bit mono **or** raw mono PCM16 with `sample_rate`), optional form fields `language`, `mode`, `privacy_required`, `provider`, `context_hints` (comma-separated hotwords). Routes through the same privacy-walled router (`TRANSCRIBE_BATCH` → only `supports_batch` providers). Returns `{request_id, provider, mode, privacy_override_applied, language, audio_duration_ms, segment_count, text, latency_ms, segments[]}`. Errors: 400 `VALIDATION` (bad/empty/stereo/non-16-bit audio), 413 (over `MS_STT__BATCH_MAX_BYTES`), 502 `PROVIDER_UNAVAILABLE`, 503 `NO_PROVIDER`. Audio is processed and dropped — nothing stored, audit records metadata only. |

## Transcripts (live session store — landed in Phase 2)

In-memory only until Phase 7 persistence; last 200 ended sessions are kept
(LRU, ended sessions evicted first), current sessions readable while open.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/transcripts/{session_id}` | optional | `TranscriptResponse{session_id, provider, language, status(open\|completed), segment_count, audio_duration_ms, segments[]}` where each segment is `{segment_id, text, start_ms, end_ms, language, confidence, edited, revision, updated_at}`. 404 `NOT_FOUND` for unknown sessions. |
| PATCH | `/api/v1/transcripts/{session_id}/segments/{segment_id}` | optional | body `{text}` (≤8000 chars) → `{segment_id, revision, edited, updated_at}`. Identical text is a no-op (revision unchanged). Audited without content. |

These expose what the WS stream finalized; interim frames are never stored.

## Planned (contract frozen in phase docs)

- `POST /api/v1/patients/search`, `POST /api/v1/encounters` — P7
- encounter-scoped transcript/report APIs (`/api/v1/transcripts?encounter_id=…`) — P7 (session-scoped live API already shipped in P2)
- `POST /api/v1/report-templates` (+ `POST /report-templates/extract-from-example`) — P6
- `POST /api/v1/reports/{encounter_id}/draft` → `PATCH .../sections` → `POST .../finalize` → `POST .../approve` — P6/P7 (two-step sign-off is non-negotiable)
- `GET /api/v1/audit/...` (admin) — P7
- WS: see `docs/WEBSOCKET_PROTOCOL.md`

Versioning policy: breaking REST changes go to `/api/v2`; v1 additive fields
are allowed and clients must ignore unknown fields (that asymmetry is
deliberate: browsers/WPF tolerate additions, servers reject client drift).
