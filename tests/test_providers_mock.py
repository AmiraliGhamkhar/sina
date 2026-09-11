"""HealthTracker + mock provider behavior (async bits via asyncio.run)."""
from __future__ import annotations

import asyncio
import json

import pytest

from ai.base import ProviderError, ProviderUnavailableError, STTRequest
from ai.router.health import HealthTracker
from ai.stt.mock import DEFAULT_SCRIPT, MockSttProvider

# -- health tracker -----------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_down_after_threshold_back_after_cooldown():
    clock = FakeClock()
    tracker = HealthTracker(failure_threshold=3, cooldown_s=30.0, clock=clock)
    for _ in range(2):
        tracker.record_failure("stt:mock")
    assert tracker.is_healthy("stt:mock") is True  # not yet at threshold
    tracker.record_failure("stt:mock")
    assert tracker.is_healthy("stt:mock") is False
    clock.t += 29.0
    assert tracker.is_healthy("stt:mock") is False  # still cooling down
    clock.t += 2.0
    assert tracker.is_healthy("stt:mock") is True  # half-open → try again


def test_success_resets_failures_and_latency():
    tracker = HealthTracker(failure_threshold=2)
    tracker.record_failure("p")
    tracker.record_success("p", latency_ms=42.0)
    tracker.record_failure("p")  # single failure after reset must not mark down
    assert tracker.is_healthy("p") is True
    snap = tracker.snapshot()["p"]
    assert snap["last_latency_ms"] == 42.0
    assert snap["total_failures"] == 2 and snap["total_successes"] == 1


def test_unknown_provider_optimistically_healthy():
    assert HealthTracker().is_healthy("never-seen") is True


# -- mock STT ------------------------------------------------------------------


def test_mock_transcribe_returns_scripted_finals():
    stt = MockSttProvider({})
    segments = asyncio.run(stt.transcribe(STTRequest(audio=b"")))
    assert len(segments) == len(DEFAULT_SCRIPT)
    assert all(s.is_final for s in segments)
    joined = " ".join(s.text for s in segments)
    for term in ("ECG", "MRI", "hypertension", "no effusion"):
        assert term in joined, f"{term} must survive round-trip"


def test_mock_stream_yields_interims_then_finals_in_order():
    async def _fake_audio():
        for _ in range(3):
            yield b"\x00\x00"

    stt = MockSttProvider({"interim_delay_s": 0})
    out = asyncio.run(_collect(stt.stream(_fake_audio())))
    finals = [s for s in out if s.is_final]
    interims = [s for s in out if not s.is_final]
    assert len(finals) == len(DEFAULT_SCRIPT)
    assert interims, "interim hypotheses are the whole point of streaming"
    # every interim grows strictly and is a word-prefix of the scripted final
    finals_by_start = {f.start_ms: f.text for f in finals}
    last_len: dict[int, int] = {}
    for interim in interims:
        final_text = finals_by_start[interim.start_ms]
        assert final_text.startswith(interim.text), (final_text, interim.text)
        assert len(interim.text) > last_len.get(interim.start_ms, 0)
        last_len[interim.start_ms] = len(interim.text)
    for seg in finals:
        assert seg.end_ms >= seg.start_ms


async def _collect(agen) -> list:
    return [s async for s in agen]


def test_mock_fail_injection():
    failing = MockSttProvider({"fail_with": "boom"})
    with pytest.raises(ProviderError):
        asyncio.run(failing.transcribe(STTRequest(audio=b"")))
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(failing.health())


def test_mock_llm_output_is_json_and_grounds_on_user_text():
    from ai.base import LLMMessage
    from ai.llm.mock import MockLlmProvider

    llm = MockLlmProvider({})
    result = asyncio.run(
        llm.complete(
            [
                LLMMessage(role="system", content="grounded drafting"),
                LLMMessage(role="user", content="10 mg warfarin, right knee pain, no effusion"),
            ],
            json_mode=True,
        )
    )
    draft = json.loads(result.text)
    subjective = draft["sections"]["subjective"]
    # numbers / laterality / negation preserved verbatim — mock can't "improve" them
    assert "10 mg" in subjective and "right" in subjective and "no effusion" in subjective
    assert draft["sections"]["assessment"] == "[[MISSING]]"
    # Phase 4: usage metering is exercised even by the mock
    assert result.usage is not None and result.usage.total_tokens > 0
