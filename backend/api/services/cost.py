"""Daily cost budget guard (Phase 5 router hardening).

Process-local token accounting keyed by UTC-ish calendar day (injectable
clock for tests). One LLM completion (or a repaired retry) adds its usage;
when the day's total reaches ``budget_tokens`` the routing layer receives
``cloud_excluded=True`` — a *soft stop*: cloud spend halts, local providers
keep serving so dictation/note workflows never fully die mid-day.

The ledger is the fast path the router consults; durable spend/usage history
lives in the Phase 7 ``ai_requests`` table. Phase 8 adds boot-time
``backfill()`` from that table, so a restart mid-day no longer resets the
budget (fail-open only while the DB is unreachable at boot).
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

    def backfill(self, by_provider: dict[str, int]) -> None:
        """Phase 8: seed today's totals from the durable ``ai_requests`` table
        (boot-time reload — a restart no longer resets the day's budget)."""
        self._roll()
        for provider, tokens in by_provider.items():
            if tokens > 0:
                self._total += int(tokens)
                self._by_provider[provider] += int(tokens)

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
