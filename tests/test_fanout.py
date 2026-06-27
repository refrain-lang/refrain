# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Mode 2a set-replication fan-out pre-pass (Task 4)."""

from pathlib import Path

import pytest

import refrain.ast as A
from refrain.amp_profile import load_amp_profile
from refrain.ir import IRArray, IRCall
from refrain.ir_json import ir_to_json_obj
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


_LABEL_COLLISION = """
    protocol "ms_label" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "smr" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "smr_t" { signal = "smr"; type = absolute(8 uV) }
      input "frontal" { montage = referential(active: "F3", reference: "linked_ears") }
      derive "fa" { from = "frontal"; pipeline = [smooth(tau: 100 ms)]; label = "smr" }
      reward { combine = "all"; event = dwell(condition: above("smr","smr_t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
"""


def test_unrelated_string_field_does_not_create_false_dependency():
    # `fa` is a fixed-channel derive whose `label` coincidentally equals the
    # per-site entity name "smr". The label must NOT create a dependency edge
    # (which would wrongly pull `fa` per-site and trip the ambiguous-boundary
    # guard). `fa` stays single; the set chain still replicates.
    ir = resolve(parse(_LABEL_COLLISION), _AMP, bindings={"sites": ["C3", "Cz"]})
    assert "fa" in ir.derives
    assert "fa@C3" not in ir.derives
    assert {"smr@C3", "smr@Cz"} <= set(ir.derives)


_AMBIGUOUS = """
    protocol "ms_ambig" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "smr" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "smr_t" { signal = "smr"; type = absolute(8 uV) }
      input "frontal" { montage = referential(active: "F3", reference: "linked_ears") }
      derive "mix" { formula = "smr" / "frontal" }
      reward { combine = "all"; event = dwell(condition: above("mix","smr_t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
"""


def test_ambiguous_replication_boundary_rejected():
    # `mix` consumes both a per-site stream ("smr") and a non-replicated one
    # ("frontal"); the replication boundary is ambiguous → ResolveError.
    with pytest.raises(ResolveError, match="ambiguous|mixes"):
        resolve(parse(_AMBIGUOUS), _AMP, bindings={"sites": ["C3", "Cz"]})


_UNKNOWN_GROUP_DEFAULT = """
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = nosuch; allowed = ["C3","Cz","C4"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "smr" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "smr_t" { signal = "smr"; type = absolute(8 uV) }
      reward { combine = "all"; event = dwell(condition: above("smr","smr_t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
"""


def test_unknown_group_as_set_default_rejected():
    # The set `default` names an undeclared group; the fan-out pre-pass (which
    # reads the default before the resolver builds its group table) must raise
    # the same "unknown group" error, not a misleading "no sites to replicate".
    with pytest.raises(ResolveError, match="unknown group 'nosuch'"):
        resolve(parse(_UNKNOWN_GROUP_DEFAULT), _AMP)


# ---------------------------------------------------------------------------
# Band fan-out (Plan 2): bands { } block + band-axis replication
# ---------------------------------------------------------------------------


def test_bands_block_parses_as_section_block():
    src = '''
    protocol "p" {
      meta { version="0.1.0"; evidence="demo"; description="x" }
      requires { sample_rate=">= 256 Hz"; channels=["Cz"] }
      bands { theta = (4 Hz, 8 Hz); alpha = (8 Hz, 12 Hz) }
      input "raw" { montage = referential(active:"Cz", reference:"device") }
      output { audio_gain = 0 }
    }'''
    proto = parse(src).protocol
    blk = next(s for s in proto.body if isinstance(s, A.SectionBlock) and s.keyword == "bands")
    entries = {s.target: s.value for s in blk.body if isinstance(s, A.Assignment)}
    assert set(entries) == {"theta", "alpha"}
    assert isinstance(entries["theta"], A.Tuple)
    assert entries["theta"].elements == (A.NumberLit(4.0, "Hz"), A.NumberLit(8.0, "Hz"))


