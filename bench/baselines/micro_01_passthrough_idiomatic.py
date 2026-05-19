"""Idiomatic baseline for micro_01_passthrough.

Pipeline: linked-ears referential montage (Cz - mean(A1, A2)) -> emit as 'raw'.
Output binding `audio_gain = 0` -> constant-zero 'output/audio_gain' stream.
"""

from __future__ import annotations

import numpy as np

from bench.baselines._dsp import linked_ears_montage


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.channel_names = channel_names

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = linked_ears_montage(raw_chunk, self.channel_names)
        n = raw_chunk.shape[0]
        return {"raw": raw, "output/audio_gain": np.zeros(n, dtype=np.float64)}
