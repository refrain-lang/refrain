# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain v0.0r1 parser.

Source bytes -> AST (`refrain.ast`). Two layers:

1. `_strip_block_comments`: a preprocessor that removes `/* ... */` block
   comments. Handled outside Lark because SPEC §2.2 promises nesting,
   which Lark's regex-based `%ignore` cannot match. Newlines inside
   stripped blocks are preserved so Lark's line numbers still mean
   something for diagnostics. String literals and `//` line comments are
   recognised so that `/*` inside them is not mistaken for a block start.

2. `_AstBuilder` (a `lark.Transformer`): walks the Lark parse tree and
   emits AST nodes. The grammar uses `?`-prefixed rules so simple
   expressions pass through without intermediate wrapping; the transformer
   only sees rule-name tokens for the cases that actually need conversion.

Public surface:
    parse(source)        -> File
    parse_file(path)     -> File
    ParseError            class
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import lark
from lark import Token, Transformer, v_args

from . import ast as A

GRAMMAR_PATH = Path(__file__).resolve().parent / "grammar.lark"


def _loc(meta) -> A.Loc | None:
    """Build a `Loc` from Lark's tree meta. Returns None for empty spans
    (which Lark produces for rules whose children were all hoisted away)."""
    if meta is None or getattr(meta, "empty", False):
        return None
    return A.Loc(
        line=meta.line,
        col=meta.column,
        end_line=meta.end_line,
        end_col=meta.end_column,
    )


class ParseError(Exception):
    """Raised when source fails to parse. Wraps the underlying Lark error."""


def _strip_block_comments(src: str) -> str:
    """Strip `/* ... */` block comments, with SPEC §2.2 nesting.

    Preserves newlines inside removed blocks (so error locations remain
    accurate), skips over string literals and `//` line comments so that
    `/*` inside them is not interpreted as a comment opener.
    """
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        # Double-quoted string: copy verbatim, honouring backslash escapes.
        if ch == '"':
            out.append(ch)
            i += 1
            while i < n and src[i] != '"':
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i])
                    out.append(src[i + 1])
                    i += 2
                else:
                    out.append(src[i])
                    i += 1
            if i < n:
                out.append(src[i])  # closing quote
                i += 1
            continue
        # Line comment: copy verbatim (Lark `%ignore`s it).
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                out.append(src[i])
                i += 1
            continue
        # Block comment: strip, preserving newlines, tracking nesting.
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth > 0:
                if src[i] == "/" and i + 1 < n and src[i + 1] == "*":
                    depth += 1
                    i += 2
                elif src[i] == "*" and i + 1 < n and src[i + 1] == "/":
                    depth -= 1
                    i += 2
                else:
                    if src[i] == "\n":
                        out.append("\n")
                    i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_STRING_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
}


