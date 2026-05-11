# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Source-location attribution on AST nodes.

The resolver and type checker in Session 2 will use `node.loc` to produce
positioned diagnostics. These tests pin down the contract:

  - Every node returned by the parser has a non-None `loc`.
  - Spans are 1-based (matching Lark's convention).
  - `loc` is excluded from equality so AST-level round-tripping still
    works regardless of how source whitespace shifts line numbers.
  - `loc` does not appear in `__repr__` so test failure output stays
    readable.
"""

from __future__ import annotations

from refrain import ast as A
from refrain import parse


# ---------------------------------------------------------------------------
# Loc is excluded from equality / hash / repr.
# ---------------------------------------------------------------------------


def test_loc_does_not_affect_equality():
    loc_a = A.Loc(line=1, col=0, end_line=1, end_col=5)
    loc_b = A.Loc(line=99, col=12, end_line=99, end_col=17)
    n1 = A.NumberLit(value=42.0, unit="Hz", loc=loc_a)
    n2 = A.NumberLit(value=42.0, unit="Hz", loc=loc_b)
    n3 = A.NumberLit(value=42.0, unit="Hz")
    assert n1 == n2 == n3


def test_loc_does_not_affect_hash():
    loc_a = A.Loc(line=1, col=0, end_line=1, end_col=5)
    n1 = A.NumberLit(value=42.0, unit="Hz", loc=loc_a)
    n2 = A.NumberLit(value=42.0, unit="Hz")
    assert hash(n1) == hash(n2)


def test_loc_does_not_appear_in_repr():
    loc_a = A.Loc(line=99, col=12, end_line=99, end_col=17)
    n = A.NumberLit(value=42.0, unit="Hz", loc=loc_a)
    assert "99" not in repr(n)
    assert "loc" not in repr(n)


# ---------------------------------------------------------------------------
# Every parser-produced node has a populated loc.
# ---------------------------------------------------------------------------


def _walk(node):
    yield node
    if isinstance(node, A.File):
        for imp in node.imports:
            yield from _walk(imp)
        yield from _walk(node.protocol)
    elif isinstance(node, A.Protocol):
        for s in node.body:
            yield from _walk(s)
    elif isinstance(node, (A.SectionBlock, A.NamedDecl, A.AmendDecl)):
        for s in node.body:
            yield from _walk(s)
    elif isinstance(node, A.Assignment):
        yield from _walk(node.value)
    elif isinstance(node, A.Call):
        for a in node.args:
            yield from _walk(a.value)
    elif isinstance(node, A.Array):
        for e in node.elements:
            yield from _walk(e)
    elif isinstance(node, A.Tuple):
        for e in node.elements:
            yield from _walk(e)
    elif isinstance(node, A.BlockExpr):
        for s in node.body:
            yield from _walk(s)
    elif isinstance(node, A.BinaryOp):
        yield from _walk(node.left)
        yield from _walk(node.right)
    elif isinstance(node, A.Conditional):
        yield from _walk(node.cond)
        yield from _walk(node.then_branch)
        yield from _walk(node.else_branch)
    elif isinstance(node, A.MemberAccess):
        yield from _walk(node.target)


def test_every_node_in_smr_example_has_a_loc():
    f = parse(open("examples/smr_cz.refrain").read())
    nodes_without_loc = [n for n in _walk(f) if n.loc is None]
    assert nodes_without_loc == [], (
        f"{len(nodes_without_loc)} nodes missing loc: "
        f"{[type(n).__name__ for n in nodes_without_loc[:5]]}"
    )


# ---------------------------------------------------------------------------
# Locations are accurate to the source position.
# ---------------------------------------------------------------------------


def test_loc_line_numbers_track_source_lines():
    src = """\
protocol "P" {
  meta {
    version = "1.0"
  }
  requires {
    sample_rate = ">= 256 Hz"
  }
}"""
    f = parse(src)
    # protocol_decl starts on line 1
    assert f.protocol.loc.line == 1
    meta = f.protocol.body[0]
    requires = f.protocol.body[1]
    assert meta.loc.line == 2
    assert requires.loc.line == 5
    version_assign = meta.body[0]
    assert version_assign.loc.line == 3
    sample_rate_assign = requires.body[0]
    assert sample_rate_assign.loc.line == 6


def test_loc_columns_track_source_columns():
    # Single-line protocol — every assignment's column matches its
    # source position. Lark uses 1-based columns.
    src = 'protocol "P" { meta { x = 1; y = 2 } }'
    f = parse(src)
    meta = f.protocol.body[0]
    x_assign, y_assign = meta.body
    # `x = 1` starts at column 23 (1-indexed: 'p' at col 1; 'x' at col 23).
    assert x_assign.loc.line == 1
    assert x_assign.loc.col == src.index("x = 1") + 1
    assert y_assign.loc.col == src.index("y = 2") + 1


def test_call_loc_spans_callee_and_args():
    f = parse('protocol "P" { meta { x = bandpass(band: (12 Hz, 15 Hz)) } }')
    call = f.protocol.body[0].body[0].value
    assert isinstance(call, A.Call)
    src = 'protocol "P" { meta { x = bandpass(band: (12 Hz, 15 Hz)) } }'
    # `bandpass(...)` starts where 'b' is and ends after the closing `)`.
    assert call.loc.line == 1
    assert call.loc.col == src.index("bandpass") + 1
    assert call.loc.end_col == src.index("))") + 2 + 1  # past the closing paren


def test_member_access_loc_covers_full_chain():
    f = parse('protocol "P" { meta { x = reward.event.holds } }')
    ma = f.protocol.body[0].body[0].value
    assert isinstance(ma, A.MemberAccess)
    src = 'protocol "P" { meta { x = reward.event.holds } }'
    assert ma.loc.col == src.index("reward.event.holds") + 1
    # End column is past 'holds'.
    assert ma.loc.end_col == src.index("reward.event.holds") + len("reward.event.holds") + 1


def test_amend_decl_loc_includes_amend_keyword():
    # The inner amend_section / amend_named span starts at the section
    # keyword (e.g. `inhibit`); amend_decl widens it to include `amend`.
    src = '''protocol "C" extends "p@1" {
  amend inhibit "emg" {
    threshold = absolute(15 uV2)
  }
}'''
    f = parse(src)
    amend = f.protocol.body[0]
    assert isinstance(amend, A.AmendDecl)
    # The `amend` keyword is at line 2, col 3. The loc must start there.
    assert amend.loc.line == 2
    assert amend.loc.col == 3


def test_round_trip_still_works_with_locs():
    """Round-trip equality is the invariant that lets the resolver lean
    on `==`; if loc affected equality this test would fail."""
    src = open("examples/smr_cz.refrain").read()
    from refrain import unparse
    ast_a = parse(src)
    ast_b = parse(unparse(ast_a))
    assert ast_a == ast_b
    # Sanity: the two ASTs do have different locs (different whitespace).
    assert ast_a.loc != ast_b.loc or ast_a.protocol.body[0].loc != ast_b.protocol.body[0].loc
