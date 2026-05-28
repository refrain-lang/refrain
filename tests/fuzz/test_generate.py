# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the directed scenario generator."""
from __future__ import annotations

import pytest

from refrain.fuzz.generate import generate_directed_scenarios
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