def _decode_string_lit(raw: str) -> str:
    """Strip enclosing quotes and decode SPEC §2.4 escape sequences."""
    if len(raw) < 2 or raw[0] != '"' or raw[-1] != '"':
        # Should not happen given the grammar, but be defensive.
        raise ParseError(f"malformed string literal: {raw!r}")
    body = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in _STRING_ESCAPES:
                out.append(_STRING_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "u" and i + 5 < len(body) + 1:
                hex_part = body[i + 2 : i + 6]
                try:
                    out.append(chr(int(hex_part, 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            # Unknown escape: pass through verbatim per minimum-surprise.
            out.append(ch)
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


@v_args(meta=True)
class _AstBuilder(Transformer):
    """Lark Tree -> Refrain AST node mapping. One method per non-inlined rule.

    `@v_args(meta=True)` (combined with `propagate_positions=True` on the
    Lark parser) provides each method with a `Meta` object carrying line
    / column spans, which are converted to `A.Loc` and attached to the
    emitted AST node.
    """

    # -- Top level ----------------------------------------------------------

    def start(self, meta, items):
        return items[0]

    def file(self, meta, items):
        imports = tuple(x for x in items if isinstance(x, A.Import))
        protocol = next(x for x in items if isinstance(x, A.Protocol))
        return A.File(imports=imports, protocol=protocol, loc=_loc(meta))

    def import_decl(self, meta, items):
        path = items[0].value  # StringLit
        alias = items[1].value if len(items) > 1 and isinstance(items[1], Token) else None
        return A.Import(path=path, alias=alias, loc=_loc(meta))

    def protocol_decl(self, meta, items):
        # items: [string_lit, extends_clause?, block]
        name = items[0].value  # StringLit
        extends = None
        body = ()
        for it in items[1:]:
            if isinstance(it, _ExtendsClause):
                extends = it.target
            elif isinstance(it, tuple):
                body = it  # block returned a tuple of statements
        return A.Protocol(name=name, extends=extends, body=body, loc=_loc(meta))

    def extends_clause(self, meta, items):
        return _ExtendsClause(target=items[0].value)

    def block(self, meta, items):
        return tuple(items)

    # -- Statement nodes ----------------------------------------------------

    def section_block(self, meta, items):
        kw = items[0].value
        body = items[1] if isinstance(items[1], tuple) else tuple(items[1])
        return A.SectionBlock(keyword=kw, body=body, loc=_loc(meta))

    def named_decl(self, meta, items):
        kw = items[0].value
        name = items[1].value  # StringLit
        body = items[2]
        return A.NamedDecl(keyword=kw, name=name, body=body, loc=_loc(meta))

    def amend_decl(self, meta, items):
        # amend_section / amend_named built the inner AmendDecl over the
        # inner span (e.g. `inhibit "emg" { ... }`). Widen to include the
        # leading `amend` keyword by replacing the loc with this rule's
        # span. dataclasses.replace works on frozen+slotted dataclasses.
        inner: A.AmendDecl = items[0]
        return dataclasses.replace(inner, loc=_loc(meta))

    def amend_section(self, meta, items):
        kw = items[0].value
        body = items[1]
        return A.AmendDecl(target_kw=kw, target_name=None, body=body, loc=_loc(meta))

    def amend_named(self, meta, items):
        kw = items[0].value
        name = items[1].value
        body = items[2]
        return A.AmendDecl(target_kw=kw, target_name=name, body=body, loc=_loc(meta))

    def remove_decl(self, meta, items):
        kw = items[0].value
        name = items[1].value
        return A.RemoveDecl(target_kw=kw, target_name=name, loc=_loc(meta))

    def assignment(self, meta, items):
        target = items[0].value
        value = items[1]
        return A.Assignment(target=target, value=value, loc=_loc(meta))

    # -- Expressions --------------------------------------------------------

    def ternary(self, meta, items):
        # Only invoked when the optional "? : " branch was taken; otherwise
        # the `?ternary` prefix inlines the single child.
        return A.Conditional(
            cond=items[0],
            then_branch=items[1],
            else_branch=items[2],
            loc=_loc(meta),
        )

    def comparison(self, meta, items):
        return A.BinaryOp(
            op=items[1].value, left=items[0], right=items[2], loc=_loc(meta)
        )

    def arith(self, meta, items):
        return _left_fold_binop(items, _loc(meta))

    def term(self, meta, items):
        return _left_fold_binop(items, _loc(meta))

    def factor(self, meta, items):
        # items: [atom, member_chain]; member_chain is a list of NAME tokens.
        # Each inner MemberAccess gets the same `factor`-level span; this
        # is coarse but adequate for diagnostics until we need otherwise.
        target = items[0]
        chain = items[1]
        outer_loc = _loc(meta)
        for name in chain:
            target = A.MemberAccess(target=target, member=name.value, loc=outer_loc)
        return target

    def member_chain(self, meta, items):
        return items  # list of NAME tokens; consumed by factor()

    def call(self, meta, items):
        callee = items[0].value
        args = items[1] if len(items) > 1 else ()
        return A.Call(callee=callee, args=tuple(args), loc=_loc(meta))

    def arg_list(self, meta, items):
        # `?arg: named_arg | expression` means positional args bubble up as
        # raw Expr while named args bubble up as Arg. Normalise both to Arg.
        # Args carry the loc of the value they wrap; we don't have per-arg
        # meta here (this method's meta spans the whole arg list).
        out: list[A.Arg] = []
        for it in items:
            if isinstance(it, A.Arg):
                out.append(it)
            else:
                out.append(A.Arg(name=None, value=it, loc=getattr(it, "loc", None)))
        return out

    def named_arg(self, meta, items):
        return A.Arg(name=items[0].value, value=items[1], loc=_loc(meta))

    def array(self, meta, items):
        return A.Array(elements=tuple(items), loc=_loc(meta))

    def tuple(self, meta, items):
        return A.Tuple(elements=tuple(items), loc=_loc(meta))

    def paren_expr(self, meta, items):
        # Pass-through: inner expression keeps its own (tighter) loc.
        return items[0]

    def block_expr(self, meta, items):
        if len(items) == 2:
            return A.BlockExpr(name=items[0].value, body=items[1], loc=_loc(meta))
        return A.BlockExpr(name=None, body=items[0], loc=_loc(meta))

    def name_ref(self, meta, items):
        return A.NameRef(name=items[0].value, loc=_loc(meta))

    # -- Literals -----------------------------------------------------------

    def literal(self, meta, items):
        # Pass-through: literal nodes built by number_with_unit /
        # string_lit / bool_lit already have their own (tighter) locs.
        return items[0]

    def number_with_unit(self, meta, items):
        value = float(items[0].value)
        unit = items[1].value if len(items) > 1 else None
        return A.NumberLit(value=value, unit=unit, loc=_loc(meta))

    def string_lit(self, meta, items):
        return A.StringLit(value=_decode_string_lit(items[0].value), loc=_loc(meta))

    def bool_lit(self, meta, items):
        return A.BoolLit(value=(items[0].value == "true"), loc=_loc(meta))


class _ExtendsClause:
    """Internal sentinel for the optional `extends "ref"` clause."""

    __slots__ = ("target",)

    def __init__(self, target: str) -> None:
        self.target = target


def _left_fold_binop(items, loc):
    """Fold `[expr, op_token, expr, op_token, ...]` into nested BinaryOp.

    All inner BinaryOp nodes inherit the same outer `loc` (the span of
    the whole expression). Refining inner spans to their actual sub-
    expression bounds is a follow-up if diagnostics need it.
    """
    result = items[0]
    for i in range(1, len(items), 2):
        op = items[i].value
        right = items[i + 1]
        result = A.BinaryOp(op=op, left=result, right=right, loc=loc)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_lark_parser() -> lark.Lark:
    return lark.Lark.open(
        str(GRAMMAR_PATH),
        start="start",
        parser="earley",
        maybe_placeholders=False,
        propagate_positions=True,
    )


_PARSER: lark.Lark | None = None


def _parser() -> lark.Lark:
    global _PARSER
    if _PARSER is None:
        _PARSER = _build_lark_parser()
    return _PARSER


def parse(source: str) -> A.File:
    """Parse Refrain source text and return an AST `File` node.

    Raises `ParseError` on any syntax error.
    """
    try:
        stripped = _strip_block_comments(source)
        tree = _parser().parse(stripped)
    except lark.exceptions.LarkError as exc:
        raise ParseError(str(exc)) from exc
    return _AstBuilder().transform(tree)


def parse_file(path: str | Path) -> A.File:
    """Parse a `.refrain` file at `path` and return an AST `File` node."""
    return parse(Path(path).read_text(encoding="utf-8"))


__all__ = ["parse", "parse_file", "ParseError"]
