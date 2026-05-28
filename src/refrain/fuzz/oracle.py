# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Independent analytic oracle.

Predicts what the evaluator *should* do for a given Scenario, using ONLY:
  - the LogicalSurface (semantics + baked filter coefficients)
  - the Scenario itself

It never calls the evaluator. The predicted envelope of a pure tone is
computed from the BAKED filter coefficients via `scipy.signal.freqz_sos` —
Python evaluating the transfer function, not running the cascade — so it
is independent of the evaluator's streaming implementation.

This file is built incrementally:
  Task 4: DSP primitives (bandpass_gain_at, tone_envelope_steady_state, settle_time_s)
  Task 5: 3-valued absolute thresholds + condition tree + dwell
  Task 6: ordinal percentile thresholds + pre-fill DON'T-CARE + phase muting
"""
from __future__ import annotations

import numpy as np
from scipy.signal import freqz_sos


def bandpass_gain_at(sos, *, freq_hz: float, fs: int) -> float:
    """|H(e^{j2π freq/fs})| for a Butterworth SOS, computed from coefficients.

    This is the oracle's independence guard: scipy evaluates the transfer
    function on the SAME numbers the evaluator's cascade carries, but it
    does NOT run the cascade — so a cascade implementation bug cannot
    influence the prediction.
    """
    sos_arr = np.asarray(sos)
    # freqz_sos expects normalized angular frequencies in [0, 2π] when fs=2π.
    # w_target = 2π·freq/fs maps the physical frequency to that range.
    w_target = 2 * np.pi * freq_hz / fs
    w, h = freqz_sos(sos_arr, worN=[w_target], fs=2 * np.pi)
    return float(np.abs(h[0]))


def tone_envelope_steady_state(sos, *, freq_hz: float, amplitude_uv: float, fs: int) -> float:
    """Steady-state smoothed envelope for a pure tone in-band.

    Bandpass output of A·sin(2πf t) ≈ A·|H(f)|·sin(2πf t + φ); the analytic
    magnitude of that is A·|H(f)|; magnitude is constant for a steady tone;
    smoothing a constant = that constant. So the prediction reduces to:
    """
    return amplitude_uv * bandpass_gain_at(sos, freq_hz=freq_hz, fs=fs)


def settle_time_s(*, sos, tau_s: float | None, chunk_s: float, fs: int) -> float:
    """Worst-case time after a condition flip before the smoothed envelope is
    trusted. Sum of:
      - filter impulse-response settle, derived analytically from the 3 dB
        bandwidth: tau_filter = N_sections / (π · BW_Hz). For a Butterworth
        IIR bandpass, each SOS section contributes one ring-down time constant
        of 1/(π·BW); the worst-case settle is N·tau_filter. This avoids
        fragile numerical simulation while bounding the ring-down correctly.
      - 3·tau for the one-pole smoother (~95% step response)
      - one chunk (event-emission quantisation)
    """
    sos_arr = np.asarray(sos)

    # Measure 3 dB bandwidth from the baked coefficients (transfer-function
    # evaluation, never running the cascade — preserves oracle independence).
    w, h = freqz_sos(sos_arr, worN=8192, fs=2 * np.pi)
    freqs_hz = w / (2 * np.pi) * fs
    mag = np.abs(h)
    peak_mag = float(mag.max())
    above_3db = mag > peak_mag / np.sqrt(2)
    if above_3db.any():
        idxs = np.where(above_3db)[0]
        bw_hz = max(float(freqs_hz[idxs[-1]] - freqs_hz[idxs[0]]), 1e-3)
    else:
        bw_hz = 1.0  # fallback: 1 Hz

    n_sections = len(sos_arr)
    impulse_settle_s = n_sections / (np.pi * bw_hz)

    tau_term = 3.0 * (tau_s or 0.0)
    return impulse_settle_s + tau_term + chunk_s


__all__ = [
    "bandpass_gain_at",
    "settle_time_s",
    "tone_envelope_steady_state",
]
