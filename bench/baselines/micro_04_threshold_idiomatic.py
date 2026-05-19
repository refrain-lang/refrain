"""Idiomatic baseline for micro_04_threshold.

Linked-ears montage -> SMR envelope (12-15 Hz, 250 ms) -> windowed percentile
threshold. Both DSP stages are the shared primitives in `_dsp.py`
(`Envelope`, `PercentileThreshold`), which mirror refrain's primitives exactly.
"""

from __future__ import annotations

import numpy as np

from bench.baselines._dsp import Envelope, PercentileThreshold, linked_ears_montage


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.channel_names = channel_names
        self.env = Envelope(band=(12.0, 15.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.smr_t = PercentileThreshold(
            target_pct=70.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz
        )

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = linked_ears_montage(raw_chunk, self.channel_names)
        envelope = self.env.step(raw)
        smr_t = self.smr_t.step(envelope)
        n = raw_chunk.shape[0]
        return {
            "raw": raw,
            "smr_envelope": envelope,
            "smr_t": smr_t,
            "output/audio_gain": np.zeros(n, dtype=np.float64),
        }
