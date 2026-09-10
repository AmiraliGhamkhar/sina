from sqlalchemy import Column, String, Text, Integer, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from app.db import Base
import uuid, datetime

# preset  defaults, referenced by the
# column defaults below, the PresetCreate schema, the chat fallback, and the
# registration seeding, so these numbers live in exactly one place.

DEFAULT_SYSTEM_PROMPT= "You are a helpful AI assistant."
DEFAULT_TEMPERATURE= 0.8
DEFAULT_TOP_P= 0.95
DEFAULT_TOP_K= 40
DEFAULT_MIN_P= 0.05
DEFAULT_REPEAT_PENALTY= 1.10
DEFAULT_CONTEXT_OVERFLOW= "truncate_middle"


class Preset(Base):
    __tablename__= "presets"
    id= Column(UUID, primary_key= True, nullable=False, default= uuid.uuid4)
    user_id= Column(UUID, ForeignKey("users.id", ondelete= "CASCADE"))
    name= Column(String, nullable= False)
    system_prompt= Column(Text, nullable= True, default= DEFAULT_SYSTEM_PROMPT)
    temperature= Column(Float, default= DEFAULT_TEMPERATURE)
    token_limit= Column(Integer, nullable= True)
    context_overflow=  Column(String, nullable=True, default=DEFAULT_CONTEXT_OVERFLOW)
    stop_strings = Column(ARRAY(String), nullable=True)
    top_k= Column(Integer, nullable= True, default= DEFAULT_TOP_K)
    top_p= Column(Float, nullable= True, default= DEFAULT_TOP_P)
    min_p= Column(Float, nullable= True, default= DEFAULT_MIN_P)
    repeat_penalty= Column(Float, nullable= True, default= DEFAULT_REPEAT_PENALTY)
    created_at= Column(DateTime, default= datetime.datetime.utcnow)


    