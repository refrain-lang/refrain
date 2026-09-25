# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Resolver: named reward checks, the autopilot section, control policies,
and the trace-based cross-checks (V1–V17 in the spec)."""

import pytest

from refrain import parse
from refrain.compile_json import compile_to_ir_json
from refrain.fanout import _reject_check_labels
from refrain.resolver import ResolveError, resolve
from tests._autopilot_fixtures import ap, plain


def _ir(src, **bindings):
    return resolve(parse(src), bindings=bindings or None)


def _err(src, **bindings) -> str:
    res = compile_to_ir_json(src, bindings=bindings or None)
    assert res.errors, "expected a resolve error"
    return res.errors[0].message


# --- named checks ---------------------------------------------------------

def test_check_names_recorded_in_condition_order():
    assert _ir(plain(theta_as=' as "theta"', xover_as=' as "crossover"')).reward.check_names == (
        "theta", "crossover")


def test_unnamed_checks_leave_check_names_empty():
    assert _ir(plain()).reward.check_names == ()


def test_partially_named_checks_use_none():
    assert _ir(plain(xover_as=' as "crossover"')).reward.check_names == (None, "crossover")


def test_duplicate_check_name_is_an_error():
    msg = _err(plain(theta_as=' as "x"', xover_as=' as "x"'))
    assert "'x'" in msg and "twice" in msg


def test_label_outside_reward_condition_list_is_an_error():
    src = plain().replace(
        "output { audio_chime = reward.event }",
        'output { audio_chime = reward.event; x = all_of([reward.event.holds as "h"]) }')
    assert "reward check" in _err(src)


def test_fanout_rejects_labels():
    with pytest.raises(ResolveError, match="per-site"):
        _reject_check_labels(parse(plain(theta_as=' as "theta"')), "per-site")
    _reject_check_labels(parse(plain()), "per-site")   # no labels: no error
