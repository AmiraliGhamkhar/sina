"""STT provider subpackage.

Planned members (added in Phase 3, each behind the registry):
- local_whisper.py  — whisper.cpp / whisper-server HTTP adapter (local)
- qwen_asr.py       — Qwen ASR service adapter (local)
- speechmatics.py   — cloud STT with WebSocket streaming + context (fa/en)
- deepgram.py       — cloud STT with WebSocket streaming + keyword boosting
- mock.py           — deterministic provider used by tests and Phase 1 (this)
"""
