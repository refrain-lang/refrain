# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Every primitive call shape from PRIMITIVES.md must round-trip the parser.

This is a syntax-level test only. Argument types, unit consistency, and
primitive existence are the resolver's problem — here we just verify the
shapes parse into the expected AST."""

from __future__ import annotations

import pytest

from refrain import ast as A
from refrain import parse


def _parse_expr(src_expr: str) -> A.Expr:
    """Parse a snippet and return the RHS of the first assignment in `meta`."""
    f = parse(f'protocol "P" {{ meta {{ x = {src_expr} }} }}')
    return f.protocol.body[0].body[0].value


def _call(expr: A.Expr) -> A.Call:
    assert isinstance(expr, A.Call), f"expected Call, got {type(expr).__name__}"
    return expr


def _arg(call: A.Call, name: str | None, idx: int | None = None) -> A.Arg:
    if name is not None:
        for a in call.args:
            if a.name == name:
                return a
        raise AssertionError(f"named arg {name!r} not found in {[a.name for a in call.args]}")
    return call.args[idx]


# ---------------------------------------------------------------------------
# Acquisition (PRIMITIVES.md "Acquisition")
# ---------------------------------------------------------------------------


def test_bipolar_named_args():
    c = _call(_parse_expr('bipolar(plus: "T3", minus: "T4")'))
    assert c.callee == "bipolar"
    assert _arg(c, "plus").value == A.StringLit("T3")
    assert _arg(c, "minus").value == A.StringLit("T4")


def test_referential_scalar_form():
    c = _call(_parse_expr('referential(active: "Cz", reference: "linked_ears")'))
    assert c.callee == "referential"
    assert _arg(c, "active").value == A.StringLit("Cz")


def test_referential_vector_form():
    c = _call(_parse_expr('referential(channels: ["Fp1", "Fp2", "F3"], reference: "linked_ears")'))
    chans = _arg(c, "channels").value
    assert isinstance(chans, A.Array)
    assert len(chans.elements) == 3
    assert all(isinstance(e, A.StringLit) for e in chans.elements)


def test_select_channel_positional():
    c = _call(_parse_expr('select_channel("F3")'))
    assert c.callee == "select_channel"
    assert c.args[0].name is None
    assert c.args[0].value == A.StringLit("F3")


def test_source_project_stub_form():
    # PRIMITIVES.md flags this as a stub but the shape should still parse.
    c = _call(_parse_expr('source_project(operator: norms.inverse_operator, roi: "all")'))
    op_arg = _arg(c, "operator").value
    assert isinstance(op_arg, A.MemberAccess)
    assert op_arg.member == "inverse_operator"
    assert isinstance(op_arg.target, A.NameRef)
    assert op_arg.target.name == "norms"


# ---------------------------------------------------------------------------
# Spectral (PRIMITIVES.md "Spectral operators")
# ---------------------------------------------------------------------------


def test_bandpass_edge_frequency_form():
    c = _call(_parse_expr("bandpass(band: (12 Hz, 15 Hz), order: 4)"))
    band = _arg(c, "band").value
    assert isinstance(band, A.Tuple)
    assert band.elements == (A.NumberLit(12.0, "Hz"), A.NumberLit(15.0, "Hz"))
    assert _arg(c, "order").value == A.NumberLit(4.0, None)


def test_bandpass_center_bandwidth_form():
    c = _call(_parse_expr("bandpass(center: orf, bandwidth: ratio(2.5), order: 4)"))
    assert _arg(c, "center").value == A.NameRef("orf")
    bw = _arg(c, "bandwidth").value
    assert isinstance(bw, A.Call) and bw.callee == "ratio"


def test_hilbert_no_args():
    c = _call(_parse_expr("hilbert()"))
    assert c.callee == "hilbert"
    assert c.args == ()


def test_bandpower():
    c = _call(_parse_expr('bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)'))
    assert c.callee == "bandpower"
    assert _arg(c, "input").value == A.StringLit("raw")


# ---------------------------------------------------------------------------
# Time-series math (PRIMITIVES.md "Time-series math")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["differentiate", "magnitude", "rectify"])
def test_unary_no_arg_primitives(name):
    c = _call(_parse_expr(f"{name}()"))
    assert c.callee == name
    assert c.args == ()


def test_smooth():
    c = _call(_parse_expr("smooth(tau: 1500 ms)"))
    assert _arg(c, "tau").value == A.NumberLit(1500.0, "ms")


def test_decimate():
    c = _call(_parse_expr("decimate(target_rate: 64 Hz)"))
    assert _arg(c, "target_rate").value == A.NumberLit(64.0, "Hz")


# ---------------------------------------------------------------------------
# Statistics (PRIMITIVES.md "Statistics")
# ---------------------------------------------------------------------------


def test_auto_range():
    c = _call(_parse_expr("auto_range(window: 5 min, percentile: (5, 95))"))
    pct = _arg(c, "percentile").value
    assert isinstance(pct, A.Tuple)
    assert pct.elements == (A.NumberLit(5.0, None), A.NumberLit(95.0, None))


def test_percentile():
    c = _call(_parse_expr("percentile(target_pct: 70, window: 2 min)"))
    assert _arg(c, "target_pct").value == A.NumberLit(70.0, None)


# ---------------------------------------------------------------------------
# Mappings (PRIMITIVES.md "Mappings")
# ---------------------------------------------------------------------------


def test_sigmoid():
    c = _call(_parse_expr('sigmoid("reward_signal", midpoint: 0.5, steepness: 4)'))
    assert c.args[0] == A.Arg(name=None, value=A.StringLit("reward_signal"))
    assert _arg(c, "midpoint").value == A.NumberLit(0.5)


def test_linear():
    c = _call(_parse_expr('linear("x", midpoint: 1.0, slope: 0.5)'))
    assert c.callee == "linear"


def test_dead_zone_planned_but_parseable():
    # Listed as planned v0.1 in PRIMITIVES.md; still a syntactic call.
    c = _call(_parse_expr('dead_zone("x", center: 0 uV, width: 2 uV)'))
    assert c.callee == "dead_zone"


# ---------------------------------------------------------------------------
# Conditions (PRIMITIVES.md "Conditions")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["above", "below"])
def test_above_below(name):
    c = _call(_parse_expr(f'{name}("smr_envelope", "smr_t")'))
    assert c.callee == name
    assert [a.value for a in c.args] == [A.StringLit("smr_envelope"), A.StringLit("smr_t")]


def test_inside():
    c = _call(_parse_expr('inside("alpha", low: 5 uV, high: 25 uV)'))
    assert c.callee == "inside"
    assert _arg(c, "low").value == A.NumberLit(5.0, "uV")


def test_all_of_takes_array():
    src = '''all_of([above("smr_envelope", "smr_t"), below("theta", "theta_t")])'''
    c = _call(_parse_expr(src))
    arr = c.args[0].value
    assert isinstance(arr, A.Array)
    assert len(arr.elements) == 2
    assert isinstance(arr.elements[0], A.Call)
    assert arr.elements[0].callee == "above"


def test_any_of():
    c = _call(_parse_expr('any_of([above("x", "t1"), below("y", "t2")])'))
    assert c.callee == "any_of"
    assert isinstance(c.args[0].value, A.Array)


# ---------------------------------------------------------------------------
# Event-producing primitives (PRIMITIVES.md "Event-producing primitives")
# ---------------------------------------------------------------------------


def test_dwell_with_condition_and_duration():
    src = '''dwell(condition: above("smr", "smr_t"), duration: 250 ms)'''
    c = _call(_parse_expr(src))
    assert c.callee == "dwell"
    cond = _arg(c, "condition").value
    assert isinstance(cond, A.Call) and cond.callee == "above"
    assert _arg(c, "duration").value == A.NumberLit(250.0, "ms")


# ---------------------------------------------------------------------------
# Inhibit actions (PRIMITIVES.md "Inhibit actions")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, args, expected_arg_names", [
    ("mute", "release: 200 ms", ["release"]),
    ("freeze", "release: 500 ms", ["release"]),
    ("flag", "", []),
])
def test_inhibit_actions(name, args, expected_arg_names):
    c = _call(_parse_expr(f"{name}({args})"))
    assert c.callee == name
    assert [a.name for a in c.args] == expected_arg_names


# ---------------------------------------------------------------------------
# Threshold-type constructors
# ---------------------------------------------------------------------------


def test_absolute_threshold():
    c = _call(_parse_expr("absolute(8 uV)"))
    assert c.callee == "absolute"
    assert c.args[0].name is None
    assert c.args[0].value == A.NumberLit(8.0, "uV")


def test_percentile_threshold():
    c = _call(_parse_expr("percentile(target_pct: 70, window: 2 min)"))
    assert c.callee == "percentile"


# ---------------------------------------------------------------------------
# Rate alignment (PRIMITIVES.md "Rate alignment")
# ---------------------------------------------------------------------------


def test_align_to_with_target():
    c = _call(_parse_expr('align_to("raw_env", target: "auto_ranged")'))
    assert c.callee == "align_to"
    assert c.args[0].value == A.StringLit("raw_env")
    assert _arg(c, "target").value == A.StringLit("auto_ranged")


def test_align_to_with_rate():
    c = _call(_parse_expr('align_to("raw_env", rate: 64 Hz)'))
    assert _arg(c, "rate").value == A.NumberLit(64.0, "Hz")


def test_align_to_with_mode():
    c = _call(_parse_expr('align_to("x", target: "y", mode: "interpolate")'))
    assert _arg(c, "mode").value == A.StringLit("interpolate")


# ---------------------------------------------------------------------------
# Vector reductions (PRIMITIVES.md "Vector reductions" — sketches)
# ---------------------------------------------------------------------------


def test_pct_in_range():
    # SPEC §3 has no unary minus and `range: (-1, 1)` from TOUR §7's LZT
    # sketch is itself flagged as incomplete in SPEC §10. Use a positive
    # range to exercise the call shape; negative-literal support is a
    # spec-revision matter (noted in PR body).
    c = _call(_parse_expr('pct_in_range("deltas", range: (0, 2))'))
    assert c.callee == "pct_in_range"
    rng = _arg(c, "range").value
    assert isinstance(rng, A.Tuple)


def test_weighted_sum():
    c = _call(_parse_expr('weighted_sum("vec", weights: [1, 2, 3])'))
    weights = _arg(c, "weights").value
    assert isinstance(weights, A.Array)
    assert len(weights.elements) == 3


# ---------------------------------------------------------------------------
# External providers (PRIMITIVES.md "External providers")
# ---------------------------------------------------------------------------


def test_norms_lookup_member_access_chain():
    # `norms.power_db.lookup(...)` is a chained member access on `norms`,
    # then a call. Spec §3 doesn't define call-on-member-access; the
    # grammar accepts it via factor's member_chain at the atom level
    # only — bare member access without a call is the supported form.
    # We test the more conservative shape: reference the chain by name.
    f = parse('protocol "P" { meta { x = norms.power_db } }')
    val = f.protocol.body[0].body[0].value
    assert isinstance(val, A.MemberAccess)
    assert val.member == "power_db"
    assert isinstance(val.target, A.NameRef) and val.target.name == "norms"


def test_client_member_access():
    f = parse('protocol "P" { meta { x = client.age } }')
    val = f.protocol.body[0].body[0].value
    assert isinstance(val, A.MemberAccess)
    assert val.target == A.NameRef("client")
    assert val.member == "age"


# ---------------------------------------------------------------------------
# Stream arithmetic (PRIMITIVES.md "Stream arithmetic")
# ---------------------------------------------------------------------------


def test_stream_division():
    e = _parse_expr('"smr_envelope" / "smr_t"')
    assert isinstance(e, A.BinaryOp)
    assert e.op == "/"
    assert e.left == A.StringLit("smr_envelope")
    assert e.right == A.StringLit("smr_t")


def test_asymmetry_expression():
    src = '("left_alpha" - "right_alpha") / ("left_alpha" + "right_alpha")'
    e = _parse_expr(src)
    assert isinstance(e, A.BinaryOp) and e.op == "/"
    assert isinstance(e.left, A.BinaryOp) and e.left.op == "-"
    assert isinstance(e.right, A.BinaryOp) and e.right.op == "+"


def test_comparison_operators():
    e = _parse_expr('"x" > "y"')
    assert isinstance(e, A.BinaryOp) and e.op == ">"


def test_ternary_with_member_chain():
    e = _parse_expr("reward.event.holds ? reward.continuous : 0")
    assert isinstance(e, A.Conditional)
    cond = e.cond
    assert isinstance(cond, A.MemberAccess) and cond.member == "holds"
    assert isinstance(cond.target, A.MemberAccess) and cond.target.member == "event"


def test_operator_precedence_mul_over_add():
    e = _parse_expr("1 + 2 * 3")
    assert isinstance(e, A.BinaryOp) and e.op == "+"
    assert isinstance(e.right, A.BinaryOp) and e.right.op == "*"


def test_left_associative_addition():
    e = _parse_expr("1 + 2 + 3")
    assert e.op == "+"
    # Left-associative: ((1+2)+3) => left is BinaryOp(1+2)
    assert isinstance(e.left, A.BinaryOp) and e.left.op == "+"
    assert e.right == A.NumberLit(3.0)


def test_multiple_member_chain_normalises_left_associatively():
    e = _parse_expr("a.b.c.d")
    # Outermost member access is `.d`; target is `a.b.c`.
    assert isinstance(e, A.MemberAccess) and e.member == "d"
    assert isinstance(e.target, A.MemberAccess) and e.target.member == "c"
