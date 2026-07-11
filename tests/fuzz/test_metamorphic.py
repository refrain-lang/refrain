# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""metamorphic.py — direction-aware monotonicity + contrast, no tolerance knob."""
from __future__ import annotations

from refrain.fuzz.metamorphic import check_metamorphic
from refrain.fuzz.scenario import PhaseOverride, Scenario
from refrain.fuzz.sweep import DOWN, NONE, UP, SweepGroup, SweepMember


def _group(tag, direction, n_rungs=4, reason=None, assert_monotonic=True) -> SweepGroup:
    def sc(label):
        return Scenario(label=label, duration_s=10.0, sample_rate_hz=256, segments=(),
                        controls={}, coverage_tags=frozenset(),
                        phase_override=PhaseOverride(1.0, 8.5, 0.5), seed=42)

    members = [SweepMember(scenario=sc(f"{tag}:baseline"), index=-1)]
    members += [SweepMember(scenario=sc(f"{tag}:rung_{i}"), index=i)
                for i in range(n_rungs)]
    return SweepGroup(tag=tag, direction=direction, reason=reason,
                      members=tuple(members), metric_window_s=(2.0, 8.0),
                      assert_monotonic=assert_monotonic)


def _metrics(tag, baseline, series) -> dict[str, float]:
    m = {f"{tag}:baseline": baseline}
    m.update({f"{tag}:rung_{i}": v for i, v in enumerate(series)})
    return m


def test_above_leaf_non_decreasing_series_passes():
    # Real measured series (micro_single_pct, seed 42): baseline 0.10.
    g = _group("rank_sweep:up_env", UP)
    v, out = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.1022,
                                             [0.2987, 0.4609, 1.0, 1.0]))
    assert v == []
    assert out[0].assertable is True


def test_below_leaf_non_increasing_series_is_not_a_violation():
    """THE pre-existing bug. The merged check asserted non-DECREASING for every
    sweep, so this real micro_single_below series 'violated' on every seed."""
    g = _group("rank_sweep:down_env", DOWN)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:down_env", 1.0,
                                           [1.0, 0.2764, 0.0, 0.0]))
    assert v == []


def test_below_leaf_increasing_series_is_a_violation():
    g = _group("rank_sweep:down_env", DOWN)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:down_env", 1.0,
                                           [1.0, 0.2, 0.9, 0.0]))
    assert [x.kind for x in v] == ["monotonicity"]


def test_above_leaf_decreasing_series_is_a_violation():
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.1,
                                           [0.3, 0.2, 0.8, 1.0]))
    assert [x.kind for x in v] == ["monotonicity"]


def test_a_flat_sweep_fails_loud_as_vacuous():
    """[0,0,0,0] and [k,k,k,k] prove nothing. They must FAIL, not pass."""
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.0,
                                           [0.0, 0.0, 0.0, 0.0]))
    assert [x.kind for x in v] == ["no_contrast"]

    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.4,
                                           [0.4, 0.4, 0.4, 0.4]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_insufficient_contrast_fails_even_when_monotone():
    # Monotone, but the top rung closes < half the gap from baseline to 1.0.
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.0,
                                           [0.0, 0.1, 0.2, 0.25]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_a_baseline_already_saturated_is_no_contrast_not_a_pass():
    """base=1.0 (up) would satisfy `last - base >= 0.5*(1-base)` as 0 >= 0.
    That is a reward firing on pure noise, not a passing sweep."""
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 1.0,
                                           [1.0, 1.0, 1.0, 1.0]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_a_baseline_already_silent_is_no_contrast_for_a_down_sweep():
    g = _group("rank_sweep:down_env", DOWN)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:down_env", 0.0,
                                           [0.0, 0.0, 0.0, 0.0]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_a_mixed_sweep_asserts_nothing_and_is_reported_not_passed():
    g = _group("rank_sweep:both", NONE, reason="derive feeds both above() and below() leaves")
    v, out = check_metamorphic([g], _metrics("rank_sweep:both", 0.5, [0.9, 0.1, 0.7, 0.2]))
    assert v == []
    assert out[0].assertable is False
    assert "both above()" in out[0].reason


def test_percentile_boundary_non_monotone_series_passes_on_contrast_alone():
    """Task 8c / Change 1: real seed-44 series from peak_alpha_up_pz
    (above/percentile(70)) -- baseline 0.872, rungs dip to 0.322 before
    saturating. This is genuinely non-monotone (0.647 -> 0.322 is a fall) but
    the leaf's decision level is a percentile of the noise (HONEST LIMIT), so
    assert_monotonic=False and only contrast is checked. Contrast passes:
    1.000 - 0.872 = 0.128 >= 0.5*(1-0.872) = 0.064."""
    g = _group("rank_sweep:peak_alpha_up_pz", UP, assert_monotonic=False)
    v, out = check_metamorphic([g], _metrics("rank_sweep:peak_alpha_up_pz", 0.872,
                                             [0.647, 0.322, 1.000, 1.000]))
    assert v == []
    assert out[0].assertable is True
    assert out[0].monotonic_asserted is False


def test_absolute_boundary_non_monotone_series_still_violates_monotonicity():
    """The mirror case: the identical non-monotone series on an absolute-leaf
    group (assert_monotonic=True, e.g. high_beta_envelope / hbeta_t) must
    still be caught -- the far-field boundary keeps the ordering assertion."""
    g = _group("rank_sweep:high_beta_envelope", UP, assert_monotonic=True)
    v, out = check_metamorphic([g], _metrics("rank_sweep:high_beta_envelope", 0.872,
                                             [0.647, 0.322, 1.000, 1.000]))
    assert [x.kind for x in v] == ["monotonicity"]
    assert out[0].assertable is True
    assert out[0].monotonic_asserted is True


def test_percentile_boundary_still_fails_no_contrast_when_flat():
    """assert_monotonic=False does not mean "nothing is asserted" -- contrast
    still runs and still fails loud on a degenerate baseline."""
    g = _group("rank_sweep:up_env", UP, assert_monotonic=False)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 1.0,
                                           [1.0, 1.0, 1.0, 1.0]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_a_missing_metric_is_an_error_not_a_silent_skip():
    g = _group("rank_sweep:up_env", UP)
    try:
        check_metamorphic([g], {"rank_sweep:up_env:baseline": 0.1})
    except KeyError as exc:
        assert "rung_0" in str(exc)
    else:
        raise AssertionError("a missing sweep metric must raise, never pass silently")
