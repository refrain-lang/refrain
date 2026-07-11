# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the directed scenario generator."""
from __future__ import annotations

from pathlib import Path

import pytest

import refrain
from refrain.fuzz.generate import (
    generate_characterization_probe,
    generate_directed_scenarios,
)
from refrain.fuzz.surface import build_surface
from refrain.resolver import resolve
from tests.fuzz._smr import resolved_smr_ir

REPO_ROOT = Path(__file__).resolve().parents[2]

# `generate_directed_scenarios` is the sample-exact-tier generator: the tier
# split (build_surface.tier) routes ANY protocol with a percentile-thresholded
# reward leaf to the metamorphic tier instead, and the sample-exact amplitude
# picker (`_amplitude_for_truth`) now raises on a non-absolute threshold. So
# its own coverage tests need an absolute-only composite fixture rather than
# realistic_smr (percentile-thresholded, metamorphic-tier) — this mirrors
# realistic_smr's three-leaf all_of shape with absolute thresholds instead.
_COMPOSITE_ABSOLUTE_PROTOCOL = '''
protocol "micro_composite_absolute" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "smr_envelope" { from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  derive "theta_envelope" { from = "raw"
    pipeline = [ bandpass(band: (4 Hz, 8 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  derive "high_beta_envelope" { from = "raw"
    pipeline = [ bandpass(band: (22 Hz, 30 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "smr_t" { signal = "smr_envelope"; type = absolute(8 uV) }
  threshold "theta_t" { signal = "theta_envelope"; type = absolute(15 uV) }
  threshold "hbeta_t" { signal = "high_beta_envelope"; type = absolute(8 uV) }
  reward {
    event = dwell(condition: all_of([
        above("smr_envelope", "smr_t"),
        below("theta_envelope", "theta_t"),
        below("high_beta_envelope", "hbeta_t"),
      ]), duration: 250 ms)
    continuous = sigmoid("smr_envelope" / "smr_t", midpoint: 1.0, steepness: 3)
  }
  output { audio_chime = reward.event }
  session { phases = [ phase { name = "training"; duration = 30 min } ] }
}
'''


def _composite_absolute_ir():
    return resolve(refrain.parse(_COMPOSITE_ABSOLUTE_PROTOCOL), amp=None)


@pytest.fixture(scope="module")
def scenarios():
    surface = build_surface(_composite_absolute_ir())
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


def test_no_percentile_warmup_scenario_for_an_absolute_only_surface(scenarios):
    # The sample-exact tier is absolute-only (a percentile leaf routes the whole
    # protocol to the metamorphic tier instead), so the percentile-warmup
    # scenario never fires here — there is no percentile threshold to warm up.
    tags = {tag for s in scenarios for tag in s.coverage_tags}
    assert "percentile:warmup_then_spike" not in tags


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


