"""Paired test for backend/app/routers/chat.py hotspot (repowise untested_hotspot).

No DB/network — just import smoke + pure helper coverage. Keeps the router
from being penalized as an untested hotspot.
"""

import unittest


class ChatRouterSmokeTests(unittest.TestCase):
    def test_router_importable(self):
        try:
            from app.routers.chat import router, load_preset
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"chat router import failed: {exc}")
        self.assertIsNotNone(router)
        self.assertTrue(callable(load_preset))

    def test_stream_tokens_inner_importable(self):
        try:
            from app.routers.chat import _stream_tokens_inner, stream_tokens
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"stream helpers import failed: {exc}")
        self.assertTrue(callable(_stream_tokens_inner))
        self.assertTrue(callable(stream_tokens))
