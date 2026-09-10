"""Mock LLM provider for tests and the Phase 1 vertical slice.

Produces a deterministic JSON "draft note" so the report pipeline, validation
rules, and the WPF editor can be developed against the mock before any real
model exists. Output is grounded *only* in the supplied transcript, exactly
like the real providers must be (docs/ARCHITECTURE.md §Medical Safety).
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ai.base import (
    LLMCompletion,
    LLMMessage,
    LLMProvider,
    PrivacyClass,
    ProviderCapabilities,
)


class MockLlmProvider(LLMProvider):
    name = "mock"

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._fail_with: str | None = cfg.get("fail_with")
        self._capabilities = ProviderCapabilities(
            privacy=PrivacyClass.LOCAL,
            supports_streaming=True,
            latency_hint_ms=1,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion:
        if self._fail_with:
            from ai.base import ProviderError

            raise ProviderError(self._fail_with, provider=self.name)
        user_text = next((m.content for m in reversed(messages) if m.role == "user"), "")
        draft = {
            "sections": {
                "subjective": user_text.strip() or "unknown",
                "objective": "",
                "assessment": "missing",
                "plan": "missing",
            },
            "warnings": ["mock-provider: output is canned, not clinically verified"],
        }
        return LLMCompletion(
            text=json.dumps(draft, ensure_ascii=False),
            model=model or "mock",
            usage=None,
        )
