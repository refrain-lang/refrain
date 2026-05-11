# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain abstract syntax tree.

One frozen dataclass per node type defined in SPEC.md v0.0r1 §3. Frozen
gives structural equality and hashability for free; slots keeps the per-
instance footprint small for protocols that compile to thousands of nodes.

Statement and Expr are marker bases — a parser consumer can pattern-match
on the concrete subclasses. Compound containers use tuples rather than
lists so that nested structures remain immutable (and thus hashable along
with their parents).

Source-location tracking is intentionally deferred. The grammar is
position-preserving in Lark; attaching `(line, col)` to every node is a
follow-up once the resolver/diagnostics layer needs it.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Marker bases
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Node:
    """Base for every AST node."""


@dataclass(frozen=True, slots=True)
class Statement(Node):
    """Marker for statement-position nodes (SPEC §3 `statement`)."""


@dataclass(frozen=True, slots=True)
class Expr(Node):
    """Marker for expression-position nodes (SPEC §3 `expression`)."""


# ---------------------------------------------------------------------------
# File and protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class File(Node):
    """A `.refrain` source unit (SPEC §3 `file`)."""

    imports: tuple[Import, ...]
    protocol: Protocol


@dataclass(frozen=True, slots=True)
class Import(Node):
    """`import "path" [as alias];` (SPEC §3 `import_decl`)."""

    path: str
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class Protocol(Node):
    """Top-level `protocol "name" [extends "ref"] { ... }` (SPEC §3, §4, §11)."""

    name: str
    extends: str | None
    body: tuple[Statement, ...]


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionBlock(Statement):
    """`meta { ... }`, `requires { ... }`, etc. (SPEC §3 `section_block`).

    `keyword` is one of: meta, requires, reward, output, controls, session.
    """

    keyword: str
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class NamedDecl(Statement):
    """`input "X" { ... }`, `derive "Y" { ... }`, etc. (SPEC §3 `named_decl`).

    `keyword` is one of: input, derive, threshold, inhibit, custom.
    """

    keyword: str
    name: str
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class AmendDecl(Statement):
    """`amend reward { ... }` or `amend inhibit "emg" { ... }` (SPEC §11.2).

    `target_kw` is the section or decl keyword being amended. `target_name`
    is the string-lit name for named decls; `None` for section-block amends.
    """

    target_kw: str
    target_name: str | None
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class RemoveDecl(Statement):
    """`remove inhibit "emg"` (SPEC §11.3)."""

    target_kw: str
    target_name: str


@dataclass(frozen=True, slots=True)
class Assignment(Statement):
    """`name = expression` (SPEC §3 `assignment`)."""

    target: str
    value: Expr


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NumberLit(Expr):
    """`12`, `0.5`, `12 Hz`, `250 ms`, `8 uV`, `95 %`, `42 uV2`."""

    value: float
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class StringLit(Expr):
    """A double-quoted string literal (escapes already decoded).

    Per SPEC §5.4 a string literal in expression position that names a
    previously declared block (input/derive/threshold/controls entry) is
    semantically a *reference*. The grammar cannot distinguish a literal
    from a reference; the resolver classifies later.
    """

    value: str


@dataclass(frozen=True, slots=True)
class BoolLit(Expr):
    """`true` / `false`."""

    value: bool


@dataclass(frozen=True, slots=True)
class NameRef(Expr):
    """A bare identifier in expression position (e.g. `orf` in a control body,
    or `reward` before a member-access chain)."""

    name: str


@dataclass(frozen=True, slots=True)
class Call(Expr):
    """`bandpass(band: (12 Hz, 15 Hz), order: 4)` (SPEC §3 `call`)."""

    callee: str
    args: tuple[Arg, ...]


@dataclass(frozen=True, slots=True)
class Arg(Node):
    """A single call argument; `name` is `None` for positional args."""

    name: str | None
    value: Expr


@dataclass(frozen=True, slots=True)
class Array(Expr):
    """`[a, b, c]` (SPEC §3 `array`). Elements may be heterogeneous."""

    elements: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class Tuple(Expr):
    """`(a, b)` — fixed-shape positional grouping. Always >= 2 elements;
    `(a)` is a parenthesised expression and parses to whatever `a` parses to."""

    elements: tuple[Expr, ...]


@dataclass(frozen=True, slots=True)
class BlockExpr(Expr):
    """`phase { name = "warmup"; duration = 60 s }` or `frequency { ... }`.

    SPEC §3 defines `block_expr = block` (anonymous record). Examples use
    the named form `NAME block`; this AST node supports both, with `name`
    being `None` for the anonymous form.
    """

    name: str | None
    body: tuple[Statement, ...]


@dataclass(frozen=True, slots=True)
class BinaryOp(Expr):
    """`a + b`, `a > b`, etc. (SPEC §3 `binary_op`)."""

    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Conditional(Expr):
    """`cond ? a : b` (SPEC §3 `conditional`)."""

    cond: Expr
    then_branch: Expr
    else_branch: Expr


@dataclass(frozen=True, slots=True)
class MemberAccess(Expr):
    """`reward.continuous`, `reward.event.holds` (SPEC §3 `member_access`).

    Chained access is represented as nested MemberAccess (left-associative):
    `reward.event.holds` -> MemberAccess(MemberAccess(reward, "event"), "holds").
    """

    target: Expr
    member: str


__all__ = [
    "Node",
    "Statement",
    "Expr",
    "File",
    "Import",
    "Protocol",
    "SectionBlock",
    "NamedDecl",
    "AmendDecl",
    "RemoveDecl",
    "Assignment",
    "NumberLit",
    "StringLit",
    "BoolLit",
    "NameRef",
    "Call",
    "Arg",
    "Array",
    "Tuple",
    "BlockExpr",
    "BinaryOp",
    "Conditional",
    "MemberAccess",
]
