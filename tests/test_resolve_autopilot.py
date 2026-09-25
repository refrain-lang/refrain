# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Resolver: named reward checks, the autopilot section, control policies,
and the trace-based cross-checks (V1–V17 in the spec)."""

import pytest

from refrain import parse
from refrain.compile_json import compile_to_ir_json
from refrain.fanout import _reject_check_labels
from refrain.resolver import ResolveError, resolve
from tests._autopilot_fixtures import ap, plain, T_PCT_AP, T_UV_AP, XOVER_AP


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


# --- the autopilot section ------------------------------------------------

def test_autopilot_section_resolves():
    a = _ir(ap()).autopilot
    assert a.evidence == "expert_opinion"
    assert a.citations == ("Test policy",)
    assert a.reward_target == (0.10, 0.35)
    assert a.phases == ("train1", "train2")
    assert (a.watch_ms, a.between_moves_ms, a.equipment_settle_ms) == (20000.0, 30000.0, 5000.0)
    assert a.tighten_first == ("crossover", "theta")
    assert [(g.inhibit, g.max_frac, g.say) for g in a.guards] == [("emg", 0.15, "Muscle artifact.")]


def test_protocol_without_block_has_no_autopilot():
    assert _ir(plain()).autopilot is None


def test_citation_list_is_accepted():
    src = ap().replace('citation         = "Test policy"', 'citation = ["A 2020", "B 2021"]')
    assert _ir(src).autopilot.citations == ("A 2020", "B 2021")


@pytest.mark.parametrize("line, needle", [
    ('evidence         = "expert_opinion"', "evidence"),
    ('citation         = "Test policy"', "citation"),
    ('rationale        = "Test rationale"', "rationale"),
])
def test_provenance_is_required(line, needle):
    assert needle in _err(ap().replace(line, ""))


def test_evidence_must_be_a_known_level():
    msg = _err(ap().replace('"expert_opinion"', '"demo"'))
    assert "evidence" in msg and "expert_opinion" in msg


@pytest.mark.parametrize("target", ["(35%, 10%)", "(10, 35)", "(0%, 50%)", "(10%)"])
def test_bad_reward_target(target):
    assert "reward_target" in _err(ap().replace("(10%, 35%)", target))


def test_phase_must_exist_and_be_unmuted():
    assert "'nope'" in _err(ap().replace('["train1", "train2"]', '["train1", "nope"]'))
    assert "muted" in _err(ap().replace('["train1", "train2"]', '["rest"]'))


def test_guard_must_name_an_inhibit():
    assert "inhibit" in _err(ap().replace("emg = guard", "delta = guard"))


def test_unknown_setting_is_an_error():
    assert "wach" in _err(ap().replace("watch            =", "wach ="))


def test_limiter_and_tighten_first_must_name_checks():
    src = ap().replace('emg = guard { max = 15%; say = "Muscle artifact." }',
                       'emg = guard { max = 15%; say = "M." }\n    nope = limiter { say = "x" }')
    assert "'nope'" in _err(src)
    assert "'zzz'" in _err(ap().replace('["crossover", "theta"]', '["zzz"]'))


def test_duration_settings_must_be_positive_durations():
    assert "watch" in _err(ap().replace("watch            = 20 s", "watch = 20"))


from tests.test_compose import _dict_loader


def test_child_block_replaces_and_amend_merges():
    parent = ap()
    child_replace = ('protocol "c" extends "base" { autopilot { evidence = "published"; '
                     'citation = "C"; rationale = "R" } }')
    ir = resolve(parse(child_replace), parent_loader=_dict_loader({"base": parent}))
    assert ir.autopilot.evidence == "published" and ir.autopilot.watch_ms is None
    child_amend = 'protocol "c" extends "base" { amend autopilot { watch = 1 min } }'
    ir = resolve(parse(child_amend), parent_loader=_dict_loader({"base": parent}))
    assert ir.autopilot.watch_ms == 60000.0 and ir.autopilot.evidence == "expert_opinion"


# --- per-control policies -------------------------------------------------

def test_policies_resolve_adaptive():
    ir = _ir(ap())
    x = ir.controls["xover"].autopilot
    assert (x.strategy, x.fixes, x.higher_is, x.apply) == ("fixed_step", "crossover", "harder", "auto")
    assert x.limits == (0.5, 1.0) and x.step == 0.05 and x.round_to == 0.01
    assert ir.controls["t_pct"].autopilot.limits == (15.0, 40.0)
    assert ir.controls["t_uv"].autopilot is None          # only_when baseline -> dropped


def test_policies_resolve_baseline():
    ir = _ir(ap(), threshold_style="baseline")
    assert ir.controls["t_pct"].autopilot is None
    p = ir.controls["t_uv"].autopilot
    assert p.strategy == "proportional_step" and p.step == 0.10 and p.round_to == 0.1


def test_limits_default_to_range():
    ir = _ir(ap(xover_ap=XOVER_AP.replace("limits = (0.5, 1.0); ", "")))
    assert ir.controls["xover"].autopilot.limits == (0.5, 1.0)


def test_rebaseline_resolves():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    p = _ir(ap(t_uv_ap=pol), threshold_style="baseline").controls["t_uv"].autopilot
    assert (p.from_entity, p.window_ms, p.percentile) == ("derive/t_env", 10000.0, 60.0)


@pytest.mark.parametrize("override, needle", [
    ({"xover_ap": XOVER_AP.replace("fixed_step", "wobble")}, "wobble"),
    ({"xover_ap": XOVER_AP.replace('"auto"', '"sometimes"')}, "apply"),
    ({"xover_ap": XOVER_AP.replace('"harder"', '"tougher"')}, "higher_is"),
    ({"xover_ap": XOVER_AP.replace("limits = (0.5, 1.0)", "limits = (0.4, 1.0)")}, "range"),
    ({"xover_ap": XOVER_AP.replace("limits = (0.5, 1.0)", "limits = (0.9, 0.6)")}, "limits"),
    ({"xover_ap": XOVER_AP.replace("step = 0.05", "step = 0")}, "step"),
    ({"xover_ap": XOVER_AP.replace("step = 0.05", "step = 0.05 uV")}, "units"),
    ({"xover_ap": XOVER_AP.replace("fixed_step", "proportional_step")}, "percent"),
    ({"xover_ap": XOVER_AP.replace("round_to = 0.01", "round_to = 0.01; colour = 1")}, "colour"),
])
def test_policy_field_errors(override, needle):
    assert needle in _err(ap(**override))


def test_rebaseline_cannot_be_auto():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "auto"; '
           'only_when = threshold_style == "baseline" }')
    assert "suggest" in _err(ap(t_uv_ap=pol), threshold_style="baseline")


def test_rebaseline_window_must_fit_watch():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 30 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    assert "watch" in _err(ap(t_uv_ap=pol), threshold_style="baseline")


def test_not_live_tunable_is_rejected():
    src = ap().replace("xover  = number  { default = 0.60; range = (0.5, 1.0); live_tunable = true;",
                       "xover  = number  { default = 0.60; range = (0.5, 1.0);")
    assert "live_tunable" in _err(src)


def test_not_live_tunable_is_rejected_even_when_only_when_is_inactive():
    # Eligibility depends only on the control's own declaration, not on
    # whether `only_when` happens to be true for the binding being compiled.
    # Here the binding is the default "adaptive", so `only_when = ... ==
    # "baseline"` would make the policy inactive -- but the control still
    # isn't live_tunable, so this must still be rejected.
    src = ap(xover_ap=XOVER_AP.replace(
        "round_to = 0.01 }", 'round_to = 0.01; only_when = threshold_style == "baseline" }',
    )).replace(
        "xover  = number  { default = 0.60; range = (0.5, 1.0); live_tunable = true;",
        "xover  = number  { default = 0.60; range = (0.5, 1.0);",
    )
    assert "live_tunable" in _err(src)


def test_ineligible_kind_is_rejected_even_when_only_when_is_inactive():
    # Same principle for control kind: a `duration` control can never carry
    # an autopilot policy, regardless of whether `only_when` is active under
    # the default "adaptive" binding.
    extra = ('hold = duration { default = 1 s; range = (0.5 s, 5 s); live_tunable = true; '
             'autopilot = fixed_step { fixes = "theta"; step = 0.1; higher_is = "harder"; '
             'apply = "suggest"; only_when = threshold_style == "baseline" } }')
    assert "duration" in _err(ap(extra_controls=extra))


def test_mode_control_policy_is_rejected():
    src = ap().replace('threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }',
                       'threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive"; '
                       'autopilot = fixed_step { fixes = "theta"; step = 1; higher_is = "harder"; apply = "suggest" } }')
    assert "mode" in _err(src)


@pytest.mark.parametrize("cond, needle", [
    ('t_pct == "adaptive"', "mode"),
    ('threshold_style == "sometimes"', "sometimes"),
    ('threshold_style', "compare"),
])
def test_only_when_errors(cond, needle):
    assert needle in _err(ap(t_pct_ap=T_PCT_AP.replace('threshold_style == "adaptive"', cond)))


def test_policy_without_block_is_an_error():
    assert "autopilot { }" in _err(ap(autopilot=""))
