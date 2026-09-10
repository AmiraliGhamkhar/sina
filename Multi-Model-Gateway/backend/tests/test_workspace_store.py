"""Tests for the git-backed workspace store (services/workspace/store.py).

Covers the two seams the Workspace deep module (#2) is responsible for:

* `_resolveInside` — the single path-security helper that can raise 422. It must
  reject absolute paths, `..`/`.` segments, control chars, and symlink escapes,
  while accepting valid relative paths and `.`.
* The mutating pipeline (write_file → git commit → file_edits audit) and
  deterministic undo by `commit_sha`.

Offline-by-design: `app.db` is stubbed before import so neither asyncpg nor a
running Postgres is required. Git must be on PATH for the pipeline tests; the
pure-helper tests run regardless. If the store module can't be imported (e.g.
some other import-time dep), the whole suite skips cleanly.
"""
import asyncio
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest


def _import_store_with_stub_db():
    """Import the store module against a DB-light app.db, then restore it.

    `store.py` pulls `app.db` at module scope (AsyncSessionLocal + FileEdit's
    Base), which drags in asyncpg. We don't want that on hosts without it, and
    we don't want to leave a fake `app.db` in sys.modules where it would break
    OTHER test modules (e.g. test_routing imports the real asyncpg-backed
    app.db). So we stub app.db only during the store import and immediately
    remove it from sys.modules; the store keeps its own captured references.
    """
    had_real = "app.db" in sys.modules
    real = sys.modules.get("app.db")
    if not had_real:
        from sqlalchemy.orm import declarative_base

        fake = types.ModuleType("app.db")
        fake.AsyncSessionLocal = None
        fake.Base = declarative_base()
        fake.get_db = lambda: None
        sys.modules["app.db"] = fake
    try:
        from app.services.workspace.store import (
            WorkspaceStore,
            _file_hashes,
            _line_hash,
            _resolveInside,
            _read_text_fp,
            _write_text_fp,
        )
    finally:
        # Restore the real app.db so sibling test modules see the real thing.
        if had_real:
            sys.modules["app.db"] = real
        else:
            sys.modules.pop("app.db", None)
    return WorkspaceStore, _file_hashes, _line_hash, _resolveInside, _read_text_fp, _write_text_fp


try:
    WorkspaceStore, _file_hashes, _line_hash, _resolveInside, _read_text_fp, _write_text_fp = _import_store_with_stub_db()
except Exception as exc:  # noqa: BLE001 — env may lack a required import dep
    WorkspaceStore = None
    _file_hashes = None
    _line_hash = None
    _resolveInside = None
    _read_text_fp = None
    _write_text_fp = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class ResolveInsideTests(unittest.TestCase):
    """The path-security seam: one helper that may raise 422."""

    @classmethod
    def setUpClass(cls):
        if _resolveInside is None:
            raise unittest.SkipTest(
                f"app.services.workspace.store import failed in this env: {_IMPORT_ERROR}"
            )

    def test_returns_path_for_valid_relative(self):
        root = pathlib.Path("/srv/ws")
        out = _resolveInside(root, "a/b/c.txt")
        self.assertEqual(out, root / "a/b/c.txt")
        self.assertNotEqual(out, root)  # not the root itself

    def test_dot_returns_root(self):
        root = pathlib.Path("/srv/ws")
        self.assertEqual(_resolveInside(root, "."), root)
        self.assertEqual(_resolveInside(root, "./"), root)
        self.assertEqual(_resolveInside(root, ""), root)

    def test_rejects_absolute_path(self):
        root = pathlib.Path("/srv/ws")
        with self.assertRaises(Exception) as ctx:
            _resolveInside(root, "/etc/passwd")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 422)

    def test_rejects_dotdot_segments(self):
        root = pathlib.Path("/srv/ws")
        for bad in ("../x", "a/../b", "a/../../b", ".."):
            with self.assertRaises(Exception) as ctx:
                _resolveInside(root, bad)
            self.assertEqual(getattr(ctx.exception, "status_code", None), 422, bad)

    def test_rejects_traversal_that_resolves_outside(self):
        """A symlink (or a pre-existing dir that escapes) must be refused."""
        root = pathlib.Path("/srv/ws")
        with self.assertRaises(Exception) as ctx:
            _resolveInside(root, "a/../..")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 422)

    def test_rejects_control_chars(self):
        root = pathlib.Path("/srv/ws")
        for bad in ("a\x00b", "a\x1fb", "\x7f"):
            with self.assertRaises(Exception) as ctx:
                _resolveInside(root, bad)
            self.assertEqual(getattr(ctx.exception, "status_code", None), 422, repr(bad))

    def test_not_directory_guard(self):
        from app.services.workspace.store import _assertNotDirectory

        with self.assertRaises(Exception) as ctx:
            _assertNotDirectory(".")
        self.assertEqual(getattr(ctx.exception, "status_code", None), 422)


class HashesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _line_hash is None:
            raise unittest.SkipTest(
                f"app.services.workspace.store import failed in this env: {_IMPORT_ERROR}"
            )

    def test_line_hash_is_short_sha1(self):
        h = _line_hash("hello world")
        self.assertEqual(len(h), 8)
        self.assertEqual(h, hashlib_sha1_8("hello world"))

    def test_file_hashes_is_per_line(self):
        self.assertEqual(_file_hashes("a\nb\nc"), [_line_hash(x) for x in ("a", "b", "c")])
        self.assertEqual(_file_hashes(""), [])
        self.assertEqual(_file_hashes("x"), [_line_hash("x")])

    def test_hash_changes_with_content(self):
        self.assertNotEqual(_line_hash("a"), _line_hash("b"))


class NofollowIoTests(unittest.TestCase):
    """F8: _read_text_fp/_write_text_fp refuse a final-component symlink via
    O_NOFOLLOW where the platform supports it (POSIX). On platforms without
    O_NOFOLLOW (Windows) they fall back to a plain read/write, so the test
    asserts the fallback round-trips correctly and, when O_NOFOLLOW is present,
    refuses a symlink target."""

    @classmethod
    def setUpClass(cls):
        if _read_text_fp is None or _write_text_fp is None:
            raise unittest.SkipTest(
                f"workspace store import failed in this env: {_IMPORT_ERROR}"
            )

    def test_read_write_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            fp = pathlib.Path(td) / "f.txt"
            _write_text_fp(fp, "hello")
            self.assertEqual(_read_text_fp(fp), "hello")

    @unittest.skipUnless(hasattr(__import__("os"), "O_NOFOLLOW"), "POSIX-only")
    def test_read_refuses_symlink_when_o_nofollow_available(self):
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td) / "real.txt"
            link = pathlib.Path(td) / "link.txt"
            target.write_text("secret", encoding="utf-8")
            link.symlink_to(target)
            # O_NOFOLLOW must refuse opening through the symlink on the final
            # component — this is the F8 symlink-swap race protection.
            with self.assertRaises(OSError):
                _read_text_fp(link)


