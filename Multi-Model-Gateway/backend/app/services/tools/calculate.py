"""First-party tool: safe arithmetic expression evaluator.

Parses the expression with ``ast.parse(expr, mode="eval")`` and evaluates it
via a recursive AST whitelist walker — never eval/exec. Only a fixed set of
node types, operators, names, and functions is admitted; anything else is
rejected with an error string.

Supported syntax:
  - integers and floats
  - binary operators + - * / // % **, unary + and -
  - parentheses for grouping
  - constants: pi, e
  - functions: sqrt, abs, round, min, max, pow, exp, log, log10, floor,
    ceil, sin, cos, tan (round accepts one or two arguments)

Rejected: attribute access, indexing, collections, comprehensions, strings,
imports, lambdas, and any other AST construct not listed above. The input is
capped at 500 characters and the parsed AST at 40 levels of nesting.
Exponentiation is bounded (exponent abs <= 100_000, int results <= 2_000_000
bits) and results must be real ints/floats — complex numbers are errors.
"""
import ast
import math
import sys

from app.services.tools.registry import Tool, ToolContext, register

MAX_EXPRESSION_LENGTH = 500
MAX_AST_DEPTH = 40
# Exponent/result bounds keep evaluation cheap: any single exponent beyond
# +/-100_000 or any int result beyond 2_000_000 bits (about 600k decimal
# digits) is rejected before it can burn CPU or memory.
MAX_EXPONENT = 100_000
MAX_INT_BITS = 2_000_000

_ALLOWED_NAMES = frozenset({"pi", "e"})

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


class _UnsupportedError(Exception):
    """Internal: the walker met a construct outside the whitelist."""


def _would_overflow_int_bits(base, exp) -> bool:
    """Conservative pre-flight: would int(base) ** int(exp) exceed MAX_INT_BITS?

    For positive ints the result satisfies bit_length(base ** exp) <=
    exp * bit_length(base), so that product being over the cap means the
    computed value would be too big to even hold. Checking before calling
    pow() stops chained exponentiation (e.g. (2**100000)**100000) from
    allocating a multi-gigabyte intermediate. The check is intentionally an
    over-approximation: borderline values get a "result too large" error,
    which is preferable to an OOM.
    """
    return (
        isinstance(base, int)
        and isinstance(exp, int)
        and exp > 0
        and base.bit_length() * exp > MAX_INT_BITS
    )


def _pow(base, exp, mod=None):
    """Builtin pow() with the same exponent/result bounds as the ** operator."""
    if isinstance(exp, int) and abs(exp) > MAX_EXPONENT:
        raise _UnsupportedError("exponent too large")
    if mod is None and _would_overflow_int_bits(base, exp):
        raise _UnsupportedError("result too large")
    result = pow(base, exp, mod) if mod is not None else pow(base, exp)
    if isinstance(result, int) and result.bit_length() > MAX_INT_BITS:
        raise _UnsupportedError("result too large")
    return result


_ALLOWED_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": _pow,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}


def _eval_node(node: ast.AST, depth: int):
    """Recursively evaluate one whitelisted AST node (see module docstring).

    Raises _UnsupportedError for anything outside the whitelist and lets the
    underlying math errors (ZeroDivisionError, OverflowError, ValueError,
    TypeError) propagate to evaluate_expression.
    """
    if depth > MAX_AST_DEPTH:
        raise _UnsupportedError("expression too deeply nested")

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth + 1)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise _UnsupportedError("expression contains unsupported syntax")

    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_NAMES:
            raise _UnsupportedError(
                f"unknown name '{node.id}' (only pi and e are allowed)"
            )
        if not isinstance(node.ctx, ast.Load):
            raise _UnsupportedError("expression contains unsupported syntax")
        return math.pi if node.id == "pi" else math.e

    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise _UnsupportedError("expression contains unsupported syntax")
        left = _eval_node(node.left, depth + 1)
        right = _eval_node(node.right, depth + 1)
        # Bound exponentiation before it can run: a huge int exponent (either
        # sign) would otherwise trigger an enormous pow() before the result
        # size check below could ever run.
        if isinstance(node.op, ast.Pow) and isinstance(right, int) and abs(right) > MAX_EXPONENT:
            raise _UnsupportedError("exponent too large")
        # Reject before computing when the result is guaranteed to blow past the
        # cap (chained exponentiation like (2**100000)**100000 would otherwise
        # allocate a multi-GB int before the post-compute check could run).
        if _would_overflow_int_bits(left, right):
            raise _UnsupportedError("result too large")
        result = op(left, right)
        # Belt-and-braces for chained exponentiation (e.g. (2**100000)**100000):
        # the final result of any op is capped even when every intermediate
        # exponent looked small.
        if isinstance(result, int) and result.bit_length() > MAX_INT_BITS:
            raise _UnsupportedError("result too large")
        return result

    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UNARYOPS.get(type(node.op))
        if op is None:
            raise _UnsupportedError("expression contains unsupported syntax")
        return op(_eval_node(node.operand, depth + 1))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _UnsupportedError("expression contains unsupported syntax")
        fn = _ALLOWED_FUNCS.get(node.func.id)
        if fn is None:
            raise _UnsupportedError(f"unsupported function '{node.func.id}'")
        if node.keywords:
            raise _UnsupportedError("expression contains unsupported syntax")
        args = [_eval_node(arg, depth + 1) for arg in node.args]
        return fn(*args)

    raise _UnsupportedError("expression contains unsupported syntax")


