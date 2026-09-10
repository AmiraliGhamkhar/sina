"""Unit tests for app.core.stream_guard (per-user SSE stream cap).

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): the guard is an in-memory counter guarded by an asyncio.Lock. If the
module can't be imported in this environment (missing settings/secret deps),
the whole suite skips cleanly.
"""
import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

try:
    from app.core import stream_guard
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    stream_guard = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class StreamGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if stream_guard is None:
            raise unittest.SkipTest(
                f"app.core.stream_guard import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        # one loop per test; state is reset in tearDown so nothing leaks across
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        # release every slot still held, whatever the test left behind
        while stream_guard._active:
            for user_id in list(stream_guard._active.keys()):
                self.loop.run_until_complete(stream_guard.release_stream_slot(user_id))
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_acquire_then_release(self):
        async def scenario():
            await stream_guard.acquire_stream_slot("alice")
            self.assertEqual(stream_guard._active.get("alice"), 1)
            await stream_guard.release_stream_slot("alice")
            self.assertNotIn("alice", stream_guard._active)

        self.loop.run_until_complete(scenario())

    def test_exceeding_max_raises_429(self):
        async def scenario():
            with patch("app.core.stream_guard.settings.MAX_CONCURRENT_STREAMS", 1):
                await stream_guard.acquire_stream_slot("bob")
                with self.assertRaises(HTTPException) as ctx:
                    await stream_guard.acquire_stream_slot("bob")
                self.assertEqual(ctx.exception.status_code, 429)
                self.assertIn("too many concurrent streams", str(ctx.exception.detail))
                # the failed acquire must not have bumped the counter
                self.assertEqual(stream_guard._active.get("bob"), 1)

        self.loop.run_until_complete(scenario())

    def test_release_then_acquire_works_again(self):
        async def scenario():
            with patch("app.core.stream_guard.settings.MAX_CONCURRENT_STREAMS", 1):
                await stream_guard.acquire_stream_slot("carol")
                with self.assertRaises(HTTPException):
                    await stream_guard.acquire_stream_slot("carol")
                await stream_guard.release_stream_slot("carol")
                # after a release the same user may acquire again
                await stream_guard.acquire_stream_slot("carol")
                self.assertEqual(stream_guard._active.get("carol"), 1)

        self.loop.run_until_complete(scenario())

    def test_release_is_stable_for_unconditional_finally(self):
        """Regression: the agent stream generator releases unconditionally in
        its `finally`, including on paths where the acquire happened in the
        router but the generator never ran (or ran and mid-raised). Releasing
        when no slot is held must be a no-op, never negative — so an
        unconditional release cannot double-free a slot."""
        async def scenario():
            # Release with nothing held: must not go negative or resurrect a key.
            await stream_guard.release_stream_slot("eve")
            self.assertNotIn("eve", stream_guard._active)

            # Acquire once, release twice (the generator's finally + a stray
            # call): the second release must still leave the user free to reuse.
            with patch("app.core.stream_guard.settings.MAX_CONCURRENT_STREAMS", 1):
                await stream_guard.acquire_stream_slot("eve")
                await stream_guard.release_stream_slot("eve")
                await stream_guard.release_stream_slot("eve")
                await stream_guard.acquire_stream_slot("eve")
                self.assertEqual(stream_guard._active.get("eve"), 1)

        self.loop.run_until_complete(scenario())

    def test_release_never_goes_negative(self):
        async def scenario():
            # releasing with nothing held is a no-op, not a negative count
            await stream_guard.release_stream_slot("dave")
            await stream_guard.release_stream_slot("dave")
            self.assertNotIn("dave", stream_guard._active)

            await stream_guard.acquire_stream_slot("dave")
            await stream_guard.release_stream_slot("dave")
            # an extra release after the counter hit zero must stay at zero
            await stream_guard.release_stream_slot("dave")
            self.assertNotIn("dave", stream_guard._active)

        self.loop.run_until_complete(scenario())

    def test_counters_are_per_user(self):
        async def scenario():
            with patch("app.core.stream_guard.settings.MAX_CONCURRENT_STREAMS", 1):
                await stream_guard.acquire_stream_slot("erin")
                # a different user is unaffected by erin holding her slot
                await stream_guard.acquire_stream_slot("frank")
                self.assertEqual(stream_guard._active.get("erin"), 1)
                self.assertEqual(stream_guard._active.get("frank"), 1)

        self.loop.run_until_complete(scenario())


if __name__ == "__main__":
    unittest.main()
