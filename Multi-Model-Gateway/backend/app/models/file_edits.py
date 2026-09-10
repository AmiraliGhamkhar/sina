from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime


class FileEdit(Base):
    """Audit row for every mutating file operation across both stores.

    Covers `store='workspace'` (git-backed per-user-per-agent folder on the
    named volume) and `store='memory'` (DB-backed memory_files). Each write
    commits the workspace git repo and inserts one row; undo reverse-applies
    the patch and inserts a new row (ADR-0003).
    """

    __tablename__ = "file_edits"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(UUID, ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True)
    store = Column(String(16), nullable=False)  # 'workspace' | 'memory'
    path = Column(Text, nullable=False)
    patch = Column(Text, nullable=False)
    before_hash = Column(String(40), nullable=True)
    after_hash = Column(String(40), nullable=True)
    tool_call_id = Column(String(64), nullable=True)
    commit_sha = Column(String(40), nullable=True)  # git commit that holds this edit (deterministic undo; NULL for old rows)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
