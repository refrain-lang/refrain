# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Mode 2a set-replication fan-out pre-pass (Task 4)."""

from pathlib import Path

import pytest

from refrain.amp_profile import load_amp_profile
from refrain.ir import IRArray, IRCall
from refrain.parser import parse
from refrain.resolver import ResolveError, resolve

_AMP = load_amp_profile(
    Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json"
)

_REPL = """
    protocol "poise_ms" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "smr" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "smr_t" { signal = "smr"; type = absolute(8 uV) }
      reward { combine = "all"; event = dwell(condition: above("smr","smr_t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
"""


def _active_of(ir, input_name):
    call = ir.inputs[input_name].montage
    return next(a.value.value for a in call.args if a.name == "active")


def _condition_call(ir):
    """Pull the dwell's `condition` arg (an IRCall) out of reward.event."""
    event = ir.reward.event
    return next(a.value for a in event.args if a.name == "condition")


def test_fan_out_replicates_per_site():
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["C3", "Cz", "C4"]})
    # Per-site inputs/derives/thresholds, named <name>@<site>.
    assert set(ir.inputs) == {"raw@C3", "raw@Cz", "raw@C4"}
    assert set(ir.derives) == {"smr@C3", "smr@Cz", "smr@C4"}
    assert set(ir.thresholds) == {"smr_t@C3", "smr_t@Cz", "smr_t@C4"}
    # Each per-site input names its own channel.
    assert _active_of(ir, "raw@C3") == "C3"
    assert _active_of(ir, "raw@Cz") == "Cz"
    assert _active_of(ir, "raw@C4") == "C4"


def test_fan_out_per_site_derive_refs_own_input():
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["C3", "Cz", "C4"]})
    # derive smr@C3 must consume input/raw@C3 (not the un-suffixed input/raw).
    assert ir.derives["smr@C3"].upstream == ("input/raw@C3",)
    assert ir.derives["smr@C4"].upstream == ("input/raw@C4",)
    # threshold smr_t@Cz must point at derive/smr@Cz.
    assert ir.thresholds["smr_t@Cz"].signal == "derive/smr@Cz"


def test_fan_out_combine_all_wraps_conditions():
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["C3", "Cz", "C4"]})
    cond = _condition_call(ir)
    assert isinstance(cond, IRCall)
    assert cond.callee == "all_of"  # combine="all"
    # one arg: the array of per-site conditions.
    arr = cond.args[0].value
    assert isinstance(arr, IRArray)
    assert len(arr.elements) == 3
    # each element is an `above(...)` over a distinct per-site stream/threshold.
    callees = [e.callee for e in arr.elements]
    assert callees == ["above", "above", "above"]
    streams = {e.args[0].value.target for e in arr.elements}
    assert streams == {"derive/smr@C3", "derive/smr@Cz", "derive/smr@C4"}


def test_fan_out_combine_any():
    ir = resolve(
        parse(_REPL.replace('combine = "all"', 'combine = "any"')),
        _AMP,
        bindings={"sites": ["C3", "Cz", "C4"]},
    )
    cond = _condition_call(ir)
    assert cond.callee == "any_of"
    assert len(cond.args[0].value.elements) == 3


def test_fan_out_single_site_degenerates():
    # min=1, bind one site → still works (one input/derive/threshold, combine over 1).
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["Cz"]})
    assert set(ir.inputs) == {"raw@Cz"}
    assert set(ir.derives) == {"smr@Cz"}
    assert set(ir.thresholds) == {"smr_t@Cz"}
    cond = _condition_call(ir)
    assert cond.callee == "all_of"
    assert len(cond.args[0].value.elements) == 1


def test_fan_out_uses_default_when_unbound():
    # No bindings → the declared default ["Cz"] drives the fan-out.
    ir = resolve(parse(_REPL), _AMP)
    assert set(ir.inputs) == {"raw@Cz"}


# --- Scoping guards (Task 4 Step 5; locked again in Task 5) ----------------


def test_continuous_reward_over_set_rejected():
    src = _REPL.replace(
        'reward { combine = "all"; event = dwell(condition: above("smr","smr_t"), duration: 100 ms) }',
        'reward { continuous = sigmoid("smr", midpoint: 0 uV, steepness: 1) }',
    )
    with pytest.raises(ResolveError, match="continuous.*aggregat|Mode 2b|aggregation"):
        resolve(parse(src), _AMP, bindings={"sites": ["C3", "Cz"]})
