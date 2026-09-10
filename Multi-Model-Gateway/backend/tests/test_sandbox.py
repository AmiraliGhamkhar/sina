"""Unit tests for the Sandbox seam (services/sandbox/).

Stdlib unittest only — no pytest dependency. The seam is an execution boundary
for the T3 code-execution story: a `Sandbox` Protocol (`exec(cmd, workdir,
user_id, agent_id) → ExecResult`) with two adapters — `MockSandbox` (offline
echo, never touches the filesystem) and `HttpSandbox` (httpx → sandbox:8001).
`get_sandbox()` selects the backend. These tests are offline: `MockSandbox` is
exercised directly, `get_sandbox()` selection is checked by patching settings,
and `HttpSandbox` is exercised against a stubbed httpx client (no network). If
the package can't be imported in this environment, the whole suite skips.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import httpx as _httpx
    from app.core.config import settings
    from app.services.sandbox.protocol import ExecResult, Sandbox
    from app.services.sandbox.mock import MockSandbox
    from app.services.sandbox.http import HttpSandbox
    from app.services.sandbox.factory import get_sandbox
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    _httpx = None
    settings = None
    ExecResult = None
    Sandbox = None
    MockSandbox = None
    HttpSandbox = None
    get_sandbox = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class ExecResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if ExecResult is None:
            raise unittest.SkipTest(
                f"app.services.sandbox import failed in this env: {_IMPORT_ERROR}"
            )

    def test_defaults_truncated_to_false(self):
        r = ExecResult(stdout="", stderr="", exit_code=0)
        self.assertFalse(r.truncated)

    def test_holds_stdout_stderr_exit_code(self):
        r = ExecResult(stdout="out", stderr="err", exit_code=3, truncated=True)
        self.assertEqual(r.stdout, "out")
        self.assertEqual(r.stderr, "err")
        self.assertEqual(r.exit_code, 3)
        self.assertTrue(r.truncated)


class SandboxProtocolTests(unittest.TestCase):
    """The protocol is a real seam: two adapters must both satisfy it."""

    @classmethod
    def setUpClass(cls):
        if Sandbox is None or MockSandbox is None or HttpSandbox is None:
            raise unittest.SkipTest(
                f"app.services.sandbox import failed in this env: {_IMPORT_ERROR}"
            )

    def test_both_adapters_are_sandbox(self):
        # isinstance against a typing.Protocol is not checkable, but calling
        # exec on both within asyncio proves they implement the same surface.
        loop = asyncio.new_event_loop()
        try:
            mock = MockSandbox()
            http = HttpSandbox(base_url="http://sandbox:8001")
            for sb in (mock, http):
                res = loop.run_until_complete(sb.exec("echo hi", "/wd", "u1", "a1"))
                self.assertIsInstance(res, ExecResult)
        finally:
            loop.close()


class MockSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MockSandbox is None:
            raise unittest.SkipTest(
                f"app.services.sandbox import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        self.sb = MockSandbox()

    def test_echoes_command_and_workdir(self):
        r = asyncio.run(self.sb.exec("ls -la", "/work", "u1", "a1"))
        self.assertEqual(r.exit_code, 0)
        self.assertIn("mock: ls -la", r.stdout)
        self.assertIn("@ /work", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_never_touches_filesystem(self):
        """The mock just echoes — it must not create or read anything on disk."""
        r = asyncio.run(self.sb.exec("cat /etc/passwd", "/work", "u1", "a1"))
        self.assertIn("mock: cat /etc/passwd", r.stdout)
        self.assertNotIn("root:", r.stdout)

    def test_fail_keyword_returns_exit_1(self):
        r = asyncio.run(self.sb.exec("this will fail", "/wd", "u1", "a1"))
        self.assertEqual(r.exit_code, 1)
        self.assertEqual(r.stderr, "mock failure")

    def test_long_output_is_truncated(self):
        with patch.object(settings, "TOOL_RESULT_MAX_CHARS", 20):
            r = asyncio.run(self.sb.exec("x" * 100, "/wd", "u1", "a1"))
            self.assertTrue(r.truncated)
            self.assertLessEqual(len(r.stdout), 20)


class HttpSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if HttpSandbox is None:
            raise unittest.SkipTest(
                f"app.services.sandbox import failed in this env: {_IMPORT_ERROR}"
            )

    def _mock_client(self, payload, status=200):
        """Build a fake httpx.AsyncClient that returns the given payload."""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=payload)
        resp.status_code = status

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=resp)
        return client

    def test_posts_to_exec_and_maps_result(self):
        client = self._mock_client({"stdout": "hi", "stderr": "", "exit_code": 0})
        with patch("app.services.sandbox.http.httpx.AsyncClient", return_value=client):
            sb = HttpSandbox(base_url="http://sandbox:8001")
            r = asyncio.run(sb.exec("echo hi", "/wd", "u1", "a1"))
        self.assertEqual(r.stdout, "hi")
        self.assertEqual(r.exit_code, 0)
        self.assertFalse(r.truncated)
        # the POST body carries cmd/workdir/user_id/agent_id
        _, kwargs = client.post.call_args
        self.assertEqual(kwargs["json"]["cmd"], "echo hi")
        self.assertEqual(kwargs["json"]["workdir"], "/wd")
        self.assertEqual(kwargs["json"]["user_id"], "u1")
        self.assertEqual(kwargs["json"]["agent_id"], "a1")

    def test_timeout_returns_exit_124(self):
        with patch(
            "app.services.sandbox.http.httpx.AsyncClient",
            side_effect=_httpx.TimeoutException("t"),
        ):
            sb = HttpSandbox(base_url="http://sandbox:8001")
            r = asyncio.run(sb.exec("sleep 10", "/wd", "u1", "a1"))
        self.assertEqual(r.exit_code, 124)
        self.assertIn("timed out", r.stderr)

    def test_http_error_returns_exit_1(self):
        client = self._mock_client({}, status=500)
        resp = client.post.return_value
        resp.raise_for_status = MagicMock(
            side_effect=_httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock())
        )
        with patch("app.services.sandbox.http.httpx.AsyncClient", return_value=client):
            sb = HttpSandbox(base_url="http://sandbox:8001")
            r = asyncio.run(sb.exec("cat x", "/wd", "u1", "a1"))
        self.assertEqual(r.exit_code, 1)
        self.assertIn("sandbox error", r.stderr)


class GetSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if get_sandbox is None or MockSandbox is None or HttpSandbox is None:
            raise unittest.SkipTest(
                f"app.services.sandbox import failed in this env: {_IMPORT_ERROR}"
            )

    def test_mock_when_code_execution_disabled(self):
        with patch.object(settings, "ENABLE_CODE_EXECUTION", False):
            sb = get_sandbox()
            self.assertIsInstance(sb, MockSandbox)

    def test_mock_when_no_url_configured(self):
        with (
            patch.object(settings, "ENABLE_CODE_EXECUTION", True),
            patch.object(settings, "SANDBOX_URL", ""),
        ):
            sb = get_sandbox()
            self.assertIsInstance(sb, MockSandbox)

    def test_http_when_enabled_and_url_set(self):
        with (
            patch.object(settings, "ENABLE_CODE_EXECUTION", True),
            patch.object(settings, "SANDBOX_URL", "http://sandbox:8001"),
        ):
            sb = get_sandbox()
            self.assertIsInstance(sb, HttpSandbox)


if __name__ == "__main__":
    unittest.main()
