"""Unit tests for the safe calculate tool (services/tools/calculate.py).

Stdlib unittest only — no pytest dependency. All tests exercise the pure
evaluate_expression function (no DB, no network). If the module can't be
imported in this environment (missing settings/secret deps), the whole suite
skips cleanly, following the test_comfy.py pattern.
"""
import unittest

try:
    from app.services.tools import registry
    from app.services.tools.calculate import evaluate_expression
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    registry = None
    evaluate_expression = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class EvaluateExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if evaluate_expression is None:
            raise unittest.SkipTest(
                f"app.services.tools.calculate import failed in this env: {_IMPORT_ERROR}"
            )

    def test_basic_arithmetic(self):
        self.assertEqual(evaluate_expression("2+2"), "4")
        self.assertEqual(evaluate_expression("2*(3+4)"), "14")
        self.assertEqual(evaluate_expression("2+2*2"), "6")  # precedence
        self.assertEqual(evaluate_expression("10/4"), "2.5")

    def test_operators(self):
        self.assertEqual(evaluate_expression("2**10"), "1024")
        self.assertEqual(evaluate_expression("7//2"), "3")
        self.assertEqual(evaluate_expression("7%3"), "1")

    def test_functions(self):
        self.assertEqual(evaluate_expression("sqrt(144)"), "12")
        self.assertEqual(evaluate_expression("abs(-5)"), "5")
        self.assertEqual(evaluate_expression("round(3.14159, 2)"), "3.14")
        self.assertEqual(evaluate_expression("min(3,1,2)"), "1")
        self.assertEqual(evaluate_expression("max(3,1,2)"), "3")

    def test_constants(self):
        self.assertTrue(evaluate_expression("pi").startswith("3.14159"))
        self.assertTrue(evaluate_expression("e").startswith("2.71828"))

    def test_rejects_unsupported_syntax(self):
        for expr in ("import os", "().__class__", "os.getcwd()", "a"):
            self.assertTrue(
                evaluate_expression(expr).startswith("Error:"),
                f"{expr!r} should have been rejected",
            )

    def test_rejects_math_errors(self):
        for expr in ("1/0", "sqrt(-1)"):
            self.assertTrue(
                evaluate_expression(expr).startswith("Error:"),
                f"{expr!r} should have been rejected",
            )

    def test_rejects_non_real_results(self):
        # Complex results (from a negative base raised to a fractional power)
        # must not be stringified — they come back as errors.
        for expr in ("(-1)**0.5", "pow(-1, 0.5)"):
            result = evaluate_expression(expr)
            self.assertTrue(
                result.startswith("Error:"),
                f"{expr!r} should have been rejected, got {result!r}",
            )
            self.assertIn("non-real", result)

    def test_rejects_absurd_exponents(self):
        # Huge int exponents would trigger an enormous pow() — guarded before
        # evaluation in both the ** operator and the pow() function.
        for expr in ("10**500000000", "pow(2, 500000000)"):
            result = evaluate_expression(expr)
            self.assertTrue(
                result.startswith("Error:"),
                f"{expr!r} should have been rejected, got {result!r}",
            )
            self.assertIn("exponent too large", result)

    def test_large_int_still_works(self):
        # (2**100000) has ~30k digits and stays well under the 2_000_000-bit
        # result cap — proof normal big ints work while absurd exponents are
        # caught.
        result = evaluate_expression("(2**100000)")
        self.assertFalse(result.startswith("Error:"), f"unexpected error: {result!r}")
        self.assertTrue(result.isdigit())
        self.assertGreater(len(result), 30000)

    def test_rejects_chained_exponentiation_overflow(self):
        # (2**100000)**100000 = 2**10_000_000_000 would need ~1.25 GB — every
        # intermediate exponent looks legal, so the result must be rejected
        # before it is ever computed.
        for expr in ("(2**100000)**100000", "pow(2**100000, 100000)"):
            result = evaluate_expression(expr)
            self.assertTrue(
                result.startswith("Error:"),
                f"{expr!r} should have been rejected, got {result!r}",
            )
            self.assertIn("result too large", result)

    def test_rejects_overlong_expression(self):
        self.assertTrue(evaluate_expression("1+1" * 200).startswith("Error:"))

    def test_rejects_deeply_nested_ast(self):
        # Parentheses don't produce AST nodes, so a long + chain is used to
        # nest BinOps 50 deep — past the 40-level cap.
        self.assertTrue(evaluate_expression("+".join(["1"] * 50)).startswith("Error:"))


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if registry is None:
            raise unittest.SkipTest(
                f"app.services.tools import failed in this env: {_IMPORT_ERROR}"
            )

    def test_calculate_is_registered(self):
        tool = registry.get_tool("calculate")
        self.assertIsNotNone(tool, "tool 'calculate' not registered")
        self.assertTrue(tool.first_party)


if __name__ == "__main__":
    unittest.main()
