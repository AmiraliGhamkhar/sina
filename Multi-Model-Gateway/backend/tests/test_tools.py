"""Unit tests for the first-party agent tools (services/tools/).

Stdlib unittest only — no pytest dependency. Coverage is deliberately shallow:
current_datetime's handler is pure, search_conversations is tested via the
compiled-SQL query builder (no DB), and generate_image's error path is tested
with redis/comfy patched (no network) — the registry assertions prove each
tool registered with the right name. If the package can't be imported in this
environment (missing settings/secret deps), the whole suite skips cleanly.
"""
import asyncio
import unittest

try:
    from app.services.tools import registry
    from app.services.tools.current_time import _current_datetime
    from app.services.tools.search_conversations import _escape_like, _build_search_query
    from app.services.tools import generate_image as _generate_image_module
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    registry = None
    _current_datetime = None
    _escape_like = None
    _build_search_query = None
    _generate_image_module = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class CurrentDatetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _current_datetime is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )

    def test_handler_returns_utc_timestamp_with_weekday(self):
        """The handler ignores its args/ctx and returns a UTC timestamp string
        with a weekday name, e.g. '2026-08-11 06:15 UTC (Tuesday)'."""
        result = asyncio.run(_current_datetime({}, None))
        self.assertRegex(
            result,
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC \([A-Za-z]+\)\.",
        )


class EscapeLikeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _escape_like is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )

    def test_percent_is_escaped(self):
        self.assertEqual(_escape_like("%"), "\\%")

    def test_underscore_is_escaped(self):
        self.assertEqual(_escape_like("_"), "\\_")

    def test_backslash_is_escaped(self):
        self.assertEqual(_escape_like("\\"), "\\\\")

    def test_all_metacharacters_at_once(self):
        self.assertEqual(_escape_like("50%_off\\now"), "50\\%\\_off\\\\now")


class SearchQueryBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _build_search_query is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )

    def _compiled_sql(self, user_id="u1", pattern="%cat%", limit=5):
        from sqlalchemy.dialects import postgresql
        stmt = _build_search_query(user_id=user_id, pattern=pattern, limit=limit)
        return str(stmt.compile(dialect=postgresql.dialect()))

    def test_uses_left_outer_join_so_title_only_conversations_match(self):
        """An inner join would drop conversations with zero messages."""
        self.assertIn("LEFT OUTER JOIN", self._compiled_sql())

    def test_both_predicates_live_in_the_where_clause(self):
        """ILIKE compiles natively on postgres; both columns must be in the
        WHERE, not the join's ON clause, or the outer join degenerates into an
        inner one and drops title-only conversations."""
        sql = self._compiled_sql()
        self.assertIn("conversations.title ILIKE", sql)
        self.assertIn("messages.content ILIKE", sql)

    def test_distinct_and_order_by_are_preserved(self):
        sql = self._compiled_sql()
        self.assertIn("DISTINCT", sql)
        self.assertIn("ORDER BY conversations.created_at DESC", sql)


class GenerateImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _generate_image_module is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )

    def test_redis_acquire_failure_returns_error_string(self):
        """A Redis failure must surface as an error string, never raise —
        otherwise the agent run dies mid-tool-call."""
        from types import SimpleNamespace
        from unittest.mock import patch

        async def redis_boom():
            raise RuntimeError("redis unavailable")

        async def fake_generate(*args, **kwargs):
            return "prompt-123"

        ctx = SimpleNamespace(user_id="u1", conversation_id="c1", db=None)
        with patch.object(_generate_image_module, "get_redis", new=redis_boom):
            with patch.object(
                _generate_image_module.comfy, "generate_image", new=fake_generate
            ):
                result = asyncio.run(
                    _generate_image_module._generate_image({"prompt": "a cat"}, ctx)
                )
        self.assertTrue(result.startswith("Error:"))
        self.assertIn("redis unavailable", result)


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if registry is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )

    def test_new_tools_are_registered(self):
        for name in ("current_datetime", "search_conversations", "generate_image"):
            tool = registry.get_tool(name)
            self.assertIsNotNone(tool, f"tool '{name}' not registered")

    def test_existing_tools_are_still_registered(self):
        for name in ("recall_recent_exchanges", "web_search", "fetch_page"):
            tool = registry.get_tool(name)
            self.assertIsNotNone(tool, f"tool '{name}' not registered")

    def test_mutating_memory_tools_denied_by_default(self):
        """F7 regression: a fetched web page can say 'write X to /profile.md'. If
        the mutating memory tools were default-allowed the page could plant
        content that build_memory_context injects verbatim into every future
        system prompt. Only memory_read is default-allowed (reads don't persist
        injection); every mutating memory tool requires an explicit user grant."""
        read = registry.get_tool("memory_read")
        self.assertIsNotNone(read)
        self.assertTrue(read.first_party)
        for name in ("memory_write", "memory_str_replace", "memory_append",
                     "memory_delete"):
            tool = registry.get_tool(name)
            self.assertIsNotNone(tool, f"tool '{name}' not registered")
            self.assertFalse(
                tool.first_party,
                f"{name} must be deny-by-default (first_party=False) — F7",
            )


if __name__ == "__main__":
    unittest.main()
