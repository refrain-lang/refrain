# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The worked alpha/theta autopilot example compiles in both modes, carries
the intended policy, and never lets a guard knob be auto-adjusted."""

from pathlib import Path

import numpy as np
import pytest

from refrain import parse, parse_file
from refrain.amp_profile import load_amp_profile
from refrain.compile_json import compile_to_ir_json
from refrain.eval_ import Evaluator
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent
PATH = REPO / "examples" / "alpha_theta_autopilot.refrain"
AMP = load_amp_profile(REPO / "src" / "refrain" / "amp_profiles" / "q21.json")


@pytest.mark.parametrize("style", ["adaptive", "baseline"])
def test_compiles_in_both_modes(style):
    res = compile_to_ir_json(PATH.read_text(), amp="q21", bindings={"threshold_style": style})
    assert not res.errors, [e.message for e in res.errors]
    assert res.ir_json["refrain_ir_version"] == "0.4"
    assert res.ir_json["reward"]["check_names"] == ["theta", "crossover"]


def test_policy_shape():
    ir = resolve(parse_file(PATH), AMP)
    c = {n: ctl.autopilot for n, ctl in ir.controls.items()}
    assert (c["crossover_target"].step, c["crossover_target"].apply) == (0.05, "auto")
    assert (c["theta_reward_pct"].step, c["theta_reward_pct"].apply) == (5.0, "suggest")
    assert c["theta_threshold_uv"] is None                  # baseline-only, adaptive is default
    for guard_knob in ("delta_inhibit_rate", "artifact_strictness"):
        assert c[guard_knob] is None
    ap = ir.autopilot
    assert ap.reward_target == (0.10, 0.35) and ap.phases == ("deep1", "deep2")
    assert {g.inhibit for g in ap.guards} == {"delta", "emg"}


def test_auto_on_a_guard_knob_is_refused():
    src = PATH.read_text().replace(
        'label        = "Artifact guard strictness"',
        'label        = "Artifact guard strictness"\n      autopilot = fixed_step { fixes = "theta"; '
        'step = 1; higher_is = "easier"; apply = "auto" }')
    res = compile_to_ir_json(src, amp="q21")
    assert res.errors  # does not feed theta (V2) or feeds a guard (V4): refused either way


def test_runs_and_advises():
    ir = resolve(parse_file(PATH), AMP)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Pz", "A1", "A2"),
                        backend="python")
    ev.start(skip_warmup=False)
    rng = np.random.default_rng(0)
    for _ in range(125):                                     # 2 min settle + 5 s
        ev.step_chunk(rng.normal(0, 10, size=(256, 3)))
    a = ev.advice()
    assert a["reason"] in ("collecting", "guard")
    assert ev.autopilot_policy()["provenance"]["evidence"] == "expert_opinion"
