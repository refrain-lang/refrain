# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the directed scenario generator."""
from __future__ import annotations

import pytest

from refrain.fuzz.generate import (
    generate_characterization_probe,
    generate_directed_scenarios,
    generate_hold_duration_sweep,
    generate_rank_sweep,
)
from refrain.fuzz.surface import build_surface
from tests.fuzz._smr import resolved_smr_ir


@pytest.fixture(scope="module")
def scenarios():
    surface = build_surface(resolved_smr_ir())
    return list(generate_directed_scenarios(surface))


def test_has_per_leaf_pivotal_scenarios(scenarios):
    tags = {tag for s in scenarios for tag in s.coverage_tags}
    for leaf_id in ("leaf:above:smr_envelope:smr_t",
                    "leaf:below:theta_envelope:theta_t",
                    "leaf:below:high_beta_envelope:hbeta_t"):
        assert f"{leaf_id}:true" in tags, f"missing TRUE scenario for {leaf_id}"
        assert f"{leaf_id}:false" in tags, f"missing FALSE scenario for {leaf_id}"


def test_has_dwell_met_and_missed_scenarios(scenarios):
    tags = {tag for s in scenarios for tag in s.coverage_tags}
    assert "dwell:met" in tags
    assert "dwell:missed" in tags


def test_has_negative_control_scenario(scenarios):
    labels = {s.label for s in scenarios}
    assert any("negative" in lb.lower() or "quiet" in lb.lower() for lb in labels)


def test_has_percentile_warmup_scenario(scenarios):
    tags = {tag for s in scenarios for tag in s.coverage_tags}
    assert "percentile:warmup_then_spike" in tags


def test_scenarios_use_phase_override_for_tractable_runs(scenarios):
    assert all(s.phase_override is not None for s in scenarios)


def test_characterization_probe_covers_all_derive_bands():
    surface = build_surface(resolved_smr_ir())
    probes = list(generate_characterization_probe(surface))
    band_centers = {d.band[0] + (d.band[1] - d.band[0]) / 2 for d in surface.derives}
    swept_freqs = {round(s.segments[0].center_hz, 1) for s in probes if s.segments}
    for center in band_centers:
        assert any(abs(f - center) < (center * 0.1) for f in swept_freqs), \
            f"probe missing a tone near {center} Hz"


def test_rank_sweep_emits_ordered_series_for_each_percentile_threshold():
    surface = build_surface(resolved_smr_ir())
    sweeps = list(generate_rank_sweep(surface))
    thr_names = [t.name for t in surface.thresholds if t.kind == "percentile"]
    for thr_name in thr_names:
        same_thr = [s for s in sweeps if f"metamorphic:rank_sweep:{thr_name}" in s.coverage_tags]
        assert len(same_thr) >= 3, f"need ≥3 sweep scenarios for {thr_name}, got {len(same_thr)}"


def test_hold_duration_sweep_emits_increasing_holds():
    surface = build_surface(resolved_smr_ir())
    sweeps = list(generate_hold_duration_sweep(surface))
    holds = [s.segments[0].duration_s for s in sweeps if s.segments]
    assert sorted(holds) == holds, "hold-duration sweep must be monotonic"
    assert len(holds) >= 3
    tags = {tag for s in sweeps for tag in s.coverage_tags}
    assert "metamorphic:hold_duration_sweep" in tags
