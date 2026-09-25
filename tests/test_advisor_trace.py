# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
from refrain import parse
from refrain.resolver import resolve
from refrain.advisor_trace import controls_in, reward_checks, trace_check, trace_protocol
from refrain.ir_json import ir_to_json_obj
from tests._autopilot_fixtures import plain


def _obj(src, **bindings):
    return ir_to_json_obj(resolve(parse(src), bindings=bindings or None))


def test_adaptive_trace():
    t = trace_protocol(_obj(plain()))
    checks = t["bundles"][""]["checks"]
    assert t["bundles"][""]["combine"] == "all"
    assert (checks[0]["knob"], checks[0]["higher_is"]) == ("t_pct", "harder")
    assert (checks[1]["knob"], checks[1]["higher_is"]) == ("xover", "harder")
    assert checks[1]["controls"] == ["xover"]
    assert t["inhibit_controls"] == ["emg_pct"]
    assert t["check_controls"] == ["t_pct", "xover"]


def test_baseline_trace_follows_absolute_threshold():
    checks = trace_protocol(_obj(plain(), threshold_style="baseline"))["bundles"][""]["checks"]
    assert (checks[0]["knob"], checks[0]["higher_is"]) == ("t_uv", "harder")


def _ctl(name, kind="number", live=True):
    return {"canonical_name": f"control/{name}", "type_kind": kind, "live_tunable": live}


def _ref(name):
    return {"node": "control_ref", "target": f"control/{name}", "default": 1.0}


def _sig():
    return {"node": "stream_ref", "target": "derive/s"}


def test_below_with_control_is_easier():
    ir = {"controls": {"k": _ctl("k")}, "derives": {}, "thresholds": {}}
    below = {"node": "call", "callee": "below", "args": [
        {"name": None, "value": _sig()}, {"name": None, "value": _ref("k")}]}
    assert trace_check(below, ir) == ("k", "easier")


def test_below_percentile_threshold_is_easier():
    ir = {"controls": {"k": _ctl("k", "percent")}, "derives": {},
          "thresholds": {"t": {"threshold_call": {"node": "call", "callee": "percentile", "args": [
              {"name": "target_pct", "value": _ref("k")},
              {"name": "window", "value": {"node": "number", "value": 2.0}}]}}}}
    below = {"node": "call", "callee": "below", "args": [
        {"name": None, "value": _sig()},
        {"name": None, "value": {"node": "threshold_ref", "target": "threshold/t"}}]}
    assert trace_check(below, ir) == ("k", "easier")


def test_ambiguous_shapes_give_no_trace():
    ir = {"controls": {"k": _ctl("k"), "j": _ctl("j")}, "derives": {}, "thresholds": {}}
    two = {"node": "binop", "op": "*", "left": _ref("k"), "right": _ref("j")}
    call = {"node": "call", "callee": "above", "args": [
        {"name": None, "value": _sig()}, {"name": None, "value": two}]}
    assert trace_check(call, ir) is None
    not_live = {"controls": {"k": _ctl("k", live=False)}, "derives": {}, "thresholds": {}}
    above = {"node": "call", "callee": "above", "args": [
        {"name": None, "value": _sig()}, {"name": None, "value": _ref("k")}]}
    assert trace_check(above, not_live) is None
    wrong_kind = {"controls": {"k": _ctl("k", "boolean")}, "derives": {}, "thresholds": {}}
    assert trace_check(above, wrong_kind) is None


def test_controls_in_follows_derives():
    ir = {"derives": {"s": {"expression": _ref("k")}}, "thresholds": {}}
    assert controls_in(_sig(), ir) == {"k"}


def test_single_condition_dwell_is_one_check():
    dwell = {"node": "call", "callee": "dwell", "args": [
        {"name": "condition", "value": {"node": "call", "callee": "above", "args": []}}]}
    checks, combine = reward_checks(dwell)
    assert len(checks) == 1 and combine == "all"
    assert reward_checks(None) == ([], "all")
