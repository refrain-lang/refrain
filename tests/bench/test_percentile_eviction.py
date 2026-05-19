"""Focused coverage: percentile ring-buffer eviction.

The protocol-level equivalence tests use a 2-min window (30,720 samples) but
run fewer samples than that, so the deque never fills and the eviction path is
never exercised. This test pins a short window and runs well past it, so the
oldest-sample-drop path is covered — and confirms the baseline's
PercentileThreshold still matches refrain's PercentileImpl once eviction is
active.
"""

from __future__ import annotations

import numpy as np

from refrain.primitive_impls import PercentileImpl

from bench.baselines._dsp import PercentileThreshold

SAMPLE_RATE_HZ = 256.0
WINDOW_MS = 200.0          # 51 samples at 256 Hz
CHUNK_SIZE = 32
N_SAMPLES = 512            # ~10x the window, so eviction is active for most of the run


def test_percentile_threshold_matches_refrain_through_eviction():
    target_pct = 70.0
    refrain_impl = PercentileImpl(
        target_pct=target_pct, window_ms=WINDOW_MS, sample_rate_hz=SAMPLE_RATE_HZ,
    )
    baseline = PercentileThreshold(
        target_pct=target_pct, window_ms=WINDOW_MS, sample_rate_hz=SAMPLE_RATE_HZ,
    )

    rng = np.random.default_rng(0)
    signal = rng.standard_normal(N_SAMPLES) * 5.0

    refrain_out = []
    baseline_out = []
    for start in range(0, N_SAMPLES, CHUNK_SIZE):
        chunk = signal[start:start + CHUNK_SIZE]
        refrain_out.append(refrain_impl.step(chunk))
        baseline_out.append(baseline.step(chunk))
    refrain_full = np.concatenate(refrain_out)
    baseline_full = np.concatenate(baseline_out)

    window_samples = int(round(WINDOW_MS / 1000.0 * SAMPLE_RATE_HZ))
    assert N_SAMPLES > 3 * window_samples, "run must exceed window so eviction is exercised"

    # Compare the post-eviction region (after the buffer has fully filled).
    np.testing.assert_allclose(
        refrain_full[window_samples:], baseline_full[window_samples:],
        atol=1e-9, rtol=1e-9,
    )
