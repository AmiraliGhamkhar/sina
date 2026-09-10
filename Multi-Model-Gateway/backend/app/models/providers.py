from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime


class Provider(Base):
    """A user-configured LLM provider (bring-your-own-key).

    The API key is stored encrypted (Fernet) in api_key_encrypted — never
    plaintext. type is one of: openai_compatible, openai, anthropic, google,
    openrouter. role is one of: local, cloud. Existing env-var behavior is
    preserved by seeding these rows from settings (see
    services/provider_registry.seed_default_providers).
    """
    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_providers_user_name"),
    )
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    type = Column(String(32), nullable=False)
    role = Column(String(16), nullable=False)
    base_url = Column(String(512), nullable=True)
    api_key_encrypted = Column(Text, nullable=True)
    default_model = Column(String(128), nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
