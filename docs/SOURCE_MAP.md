# Source reference map

This project is clean-room inspired by four open-source repositories, vendored
at the repository root as **read-only reference checkouts**. No source file
was copied verbatim; adapted patterns are marked where they occur
(`docs/THIRD_PARTY_NOTICES.md` is the attribution index).

## References

| Reference (license) | Checkout | Primary reference (relative to checkout) |
|---|---|---|
| SpeakType (MIT) | `speaktype/` | `src/SpeakType.Core/` |
| Phlox (MIT) | `phlox/` | `server/api/`, `server/nlp_tools/`, `server/llm_client/` |
| Open Medical Scribe (MIT) | `open-medical-scribe/` | `src/providers/`, `src/services/` |
| Multi-Model-Gateway (MIT) | `Multi-Model-Gateway/` | `backend/app/services/`, `backend/app/core/`, `backend/app/middleware/` |

### SpeakType

WPF audio recording, microphone handling, hotkeys, desktop interaction.

Relevant files (`speaktype/src/SpeakType.Core/`):
`AppSettings.cs`, `AudioDeviceHelper.cs`, `AudioRecorder.cs`,
`AudioService.cs`, `FileLogger.cs`, `HotkeyService.cs`, `IAudioRecorder.cs`,
`IAudioService.cs`, `IHotkeyService.cs`, `ILogger.cs`,
`IWhisperTranscriber.cs`, `InputSender.cs`, `SettingsStore.cs`,
`WhisperTranscriber.cs`.

### Phlox

Medical report workflow, FastAPI patterns, templates, transcription, LLM
integration concepts.

Relevant files (`phlox/server/`): `api/chat.py`, `api/patient.py`,
`api/templates.py`, `api/transcribe.py`, `api/audit.py`,
`nlp_tools/adaptive_refinement.py`, `nlp_tools/document_processing.py`,
`nlp_tools/summarization_manager.py`, `nlp_tools/templates.py`,
`llm_client/client.py`, `llm_client/utils.py`.

### Open Medical Scribe

Provider abstraction, local/cloud/hybrid architecture, streaming
transcription, privacy, audit, medical note orchestration.

Relevant files (`open-medical-scribe/src/`):
`providers/transcription/index.js`, `providers/transcription/streamIndex.js`,
`providers/transcription/resultAdapter.js`,
`providers/transcription/whisperCppProvider.js`,
`providers/transcription/whisperOnnxProvider.js`,
`providers/transcription/whisperStreamProvider.js`,
`providers/transcription/deepgramProvider.js`,
`providers/transcription/deepgramStreamProvider.js`,
`providers/transcription/openAiProvider.js`, `providers/note/index.js`,
`providers/note/ollamaProvider.js`, `providers/note/openAiProvider.js`,
`providers/note/anthropicProvider.js`, `providers/note/geminiProvider.js`,
`services/auditLogger.js`, `services/privacy.js`, `services/promptBuilder.js`,
`services/scribeService.js`, `services/soapFormatter.js`,
`services/transcriptArtifacts.js`.

### Multi-Model-Gateway

Provider routing, authentication, Redis, queueing, rate limiting,
observability concepts.

Relevant files (`Multi-Model-Gateway/backend/app/`):
`services/provider_registry.py`, `services/provider_router.py`,
`services/router.py`, `services/fit_score.py`, `core/config.py`,
`core/security.py`, `core/redis.py`, `core/queue.py`, `core/metrics.py`,
`core/stream_guard.py`, `middleware/ratelimit.py`.

## Usage rules

- These repositories are **reference implementations only**.
- Do not blindly copy their architecture.
- Do not preserve their UI/framework choices when they conflict with this
  project's architecture.
- Do not copy code without preserving the applicable license/attribution.

## Usage log (per phase)

| Phase | Usage |
|---|---|
| 1 (2026-09-10) | Full inspection per the map above. Per-reference take/leave decisions, conflict resolutions, and the exact files where adapted concepts landed: `docs/ASSESSMENT.md`. Attribution index: `docs/THIRD_PARTY_NOTICES.md` (all four references are MIT). Target design: `docs/ARCHITECTURE.md`. No code was copied; SpeakType-derived client patterns carry in-file attribution headers. |
| 2 (2026-09-11) | SpeakType (MIT) — `AudioRecorder.cs`/`AudioDeviceHelper.cs` patterns adapted into `client/MedicalScribe.WPF/Audio/NAudioCaptureService.cs` (WaveInEvent 16 kHz mono loop, RMS level math normalized to 0..1, "skip virtual devices, prefer names containing mic" auto-pick). WAV writing deliberately NOT taken (STT is server-side). No reference code copied for the WS client or the backend hub; both follow this repo's frozen protocol doc. |
| 3 (2026-09-11) | No reference code copied. Adapter *patterns* follow the frozen contracts in `ai/base.py` (per ASSESSMENT §2: whisper-server stays an EXTERNAL service — no spawning, no SDK-embedded inference; cloud traffic only from the backend). Deepgram/Speechmatics wire formats implemented from their public protocol shapes and pinned by fixture tests in `tests/fixtures/` rather than live accounts. openai-compat HTTP plumbing for qwen-asr mirrors the llama-server pattern already in `ai/llm/openai_compat.py` (same repo module, not a reference one). |
| 4 (2026-09-11) | Phlox (MIT) — the *strict-JSON + repair round-trip* idea for structured note output informed `api/services/note_prompt.py` (message shape + fail-closed after one attempt). No code copied; prompt contract, sentinels, fidelity check and provider adapters are this repo's own. Open Medical Scribe (MIT) — mock-provider-parity rule followed: the mock LLM satisfies the same grounding contract as real providers so the pipeline is honest end-to-end without keys. Cloud LLM wires (OpenAI/Anthropic/Gemini) implemented from public API shapes, fixture-verified; SDKs deliberately avoided (httpx-only rule). |
