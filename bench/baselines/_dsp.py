"""Shared, refrain-mirroring DSP primitives for the idiomatic baselines.

This module is the single home for the streaming numpy/scipy logic that all
baselines need. Each primitive here mirrors the corresponding implementation in
``src/refrain/primitive_impls.py`` (and the ``/`` binop in ``eval_.py``)
exactly, so baseline outputs match refrain bit-for-bit up to float tolerance.

Baselines import from this module and never from each other.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from scipy import signal as scisig

# Standard ear-electrode labels averaged by refrain's ReferentialImpl for the
# "linked_ears" reference, in the order it scans them.
_EAR_LABELS = ("A1", "A2", "M1", "M2", "T9", "T10")


def linked_ears_montage(
    raw_chunk: np.ndarray, channel_names: tuple[str, ...]
) -> np.ndarray:
    """Linked-ears referential montage for active "Cz".

    Mirrors ``ReferentialImpl`` with ``reference="linked_ears"``: subtract the
    mean of whichever of A1/A2/M1/M2/T9/T10 are present from Cz. For the
    benchmark's (Cz, A1, A2) channels this reduces to ``Cz - 0.5*(A1+A2)``.
    If fewer than two ear labels are present, refrain falls back to a
    common-average reference (mean of all channels), which this helper also
    mirrors.
    """
    active = raw_chunk[:, channel_names.index("Cz")]
    ear_indices = [channel_names.index(n) for n in _EAR_LABELS if n in channel_names]
    if len(ear_indices) < 2:
        # linked_ears fallback: common-average reference.
        ref = raw_chunk.mean(axis=1)
    else:
        ref = raw_chunk[:, ear_indices].mean(axis=1)
    return active - ref


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


def design_hilbert_fir(taps: int = 65) -> np.ndarray:
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


class Envelope:
    """bandpass(band, order=4) -> FIR hilbert -> magnitude -> one-pole smooth.

    Parameterized by frequency band and smoothing time constant so multiple
    bands (SMR, theta, ...) share one implementation. Mirrors the chain of
    BandpassImpl, HilbertFirImpl, MagnitudeImpl, and SmoothImpl.
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
        self.h = design_hilbert_fir(65)
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


class PercentileThreshold:
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


class DwellMachine:
    """`dwell(condition, duration)` state machine (mirrors DwellImpl).

    Maintains a streak that increments while `condition` is True and resets to
    0 otherwise. `holds` is True when the streak meets/exceeds `dwell_samples`;
    `events` fire on the rising edge of holding. Streak and was-holding state
    persist across chunks.
    """

    def __init__(self, *, duration_ms: float, sample_rate_hz: float):
        self.dwell_samples = max(1, int(round(duration_ms / 1000.0 * sample_rate_hz)))
        self.streak = 0
        self.was_holding = False

    def step(self, condition: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = condition.shape[0]
        events = np.zeros(n, dtype=bool)
        holds = np.zeros(n, dtype=bool)
        for i in range(n):
            if condition[i]:
                self.streak += 1
            else:
                self.streak = 0
            is_holding = self.streak >= self.dwell_samples
            holds[i] = is_holding
            if is_holding and not self.was_holding:
                events[i] = True
            self.was_holding = is_holding
        return events, holds


def safe_ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """`num / den`, mirroring refrain's `/` binop: non-finite results
    (e.g. 0/0 during warm-up) are replaced with 0.0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        r = num / den
    return np.where(np.isfinite(r), r, 0.0)


def sigmoid(x: np.ndarray, *, midpoint: float, steepness: float) -> np.ndarray:
    """Logistic curve (mirrors SigmoidImpl): 1/(1+exp(-steepness*(x-midpoint)))."""
    return 1.0 / (1.0 + np.exp(-steepness * (x - midpoint)))
