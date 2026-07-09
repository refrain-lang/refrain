# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the checker (alignment + coverage + vacuity guard)."""
from __future__ import annotations

import pytest

from refrain.fuzz.check import (
    ActualEvent,
    VacuityError,
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
