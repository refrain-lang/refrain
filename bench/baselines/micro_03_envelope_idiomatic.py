"""Idiomatic baseline for micro_03_envelope.

Pipeline: linked-ears referential montage -> bandpass(12-15Hz) -> FIR Hilbert
-> magnitude -> one-pole smooth(250ms). The DSP chain is the shared
`Envelope` primitive in `_dsp.py`, which mirrors the corresponding refrain
primitives (BandpassImpl, HilbertFirImpl, MagnitudeImpl, SmoothImpl) exactly
so the output matches bit-for-bit up to float tolerance.
"""

from __future__ import annotations

import numpy as np

from bench.baselines._dsp import Envelope, linked_ears_montage


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.channel_names = channel_names
        self.env = Envelope(band=(12.0, 15.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = linked_ears_montage(raw_chunk, self.channel_names)
        env = self.env.step(raw)
        n = raw_chunk.shape[0]
        return {
            "raw": raw,
            "smr_envelope": env,
            "output/audio_gain": np.zeros(n, dtype=np.float64),
        }
