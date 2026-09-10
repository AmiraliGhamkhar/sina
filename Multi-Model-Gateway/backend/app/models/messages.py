from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime

class Message(Base):
    __tablename__="messages"
    id= Column(UUID, primary_key=True, default=uuid.uuid4)
    conversation_id= Column(UUID, ForeignKey("conversations.id", ondelete="CASCADE"))
    role= Column(String, nullable=False)
    content= Column(Text)
    created_at= Column(DateTime, default=datetime.datetime.utcnow)
    model_used= Column(String, nullable=True)
    tokens_used= Column(Integer, default=0)
    # exact | chunk_count | null — how tokens_used was derived (R3: local LM Studio
    # omits usage, so counts start as chunk_count and get overwritten to exact by
    # the off-path /v1/tokenize/encode sync; null means legacy rows).
    token_provenance= Column(String(16), nullable=True)
    # NOT NULL + unique per conversation: the exchange invariant (user=k, assistant=k+1)
    # depends on a total order; the DB now enforces what save_messages assumes (issues.md CR-9/DEV-2).
    index = Column(Integer, nullable=False)
    __table_args__ = (
        UniqueConstraint("conversation_id", "index", name="uq_messages_conversation_index"),
    )