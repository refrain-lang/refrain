# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Parser surface for protocol autopilot: `autopilot { }` and `as "<name>"`."""

from refrain import ast as A
from refrain import parse, unparse

SRC = '''protocol "p" {
  meta { version = "1.0"; evidence = "clinical"; description = "x" }
  reward {
    event = dwell(condition: all_of([above("a", "b") as "theta", above("c", k)]), duration: 1 s)
  }
  autopilot {
    evidence = "expert_opinion"
    watch = 2 min
    emg = guard { max = 15%; say = "Muscle." }
  }
}'''


def _section(proto, kw):
    return next(s for s in proto.body if isinstance(s, A.SectionBlock) and s.keyword == kw)


def test_labeled_array_element_parses():
    proto = parse(SRC).protocol
    dwell = _section(proto, "reward").body[0].value
    arr = dwell.args[0].value.args[0].value
    assert isinstance(arr, A.Array)
    first, second = arr.elements
    assert isinstance(first, A.Labeled)
    assert first.label == "theta"
    assert isinstance(first.expr, A.Call) and first.expr.callee == "above"
    assert isinstance(second, A.Call)          # unlabeled elements are unchanged


def test_autopilot_section_parses():
    body = _section(parse(SRC).protocol, "autopilot").body
    entries = {s.target: s.value for s in body}
    assert entries["evidence"] == A.StringLit("expert_opinion")
    assert entries["watch"] == A.NumberLit(2.0, "min")
    assert isinstance(entries["emg"], A.BlockExpr) and entries["emg"].name == "guard"


def test_amend_autopilot_parses():
    f = parse('protocol "c" extends "p" { amend autopilot { watch = 3 min } }')
    stmt = f.protocol.body[0]
    assert isinstance(stmt, A.AmendDecl) and stmt.target_kw == "autopilot"


def test_round_trip_keeps_labels_and_section():
    f = parse(SRC)
    text = unparse(f)
    assert 'as "theta"' in text
    assert parse(text) == f
    assert unparse(parse(text)) == text
