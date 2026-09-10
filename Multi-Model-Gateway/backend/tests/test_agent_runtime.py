"""Unit tests for the AgentRuntime loop helpers (services/agent/runtime.py).

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): _estimate_tokens / _is_context_error / _prune_old_tool_rounds /
_MEMORY_WRITE_TOOLS are pure. Imported from the runtime module — the one the
live loop uses — so the tests cover the code that actually runs. Optional
runtime deps are stubbed only during the import so these run on a bare host too.
"""
import unittest

from tests.agent_test_stubs import import_with_stubs


def _load():
    from app.services.agent.runtime import (
        _estimate_tokens,
        _is_context_error,
        _prune_old_tool_rounds,
        _MEMORY_WRITE_TOOLS,
    )
    return _estimate_tokens, _is_context_error, _prune_old_tool_rounds, _MEMORY_WRITE_TOOLS


try:
    _estimate_tokens, _is_context_error, _prune_old_tool_rounds, _MEMORY_WRITE_TOOLS = import_with_stubs(_load)
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    _estimate_tokens = None
    _is_context_error = None
    _prune_old_tool_rounds = None
    _MEMORY_WRITE_TOOLS = None
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

    def test_content_is_coerced_and_blank_counts_overhead(self):
        # None content -> "None" string; empty string -> 4 tokens of overhead
        self.assertEqual(_estimate_tokens([{"role": "user"}]), 4)
        self.assertGreater(_estimate_tokens([{"role": "user", "content": "abcdefgh"}]), 4)


class IsContextErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _is_context_error is None:
            raise unittest.SkipTest(
                f"app.services.agent.runtime import failed in this env: {_IMPORT_ERROR}"
            )

    def test_context_indicators_are_flagged(self):
        for msg in (
            "This model's maximum context length is 8192 tokens",
            "Error: the prompt is too long",
            "Request too large for token limit",
            "prompt exceeds the context window",
        ):
            with self.subTest(msg=msg):
                self.assertTrue(_is_context_error(Exception(msg)))

    def test_non_context_errors_are_not_flagged(self):
        for msg in ("rate limit exceeded", "connection reset", "invalid api key", "5xx server error"):
            with self.subTest(msg=msg):
                self.assertFalse(_is_context_error(Exception(msg)))


class PruneOldToolRoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _prune_old_tool_rounds is None:
            raise unittest.SkipTest(
                f"app.services.agent.runtime import failed in this env: {_IMPORT_ERROR}"
            )

    def setUp(self):
        # essential prefix (system + user) = 8 tokens; round1 (assistant+2 tool) = 12;
        # round2 (assistant+2 tool) = 12; then a plain assistant answer = 4.
        self.messages = [
            {"role": "system", "content": "sys"},          # 4
            {"role": "user", "content": "u"},              # 4
            *_tool_round("a1", "r1", "r2"),                # 12
            *_tool_round("a2", "r3", "r4"),                # 12
            {"role": "assistant", "content": "final"},     # 4
        ]

    def assert_well_formed(self, messages):
        """Every tool-result run must immediately follow an assistant tool_calls.

        A round is [assistant(tool_calls), tool, tool, …]; consecutive tool
        messages are valid and all belong to that one assistant. So we scan from
        each assistant-with-tool_calls and require the following run of tools to
        be non-empty (and not to be interrupted by another role before a tool).
        """
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

    def test_prunes_in_place_and_returns_same_list(self):
        # total = 4(sys) + 4(user) + 12(round1: assistant+2 tool) + 12(round2) + 5(final) = 37.
        # Budget 25 → the oldest round (round1, 12) goes, leaving 25 which is not > 25.
        pruned = _prune_old_tool_rounds(self.messages, 25)
        self.assertIs(pruned, self.messages, "prunes in place")
        self.assertEqual(
            [m["role"] for m in pruned],
            ["system", "user", "assistant", "tool", "tool", "assistant"],
        )
        self.assert_well_formed(pruned)

    def test_under_budget_unchanged(self):
        before = [dict(m) for m in self.messages]
        pruned = _prune_old_tool_rounds(self.messages, 10_000)
        self.assertEqual(pruned, before)
        self.assertEqual(len(pruned), len(self.messages))

    def test_drop_all_keeps_essential_only(self):
        # drop_all removes every tool-call round; the final tool-less assistant
        # answer and the essential prefix (system + user) survive.
        pruned = _prune_old_tool_rounds(self.messages, 5, drop_all=True)
        self.assertEqual([m["role"] for m in pruned], ["system", "user", "assistant"])
        self.assert_well_formed(pruned)

    def test_drops_too_big_oldest_round_is_not_cut(self):
        # If the essential prefix alone exceeds budget, prune should NOT delete it.
        tiny = self.messages[:2]
        pruned = _prune_old_tool_rounds(tiny, 1)
        self.assertEqual([m["role"] for m in pruned], ["system", "user"])


class MemoryWriteToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _MEMORY_WRITE_TOOLS is None:
            raise unittest.SkipTest(
                f"app.services.agent.runtime import failed in this env: {_IMPORT_ERROR}"
            )

    def test_members_are_the_memory_mutators(self):
        self.assertEqual(
            tuple(_MEMORY_WRITE_TOOLS),
            ("memory_write", "memory_str_replace", "memory_append", "memory_delete"),
        )


if __name__ == "__main__":
    unittest.main()
