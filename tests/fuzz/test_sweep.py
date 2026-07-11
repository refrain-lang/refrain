# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""sweep.py — direction classification, anchors, geometry, segment construction.

All pure: no engine, no DSP beyond the baked bandpass gain.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.fuzz.scenario import Tone
from refrain.fuzz.surface import (
    METAMORPHIC,
    SAMPLE_EXACT,
    ConditionLeaf,
    DeriveSurface,
    LogicalSurface,
    ThresholdSurface,
    build_surface,
    reward_leaves,
    threshold_for,
)
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

# Quiet-run envelope samples (tests/fuzz/test_engine.py asserts real ones are
# stable across seeds). A constant array makes every percentile equal that
# constant, so the expected anchors below stay readable.
SMR_QUIET_ENVELOPES = {
    "smr_envelope": np.full(4096, 1.125),
    "theta_envelope": np.full(4096, 2.8),
    "high_beta_envelope": np.full(4096, 1.0),
}


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
    assert leaf_anchor_uv(leaf, s, SMR_QUIET_ENVELOPES) == 8.0


def test_anchor_is_the_target_percentile_of_the_quiet_envelope_for_a_percentile_leaf():
    """Cause C: the decision level is the p-th percentile of the quiet
    envelope (literally what PercentileImpl computes), NOT its median. On a
    constant array every percentile equals the constant, so this also proves
    the anchor reads `thr.percentile_target`, not some fixed statistic."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    leaf = next(x for x in reward_leaves(s) if x.signal == "smr_envelope")
    assert leaf_anchor_uv(leaf, s, SMR_QUIET_ENVELOPES) == pytest.approx(1.125)


def test_geometry_keeps_the_spike_a_small_fraction_of_the_percentile_buffer():
    """A spike that fills too much of the rolling window shifts the percentile
    onto itself and the sweep flattens. Constraint: spike/(fill+spike) <= p/2.5
    where p = min(target_pct, 100-target_pct)/100 (= 0.30 for p70/p30)."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    geom = sweep_geometry(s, collar_s=1.0)
    assert geom.usable is True
    assert geom.fill_s == pytest.approx(29.333333, abs=1e-3)
    assert geom.spike_s == pytest.approx(4.0, abs=1e-9)
    assert geom.spike_s / (geom.fill_s + geom.spike_s) <= 0.30 / 2.5 + 1e-9
    # And the fill never waits out the declared 2-minute window.
    assert geom.fill_s < 60.0
    # A usable metric window survives the settle + dwell collar.
    lo, hi = geom.metric_window_s
    assert hi - lo >= 0.5


def _short_window_surface() -> LogicalSurface:
    """A synthetic percentile surface whose declared window (5 s) is short
    enough that the fraction cap shrinks `spike_s` AFTER `metric_start` was
    already fixed by the settle+dwell collar -- the exact geometry that used
    to hand `time_in_reward` an inverted window (metric_end < metric_start).
    No corpus protocol hits this today (shortest declared window is 30 s);
    this reproduces it directly rather than shipping a fixture file."""
    d = DeriveSurface(name="e", band=(12.0, 15.0), sos=[[1, 0, 0, 1, 0, 0]],
                      smooth_tau_ms=250.0, hilbert_group_delay_samples=32, channel="Cz")
    t = ThresholdSurface(name="t", signal="e", kind="percentile",
                        percentile_target=70.0, percentile_window_ms=5000.0)
    return LogicalSurface(
        protocol_name="p", sample_rate_hz=256, required_channels=("Cz",),
        derives=(d,), thresholds=(t,),
        reward_condition=ConditionLeaf(op="above", signal="e", threshold="t"),
        dwell_ms=250.0, phases=(), outputs=(), controls=(), tier=METAMORPHIC,
    )


def test_short_declared_window_yields_an_unusable_but_ordered_metric_window():
    """The reproduction: percentile target 70, declared window 5 s, collar 1 s,
    dwell 250 ms used to yield metric_window_s=(8.25, 7.954...) -- length
    -0.295 s, an inverted window that made `time_in_reward` raise ValueError
    for every group (assertable or not, since unassertable groups are still
    run so their series can be reported)."""
    s = _short_window_surface()
    geom = sweep_geometry(s, collar_s=1.0)
    assert geom.usable is False
    assert geom.reason == "metric window shorter than the settle+dwell collar"
    lo, hi = geom.metric_window_s
    assert hi > lo  # ordered, non-empty -- never inverted
    assert hi - lo > 0.0


def test_short_declared_window_groups_are_all_unassertable():
    s = _short_window_surface()
    groups = plan_sweeps(s, quiet_envelopes={"e": np.full(4096, 1.0)}, collar_s=1.0, seed=42)
    assert groups  # both the rank sweep and the hold sweep are planned
    for g in groups:
        assert g.direction == NONE, g.tag
        assert g.reason is not None, g.tag


def test_geometry_needs_no_fill_without_percentile_thresholds():
    s = _surface("bench/protocols/micro_single_above.refrain")
    assert sweep_geometry(s, collar_s=1.0).fill_s == 0.0


def test_plan_sweeps_emits_a_baseline_plus_an_ascending_ladder_per_derive():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, quiet_envelopes=SMR_QUIET_ENVELOPES, collar_s=1.0, seed=42)
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


