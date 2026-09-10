"""Tests for the background curation pipeline (services/memory_curation.py).

Stdlib unittest only — no pytest dependency. The parse/helper tests are pure;
the apply-flow tests run IN the container against the real Postgres with a
throwaway user row (the test_memory_files.py pattern). The memory_files table
is ensured to exist first — created from the model when the migration hasn't
been applied.

If the module can't be imported in this environment, the whole suite skips
cleanly (the test_comfy.py pattern).
"""
import asyncio
import json
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, inspect

try:
    from app.db import AsyncSessionLocal, engine
    from app.models.conversations import Conversation
    from app.models.memory_files import MemoryFile
    from app.models.messages import Message
    from app.models.users import User
    from app.services import memory_curation, memory_files
    from app.services.memory_curation import (
        NEW_SENTINEL,
        apply_ops,
        parse_ops,
        should_skip_curation,
    )
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    AsyncSessionLocal = None
    engine = None
    Conversation = None
    MemoryFile = None
    Message = None
    User = None
    memory_curation = None
    memory_files = None
    NEW_SENTINEL = None
    apply_ops = None
    parse_ops = None
    should_skip_curation = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class _FakeProvider:
    """Minimal stand-in for the batch provider: captures completion calls and
    returns no ops (parse_ops('[]') -> []), so the pass never touches files."""

    def __init__(self):
        self.complete = AsyncMock(return_value="[]")


class ParseOpsTests(unittest.TestCase):
    """Pure parsing tests — no DB, no network."""

    @classmethod
    def setUpClass(cls):
        if parse_ops is None:
            raise unittest.SkipTest(
                f"app.services.memory_curation import failed in this env: {_IMPORT_ERROR}"
            )

    def test_valid_array_parses(self):
        raw = json.dumps([
            {"op": "create", "path": "/profile.md", "description": "Profile",
             "aliases": ["me"], "content": "Name: Ada"},
            {"op": "append", "path": "/topics/rust.md", "content": "learning rust",
             "if_version": 1},
        ])
        ops = parse_ops(raw)
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0]["op"], "create")
        self.assertEqual(ops[0]["if_version"], NEW_SENTINEL)  # normalized
        self.assertEqual(ops[1]["op"], "append")
        self.assertEqual(ops[1]["if_version"], 1)

    def test_prose_wrapped_array_parses(self):
        raw = ("Sure! Here you go:\n```json\n"
               '[{"op":"create","path":"/profile.md","description":"d","content":"x"}]\n'
               "```\nHope that helps.")
        ops = parse_ops(raw)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["path"], "/profile.md")

    def test_unparseable_returns_empty(self):
        self.assertEqual(parse_ops("not json at all"), [])
        self.assertEqual(parse_ops(""), [])
        self.assertEqual(parse_ops('{"op":"create"}'), [])  # dict, not a list

    def test_invalid_op_names_dropped(self):
        ops = parse_ops(json.dumps([
            {"op": "explode", "path": "/x.md", "content": "boom"},
            {"op": "create", "path": "/ok.md", "description": "d", "content": "x"},
        ]))
        self.assertEqual([o["op"] for o in ops], ["create"])

    def test_create_missing_description_dropped(self):
        ops = parse_ops(json.dumps([
            {"op": "create", "path": "/a.md", "content": "x"},
            {"op": "create", "path": "/b.md", "description": "d", "content": "x"},
        ]))
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["path"], "/b.md")

    def test_str_replace_without_old_str_dropped(self):
        ops = parse_ops(json.dumps([
            {"op": "str_replace", "path": "/a.md", "new_str": "y", "if_version": 1},
            {"op": "str_replace", "path": "/a.md", "old_str": "x", "new_str": "y",
             "if_version": 1},
        ]))
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["old_str"], "x")

    def test_path_validation_applied(self):
        ops = parse_ops(json.dumps([
            {"op": "create", "path": "no-slash.md", "description": "d", "content": "x"},
            {"op": "create", "path": "/a/../b.md", "description": "d", "content": "x"},
            {"op": "create", "path": "/ok.md", "description": "d", "content": "x"},
        ]))
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["path"], "/ok.md")

    def test_create_with_non_new_if_version_dropped(self):
        ops = parse_ops(json.dumps([
            {"op": "create", "path": "/a.md", "description": "d", "content": "x",
             "if_version": 3},
            {"op": "create", "path": "/b.md", "description": "d", "content": "x",
             "if_version": "__new__"},
        ]))
        self.assertEqual([o["path"] for o in ops], ["/b.md"])

    def test_at_most_ten_ops(self):
        ops_list = [
            {"op": "create", "path": f"/f{i}.md", "description": "d", "content": "x"}
            for i in range(15)
        ]
        ops = parse_ops(json.dumps(ops_list))
        self.assertEqual(len(ops), 10)


