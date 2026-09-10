"""MedicalScribe AI layer.

Provider contracts, registry, router and concrete adapters for speech-to-text
(STT) and large language models (LLM).

Boundaries (see docs/ARCHITECTURE.md):
- Only the FastAPI backend imports this package. The WPF client never talks to
  providers directly and never sees provider credentials.
- Providers are constructed centrally via :mod:`ai.registry`; call sites select
  providers by name through :mod:`ai.router` only.
- Secrets are read from server-side configuration and must never be serialized
  into API responses or logs.
"""

__all__ = ["base", "registry", "router", "stt", "llm"]
