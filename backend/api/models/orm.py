"""SQLAlchemy ORM entities (Phase 7, spec §13).

Design rules:
- **String primary keys** with domain prefixes (`usr_`, `pat_`, `enc_`, …)
  and uuid4 bodies: portable across PostgreSQL/SQLite, matches the wire
  style already used by report ids, and avoids dialect-specific UUID types
  in migrations.
- **JSON columns** for structured payloads that are always read as a whole
  (template sections, report warnings/acks/events, audit payloads) — the
  service layer owns their shape; queries never filter inside them except
  audit's event/user top-level fields which are real columns.
- **No audio, no model binaries, no provider secrets in cleartext** — audio
  is never persisted (spec §13); provider secrets live only in the
  `ai_providers.secret_ciphertext` column (Fernet, spec §14).
- Timestamps are timezone-aware UTC datetimes.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# -- identity -------------------------------------------------------------------


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # 'admin', 'physician', …
    description: Mapped[str] = mapped_column(Text, default="")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list[User]] = relationship(back_populates="role_rel")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("usr"))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    #: argon2 hash — never the password, never returned by any query path
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role_id: Mapped[str] = mapped_column(String(32), ForeignKey("roles.id"), default="clinician")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    role_rel: Mapped[Role] = relationship(back_populates="users")


class RefreshToken(Base):
    """One-time refresh tokens: hash-stored, rotated, revocable (spec §14)."""

    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), index=True)  # sha256 hex
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device: Mapped[str] = mapped_column(String(128), default="")


# -- clinical domain ---------------------------------------------------------------


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("pat"))
    mrn: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(160))
    date_of_birth: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: free-text notes never auto-populated from AI output (spec §8)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    encounters: Mapped[list[Encounter]] = relationship(back_populates="patient")


class Encounter(Base):
    __tablename__ = "encounters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("enc"))
    patient_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("patients.id"), nullable=True, index=True
    )
    encounter_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed
    privacy_required: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    patient: Mapped[Patient | None] = relationship(back_populates="encounters")


class Transcript(Base):
    __tablename__ = "transcripts"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    encounter_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | completed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audio_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="transcript", cascade="all, delete-orphan"
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_id", "segment_id", name="uq_segment_per_transcript"),
        Index("ix_segments_transcript_position", "transcript_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("seg"))
    transcript_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("transcripts.session_id"), index=True
    )
    segment_id: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20), default="dictated")
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    transcript: Mapped[Transcript] = relationship(back_populates="segments")


# -- reports -------------------------------------------------------------------------


class ReportTemplate(Base):
    __tablename__ = "report_templates"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(32), default="custom")
    description: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[list] = mapped_column(JSON, default=list)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # soft delete (custom only)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Report(Base):
    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    encounter_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="fa-en")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    #: sections/section_titles/warnings (with ack state)/events are read as a
    #: whole by the lifecycle service — JSON keeps the round-trip exact
    sections: Mapped[dict] = mapped_column(JSON, default=dict)
    section_titles: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    privacy_override_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    phi_redaction_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    terminology_substitutions: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    amended_from: Mapped[str | None] = mapped_column(String(32), nullable=True)

    revisions: Mapped[list[ReportRevision]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ReportRevision(Base):
    """Append-only clinician-edit log (spec §5 step 7, §13)."""

    __tablename__ = "report_revisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rev"))
    report_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("reports.report_id"), index=True
    )
    action: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    report: Mapped[Report] = relationship(back_populates="revisions")


# -- AI plumbing -----------------------------------------------------------------------


class AIProvider(Base):
    """Registry mirror + optional encrypted secret storage (spec §14).

    Metadata (name/kind/privacy/capabilities) is seeded from env at startup;
    `secret_ciphertext` holds a Fernet-encrypted API key when an admin
    stores one via the admin API — never returned by any query path.
    """

    __tablename__ = "ai_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # f"{kind}:{name}"
    kind: Mapped[str] = mapped_column(String(8), index=True)  # stt | llm
    name: Mapped[str] = mapped_column(String(64))
    privacy_class: Mapped[str] = mapped_column(String(16))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AIRequest(Base):
    """Per-request AI usage ledger (cost/audit backfill source, spec §13/§17)."""

    __tablename__ = "ai_requests"
    __table_args__ = (Index("ix_ai_requests_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("air"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(8))  # stt | llm
    task: Mapped[str] = mapped_column(String(32))  # transcribe_stream | transcribe_batch | generate_note
    provider: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # ok | error
    routed_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    privacy_override: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encounter_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


# -- compliance --------------------------------------------------------------------------


class AuditLogRow(Base):
    """Append-only audit events (deny-listed payloads, no transcript text)."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_event_ts", "event", "created_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("aud"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Setting(Base):
    """Server-side key/value settings (terminology catalog, feature toggles)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


ALL_MODELS: tuple[type[Base], ...] = (
    Role,
    User,
    RefreshToken,
    Patient,
    Encounter,
    Transcript,
    TranscriptSegment,
    ReportTemplate,
    Report,
    ReportRevision,
    AIProvider,
    AIRequest,
    AuditLogRow,
    Setting,
)