def test_assert_monotonic_is_false_for_a_percentile_leaf_true_for_an_absolute_leaf():
    """realistic_smr exercises both paths in one protocol: smr_envelope's
    threshold is percentile (decision level inside the noise -- contrast
    only), high_beta_envelope's is absolute 8 uV (far-field boundary --
    ordering still holds). See sweep.py's HONEST LIMIT."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, quiet_envelopes=SMR_QUIET_ENVELOPES, collar_s=1.0, seed=42)
    by_tag = {g.tag: g for g in groups}
    assert by_tag["rank_sweep:smr_envelope"].assert_monotonic is False
    assert by_tag["rank_sweep:high_beta_envelope"].assert_monotonic is True


def test_ladder_amplitudes_ascend_and_are_gain_compensated():
    s = _surface("bench/protocols/micro_single_above.refrain")
    quiet_envelopes = {d.name: np.full(4096, 2.0) for d in s.derives}
    g = next(g for g in plan_sweeps(s, quiet_envelopes=quiet_envelopes, collar_s=1.0, seed=42)
             if g.tag.startswith("rank_sweep:"))
    amps = []
    for m in g.members[1:]:
        tone = next(seg.content for seg in m.scenario.segments
                    if isinstance(seg.content, Tone))
        amps.append(tone.amplitude_uv)
    assert amps == sorted(amps)
    assert amps[-1] / amps[0] == pytest.approx(8.0, rel=1e-6)  # 8.0x / 1.0x


def test_percentile_below_leaves_are_primed_high_during_the_fill():
    """Quiet is NOT favourable for below(x, percentile(x)): the threshold tracks
    its own signal, so it holds ~p% of the time however quiet x is. Priming it
    high during the fill raises its rolling percentile so the quiet spike sits
    clearly below it."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, quiet_envelopes=SMR_QUIET_ENVELOPES, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:smr_envelope")
    geom = sweep_geometry(s, collar_s=1.0)
    theta = next(d for d in s.derives if d.name == "theta_envelope")
    for m in g.members:
        primes = [seg for seg in m.scenario.segments
                  if seg.band == theta.band and seg.start_s == 0.0]
        assert len(primes) == 1, "theta (below+percentile) must be primed in the fill"
        assert primes[0].end_s == pytest.approx(geom.fill_s)


def test_the_swept_below_percentile_derive_is_now_also_primed():
    """Change 2 (task 8c): an earlier version excluded the swept derive from
    priming, on the theory that priming it would flatten its own sweep. That
    rationale applied only while monotonicity was asserted across the swept
    leaf's own boundary. Now that a below+percentile leaf is contrast-only
    (assert_monotonic=False), the swept derive is primed like every other
    below+percentile leaf -- without it the no-drive baseline is a dwell
    lottery (measured 0.000/0.014/0.248 across seeds) and the sweep is
    vacuous."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, quiet_envelopes=SMR_QUIET_ENVELOPES, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:theta_envelope")
    geom = sweep_geometry(s, collar_s=1.0)
    theta = next(d for d in s.derives if d.name == "theta_envelope")
    for m in g.members:
        primes = [seg for seg in m.scenario.segments
                  if seg.band == theta.band and seg.start_s == 0.0]
        assert len(primes) == 1, "the swept below+percentile leaf must be primed too"
        assert primes[0].end_s == pytest.approx(geom.fill_s)


def test_absolute_below_leaves_are_left_quiet():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, quiet_envelopes=SMR_QUIET_ENVELOPES, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:smr_envelope")
    hbeta = next(d for d in s.derives if d.name == "high_beta_envelope")
    for m in g.members:
        assert not [seg for seg in m.scenario.segments if seg.band == hbeta.band]


def test_hold_sweep_group_is_planned_but_unassertable_on_the_metamorphic_tier():
    """Cause D: on a noise-dominated (METAMORPHIC) protocol the quiet state
    already holds reward a good fraction of the time, so the hold sweep's
    series is still built and run (recorded, never silenced) but not
    asserted."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    assert s.tier == METAMORPHIC
    groups = plan_sweeps(s, quiet_envelopes=SMR_QUIET_ENVELOPES, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "hold_duration_sweep")
    assert g.direction == NONE
    assert g.reason is not None
    assert [m.index for m in g.members] == [-1, 0, 1, 2, 3, 4]
    assert len({m.scenario.duration_s for m in g.members}) == 1
    # assert_monotonic is unconditionally True on the hold group -- direction
    # NONE means nothing is asserted here regardless, but the field itself
    # does not encode "this tier suppresses it," only "the boundary is not a
    # percentile of the noise" (see sweep.py's HONEST LIMIT).
    assert g.assert_monotonic is True


def test_hold_sweep_group_still_asserts_up_on_the_sample_exact_tier():
    s = _surface("bench/protocols/micro_single_above.refrain")
    assert s.tier == SAMPLE_EXACT
    quiet_envelopes = {d.name: np.full(4096, 2.0) for d in s.derives}
    groups = plan_sweeps(s, quiet_envelopes=quiet_envelopes, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "hold_duration_sweep")
    assert g.direction == UP
    assert g.reason is None
    assert [m.index for m in g.members] == [-1, 0, 1, 2, 3, 4]
    assert g.assert_monotonic is True
