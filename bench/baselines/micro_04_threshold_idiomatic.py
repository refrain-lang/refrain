"""Idiomatic baseline for micro_04_threshold.

Reuses the micro_03 envelope baseline and adds a windowed percentile threshold
mirroring refrain's PercentileImpl exactly (deque(maxlen=window_samples),
per-sample np.percentile over the current buffer).
"""

from __future__ import annotations

from collections import deque

import numpy as np

from bench.baselines.micro_03_envelope_idiomatic import Baseline as EnvelopeBaseline


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.env = EnvelopeBaseline(sample_rate_hz=sample_rate_hz, channel_names=channel_names)
        window_samples = max(1, int(round(120000.0 / 1000.0 * sample_rate_hz)))
        self.target_pct = 70.0
        self.buffer: deque[float] = deque(maxlen=window_samples)

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        out = self.env.step(raw_chunk)
        envelope = out["smr_envelope"]
        smr_t = np.empty(envelope.shape[0], dtype=np.float64)
        for i, v in enumerate(envelope):
            self.buffer.append(float(v))
            arr = np.fromiter(self.buffer, dtype=np.float64)
            smr_t[i] = float(np.percentile(arr, self.target_pct))
        out["smr_t"] = smr_t
        return out
