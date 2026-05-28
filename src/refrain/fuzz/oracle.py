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

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
from scipy.signal import freqz_sos

from .scenario import DontCareReason


def bandpass_gain_at(sos, *, freq_hz: float, fs: int) -> float:
    """|H(e^{j2π freq/fs})| for a Butterworth SOS, computed from coefficients.

    This is the oracle's independence guard: scipy evaluates the transfer
    function on the SAME numbers the evaluator's cascade carries, but it
    does NOT run the cascade — so a cascade implementation bug cannot
    influence the prediction.
    """
    sos_arr = np.asarray(sos)
    # Evaluate |H| at the physical frequency directly: with fs given, freqz_sos
    # samples the response at freq_hz (in [0, fs/2]) — no manual angular-frequency
    # conversion needed.
    w, h = freqz_sos(sos_arr, worN=[freq_hz], fs=fs)
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
        of ~1/(π·BW); summing over N sections approximates the worst-case
        ring-down. This is a tight engineering approximation (not a provable
        strict upper bound), chosen over fragile numerical impulse simulation;
        the surrounding 3·tau + chunk terms give comfortable headroom.
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

    tau_term = 3.0 * (tau_s if tau_s is not None else 0.0)
    return impulse_settle_s + tau_term + chunk_s


# 3-valued truth sentinels (for readability in the report code).
SHOULD_FIRE = "should_fire"
SHOULD_NOT_FIRE = "should_not_fire"
DONT_CARE = "dont_care"


@dataclass(frozen=True, slots=True)
class DontCareInterval:
    start_sample: int
    end_sample: int
    reason: DontCareReason


@dataclass(frozen=True, slots=True)
class ExpectedTimeline:
    """3-valued expected event timeline for a single Scenario.

    `should_fire_event_samples` lists the sample indices where the oracle
    predicts an event MUST be observed (within the collar). The whole
    timeline is otherwise SHOULD-NOT-FIRE, EXCEPT during `dont_care_intervals`
    which are not asserted (counted, with reason).
    """
    should_fire_event_samples: list[int]
    dont_care_intervals: list[DontCareInterval] = field(default_factory=list)


def predict_absolute_leaf_truth(
    *, env: float, threshold: float, margin: float, op: str
) -> bool | None:
    """3-valued truth of `env op threshold` with ±margin DON'T-CARE band.

    Returns True / False / None (None = DON'T-CARE)."""
    if op == "above":
        if env > threshold + margin:
            return True
        if env < threshold - margin:
            return False
        return None
    if op == "below":
        if env < threshold - margin:
            return True
        if env > threshold + margin:
            return False
        return None
    raise ValueError(f"unsupported leaf op: {op!r}")


def combine_condition_tree(op: str, kids: Iterable[bool | None]) -> bool | None:
    """3-valued AND / OR over the children's truth values.

    all_of: TRUE iff every child TRUE; FALSE iff any child FALSE; else DON'T-CARE.
    any_of: TRUE iff any child TRUE; FALSE iff every child FALSE; else DON'T-CARE.
    """
    vals = list(kids)
    if op == "all_of":
        if any(v is False for v in vals):
            return False
        if all(v is True for v in vals):
            return True
        return None
    if op == "any_of":
        if any(v is True for v in vals):
            return True
        if all(v is False for v in vals):
            return False
        return None
    raise ValueError(f"unsupported condition op: {op!r}")


def apply_dwell(
    truth_per_sample,
    *,
    dwell_samples: int,
    fs: int,
    collar_s: float,
    muted_mask,
) -> ExpectedTimeline:
    """Predict SHOULD-FIRE events from a per-sample 3-valued condition truth.

    Algorithm:
      1. Find runs where condition is robustly TRUE (not None and not False).
      2. The dwell counter behaves identically to the evaluator's DwellMachine:
         increment while TRUE, reset otherwise. SHOULD-FIRE at the rising edge
         where streak == dwell_samples.
      3. Mask out muted intervals (the output is suppressed there) and mark
         them DON'T-CARE with reason=PHASE_MUTED.
      4. Apply a ±collar DON'T-CARE around every condition transition (so
         literally-marginal timing doesn't get crisp assertions).
    """
    n = len(truth_per_sample)
    fire_samples: list[int] = []
    dont_care: list[DontCareInterval] = []
    streak = 0
    last_t = None
    transitions: list[int] = []
    for i, t in enumerate(truth_per_sample):
        if t is True:
            streak += 1
        else:
            streak = 0
        if streak == dwell_samples:
            fire_samples.append(i)
        if last_t is not None and t != last_t:
            transitions.append(i)
        last_t = t

    # Phase-muted intervals: collapse runs and emit DON'T-CARE intervals.
    in_muted = False
    mstart = 0
    for i, m in enumerate(muted_mask):
        if m and not in_muted:
            in_muted = True
            mstart = i
        elif not m and in_muted:
            in_muted = False
            dont_care.append(DontCareInterval(mstart, i, DontCareReason.PHASE_MUTED))
    if in_muted:
        dont_care.append(DontCareInterval(mstart, n, DontCareReason.PHASE_MUTED))

    # Drop fire events that land inside muted intervals (output suppressed).
    fire_samples = [
        s for s in fire_samples
        if not any(iv.start_sample <= s < iv.end_sample for iv in dont_care
                   if iv.reason is DontCareReason.PHASE_MUTED)
    ]

    # Collar around transitions.
    collar_samples = int(round(collar_s * fs))
    if collar_samples > 0:
        for t_idx in transitions:
            a = max(0, t_idx - collar_samples)
            b = min(n, t_idx + collar_samples)
            dont_care.append(DontCareInterval(a, b, DontCareReason.SETTLE_COLLAR))

    return ExpectedTimeline(
        should_fire_event_samples=fire_samples,
        dont_care_intervals=dont_care,
    )


__all__ = [
    "bandpass_gain_at",
    "settle_time_s",
    "tone_envelope_steady_state",
    "DontCareInterval",
    "ExpectedTimeline",
    "SHOULD_FIRE",
    "SHOULD_NOT_FIRE",
    "DONT_CARE",
    "predict_absolute_leaf_truth",
    "combine_condition_tree",
    "apply_dwell",
]
