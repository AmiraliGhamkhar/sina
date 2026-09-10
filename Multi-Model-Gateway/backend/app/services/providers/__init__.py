"""Provider adapter protocol + concrete adapters.

Adapters wrap provider families behind a common async interface so the routing
engine can treat every provider uniformly. See base.py for the protocol.
"""
from .base import LLMProvider, StreamChunk
from .openai_compat import OpenAICompatProvider
from .openai import OpenAIProvider
from .openrouter import OpenRouterProvider
from .anthropic import AnthropicProvider
from .google import GoogleProvider

__all__ = [
    "LLMProvider",
    "StreamChunk",
    "OpenAICompatProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "AnthropicProvider",
    "GoogleProvider",
]
