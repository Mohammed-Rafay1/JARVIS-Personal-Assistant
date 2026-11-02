"""Exact spoken arithmetic ("what's 18% of 4,500", "square root of 144").

Small local LLMs get arithmetic wrong surprisingly often, so numbers never go to the model.
"""
from __future__ import annotations

import ast
import math
import operator
import re

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
_PREFIX = re.compile(r"^(?:what(?:'s| is)|calculate|compute|how much is|tell me|work out|whats|what are)\s+")
_WORDS = [
    (r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+", r"(\1/100)*"),
    (r"(\d+(?:\.\d+)?)\s*(?:%|percent)", r"(\1/100)"),
    (r"\bsquare root of\s+([\d.]+)", r"sqrt(\1)"),
    (r"\b(?:to the power of|raised to(?: the power of)?|to the power)\b", "**"),
    (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"),
    (r"\b(?:plus|add|and)\b", "+"), (r"\b(?:minus|less|subtract)\b", "-"),
    (r"\b(?:times|multiplied by|into|x)\b", "*"), (r"\b(?:divided by|over|by)\b", "/"),
    (r"\bmod(?:ulo)?\b", "%"),
]


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "sqrt" and len(node.args) == 1:
        return math.sqrt(_eval(node.args[0]))
    raise ValueError("unsupported")


def evaluate(text: str) -> float | None:
    """Returns the value, or None when the text isn't plain arithmetic."""
    t = text.lower().strip().rstrip("?.! ")
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    t = _PREFIX.sub("", t)
    for pat, rep in _WORDS:
        t = re.sub(pat, rep, t)
    t = t.replace("÷", "/").replace("×", "*").replace("^", "**")
    if not re.fullmatch(r"[\d.\s+\-*/%()sqrt]+", t) or not re.search(r"[+\-*/%]|sqrt", t):
        return None
    if not re.search(r"\d", t):
        return None
    try:
        value = _eval(ast.parse(t.strip(), mode="eval"))
    except Exception:
        return None
    return float(value)


def spoken(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.4f}".rstrip("0").rstrip(".")
