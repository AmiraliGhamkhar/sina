from sqlalchemy import Column, String, Integer, DateTime, ForeignKey 
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    parent_id = Column(UUID, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    branched_from_message_id = Column(UUID, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(UUID, ForeignKey("users.id", ondelete="CASCADE" ))
    title= Column(String)
    created_at= Column(DateTime, default=datetime.datetime.utcnow)
    token_count= Column(Integer, default=0 )
    agent_id = Column(UUID, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_version = Column(Integer, nullable=True)

    

    