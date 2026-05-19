"""Idiomatic baseline for micro_02_bandpass.

Pipeline: linked-ears referential montage -> butter SOS bandpass (12-15 Hz,
order 4), mirroring refrain's BandpassImpl (zero initial state, sosfilt
axis=0). Output binding `audio_gain = 0` -> constant-zero stream.
"""

from __future__ import annotations

import numpy as np
from scipy import signal as scisig

from bench.baselines._dsp import linked_ears_montage


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.channel_names = channel_names
        nyq = sample_rate_hz / 2.0
        self.sos = scisig.butter(4, [12.0 / nyq, 15.0 / nyq], btype="band", output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = linked_ears_montage(raw_chunk, self.channel_names)
        smr_bp, self.zi = scisig.sosfilt(self.sos, raw, zi=self.zi, axis=0)
        n = raw_chunk.shape[0]
        return {"raw": raw, "smr_bp": smr_bp, "output/audio_gain": np.zeros(n, dtype=np.float64)}
