"""LLM provider subpackage.

Planned members:
- openai_compat.py  — shared OpenAI-compatible HTTP transport (+ retries, SSE)
- llama_server.py   — self-hosted llama.cpp server adapter (this phase)
- mock.py           — deterministic provider for tests and Phase 1 (this)
- openai.py, anthropic.py, gemini.py — added in Phase 4 behind the registry

Cloud providers are thin subclasses/compositions over ``openai_compat`` only
where their APIs are genuinely OpenAI-compatible; native APIs (Anthropic
messages, Gemini generateContent) get their own adapter in Phase 4.
"""
