"""Daily cost budget guard (Phase 5 router hardening).

Process-local token accounting keyed by UTC-ish calendar day (injectable
clock for tests). One LLM completion (or a repaired retry) adds its usage;
when the day's total reaches ``budget_tokens`` the routing layer receives
``cloud_excluded=True`` — a *soft stop*: cloud spend halts, local providers
keep serving so dictation/note workflows never fully die mid-day.

Deliberately in-memory: durable spend/usage history is the Phase 7
``ai_requests`` table's job (same fields, per the API.md note); the ledger is
only the fast path the router consults. A restart resets to zero — an
acceptable fail-open for a cost guard (never a privacy one).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date


@dataclass
class CostLedger:
    budget_tokens_per_day: int = 0  # 0/None => no cap (guard disabled)
    clock: Callable[[], date] = field(default=date.today)
    _day: date | None = field(default=None, init=False)
    _total: int = field(default=0, init=False)
    _by_provider: dict[str, int] = field(default_factory=lambda: defaultdict(int), init=False)

    def _roll(self) -> None:
        today = self.clock()
        if self._day != today:
            self._day = today
            self._total = 0
            self._by_provider.clear()

    def add_usage(self, provider: str, tokens: int) -> None:
        if tokens <= 0:
            return
        self._roll()
        self._total += tokens
        self._by_provider[provider] += tokens

    @property
    def enabled(self) -> bool:
        return bool(self.budget_tokens_per_day) and self.budget_tokens_per_day > 0

    @property
    def exhausted(self) -> bool:
        """Hard-edged on purpose: exactly-at-budget already soft-stops."""
        self._roll()
        return self.enabled and self._total >= self.budget_tokens_per_day

    def snapshot(self) -> dict:
        self._roll()
        return {
            "budget_tokens_per_day": self.budget_tokens_per_day or None,
            "tokens_today": self._total,
            "exhausted": self.exhausted,
            "by_provider": dict(sorted(self._by_provider.items())),
        }
