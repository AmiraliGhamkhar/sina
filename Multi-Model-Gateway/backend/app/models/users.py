from sqlalchemy import Column, String, Boolean, DateTime 
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime


class User(Base):
    __tablename__= "users"
    id = Column(UUID, primary_key=True, default=uuid.uuid4)   
    email = Column(String, unique=True, index=True, nullable=False )
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default= datetime.datetime.utcnow)
    is_active = Column(Boolean, default=True)
    last_active = Column(DateTime, nullable=True)





