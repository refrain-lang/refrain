"""Idiomatic baseline for micro_05_reward.

Defines reusable _Envelope(band, tau_ms) and _PercentileThreshold helpers
(mirroring refrain's primitives) used here and by the realistic_smr baseline.

Pipeline (per the protocol): two envelopes (SMR 12-15 Hz, theta 4-8 Hz), each
a windowed-percentile threshold; a dwell event machine over
all_of([above(smr_env, smr_t), below(theta_env, theta_t)]); a sigmoid over the
ratio smr_env / smr_t; and the two output streams (audio_chime event channel,
audio_gain value channel). Each stage mirrors the corresponding refrain
primitive in src/refrain/primitive_impls.py exactly.
"""

from __future__ import annotations

from collections import deque

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


def _design_hilbert_fir(taps: int = 65) -> np.ndarray:
    """FIR Hilbert transformer taps (mirrors HilbertFirImpl): odd length,
    h = 2/(pi*n) for odd nonzero n, windowed by a Hamming window."""
    if taps % 2 == 0:
        taps += 1
    N = taps
    center = N // 2
    n = np.arange(N) - center
    h = np.zeros(N, dtype=np.float64)
    mask = (n != 0) & (n % 2 != 0)
    h[mask] = 2.0 / (np.pi * n[mask])
    h *= np.hamming(N)
    return h


class _Envelope:
    """bandpass(band, order=4) -> FIR hilbert -> magnitude -> one-pole smooth.

    Parameterized by frequency band and smoothing time constant so multiple
    bands (SMR, theta, ...) share one implementation.
    """

    def __init__(
        self,
        *,
        band: tuple[float, float],
        tau_ms: float,
        sample_rate_hz: float,
        order: int = 4,
    ):
        # --- bandpass (BandpassImpl): butter SOS, zero initial state ---
        nyq = sample_rate_hz / 2.0
        self.sos = scisig.butter(
            order, [band[0] / nyq, band[1] / nyq], btype="band", output="sos"
        )
        self.bp_zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)

        # --- hilbert (HilbertFirImpl): FIR Hilbert transformer ---
        self.h = _design_hilbert_fir(65)
        N = len(self.h)
        self.center = N // 2
        self.imag_buf = np.zeros(N - 1, dtype=np.float64)
        self.real_buf = np.zeros(self.center, dtype=np.float64)

        # --- smooth (SmoothImpl): one-pole IIR low-pass ---
        tau_s = tau_ms / 1000.0
        self.alpha = 1.0 - np.exp(-1.0 / (tau_s * float(sample_rate_hz)))
        self.smooth_state = 0.0

    def step(self, signal_1d: np.ndarray) -> np.ndarray:
        # bandpass
        bp, self.bp_zi = scisig.sosfilt(self.sos, signal_1d, zi=self.bp_zi, axis=0)

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
        return env


class _PercentileThreshold:
    """Windowed-percentile threshold (mirrors PercentileImpl): a deque of the
    last `window` samples; per sample append then np.percentile over the
    current buffer (default linear interpolation)."""

    def __init__(
        self,
        *,
        target_pct: float,
        window_ms: float,
        sample_rate_hz: float,
    ):
        self.target_pct = float(target_pct)
        window_samples = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        self.buffer: deque[float] = deque(maxlen=window_samples)

    def step(self, x_1d: np.ndarray) -> np.ndarray:
        out = np.empty(x_1d.shape[0], dtype=np.float64)
        for i, v in enumerate(x_1d):
            self.buffer.append(float(v))
            arr = np.fromiter(self.buffer, dtype=np.float64)
            out[i] = float(np.percentile(arr, self.target_pct))
        return out


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.cz = channel_names.index("Cz")
        self.a1 = channel_names.index("A1")
        self.a2 = channel_names.index("A2")

        self.smr_env = _Envelope(band=(12.0, 15.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.theta_env = _Envelope(band=(4.0, 8.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.smr_t = _PercentileThreshold(target_pct=70.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz)
        self.theta_t = _PercentileThreshold(target_pct=30.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz)

        # dwell state machine (DwellImpl): 250 ms @ rate
        self.dwell_samples = max(1, int(round(0.250 * sample_rate_hz)))
        self.streak = 0
        self.was_holding = False

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.cz] - 0.5 * (raw_chunk[:, self.a1] + raw_chunk[:, self.a2])

        smr_e = self.smr_env.step(raw)
        theta_e = self.theta_env.step(raw)
        smr_t = self.smr_t.step(smr_e)
        theta_t = self.theta_t.step(theta_e)

        # all_of([above(smr_env, smr_t), below(theta_env, theta_t)])
        condition = (smr_e > smr_t) & (theta_e < theta_t)

        # dwell rising-edge / holds state machine, persisted across chunks
        n = condition.shape[0]
        events = np.zeros(n, dtype=bool)
        holds = np.zeros(n, dtype=bool)
        for i in range(n):
            if condition[i]:
                self.streak += 1
            else:
                self.streak = 0
            is_h = self.streak >= self.dwell_samples
            holds[i] = is_h
            if is_h and not self.was_holding:
                events[i] = True
            self.was_holding = is_h

        # ratio smr_env / smr_t. Mirror refrain's `/` binop: replace
        # non-finite results (e.g. 0/0 during warmup) with 0.0.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = smr_e / smr_t
        ratio = np.where(np.isfinite(ratio), ratio, 0.0)

        # sigmoid (midpoint 1.0, steepness 3)
        continuous = 1.0 / (1.0 + np.exp(-3.0 * (ratio - 1.0)))

        return {
            "raw": raw,
            "smr_envelope": smr_e,
            "theta_envelope": theta_e,
            "smr_t": smr_t,
            "theta_t": theta_t,
            "reward.continuous": continuous,
            "reward.event": events,
            "reward.event.holds": holds,
            "output/audio_chime": events,
            "output/audio_gain": np.clip(continuous, 0.0, 1.0),
        }
