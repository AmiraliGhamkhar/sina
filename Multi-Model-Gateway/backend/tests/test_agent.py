"""Unit tests for the R2 agent-loop helpers (services/agent/runtime.py).

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): _estimate_tokens / _is_context_error / _prune_old_tool_rounds are
pure functions. They are imported from the runtime module (the one the live
loop uses), not the adapter — so the tests cover the code that actually runs.
Optional runtime deps are stubbed only during the import so these run on a bare
host too.
"""
import unittest

from tests.agent_test_stubs import import_with_stubs


def _load():
    from app.services.agent.runtime import (
        _estimate_tokens,
        _is_context_error,
        _prune_old_tool_rounds,
    )
    return _estimate_tokens, _is_context_error, _prune_old_tool_rounds


try:
    _estimate_tokens, _is_context_error, _prune_old_tool_rounds = import_with_stubs(_load)
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    _estimate_tokens = None
    _is_context_error = None
    _prune_old_tool_rounds = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _tool_round(assistant_content="x", *tool_contents):
    """One assistant-with-tool_calls message plus its tool result messages."""
    msgs = [{
        "role": "assistant",
        "content": assistant_content,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "recall_recent_exchanges", "arguments": "{}"}}],
    }]
    for i, content in enumerate(tool_contents):
        msgs.append({"role": "tool", "tool_call_id": f"call_1_{i}", "content": content})
    return msgs


class EstimateTokensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _estimate_tokens is None:
            raise unittest.SkipTest(
                f"app.services.agent.runtime import failed in this env: {_IMPORT_ERROR}"
            )

    def test_empty_list_is_zero(self):
        self.assertEqual(_estimate_tokens([]), 0)

    def test_larger_messages_estimate_higher(self):
        small = _estimate_tokens([{"role": "user", "content": "hi"}])
        large = _estimate_tokens([{"role": "user", "content": "x" * 400}])
        self.assertGreater(large, small)


class IsContextErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _is_context_error is None:
            raise unittest.SkipTest(
                f"app.services.agent.runtime import failed in this env: {_IMPORT_ERROR}"
            )

    def test_context_indicators(self):
        for message in (
            "This model's maximum context length is 128000 tokens",
            "Requested tokens exceed the context window",
            "too many tokens in the prompt",
        ):
            self.assertTrue(_is_context_error(Exception(message)), message)

    def test_non_context_errors(self):
        for message in ("connection reset", "rate limit", ""):
            self.assertFalse(_is_context_error(Exception(message)), message)


class PruneOldToolRoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _prune_old_tool_rounds is None:
            raise unittest.SkipTest(
                f"app.services.agent.runtime import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        # every message has 1 char of content → exactly 4 estimated tokens each
        self.messages = (
            [{"role": "system", "content": "s"},
             {"role": "user", "content": "u"}]
            + _tool_round("a1", "t1", "t2")
            + _tool_round("a2", "t3")
        )

    def assert_well_formed(self, messages):
        """Rounds stay intact: a run of tool messages must immediately follow
        an assistant-with-tool_calls (consecutive tools = one assistant's
        multiple results); no orphaned halves."""
        i = 0
        while i < len(messages):
            if messages[i].get("role") == "assistant" and "tool_calls" in messages[i]:
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    j += 1
                self.assertGreater(j, i + 1, "assistant with tool_calls has no tool result")
                i = j
            else:
                self.assertNotEqual(messages[i].get("role"), "tool",
                                    "tool message not preceded by a tool_calls assistant")
                i += 1

    def test_prunes_oldest_round_only(self):
        # total = 7 * 4 = 28 tokens; essential + round2 = 8 + 8 = 16
        # → only the oldest round (12 tokens) goes
        pruned = _prune_old_tool_rounds(self.messages, 16)
        self.assertIs(pruned, self.messages, "prunes in place")
        self.assertEqual([m["role"] for m in pruned], ["system", "user", "assistant", "tool"])
        self.assert_well_formed(pruned)

    def test_under_budget_unchanged(self):
        before = [dict(m) for m in self.messages]
        pruned = _prune_old_tool_rounds(self.messages, 10_000)
        self.assertEqual(pruned, before)
        self.assertEqual(len(pruned), 7)

    def test_drop_all_keeps_essential_only(self):
        pruned = _prune_old_tool_rounds(self.messages, 5, drop_all=True)
        self.assertEqual([m["role"] for m in pruned], ["system", "user"])
        self.assert_well_formed(pruned)


if __name__ == "__main__":
    unittest.main()
