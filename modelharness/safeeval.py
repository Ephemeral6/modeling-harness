"""Small AST-whitelisted expression evaluator for numeric contracts."""
from __future__ import annotations

import ast
import math
from typing import Any, Callable


_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
    "sqrt": math.sqrt,
}
_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.Compare,
    ast.Call,
)
_COMPARATORS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _validate(
    tree: ast.AST,
    names: set[str],
    functions: set[str],
    *,
    max_depth: int,
    max_nodes: int,
) -> None:
    count = 0

    def visit(node: ast.AST, depth: int) -> None:
        nonlocal count
        count += 1
        if count > max_nodes:
            raise ValueError(f"expression exceeds {max_nodes} AST nodes")
        if depth > max_depth:
            raise ValueError(f"expression exceeds AST depth {max_depth}")
        if isinstance(node, ast.cmpop):
            if not isinstance(node, _COMPARATORS):
                raise ValueError(f"comparison operator is not allowed: {type(node).__name__}")
        elif not isinstance(node, _NODES):
            raise ValueError(f"AST node is not allowed: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in names | functions:
            raise ValueError(f"name is not allowed: {node.id}")
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or node.func.id not in functions
                or node.keywords
            ):
                raise ValueError("only whitelisted function-name calls are allowed")
        for child in ast.iter_child_nodes(node):
            visit(child, depth + 1)

    visit(tree, 1)


def evaluate(
    expression: str,
    names: dict[str, Any] | None = None,
    *,
    functions: dict[str, Callable[..., Any]] | None = None,
    max_depth: int = 32,
    max_nodes: int = 512,
) -> Any:
    """Evaluate a restricted expression with explicitly supplied names."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be a non-empty string")
    values = dict(names or {})
    callables = {**_FUNCTIONS, **(functions or {})}
    overlap = set(values) & set(callables)
    if overlap:
        raise ValueError(f"names shadow safe functions: {sorted(overlap)}")
    tree = ast.parse(expression, mode="eval")
    _validate(
        tree,
        set(values),
        set(callables),
        max_depth=max_depth,
        max_nodes=max_nodes,
    )
    return eval(  # noqa: S307 - the parsed tree is exhaustively whitelisted above.
        compile(tree, "<safeeval>", "eval"),
        {"__builtins__": {}},
        {**callables, **values},
    )
