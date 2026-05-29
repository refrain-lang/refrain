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

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from scipy.signal import freqz_sos

from .scenario import DontCareReason, Tone
from .surface import ConditionLeaf, ConditionNode

if TYPE_CHECKING:
    from .scenario import Scenario
    from .surface import LogicalSurface

# Evaluator chunk granularity (samples/step) that `refrain run` uses; the
# oracle quantises its settle collar to the same step.
_DEFAULT_CHUNK_SAMPLES = 64


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


def _muted_intervals(muted_mask: Sequence[bool], n: int) -> list[DontCareInterval]:
    """Collapse a per-sample muted mask into PHASE_MUTED DON'T-CARE intervals."""
    intervals: list[DontCareInterval] = []
    in_muted = False
    mstart = 0
    for i, m in enumerate(muted_mask):
        if m and not in_muted:
            in_muted = True
            mstart = i
        elif not m and in_muted:
            in_muted = False
            intervals.append(DontCareInterval(mstart, i, DontCareReason.PHASE_MUTED))
    if in_muted:
        intervals.append(DontCareInterval(mstart, n, DontCareReason.PHASE_MUTED))
    return intervals


def apply_dwell(
    truth_per_sample: Sequence[bool | None],
    *,
    dwell_samples: int,
    fs: int,
    collar_s: float,
    muted_mask: Sequence[bool],
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
      5. Collar deferral: a SHOULD-FIRE whose rising edge lands inside a
         settle-collar interval is dropped (its timing is untrustworthy
         there) and re-asserted at the collar's end iff dwell is still
         satisfied at that point. So a fire's asserted sample may be shifted
         later than its raw rising edge when collar_s > 0.
    """
    n = len(truth_per_sample)
    fire_samples: list[int] = []
    dont_care: list[DontCareInterval] = []
    streak = 0
    last_t = None
    transitions: list[int] = []
    streak_at: list[int] = [0] * n
    for i, t in enumerate(truth_per_sample):
        if t is True:
            streak += 1
        else:
            streak = 0
        streak_at[i] = streak
        if streak == dwell_samples:
            fire_samples.append(i)
        if last_t is not None and t != last_t:
            transitions.append(i)
        last_t = t

    # Phase-muted intervals: the output is suppressed there → DON'T-CARE.
    dont_care.extend(_muted_intervals(muted_mask, n))

    # Drop fire events that land inside muted intervals (output suppressed).
    # O(F×M) scan — fine for v1 (few events, ≤handful of intervals); if either
    # grows large, replace with a sorted-interval merge.  # TODO(perf)
    fire_samples = [
        s for s in fire_samples
        if not any(iv.start_sample <= s < iv.end_sample for iv in dont_care
                   if iv.reason is DontCareReason.PHASE_MUTED)
    ]

    # Collar around transitions: the settle window after a condition flip is
    # untrustworthy, so timing of any event there is not asserted.
    collar_samples = int(round(collar_s * fs))
    collar_ivals: list[tuple[int, int]] = []
    if collar_samples > 0:
        for t_idx in transitions:
            a = max(0, t_idx - collar_samples)
            b = min(n, t_idx + collar_samples)
            collar_ivals.append((a, b))
            dont_care.append(DontCareInterval(a, b, DontCareReason.SETTLE_COLLAR))

    # A SHOULD-FIRE whose timing lands inside a collar can't be crisply
    # asserted there. Drop it, but if the condition is still robustly TRUE
    # (dwell already satisfied) at the sample where the collar clears, assert
    # the event there instead — the evaluator must fire once the signal is
    # trustworthy and the dwell has elapsed.
    if collar_ivals:
        kept: list[int] = []
        for s in fire_samples:
            covering = [(a, b) for (a, b) in collar_ivals if a <= s < b]
            if not covering:
                kept.append(s)
                continue
            # Defer to the end of the latest collar covering this fire.
            collar_end = max(b for (_, b) in covering)
            if collar_end < n and streak_at[collar_end] >= dwell_samples:
                kept.append(collar_end)
        fire_samples = kept

    return ExpectedTimeline(
        should_fire_event_samples=sorted(set(fire_samples)),
        dont_care_intervals=dont_care,
    )


def predict(scenario: Scenario, surface: LogicalSurface) -> ExpectedTimeline:
    """Predict the 3-valued expected event timeline for a Scenario.

    Wires together every prior piece: per-derive envelope-over-time, 3-valued
    threshold leaves (absolute via analytic margin, percentile via ordinal
    rank over a rolling window), condition-tree combination, dwell, phase
    muting, and pre-window-fill DON'T-CARE. NEVER calls the evaluator.
    """
    fs = surface.sample_rate_hz
    n_samples = int(round(scenario.duration_s * fs))
    chunk_s = _DEFAULT_CHUNK_SAMPLES / fs

    # Step 1: per-derive predicted envelope-over-time (piecewise constant).
    env_per_derive = {
        d.name: _predicted_envelope_timeline(d, scenario, n_samples, fs)
        for d in surface.derives
    }

    # Step 2: per-sample 3-valued truth of each threshold leaf.
    leaf_truth: dict[tuple[str, str], list[bool | None]] = {}
    for thr in surface.thresholds:
        env = env_per_derive[thr.signal]
        leaf_truth[(thr.signal, thr.name)] = _leaf_truth_timeline(
            env=env, thr=thr, fs=fs,
        )

    # Step 3: combine through the condition tree, sample by sample.
    truth_per_sample = _walk_condition(surface.reward_condition, leaf_truth, n_samples)

    # Step 4: phase muting mask.
    muted_mask = _muted_mask(scenario, surface, n_samples, fs)

    # Step 5: dwell + collar.
    dwell_samples = int(round(surface.dwell_ms / 1000.0 * fs))
    settle_candidates = [
        settle_time_s(sos=d.sos, tau_s=(d.smooth_tau_ms or 0.0) / 1000.0,
                      chunk_s=chunk_s, fs=fs)
        for d in surface.derives if d.sos is not None
    ]
    collar_s = max(settle_candidates) if settle_candidates else 0.0
    timeline = apply_dwell(
        truth_per_sample,
        dwell_samples=dwell_samples,
        fs=fs,
        collar_s=collar_s,
        muted_mask=muted_mask,
    )

    # Step 6: merge in pre-window-fill DON'T-CARE intervals for percentile thresholds.
    timeline = _add_pre_fill_dont_care(timeline, surface, fs, n_samples)

    return timeline


def _predicted_envelope_timeline(derive, scenario, n_samples, fs) -> list[float]:
    """Piecewise envelope: noise-floor baseline + tone contribution where any
    BandSegment overlaps the derive's band on the derive's channel.

    v1 simplification: when multiple segments overlap a band, the strongest
    Tone's envelope is taken; BandNoise contributions are NOT predicted as
    absolute values (only their rank is used downstream)."""
    env = [_noise_floor_envelope(derive, fs)] * n_samples
    if derive.sos is None:
        return env
    for seg in scenario.segments:
        if seg.channel != derive.channel:
            continue
        if seg.band[1] < derive.band[0] or seg.band[0] > derive.band[1]:
            continue
        if isinstance(seg.content, Tone):
            steady = tone_envelope_steady_state(
                derive.sos, freq_hz=seg.center_hz,
                amplitude_uv=seg.content.amplitude_uv, fs=fs,
            )
            a = int(round(seg.start_s * fs))
            b = int(round(seg.end_s * fs))
            for i in range(max(0, a), min(n_samples, b)):
                env[i] = max(env[i], steady)
    return env


def _noise_floor_envelope(derive, fs) -> float:
    """Coarse estimate of the in-band envelope of pink noise. Concrete numbers
    don't matter for v1 because scenarios use clear margins."""
    return 2.0


def _leaf_truth_timeline(*, env: list[float], thr, fs: int) -> list[bool | None]:
    """Per-sample 3-valued truth of one threshold leaf."""
    if thr.kind == "absolute":
        margin = max(1.0, 0.20 * thr.absolute_uv)
        return [
            predict_absolute_leaf_truth(env=e, threshold=thr.absolute_uv,
                                        margin=margin, op="above")
            for e in env
        ]
    window_samples = int(round(thr.percentile_window_ms / 1000.0 * fs))
    return _ordinal_percentile_truth(env, thr, window_samples)


def _ordinal_percentile_truth(env: list[float], thr, window_samples: int) -> list[bool | None]:
    """Rank-based 3-valued truth for a percentile threshold.

    At each sample i (with i >= window_samples), compute the sample's rank
    within env[i-window_samples : i]:
        rank = 100 * (#strictly-less elements) / window_samples
    rank > target+margin -> TRUE; rank < target-margin -> FALSE; else DON'T-CARE.
    Pre-fill (i < window_samples) -> DON'T-CARE.

    Implemented with an incremental sorted trailing window (bisect) so the
    cost is O(n log w) rather than O(n * w); this is semantically IDENTICAL to
    a naive per-sample `(window < x).sum()` count — it just avoids rescanning
    the whole window each step (critical for the 2-min / 30 720-sample window).
    """
    rank_margin = 15.0
    target = thr.percentile_target
    n = len(env)
    out: list[bool | None] = [None] * n
    if window_samples <= 0 or window_samples >= n:
        return out
    sorted_window: list[float] = sorted(env[:window_samples])
    for i in range(window_samples, n):
        x = env[i]
        # #elements strictly less than x in the trailing window.
        less = bisect.bisect_left(sorted_window, x)
        rank = less / window_samples * 100.0
        if rank > target + rank_margin:
            out[i] = True
        elif rank < target - rank_margin:
            out[i] = False
        else:
            out[i] = None
        # Slide the window: drop env[i-window_samples], add env[i].
        old = env[i - window_samples]
        del sorted_window[bisect.bisect_left(sorted_window, old)]
        bisect.insort(sorted_window, x)
    return out


def _walk_condition(node, leaf_truth, n_samples) -> list[bool | None]:
    if isinstance(node, ConditionLeaf):
        return [
            _flip_for_op(node.op, leaf_truth[(node.signal, node.threshold)][i])
            for i in range(n_samples)
        ]
    assert isinstance(node, ConditionNode)
    kid_truths = [_walk_condition(c, leaf_truth, n_samples) for c in node.children]
    return [
        combine_condition_tree(node.op, [kt[i] for kt in kid_truths])
        for i in range(n_samples)
    ]


def _flip_for_op(op: str, t: bool | None) -> bool | None:
    """Leaf op is `above` or `below`; leaf_truth was computed for `above`."""
    if t is None:
        return None
    if op == "above":
        return t
    if op == "below":
        return not t
    raise ValueError(op)


def _muted_mask(scenario, surface, n_samples: int, fs: int) -> list[bool]:
    """Boolean mask of samples where output is muted. Uses scenario.phase_override
    if given, else surface.phases."""
    mask = [False] * n_samples
    if scenario.phase_override is not None:
        po = scenario.phase_override
        durations = [(po.warmup_s, True), (po.training_s, False), (po.cooldown_s, True)]
    else:
        durations = [(p.duration_s, p.output_muted) for p in surface.phases]
    i = 0
    for dur_s, is_muted in durations:
        j = min(n_samples, i + int(round(dur_s * fs)))
        for k in range(i, j):
            mask[k] = is_muted
        i = j
    return mask


def _add_pre_fill_dont_care(timeline, surface, fs: int, n_samples: int) -> ExpectedTimeline:
    """For each percentile threshold, mark [0, window_samples) DON'T-CARE with
    reason PRE_WINDOW_FILL (use the longest window). Drop SHOULD-FIRE samples
    landing in the pre-fill region."""
    longest = 0
    for thr in surface.thresholds:
        if thr.kind == "percentile":
            w = int(round(thr.percentile_window_ms / 1000.0 * fs))
            longest = max(longest, w)
    if longest <= 0:
        return timeline
    end = min(n_samples, longest)
    new_dc = list(timeline.dont_care_intervals)
    new_dc.append(DontCareInterval(0, end, DontCareReason.PRE_WINDOW_FILL))
    fires = [s for s in timeline.should_fire_event_samples if s >= end]
    return ExpectedTimeline(should_fire_event_samples=fires, dont_care_intervals=new_dc)


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
    "predict",
]
