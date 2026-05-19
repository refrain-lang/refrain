"""Idiomatic baseline for micro_02_bandpass.

Pipeline: referential montage -> butter SOS bandpass (12-15 Hz, order 4),
mirroring refrain's BandpassImpl (zero initial state, sosfilt axis=0).
Output binding `audio_gain = 0` -> constant-zero stream.
"""

from __future__ import annotations

import numpy as np
from scipy import signal as scisig


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.cz = channel_names.index("Cz")
        self.a1 = channel_names.index("A1")
        self.a2 = channel_names.index("A2")
        nyq = sample_rate_hz / 2.0
        self.sos = scisig.butter(4, [12.0 / nyq, 15.0 / nyq], btype="band", output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.cz] - 0.5 * (raw_chunk[:, self.a1] + raw_chunk[:, self.a2])
        smr_bp, self.zi = scisig.sosfilt(self.sos, raw, zi=self.zi, axis=0)
        n = raw_chunk.shape[0]
        return {"raw": raw, "smr_bp": smr_bp, "output/audio_gain": np.zeros(n, dtype=np.float64)}