class DuQuotaTests(unittest.TestCase):
    """F9: du_mb must count .git so bash (git clone/dd) can't outgrow the quota,
    and quota_mb() must expose the configured cap to the bash tool."""

    @classmethod
    def setUpClass(cls):
        if WorkspaceStore is None:
            raise unittest.SkipTest(
                f"app.services.workspace.store import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = WorkspaceStore()
        self.root = pathlib.Path(self._tmp.name)
        import app.services.workspace.store as store_mod
        self._orig_path = store_mod._workspace_path
        store_mod._workspace_path = lambda u, a: self.root / str(u) / str(a)
        self.addCleanup(lambda: setattr(store_mod, "_workspace_path", self._orig_path))

    def test_du_mb_counts_git(self):
        """Regression: .git objects were excluded, letting git clone/dd inflate the
        workspace past the quota. Now every regular file (incl. .git) counts."""
        self.store.ensure_workspace("u1", "a1")
        wp = self.root / "u1" / "a1"
        # write a file AND a pseudo .git object
        (wp / "code.txt").write_text("x" * 1024, encoding="utf-8")
        git_obj = wp / ".git" / "objects" / "ab" / "cdef"
        git_obj.parent.mkdir(parents=True, exist_ok=True)
        git_obj.write_bytes(b"y" * 2048)
        du = self.store.du_mb("u1", "a1")
        self.assertGreater(du, 0)
        # ~3KiB total => about 0.0029 MiB; assert it's in a sane nonzero range
        self.assertGreater(du, 0.001)
        self.assertLess(du, 0.05)

    def test_quota_mb_returns_setting(self):
        import app.services.workspace.store as store_mod
        self.assertEqual(
            self.store.quota_mb(),
            float(store_mod.settings.SANDBOX_DISK_QUOTA_MB),
        )


def hashlib_sha1_8(text):
    import hashlib

    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self


class _FakeDB:
    """Mimics the AsyncSession surface the store calls: add/commit/execute.

    Records added rows so the test can assert on the file_edits audit row.
    """

    def __init__(self, rows=None):
        self.added = []
        self._result_rows = rows or []
        self.committed = 0

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed += 1

    async def execute(self, *_a, **_k):
        return _FakeResult(self._result_rows)


class WorkspacePipelineTests(unittest.TestCase):
    """write → git commit → audit row, then deterministic undo by commit_sha."""

    @classmethod
    def setUpClass(cls):
        if WorkspaceStore is None:
            raise unittest.SkipTest(
                f"app.services.workspace.store import failed in this env: {_IMPORT_ERROR}"
            )
        # git must be on PATH for the pipeline tests
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("git is not available on PATH")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        self.store = WorkspaceStore()
        # Point the store's workspace root at our temp dir by patching the
        # module-level _workspace_path resolver for the duration of the test.
        import app.services.workspace.store as store_mod

        self._orig_path = store_mod._workspace_path
        store_mod._workspace_path = lambda u, a: self.root / str(u) / str(a)
        self.addCleanup(lambda: setattr(store_mod, "_workspace_path", self._orig_path))

    def test_planted_git_hook_does_not_execute(self):
        """Sweep H4 regression: model-controlled bash can write .git/hooks/* in
        any workspace on the shared volume; the store's hardened git calls
        (core.hooksPath override) must ensure the planted hook never runs
        inside the backend container. The control (raw git commit) proves the
        hook genuinely fires without hardening."""
        import os
        loop = asyncio.new_event_loop()
        try:
            wp = self.root / "u1" / "a1"
            self.store.ensure_workspace("u1", "a1")
            (wp / "code.txt").write_text("hello\n", encoding="utf-8")
            hooks = wp / ".git" / "hooks"
            hooks.mkdir(exist_ok=True)
            hook = hooks / "pre-commit"
            hook.write_text("#!/bin/sh\necho EVIL > pwned.txt\nexit 0\n", encoding="utf-8")
            try:
                os.chmod(hook, 0o755)
            except OSError:
                pass  # Windows: exec-bit not enforced; git on windows still runs hooks

            self.store._commit(wp, "hardened commit with planted hook")
            self.assertFalse((wp / "pwned.txt").exists(), "planted hook executed despite hooksPath hardening")

            # Control: the same hook DOES fire via raw git (no hardening) —
            # guards against the test passing because the hook is inert.
            subprocess.run(["git", "-C", str(wp), "add", "-A"], check=False)
            subprocess.run(["git", "-C", str(wp), "commit", "--allow-empty", "-m", "raw control"], check=False)
            self.assertTrue((wp / "pwned.txt").exists(), "control failed: hook did not fire on raw git — test is vacuous")
        finally:
            loop.close()

    def test_write_file_commits_and_audits(self):
        loop = asyncio.new_event_loop()
        try:
            db = _FakeDB()
            result = loop.run_until_complete(
                self.store.write_file(
                    user_id="u1", agent_id="a1", path="src/app.py",
                    content="print('hi')\n", tool_call_id="tc1", db=db,
                )
            )
            self.assertIn("edit_id", result)
            self.assertIn("commit_sha", result)
            self.assertIsNotNone(result["commit_sha"])
            # The file exists on disk
            f = self.root / "u1" / "a1" / "src" / "app.py"
            self.assertTrue(f.is_file())
            self.assertEqual(f.read_text(encoding="utf-8"), "print('hi')\n")
            # audit row was added with the commit sha captured
            self.assertEqual(len(db.added), 1)
            row = db.added[0]
            self.assertEqual(row.store, "workspace")
            self.assertEqual(row.path, "src/app.py")
            self.assertEqual(row.commit_sha, result["commit_sha"])
            # git repo has the commit
            wp = self.root / "u1" / "a1"
            head = subprocess.run(
                ["git", "-C", str(wp), "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(head, result["commit_sha"])
        finally:
            loop.close()

    def test_write_file_rejects_escape(self):
        loop = asyncio.new_event_loop()
        try:
            with self.assertRaises(Exception) as ctx:
                loop.run_until_complete(
                    self.store.write_file("u1", "a1", "../evil", "x")
                )
            self.assertEqual(getattr(ctx.exception, "status_code", None), 422)
        finally:
            loop.close()

    def test_write_file_conflict_on_hash_mismatch(self):
        loop = asyncio.new_event_loop()
        try:
            db = _FakeDB()
            loop.run_until_complete(
                self.store.write_file("u1", "a1", "f.txt", "aaa\n", db=db)
            )
            # Now write with stale hashes that don't match current content
            with self.assertRaises(Exception) as ctx:
                loop.run_until_complete(
                    self.store.write_file(
                        "u1", "a1", "f.txt", "bbb\n",
                        expected_hashes=[_line_hash("gone")],
                        db=db,
                    )
                )
            self.assertEqual(getattr(ctx.exception, "status_code", None), 409)
        finally:
            loop.close()

    def test_edit_lines_reflects_replace(self):
        loop = asyncio.new_event_loop()
        try:
            db = _FakeDB()
            self.store.write_file.__wrapped__ if False else None
            loop.run_until_complete(
                self.store.write_file("u1", "a1", "f.txt", "a\nb\nc\n", db=db)
            )
            # replace line b (index 1) with X — but edit_lines keys on hashes
            hashes = [_line_hash(x) for x in ("a", "b", "c")]
            result = loop.run_until_complete(
                self.store.edit_lines(
                    "u1", "a1", "f.txt", [hashes[1]], "B\n", db=db
                )
            )
            self.assertIn("commit_sha", result)
            f = self.root / "u1" / "a1" / "f.txt"
            self.assertEqual(f.read_text(encoding="utf-8"), "a\nB\nc\n")
        finally:
            loop.close()

    def test_undo_by_commit_sha_reverts(self):
        loop = asyncio.new_event_loop()
        try:
            db = _FakeDB()
            first = loop.run_until_complete(
                self.store.write_file("u1", "a1", "f.txt", "v1\n", db=db)
            )
            second = loop.run_until_complete(
                self.store.write_file("u1", "a1", "f.txt", "v2\n", db=db)
            )
            f = self.root / "u1" / "a1" / "f.txt"
            self.assertEqual(f.read_text(encoding="utf-8"), "v2\n")

            # undo the second edit (commit_sha). The undo path needs a db row to
            # look up; build a fake row carrying the commit_sha.
            edit_row = types.SimpleNamespace(
                id=second["edit_id"], user_id="u1", agent_id="a1",
                store="workspace", path="f.txt",
                before_hash=None, after_hash=None, commit_sha=second["commit_sha"],
            )
            undo_db = _FakeDB(rows=[edit_row])
            res = loop.run_until_complete(
                self.store.undo("u1", "a1", second["edit_id"], undo_db)
            )
            self.assertIn("undone", res)
            self.assertEqual(res["undone"], second["edit_id"])
            self.assertEqual(f.read_text(encoding="utf-8"), "v1\n")
            # an undo audit row was recorded
            self.assertGreaterEqual(len(undo_db.added), 1)
            last = undo_db.added[-1]
            self.assertEqual(last.patch, f"undo {second['edit_id']}")
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