_BANDS = """
    protocol "bandfan" {
      meta { version="1.0"; evidence="clinical"; description="x" }
      requires { sample_rate=">= 256 Hz"; channels=["Cz"] }
      bands { theta = (4 Hz, 8 Hz); alpha = (8 Hz, 12 Hz); smr = (12 Hz, 15 Hz) }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env"   { from = "raw"; pipeline = [bandpass(band: bands, order: 4), hilbert(), magnitude()] }
      derive "score" { from = "env"; pipeline = [auto_range(window: 30 s)] }
      inhibit "critical_fluctuation" { metric = rectify("score"); threshold = percentile(target_pct: 90, window: 2 min); action = mute(release: 400 ms) }
      reward { continuous = 1.0 }
      output { audio_gain = reward.continuous }
    }
"""


def _walk_json(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_json(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_json(v)


def _bandpass_band_of(ir, derive_name):
    obj = ir_to_json_obj(ir, sample_rate_hz=256.0)
    expr = obj["derives"][derive_name]["expression"]
    bp = next(n for n in _walk_json(expr)
              if n.get("node") == "call" and n.get("callee") == "bandpass")
    band = next(a["value"] for a in bp["args"] if a.get("name") == "band")
    return tuple(e["value"] for e in band["elements"])


def test_band_fan_out_replicates_per_band():
    ir = resolve(parse(_BANDS), _AMP)
    assert set(ir.derives) == {
        "env@theta", "env@alpha", "env@smr",
        "score@theta", "score@alpha", "score@smr",
    }
    assert set(ir.inhibits) == {"critical_fluctuation@theta", "critical_fluctuation@alpha", "critical_fluctuation@smr"}
    # `raw` is shared (upstream of the seed), NOT replicated.
    assert set(ir.inputs) == {"raw"}


def test_band_fan_out_substitutes_band_tuple():
    ir = resolve(parse(_BANDS), _AMP)
    assert _bandpass_band_of(ir, "env@theta") == (4.0, 8.0)
    assert _bandpass_band_of(ir, "env@alpha") == (8.0, 12.0)
    assert _bandpass_band_of(ir, "env@smr") == (12.0, 15.0)


def test_band_fan_out_per_band_wiring():
    ir = resolve(parse(_BANDS), _AMP)
    assert ir.derives["score@theta"].upstream == ("derive/env@theta",)
    assert ir.derives["score@alpha"].upstream == ("derive/env@alpha",)


def test_bands_block_rejects_inverted_range():
    src = _BANDS.replace("theta = (4 Hz, 8 Hz)", "theta = (8 Hz, 4 Hz)")
    with pytest.raises(ResolveError, match="low .* must be < high"):
        resolve(parse(src), _AMP)


def test_bands_block_rejects_non_frequency_tuple():
    src = _BANDS.replace("theta = (4 Hz, 8 Hz)", "theta = (4, 8)")
    with pytest.raises(ResolveError, match="frequency 2-tuple"):
        resolve(parse(src), _AMP)


_BANDS_X_SITES = """
    protocol "xfan" {
      meta { version="1.0"; evidence="clinical"; description="x" }
      requires { sample_rate=">= 256 Hz"; channels=["C3","C4"] }
      bands { theta = (4 Hz, 8 Hz); alpha = (8 Hz, 12 Hz) }
      controls { sites = placement { kind="set"; default=["C3","C4"]; allowed=["C3","C4"]; min=1; max=2 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "env"   { from = "raw"; pipeline = [bandpass(band: bands, order: 4), hilbert(), magnitude()] }
      derive "score" { from = "env"; pipeline = [auto_range(window: 30 s)] }
      inhibit "critical_fluctuation" { metric = rectify("score"); threshold = percentile(target_pct: 90, window: 2 min); action = mute(release: 400 ms) }
      reward { continuous = 1.0 }
      output { audio_gain = reward.continuous }
    }
"""


def test_band_cross_site_full_grid():
    ir = resolve(parse(_BANDS_X_SITES), _AMP, bindings={"sites": ["C3", "C4"]})
    # 2 bands x 2 channels = 4 of each replicated entity.
    assert set(ir.inputs) == {"raw@C3", "raw@C4"}
    assert set(ir.derives) == {
        "env@theta@C3", "env@theta@C4", "env@alpha@C3", "env@alpha@C4",
        "score@theta@C3", "score@theta@C4", "score@alpha@C3", "score@alpha@C4",
    }
    assert set(ir.inhibits) == {
        "critical_fluctuation@theta@C3", "critical_fluctuation@theta@C4",
        "critical_fluctuation@alpha@C3", "critical_fluctuation@alpha@C4",
    }
