from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey 
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime




class RefreshToken(Base):
    __tablename__= "refresh_tokens"
    id= Column(UUID, primary_key=True, default=uuid.uuid4)
    token_hash= Column(String, nullable=False, unique=True, index=True)
    user_id= Column(UUID, ForeignKey("users.id", ondelete="CASCADE"))
    created_at= Column(DateTime, default= datetime.datetime.utcnow)
    expires_at= Column(DateTime)
    # client-provided device binding for refresh replay protection
    device_id= Column(String(128), nullable=True)