class ApplyOpsTests(unittest.TestCase):
    """DB-backed apply-flow tests through the versioned primitives."""

    @classmethod
    def setUpClass(cls):
        if apply_ops is None or memory_files is None:
            raise unittest.SkipTest(
                f"app.services.memory_curation import failed in this env: {_IMPORT_ERROR}"
            )
        # ONE loop for the whole class: asyncpg pool connections are bound to
        # the loop they were created on (see test_memory_files.py).
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
            user = User(email=f"curtest-{uuid.uuid4().hex}@example.com",
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

    def test_apply_create_append_str_replace(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                ops = [
                    {"op": "create", "path": "/notes.md", "description": "Notes",
                     "content": "topic: cooking"},
                    {"op": "append", "path": "/notes.md", "content": "likes pasta",
                     "if_version": 1},
                    {"op": "str_replace", "path": "/notes.md",
                     "old_str": "likes pasta", "new_str": "loves pasta",
                     "if_version": 2},
                ]
                log = await apply_ops(db, self.user_id, ops, written_paths=None)
                self.assertTrue(any(line.startswith("ok") for line in log), log)
                current = await memory_files.memory_read(db, self.user_id, "/notes.md")
                self.assertEqual(current["content"], "topic: cooking\nloves pasta")
                self.assertEqual(current["version"], 3)
                self.assertEqual(current["description"], "Notes")
        self._run(scenario())

    def test_ops_on_written_paths_are_skipped(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                ops = [
                    {"op": "create", "path": "/notes.md", "description": "Notes",
                     "content": "agent wrote this"},
                    {"op": "create", "path": "/other.md", "description": "Other",
                     "content": "curator wrote this"},
                ]
                log = await apply_ops(db, self.user_id, ops,
                                      written_paths=["/notes.md"])
                self.assertTrue(
                    any(line.startswith("skip") and "/notes.md" in line for line in log),
                    log,
                )
                self.assertTrue(
                    any(line.startswith("ok") and "/other.md" in line for line in log),
                    log,
                )
                self.assertIsNone(
                    await memory_files.memory_read(db, self.user_id, "/notes.md"))
                self.assertIsNotNone(
                    await memory_files.memory_read(db, self.user_id, "/other.md"))
        self._run(scenario())

    def test_stale_write_conflict_retried_with_fresh_version(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v1", NEW_SENTINEL,
                    description="Notes")
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v2", 1, description="Notes")
                # stale if_version (current version is 2) -> conflict -> retry
                ops = [
                    {"op": "write", "path": "/notes.md", "content": "v3",
                     "if_version": "1"},
                ]
                log = await apply_ops(db, self.user_id, ops, written_paths=None)
                self.assertTrue(
                    any(line.startswith("ok") and "retried" in line for line in log),
                    log,
                )
                current = await memory_files.memory_read(db, self.user_id, "/notes.md")
                self.assertEqual(current["content"], "v3")
                self.assertEqual(current["version"], 3)
        self._run(scenario())

    def test_second_conflict_drops_op_and_leaves_file_intact(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v1", NEW_SENTINEL,
                    description="Notes")
                await memory_files.memory_write(
                    db, self.user_id, "/notes.md", "v2", 1, description="Notes")
                ops = [
                    {"op": "delete", "path": "/notes.md", "if_version": "1"},
                ]
                real_read = memory_files.memory_read

                async def stale_read(sess, uid, path):
                    # the retry's fresh re-read sees version 1 while the row is
                    # really version 2 — simulating the file moving again between
                    # the conflict and the retry
                    current = await real_read(sess, uid, path)
                    if current is None:
                        return None
                    return {**current, "version": 1}

                with patch.object(memory_files, "memory_read", stale_read):
                    log = await apply_ops(db, self.user_id, ops, written_paths=None)
                self.assertTrue(
                    any(line.startswith("drop") and "second conflict" in line
                        for line in log),
                    log,
                )
                current = await memory_files.memory_read(db, self.user_id, "/notes.md")
                self.assertIsNotNone(current)
                self.assertEqual(current["content"], "v2")
                self.assertEqual(current["version"], 2)
        self._run(scenario())


class SkipCurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if should_skip_curation is None:
            raise unittest.SkipTest(
                f"app.services.memory_curation import failed in this env: {_IMPORT_ERROR}"
            )

    def test_private_always_skips(self):
        self.assertTrue(should_skip_curation(True, True))
        self.assertTrue(should_skip_curation(True, False))

    def test_non_private_never_skips(self):
        # an empty index is NOT a skip — the first-ever pass is what creates files
        self.assertFalse(should_skip_curation(False, False))
        self.assertFalse(should_skip_curation(False, True))


class EnqueueCurationTests(unittest.TestCase):
    """enqueue_curation wiring: private excluded, off-path, never raises."""

    @classmethod
    def setUpClass(cls):
        if memory_curation is None:
            raise unittest.SkipTest(
                f"app.services.memory_curation import failed in this env: {_IMPORT_ERROR}"
            )
        cls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(cls.loop)

    @classmethod
    def tearDownClass(cls):
        cls.loop.close()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def test_private_chat_never_enqueues(self):
        with patch.object(memory_curation, "get_queue",
                          new=AsyncMock()) as gq:
            self._run(memory_curation.enqueue_curation(
                "u", "c", [], private=True))
        gq.assert_not_called()

    def test_non_private_enqueues_with_paths(self):
        queue = AsyncMock()
        with patch.object(memory_curation, "get_queue",
                          new=AsyncMock(return_value=queue)):
            self._run(memory_curation.enqueue_curation(
                "u", "c", ["/notes.md"], private=False))
        queue.enqueue_job.assert_awaited_once_with(
            "run_memory_curation", "u", "c", ["/notes.md"]
        )

    def test_enqueue_failure_is_logged_not_raised(self):
        with patch.object(memory_curation, "get_queue",
                          new=AsyncMock(side_effect=RuntimeError("redis down"))):
            self._run(memory_curation.enqueue_curation(
                "u", "c", [], private=False))


class TranscriptOwnershipTests(unittest.TestCase):
    """M2 ownership regression: a curation pass must never read — or feed to
    the batch model — another user's conversation transcript.

    The transcript fetch is factored into _fetch_transcript(db, user_id,
    conversation_id) so the ownership scoping is directly testable; the
    run_curation_pass tests prove the leak is closed end-to-end with the batch
    model patched out (no real model, no real provider network call).
    """

    @classmethod
    def setUpClass(cls):
        if memory_curation is None or Message is None or Conversation is None:
            raise unittest.SkipTest(
                f"app.services.memory_curation import failed in this env: {_IMPORT_ERROR}"
            )
        # ONE loop for the class (asyncpg pool binding — see ApplyOpsTests).
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
        self.user_a_id, self.user_b_id, self.convo_a_id = self._run(self._seed())
        self.provider = _FakeProvider()

    async def _seed(self):
        """User A owns a conversation with a distinctive message; user B owns
        nothing. Returns (user_a_id, user_b_id, convo_a_id) as strings."""
        async with AsyncSessionLocal() as db:
            user_a = User(email=f"curowner-a-{uuid.uuid4().hex}@example.com",
                          hashed_password="x")
            user_b = User(email=f"curowner-b-{uuid.uuid4().hex}@example.com",
                          hashed_password="x")
            db.add_all([user_a, user_b])
            await db.flush()
            convo = Conversation(title="curation-ownership", user_id=user_a.id)
            db.add(convo)
            await db.flush()
            db.add_all([
                Message(conversation_id=convo.id, role="user",
                        content="SECRET-FACT-A prefers dark roast", index=0),
                Message(conversation_id=convo.id, role="assistant",
                        content="noted", index=1),
            ])
            await db.commit()
            return str(user_a.id), str(user_b.id), str(convo.id)

    def tearDown(self):
        self._run(self._cleanup())

    async def _cleanup(self):
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Message)
                             .where(Message.conversation_id == self.convo_a_id))
            await db.execute(delete(Conversation)
                             .where(Conversation.id == self.convo_a_id))
            await db.execute(delete(MemoryFile).where(
                MemoryFile.user_id.in_([self.user_a_id, self.user_b_id])))
            await db.execute(delete(User).where(
                User.id.in_([self.user_a_id, self.user_b_id])))
            await db.commit()

    def test_fetch_transcript_empty_for_foreign_conversation(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                rows = await memory_curation._fetch_transcript(
                    db, self.user_b_id, self.convo_a_id)
                self.assertEqual(rows, [])
        self._run(scenario())

    def test_fetch_transcript_returns_own_messages(self):
        async def scenario():
            async with AsyncSessionLocal() as db:
                rows = await memory_curation._fetch_transcript(
                    db, self.user_a_id, self.convo_a_id)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0].role, "user")
                self.assertIn("SECRET-FACT-A", rows[0].content)
                self.assertEqual(rows[1].role, "assistant")
        self._run(scenario())

    def test_run_curation_pass_foreign_conversation_never_calls_model(self):
        """User B runs the pass on A's conversation: the ownership-scoped
        transcript is empty, so the pass returns early — the batch model never
        sees A's content (complete is never awaited)."""
        pick = AsyncMock(return_value=(self.provider, "fake-model"))
        with patch.object(memory_curation, "_pick_batch_model", pick):
            self._run(memory_curation.run_curation_pass(
                self.user_b_id, self.convo_a_id))
        pick.assert_not_called()
        self.provider.complete.assert_not_called()

    def test_run_curation_pass_own_conversation_feeds_own_transcript(self):
        """Positive control: with A's own conversation the pass DOES reach the
        model and the prompt carries A's own content — proving the patched path
        works and would catch a leak if the scoping regressed."""
        pick = AsyncMock(return_value=(self.provider, "fake-model"))
        with patch.object(memory_curation, "_pick_batch_model", pick):
            self._run(memory_curation.run_curation_pass(
                self.user_a_id, self.convo_a_id))
        pick.assert_awaited_once()
        self.provider.complete.assert_awaited_once()
        user_prompt = self.provider.complete.await_args.kwargs["messages"][1]["content"]
        self.assertIn("SECRET-FACT-A", user_prompt)


if __name__ == "__main__":
    unittest.main()
