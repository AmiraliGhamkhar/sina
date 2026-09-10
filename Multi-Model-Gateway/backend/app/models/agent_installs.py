from sqlalchemy import Column, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime


class AgentInstall(Base):
    """Versioned direct-shared install: pointer to same agent row.

    `pinned_version` is the version the installing user has explicitly
    upgraded to. Owner always sees latest; installers see banner when
    `pinned_version < agent.version` (ADR-0001).
    """

    __tablename__ = "agent_installs"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(UUID, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    pinned_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "agent_id", name="uq_agent_installs_user_agent"),
    )
