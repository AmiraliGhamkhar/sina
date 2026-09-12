# Source Reference Map

This project uses the following open-source repositories as
architecture/reference sources.

## SpeakType
Path:
../speaktype

Primary reference:
src/SpeakType.Core/

Relevant files:
- AppSettings.cs
- AudioDeviceHelper.cs
- AudioRecorder.cs
- AudioService.cs
- FileLogger.cs
- HotkeyService.cs
- IAudioRecorder.cs
- IAudioService.cs
- IHotkeyService.cs
- ILogger.cs
- IWhisperTranscriber.cs
- InputSender.cs
- SettingsStore.cs
- WhisperTranscriber.cs

Purpose:
WPF audio recording, microphone handling, hotkeys and desktop interaction.

## Phlox
Path:
../phlox

Primary reference:
server/api/
server/nlp_tools/
server/llm_client/

Relevant files:
- server/api/chat.py
- server/api/patient.py
- server/api/templates.py
- server/api/transcribe.py
- server/api/audit.py
- server/nlp_tools/adaptive_refinement.py
- server/nlp_tools/document_processing.py
- server/nlp_tools/summarization_manager.py
- server/nlp_tools/templates.py
- server/llm_client/client.py
- server/llm_client/utils.py

Purpose:
Medical report workflow, FastAPI patterns, templates, transcription
and LLM integration concepts.

## Open Medical Scribe
Path:
../open-medical-scribe

Primary reference:
src/providers/
src/services/

Relevant files:
- src/providers/transcription/index.js
- src/providers/transcription/streamIndex.js
- src/providers/transcription/resultAdapter.js
- src/providers/transcription/whisperCppProvider.js
- src/providers/transcription/whisperOnnxProvider.js
- src/providers/transcription/whisperStreamProvider.js
- src/providers/transcription/deepgramProvider.js
- src/providers/transcription/deepgramStreamProvider.js
- src/providers/transcription/openAiProvider.js
- src/providers/note/index.js
- src/providers/note/ollamaProvider.js
- src/providers/note/openAiProvider.js
- src/providers/note/anthropicProvider.js
- src/providers/note/geminiProvider.js
- src/services/auditLogger.js
- src/services/privacy.js
- src/services/promptBuilder.js
- src/services/scribeService.js
- src/services/soapFormatter.js
- src/services/transcriptArtifacts.js

Purpose:
Provider abstraction, local/cloud/hybrid architecture,
streaming transcription, privacy, audit and medical note orchestration.

## Multi-Model-Gateway
Path:
../Multi-Model-Gateway

Primary reference:
backend/app/services/
backend/app/core/
backend/app/middleware/

Relevant files:
- backend/app/services/provider_registry.py
- backend/app/services/provider_router.py
- backend/app/services/router.py
- backend/app/services/fit_score.py
- backend/app/core/config.py
- backend/app/core/security.py
- backend/app/core/redis.py
- backend/app/core/queue.py
- backend/app/core/metrics.py
- backend/app/core/stream_guard.py
- backend/app/middleware/ratelimit.py

Purpose:
Provider routing, authentication, Redis, queueing,
rate limiting and observability concepts.

IMPORTANT:
These repositories are reference implementations only.
Do not blindly copy their architecture.
Do not preserve their UI/framework choices when they conflict
with this project's architecture.
Do not copy code without preserving the applicable license/attribution.
---

## Phase 1 usage log (2026-09-10)

This map was followed during the Phase 1 inspection. Per-reference take/leave
decisions, conflict resolutions, and the exact files where adapted concepts
landed are recorded in:

- `docs/ASSESSMENT.md` — analysis + implementation order
- `docs/THIRD_PARTY_NOTICES.md` — attribution index (all four references are MIT)
- `docs/ARCHITECTURE.md` — target design (only reference *patterns* survive here)

No code was copied from reference repositories; SpeakType-derived client
patterns carry in-file attribution headers.

## Phase 2 usage log (2026-09-11)

- SpeakType (MIT) — `AudioRecorder.cs`/`AudioDeviceHelper.cs` patterns adapted
  into `client/MedicalScribe.WPF/Audio/NAudioCaptureService.cs` (WaveInEvent
  16 kHz mono loop, RMS level math normalized to 0..1, "skip virtual devices,
  prefer names containing mic" auto-pick — Steam hint generalized). WAV
  writing deliberately NOT taken (STT is server-side).
- No reference code copied for the WS client or the backend hub; both follow
  this repo's frozen protocol doc. `frontend/` still intentionally empty.

## Phase 3 usage log (2026-09-11)

- No reference code copied. Adapter *patterns* follow the frozen contracts in
  `ai/base.py` (per ASSESSMENT §2: whisper-server stays an EXTERNAL service —
  no spawning, no SDK-embedded inference; cloud traffic only from the
  backend). The Deepgram/Speechmatics wire formats are implemented from their
  public protocol shapes and pinned by fixture tests in `tests/fixtures/`
  rather than live accounts.
- openai-compat HTTP plumbing for qwen-asr mirrors the llama-server pattern
  already in `ai/llm/openai_compat.py` (same repo module, not a reference one).

## Phase 4 usage log (2026-09-11)

- Phlox (MIT) — the *strict-JSON + repair round-trip* idea for structured note
  output informed `api/services/note_prompt.py` (message shape + fail-closed
  after one attempt). No code copied; prompt contract, sentinels, fidelity
  check and provider adapters are this repo's own.
- open-medical-scribe (MIT) — mock-provider-parity rule followed: the mock
  LLM now satisfies the same grounding contract as real providers so the
  pipeline is honest end-to-end without keys.
- Cloud LLM wires (OpenAI/Anthropic/Gemini) implemented from public API
  shapes, fixture-verified; SDKs deliberately avoided (httpx-only rule).

## Provider-expansion usage log (2026-09-12)

- **9Router** (github.com/decolua/9router, MIT © decolua) — consulted as an
  *API-shape reference only*: no local checkout under `/sina`, no code copied,
  no dependency added. Its documented OpenAI-compatible surface under
  `/api/v1` (`chat/completions`, `messages`, `models`, `models/{kind}`,
  `audio/transcriptions`) and its auth rule (`Authorization: Bearer` /
  `x-api-key` **only** when `REQUIRE_API_KEY=true`) drove
  `ai/nine_router_client.py`, `ai/llm/nine_router.py` and
  `ai/stt/nine_router.py`. Model ids are `provider/model`, which is why the
  catalog is *discovered* rather than configured. The "one gateway fronts many
  upstreams" idea is the same pattern already taken from
  Multi-Model-Gateway — this added a concrete adapter, not a new concept.
- **Speechmatics realtime** (docs.speechmatics.com, public API reference) —
  `ai/stt/speechmatics.py`'s WebSocket path was re-aligned to the *current*
  published protocol: `StartRecognition` → `RecognitionStarted` → binary
  `AddAudio` → `AddPartialTranscript`/`AddTranscript` → `EndOfStream`
  (with `last_seq_no`) → `EndOfTranscript`, `transcription_config` carrying
  `language`/`max_delay`/`enable_partials`/`domain`/`diarization`/
  `additional_vocab`, and the documented close-code mapping
  (4001/4003 → `ProviderError`; 1011/4005/4013 → `ProviderUnavailableError`).
  The **batch job API was deliberately left untouched** in this pass. Still
  httpx + the existing `WsTransport` seam — no SDK, no copied code; the
  protocol is pinned by fixture tests in `tests/test_stt_speechmatics.py`.
- Nothing above is vendored, so `docs/THIRD_PARTY_NOTICES.md` gains only a
  reference-table row (MIT, shape-only) and no license text.
