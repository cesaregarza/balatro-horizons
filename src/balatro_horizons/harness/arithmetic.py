"""Restricted, bounded decimal arithmetic for agent helper requests."""

import ast
import operator
from decimal import Decimal

from balatro_horizons.config import MAX_ARITHMETIC_NODES


def arithmetic(expression):
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > MAX_ARITHMETIC_NODES:
        raise ValueError("EXPRESSION_TOO_COMPLEX")
    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
    }

    def walk(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = Decimal(str(node.value))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            result = walk(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in ops:
            result = ops[type(node.op)](walk(node.left), walk(node.right))
        else:
            raise ValueError("UNSUPPORTED_ARITHMETIC")
        if not result.is_finite() or abs(result) > Decimal("1e100"):
            raise ValueError("ARITHMETIC_MAGNITUDE_LIMIT")
        return result

    return str(walk(tree.body))
