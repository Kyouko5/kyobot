"""Arithmetic tool: the Phase 2 way to prove tool calling end to end."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

from myagent.tools.base import Tool, ToolResult, schema_copy

__all__ = ["CalculatorTool"]

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "Arithmetic expression, e.g. '(12+8)*3' or '2**10'. "
            "Only numbers, parentheses and + - * / // % ** are allowed.",
        }
    },
    "required": ["expression"],
    "additionalProperties": False,
}

_Number = int | float

_OPERATORS: dict[type[ast.operator], Callable[[_Number, _Number], _Number]] = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
    ast.Pow: lambda left, right: left**right,
}

_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[_Number], _Number]] = {
    ast.UAdd: lambda value: +value,
    ast.USub: lambda value: -value,
}

_MAX_EXPONENT = 64


class ExpressionError(ValueError):
    """The expression is not something this tool is willing to evaluate."""


def _evaluate(expression: str) -> _Number:
    """Evaluate a numeric expression through an AST allow-list (never ``eval``)."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"not a valid expression ({exc.msg})") from exc
    return _evaluate_node(tree.body)


def _evaluate_node(node: ast.expr) -> _Number:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        operation = _OPERATORS.get(type(node.op))
        if operation is None:
            raise ExpressionError(f"operator {type(node.op).__name__} is not supported")
        left = _evaluate_node(node.left)
        right = _evaluate_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ExpressionError(f"exponent must be at most {_MAX_EXPONENT}")
        try:
            return operation(left, right)
        except ZeroDivisionError as exc:
            raise ExpressionError("division by zero") from exc
        except OverflowError as exc:
            raise ExpressionError("the result is too large") from exc
    if isinstance(node, ast.UnaryOp):
        unary = _UNARY_OPERATORS.get(type(node.op))
        if unary is None:
            raise ExpressionError(f"operator {type(node.op).__name__} is not supported")
        return unary(_evaluate_node(node.operand))
    raise ExpressionError(f"{type(node).__name__} is not supported")


class CalculatorTool(Tool):
    """A read-only, concurrency-safe arithmetic evaluator."""

    name = "calculator"
    description = "Evaluate a basic arithmetic expression with + - * / // % ** and parentheses."
    read_only = True

    @property
    def parameters(self) -> dict[str, Any]:
        return schema_copy(_SCHEMA)

    async def execute(self, expression: str = "", **kwargs: Any) -> ToolResult:
        try:
            value = _evaluate(expression)
        except ExpressionError as exc:
            return ToolResult.error(f"Error: cannot evaluate {expression!r}: {exc}")
        return ToolResult(f"{expression} = {value}")
