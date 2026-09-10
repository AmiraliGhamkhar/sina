from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from app.db import Base
import uuid

class MemoryFile(Base):
    __tablename__= "memory_files"
    id= Column(UUID, primary_key= True, default= uuid.uuid4)
    user_id= Column(UUID, ForeignKey("users.id", ondelete= "CASCADE"), nullable= False)
    path= Column(String(512), nullable= False)
    description= Column(String(512), nullable= False)
    aliases= Column(ARRAY(String), nullable= False, server_default= "{}")
    content= Column(Text, nullable= False, server_default= "")
    version= Column(Integer, nullable= False, server_default= "1")
    size_bytes= Column(Integer, nullable= False, server_default= "0")
    sources= Column(String(64), nullable= True)
    updated_at= Column(DateTime, nullable= False, server_default= text("now()"))

    __table_args__ = (
        UniqueConstraint("user_id", "path", name="uq_memory_files_user_path"),
    )
