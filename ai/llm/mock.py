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
    LLMUsage,
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
        draft = self._grounded_draft(user_text)
        if draft is None:
            # legacy ad-hoc prompts (no report sentinels): canned SOAP shape
            draft = {
                "sections": {
                    "subjective": user_text.strip() or "unknown",
                    "objective": "",
                    "assessment": "[[MISSING]]",
                    "plan": "[[MISSING]]",
                },
                "warnings": ["mock-provider: output is canned, not clinically verified"],
            }
        text = json.dumps(draft, ensure_ascii=False)
        return LLMCompletion(
            text=text,
            model=model or "mock",
            usage=LLMUsage(
                prompt_tokens=max(1, len(user_text) // 4),
                completion_tokens=max(1, len(text) // 4),
                total_tokens=max(2, (len(user_text) + len(text)) // 4),
            ),
            meta={"mock": True},
        )

    @staticmethod
    def _grounded_draft(user_text: str) -> dict | None:
        """Honor the report-prompt contract (services/note_prompt.py): fill
        the FIRST template section with the transcript VERBATIM — nothing
        else — and mark every other section [[MISSING]]. The mock never
        invents content, which keeps it a valid stand-in for grounding tests
        (docs/ROADMAP.md Phase 4 acceptance)."""
        import re

        m = re.search(
            r"<<<TRANSCRIPT>>>\n(.*?)\n<<<END TRANSCRIPT>>>", user_text, re.DOTALL
        )
        t = re.search(
            r"<<<SECTIONS>>>\n(\[.*?\])\n<<<END SECTIONS>>>", user_text, re.DOTALL
        )
        if not (m and t):
            return None
        try:
            specs = json.loads(t.group(1))
        except json.JSONDecodeError:
            return None
        transcript = m.group(1).strip()
        sections: dict[str, str] = {}
        for i, spec in enumerate(specs if isinstance(specs, list) else []):
            sid = str(spec.get("id") or f"section_{i}")
            sections[sid] = transcript if i == 0 and transcript else "[[MISSING]]"
        return {"sections": sections, "warnings": []}
