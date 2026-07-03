# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the directed scenario generator."""
from __future__ import annotations

from pathlib import Path

import pytest

from refrain.fuzz.generate import (
    generate_characterization_probe,
    generate_directed_scenarios,
    generate_hold_duration_sweep,
    generate_rank_sweep,
)
from refrain.fuzz.surface import build_surface
from tests.fuzz._smr import resolved_smr_ir

REPO_ROOT = Path(__file__).resolve().parents[2]

# Snapshot of scenario labels for realistic_smr (all_of protocol).
# Captured before the Task 3 gates were added to prove both gates are no-ops
# for all_of protocols (they have percentile thresholds + a ConditionNode reward).
EXPECTED_REALISTIC_SMR_LABELS = [
    'dwell_met',
    'dwell_missed',
    'hold_sweep:0.5x_dwell',
    'hold_sweep:0.9x_dwell',
    'hold_sweep:1.5x_dwell',
    'hold_sweep:2.5x_dwell',
    'hold_sweep:5x_dwell',
    'leaf:above:smr_envelope:smr_t:false',
    'leaf:above:smr_envelope:smr_t:true',
    'leaf:below:high_beta_envelope:hbeta_t:false',
    'leaf:below:high_beta_envelope:hbeta_t:true',
    'leaf:below:theta_envelope:theta_t:false',
    'leaf:below:theta_envelope:theta_t:true',
    'negative_control_quiet',
    'percentile_warmup_then_spike',
    'probe:tone_13.5hz',
    'probe:tone_26.0hz',
    'probe:tone_6.0hz',
    'rank_sweep:smr_t:amp_15',
    'rank_sweep:smr_t:amp_25',
    'rank_sweep:smr_t:amp_40',
    'rank_sweep:smr_t:amp_5',
    'rank_sweep:theta_t:amp_15',
    'rank_sweep:theta_t:amp_25',
    'rank_sweep:theta_t:amp_40',
    'rank_sweep:theta_t:amp_5',
]


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


def test_all_of_corpus_unchanged_after_gating():
    from refrain.fuzz.runner import _build_corpus
    from refrain.parser import parse_file
    from refrain.resolver import resolve
    surf = build_surface(resolve(parse_file(REPO_ROOT / "bench/protocols/realistic_smr.refrain"), None))
    labels = sorted(sc.label for sc in _build_corpus(surf))
    assert labels == EXPECTED_REALISTIC_SMR_LABELS
