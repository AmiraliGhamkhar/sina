# WebSocket Transcription Protocol — v1

Endpoint: `GET /ws/v1/transcribe` (upgrade).
Auth: `Authorization: Bearer <jwt>` header **or** `?token=<jwt>` query param
(query param exists for browser-based tools; prefer the header).
Unauthenticated production sockets are closed with HTTP 4401 before accept;
dev mode (env != production) allows anonymous sockets with a loud server
warning.

Schemas: `backend/api/schemas/ws.py` · Conformance tests:
`tests/test_ws_protocol.py`, `tests/test_ws_audio_flow.py`,
`tests/test_ws_commands_e2e.py`.

## Framing rules

- Control frames are UTF-8 JSON objects. **Every frame** carries
  `"v": 1` (integer) and `"type": "<string>"`.
- Inbound frames are **strictly validated**: unknown fields, wrong types, or
  unknown `type` values produce an `error` frame
  (`PROTOCOL_FRAME_INVALID` / `SESSION_STATE_INVALID`) and the socket stays
  open — except protocol-negotiation failures, which close (codes below).
- Audio: after `session.start`, prefer **binary WS frames** — raw
  little-endian PCM16 mono @ the negotiated sample rate (default 16 kHz),
  ~100 ms per frame (3200 B). The `audio.chunk` JSON form (base64) is also
  accepted for non-browser clients and tests. Binary frames have no envelope;
  chunk = frame.
- Session-level rate limit: at most `MS_WEBSOCKET__MAX_MESSAGE_BYTES`
  (default 1 MiB) per frame; larger frames close the socket (1009).

## Close codes

| Code | Meaning |
|---|---|
| 4401 | authentication failed / required |
| 4408 | handshake or idle timeout (idle = 3× heartbeat interval with no frame) |
| 4409 | protocol version unsupported (client must upgrade or refuse) |
| 4400 | policy violation (malformed frame, unknown first message, per-user session cap exceeded — `error.code` = `RATE_LIMITED`) |
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
  "encounter_id": "enc_01...",         // optional: links the session to a durable
                                       //   encounter row; unknown ids are NOT rejected (encounter
                                       //   creation is async in clinician workflow) — they persist as-is
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

Audio sent while `paused` is buffered briefly
(`MS_WEBSOCKET__PAUSE_BUFFER_MS`, default 2000 ms) then dropped with a
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

## Semantics

- **Segments.** `segment_id` is stable for a given utterance: interims replace
  the *current* segment's provisional text; `transcript.final` commits it.
  Clients must not paste interims into the persistent transcript.
- **Command detection** (server-side, live). The parser
  (`backend/api/services/voice_commands/`) is data-driven (catalog → parser →
  effects) and bilingual. An utterance is a command ONLY when the whole
  utterance matches a trigger (exact) or a trigger + free-text argument
  (anchored, arg-taking commands like "درج بخش سابقه بیماری"); mode prefixes
  (`فرمان` / "command") force interpretation. A trigger embedded mid-speech
  is ambiguous → the text STAYS in the transcript and a `COMMAND_AMBIGUOUS`
  warning is emitted (never silently delete clinical speech, spec §8).
  Recognized commands are removed from transcript text, reported via
  `command.detected` (with `args`, `utterance_text`, `segment_id`), and their
  effects are applied to the session transcript store with an undo journal
  (`undo_last` reverses the last structural effect). Warning codes:
  `COMMAND_AMBIGUOUS`, `COMMAND_NO_TARGET` (e.g. nothing to delete/repeat),
  `COMMAND_MISSING_ARG`, `COMMAND_GATE`, `COMMAND_IGNORED_PAUSED`,
  `COMMAND_ALREADY_PAUSED`, `COMMAND_NOT_PAUSED`.
- **Voice pause vs control-frame pause.** Voice pause (`pause_recording`) is
  distinct from `session.pause`: audio keeps flowing (so "ادامه ضبط" stays
  audible) while non-command finals and interims are suppressed from the
  transcript. The control-frame pause buffers audio instead.
- **Errors.** `error.recoverable=true` ⇒ client may continue (e.g. provider
  fell back); `false` ⇒ expect close shortly.
- **Heartbeats.** The server sends `heartbeat.ping`
  (`{"v":1,"type":"heartbeat.ping","session_id":…,"server_time_ms":…}`) when a
  session is open but silent for one `MS_WEBSOCKET__HEARTBEAT_SECONDS`
  interval; the client SHOULD reply `{"v":1,"type":"pong"}` (any inbound frame
  — audio, control, pong — resets the idle counter). Three consecutive silent
  intervals close with 4408. Nginx read/send timeouts are tuned to 3600 s.
- **Provider fallback mid-session.** Emits `warning` `code=PROVIDER_FALLBACK`
  with `details: {from, to}`; the updated `provider` appears in
  `warning.details` only — session identity never changes. The fallback chain
  is privacy-filtered at selection time, so a `privacy_required` session can
  never fall back past the wall. Audio already dequeued by the dying provider
  is lost with it (the new provider resumes from the shared queue). Exactly
  one warning per switch.

## Status

Fully implemented (Phases 1–8):

| Area | Coverage |
|---|---|
| Control plane | auth gate, `session.start` validation + protocol negotiation, router-backed provider selection, pause/resume/stop, `session.completed`, audit records, error frames — `tests/test_ws_protocol.py` |
| Audio pipeline | `audio.chunk` **and raw binary WS frames** feed `transcription_hub` → `STTProvider.stream()` → `transcript.interim/final` frames with stable per-utterance `segment_id`s; finals land in the in-memory `TranscriptStore` (served by `GET /api/v1/transcripts/{session_id}`) with durable write-through; pause buffers ≤ `MS_WEBSOCKET__PAUSE_BUFFER_MS` then drops with a single `AUDIO_DROPPED_PAUSED` warning; queue overflow warns `AUDIO_OVERLOADED`; stop flushes the provider with `AUDIO`/`PROVIDER_FLUSH_TIMEOUT` guard — `tests/test_ws_audio_flow.py` (incl. a faulting provider → `PROVIDER_UNAVAILABLE` error frame) |
| Voice commands | live end-to-end — `tests/test_ws_commands_e2e.py` drives a scripted mock STT whose dictation interleaves clinical speech with commands (paragraph, delete-last-sentence, insert-section, ambiguous trigger, pause/resume) and pins: `command.detected` frames with args, transcript-store effects (markers, sentence removal, suppression while voice-paused), ambiguity warnings keeping text verbatim, and `voice_commands_executed` metrics |
| Keepalive | server `heartbeat.ping` on idle, client auto-pong; 3 silent intervals still close 4408 |
