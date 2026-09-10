"""MCP client manager.

Connects to the MCP servers configured in MCP_SERVERS (JSON list) and
surfaces their tools through the same registry as first-party tools, so the
agent loop treats them uniformly. Example config:

[{"name":"fs","transport":"stdio","command":"npx",
  "args":["-y","@modelcontextprotocol/server-filesystem","/data"]},
 {"name":"remote","transport":"sse","url":"http://mcp-host:8080/sse"}]

Connections live for the app's lifetime — opened and closed inside the
FastAPI lifespan (same task, so anyio cancel scopes stay valid; stdio servers
must have their command available inside the backend container).

MCP tools register as "mcp_<server>_<tool>" with first_party=False, so they
are deny-by-default until a user grants them via the permissions endpoint.
"""
import json
import logging
import re
from contextlib import AsyncExitStack

from app.core.config import settings
from app.services.tools import registry

logger = logging.getLogger(__name__)

_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")


class MCPManager:
    def __init__(self):
        self._stack: AsyncExitStack | None = None
        self._tool_names: list[str] = []

    async def startup(self) -> None:
        configs = self._load_configs()
        if not configs:
            return
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.sse import sse_client
            from mcp.client.stdio import stdio_client
        except ImportError:
            logger.warning("MCP_SERVERS is set but the 'mcp' package is not installed; MCP tools disabled")
            return

        self._stack = AsyncExitStack()
        for cfg in configs:
            name = cfg.get("name")
            if not name:
                logger.warning("skipping MCP server config without a 'name': %s", cfg)
                continue
            try:
                transport = cfg.get("transport", "stdio")
                if transport == "stdio":
                    params = StdioServerParameters(
                        command=cfg["command"],
                        args=cfg.get("args", []),
                        env=cfg.get("env"),
                    )
                    read, write = await self._stack.enter_async_context(stdio_client(params))
                elif transport == "sse":
                    read, write = await self._stack.enter_async_context(sse_client(cfg["url"]))
                else:
                    logger.warning("MCP server '%s': unknown transport '%s'", name, transport)
                    continue

                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                for tool in listed.tools:
                    self._register_tool(name, session, tool)
                logger.info("MCP server '%s' connected (%d tools)", name, len(listed.tools))
            except Exception as e:
                # a broken server must not take the app down
                logger.warning("MCP server '%s' failed to connect: %r", name, e)

    def _register_tool(self, server_name: str, session, tool) -> None:
        # function-calling APIs require ^[a-zA-Z0-9_-]{1,64}$ names
        full_name = _NAME_SAFE_RE.sub("_", f"mcp_{server_name}_{tool.name}")[:64]

        async def handler(args: dict, ctx: registry.ToolContext,
                          _session=session, _tool=tool.name) -> str:
            result = await _session.call_tool(_tool, arguments=args)
            parts = [c.text for c in result.content if getattr(c, "text", None)]
            text = "\n".join(parts) or "(empty result)"
            if getattr(result, "isError", False):
                return f"Error from MCP tool: {text}"
            return text

        registry.register(registry.Tool(
            name=full_name,
            description=tool.description or f"Tool '{tool.name}' from MCP server '{server_name}'",
            parameters=tool.inputSchema or {"type": "object", "properties": {}},
            handler=handler,
            first_party=False,
        ))
        self._tool_names.append(full_name)

    async def shutdown(self) -> None:
        for name in self._tool_names:
            registry.unregister(name)
        self._tool_names = []
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as e:
                logger.warning("error closing MCP connections: %r", e)
            self._stack = None

    @staticmethod
    def _load_configs() -> list[dict]:
        raw = settings.MCP_SERVERS.strip()
        if not raw:
            return []
        try:
            configs = json.loads(raw)
        except ValueError:
            logger.warning("MCP_SERVERS is not valid JSON; MCP tools disabled")
            return []
        if not isinstance(configs, list):
            logger.warning("MCP_SERVERS must be a JSON list; MCP tools disabled")
            return []
        return configs


mcp_manager = MCPManager()
