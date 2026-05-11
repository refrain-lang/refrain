# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""All three canonical examples must parse, produce a structurally sound
AST, and round-trip through the unparser to an AST equal to the original."""

from __future__ import annotations

from pathlib import Path

import pytest

from refrain import ast as A
from refrain import parse, parse_file, unparse

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

EXAMPLES = ["smr_cz.refrain", "othmer_ilf_t3t4.refrain", "alpha_theta.refrain"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_parses(name):
    f = parse_file(EXAMPLES_DIR / name)
    assert isinstance(f, A.File)
    assert isinstance(f.protocol, A.Protocol)
    assert f.protocol.name  # non-empty


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_has_required_meta_fields(name):
    """SPEC §4.1 mandates version, evidence, description."""
    f = parse_file(EXAMPLES_DIR / name)
    meta = next(
        s for s in f.protocol.body
        if isinstance(s, A.SectionBlock) and s.keyword == "meta"
    )
    field_names = {a.target for a in meta.body if isinstance(a, A.Assignment)}
    assert "version" in field_names
    assert "evidence" in field_names
    assert "description" in field_names


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_round_trips_through_unparser(name):
    """parse -> AST -> unparse -> parse -> AST yields an equal AST.

    This is the strong acceptance criterion: structural equality survives
    the unparser, proving the AST captures everything semantically
    meaningful in the source.
    """
    original_ast = parse_file(EXAMPLES_DIR / name)
    rendered = unparse(original_ast)
    rerendered_ast = parse(rendered)
    assert original_ast == rerendered_ast


@pytest.mark.parametrize("name", EXAMPLES)
def test_round_trip_is_idempotent(name):
    """Double-unparse should converge: unparse(parse(unparse(parse(src))))
    equals unparse(parse(src))."""
    original_ast = parse_file(EXAMPLES_DIR / name)
    once = unparse(original_ast)
    twice = unparse(parse(once))
    assert once == twice


# ---------------------------------------------------------------------------
# Structural spot checks on the three protocols.
# ---------------------------------------------------------------------------


def test_smr_cz_uses_dwell_with_all_of():
    f = parse_file(EXAMPLES_DIR / "smr_cz.refrain")
    reward = next(s for s in f.protocol.body if isinstance(s, A.SectionBlock) and s.keyword == "reward")
    event_assign = next(s for s in reward.body if isinstance(s, A.Assignment) and s.target == "event")
    dwell = event_assign.value
    assert isinstance(dwell, A.Call) and dwell.callee == "dwell"
    condition_arg = next(a for a in dwell.args if a.name == "condition")
    assert isinstance(condition_arg.value, A.Call)
    assert condition_arg.value.callee == "all_of"


def test_othmer_ilf_uses_orf_control_with_log_scale():
    f = parse_file(EXAMPLES_DIR / "othmer_ilf_t3t4.refrain")
    controls = next(s for s in f.protocol.body if isinstance(s, A.SectionBlock) and s.keyword == "controls")
    orf_assign = next(s for s in controls.body if isinstance(s, A.Assignment) and s.target == "orf")
    block = orf_assign.value
    assert isinstance(block, A.BlockExpr) and block.name == "frequency"
    log_field = next(s for s in block.body if isinstance(s, A.Assignment) and s.target == "log")
    assert log_field.value == A.BoolLit(True)


def test_alpha_theta_uses_formula_for_crossover():
    f = parse_file(EXAMPLES_DIR / "alpha_theta.refrain")
    derives = [s for s in f.protocol.body if isinstance(s, A.NamedDecl) and s.keyword == "derive"]
    crossover = next(d for d in derives if d.name == "theta_minus_alpha")
    formula_assign = crossover.body[0]
    assert formula_assign.target == "formula"
    assert isinstance(formula_assign.value, A.BinaryOp)
    assert formula_assign.value.op == "-"


def test_othmer_ilf_uses_bipolar_montage():
    f = parse_file(EXAMPLES_DIR / "othmer_ilf_t3t4.refrain")
    inp = next(s for s in f.protocol.body if isinstance(s, A.NamedDecl) and s.keyword == "input")
    montage = next(a for a in inp.body if a.target == "montage").value
    assert isinstance(montage, A.Call) and montage.callee == "bipolar"
    plus = next(a for a in montage.args if a.name == "plus")
    minus = next(a for a in montage.args if a.name == "minus")
    assert plus.value == A.StringLit("T3")
    assert minus.value == A.StringLit("T4")
