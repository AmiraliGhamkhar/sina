"""Provider health tracking with failure thresholds and half-open recovery.

Concept adapted from Multi-Model-Gateway's health/metrics plumbing, reduced to
an in-process tracker (Phase 5 promotes it to Redis so all API workers share
one view). The tracker is clock-injectable and side-effect free, which keeps
fallback policy unit-testable without network calls.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


@dataclass
class ProviderState:
    healthy: bool = True
    consecutive_failures: int = 0
    last_failure_at: float | None = None
    last_latency_ms: float | None = None
    total_failures: int = 0
    total_successes: int = 0


@dataclass
class HealthTracker:
    """Down after ``failure_threshold`` consecutive failures; half-open retry
    after ``cooldown_s`` so a recovered provider rejoins routing without a
    restart. Unknown providers are optimistically healthy (first real probe
    corrects them)."""

    failure_threshold: int = 3
    cooldown_s: float = 30.0
    clock: Callable[[], float] = field(default=time.monotonic)
    _states: dict[str, ProviderState] = field(default_factory=dict)

    def state(self, name: str) -> ProviderState:
        return self._states.setdefault(name, ProviderState())

    def is_healthy(self, name: str) -> bool:
        state = self.state(name)
        if state.healthy:
            return True
        # half-open: allow the provider back once the cooldown elapsed
        if state.last_failure_at is not None:
            if (self.clock() - state.last_failure_at) >= self.cooldown_s:
                state.healthy = True
                state.consecutive_failures = 0
                return True
        return False

    def record_success(self, name: str, latency_ms: float | None = None) -> None:
        state = self.state(name)
        state.healthy = True
        state.consecutive_failures = 0
        state.total_successes += 1
        if latency_ms is not None:
            state.last_latency_ms = latency_ms

    def record_failure(self, name: str) -> None:
        state = self.state(name)
        state.consecutive_failures += 1
        state.total_failures += 1
        state.last_failure_at = self.clock()
        if state.consecutive_failures >= self.failure_threshold:
            state.healthy = False

    def snapshot(self) -> Mapping[str, dict]:
        return {
            name: {
                "healthy": self.is_healthy(name),
                "consecutive_failures": s.consecutive_failures,
                "total_failures": s.total_failures,
                "total_successes": s.total_successes,
                "last_latency_ms": s.last_latency_ms,
            }
            for name, s in sorted(self._states.items())
        }

    def healthiness_map(self) -> dict[str, bool]:
        return {name: self.is_healthy(name) for name in sorted(self._states)}