def _int_to_str(value: int) -> str:
    """str() for ints, temporarily raising Python's int->str digit safety cap.

    Since 3.11, CPython refuses to stringify ints with more than 4300 decimal
    digits by default. Results here are already bounded by MAX_INT_BITS (about
    600k decimal digits), so the cap is raised just for this conversion and
    restored right after — the window is small and the work is bounded.
    """
    if hasattr(sys, "get_int_max_str_digits"):
        # ~log10(2) * bit_length, rounded up — only touch the cap when needed.
        digits_estimate = value.bit_length() * 30103 // 100000 + 1
        if digits_estimate > sys.get_int_max_str_digits():
            old_limit = sys.get_int_max_str_digits()
            sys.set_int_max_str_digits(digits_estimate + 1)
            try:
                return str(value)
            finally:
                sys.set_int_max_str_digits(old_limit)
    return str(value)


def _format_result(value) -> str:
    """Render the numeric result: integral values as ints, floats to 10 sig figs.

    Only real ints and floats are formattable — anything else (bools,
    complex numbers, or any unexpected type) is reported as an error string
    rather than stringified.
    """
    if isinstance(value, bool):
        return "Error: non-real result"  # unreachable — bools are rejected before evaluation
    if isinstance(value, int):
        return _int_to_str(value)
    if isinstance(value, float):
        if math.isclose(value, round(value), abs_tol=1e-10):
            return str(int(round(value)))
        return f"{value:.10g}"
    return "Error: non-real result"


def evaluate_expression(expr: str) -> str:
    """Safely evaluate a mathematical expression and return the result as a string.

    The expression is parsed with ast.parse(expr, mode="eval") and evaluated by a
    recursive whitelist walker; eval/exec are never used. Supported syntax: numeric
    literals, binary + - * / // % **, unary + and -, parentheses, the constants pi
    and e, and the functions sqrt, abs, round, min, max, pow, exp, log, log10,
    floor, ceil, sin, cos, tan. Everything else returns "Error: ...".

    Guard rails: expression length <= 500, AST depth <= 40, exponentiation
    exponents bounded to abs <= 100_000 (both ** and pow), integer results
    bounded to 2_000_000 bits, non-real (complex) results rejected, and
    arithmetic errors (division by zero, math domain, overflow) and
    non-finite results all come back as "Error: ..." strings rather than
    raising.
    """
    if not isinstance(expr, str):
        return "Error: expression must be a string"
    expr = expr.strip()
    if not expr:
        return "Error: empty expression"
    if len(expr) > MAX_EXPRESSION_LENGTH:
        return "Error: expression too long"

    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return "Error: invalid expression"

    try:
        result = _eval_node(tree, 0)
        if isinstance(result, float) and not math.isfinite(result):
            return "Error: non-finite result"
        return _format_result(result)
    except _UnsupportedError as exc:
        return f"Error: {exc}"
    except (ZeroDivisionError, OverflowError, ValueError, TypeError) as exc:
        return f"Error: {exc}"


async def _calculate(args: dict, ctx: ToolContext) -> str:
    return evaluate_expression(str(args.get("expression", "")).strip())


register(Tool(
    name="calculate",
    description=(
        "Evaluate a mathematical expression and return the numeric result. "
        "Supports + - * / % ** //, parentheses, the constants pi and e, and the "
        "functions sqrt, abs, round, min, max, pow, exp, log, log10, floor, ceil, "
        "sin, cos, tan."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The mathematical expression to evaluate",
            },
        },
        "required": ["expression"],
    },
    handler=_calculate,
))
