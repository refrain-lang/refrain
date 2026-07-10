# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the balanced two-section fuzz report."""
from __future__ import annotations

from refrain.fuzz.check import PerScenarioResult
from refrain.fuzz.metamorphic import MetamorphicViolation, SweepOutcome
from refrain.fuzz.report import render_report
from refrain.fuzz.scenario import Verdict


def _r(label, verdict, tags, n_events=0, dont_care=0):
    return PerScenarioResult(
        label=label, verdict=verdict, n_events=n_events,
        n_crisp_assertions=1, n_dont_care_intervals=dont_care,
        coverage_tags=frozenset(tags),
    )


def test_report_has_both_sections():
    results = [
        _r("dwell_met", Verdict.PASS, {"dwell:met"}, n_events=1),
        _r("dwell_missed", Verdict.PASS, {"dwell:missed"}),
    ]
    text = render_report(
        protocol_name="smr_cz",
        tier="sample_exact",
        results=results,
        sweep_outcomes=[],
        metamorphic_violations=[],
        all_coverage_tags={"dwell:met", "dwell:missed"},
    )
    assert "What your protocol does" in text
    assert "Engine check" in text
    assert "smr_cz" in text


def test_report_flags_engine_violations():
    results = [
        _r("dwell_met", Verdict.MISSED, {"dwell:met"}),
        _r("hbeta_artifact", Verdict.SPURIOUS, {"leaf:below:high_beta_envelope:hbeta_t:false"},
           n_events=2),
    ]
    text = render_report(
        protocol_name="smr_cz", tier="sample_exact", results=results,
        sweep_outcomes=[],
        metamorphic_violations=[],
        all_coverage_tags={"dwell:met", "leaf:below:high_beta_envelope:hbeta_t:false"},
    )
    assert "VIOLATION" in text or "FAIL" in text
    assert "dwell_met" in text and "missed" in text.lower()
    assert "hbeta_artifact" in text and "spurious" in text.lower()


def test_report_lists_unreachable_branches():
    results = [
        _r("dwell_met", Verdict.PASS, {"dwell:met"}, n_events=1),
    ]
    text = render_report(
        protocol_name="smr_cz", tier="sample_exact", results=results,
        sweep_outcomes=[],
        metamorphic_violations=[],
        all_coverage_tags={"dwell:met", "dwell:missed"},
    )
    assert "unreachable" in text.lower() or "uncovered" in text.lower()
    assert "dwell:missed" in text


def test_report_includes_dont_care_breakdown_by_reason():
    results = [
        _r("scen_a", Verdict.PASS, {"x"}, dont_care=3),
        _r("scen_b", Verdict.PASS, {"y"}, dont_care=1),
    ]
    text = render_report(
        protocol_name="smr_cz", tier="sample_exact", results=results,
        sweep_outcomes=[],
        metamorphic_violations=[],
        all_coverage_tags={"x", "y"},
    )
    assert "don't-care intervals: 4" in text  # 3 + 1


def test_report_lists_metamorphic_violations():
    results = [_r("amp_5", Verdict.PASS, {"metamorphic:rank_sweep:smr_t"})]
    violations = [MetamorphicViolation(
        tag="rank_sweep:smr_t", kind="monotonicity", direction="up",
        baseline=0.1, series=(("amp_5", 0.5), ("amp_15", 0.2)),
        detail="time-in-reward must be non-decreasing in drive",
    )]
    text = render_report(
        protocol_name="smr_cz", tier="metamorphic", results=results,
        sweep_outcomes=[],
        metamorphic_violations=violations,
        all_coverage_tags={"metamorphic:rank_sweep:smr_t"},
    )
    assert "metamorphic" in text.lower()
    assert "smr_t" in text


def test_report_states_when_monotonicity_was_not_asserted():
    """Task 8c / Change 3: a percentile-boundary group must never silently
    drop the monotonicity assertion -- the report has to say why."""
    results = [_r("amp_5", Verdict.PASS, {"metamorphic:rank_sweep:smr_t"})]
    outcomes = [SweepOutcome(
        tag="rank_sweep:smr_t", direction="up", baseline=0.872,
        series=(("amp_5", 0.647), ("amp_15", 1.0)),
        assertable=True, reason=None, monotonic_asserted=False,
    )]
    text = render_report(
        protocol_name="smr_cz", tier="metamorphic", results=results,
        sweep_outcomes=outcomes,
        metamorphic_violations=[],
        all_coverage_tags={"metamorphic:rank_sweep:smr_t"},
    )
    assert "monotonicity not asserted" in text.lower()
    assert "percentile" in text.lower()


def test_report_stays_silent_on_the_monotonicity_note_when_it_was_asserted():
    results = [_r("amp_5", Verdict.PASS, {"metamorphic:rank_sweep:hbeta_t"})]
    outcomes = [SweepOutcome(
        tag="rank_sweep:hbeta_t", direction="down", baseline=1.0,
        series=(("amp_5", 0.1), ("amp_15", 0.0)),
        assertable=True, reason=None, monotonic_asserted=True,
    )]
    text = render_report(
        protocol_name="smr_cz", tier="metamorphic", results=results,
        sweep_outcomes=outcomes,
        metamorphic_violations=[],
        all_coverage_tags={"metamorphic:rank_sweep:hbeta_t"},
    )
    assert "monotonicity not asserted" not in text.lower()
