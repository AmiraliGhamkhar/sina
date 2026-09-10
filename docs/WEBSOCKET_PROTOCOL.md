# WebSocket Transcription Protocol — v1

Endpoint: `GET /ws/v1/transcribe` (upgrade) · Auth: `Authorization: Bearer <jwt>`
header **or** `?token=<jwt>` query param (query param exists for browser-based
tools; prefer the header). Unauthenticated production sockets are closed with
HTTP 4401 before accept; dev-mode (env != production) allows anonymous sockets
with a loud server warning.

## Framing rules

- Control frames are UTF-8 JSON objects. **Every frame** carries
  `"v": 1` (integer) and `"type": "<string>"`.
- Inbound frames are **strictly validated**: unknown fields, wrong types, or
  unknown `type` values produce an `error` frame
  (`PROTOCOL_FRAME_INVALID` / `SESSION_STATE_INVALID`) and the socket stays
  open — except protocol-negotiation failures, which close (codes below).
- Audio: after `session.start`, prefer **binary WS frames** — raw little-endian
  PCM16 mono @ the negotiated sample rate (default 16 kHz), ~100 ms per frame
  (3200 B). The `audio.chunk` JSON form (base64) is also accepted for
  non-browser clients and tests. Binary frames have no envelope; framing is
  chunk = frame.
- Session-level rate limit: at most `MS_WEBSOCKET__MAX_MESSAGE_BYTES` (default
  1 MiB) per frame; larger frames close the socket (1009).

## Close codes

| Code | Meaning |
|---|---|
| 4401 | authentication failed / required |
| 4408 | handshake or idle timeout (idle = 3× heartbeat) |
| 4409 | protocol version unsupported (client must upgrade or refuse) |
| 4400 | policy violation (malformed frame, unknown first message) |
| 4503 | no eligible AI provider right now (retry later; see `error.code`) |
| 1000 | normal `session.stop` completion |

## Client → server

```jsonc
{ "v": 1, "type": "session.start",
  "session_id": null,                  // optional client correlation id
  "language": "fa-en",                 // "fa" | "en" | "fa-en" | null=auto-detect
  "provider": null,                    // explicit STT provider name or null → router decides
  "mode": "auto",                      // "local" | "cloud" | "hybrid" | "auto" (policy, may be overridden by privacy)
  "privacy_required": true,            // null → server default (currently: true = local only)
  "encounter_id": "enc_01...",         // optional, validated server-side once P7 exists
  "audio": { "encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1 },
  "client": { "name": "MedicalScribe.WPF", "version": "0.1.0", "device": "clinic-pc-3" }
}
```

```jsonc
{ "v": 1, "type": "audio.chunk", "seq": 12, "ts_ms": 1200, "pcm16_b64": "AAABAAIA..." }
{ "v": 1, "type": "session.pause",  "reason": "clinician stepped away" }
{ "v": 1, "type": "session.resume" }
{ "v": 1, "type": "session.stop" }
```

State machine (server-enforced):

```
(handshake) --session.start--> recording --session.pause--> paused
    recording --session.resume--> recording (no-op if already recording)
    recording|paused --session.stop--> completed (server closes after ack)
```

Audio sent while `paused` is buffered briefly (≤2 s) then dropped with a
`warning`. `session.stop` flushes buffers, finalizes the last utterance, then
emits `session.completed`.

## Server → client

```jsonc
{ "v": 1, "type": "session.started", "session_id": "ws_ab12...", "protocol": 1,
  "provider": "whisper-local", "mode": "local", "language": "fa-en",
  "server_time_ms": 1757550000000 }

{ "v": 1, "type": "transcript.interim", "session_id": "...", "segment_id": "seg_41",
  "text": "بیمار به MRI ارجاع داده شد و", "start_ms": 120400 }

{ "v": 1, "type": "transcript.final", "session_id": "...", "segment_id": "seg_41",
  "text": "بیمار به MRI ارجاع داده شد و CT انجام شد.",
  "start_ms": 120400, "end_ms": 128930, "language": "fa-en", "confidence": 0.93 }

{ "v": 1, "type": "command.detected", "session_id": "...",
  "command": "new_paragraph", "args": {}, "utterance_text": "پاراگراف جدید",
  "segment_id": "seg_42" }

{ "v": 1, "type": "warning", "code": "VALIDATION_WARNING",
  "message": "dose '10 mg' in transcript missing from generated text",
  "segment_id": "seg_43" }

{ "v": 1, "type": "error", "code": "PROVIDER_UNAVAILABLE",
  "message": "deepgram stream closed unexpectedly (retrying via fallback)",
  "recoverable": true }

{ "v": 1, "type": "session.completed", "session_id": "...", "segment_count": 17,
  "duration_ms": 248123, "provider": "whisper-local" }
```

Notes:

- `segment_id` is stable for a given utterance: interims replace the *current*
  segment's provisional text; `transcript.final` commits it. Clients must not
  paste interims into the persistent transcript.
- Command detection runs server-side (extensible parser, Phase 6). A recognized
  command is *removed* from transcript text and reported via `command.detected`
  only when it is distinguishable from clinical speech (exact-trigger or
  explicit "command mode" segment); ambiguous matches stay in the transcript.
- `error.recoverable=true` ⇒ client may continue (e.g. provider fell back);
  `false` ⇒ expect close shortly.
- Heartbeats: Phase 1 relies on TCP/WS infra + the idle-timeout close (4408);
  Phase 2 adds explicit app-level pings — server
  `{"v":1,"type":"heartbeat","server_time_ms":...}` every
  `MS_WEBSOCKET__HEARTBEAT_SECONDS`, client `{"v":1,"type":"pong"}` (both will
  be added to the v1 schema as *new* allowed types — additive, non-breaking).
- Provider fallback mid-session emits `warning` `code=PROVIDER_FALLBACK` with
  `details: {from, to}` and a following `session.started`-style
  `provider` field update inside `warning.details` only — session identity
  never changes.

## Phase status (honesty ledger)

Implemented in Phase 1: auth gate, `session.start` validation + protocol
negotiation, router-backed provider selection, session state machine
(pause/resume/stop), `session.completed`, audit records, error frames — all
covered by `tests/test_ws_protocol.py`. `audio.chunk` is schema-validated but
answered `CAPABILITY_NOT_IMPLEMENTED` until Phase 2, when `transcript.*` and
`command.detected` frames are emitted by real provider streams.
