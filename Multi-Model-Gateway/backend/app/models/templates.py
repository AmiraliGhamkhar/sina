from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db import Base
import uuid, datetime

class PromptTemplate(Base): 
    __tablename__= "prompt_templates"
    id= Column(UUID, primary_key= True, nullable=False, default= uuid.uuid4)
    user_id= Column(UUID, ForeignKey("users.id", ondelete= "CASCADE"))
    name= Column(String, nullable= False)
    description= Column(Text, nullable= True)
    structure= Column(Text, nullable= False)
    created_at= Column(DateTime, default= datetime.datetime.utcnow)


    