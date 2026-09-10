# Source Reference Map

This project uses the following open-source repositories as
architecture/reference sources.

## SpeakType
Path:
../MedicalScribe-References/speaktype

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
../MedicalScribe-References/phlox

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
../MedicalScribe-References/open-medical-scribe

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
../MedicalScribe-References/Multi-Model-Gateway

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