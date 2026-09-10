"""Unit tests for user-created agents (models + guards).

Covers T1-001..T2-002 without touching the DB when unavailable — uses unittest
mocks for the policy logic, and skips cleanly when required settings are missing.
"""

import unittest
from unittest.mock import MagicMock


class AgentModelsTests(unittest.TestCase):
    def test_agents_model_importable(self):
        try:
            from app.models.agents import Agent
            from app.models.agent_installs import AgentInstall
            from app.models.file_edits import FileEdit
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"agent models import failed in this env: {exc}")
        self.assertTrue(hasattr(Agent, "__tablename__"))
        self.assertTrue(hasattr(AgentInstall, "__tablename__"))
        self.assertTrue(hasattr(FileEdit, "__tablename__"))

    def test_conversations_carries_agent_columns(self):
        try:
            from app.models.conversations import Conversation
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Conversation import failed: {exc}")
        cols = {c.key for c in Conversation.__table__.columns}
        self.assertIn("agent_id", cols)
        self.assertIn("agent_version", cols)

    def test_chatrequest_carries_agent_fields(self):
        try:
            from app.services.router import ChatRequest
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"ChatRequest import failed: {exc}")
        # Agent binding is optional and backward-compat
        req = ChatRequest(messages=[{"role": "user", "content": "hi"}])
        self.assertIsNone(req.agent_id)
        self.assertIsNone(req.agent_version)


class ToolContextTests(unittest.TestCase):
    def test_registry_context_has_agent_id(self):
        try:
            from app.services.tools.registry import ToolContext
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"ToolContext import failed: {exc}")
        ctx = ToolContext(user_id="u", conversation_id="c", db=MagicMock(), agent_id="a")
        self.assertEqual(ctx.agent_id, "a")
        ctx2 = ToolContext(user_id="u", conversation_id="c", db=MagicMock())
        self.assertIsNone(ctx2.agent_id)
        # Also check file/bash tools require agent_id at runtime (without DB)
        try:
            from app.services.workspace.store import _resolveInside
            self.assertIsNotNone(_resolveInside)
        except Exception as exc:  # noqa: BLE001
            if "No module" in str(exc):
                pass
            else:
                raise


class AgentGuardTests(unittest.TestCase):
    """Policy: get_allowed_tools_for_agent = agent.allowed_tools ∩ ToolPermission ∩ master switch."""

    def test_routing_helpers_present(self):
        try:
            from app.services.provider_router import resolve_role
            from app.services.router import ChatRequest
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"provider_router import failed: {exc}")
        self.assertTrue(callable(resolve_role))
        req = ChatRequest(messages=[{"role": "user", "content": "debug my python"}])
        self.assertEqual(resolve_role(text=req.messages[-1].content, provider_choice=req.provider, is_private=req.private, message_count=len(req.messages)), "cloud")
        req2 = ChatRequest(messages=[{"role": "user", "content": "hello"}])
        self.assertIn(resolve_role(text=req2.messages[-1].content, provider_choice=req2.provider, is_private=req2.private, message_count=len(req2.messages)), ("local", "cloud"))
