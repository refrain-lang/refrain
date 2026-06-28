# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""End-to-end oracle prediction for full Scenarios on smr_cz."""
from __future__ import annotations

import pytest

from refrain.fuzz.oracle import predict
from refrain.fuzz.scenario import (
    BandSegment, DontCareReason, PhaseOverride, Scenario, Tone,
)
from refrain.fuzz.surface import build_surface
from tests.fuzz._smr import resolved_smr_ir


@pytest.fixture(scope="module")
def surface():
    return build_surface(resolved_smr_ir())


def test_high_beta_artifact_alone_keeps_reward_silent(surface):
    scenario = Scenario(
        label="hbeta-artifact",
        duration_s=10.0,
        sample_rate_hz=surface.sample_rate_hz,
        segments=(
            BandSegment(band=(22.0, 30.0), channel="Cz",
                        start_s=4.0, end_s=6.0,
                        content=Tone(amplitude_uv=30.0)),
        ),
        controls={}, coverage_tags=frozenset({"hbeta_artifact"}),
        phase_override=PhaseOverride(warmup_s=1.0, training_s=8.5, cooldown_s=0.5),
    )
    timeline = predict(scenario, surface)
    assert timeline.should_fire_event_samples == [], \
        "high-beta artifact should suppress reward via the artifact leaf"


def test_pre_window_fill_is_dont_care_for_percentile(surface):
    scenario = Scenario(
        label="early-smr",
        duration_s=10.0,
        sample_rate_hz=surface.sample_rate_hz,
        segments=(
            BandSegment(band=(12.0, 15.0), channel="Cz",
                        start_s=0.0, end_s=10.0,
                        content=Tone(amplitude_uv=40.0)),
        ),
        controls={}, coverage_tags=frozenset(),
        phase_override=PhaseOverride(warmup_s=1.0, training_s=8.5, cooldown_s=0.5),
    )
    timeline = predict(scenario, surface)
    pre_fill = [iv for iv in timeline.dont_care_intervals
                if iv.reason is DontCareReason.PRE_WINDOW_FILL]
    assert pre_fill, "early region should be marked PRE_WINDOW_FILL DON'T-CARE"


def test_post_fill_smr_dominance_predicts_fire(surface):
    fs = surface.sample_rate_hz
    duration = 130.0
    scenario = Scenario(
        label="post-fill-smr",
        duration_s=duration,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=(12.0, 15.0), channel="Cz",
                        start_s=121.0, end_s=128.0,
                        content=Tone(amplitude_uv=40.0)),
        ),
        controls={}, coverage_tags=frozenset({"post_fill_smr"}),
        phase_override=PhaseOverride(warmup_s=2.0, training_s=duration - 2.5, cooldown_s=0.5),
    )
    timeline = predict(scenario, surface)
    spike_start = int(122.0 * fs)
    spike_end = int(128.0 * fs)
    assert any(spike_start <= s <= spike_end for s in timeline.should_fire_event_samples), \
        f"expected SHOULD-FIRE in [{spike_start}, {spike_end}], got {timeline.should_fire_event_samples}"
