"""Unit tests for the agent adapter's policy + tool-dispatch helpers.

Stdlib unittest only — no pytest dependency. Covers the DB-free surface of
`services/agent/agent.py`: the safety-ceiling tool filter
(`get_allowed_tools_for_agent`), the per-tenant policy (`get_allowed_tools`),
agent resolution (`_resolve_agent` → 404/403), the conversation binding
(`_ensure_conversation_agent_binding`), and `_execute_tool` (JSON parse /
timeout / truncation / failure-as-string). All run with a mocked DB session and
a stubbed tool registry — no network, no real rows. If the module can't be
imported in this environment, the whole suite skips cleanly.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.agent_test_stubs import import_with_stubs


def _load():
    from app.core.config import settings
    from app.services.agent.agent import (
        get_allowed_tools,
        get_allowed_tools_for_agent,
        _resolve_agent,
        _ensure_conversation_agent_binding,
        _CODE_TOOLS,
    )
    # _execute_tool is owned by the runtime (the module that runs the loop) and
    # re-exported by the package — the adapter's copy was deleted as dead code.
    from app.services.agent.runtime import _execute_tool
    from app.services.tools.registry import Tool, ToolContext, get_tool
    from app.services.router import ChatRequest
    return (settings, get_allowed_tools, get_allowed_tools_for_agent, _resolve_agent,
            _ensure_conversation_agent_binding, _execute_tool, _CODE_TOOLS, Tool,
            ToolContext, get_tool, ChatRequest)


try:
    (settings, get_allowed_tools, get_allowed_tools_for_agent, _resolve_agent,
     _ensure_conversation_agent_binding, _execute_tool, _CODE_TOOLS, Tool,
     ToolContext, get_tool, ChatRequest) = import_with_stubs(_load)
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    settings = None
    get_allowed_tools = None
    get_allowed_tools_for_agent = None
    _resolve_agent = None
    _ensure_conversation_agent_binding = None
    _execute_tool = None
    _CODE_TOOLS = None
    Tool = None
    ToolContext = None
    get_tool = None
    ChatRequest = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _tool(name: str, first_party: bool = True) -> Tool:
    """A Tool with a no-op handler returning a fixed string."""
    async def handler(_args, _ctx):
        return "ok"
    return Tool(name=name, description=f"{name} desc", parameters={"type": "object"},
                handler=handler, first_party=first_party)


# Registry is module-global; temporarily populate it for the policy tests.
class _RegistryToolsStub:
    """Context to swap registry.all_tools() without touching real registration."""

    def __init__(self, tools: list[Tool]):
        self._tools = tools

    def __enter__(self):
        self._orig = get_allowed_tools.__globals__["registry"]
        # get_allowed_tools calls registry.all_tools(); patch its method.
        self._patcher = patch.object(self._orig, "all_tools", return_value=self._tools)
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        self._patcher.stop()
        return False


def _db(rows_by_type: dict[str, list[SimpleNamespace]] | None = None):
    """A fake AsyncSession whose execute returns configured rows for any SELECT.

    `rows_by_type` maps a key ('agent'/'permission'/'conversation') to rows; a
    key that selects a model we looked up but isn't present returns [] so the
    caller's `scalar_one_or_none()` yields None (→ 404). Every query is awaited
    by the adapter, so execute is an async callable.
    """
    rows = rows_by_type or {}
    collection = {"agent": rows.get("agent", []),
                  "permission": rows.get("permission", []),
                  "conversation": rows.get("conversation", [])}

    async def _execute(stmt, *a, **k):
        sql = str(stmt)
        key = None
        for candidate in ("tool_permissions", "agents", "conversations"):
            if candidate in sql:
                key = {"tool_permissions": "permission", "agents": "agent", "conversations": "conversation"}[candidate]
        items = collection.get(key, [])
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items),
                               scalar_one_or_none=lambda: items[0] if items else None)

    session = MagicMock()
    session.execute = _execute
    session.commit = AsyncMock()
    return session


class _FakeAgent:
    def __init__(self, user_id="u1", is_public=False, allowed_tools=None, version=1, system_prompt="sp"):
        self.id = MagicMock()
        self.user_id = user_id
        self.is_public = is_public
        self.allowed_tools = allowed_tools or []
        self.version = version
        self.system_prompt = system_prompt


class _FakePermission:
    def __init__(self, tool_name, allowed):
        self.tool_name = tool_name
        self.allowed = allowed
        self.user_id = "u1"


class _FakeConversation:
    def __init__(self, agent_id=None, agent_version=None):
        self.id = MagicMock()
        self.agent_id = agent_id
        self.agent_version = agent_version


class SetupMixin:
    @classmethod
    def setUpClass(cls):
        if get_allowed_tools is None:
            raise unittest.SkipTest(
                f"app.services.agent import failed in this env: {_IMPORT_ERROR}"
            )


class GetAllowedToolsTests(SetupMixin, unittest.TestCase):
    def test_per_tenant_override_wins(self):
        tools = [_tool("first_party_t", first_party=True), _tool("mcp_t", first_party=False)]
        with _RegistryToolsStub(tools):
            db = _db({"permission": [_FakePermission("mcp_t", True), _FakePermission("first_party_t", False)]})
            allowed = asyncio.run(get_allowed_tools("u1", db))
        names = {t.name for t in allowed}
        # mcp_t explicitly granted despite being non-first-party; first_party_t denied.
        self.assertIn("mcp_t", names)
        self.assertNotIn("first_party_t", names)

    def test_default_first_party_allowed(self):
        tools = [_tool("a", first_party=True), _tool("b", first_party=False)]
        with _RegistryToolsStub(tools):
            allowed = asyncio.run(get_allowed_tools("u1", _db({"permission": []})))
        names = {t.name for t in allowed}
        self.assertIn("a", names)
        self.assertNotIn("b", names)

    def test_master_switch_gates_global_path(self):
        """Regression (C2): the legacy global path must apply the same
        ENABLE_CODE_EXECUTION ceiling as the agent path. A user could
        self-grant write_file via PUT /agent/tools/{name}/permission and then
        chat without an agent_id to bypass the switch entirely."""
        tools = [_tool("write_file", first_party=False), _tool("recall", first_party=True)]
        with _RegistryToolsStub(tools):
            # Switch OFF: write_file dropped even though it was granted; the
            # non-code tool (recall) is unaffected.
            with patch.object(settings, "ENABLE_CODE_EXECUTION", False):
                allowed = asyncio.run(get_allowed_tools("u1", _db({"permission": [_FakePermission("write_file", True), _FakePermission("recall", True)]})))
            self.assertEqual({t.name for t in allowed}, {"recall"})
            # Switch ON: the code tool returns.
            with patch.object(settings, "ENABLE_CODE_EXECUTION", True):
                allowed = asyncio.run(get_allowed_tools("u1", _db({"permission": [_FakePermission("write_file", True), _FakePermission("recall", True)]})))
        self.assertEqual({t.name for t in allowed}, {"write_file", "recall"})


class GetAllowedToolsForAgentTests(SetupMixin, unittest.TestCase):
    def test_intersection_of_agent_request_and_ceiling(self):
        tools = [_tool("write_file", False), _tool("read_file", False), _tool("recall", True)]
        agent = _FakeAgent(allowed_tools=["write_file", "read_file", "nothing"])
        with _RegistryToolsStub(tools):
            with patch.object(settings, "ENABLE_CODE_EXECUTION", True):
                allowed = asyncio.run(get_allowed_tools_for_agent(
                    "agent-1", None, "u1",
                    _db({"agent": [agent], "permission": [_FakePermission("write_file", True), _FakePermission("read_file", True)]}),
                ))
        names = {t.name for t in allowed}
        # write_file/read_file requested+allowed; 'nothing' isn't a tool; recall not requested.
        self.assertEqual(names, {"write_file", "read_file"})

    def test_master_switch_blocks_code_tools(self):
        tools = [_tool("write_file", False)]
        agent = _FakeAgent(allowed_tools=["write_file"])
        with _RegistryToolsStub(tools):
            with patch.object(settings, "ENABLE_CODE_EXECUTION", False):
                allowed = asyncio.run(get_allowed_tools_for_agent(
                    "agent-1", None, "u1",
                    _db({"agent": [agent], "permission": [_FakePermission("write_file", True)]}),
                ))
        self.assertEqual(allowed, [])

    def test_missing_agent_raises_not_found(self):
        with _RegistryToolsStub([]):
            with self.assertRaises(Exception) as ctx:
                asyncio.run(get_allowed_tools_for_agent("nope", None, "u1", _db({"agent": []})))
            self.assertEqual(getattr(ctx.exception, "status_code", None), 404)

    def test_private_agent_not_owner_is_forbidden(self):
        agent = _FakeAgent(user_id="owner", is_public=False, allowed_tools=[])
        with _RegistryToolsStub([]):
            with self.assertRaises(Exception) as ctx:
                asyncio.run(get_allowed_tools_for_agent("agent-1", None, "u1", _db({"agent": [agent]})))
            self.assertEqual(getattr(ctx.exception, "status_code", None), 403)

    def test_code_tools_membership(self):
        self.assertIn("bash", _CODE_TOOLS)
        self.assertIn("write_file", _CODE_TOOLS)


class ResolveAgentTests(SetupMixin, unittest.TestCase):
    def test_no_agent_id_returns_none_triple(self):
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
        out = asyncio.run(_resolve_agent(req, "u1", _db()))
        self.assertEqual(out, (None, None, None))

    def test_resolves_agent(self):
        agent = _FakeAgent(user_id="u1", is_public=False)
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}], agent_id="agent-1", agent_version=3)
        aid, aver, a = asyncio.run(_resolve_agent(req, "u1", _db({"agent": [agent]})))
        self.assertEqual(aid, "agent-1")
        self.assertEqual(aver, 3)
        self.assertIs(a, agent)

    def test_missing_agent_404(self):
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}], agent_id="agent-1")
        with self.assertRaises(Exception) as ctx:
            asyncio.run(_resolve_agent(req, "u1", _db({"agent": []})))
        self.assertEqual(getattr(ctx.exception, "status_code", None), 404)

    def test_private_not_owner_403(self):
        agent = _FakeAgent(user_id="owner", is_public=False)
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}], agent_id="agent-1")
        with self.assertRaises(Exception) as ctx:
            asyncio.run(_resolve_agent(req, "u1", _db({"agent": [agent]})))
        self.assertEqual(getattr(ctx.exception, "status_code", None), 403)

    def test_public_agent_anyone_reads(self):
        agent = _FakeAgent(user_id="owner", is_public=True)
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}], agent_id="agent-1")
        aid, _, a = asyncio.run(_resolve_agent(req, "u1", _db({"agent": [agent]})))
        self.assertIs(a, agent)


class EnsureBindingTests(SetupMixin, unittest.TestCase):
    def test_no_agent_id_does_nothing(self):
        db = _db()
        asyncio.run(_ensure_conversation_agent_binding("conv-1", None, None, None, db))
        db.commit.assert_not_called()

    def test_stamps_conversation_when_unbound(self):
        crow = _FakeConversation(agent_id=None, agent_version=None)
        db = _db({"conversation": [crow]})
        agent = _FakeAgent(version=5)
        asyncio.run(_ensure_conversation_agent_binding("conv-1", "agent-1", 2, agent, db))
        self.assertEqual(crow.agent_id, "agent-1")
        self.assertEqual(crow.agent_version, 2)
        db.commit.assert_called_once()

    def test_defaults_version_to_agent_when_not_provided(self):
        crow = _FakeConversation(agent_id=None)
        db = _db({"conversation": [crow]})
        agent = _FakeAgent(version=9)
        asyncio.run(_ensure_conversation_agent_binding("conv-1", "agent-1", None, agent, db))
        self.assertEqual(crow.agent_version, 9)

    def test_does_not_overwrite_existing_binding(self):
        crow = _FakeConversation(agent_id="existing", agent_version=7)
        db = _db({"conversation": [crow]})
        agent = _FakeAgent(version=5)
        asyncio.run(_ensure_conversation_agent_binding("conv-1", "new", 3, agent, db))
        self.assertEqual(crow.agent_id, "existing")
        db.commit.assert_not_called()


class ExecuteToolTests(SetupMixin, unittest.TestCase):
    def setUp(self):
        self.ctx = ToolContext(user_id="u1", conversation_id="c1", db=MagicMock())
    def test_invalid_json_is_error(self):
        out = asyncio.run(_execute_tool(_tool("t"), "not json", self.ctx))
        self.assertEqual(out, "Error: tool arguments were not valid JSON")

    def test_non_object_args_is_error(self):
        out = asyncio.run(_execute_tool(_tool("t"), "[1,2,3]", self.ctx))
        self.assertEqual(out, "Error: tool arguments must be a JSON object")

    def test_timeout_returns_error(self):
        async def slow(_args, _ctx):
            await asyncio.sleep(10)
            return "done"
        tool = Tool(name="slow", description="d", parameters={}, handler=slow, first_party=False)
        with patch.object(settings, "TOOL_TIMEOUT_SECONDS", 0.01):
            out = asyncio.run(_execute_tool(tool, "{}", self.ctx))
        self.assertIn("timed out", out)

    def test_handler_exception_is_generic_string(self):
        """F11: exception detail must NOT leak to the model — it can contain
        internal hostnames/IPs (e.g. an SSRF guard message). The runtime logs the
        detail server-side and returns only a generic error string."""
        async def boom(_args, _ctx):
            raise RuntimeError("kaboom")
        tool = Tool(name="boom", description="d", parameters={}, handler=boom, first_party=False)
        out = asyncio.run(_execute_tool(tool, "{}", self.ctx))
        self.assertEqual(out, "Error: tool 'boom' failed")

    def test_long_result_truncated(self):
        async def big(_args, _ctx):
            return "x" * 1000
        tool = Tool(name="big", description="d", parameters={}, handler=big, first_party=False)
        with patch.object(settings, "TOOL_RESULT_MAX_CHARS", 50):
            out = asyncio.run(_execute_tool(tool, "{}", self.ctx))
        self.assertTrue(out.endswith("\n[truncated]"))
        self.assertLessEqual(len(out), 50 + len("\n[truncated]"))


class MemoryToolDefaultDenyTests(SetupMixin, unittest.TestCase):
    """F7 regression on the REAL registry (loaded through the stubs helper, so it
    runs on a host without asyncpg). A fetched web page can say 'write X to
    /profile.md'; if the mutating memory tools were default-allowed the page could
    plant content that build_memory_context injects verbatim into every future
    system prompt. Only memory_read is default-allowed (reads don't persist
    injection); every mutating memory tool must require an explicit user grant."""

    @classmethod
    def setUpClass(cls):
        try:
            from app.services.tools import registry as real_registry
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"real registry unavailable: {exc}")
        super().setUpClass()
        cls.registry = real_registry

    def test_memory_read_is_default_allowed(self):
        self.assertTrue(self.registry.get_tool("memory_read").first_party)

    def test_mutating_memory_tools_are_deny_by_default(self):
        for name in ("memory_write", "memory_str_replace", "memory_append",
                     "memory_delete"):
            tool = self.registry.get_tool(name)
            self.assertIsNotNone(tool, f"tool '{name}' not registered")
            self.assertFalse(
                tool.first_party,
                f"{name} must be deny-by-default (first_party=False) — F7",
            )

    def test_memory_writes_not_in_default_allowlist_for_real_user(self):
        # With no explicit grants, get_allowed_tools must NOT surface any mutating
        # memory tool — proving the injection chain is broken at the tool gate by
        # default (a user must opt in via PUT /agent/tools/{name}/permission).
        tools = [
            _tool("memory_read", first_party=True),
            _tool("memory_write", first_party=False),
            _tool("memory_str_replace", first_party=False),
            _tool("memory_append", first_party=False),
            _tool("memory_delete", first_party=False),
        ]
        with _RegistryToolsStub(tools):
            allowed = asyncio.run(get_allowed_tools("u1", _db({"permission": []})))
        names = {t.name for t in allowed}
        self.assertIn("memory_read", names)
        for n in ("memory_write", "memory_str_replace", "memory_append", "memory_delete"):
            self.assertNotIn(n, names)  # no grant, no write capability


if __name__ == "__main__":
    unittest.main()
