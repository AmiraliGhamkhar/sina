from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime


class ToolPermission(Base):
    """Per tenant tool grant/deny override.

    No row for a (user, tool) pair means:
    first party tools are allowed but MCP tools denied.
    """
    __tablename__ = "tool_permissions"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_name = Column(String, nullable=False)
    allowed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "tool_name", name="uq_tool_permissions_user_tool"),
    )
