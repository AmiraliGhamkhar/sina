from app.core.config import settings, get_openrouter_api_key
from pydantic import BaseModel, Field
from typing import List, Optional
from openai import AsyncOpenAI

from enum import Enum


class Provider(str, Enum):
    auto = "auto"
    local = "local"
    openrouter = "openrouter"


class ChatMessage(BaseModel):
    role: str
    # bound the message size so an unbounded payload can't OOM the process (issues.md MED-6)
    content: str = Field(max_length=100_000)


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    preset_id: Optional[str] = None
    messages: List[ChatMessage] = Field(min_length=1)
    model: str = "auto"
    stream: bool = True
    provider: Provider = Provider.auto
    private: bool = False
    provider_id: Optional[str] = None  # pin a specific configured provider row; overrides all routing heuristics
    agent_id: Optional[str] = None  # run as this agent; when set, agent's allowed_tools + system_prompt win (ADR-0004)
    agent_version: Optional[int] = None  # pinned version when using a marketplace agent


def get_local_client():
    return AsyncOpenAI(
        base_url=f"{settings.LM_URL}/v1",
        api_key="LM-STUDIO",
    )


def get_openrouter_client():
    key = get_openrouter_api_key()
    if not key:
        raise RuntimeError("OpenRouter is not configured (no API key)")
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
    )
