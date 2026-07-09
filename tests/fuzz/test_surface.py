"""Tests for the LogicalSurface extraction from a resolved Refrain IR."""
from __future__ import annotations

from pathlib import Path

import pytest

import refrain
from refrain.fuzz.surface import LogicalSurface, build_surface
from refrain.parser import parse_file
from refrain.resolver import resolve
from tests.fuzz._smr import resolved_smr_ir

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ir(rel: str):
    return resolve(parse_file(REPO_ROOT / rel), None)


@pytest.fixture(scope="module")
def smr_surface() -> LogicalSurface:
    return build_surface(resolved_smr_ir())


def test_surface_extracts_three_envelope_derives(smr_surface):
    names = {d.name for d in smr_surface.derives}
    assert names == {"smr_envelope", "theta_envelope", "high_beta_envelope"}


def test_surface_carries_baked_sos_per_derive(smr_surface):
    by_name = {d.name: d for d in smr_surface.derives}
    smr = by_name["smr_envelope"]
    assert smr.sos is not None
    assert len(smr.sos) >= 1
    assert all(len(s) == 6 for s in smr.sos)
    assert smr.band == pytest.approx((12.0, 15.0))


def test_surface_smoothing_tau_ms_present(smr_surface):
    by_name = {d.name: d for d in smr_surface.derives}
    assert by_name["smr_envelope"].smooth_tau_ms == pytest.approx(250.0)


def test_surface_three_thresholds_correct_kinds(smr_surface):
    by_name = {t.name: t for t in smr_surface.thresholds}
    assert set(by_name) == {"smr_t", "theta_t", "hbeta_t"}
    assert by_name["hbeta_t"].kind == "absolute"
    assert by_name["hbeta_t"].absolute_uv == pytest.approx(8.0)
    assert by_name["smr_t"].kind == "percentile"
    assert by_name["smr_t"].percentile_window_ms == pytest.approx(120_000.0)
    assert by_name["smr_t"].percentile_target == pytest.approx(70.0)  # default control


def test_surface_condition_tree_is_all_of_three_leaves(smr_surface):
    cond = smr_surface.reward_condition
    assert cond.op == "all_of"
    assert len(cond.children) == 3
    leaves = {(c.op, c.signal, c.threshold) for c in cond.children}
    assert leaves == {
        ("above", "smr_envelope",        "smr_t"),
        ("below", "theta_envelope",      "theta_t"),
        ("below", "high_beta_envelope",  "hbeta_t"),
    }


def test_surface_dwell_is_250ms(smr_surface):
    assert smr_surface.dwell_ms == pytest.approx(250.0)


def test_surface_phases_include_warmup_muted(smr_surface):
    phases = smr_surface.phases
    assert [p.name for p in phases] == ["warmup", "training", "cooldown"]
    assert phases[0].output_muted is True
    assert phases[1].output_muted is False
    assert phases[2].output_muted is True


def test_surface_sample_rate_resolved(smr_surface):
    assert smr_surface.sample_rate_hz > 0
    assert smr_surface.sample_rate_hz == 256


def test_surface_lists_relevant_channels(smr_surface):
    assert "Cz" in smr_surface.required_channels


def test_center_bandwidth_band_from_args():
    from refrain.parser import parse_file
    from refrain.resolver import resolve
    from refrain.fuzz.surface import _band_from_call, _arg
    from refrain.ir import IRCall
    ir = resolve(parse_file(REPO_ROOT / "bench/protocols/micro_center_bandwidth.refrain"), None)
    # find the bandpass call in the cb_env derive
    call = None
    def walk(o):
        nonlocal call
        if isinstance(o, IRCall) and o.callee == "bandpass":
            call = o
        for a in getattr(o, "args", []):
            walk(a.value)
        for attr in ("expression", "value"):
            v = getattr(o, attr, None)
            if v is not None and not isinstance(v, (str, int, float)):
                walk(v)
    walk(ir.derives["cb_env"])
    lo, hi = _band_from_call(call, ir)
    assert abs(lo - 12.15) < 0.1 and abs(hi - 15.0) < 0.1


_MODE_CONTROL_PROTOCOL = '''
protocol "mode_test" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
  reward { event = dwell(condition: all_of([above("env", "env_t")]), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %" }
    threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive"; label = "Threshold style" }
  }
  session {
    phases = [ phase { name = "training"; duration = 30 min } ]
  }
}
'''


def test_surface_excludes_mode_control_from_numeric_controls():
    # A `mode` control (an enum-like choice, not a tunable number) must not
    # leak into the numeric ControlSurface list — it has no meaningful
    # default/min/max and would otherwise surface as a bogus 0.0-valued
    # control. Only the plain numeric control should appear.
    ir = resolve(refrain.parse(_MODE_CONTROL_PROTOCOL), amp=None)
    surface = build_surface(ir)
    names = {c.name for c in surface.controls}
    assert names == {"reward_pct"}
    assert "threshold_style" not in names


from refrain.fuzz.surface import (
    METAMORPHIC,
    SAMPLE_EXACT,
    derive_for,
    reward_leaves,
    threshold_for,
)


def test_absolute_only_reward_is_sample_exact_tier():
    surface = build_surface(_ir("bench/protocols/micro_single_above.refrain"))
    assert surface.tier == SAMPLE_EXACT


def test_any_percentile_leaf_routes_to_the_metamorphic_tier():
    # A percentile threshold makes the reward noise-dominated: the oracle can
    # only mark those regions DON'T-CARE, which is exactly the hollow pass.
    surface = build_surface(_ir("bench/protocols/realistic_smr.refrain"))
    assert surface.tier == METAMORPHIC


def test_reward_leaves_flattens_the_condition_tree_in_order():
    surface = build_surface(_ir("bench/protocols/realistic_smr.refrain"))
    assert [(leaf.op, leaf.signal) for leaf in reward_leaves(surface)] == [
        ("above", "smr_envelope"),
        ("below", "theta_envelope"),
        ("below", "high_beta_envelope"),
    ]


def test_derive_for_and_threshold_for_look_up_by_name():
    surface = build_surface(_ir("bench/protocols/realistic_smr.refrain"))
    assert derive_for(surface, "smr_envelope").band == (12.0, 15.0)
    assert threshold_for(surface, "hbeta_t").absolute_uv == 8.0
    with pytest.raises(KeyError):
        derive_for(surface, "nope")
