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
