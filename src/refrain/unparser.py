# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""AST -> source emitter for Refrain v0.0r1.

Goal: round-trip identity at the AST level. `parse(unparse(parse(src)))`
yields an AST equal to `parse(src)`. Original whitespace, comments, and
trailing-comma style are NOT preserved — only the structural content.

The emitter follows a minimal canonical format:
- 2-space indents inside blocks
- one statement per line
- a single space around binary operators
- parenthesise binary subexpressions inside other binary expressions so
  precedence is unambiguous after re-parsing
"""

from __future__ import annotations

from . import ast as A

_INDENT = "  "

_OP_PRECEDENCE = {
    "*": 5,
    "/": 5,
    "+": 4,
    "-": 4,
    "<": 3,
    ">": 3,
    "<=": 3,
    ">=": 3,
    "==": 3,
    "!=": 3,
}


def unparse(node: A.Node) -> str:
    """Render an AST back to Refrain source text."""
    if isinstance(node, A.File):
        return _emit_file(node)
    if isinstance(node, A.Protocol):
        return _emit_protocol(node, 0)
    if isinstance(node, A.Statement):
        return _emit_stmt(node, 0)
    if isinstance(node, A.Expr):
        return _emit_expr(node)
    raise TypeError(f"cannot unparse {type(node).__name__}")


# ---------------------------------------------------------------------------
# File / protocol
# ---------------------------------------------------------------------------


def _emit_file(f: A.File) -> str:
    parts: list[str] = []
    for imp in f.imports:
        parts.append(_emit_import(imp))
    parts.append(_emit_protocol(f.protocol, 0))
    return "\n".join(parts) + "\n"


def _emit_import(imp: A.Import) -> str:
    if imp.alias is not None:
        return f'import "{_escape(imp.path)}" as {imp.alias};'
    return f'import "{_escape(imp.path)}";'


def _emit_protocol(p: A.Protocol, depth: int) -> str:
    head = f'protocol "{_escape(p.name)}"'
    if p.extends is not None:
        head += f' extends "{_escape(p.extends)}"'
    return head + " " + _emit_block(p.body, depth)


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


def _emit_stmt(stmt: A.Statement, depth: int) -> str:
    if isinstance(stmt, A.SectionBlock):
        return f"{stmt.keyword} " + _emit_block(stmt.body, depth)
    if isinstance(stmt, A.NamedDecl):
        return f'{stmt.keyword} "{_escape(stmt.name)}" ' + _emit_block(stmt.body, depth)
    if isinstance(stmt, A.AmendDecl):
        if stmt.target_name is None:
            return f"amend {stmt.target_kw} " + _emit_block(stmt.body, depth)
        return (
            f'amend {stmt.target_kw} "{_escape(stmt.target_name)}" '
            + _emit_block(stmt.body, depth)
        )
    if isinstance(stmt, A.RemoveDecl):
        return f'remove {stmt.target_kw} "{_escape(stmt.target_name)}"'
    if isinstance(stmt, A.Assignment):
        return f"{stmt.target} = {_emit_expr(stmt.value)}"
    raise TypeError(f"unknown statement type: {type(stmt).__name__}")


def _emit_block(body: tuple[A.Statement, ...], depth: int) -> str:
    if not body:
        return "{}"
    inner_indent = _INDENT * (depth + 1)
    close_indent = _INDENT * depth
    lines = [inner_indent + _emit_stmt(s, depth + 1) for s in body]
    return "{\n" + "\n".join(lines) + "\n" + close_indent + "}"


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


def _emit_expr(expr: A.Expr) -> str:
    if isinstance(expr, A.NumberLit):
        return _emit_number(expr)
    if isinstance(expr, A.StringLit):
        return f'"{_escape(expr.value)}"'
    if isinstance(expr, A.BoolLit):
        return "true" if expr.value else "false"
    if isinstance(expr, A.NameRef):
        return expr.name
    if isinstance(expr, A.Call):
        args = ", ".join(_emit_arg(a) for a in expr.args)
        return f"{expr.callee}({args})"
    if isinstance(expr, A.Array):
        elts = ", ".join(_emit_expr(e) for e in expr.elements)
        return f"[{elts}]"
    if isinstance(expr, A.Tuple):
        elts = ", ".join(_emit_expr(e) for e in expr.elements)
        return f"({elts})"
    if isinstance(expr, A.BlockExpr):
        head = expr.name if expr.name is not None else ""
        return (head + (" " if head else "") + _emit_inline_block(expr.body)).strip() or "{}"
    if isinstance(expr, A.BinaryOp):
        return _emit_binary(expr)
    if isinstance(expr, A.Conditional):
        return (
            _emit_expr(expr.cond)
            + " ? "
            + _emit_expr(expr.then_branch)
            + " : "
            + _emit_expr(expr.else_branch)
        )
    if isinstance(expr, A.MemberAccess):
        return _emit_expr(expr.target) + "." + expr.member
    raise TypeError(f"unknown expression type: {type(expr).__name__}")


def _emit_number(n: A.NumberLit) -> str:
    # Render integers without trailing .0 so `8 uV` round-trips cleanly.
    if n.value == int(n.value) and abs(n.value) < 1e16:
        body = str(int(n.value))
    else:
        body = repr(n.value)
    return body + (" " + n.unit if n.unit else "")


def _emit_arg(arg: A.Arg) -> str:
    if arg.name is None:
        return _emit_expr(arg.value)
    return f"{arg.name}: {_emit_expr(arg.value)}"


def _emit_binary(b: A.BinaryOp) -> str:
    """Emit `a op b` with parentheses where precedence would otherwise change."""
    left = _emit_binary_operand(b.left, b.op, side="left")
    right = _emit_binary_operand(b.right, b.op, side="right")
    return f"{left} {b.op} {right}"


def _emit_binary_operand(operand: A.Expr, parent_op: str, side: str) -> str:
    text = _emit_expr(operand)
    if isinstance(operand, A.BinaryOp):
        parent_prec = _OP_PRECEDENCE[parent_op]
        child_prec = _OP_PRECEDENCE[operand.op]
        if child_prec < parent_prec:
            return f"({text})"
        # Same precedence on right side of a left-assoc op also needs parens.
        if child_prec == parent_prec and side == "right" and parent_op in {"-", "/"}:
            return f"({text})"
    if isinstance(operand, A.Conditional):
        return f"({text})"
    return text


def _emit_inline_block(body: tuple[A.Statement, ...]) -> str:
    """Block-expr body emitted on a single line, semicolons between assignments."""
    if not body:
        return "{}"
    inner = "; ".join(_emit_stmt(s, 0) for s in body)
    return "{ " + inner + " }"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


__all__ = ["unparse"]
