from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector
from app.db import Base
import uuid, datetime

class Memory(Base):
    __tablename__= "memories"
    id= Column(UUID, primary_key= True, default= uuid.uuid4)
    conversation_id= Column(UUID, ForeignKey("conversations.id", ondelete="CASCADE"))
    content= Column(Text, nullable= False)
    role= Column(String, nullable= False)
    embedding= Column(Vector(768), nullable= False)
    created_at= Column(DateTime, default=datetime.datetime.utcnow)