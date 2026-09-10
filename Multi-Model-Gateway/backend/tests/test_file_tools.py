"""Unit tests for the workspace file + bash tools (services/tools/).

Stdlib unittest only — no pytest dependency. These exercise the tool handlers
against a real git-backed temp workspace (created in setUp) with a fake DB passed
through the ToolContext, so the write→commit→audit→undo pipeline runs offline.
`app.db` is stubbed before import (no asyncpg) and restored after; `_workspace_path`
is monkeypatched to the temp dir so the singleton store operates on a throwaway
root. If the tools can't be imported in this environment, the whole suite skips.
"""
import asyncio
import json
import pathlib
import subprocess
import tempfile
import unittest

from tests.agent_test_stubs import import_with_stubs


def _load():
    from app.services.tools import files as _files  # noqa: F401
    from app.services.tools import bash_tool as _bash  # noqa: F401
    from app.services.tools.registry import ToolContext
    from app.services.workspace.store import _line_hash
    return _files, _bash, ToolContext, _line_hash


try:
    _files, _bash, ToolContext, _line_hash = import_with_stubs(_load)
except Exception as exc:  # noqa: BLE001
    _files = None
    _bash = None
    ToolContext = None
    _line_hash = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class _FakeDB:
    """Mimics the AsyncSession surface the store needs: add/commit/execute."""

    def __init__(self, rows=None):
        self.added = []
        self._rows = rows or []

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        pass

    async def execute(self, *_a, **_k):
        from types import SimpleNamespace
        return SimpleNamespace(scalar_one_or_none=lambda: self._rows[0] if self._rows else None)


def _ctx(user_id="u1", agent_id="a1", db=None):
    return ToolContext(user_id=user_id, conversation_id="c1", db=db or _FakeDB(), agent_id=agent_id)


def _patch_available() -> bool:
    """True when the `patch` binary is on PATH (needed by workspace apply_patch)."""
    try:
        return subprocess.run(["patch", "--version"], capture_output=True).returncode == 0
    except (FileNotFoundError, OSError):
        return False


class FileToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _files is None or _line_hash is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("git is not available on PATH")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        # Point the store's workspace root at our temp dir for the duration of the test.
        import app.services.workspace.store as store_mod
        self._orig = store_mod._workspace_path
        store_mod._workspace_path = lambda u, a: self.root / str(u) / str(a)
        self.addCleanup(lambda: setattr(store_mod, "_workspace_path", self._orig))

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_write_then_read_roundtrip(self):
        ctx = _ctx()
        out = self._run(_files._write_file({"path": "src/app.py", "content": "print('hi')\n"}, ctx))
        res = json.loads(out)
        self.assertIn("edit_id", res)
        self.assertIn("commit_sha", res)

        read = self._run(_files._read_file({"path": "src/app.py"}, ctx))
        data = json.loads(read)
        self.assertEqual(data["content"], "print('hi')\n")
        self.assertEqual(len(data["lines"]), 1)
        self.assertEqual(data["lines"][0]["text"], "print('hi')")
        # hashline matches the store's _line_hash
        self.assertEqual(data["lines"][0]["hash"], _line_hash("print('hi')"))

    def test_list_files(self):
        ctx = _ctx()
        self._run(_files._write_file({"path": "a.txt", "content": "a\n"}, ctx))
        self._run(_files._write_file({"path": "sub/b.txt", "content": "b\n"}, ctx))
        out = self._run(_files._list_files({"path": "."}, ctx))
        names = json.loads(out)
        self.assertIn("a.txt", names)
        self.assertIn("sub/b.txt", names)

    def test_missing_agent_id_returns_error(self):
        ctx = ToolContext(user_id="u1", conversation_id="c1", db=_FakeDB(), agent_id=None)
        out = self._run(_files._write_file({"path": "x", "content": "y"}, ctx))
        self.assertIn("no workspace", out)

    def test_read_missing_file_returns_error(self):
        ctx = _ctx()
        out = self._run(_files._read_file({"path": "nope.txt"}, ctx))
        self.assertTrue(out.startswith("Error:"), out)

    def test_edit_lines_replaces_referenced_line(self):
        ctx = _ctx()
        self._run(_files._write_file({"path": "f.txt", "content": "a\nb\nc\n"}, ctx))
        read = json.loads(self._run(_files._read_file({"path": "f.txt"}, ctx)))
        hb = next(l["hash"] for l in read["lines"] if l["text"] == "b")
        out = self._run(_files._edit_lines({"path": "f.txt", "old_hashes": [hb], "new_content": "B\n"}, ctx))
        self.assertIn("edit_id", json.loads(out))
        read2 = json.loads(self._run(_files._read_file({"path": "f.txt"}, ctx)))
        self.assertEqual(read2["content"], "a\nB\nc\n")

    def test_edit_lines_hash_mismatch_is_error(self):
        ctx = _ctx()
        self._run(_files._write_file({"path": "f.txt", "content": "a\nb\n"}, ctx))
        out = self._run(_files._edit_lines({"path": "f.txt", "old_hashes": [_line_hash("nope")], "new_content": "x\n"}, ctx))
        self.assertTrue(out.startswith("Error:"), out)
        self.assertIn("changed", out)

    def test_edit_patch_applies_diff(self):
        if not _patch_available():
            self.skipTest("the `patch` binary is not on PATH (Windows host)")
        ctx = _ctx()
        self._run(_files._write_file({"path": "f.txt", "content": "one\ntwo\n"}, ctx))
        patch = "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n-one\n+ONE\n two\n"
        out = self._run(_files._edit_patch({"path": "f.txt", "patch": patch}, ctx))
        self.assertIn("edit_id", json.loads(out))
        read = json.loads(self._run(_files._read_file({"path": "f.txt"}, ctx)))
        self.assertIn("ONE", read["content"])

    def test_edit_patch_rejects_escaping_diff_headers(self):
        """Regression: the diff body's own ---/+++ header paths are what
        `patch -p1` opens — they must pass the same _resolveInside rules as the
        path argument, or a crafted diff targets files outside the workspace."""
        ctx = _ctx()
        self._run(_files._write_file({"path": "f.txt", "content": "one\n"}, ctx))
        for bad in (
            "--- a/../../victim.txt\n+++ b/../../victim.txt\n@@ -1 +1 @@\n-x\n+y\n",
            "--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
            "--- a/sub/../../victim.txt\n+++ b/sub/../../victim.txt\n@@ -1 +1 @@\n-x\n+y\n",
        ):
            out = self._run(_files._edit_patch({"path": "f.txt", "patch": bad}, ctx))
            self.assertTrue(out.startswith("Error:"), f"expected rejection for {bad!r}, got {out!r}")

    def test_patch_header_paths_helper(self):
        """Pure helper: extracts ---/+++ targets, handles timestamps and /dev/null."""
        from app.services.workspace.store import _patch_header_paths, _validate_patch_targets
        paths = _patch_header_paths(
            "--- a/src/x.py\t2026-01-01 00:00:00\n"
            "+++ b/src/x.py\n"
            "+++ /dev/null\n"
            "--- /dev/null\n"
            "--- a/new_file.txt\n"
        )
        self.assertEqual(paths, ["a/src/x.py", "b/src/x.py", "a/new_file.txt"])
        # git-style a//b prefixes strip to valid inside targets → no raise
        _validate_patch_targets(pathlib.Path("."), "--- a/f.txt\n+++ b/f.txt\n")
        # a degenerate `--- a/` header is skipped (patch fails later on the
        # empty filename) but must NOT bypass validation of the other headers
        with self.assertRaises(Exception) as ctxm:
            _validate_patch_targets(pathlib.Path("."), "--- a/\t2026\n+++ b/../../victim.txt\n")
        self.assertEqual(getattr(ctxm.exception, "status_code", None), 422)

    def test_write_with_stale_expected_hashes_conflicts(self):
        ctx = _ctx()
        self._run(_files._write_file({"path": "f.txt", "content": "a\n"}, ctx))
        out = self._run(_files._write_file(
            {"path": "f.txt", "content": "b\n", "expected_hashes": [_line_hash("stale")]}, ctx
        ))
        self.assertTrue(out.startswith("Error:"), out)
        self.assertIn("changed", out)


class BashToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _bash is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        import app.services.workspace.store as store_mod
        self._orig = store_mod._workspace_path
        store_mod._workspace_path = lambda u, a: self.root / str(u) / str(a)
        self.addCleanup(lambda: setattr(store_mod, "_workspace_path", self._orig))

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_missing_cmd_returns_error(self):
        out = self._run(_bash._bash({}, _ctx()))
        self.assertIn("command is required", out)

    def test_command_too_long(self):
        out = self._run(_bash._bash({"command": "x" * 9000}, _ctx()))
        self.assertIn("too long", out)

    def test_mock_sandbox_returns_structured_json(self):
        # get_sandbox() returns MockSandbox when code execution is disabled.
        from unittest.mock import patch
        from app.core.config import settings
        with patch.object(settings, "ENABLE_CODE_EXECUTION", False):
            out = self._run(_bash._bash({"command": "echo hi", "workdir": "."}, _ctx()))
        data = json.loads(out)
        self.assertIn("stdout", data)
        self.assertIn("exit_code", data)
        self.assertEqual(data["exit_code"], 0)

    def test_quota_exceeded_after_exec_returns_413(self):
        """F9 regression: bash must be held to the same workspace quota as the
        file tools. A workspace over quota returns a structured 413 result, not a
        free pass."""
        from unittest.mock import patch
        import app.services.workspace.store as store_mod
        from app.core.config import settings

        async def scenario():
            # Write a file into the workspace so du_mb > 0.
            with patch.object(settings, "ENABLE_CODE_EXECUTION", False):
                # force quota_mb() to 0 so any content exceeds it
                with patch.object(store_mod.WorkspaceStore, "quota_mb", return_value=0.0):
                    out = await _bash._bash({"command": "echo hi", "workdir": "."}, _ctx())
            data = json.loads(out)
            self.assertEqual(data["exit_code"], 413)
            self.assertIn("quota exceeded", data["stderr"])
            self.assertTrue(data.get("truncated"))
        self._run(scenario())


if __name__ == "__main__":
    unittest.main()
