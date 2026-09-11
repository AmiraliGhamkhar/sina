# Third-party notices & attribution

This project is **clean-room inspired** by four open-source repositories,
vendored at the repository root as reference checkouts for architecture study.
No source file was copied verbatim; several *patterns* were adapted and are
marked where they occur. All reference licenses are permissive; where
adaptation is substantial the file header carries a pointer back here.

## Reference repositories

| Repo (checkout) | License | What we took | What we rejected |
|---|---|---|---|
| SpeakType (`speaktype/`) | MIT © 2026 "SpeakType" authors (`installer/LICENSE.txt`) | Windows audio-capture format (16 kHz mono PCM16, RMS level events), NAudio usage shape, global hotkey (RegisterHotKey/WM_HOTKEY), JSON settings store pattern, overlay-less WPF app lifecycle ideas | local Whisper.cpp pipeline, clipboard auto-insert, WAV-on-disk flow, single-hotkey limitation, out-of-range hotkey IDs |
| Phlox (`phlox/`) | MIT © 2024 Filipe Gonsalves | FastAPI route/middleware organization, template-driven clinical documentation concepts, unified OpenAI-compatible LLM client, JSON-repair for local models, upload-transcribe endpoint shape | Tauri/React frontend, SQLite config-manager persistence, scheduler-based cleanup |
| Open Medical Scribe (`open-medical-scribe/`) | MIT © 2025 Birger Moell | batch-vs-streaming provider families, provider factories + result adapter idea, prompt framing (grounding + warnings + follow-up questions), JSONL audit logging, VAD-windowed local streaming plan | Node/Express runtime (backend is Python-native), Electron process-spawning of llama.cpp, redact-then-cloud privacy posture |
| Multi-Model-Gateway (`Multi-Model-Gateway/`) | MIT (README §License) | pure routing decision function separated from IO, descriptor→adapter registry, masked key display, sliding-window rate limiting with degrade-open note, Fernet secret storage plan | agents/research/images feature surface, arq chat queues (WS streams instead), OpenRouter coupling |

## Files carrying adapted patterns (attribution headers in source)

| File | Adapted from |
|---|---|
| `client/MedicalScribe.WPF/Audio/AudioContracts.cs` | SpeakType — audio format + level events |
| `client/MedicalScribe.WPF/Hotkeys/Hotkeys.cs` | SpeakType — Win32 hotkeys (extended) |
| `client/MedicalScribe.WPF/Settings/AppSettings.cs` | SpeakType — settings store (retargeted) |
| `client/MedicalScribe.WPF/Infrastructure/FileLogger.cs` | SpeakType — log-file pattern (hardened: rotation + redaction backstop) |
| `ai/registry.py` | MMG `provider_registry.py`, OMS factories — descriptor/factory registry |
| `ai/router/router.py` | MMG `provider_router.py` `resolve_role` idea — pure heuristic router (generalized + privacy wall) |
| `ai/router/health.py` | MMG metrics/health concepts — demotion/half-open policy |
| `backend/api/services/audit.py` | OMS `auditLogger.js` — JSONL audit (deny-list added) |
| `backend/api/schemas/providers.py` | MMG `mask_key` — masked-config policy |
| `ai/llm/openai_compat.py` | Phlox `llm_client` — unified OpenAI-compat client + SSE (extended with retries) |

## Downloaded AI models (model hub — see `docs/MODELS.md`)

Model weights are **not** vendored in this repository; they are downloaded at
runtime from the pinned HuggingFace sources below (sha256-pinned in
`backend/api/services/model_catalog.py`). Deployments must honor each model's
license.

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
`speaktype/installer/LICENSE.txt`, `Multi-Model-Gateway/README.md` (§License:
MIT).

## Third-party runtime dependencies (client & server)

WPF client: `CommunityToolkit.Mvvm` (MIT),
`Microsoft.Extensions.DependencyInjection` (MIT), `NAudio` (MIT).
Server (licenses per the installed package metadata): FastAPI (MIT),
Pydantic (MIT), uvicorn (BSD-3), httpx (BSD-3), PyJWT (MIT),
python-multipart (Apache-2.0), websockets (BSD-3), anyio (MIT), SQLAlchemy
(MIT), alembic (MIT), asyncpg (Apache-2.0), redis-py (MIT), argon2-cffi (MIT),
cryptography (Apache-2.0 or BSD-3). Model weights fetched at runtime (e.g.
GGUF) carry their own licenses — see `docs/MODELS.md`; weights are never
committed.

## Algorithms adapted from public-domain formulations

- `backend/api/services/validation.py::jalali_to_gregorian` — the classic
  Jalali→Gregorian day-number arithmetic (public-domain formulation used
  unchanged across jalaali-js and countless ports). Adapted, not copied from
  any single project; no license encumbrance.

**Medical-device posture:** none of the reference code grants any regulatory
claim; MedicalScribe is assistive documentation software with mandatory human
review (spec §8, §19.18) and must not be characterized as autonomous
diagnosis.
