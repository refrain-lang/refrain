# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
from refrain.ir import (
    IRAutopilot, IRControlAutopilot, IRGuard, IRLimiter, IRReward,
)


def test_new_fields_default_to_absent():
    r = IRReward(continuous=None, event=None)
    assert r.check_names == ()


def test_autopilot_shapes_construct():
    ap = IRAutopilot(
        evidence="expert_opinion", citations=("x",), rationale="y",
        guards=(IRGuard(inhibit="emg", max_frac=0.15, say=None),),
        limiters=(IRLimiter(check="hb", say="coach"),),
    )
    assert ap.reward_target is None and ap.tighten_first == ()
    pol = IRControlAutopilot(
        strategy="fixed_step", fixes="crossover", higher_is="harder",
        apply="auto", limits=(0.5, 1.0), step=0.05,
    )
    assert pol.round_to is None and pol.citations == ()
