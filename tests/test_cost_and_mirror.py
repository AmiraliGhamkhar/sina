"""Cost-ledger + Redis health-mirror units (Phase 5)."""
from __future__ import annotations

from datetime import date

from api.services.cost import CostLedger
from api.services.health_mirror import HEALTH_KEY, QUEUE_DEPTH_KEY, HealthMirror

from ai.router.health import HealthTracker

# -- ledger ---------------------------------------------------------------


def _fixed_day(day: date):
    holder = {"today": day}
    return holder, lambda: holder["today"]


def test_ledger_counts_and_exhausts_on_the_edge():
    ledger = CostLedger(budget_tokens_per_day=100)
    assert ledger.exhausted is False
    ledger.add_usage("mock", 60)
    assert ledger.exhausted is False
    ledger.add_usage("mock", 40)  # exactly at budget => hard edge soft-stop
    assert ledger.exhausted is True
    snap = ledger.snapshot()
    assert snap["tokens_today"] == 100
    assert snap["by_provider"] == {"mock": 100}


def test_ledger_rolls_over_daily():
    holder, clock = _fixed_day(date(2026, 9, 11))
    ledger = CostLedger(budget_tokens_per_day=50, clock=clock)
    ledger.add_usage("gpt", 50)
    assert ledger.exhausted
    holder["today"] = date(2026, 9, 12)
    assert ledger.exhausted is False
    assert ledger.snapshot()["tokens_today"] == 0


def test_ledger_disabled_never_blocks_and_ignores_junk():
    ledger = CostLedger(budget_tokens_per_day=0)
    ledger.add_usage("gpt", 10**9)
    ledger.add_usage("gpt", -5)
    assert ledger.exhausted is False
    assert ledger.snapshot()["budget_tokens_per_day"] is None


# -- mirror (fake KV, no redis driver needed) --------------------------------


class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.store: dict[str, str] = {}
        self.fail = fail

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        self.store[key] = value


class _NoMetrics:
    def incr(self, *a, **k):
        return None

    def observe_ms(self, *a, **k):
        return None


def _mirror(tracker, client):
    return HealthMirror(
        tracker=tracker,
        metrics=_NoMetrics(),
        url="redis://fake",
        interval_s=1,
        client=client,
    )


def test_publish_then_hydrate_shares_demotion():
    async def scenario():
        src_tracker = HealthTracker(failure_threshold=1)
        src_tracker.record_failure("stt:dead")
        src = _mirror(src_tracker, fake := _FakeRedis())
        assert await src.publish() is True
        assert fake.store[HEALTH_KEY]  # published JSON

        peer_tracker = HealthTracker(failure_threshold=99)  # pristine locally
        peer = _mirror(peer_tracker, fake)
        absorbed = await peer.hydrate()
        assert absorbed == 1
        assert peer_tracker.is_healthy("stt:dead") is False  # demotion shared

    import asyncio

    asyncio.run(scenario())


def test_mirror_degrades_silently_when_redis_is_down():
    import asyncio

    async def scenario():
        tracker = HealthTracker()
        mirror = _mirror(tracker, _FakeRedis(fail=True))
        assert await mirror.publish() is False  # no raise, no crash
        assert await mirror.hydrate() == 0

    asyncio.run(scenario())


def test_mirror_disabled_without_url():
    import asyncio

    async def scenario():
        mirror = HealthMirror(
            tracker=HealthTracker(),
            metrics=_NoMetrics(),
            url="",
            interval_s=0,
            client=_FakeRedis(),
        )
        await mirror.start()  # must no-op
        assert mirror._task is None
        assert await mirror.publish() is False

    asyncio.run(scenario())


def test_queue_depth_key_published():
    import asyncio

    async def scenario():
        fake = _FakeRedis()
        mirror = _mirror(HealthTracker(), fake)
        await mirror.publish()
        assert fake.store[QUEUE_DEPTH_KEY] == "0"  # no live hubs in this test

    asyncio.run(scenario())

