# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the checker (alignment + coverage + vacuity guard)."""
from __future__ import annotations

import pytest

from refrain.fuzz.check import (
    ActualEvent,
    PerScenarioResult,
    VacuityError,
    check_metamorphic_monotonic,
    check_scenario,
)
from refrain.fuzz.oracle import (
    DontCareInterval,
    ExpectedTimeline,
)
from refrain.fuzz.scenario import DontCareReason, Verdict


def test_pass_when_event_inside_should_fire_window():
    fs = 256
    expected = ExpectedTimeline(should_fire_event_samples=[500])
    actual = [ActualEvent(sample=510, kind="event", channel="audio_chime")]
    res = check_scenario(
        scenario_label="t", expected=expected, actual=actual,
        fs=fs, collar_samples=64,
        coverage_tags=frozenset({"leaf:above:smr_envelope:smr_t:true"}),
    )
    assert res.verdict is Verdict.PASS
    assert res.n_crisp_assertions >= 1


def test_missed_when_should_fire_has_no_event():
    fs = 256
    expected = ExpectedTimeline(should_fire_event_samples=[500])
    res = check_scenario(
        scenario_label="t", expected=expected, actual=[],
        fs=fs, collar_samples=64,
        coverage_tags=frozenset({"leaf:above:smr_envelope:smr_t:true"}),
    )
    assert res.verdict is Verdict.MISSED


def test_spurious_when_event_in_should_not_fire():
    fs = 256
    expected = ExpectedTimeline(should_fire_event_samples=[])
    actual = [ActualEvent(sample=500, kind="event", channel="audio_chime")]
    res = check_scenario(
        scenario_label="t", expected=expected, actual=actual,
        fs=fs, collar_samples=64,
        coverage_tags=frozenset({"dwell:missed"}),
    )
    assert res.verdict is Verdict.SPURIOUS


def test_event_in_dont_care_interval_does_not_violate():
    fs = 256
    expected = ExpectedTimeline(
        should_fire_event_samples=[],
        dont_care_intervals=[DontCareInterval(400, 600, DontCareReason.PHASE_MUTED)],
    )
    actual = [ActualEvent(sample=500, kind="event", channel="audio_chime")]
    res = check_scenario(
        scenario_label="t", expected=expected, actual=actual,
        fs=fs, collar_samples=64,
        coverage_tags=frozenset({"some_tag"}),
    )
    assert res.verdict is not Verdict.SPURIOUS


def test_vacuity_raises_when_no_crisp_assertions():
    fs = 256
    expected = ExpectedTimeline(
        should_fire_event_samples=[],
        dont_care_intervals=[DontCareInterval(0, 1024, DontCareReason.PRE_WINDOW_FILL)],
    )
    with pytest.raises(VacuityError):
        check_scenario(
            scenario_label="vacuous", expected=expected, actual=[],
            fs=fs, collar_samples=64,
            coverage_tags=frozenset(),
        )


def test_metamorphic_monotonic_passes_for_non_decreasing_fire_counts():
    results = [
        PerScenarioResult(label="amp_5",  verdict=Verdict.PASS, n_events=0,
                          n_crisp_assertions=1, n_dont_care_intervals=0,
                          coverage_tags=frozenset({"metamorphic:rank_sweep:smr_t",
                                                    "rank_sweep:amp_5"})),
        PerScenarioResult(label="amp_15", verdict=Verdict.PASS, n_events=2,
                          n_crisp_assertions=1, n_dont_care_intervals=0,
                          coverage_tags=frozenset({"metamorphic:rank_sweep:smr_t",
                                                    "rank_sweep:amp_15"})),
        PerScenarioResult(label="amp_25", verdict=Verdict.PASS, n_events=5,
                          n_crisp_assertions=1, n_dont_care_intervals=0,
                          coverage_tags=frozenset({"metamorphic:rank_sweep:smr_t",
                                                    "rank_sweep:amp_25"})),
    ]
    violations = check_metamorphic_monotonic(results, tag_prefix="metamorphic:rank_sweep:")
    assert violations == []


def test_metamorphic_monotonic_violates_when_fire_count_drops():
    results = [
        PerScenarioResult(label="amp_5",  verdict=Verdict.PASS, n_events=5,
                          n_crisp_assertions=1, n_dont_care_intervals=0,
                          coverage_tags=frozenset({"metamorphic:rank_sweep:smr_t",
                                                    "rank_sweep:amp_5"})),
        PerScenarioResult(label="amp_15", verdict=Verdict.PASS, n_events=2,
                          n_crisp_assertions=1, n_dont_care_intervals=0,
                          coverage_tags=frozenset({"metamorphic:rank_sweep:smr_t",
                                                    "rank_sweep:amp_15"})),
    ]
    violations = check_metamorphic_monotonic(results, tag_prefix="metamorphic:rank_sweep:")
    assert len(violations) == 1


def _hold(frac, n_events):
    # Mirrors generate.py's hold-sweep label format: f"hold_sweep:{f:g}x_dwell".
    label = f"hold_sweep:{frac:g}x_dwell"
    return PerScenarioResult(
        label=label, verdict=Verdict.PASS, n_events=n_events,
        n_crisp_assertions=1, n_dont_care_intervals=0,
        coverage_tags=frozenset({"metamorphic:hold_duration_sweep", label}),
    )


def test_metamorphic_monotonic_orders_fractional_hold_labels_by_magnitude():
    # Fractional dwell-multiples; n_events non-decreasing in TRUE magnitude
    # order (0.5 < 0.9 < 1.5 < 2.5 < 5). A naive trailing-integer sort would
    # read "0.5x_dwell" as 5 and scramble the order into a false violation.
    results = [_hold(0.5, 0), _hold(0.9, 1), _hold(1.5, 2), _hold(2.5, 3), _hold(5.0, 4)]
    violations = check_metamorphic_monotonic(
        results, tag_prefix="metamorphic:hold_duration_sweep"
    )
    assert violations == []


def test_metamorphic_monotonic_flags_fractional_hold_drop():
    # A genuine drop in magnitude order (0.5→5 events, then 0.9→2) must flag.
    results = [_hold(0.5, 5), _hold(0.9, 2), _hold(1.5, 3)]
    violations = check_metamorphic_monotonic(
        results, tag_prefix="metamorphic:hold_duration_sweep"
    )
    assert len(violations) == 1
