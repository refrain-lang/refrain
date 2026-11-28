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
        ("1.5 C", 1.5, "C"),
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


def test_fahrenheit_is_not_a_unit():
    """`C` is the one temperature spelling. Celsius and Fahrenheit differ by
    an offset as well as a scale, which Refrain's exponent vector cannot
    express, so `F` must not lex as a unit either."""
    with pytest.raises(ParseError):
        parse('protocol "P" { meta { x = 98.6 F } }')


def test_a_single_letter_unit_does_not_eat_identifiers():
    """`C` is a one-character UNIT terminal sitting next to the identifier
    pattern. A name that merely starts with it — a channel-ish `Cz`, a control
    named `Cz_gain` — must still lex as a name."""
    n = _value_of("Cz_gain")
    assert isinstance(n, A.NameRef) and n.name == "Cz_gain"


def _assignments(body: str) -> list[tuple[str, float, str | None]]:
    """Parse `body` as the inside of a section and return each assignment as
    `(target, value, unit)`."""
    f = parse(f'protocol "P" {{ meta {{ {body} }} }}')
    return [(s.target, s.value.value, s.value.unit) for s in f.protocol.body[0].body]


@pytest.mark.parametrize(
    "next_field",
    [
        # `s`
        "scale", "seed", "steepness", "step", "signal", "slope", "s2",
        # `C`
        "Cz_gain", "Cz", "C3",
        # the multi-character units
        "min_step", "minimum", "ms_window", "Hz_floor", "uV_limit", "uV2_limit", "uV2x",
    ],
)
@pytest.mark.parametrize("sep", ["\n  ", " ", "\n\n", " // why\n  "])
def test_a_unit_does_not_eat_the_start_of_the_next_field(next_field, sep):
    """A bare number followed by a field whose name merely *starts* with a
    unit spelling is a number and a field, not a number-with-unit and the
    field's tail. `;` between assignments is optional and newlines are
    insignificant (SPEC §2.7), so this is the ordinary one-field-per-line
    layout:

        target_pct = 50
        scale      = 3.0

    which read as `target_pct = 50 s` and `cale = 3.0`. The seed rule then
    ran with the default multiplier of 1.0 and nothing said so."""
    assert _assignments(f"a = 50{sep}{next_field} = 3.0") == [
        ("a", 50.0, None),
        (next_field, 3.0, None),
    ]


@pytest.mark.parametrize("unit", ["s", "ms", "min", "Hz", "uV", "uV2", "C"])
def test_a_field_named_exactly_like_a_unit_is_still_a_field(unit):
    assert _assignments(f"a = 50\n  {unit} = 3.0") == [
        ("a", 50.0, None),
        (unit, 3.0, None),
    ]


@pytest.mark.parametrize("unit", ["s", "ms", "min", "Hz", "uV", "uV2", "C", "%"])
@pytest.mark.parametrize("gap", [" ", ""])
@pytest.mark.parametrize("tail", ["\n  scale = 3.0", "; scale = 3.0", " scale = 3.0"])
def test_a_real_unit_still_binds_before_the_next_field(unit, gap, tail):
    """The other direction: the fix must not cost a literal its unit."""
    assert _assignments(f"a = 50{gap}{unit}{tail}") == [
        ("a", 50.0, unit),
        ("scale", 3.0, None),
    ]


@pytest.mark.parametrize(
    "src, unit",
    [
        ("(8 uV, 30 uV)", "uV"),
        ("(4 Hz,8 Hz)", "Hz"),
        ("[250 ms]", "ms"),
        ("f(2 s)", "s"),
        ("5 s+1 s", "s"),
        ("5 s-1 s", "s"),
        ("5 s*2", "s"),
        ("5 s/2", "s"),
        ("5 uV>1 uV", "uV"),
        ("5 s==5 s", "s"),
    ],
)
def test_a_unit_is_delimited_by_punctuation(src, unit):
    """A unit ends at the first character that cannot continue an identifier,
    so closers, commas and operators hard against it are fine."""
    e = _value_of(src)
    while not isinstance(e, A.NumberLit):
        if isinstance(e, A.BinaryOp):
            e = e.left
        elif isinstance(e, A.Call):
            e = e.args[0].value
        else:
            e = e.elements[0]
    assert e.unit == unit


@pytest.mark.parametrize(
    "src",
    ["5 seconds", "5 sec", "5 mins", "5 msec", "5 Hzz", "5 uVolts", "5 Celsius", "5 s2"],
)
def test_a_misspelled_unit_is_a_parse_error(src):
    """`5 seconds` used to be `5 s` followed by a stray `econds`, which could
    only fail later and somewhere else. It now fails here."""
    with pytest.raises(ParseError):
        parse(f'protocol "P" {{ meta {{ x = {src} }} }}')


# ---------------------------------------------------------------------------
# Unary minus (DESIGN-NOTES §1.3; absent from SPEC §3's EBNF)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src, expected_value, expected_unit",
    [
        ("-3", -3.0, None),
        ("- 3", -3.0, None),
        ("-0.5", -0.5, None),
        ("-1.5e3", -1500.0, None),
        ("-3 uV", -3.0, "uV"),
        ("-200 ms", -200.0, "ms"),
        ("-(-3)", 3.0, None),
    ],
)
def test_negated_literal_is_one_literal(src, expected_value, expected_unit):
    """A negated numeric literal folds in the parser, so every slot that reads
    a constant sees a `NumberLit` and nothing has to learn a new node."""
    n = _value_of(src)
    assert isinstance(n, A.NumberLit)
    assert n.value == expected_value
    assert n.unit == expected_unit


def test_binary_minus_still_binds_as_binary():
    """The reason SPEC §3 left unary minus out was the ambiguity. `a - b` must
    stay a subtraction, and `a - -b` must be a subtraction of a negative."""
    b = _value_of("1 - 2")
    assert isinstance(b, A.BinaryOp) and b.op == "-"
    assert b.left == A.NumberLit(value=1.0, unit=None, loc=b.left.loc)
    assert b.right.value == 2.0

    c = _value_of("1 - -2")
    assert isinstance(c, A.BinaryOp) and c.op == "-"
    assert c.right.value == -2.0


def test_negated_tuple_elements():
    """`range: (-1, 1)` — the TOUR §7 spelling DESIGN-NOTES §1.3 called out as
    unparseable."""
    t = _value_of("(-1, 1)")
    assert isinstance(t, A.Tuple)
    assert [e.value for e in t.elements] == [-1.0, 1.0]


def test_negating_a_non_literal_preserves_its_dimensions():
    """A stream cannot fold, so it desugars. `0 - x` would be dimension-
    checked against a bare zero and refused; `-1 * x` is not."""
    e = _value_of('-"env"')
    assert isinstance(e, A.BinaryOp) and e.op == "*"
    assert e.left.value == -1.0 and e.left.unit is None
    assert isinstance(e.right, A.StringLit) and e.right.value == "env"


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
