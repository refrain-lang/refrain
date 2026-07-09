# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""sweep.py — direction classification, anchors, geometry, segment construction.

All pure: no engine, no DSP beyond the baked bandpass gain.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from refrain.fuzz.scenario import Tone
from refrain.fuzz.surface import build_surface, reward_leaves, threshold_for
from refrain.fuzz.sweep import (
    DOWN,
    NONE,
    UP,
    leaf_anchor_uv,
    plan_sweeps,
    sweep_direction,
    sweep_geometry,
)
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]

# Measured quiet-run medians (tests/fuzz/test_engine.py asserts these are stable).
SMR_FLOORS = {"smr_envelope": 1.1, "theta_envelope": 2.8, "high_beta_envelope": 1.0}


def _surface(rel: str):
    return build_surface(resolve(parse_file(REPO_ROOT / rel), None))


def test_above_leaf_sweeps_non_decreasing():
    s = _surface("bench/protocols/realistic_smr.refrain")
    assert sweep_direction(s, "smr_envelope") == (UP, None)


def test_below_leaf_sweeps_non_increasing():
    """The pre-existing bug: check_metamorphic_monotonic asserted non-DECREASING
    for every swept threshold, false-failing every inhibit/below leaf."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    assert sweep_direction(s, "theta_envelope") == (DOWN, None)
    assert sweep_direction(s, "high_beta_envelope") == (DOWN, None)


def test_derive_not_feeding_the_reward_asserts_nothing():
    s = _surface("bench/protocols/realistic_smr.refrain")
    direction, reason = sweep_direction(s, "nonexistent_envelope")
    assert direction == NONE
    assert "does not feed" in reason


def test_anchor_is_the_absolute_threshold_for_an_absolute_leaf():
    s = _surface("bench/protocols/realistic_smr.refrain")
    leaf = next(x for x in reward_leaves(s) if x.signal == "high_beta_envelope")
    assert threshold_for(s, leaf.threshold).kind == "absolute"
    assert leaf_anchor_uv(leaf, s, SMR_FLOORS) == 8.0


def test_anchor_is_the_measured_noise_floor_for_a_percentile_leaf():
    s = _surface("bench/protocols/realistic_smr.refrain")
    leaf = next(x for x in reward_leaves(s) if x.signal == "smr_envelope")
    assert leaf_anchor_uv(leaf, s, SMR_FLOORS) == pytest.approx(1.1)


def test_geometry_keeps_the_spike_a_small_fraction_of_the_percentile_buffer():
    """A spike that fills too much of the rolling window shifts the percentile
    onto itself and the sweep flattens. Constraint: spike/(fill+spike) <= p/2.5
    where p = min(target_pct, 100-target_pct)/100 (= 0.30 for p70/p30)."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    geom = sweep_geometry(s, collar_s=1.0)
    assert geom.spike_s / (geom.fill_s + geom.spike_s) <= 0.30 / 2.5 + 1e-9
    # And the fill never waits out the declared 2-minute window.
    assert geom.fill_s < 60.0
    # A usable metric window survives the settle + dwell collar.
    lo, hi = geom.metric_window_s
    assert hi - lo >= 0.5


def test_geometry_needs_no_fill_without_percentile_thresholds():
    s = _surface("bench/protocols/micro_single_above.refrain")
    assert sweep_geometry(s, collar_s=1.0).fill_s == 0.0


def test_plan_sweeps_emits_a_baseline_plus_an_ascending_ladder_per_derive():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    rank = {g.tag: g for g in groups if g.tag.startswith("rank_sweep:")}
    assert set(rank) == {
        "rank_sweep:smr_envelope",
        "rank_sweep:theta_envelope",
        "rank_sweep:high_beta_envelope",
    }
    g = rank["rank_sweep:smr_envelope"]
    assert g.direction == UP
    assert [m.index for m in g.members] == [-1, 0, 1, 2, 3]
    # All members share one duration => one noise realization, byte-identical.
    assert len({m.scenario.duration_s for m in g.members}) == 1
    assert len({m.scenario.seed for m in g.members}) == 1
    # The baseline drives nothing on the swept derive.
    base = g.members[0].scenario
    assert not any(seg.band == (12.0, 15.0) and seg.start_s >= g.metric_window_s[0] - 5
                   for seg in base.segments)


def test_ladder_amplitudes_ascend_and_are_gain_compensated():
    s = _surface("bench/protocols/micro_single_above.refrain")
    floors = {d.name: 2.0 for d in s.derives}
    g = next(g for g in plan_sweeps(s, noise_floor=floors, collar_s=1.0, seed=42)
             if g.tag.startswith("rank_sweep:"))
    amps = []
    for m in g.members[1:]:
        tone = next(seg.content for seg in m.scenario.segments
                    if isinstance(seg.content, Tone))
        amps.append(tone.amplitude_uv)
    assert amps == sorted(amps)
    assert amps[-1] / amps[0] == pytest.approx(8.0, rel=1e-6)  # 4.0x / 0.5x


def test_percentile_below_leaves_are_primed_high_during_the_fill():
    """Quiet is NOT favourable for below(x, percentile(x)): the threshold tracks
    its own signal, so it holds ~p% of the time however quiet x is. Priming it
    high during the fill raises its rolling percentile so the quiet spike sits
    clearly below it."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:smr_envelope")
    geom = sweep_geometry(s, collar_s=1.0)
    theta = next(d for d in s.derives if d.name == "theta_envelope")
    for m in g.members:
        primes = [seg for seg in m.scenario.segments
                  if seg.band == theta.band and seg.start_s == 0.0]
        assert len(primes) == 1, "theta (below+percentile) must be primed in the fill"
        assert primes[0].end_s == pytest.approx(geom.fill_s)


def test_the_swept_derive_is_never_primed():
    """Priming the swept derive would raise its own percentile and flatten the sweep."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:theta_envelope")
    theta = next(d for d in s.derives if d.name == "theta_envelope")
    for m in g.members:
        assert not [seg for seg in m.scenario.segments
                    if seg.band == theta.band and seg.start_s == 0.0]


def test_absolute_below_leaves_are_left_quiet():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:smr_envelope")
    hbeta = next(d for d in s.derives if d.name == "high_beta_envelope")
    for m in g.members:
        assert not [seg for seg in m.scenario.segments if seg.band == hbeta.band]


def test_hold_sweep_group_is_planned_with_ascending_holds():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "hold_duration_sweep")
    assert g.direction == UP
    assert [m.index for m in g.members] == [-1, 0, 1, 2, 3, 4]
    assert len({m.scenario.duration_s for m in g.members}) == 1
