"""Unit tests for app.core.metrics_auth.metrics_authorized (the GET /metrics bearer gate).

Stdlib unittest only — no pytest dependency. Tests are offline and pure: the
helper is an exact string comparison over a stdlib-only module, so the import
never needs a full settings/secret environment.
"""
import unittest

from app.core.metrics_auth import metrics_authorized


class MetricsAuthorizedTests(unittest.TestCase):
    def test_correct_token_and_header_true(self):
        self.assertTrue(metrics_authorized("Bearer tok", "tok"))

    def test_lowercase_scheme_false(self):
        # "bearer" is not the exact scheme the route requires
        self.assertFalse(metrics_authorized("bearer tok", "tok"))

    def test_double_space_false(self):
        # "Bearer  tok" != "Bearer tok" (extra space changes the string)
        self.assertFalse(metrics_authorized("Bearer  tok", "tok"))

    def test_extra_value_after_token_false(self):
        self.assertFalse(metrics_authorized("Bearer tok extra", "tok"))

    def test_wrong_token_false(self):
        self.assertFalse(metrics_authorized("Bearer wrong", "tok"))

    def test_missing_header_false(self):
        self.assertFalse(metrics_authorized(None, "tok"))

    def test_empty_token_never_authorizes(self):
        # empty METRICS_TOKEN means the route 404s before this helper is
        # consulted, so no realistic header should ever pass here
        self.assertFalse(metrics_authorized("Bearer tok", ""))
        self.assertFalse(metrics_authorized(None, ""))


if __name__ == "__main__":
    unittest.main()
