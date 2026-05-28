"""Tests for the Scenario shared contract."""
from __future__ import annotations

import pytest

from refrain.fuzz.scenario import (
    BandNoise,
    BandSegment,
    DontCareReason,
    Scenario,
    Tone,
    Verdict,
)


def test_tone_and_band_noise_are_distinct_frozen_types():
    t = Tone(amplitude_uv=20.0)
    n = BandNoise(rms_uv=10.0)
    assert t.amplitude_uv == 20.0
    assert n.rms_uv == 10.0
    with pytest.raises(Exception):
        t.amplitude_uv = 99.0  # frozen
    with pytest.raises(Exception):
        n.rms_uv = 99.0


def test_band_segment_validates_time_order_and_band():
    seg = BandSegment(
        band=(12.0, 15.0),
        channel="Cz",
        start_s=1.0,
        end_s=2.0,
        content=Tone(amplitude_uv=20.0),
    )
    assert seg.duration_s == pytest.approx(1.0)
    with pytest.raises(ValueError):
        BandSegment(band=(15.0, 12.0), channel="Cz", start_s=0, end_s=1, content=Tone(1.0))
    with pytest.raises(ValueError):
        BandSegment(band=(12.0, 15.0), channel="Cz", start_s=2.0, end_s=1.0, content=Tone(1.0))


def test_scenario_validates_required_fields_and_defaults():
    s = Scenario(
        label="all-quiet",
        duration_s=5.0,
        sample_rate_hz=256,
        segments=(),
        controls={},
        coverage_tags=frozenset({"negative_control"}),
    )
    assert s.duration_s == 5.0
    assert s.sample_rate_hz == 256
    assert s.segments == ()
    assert s.phase_override is None
    with pytest.raises(ValueError):
        Scenario(
            label="bad", duration_s=-1, sample_rate_hz=256,
            segments=(), controls={}, coverage_tags=frozenset(),
        )
    with pytest.raises(ValueError):
        Scenario(
            label="bad-rate", duration_s=1, sample_rate_hz=0,
            segments=(), controls={}, coverage_tags=frozenset(),
        )


def test_dont_care_reasons_enumerate_expected_set():
    expected = {"near_boundary", "settle_collar", "pre_window_fill",
                "phase_muted", "inhibit_ambiguous"}
    assert {r.value for r in DontCareReason} == expected


def test_verdict_has_expected_classes():
    expected = {"pass", "missed", "spurious", "dont_care"}
    assert {v.value for v in Verdict} == expected
