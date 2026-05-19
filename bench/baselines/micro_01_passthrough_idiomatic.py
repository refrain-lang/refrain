"""Idiomatic baseline for micro_01_passthrough.

Pipeline: referential montage (Cz - mean(A1, A2)) -> emit as 'raw'.
Output binding `audio_gain = 0` -> constant-zero 'output/audio_gain' stream.
"""

from __future__ import annotations

import numpy as np


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.cz = channel_names.index("Cz")
        self.a1 = channel_names.index("A1")
        self.a2 = channel_names.index("A2")

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.cz] - 0.5 * (raw_chunk[:, self.a1] + raw_chunk[:, self.a2])
        n = raw_chunk.shape[0]
        return {"raw": raw, "output/audio_gain": np.zeros(n, dtype=np.float64)}
