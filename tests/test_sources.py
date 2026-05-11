# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Source layer: format detection + the synthetic source."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.sources import (
    SourceError,
    SyntheticSource,
    XdfSource,
    open_source,
)
from refrain.synthetic import SignalGenerator, SMRBurst


XDF_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "CRJA_20240228_EO.xdf"


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def test_unknown_extension_raises():
    with pytest.raises(SourceError, match="unrecognised extension"):
        open_source("/tmp/recording.weirdformat")


def test_no_such_file_returns_useful_error():
    with pytest.raises(SourceError):
        open_source("/tmp/definitely_does_not_exist_12345.fif")


# ---------------------------------------------------------------------------
# XDF (against the shipped real-data fixture)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not XDF_PATH.exists(), reason="XDF fixture not available")
def test_xdf_source_reads_real_file():
    src = open_source(XDF_PATH)
    assert isinstance(src, XdfSource)
    assert src.sample_rate_hz == 256.0
    assert "Cz" in src.channel_names
    # 300 seconds at 256 Hz = 76800 samples.
    assert src.n_samples == 76800


@pytest.mark.skipif(not XDF_PATH.exists(), reason="XDF fixture not available")
def test_xdf_source_chunks_have_correct_shape():
    src = open_source(XDF_PATH)
    chunks = list(src.iter_chunks(256))
    assert len(chunks) > 0
    first = chunks[0]
    assert first.shape == (256, 19)
    assert first.dtype == np.float64


@pytest.mark.skipif(not XDF_PATH.exists(), reason="XDF fixture not available")
def test_xdf_source_explicit_stream_name():
    src = XdfSource(XDF_PATH, stream_name="Neurofield Q21")
    assert "Cz" in src.channel_names


@pytest.mark.skipif(not XDF_PATH.exists(), reason="XDF fixture not available")
def test_xdf_source_unknown_stream_name_raises():
    with pytest.raises(SourceError, match="no stream named"):
        XdfSource(XDF_PATH, stream_name="Nonexistent")


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------


def test_synthetic_source_emits_correct_sample_count():
    gen = SignalGenerator(sample_rate_hz=256, channels=("Cz",), seed=1)
    src = SyntheticSource(gen, duration_s=2.0)
    assert src.n_samples == 512
    total = 0
    for chunk in src.iter_chunks(64):
        total += chunk.shape[0]
    assert total == 512


def test_synthetic_source_chunks_continuous_in_time():
    """Successive chunks should continue the same random stream — no
    discontinuities at chunk boundaries."""
    gen = SignalGenerator(
        sample_rate_hz=256, channels=("Cz",),
        bursts=(),  # pure pink noise
        seed=42,
    )
    src = SyntheticSource(gen, duration_s=1.0)
    chunks = list(src.iter_chunks(64))
    full = np.concatenate(chunks).ravel()
    # No big jumps at chunk boundaries (sample 63→64, 127→128, ...).
    jumps = np.diff(full)
    assert np.std(jumps) < 50.0, "huge discontinuities suggest broken state"


def test_synthetic_burst_increases_local_rms():
    """RMS during a burst window > RMS during quiet."""
    bursts = (SMRBurst(start_s=2.0, end_s=4.0, center_hz=13.0, amplitude_uv=30.0),)
    gen = SignalGenerator(
        sample_rate_hz=256, channels=("Cz",),
        bursts=bursts, noise_uv_rms=10.0, seed=42,
    )
    src = SyntheticSource(gen, duration_s=6.0)
    data = np.concatenate(list(src.iter_chunks(256))).ravel()
    burst_rms = np.sqrt(np.mean(data[2 * 256 : 4 * 256] ** 2))
    quiet_rms = np.sqrt(np.mean(data[5 * 256 :] ** 2))
    assert burst_rms > quiet_rms * 1.3, (
        f"burst RMS {burst_rms:.2f} should exceed quiet RMS {quiet_rms:.2f}"
    )


def test_synthetic_deterministic_via_seed():
    """Same seed -> same samples."""
    def make():
        gen = SignalGenerator(sample_rate_hz=256, channels=("Cz",), seed=99)
        return np.concatenate(list(SyntheticSource(gen, 2.0).iter_chunks(64))).ravel()
    a = make()
    b = make()
    assert np.allclose(a, b)


def test_synthetic_burst_channel_isolation():
    """A per-channel burst affects only the named channel."""
    bursts = (SMRBurst(start_s=1.0, end_s=2.0, center_hz=13.0, amplitude_uv=30.0, channel="Cz"),)
    gen = SignalGenerator(
        sample_rate_hz=256, channels=("Cz", "Pz"),
        bursts=bursts, seed=1,
    )
    data = np.concatenate(list(SyntheticSource(gen, 3.0).iter_chunks(256)))
    cz_burst = data[1 * 256 : 2 * 256, 0]
    pz_burst = data[1 * 256 : 2 * 256, 1]
    assert np.sqrt(np.mean(cz_burst ** 2)) > np.sqrt(np.mean(pz_burst ** 2)) * 1.5
