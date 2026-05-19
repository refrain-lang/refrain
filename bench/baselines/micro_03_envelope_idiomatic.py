"""Idiomatic baseline for micro_03_envelope.

Pipeline: referential montage -> bandpass(12-15Hz) -> FIR Hilbert -> magnitude
-> one-pole smooth(250ms). Each stage mirrors the corresponding refrain
primitive in src/refrain/primitive_impls.py exactly (BandpassImpl,
HilbertFirImpl + _fir_stream/_pure_delay, MagnitudeImpl, SmoothImpl) so the
output matches bit-for-bit up to float tolerance.
"""

from __future__ import annotations

import numpy as np
from scipy import signal as scisig


def _fir_stream(
    h: np.ndarray, x: np.ndarray, state: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Streaming FIR (mirrors refrain's _fir_stream)."""
    padded = np.concatenate([state, x])
    y = np.convolve(padded, h, mode="valid")
    new_state = padded[-(len(h) - 1):] if len(h) > 1 else state
    return y, new_state


def _pure_delay(
    x: np.ndarray, state: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Pure delay line (mirrors refrain's _pure_delay)."""
    combined = np.concatenate([state, x])
    new_state = combined[-len(state):] if len(state) > 0 else state
    return combined[: len(x)], new_state


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.cz = channel_names.index("Cz")
        self.a1 = channel_names.index("A1")
        self.a2 = channel_names.index("A2")

        # --- bandpass (BandpassImpl): butter SOS, zero initial state ---
        nyq = sample_rate_hz / 2.0
        self.sos = scisig.butter(4, [12.0 / nyq, 15.0 / nyq], btype="band", output="sos")
        self.bp_zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)

        # --- hilbert (HilbertFirImpl): FIR Hilbert transformer ---
        taps = 65
        if taps % 2 == 0:
            taps += 1
        N = taps
        center = N // 2
        n = np.arange(N) - center
        h = np.zeros(N, dtype=np.float64)
        mask = (n != 0) & (n % 2 != 0)
        h[mask] = 2.0 / (np.pi * n[mask])
        h *= np.hamming(N)
        self.h = h
        self.center = center
        self.imag_buf = np.zeros(N - 1, dtype=np.float64)
        self.real_buf = np.zeros(center, dtype=np.float64)

        # --- smooth (SmoothImpl): one-pole IIR low-pass, tau = 250 ms ---
        tau_s = 250.0 / 1000.0
        self.alpha = 1.0 - np.exp(-1.0 / (tau_s * float(sample_rate_hz)))
        self.smooth_state = 0.0

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.cz] - 0.5 * (raw_chunk[:, self.a1] + raw_chunk[:, self.a2])

        # bandpass
        bp, self.bp_zi = scisig.sosfilt(self.sos, raw, zi=self.bp_zi, axis=0)

        # hilbert: analytic = delayed_real + 1j * hilbert_imag
        imag, self.imag_buf = _fir_stream(self.h, bp, self.imag_buf)
        real, self.real_buf = _pure_delay(bp, self.real_buf)
        analytic = real + 1j * imag

        # magnitude
        mag = np.abs(analytic)

        # smooth: one-pole IIR via lfilter (mirrors SmoothImpl.step)
        b = np.array([self.alpha])
        a = np.array([1.0, -(1.0 - self.alpha)])
        zi = np.array([self.smooth_state * (1.0 - self.alpha)])
        env, _ = scisig.lfilter(b, a, mag, zi=zi)
        self.smooth_state = float(env[-1])

        n = raw_chunk.shape[0]
        return {
            "raw": raw,
            "smr_envelope": env,
            "output/audio_gain": np.zeros(n, dtype=np.float64),
        }
