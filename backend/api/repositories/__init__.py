"""Repositories — the only modules holding SQLAlchemy sessions (docs/
ARCHITECTURE.md layering). One module per aggregate; all async.

Each repository takes an ``async_sessionmaker``; nothing imports the engine
singleton, so tests construct isolated databases trivially.
"""
from __future__ import annotations

from api.repositories.ai import AIProviderRepository, AIRequestRepository
from api.repositories.audit import AuditRepository
from api.repositories.encounters import EncounterRepository, PatientRepository
from api.repositories.reports import ReportRepository
from api.repositories.templates import TemplateRepository
from api.repositories.transcripts import TranscriptRepository
from api.repositories.users import UserRepository

__all__ = [
    "AIProviderRepository",
    "AIRequestRepository",
    "AuditRepository",
    "EncounterRepository",
    "PatientRepository",
    "ReportRepository",
    "TemplateRepository",
    "TranscriptRepository",
    "UserRepository",
]
