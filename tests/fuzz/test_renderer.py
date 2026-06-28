"""Tests for the Scenario renderer (extension of synthetic.py)."""
from __future__ import annotations

import numpy as np
import pytest
from numpy.fft import rfft, rfftfreq

from refrain.fuzz.scenario import BandNoise, BandSegment, Scenario, Tone
from refrain.synthetic import render_scenario


def _power_in_band(samples_1d, fs, band):
    """Return total spectral power in the (low, high) band of a 1D signal."""
    spec = np.abs(rfft(samples_1d))
    freqs = rfftfreq(len(samples_1d), 1.0 / fs)
    mask = (freqs >= band[0]) & (freqs <= band[1])
    return float((spec[mask] ** 2).sum())


def _full_signal(gen, n_samples, chunk=256):
    parts = []
    remaining = n_samples
    while remaining > 0:
        size = min(chunk, remaining)
        parts.append(gen.next_chunk(size))
        remaining -= size
    return np.concatenate(parts, axis=0)


def test_tone_injects_power_at_center_band_and_quiet_elsewhere():
    fs = 256
    duration = 4.0
    n = int(duration * fs)
    scenario = Scenario(
        label="smr-tone",
        duration_s=duration,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=(12.0, 15.0), channel="Cz",
                        start_s=1.0, end_s=3.0,
                        content=Tone(amplitude_uv=30.0)),
        ),
        controls={}, coverage_tags=frozenset(),
    )
    gen = render_scenario(scenario, channels=("Cz",))
    samples = _full_signal(gen, n)[:, 0]
    p_in = _power_in_band(samples, fs, (12.0, 15.0))
    p_off = _power_in_band(samples, fs, (22.0, 30.0))
    assert p_in > 50 * p_off, f"in-band power should dominate; got {p_in=}, {p_off=}"


def test_band_noise_targets_in_band_rms_within_tolerance():
    fs = 256
    duration = 8.0
    n = int(duration * fs)
    target_rms = 20.0
    scenario = Scenario(
        label="smr-noise",
        duration_s=duration,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=(12.0, 15.0), channel="Cz",
                        start_s=0.5, end_s=7.5,
                        content=BandNoise(rms_uv=target_rms)),
        ),
        controls={}, coverage_tags=frozenset(),
    )
    gen = render_scenario(scenario, channels=("Cz",))
    samples = _full_signal(gen, n)[:, 0]

    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, [12.0 / (fs / 2), 15.0 / (fs / 2)], btype="band", output="sos")
    filtered = sosfiltfilt(sos, samples)
    mid = slice(int(1.0 * fs), int(7.0 * fs))
    in_band_rms = float(np.sqrt(np.mean(filtered[mid] ** 2)))
    assert in_band_rms == pytest.approx(target_rms, rel=0.30), (
        f"in-band RMS {in_band_rms:.2f} != target {target_rms:.2f} ±30%"
    )


def test_off_segment_regions_stay_at_pink_floor():
    fs = 256
    duration = 4.0
    n = int(duration * fs)
    scenario = Scenario(
        label="late-tone",
        duration_s=duration,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=(12.0, 15.0), channel="Cz",
                        start_s=3.0, end_s=3.5,
                        content=Tone(amplitude_uv=30.0)),
        ),
        controls={}, coverage_tags=frozenset(),
    )
    gen = render_scenario(scenario, channels=("Cz",))
    samples = _full_signal(gen, n)[:, 0]

    early_rms = float(np.sqrt(np.mean(samples[: int(2.5 * fs)] ** 2)))
    assert 3.0 < early_rms < 25.0, f"early region should be at noise floor, got {early_rms}"


def test_deterministic_by_seed():
    scenario = Scenario(
        label="repro", duration_s=2.0, sample_rate_hz=256,
        segments=(), controls={}, coverage_tags=frozenset(), seed=7,
    )
    g1 = render_scenario(scenario, channels=("Cz",))
    g2 = render_scenario(scenario, channels=("Cz",))
    s1 = _full_signal(g1, 512)
    s2 = _full_signal(g2, 512)
    assert np.array_equal(s1, s2)
