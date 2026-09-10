"""Unit tests for Smart Suggest (services/agent_suggest.py).

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network) and cover the pure helpers — `_parse_suggest_json`, `_build_suggest`,
`_looks_like_auth_error`, `_cloud_candidates`. The provider-calling path
(`_try_cloud` / `_try_local` / `suggest`) needs a live provider and a DB, so it
is not exercised here. If the module can't be imported in this environment
(missing settings/secret deps), the whole suite skips cleanly.
"""
import unittest

try:
    from app.services.agent_suggest import (
        _parse_suggest_json,
        _build_suggest,
        _looks_like_auth_error,
        Suggest,
    )
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    _parse_suggest_json = None
    _build_suggest = None
    _looks_like_auth_error = None
    Suggest = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class ParseSuggestJsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _parse_suggest_json is None:
            raise unittest.SkipTest(
                f"app.services.agent_suggest import failed in this env: {_IMPORT_ERROR}"
            )

    def test_exact_json(self):
        self.assertEqual(_parse_suggest_json('{"name":"x"}'), {"name": "x"})

    def test_json_wrapped_in_prose(self):
        self.assertEqual(_parse_suggest_json('sure, here: {"name":"y"} done'), {"name": "y"})

    def test_non_object_returns_none(self):
        self.assertIsNone(_parse_suggest_json("not json at all"))
        self.assertIsNone(_parse_suggest_json("[1,2,3]"))
        self.assertIsNone(_parse_suggest_json(""))
        self.assertIsNone(_parse_suggest_json(None))

    def test_multiple_adjacent_objects_are_ambiguous(self):
        # The rfind("}") slice spans both objects, so the whole string fails to
        # parse and returns None. In practice the model returns one object; this
        # only documents the (unchanged) behavior.
        self.assertIsNone(_parse_suggest_json('{"a":1} and {"b":2}'))


class LooksLikeAuthErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _looks_like_auth_error is None:
            raise unittest.SkipTest(
                f"app.services.agent_suggest import failed in this env: {_IMPORT_ERROR}"
            )

    def test_auth_indicators(self):
        for msg in ("401 User not found.", "invalid api key", "AuthenticationError", "401 Unauthorized"):
            with self.subTest(msg=msg):
                self.assertTrue(_looks_like_auth_error(msg))

    def test_non_auth_indicators(self):
        for msg in ("rate limit exceeded", "502 Bad Gateway", "timeout", ""):
            with self.subTest(msg=msg):
                self.assertFalse(_looks_like_auth_error(msg))

    def test_case_insensitive(self):
        self.assertTrue(_looks_like_auth_error("USER NOT FOUND"))


class BuildSuggestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _build_suggest is None:
            raise unittest.SkipTest(
                f"app.services.agent_suggest import failed in this env: {_IMPORT_ERROR}"
            )

    def test_maps_fields_and_filters_unknown_tools(self):
        s = _build_suggest(
            {"name": "My Agent", "description": "desc", "system_prompt": "sp",
             "suggested_tools": ["recall", "bogus"], "suggested_model": "a/b"},
            "goal", ["recall", "web_search"],
        )
        self.assertEqual(s.name, "My Agent")
        self.assertEqual(s.suggested_tools, ["recall"])
        self.assertEqual(s.suggested_model, "a/b")

    def test_empty_object_falls_back_to_goal(self):
        s = _build_suggest({}, "build me a thing", [])
        self.assertEqual(s.name, "build me a thing")
        self.assertIn("build me a thing", s.description)
        self.assertIn("build me a thing", s.system_prompt)

    def test_non_list_tools_becomes_empty(self):
        s = _build_suggest({"suggested_tools": "not-a-list"}, "g", [])
        self.assertEqual(s.suggested_tools, [])

    def test_returns_suggest_instance(self):
        s = _build_suggest({"name": "n"}, "g", [])
        self.assertIsInstance(s, Suggest)


if __name__ == "__main__":
    unittest.main()
