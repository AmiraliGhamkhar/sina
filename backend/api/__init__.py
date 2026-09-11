"""MedicalScribe backend API package (FastAPI).

Layering inside ``api``:
- routes/     HTTP + WebSocket endpoints (validation + status codes only)
- services/   orchestration (sessions, transcription pipeline, reports)
- repositories/ persistence (SQLAlchemy, Phase 7)
- models/     ORM entities (Phase 7)
- schemas/    Pydantic wire contracts (REST + WS protocol v1)
- auth/       token + dependency plumbing
- db/         engine/session factories
- ai access   exclusively through the top-level ``ai`` package (registry/router)

No module here may import provider SDKs directly; everything is resolved via
:func:`ai.registry.build_default_registry` and :func:`ai.router.route`.
"""
