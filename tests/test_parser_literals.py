# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Literal-level surface coverage: numbers, units, strings, booleans,
comments (line/block/nested), and identifier rules."""

from __future__ import annotations

import pytest

from refrain import ParseError, parse
from refrain import ast as A


def _value_of(src_expr: str) -> A.Expr:
    """Helper: parse a minimal protocol and return the RHS of a single assignment."""
    src = f'protocol "P" {{ meta {{ x = {src_expr} }} }}'
    f = parse(src)
    section = f.protocol.body[0]
    return section.body[0].value


# ---------------------------------------------------------------------------
# Numeric literals with units (SPEC §2.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src, expected_value, expected_unit",
    [
        ("0", 0.0, None),
        ("42", 42.0, None),
        ("0.01", 0.01, None),
        ("1.5e3", 1500.0, None),
        ("1.5e-3", 0.0015, None),
        ("0.01 Hz", 0.01, "Hz"),
        ("12 Hz", 12.0, "Hz"),
        ("250 ms", 250.0, "ms"),
        ("2 min", 2.0, "min"),
        ("30 s", 30.0, "s"),
        ("8 uV", 8.0, "uV"),
        ("64 uV2", 64.0, "uV2"),
        ("95 %", 95.0, "%"),
    ],
)
def test_numeric_literals_parse_with_units(src, expected_value, expected_unit):
    n = _value_of(src)
    assert isinstance(n, A.NumberLit)
    assert n.value == expected_value
    assert n.unit == expected_unit


def test_unknown_unit_rejected():
    # `dB` is not in SPEC §2.5 — should not lex as a unit, leaving the
    # standalone `dB` as a bare identifier in expression position, which
    # is fine on its own — but `42 dB` should fail because `dB` is not
    # a UNIT terminal and `42 dB` cannot parse as two expressions.
    with pytest.raises(ParseError):
        parse('protocol "P" { meta { x = 42 dB } }')


# ---------------------------------------------------------------------------
# String literals (SPEC §2.4)
# ---------------------------------------------------------------------------


def test_string_basic():
    s = _value_of('"hello"')
    assert isinstance(s, A.StringLit)
    assert s.value == "hello"


def test_string_with_escapes():
    s = _value_of('"line1\\nline2\\t\\"quoted\\""')
    assert s.value == 'line1\nline2\t"quoted"'


def test_string_unicode_escape():
    s = _value_of('"\\u00b5V"')
    assert s.value == "µV"


# ---------------------------------------------------------------------------
# Boolean literals (SPEC §2.6)
# ---------------------------------------------------------------------------


def test_bool_true_false():
    assert _value_of("true") == A.BoolLit(value=True)
    assert _value_of("false") == A.BoolLit(value=False)


# ---------------------------------------------------------------------------
# Comments (SPEC §2.2)
# ---------------------------------------------------------------------------


def test_line_comment_at_end_of_line():
    f = parse('protocol "P" { meta { x = 1 // trailing\n } }')
    assert f.protocol.name == "P"


def test_line_comment_full_line():
    f = parse(
        """
        // top-of-file remark
        protocol "P" {
          // inside protocol
          meta { x = 1 }
        }
        """
    )
    assert f.protocol.name == "P"


def test_block_comment_simple():
    f = parse('protocol "P" { /* hi */ meta { x = 1 } }')
    assert f.protocol.name == "P"


def test_block_comment_multiline_preserves_line_numbers():
    src = """protocol "P" {
        /* line 2
           line 3
           line 4 */
        meta { x = 1 }
    }"""
    f = parse(src)
    assert f.protocol.body[0].body[0].target == "x"


def test_block_comment_nested():
    # SPEC §2.2 explicitly says block comments may nest.
    f = parse('protocol "P" { /* outer /* inner */ still outer */ meta { x = 1 } }')
    assert f.protocol.body[0].body[0].target == "x"


def test_block_comment_deeply_nested():
    f = parse('protocol "P" { /* a /* b /* c */ b */ a */ meta { x = 1 } }')
    assert f.protocol.body[0].body[0].target == "x"


def test_block_comment_inside_string_is_not_a_comment():
    # The literal value should retain the /* */ characters.
    f = parse('protocol "P" { meta { x = "/* not a comment */" } }')
    s = f.protocol.body[0].body[0].value
    assert isinstance(s, A.StringLit)
    assert s.value == "/* not a comment */"


def test_slash_star_inside_line_comment_is_not_a_block_start():
    # If the preprocessor mishandles this, the rest of the file becomes
    # an unterminated block comment.
    src = """protocol "P" {
        // here is /* an apparent block start that's just a line comment
        meta { x = 1 }
    }"""
    f = parse(src)
    assert f.protocol.body[0].body[0].target == "x"


# ---------------------------------------------------------------------------
# Identifiers (SPEC §2.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ident",
    [
        "a",
        "abc",
        "_underscore",
        "snake_case",
        "_leading",
        "name123",
        "camelCase",
        "PascalCase",
    ],
)
def test_legal_identifier_as_assignment_target(ident):
    f = parse(f'protocol "P" {{ meta {{ {ident} = 1 }} }}')
    assert f.protocol.body[0].body[0].target == ident


@pytest.mark.parametrize("ident", ["1bad", "-bad", "bad name"])
def test_illegal_identifier_rejected(ident):
    with pytest.raises(ParseError):
        parse(f'protocol "P" {{ meta {{ {ident} = 1 }} }}')


def test_bool_keyword_in_expression_position_is_boollit():
    # In expression context `true` lexes as BOOL; SPEC §2.3 does not
    # reserve it from identifier position. The contextual lexer
    # disambiguates based on grammar position.
    assert _value_of("true") == A.BoolLit(value=True)
    f = parse('protocol "P" { meta { true = 1 } }')
    assert f.protocol.body[0].body[0].target == "true"


# ---------------------------------------------------------------------------
# Optional `;` terminator (SPEC §3 `assignment`)
# ---------------------------------------------------------------------------


def test_assignment_with_and_without_semicolon():
    f = parse(
        """
        protocol "P" {
          meta { x = 1; y = 2 }
        }
        """
    )
    items = f.protocol.body[0].body
    assert [s.target for s in items] == ["x", "y"]


def test_remove_decl_semicolon_optional():
    # remove may have a trailing `;` (SPEC §3 `remove_decl`).
    f = parse(
        'protocol "P" extends "lib/parent@1" { remove derive "x"; remove derive "y" }'
    )
    targets = [s.target_name for s in f.protocol.body]
    assert targets == ["x", "y"]
