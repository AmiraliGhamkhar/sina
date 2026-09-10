from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from app.db import Base
import uuid, datetime

# lifecycle: queued then running to complete or failed or cancelled
RESEARCH_STATUSES = {"queued", "running", "complete", "failed", "cancelled"}


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    query = Column(Text, nullable=False)
    provider = Column(String, nullable=True)   # auto local or openrouter"
    model = Column(String, nullable=True)
    status = Column(String, nullable=False, default="queued")
    stage = Column(String, nullable=True)      # planning or searching or reading or synthesizing
    progress = Column(Integer, nullable=False, default=0)  # 0-100
    result = Column(Text, nullable=True)
    sources = Column(JSONB, nullable=True)     # [{n, title, url}]
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)
