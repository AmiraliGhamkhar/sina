from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.db import Base
import uuid, datetime


class Agent(Base):
    """User-created agent configuration.

    Composition of existing primitives: name/description/instructions,
    optional preset link, provider/model override, allowed tool list,
    iteration/budget tunables, visibility, and versioning for the
    versioned direct-shared marketplace (ADR-0001).
    """

    __tablename__ = "agents"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    preset_id = Column(UUID, ForeignKey("presets.id", ondelete="SET NULL"), nullable=True)
    provider = Column(String(32), nullable=True)
    model = Column(String(128), nullable=True)
    allowed_tools = Column(JSONB, nullable=False, default=list)
    max_iterations = Column(Integer, nullable=False, default=6)
    token_budget = Column(Integer, nullable=False, default=24000)
    is_public = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
