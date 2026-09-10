"""Tests for the memory-files storage layer (services/memory_files.py).

Stdlib unittest only — no pytest dependency. These run IN the container
against the real Postgres: each test creates a throwaway user row in setUp
and deletes it (plus its memory_files rows) in tearDown. The memory_files
table is ensured to exist first — alembic is code-review-only for this phase,
so the migration is NOT applied; the test creates the table from the model,
which is the same schema the migration defines.

If the module can't be imported in this environment, the whole suite skips
cleanly (the test_comfy.py pattern).
"""
import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, inspect

try:
    from app.db import AsyncSessionLocal, engine
    from app.models.memory_files import MemoryFile
    from app.models.users import User
    from app.services import memory_files
    from app.services.memory_files import NEW_SENTINEL
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    AsyncSessionLocal = None
    engine = None
    MemoryFile = None
    User = None
    memory_files = None
    NEW_SENTINEL = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class MemoryFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if memory_files is None:
            raise unittest.SkipTest(
                f"app.services.memory_files import failed in this env: {_IMPORT_ERROR}"
            )
        # ONE loop for the whole class: asyncpg pool connections are bound to
        # the loop they were created on, so a fresh asyncio.run() per test
        # would hand the same connection to a different loop.
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)
        cls.loop.run_until_complete(cls._ensure_table())

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(engine.dispose())
        cls.loop.close()

    @classmethod
    async def _ensure_table(cls):
        async with AsyncSessionLocal() as db:
            def _create(sync_session):
                conn = sync_session.connection()
                if not inspect(conn).has_table("memory_files"):
                    MemoryFile.__table__.create(conn)
            await db.run_sync(_create)
            await db.commit()  # DDL runs in a transaction — commit or it rolls back

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def setUp(self):
        self.user_id = self._run(self._create_user())

    async def _create_user(self):
        async with AsyncSessionLocal() as db:
            user = User(email=f"memtest-{uuid.uuid4().hex}@example.com",
                        hashed_password="x")
            db.add(user)
            await db.commit()
            return str(user.id)

    def tearDown(self):
        if not hasattr(self, "user_id"):
            return
        self._run(self._cleanup())

    async def _cleanup(self):
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(MemoryFile).where(MemoryFile.user_id == self.user_id)
            )
            await db.execute(delete(User).where(User.id == self.user_id))
            await db.commit()

    def test_versioned_write_create_then_update(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                created = await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "hello", NEW_SENTINEL,
                    description="Scratch notes",
                )
                self.assertTrue(created["ok"])
                self.assertEqual(created["version"], 1)

                updated = await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "hello world", "1",
                    description="Scratch notes",
                )
                self.assertTrue(updated["ok"])
                self.assertEqual(updated["version"], 2)

                current = await memory_files.memory_read(db, self.user_id, "/notes.md")
                self.assertEqual(current["content"], "hello world")
                self.assertEqual(current["version"], 2)
        self._run(scenario())

    def test_stale_version_rejection_returns_current(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v1", NEW_SENTINEL, description="d")
                landed = await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v2", 1, description="d")
                self.assertTrue(landed["ok"])

                stale = await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v3", 1, description="d")
                self.assertFalse(stale["ok"])
                self.assertEqual(stale["reason"], "conflict")
                self.assertEqual(stale["current"]["version"], 2)
                self.assertEqual(stale["current"]["content"], "v2")
        self._run(scenario())

    def test_create_when_exists_rejected(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "x", NEW_SENTINEL, description="d")
                result = await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "y", NEW_SENTINEL, description="d")
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "conflict")
                self.assertEqual(result["current"]["version"], 1)
        self._run(scenario())

    def test_read_miss_returns_none(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                self.assertIsNone(
                    await memory_files.memory_read(db, self.user_id, "/missing.md"))
        self._run(scenario())

    def test_str_replace_outcomes(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                await memory_files.memory_write(
                    db, self.user_id, "/profile.md", "Name: Alice\nCity: Paris",
                    NEW_SENTINEL, description="Profile")

                # 0 matches -> not_found
                zero = await memory_files.memory_str_replace(
                    db, self.user_id, "/profile.md", "Berlin", "Rome", 1)
                self.assertFalse(zero["ok"])
                self.assertEqual(zero["reason"], "not_found")

                # 2 matches -> ambiguous
                await memory_files.memory_write(
                    db, self.user_id, "/profile.md", "cat cat dog", 1,
                    description="Profile")
                two = await memory_files.memory_str_replace(
                    db, self.user_id, "/profile.md", "cat", "bird", "2")
                self.assertFalse(two["ok"])
                self.assertEqual(two["reason"], "ambiguous")
                self.assertIn("2 times", two["message"])

                # 1 match -> applied, version advances
                one = await memory_files.memory_str_replace(
                    db, self.user_id, "/profile.md", "dog", "mouse", 2)
                self.assertTrue(one["ok"])
                self.assertEqual(one["version"], 3)
                current = await memory_files.memory_read(db, self.user_id, "/profile.md")
                self.assertEqual(current["content"], "cat cat mouse")
        self._run(scenario())

    def test_append_appends_with_newline_and_advances_version(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "line1", NEW_SENTINEL, description="d")
                result = await memory_files.memory_append(
                    db, self.user_id, "/notes.md", "line2", 1)
                self.assertTrue(result["ok"])
                self.assertEqual(result["version"], 2)
                current = await memory_files.memory_read(db, self.user_id, "/notes.md")
                self.assertEqual(current["content"], "line1\nline2")
        self._run(scenario())

    def test_delete_then_read_none(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "x", NEW_SENTINEL, description="d")
                result = await memory_files.memory_delete(db, self.user_id, "/notes.md", 1)
                self.assertTrue(result["ok"])
                self.assertIsNone(
                    await memory_files.memory_read(db, self.user_id, "/notes.md"))

                again = await memory_files.memory_delete(db, self.user_id, "/notes.md", 1)
                self.assertFalse(again["ok"])
                self.assertEqual(again["reason"], "not_found")
        self._run(scenario())

    def test_size_cap_rejects_over_cap_write(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                with patch.object(memory_files.settings, "MEMORY_FILE_CAP_BYTES", 8):
                    result = await memory_files.memory_write(
                        db, self.user_id, "/notes.md", "0123456789", NEW_SENTINEL,
                        description="d")
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "size_cap")
                self.assertIn("consolidate", result["message"])
                # nothing was written
                self.assertIsNone(
                    await memory_files.memory_read(db, self.user_id, "/notes.md"))
        self._run(scenario())

    def test_size_cap_rejects_at_cap_write(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                # AT the cap is rejected too (>=, never truncate)...
                with patch.object(memory_files.settings, "MEMORY_FILE_CAP_BYTES", 8):
                    at_cap = await memory_files.memory_write(
                        db, self.user_id, "/notes.md", "01234567", NEW_SENTINEL,
                        description="d")
                self.assertFalse(at_cap["ok"])
                self.assertEqual(at_cap["reason"], "size_cap")
                self.assertIsNone(
                    await memory_files.memory_read(db, self.user_id, "/notes.md"))
                # ...while one byte under the cap lands
                with patch.object(memory_files.settings, "MEMORY_FILE_CAP_BYTES", 8):
                    under = await memory_files.memory_write(
                        db, self.user_id, "/notes.md", "0123456", NEW_SENTINEL,
                        description="d")
                self.assertTrue(under["ok"])
                self.assertEqual(under["version"], 1)
        self._run(scenario())

    def test_concurrent_create_race_returns_conflict(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                # Simulate the race: the row exists, but the __new__ pre-check
                # misses it (first _get_row call), so the INSERT hits the
                # unique (user_id, path) constraint. The storage layer must
                # catch the IntegrityError, roll back, and report the winner.
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v1", NEW_SENTINEL, description="d")

                real_get_row = memory_files._get_row
                calls = {"n": 0}

                async def fake_get_row(sess, uid, path):
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return None  # pre-check misses
                    return await real_get_row(sess, uid, path)  # post-rollback re-read

                with patch.object(memory_files, "_get_row", fake_get_row):
                    result = await memory_files.memory_write(
                        db, self.user_id, "/notes.md", "v2", NEW_SENTINEL, description="d")

                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "conflict")
                self.assertEqual(result["current"]["version"], 1)
                self.assertEqual(result["current"]["content"], "v1")
        self._run(scenario())

    def test_concurrent_create_race_readback_missing_returns_not_found(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                # Edge mirror: the unique-constraint INSERT failed but the
                # post-rollback re-read sees nothing (the racing transaction
                # rolled back too) — surface not_found, never an exception.
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v1", NEW_SENTINEL, description="d")

                async def fake_get_row(sess, uid, path):
                    return None  # pre-check AND re-read both miss

                with patch.object(memory_files, "_get_row", fake_get_row):
                    result = await memory_files.memory_write(
                        db, self.user_id, "/notes.md", "v2", NEW_SENTINEL, description="d")

                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], "not_found")
        self._run(scenario())

    def test_storage_layer_path_validation(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                # read: invalid path behaves like a miss — None, not an exception
                self.assertIsNone(
                    await memory_files.memory_read(db, self.user_id, "notes.md"))
                self.assertIsNone(
                    await memory_files.memory_read(db, self.user_id, "/a/../b"))
                # write: invalid path -> invalid_path error dict, not an exception
                wrote = await memory_files.memory_write(
                    db, self.user_id, "notes.md", "x", NEW_SENTINEL, description="d")
                self.assertFalse(wrote["ok"])
                self.assertEqual(wrote["reason"], "invalid_path")
                self.assertIn("start with", wrote["message"])
                # append/str_replace validate the same way
                appended = await memory_files.memory_append(
                    db, self.user_id, "notes.md", "x", NEW_SENTINEL)
                self.assertFalse(appended["ok"])
                self.assertEqual(appended["reason"], "invalid_path")
                replaced = await memory_files.memory_str_replace(
                    db, self.user_id, "notes.md", "a", "b", NEW_SENTINEL)
                self.assertFalse(replaced["ok"])
                self.assertEqual(replaced["reason"], "invalid_path")
                # delete: invalid path mirrors not_found, not an exception
                deleted = await memory_files.memory_delete(
                    db, self.user_id, "notes.md", NEW_SENTINEL)
                self.assertFalse(deleted["ok"])
                self.assertEqual(deleted["reason"], "not_found")
        self._run(scenario())

    def test_build_memory_context(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                # no files -> ""
                self.assertEqual(
                    await memory_files.build_memory_context(db, self.user_id), "")

                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "some content", NEW_SENTINEL,
                    description="Scratch notes", aliases=["notes"])
                context = await memory_files.build_memory_context(db, self.user_id)
                self.assertIn("- /notes.md — Scratch notes", context)
                self.assertIn("aliases: notes", context)
                self.assertNotIn("some content", context)  # tier 1 is index-only

                # tier 1.5: configured path appended in full (delimited + capped)
                with patch.object(
                    memory_files.settings, "MEMORY_TIER1_5_PATHS", "/notes.md"
                ):
                    context15 = await memory_files.build_memory_context(db, self.user_id)
                self.assertIn('<memory_file path="/notes.md">', context15)
                self.assertIn("some content", context15)
                self.assertIn("</memory_file>", context15)

                # tier 1.5 injection is byte-capped (defense-in-depth, F7)
                big = "x" * (memory_files.settings.MEMORY_TIER1_5_INJECT_CAP * 2)
                await memory_files.memory_write(
                    db, self.user_id, "/big.md", big, NEW_SENTINEL)
                with patch.object(
                    memory_files.settings, "MEMORY_TIER1_5_PATHS", "/big.md"
                ):
                    context_big = await memory_files.build_memory_context(db, self.user_id)
                self.assertIn("[truncated]", context_big)
                self.assertLess(
                    len(context_big),
                    memory_files.settings.MEMORY_TIER1_5_INJECT_CAP + 200,
                )
        self._run(scenario())

    def test_path_validation(self):
        for bad in ("", "notes.md", "/a/../b", "/a/..", "..", "/with\u0000null",
                    "/with\u007fdel"):
            with self.assertRaises(ValueError, msg=bad):
                memory_files._validate_path(bad)
        self.assertEqual(memory_files._validate_path("/notes.md"), "/notes.md")
        self.assertEqual(memory_files._validate_path("/a/b.md"), "/a/b.md")


class MemoryToolsRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from app.services.tools import registry
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"tools registry import failed: {exc}")
        # The handlers reference memory_files service functions; if that module
        # didn't import (e.g. host without asyncpg), the registry alone isn't
        # enough to exercise them.
        if memory_files is None:
            raise unittest.SkipTest("app.services.memory_files is unavailable in this env")
        cls.registry = registry
        # ONE loop for handler tests; storage tests use their own class loop,
        # and both classes create separate loops — no shared connections.
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _ctx(self, db=None):
        return self.registry.ToolContext(user_id="u", conversation_id="c", db=db)

    def test_all_five_memory_tools_are_registered(self):
        for name in ("memory_read", "memory_write", "memory_str_replace",
                     "memory_append", "memory_delete"):
            tool = self.registry.get_tool(name)
            self.assertIsNotNone(tool, f"tool '{name}' not registered")

    def test_mutating_memory_tools_are_deny_by_default(self):
        """F7: a fetched web page can say 'write X to /profile.md'. If the mutating
        memory tools were default-allowed the page could plant content that
        build_memory_context injects verbatim into every future system prompt.
        Only memory_read is default-allowed (reads don't persist injection);
        every mutating memory tool requires an explicit user grant."""
        read = self.registry.get_tool("memory_read")
        self.assertTrue(read.first_party)
        for name in ("memory_write", "memory_str_replace", "memory_append",
                     "memory_delete"):
            tool = self.registry.get_tool(name)
            self.assertFalse(
                tool.first_party,
                f"{name} must be deny-by-default (first_party=False) — F7",
            )

    def test_handlers_return_error_string_for_invalid_path(self):
        async def scenario():
            ctx = self._ctx()
            for name in ("memory_read", "memory_write", "memory_str_replace",
                         "memory_append", "memory_delete"):
                handler = self.registry.get_tool(name).handler
                args = {"path": "not-a-path"}
                if name != "memory_read":
                    args["if_version"] = "__new__"
                if name == "memory_write":
                    args["content"] = "x"
                if name == "memory_str_replace":
                    args["old_str"] = "a"
                    args["new_str"] = "b"
                if name == "memory_append":
                    args["content"] = "x"
                result = await handler(args, ctx)
                self.assertTrue(result.startswith("Error:"),
                                f"{name} leaked: {result!r}")
        self._run(scenario())

    def test_handlers_never_raise_on_db_error(self):
        async def scenario():
            ctx = self._ctx()
            # Every memory tool handler must turn a broken service call into an
            # "Error: ..." string, never raise — the never-raise contract that
            # keeps the agent run alive. Each handler's DIRECT service call is
            # patched to raise RuntimeError; the _safe wrapper is the backstop
            # that converts it. (Patching the direct call means the failure
            # path is reached before the service can do its own memory_read.)
            cases = {
                "memory_read": (
                    "memory_read",
                    {"path": "/notes.md"},
                ),
                "memory_write": (
                    "memory_write",
                    {"path": "/notes.md", "content": "x", "if_version": "__new__"},
                ),
                "memory_str_replace": (
                    "memory_str_replace",
                    {"path": "/notes.md", "old_str": "a", "new_str": "b",
                     "if_version": "1"},
                ),
                "memory_append": (
                    "memory_append",
                    {"path": "/notes.md", "content": "x", "if_version": "1"},
                ),
                "memory_delete": (
                    "memory_delete",
                    {"path": "/notes.md", "if_version": "1"},
                ),
            }
            for name, (service_name, args) in cases.items():
                handler = self.registry.get_tool(name).handler
                with patch.object(memory_files, service_name,
                                  new=AsyncMock(side_effect=RuntimeError("db down"))):
                    result = await handler(args, ctx)
                self.assertTrue(
                    result.startswith("Error: memory operation failed:"),
                    f"{name} leaked: {result!r}",
                )
        self._run(scenario())


if __name__ == "__main__":
    unittest.main()
