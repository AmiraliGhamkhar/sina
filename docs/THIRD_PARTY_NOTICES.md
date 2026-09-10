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

Medical-device posture: none of the reference code grants any regulatory
claim; MedicalScribe is assistive documentation software with mandatory human
review (spec §8, §19.18) and must not be characterized as autonomous diagnosis.
