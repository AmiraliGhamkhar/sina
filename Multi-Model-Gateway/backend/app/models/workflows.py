from sqlalchemy import Column, String, Text, DateTime, ForeignKey 
from sqlalchemy.dialects.postgresql import UUID, JSONB
from app.db import Base
import uuid, datetime

class Workflow(Base):
    __tablename__="workflows"
    id= Column(UUID, primary_key=True, default=uuid.uuid4)
    user_id= Column(UUID, ForeignKey("users.id", ondelete="CASCADE"))
    name= Column(String, nullable=False)
    description= Column(Text)
    graph= Column(JSONB, nullable=False)
    param_map= Column(JSONB, nullable=True)
    created_at= Column(DateTime, default=datetime.datetime.utcnow)



