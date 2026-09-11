# Third-party notices & attribution

This project is **clean-room inspired** by four open-source repositories
vendored under `/sina` reference checkouts for architecture study. No source
file was copied verbatim; several *patterns* were adapted and are marked where
they occur. All reference licenses below are permissive; where adaptation is
substantial the file header carries a pointer back here.

## Reference repositories

| Repo | License | What we took | What we rejected |
|---|---|---|---|
| SpeakType | MIT © 2026 "SpeakType" authors (installer/LICENSE.txt) | Windows audio-capture format (16 kHz mono PCM16, RMS level events), NAudio usage shape, global hotkey (RegisterHotKey/WM_HOTKEY), JSON settings store pattern, overlay-less WPF app lifecycle ideas | local Whisper.cpp pipeline, clipboard auto-insert, WAV-on-disk flow, single-hotkey limitation, out-of-range hotkey IDs |
| Phlox | MIT © 2024 Filipe Gonsalves | FastAPI route/middleware organization, template-driven clinical documentation concepts, unified OpenAI-compatible LLM client, JSON-repair for local models, upload-transcribe endpoint shape | Tauri/React frontend, SQLite config-manager persistence, scheduler-based cleanup |
| Open Medical Scribe | MIT © 2025 Birger Moell | batch-vs-streaming provider families, provider factories + result adapter idea, prompt framing (grounding + warnings + follow-up questions), JSONL audit logging, VAD-windowed local streaming plan | Node/Express runtime (backend must be Python-native), Electron process-spawning of llama.cpp, redact-then-cloud privacy posture |
| Multi-Model-Gateway | MIT (README §License) | pure routing decision function separated from IO, descriptor→adapter registry, masked key display, sliding-window rate limiting with degrade-open note, Fernet secret storage plan | agents/research/images feature surface, arq chat queues (WS streams instead), OpenRouter coupling |

## Files carrying adapted patterns (attribution headers in source)

- `client/MedicalScribe.WPF/Audio/AudioContracts.cs` — audio format + level
  events (SpeakType).
- `client/MedicalScribe.WPF/Hotkeys/Hotkeys.cs` — Win32 hotkeys (SpeakType,
  extended).
- `client/MedicalScribe.WPF/Settings/AppSettings.cs` — settings store (SpeakType,
  retargeted).
- `client/MedicalScribe.WPF/Infrastructure/FileLogger.cs` — log-file pattern
  (SpeakType, hardened: rotation + redaction backstop).
- `ai/registry.py` — descriptor/factory registry (MMG `provider_registry.py`,
  OMS factories).
- `ai/router/router.py` — pure heuristic router (MMG `provider_router.py`
  `resolve_role` idea, generalized + privacy wall).
- `ai/router/health.py` — demotion/half-open policy (MMG metrics/health
  concepts).
- `backend/api/services/audit.py` — JSONL audit (OMS `auditLogger.js`, with
  deny-list added).
- `backend/api/schemas/providers.py` masked-config policy (MMG `mask_key`).
- `ai/llm/openai_compat.py` unified OpenAI-compat client + SSE (Phlox
  `llm_client`, extended with retries).

## Downloaded AI models (Model hub — see docs/MODELS.md)

Model weights are **not** vendored in this repository; they are downloaded at
runtime from the pinned HuggingFace sources below (sha256-pinned in
`backend/api/services/model_catalog.py`). Deployments must honor each
model's license:

| Model (catalog id) | Source repo | License | Notes |
|---|---|---|---|
| Whisper large-v3-turbo Q4_0 (`whisper-large-v3-turbo`) | `Xviers/whisper-large-v3-turbo-GGUF` | MIT | quantized GGUF conversion of OpenAI Whisper large-v3-turbo (OpenAI Whisper is MIT) |
| Shenava Koochik v1.0 (`shenava-koochik`) | `Reza2kn/Shenava-Koochik-v1.0-tract-streaming` | Apache-2.0 | the `mah92/sherpa-onnx-nemo-ctc-fa-shenava-koochik-…` repackaging is CC-BY-NC-4.0 and deliberately NOT used |
| MiniCPM5 2B Q4_K_M (`minicpm5-2b`) | `openbmb/MiniCPM5-2B-GGUF` | Apache-2.0 | OpenBMB MiniCPM5 |
| Jibay 2 Q4_K_M (`jibay-2`) | `JibayAi/Jibay_2_GGUF_Q4-K-M` | Apache-2.0 | JibayAi |
| OpenMed Persian PII TookaBERT-Large INT4 (`persian-pii-tookabert`) | `Reza2kn/openmed-persian-pii-tookabert-large-onnx-int4` | CC-BY-4.0 | attribution required; derived from TookaBERT (Apache-2.0) |

## Local AI runtime dependencies (`local-ai` extra)

| Package | License | Use |
|---|---|---|
| sherpa-onnx | Apache-2.0 | in-process Shenava STT (NeMo FastConformer CTC streaming) |
| onnxruntime | MIT | in-process PII NER inference (INT4 quantized graphs on CPU) |
| tokenizers | Apache-2.0 | NER tokenization (Rust tokenizers, Python bindings) |

## License texts (required attribution)

### MIT License (applies to all four references above)

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Full texts: `phlox/LICENSE`, `open-medical-scribe/LICENSE`,
`speaktype/installer/LICENSE.txt`, `Multi-Model-Gateway/README.md` (§License: MIT).

## Third-party runtime dependencies (client & server)

WPF client (Phase 1): `CommunityToolkit.Mvvm` (MIT),
`Microsoft.Extensions.DependencyInjection` (MIT). Phase 2 adds `NAudio` (MIT).
Server: FastAPI (MIT), Pydantic (MIT), uvicorn (MIT/BSD), httpx (BSD-3),
PyJWT (MIT); Phase 3/7 add SQLAlchemy (MIT), alembic (MIT), asyncpg (PostgreSQL
license), redis-py (MIT), argon2-cffi (MIT). Model weights fetched later
(e.g. GGUF) carry their own licenses — see `models/README.md`; weights are
never committed.

## Algorithms adapted from public-domain formulations

- `backend/api/services/validation.py::jalali_to_gregorian` — the classic
  Jalali→Gregorian day-number arithmetic (public-domain formulation used
  unchanged across jalaali-js and countless ports). Adapted, not copied from
  any single project; no license encumbrance.

Medical-device posture: none of the reference code grants any regulatory
claim; MedicalScribe is assistive documentation software with mandatory human
review (spec §8, §19.18) and must not be characterized as autonomous diagnosis.
