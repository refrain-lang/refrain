# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Streaming implementations of every primitive the SMR Cz protocol uses.

Each primitive is a small class with a `step(*chunks) -> chunk` method.
State (filter coefficients, delay lines, smoother accumulator, dwell
counter, percentile window) is held on the instance and persists across
chunks. The evaluator instantiates one impl per `IRCall` in the IR and
calls `step()` for every chunk of input.

Numerical libraries:
  - scipy.signal for filter design and streaming (`butter`, `bessel`,
    `cheby2`, `sosfilt`)
  - numpy for vectorised arithmetic everywhere else

Filter families dispatch on `kind`:
  - "butterworth"  -> scipy.signal.butter
  - "bessel"       -> scipy.signal.bessel (phase-matched normalization
                      for constant group delay in the passband)
  - "chebyshev2"   -> scipy.signal.cheby2 (requires `attenuation_db`)

Hilbert family:
  - "fir"          -> direct FIR design from h[n] = 2/(π·n), Hamming
                      windowed; group delay = (N−1)/2 samples
  - "iir_allpass"  -> not yet implemented in the evaluator (deliberate;
                      the kind is recognised by the language so future
                      runtimes can supply it without spec churn)
"""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np
from scipy import signal as scisig


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class PrimitiveImpl:
    """Base class. Subclasses implement `step(*chunks) -> np.ndarray`."""

    def step(self, *chunks: np.ndarray) -> np.ndarray:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Acquisition (applied to raw multi-channel input by the evaluator)
# ---------------------------------------------------------------------------


class BipolarImpl(PrimitiveImpl):
    """`bipolar(plus, minus)` — `samples[plus] - samples[minus]`."""

    def __init__(self, *, plus: str, minus: str, channel_names: tuple[str, ...]):
        if plus not in channel_names:
            raise ValueError(f"bipolar: plus channel {plus!r} not in source")
        if minus not in channel_names:
            raise ValueError(f"bipolar: minus channel {minus!r} not in source")
        self.plus_idx = channel_names.index(plus)
        self.minus_idx = channel_names.index(minus)

    def step(self, raw_chunk: np.ndarray) -> np.ndarray:
        # raw_chunk shape: (n_samples, n_channels). Output shape: (n_samples,).
        return raw_chunk[:, self.plus_idx] - raw_chunk[:, self.minus_idx]


class ReferentialImpl(PrimitiveImpl):
    """`referential(active, reference)` — single-active-electrode referencing.

    `reference` may be:
      - a physical channel name in the source
      - `"linked_ears"` — mean of A1/A2 or M1/M2 or T9/T10 if any are present;
        falls back to common_average if no ear channels are in the source
      - `"common_average"` — mean of all channels
      - `"device"` — return the active channel as-recorded, applying no
        software re-referencing. The amp's hardware reference is already
        baked into the channel values. Used for amplifiers (e.g. BrainBit
        Flex) that have a dedicated hardware reference electrode rather
        than exposing it as a software channel.
    """

    def __init__(
        self,
        *,
        active: str,
        reference: str,
        channel_names: tuple[str, ...],
    ):
        if active not in channel_names:
            raise ValueError(f"referential: active channel {active!r} not in source")
        self.active_idx = channel_names.index(active)
        self.channel_names = channel_names
        self.reference = reference
        self.use_hardware_reference = (reference == "device")
        # ref_indices is None for "device" (no software re-referencing) and
        # for "common_average" (computed per-step over all channels).
        if self.use_hardware_reference:
            self.ref_indices: tuple[int, ...] | None = None
        else:
            self.ref_indices = self._resolve_reference(reference, channel_names)

    @staticmethod
    def _resolve_reference(
        reference: str, channel_names: tuple[str, ...]
    ) -> tuple[int, ...] | None:
        """Return the channel indices to average, or None for common-average
        (which uses all channels at step-time)."""
        if reference == "linked_ears":
            # Try the standard ear-electrode label sets.
            candidates: list[int] = []
            for name in ("A1", "A2", "M1", "M2", "T9", "T10"):
                if name in channel_names:
                    candidates.append(channel_names.index(name))
            if len(candidates) < 2:
                # Some recordings ship without explicit ear channels.
                # Fall back to common-average; the evaluator logs this.
                return None
            return tuple(candidates)
        if reference == "common_average":
            return None
        if reference in channel_names:
            return (channel_names.index(reference),)
        raise ValueError(f"referential: reference {reference!r} not in source")

    def step(self, raw_chunk: np.ndarray) -> np.ndarray:
        active = raw_chunk[:, self.active_idx]
        if self.use_hardware_reference:
            # Channel as recorded; no software subtraction.
            return active.copy()
        if self.ref_indices is None:
            # common_average (or linked_ears fallback): mean of all channels
            ref = raw_chunk.mean(axis=1)
        elif len(self.ref_indices) == 1:
            ref = raw_chunk[:, self.ref_indices[0]]
        else:
            ref = raw_chunk[:, list(self.ref_indices)].mean(axis=1)
        return active - ref


# ---------------------------------------------------------------------------
# Bandpass (with filter-family dispatch)
# ---------------------------------------------------------------------------


class BandpassImpl(PrimitiveImpl):
    """`bandpass` over a scalar stream. Supports `kind` of butterworth,
    bessel, or chebyshev2."""

    def __init__(
        self,
        *,
        band: tuple[float, float] | None = None,
        center_hz: float | None = None,
        bandwidth_ratio: float | None = None,
        order: int = 4,
        kind: str = "butterworth",
        attenuation_db: float = 40.0,
        sample_rate_hz: float,
    ):
        low, high = _resolve_band(band, center_hz, bandwidth_ratio)
        nyq = sample_rate_hz / 2.0
        if not (0 < low < high < nyq):
            raise ValueError(
                f"bandpass: band ({low}, {high}) Hz must satisfy 0 < low < high < nyquist ({nyq} Hz)"
            )
        w = [low / nyq, high / nyq]
        if kind == "butterworth":
            self.sos = scisig.butter(order, w, btype="band", output="sos")
        elif kind == "bessel":
            # `norm="phase"` gives constant group delay in the passband —
            # the property that makes Bessel attractive for NF.
            self.sos = scisig.bessel(order, w, btype="band", output="sos", norm="phase")
        elif kind == "chebyshev2":
            self.sos = scisig.cheby2(
                order, attenuation_db, w, btype="band", output="sos"
            )
        else:
            raise ValueError(f"bandpass: unknown kind {kind!r}")
        # Zero initial state (warm-up phase fills it).
        self._zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)

    def step(self, x: np.ndarray) -> np.ndarray:
        y, self._zi = scisig.sosfilt(self.sos, x, zi=self._zi, axis=0)
        return y


def _resolve_band(
    band: tuple[float, float] | None,
    center_hz: float | None,
    bandwidth_ratio: float | None,
) -> tuple[float, float]:
    """Convert either parametrisation to (low, high) edge frequencies."""
    if band is not None:
        return float(band[0]), float(band[1])
    if center_hz is None or bandwidth_ratio is None:
        raise ValueError("bandpass needs either `band` or `center` + `bandwidth`")
    sqrt_r = np.sqrt(float(bandwidth_ratio))
    return center_hz / sqrt_r, center_hz * sqrt_r


# ---------------------------------------------------------------------------
# Hilbert (FIR; IIR all-pass is grammar-accepted but not yet evaluator-impl)
# ---------------------------------------------------------------------------


class HilbertFirImpl(PrimitiveImpl):
    """`hilbert()` — analytic signal via FIR Hilbert transformer.

    Output is complex: `analytic = delayed_real + j * hilbert_imag`. The
    real branch delays the input by the FIR's group delay so the real
    and imaginary parts align in time.
    """

    def __init__(self, *, taps: int = 65, sample_rate_hz: float):
        if taps % 2 == 0:
            taps += 1  # need odd length
        N = taps
        center = N // 2
        n = np.arange(N) - center
        h = np.zeros(N, dtype=np.float64)
        # Hilbert impulse response: 2/(πn) for odd n, 0 elsewhere.
        mask = (n != 0) & (n % 2 != 0)
        h[mask] = 2.0 / (np.pi * n[mask])
        h *= np.hamming(N)
        self.h = h
        self.group_delay = center
        # Imag-branch delay line for streaming FIR convolution.
        self._imag_buf = np.zeros(N - 1, dtype=np.float64)
        # Real-branch delay line (just delays input by group_delay samples).
        self._real_buf = np.zeros(center, dtype=np.float64)

    def step(self, x: np.ndarray) -> np.ndarray:
        # Imag branch: streaming FIR with persistent state.
        imag, self._imag_buf = _fir_stream(self.h, x, self._imag_buf)
        # Real branch: just delay by group_delay.
        real, self._real_buf = _pure_delay(x, self._real_buf)
        return real + 1j * imag


class HilbertIirAllpassImpl(PrimitiveImpl):
    """`hilbert(kind="iir_allpass")` — not yet implemented in this
    evaluator. The language admits it; future evaluators may supply it."""

    def __init__(self, **_kwargs: Any):
        raise NotImplementedError(
            "hilbert(kind=\"iir_allpass\") is not yet implemented in the "
            "Phase 0d evaluator; use the default kind=\"fir\" for now"
        )


def _fir_stream(
    h: np.ndarray, x: np.ndarray, state: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Apply FIR `h` to `x` with persistent delay-line `state`.

    `state` has length len(h)-1 and holds the trailing samples from the
    previous chunk.
    """
    padded = np.concatenate([state, x])
    y = np.convolve(padded, h, mode="valid")
    new_state = padded[-(len(h) - 1):] if len(h) > 1 else state
    return y, new_state


def _pure_delay(
    x: np.ndarray, state: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Delay `x` by `len(state)` samples, carrying state across chunks."""
    combined = np.concatenate([state, x])
    new_state = combined[-len(state):] if len(state) > 0 else state
    return combined[: len(x)], new_state


# ---------------------------------------------------------------------------
# Time-series math
# ---------------------------------------------------------------------------


class MagnitudeImpl(PrimitiveImpl):
    """`magnitude()` — `|z|` for a complex stream."""

    def step(self, x: np.ndarray) -> np.ndarray:
        return np.abs(x)


class RectifyImpl(PrimitiveImpl):
    """`rectify()` — `|x|` for a real stream."""

    def step(self, x: np.ndarray) -> np.ndarray:
        return np.abs(x)


class SmoothImpl(PrimitiveImpl):
    """`smooth(tau)` — one-pole IIR low-pass.

    α = 1 − exp(−1 / (τ · rate)). State: previous output value.
    Supports live retuning of `tau` via `update_control`; the running
    output value carries across to avoid an audible step.
    """

    def __init__(self, *, tau_ms: float, sample_rate_hz: float):
        if tau_ms <= 0:
            raise ValueError("smooth: tau must be positive")
        self._sample_rate_hz = float(sample_rate_hz)
        self._set_tau(tau_ms)
        self._state = 0.0  # initialised lazily on first sample

    def _set_tau(self, tau_ms: float) -> None:
        tau_s = tau_ms / 1000.0
        self.tau_ms = tau_ms
        self.alpha = 1.0 - np.exp(-1.0 / (tau_s * self._sample_rate_hz))

    def step(self, x: np.ndarray) -> np.ndarray:
        b = np.array([self.alpha])
        a = np.array([1.0, -(1.0 - self.alpha)])
        zi = np.array([self._state * (1.0 - self.alpha)])
        y, zf = scisig.lfilter(b, a, x, zi=zi)
        self._state = float(y[-1])
        return y

    def update_control(self, target: str, value: float) -> None:
        if value > 0:
            self._set_tau(float(value))


class DifferentiateImpl(PrimitiveImpl):
    """`differentiate()` — centered finite differences."""

    def __init__(self, *, sample_rate_hz: float):
        self.dt = 1.0 / sample_rate_hz
        self._prev = 0.0
        self._prev2 = 0.0

    def step(self, x: np.ndarray) -> np.ndarray:
        # Centered: (x[t+1] - x[t-1]) / (2·dt). For streaming, we lag by 1 sample.
        ext = np.concatenate([np.array([self._prev2, self._prev]), x])
        # ext indexed as: prev2, prev, x[0], x[1], ...
        # Output y[i] = (ext[i+2] - ext[i]) / (2·dt) for i in 0..len(x)-1
        y = (ext[2:] - ext[:-2]) / (2.0 * self.dt)
        self._prev2 = float(ext[-2])
        self._prev = float(ext[-1])
        return y


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class PercentileImpl(PrimitiveImpl):
    """`percentile(target_pct, window)` — windowed percentile tracker.

    Maintains a rolling buffer of `window` samples; per chunk computes
    `numpy.percentile`. With a 2-minute window at 256 Hz that's 30,720
    samples — a single percentile call is microseconds.

    Supports live retuning of `target_pct` via `update_control` (the
    threshold percentile is the most commonly clinician-tuned parameter
    in operant NF). The buffer state survives the change unchanged.
    """

    def __init__(
        self,
        *,
        target_pct: float,
        window_ms: float,
        sample_rate_hz: float,
    ):
        if not 0 <= target_pct <= 100:
            raise ValueError("percentile: target_pct must be in [0, 100]")
        self.target_pct = target_pct
        self.window_samples = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        self._buffer: deque[float] = deque(maxlen=self.window_samples)
        # Track which control names map to which parameters, populated
        # via update_control. The map is empty until the evaluator has
        # registered control->impl deps; PercentileImpl supports
        # updating `target_pct` from any incoming control change.
        self._control_param_map: dict[str, str] = {}

    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(x.shape[0], dtype=np.float64)
        for i, v in enumerate(x):
            self._buffer.append(float(v))
            # Compute percentile over current buffer (warm-up: short buffer is OK).
            arr = np.fromiter(self._buffer, dtype=np.float64)
            out[i] = float(np.percentile(arr, self.target_pct))
        return out

    def update_control(self, target: str, value: float) -> None:
        """Set `target_pct` to a new value. Percentile is typically the
        live-tunable parameter for operant thresholds; clamp to [0, 100]
        defensively so a clinician's slider can't wedge the impl."""
        self.target_pct = float(max(0.0, min(100.0, value)))


class AutoRangeImpl(PrimitiveImpl):
    """`auto_range(window, percentile)` — rolling normalisation to [0, 1].

    Tracks `low_pct` and `high_pct` over the window, maps the signal to
    `(x - low) / (high - low)` clamped to [0, 1].
    """

    def __init__(
        self,
        *,
        window_ms: float,
        low_pct: float = 5.0,
        high_pct: float = 95.0,
        sample_rate_hz: float,
    ):
        self.window_samples = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        self.low_pct = low_pct
        self.high_pct = high_pct
        self._buffer: deque[float] = deque(maxlen=self.window_samples)

    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(x.shape[0], dtype=np.float64)
        for i, v in enumerate(x):
            self._buffer.append(float(v))
            arr = np.fromiter(self._buffer, dtype=np.float64)
            low, high = np.percentile(arr, [self.low_pct, self.high_pct])
            span = max(high - low, 1e-9)
            out[i] = float(np.clip((v - low) / span, 0.0, 1.0))
        return out


# ---------------------------------------------------------------------------
# Threshold-type "constructors" — wrap a percentile tracker / constant
# ---------------------------------------------------------------------------


class AbsoluteThresholdImpl(PrimitiveImpl):
    """`absolute(value)` — emits a constant stream."""

    def __init__(self, *, value: float):
        self.value = float(value)

    def step(self, x: np.ndarray) -> np.ndarray:
        return np.full(x.shape[0], self.value, dtype=np.float64)


class PercentileThresholdImpl(PercentileImpl):
    """Same machinery as PercentileImpl, just named for clarity in IR
    contexts where it's used as a threshold constructor."""


# ---------------------------------------------------------------------------
# Conditions (boolean output)
# ---------------------------------------------------------------------------


class AboveImpl(PrimitiveImpl):
    def step(self, signal: np.ndarray, threshold: np.ndarray) -> np.ndarray:
        return signal > threshold


class BelowImpl(PrimitiveImpl):
    def step(self, signal: np.ndarray, threshold: np.ndarray) -> np.ndarray:
        return signal < threshold


class InsideImpl(PrimitiveImpl):
    def __init__(self, *, low: float, high: float):
        self.low = float(low)
        self.high = float(high)

    def step(self, signal: np.ndarray) -> np.ndarray:
        return (signal >= self.low) & (signal <= self.high)


class AllOfImpl(PrimitiveImpl):
    def step(self, *conditions: np.ndarray) -> np.ndarray:
        if not conditions:
            return np.array([], dtype=bool)
        stacked = np.stack(conditions, axis=0)
        return np.all(stacked, axis=0)


class AnyOfImpl(PrimitiveImpl):
    def step(self, *conditions: np.ndarray) -> np.ndarray:
        if not conditions:
            return np.array([], dtype=bool)
        stacked = np.stack(conditions, axis=0)
        return np.any(stacked, axis=0)


# ---------------------------------------------------------------------------
# Dwell — event_stream with both rising-edge and .holds views
# ---------------------------------------------------------------------------


class DwellResult:
    """The two views of an event_stream that primitive instances return.

    `events` is a boolean array (True on the sample where the rising-edge
    event fires); `holds` is the boolean current-state-meets-dwell view.
    """

    __slots__ = ("events", "holds")

    def __init__(self, events: np.ndarray, holds: np.ndarray):
        self.events = events
        self.holds = holds


class DwellImpl(PrimitiveImpl):
    """`dwell(condition, duration)` state machine.

    Maintains `samples_true_streak`. When `condition` is True the counter
    increments; when False it resets to 0. `holds` is true when the
    streak meets or exceeds `dwell_samples`; the rising-edge `event`
    fires on the transition from not-holding to holding.
    """

    def __init__(self, *, duration_ms: float, sample_rate_hz: float):
        if duration_ms <= 0:
            raise ValueError("dwell: duration must be positive")
        self.dwell_samples = max(1, int(round(duration_ms / 1000.0 * sample_rate_hz)))
        self._streak = 0
        self._was_holding = False

    def step(self, condition: np.ndarray) -> DwellResult:
        n = condition.shape[0]
        events = np.zeros(n, dtype=bool)
        holds = np.zeros(n, dtype=bool)
        streak = self._streak
        was_holding = self._was_holding
        for i in range(n):
            if condition[i]:
                streak += 1
            else:
                streak = 0
            is_holding = streak >= self.dwell_samples
            holds[i] = is_holding
            if is_holding and not was_holding:
                events[i] = True
            was_holding = is_holding
        self._streak = streak
        self._was_holding = was_holding
        return DwellResult(events=events, holds=holds)


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------


class SigmoidImpl(PrimitiveImpl):
    """`sigmoid(input, midpoint, steepness)` — logistic curve.

    Stateless aside from the parameters themselves, so live retuning of
    either parameter is a one-attribute update.
    """

    def __init__(self, *, midpoint: float, steepness: float):
        self.midpoint = float(midpoint)
        self.steepness = float(steepness)

    def step(self, x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.steepness * (x - self.midpoint)))

    def update_control(self, target: str, value: float) -> None:
        # Without per-control routing we can't know whether the change
        # is for midpoint or steepness. The protocol writer wires
        # exactly one control into a sigmoid parameter, so a single
        # target maps to one slot — pick midpoint as the conventional
        # default. Phase 0e-c may refine this to per-parameter routing.
        self.midpoint = float(value)


class LinearImpl(PrimitiveImpl):
    """`linear(input, midpoint, slope)` — affine map, not clamped here."""

    def __init__(self, *, midpoint: float, slope: float):
        self.midpoint = float(midpoint)
        self.slope = float(slope)

    def step(self, x: np.ndarray) -> np.ndarray:
        return self.slope * (x - self.midpoint)


# ---------------------------------------------------------------------------
# Inhibit actions
# ---------------------------------------------------------------------------


class MuteAction:
    """`mute(release: duration)` — gate output to zero while the
    inhibit's metric is over threshold, plus a hangover of `release`
    after the metric returns below threshold.

    Not a streaming primitive in the same sense as the others — this is
    a state machine the evaluator applies at the output stage, taking
    the inhibit's boolean stream and modulating output values.
    """

    def __init__(self, *, release_ms: float, sample_rate_hz: float):
        self.release_samples = max(0, int(round(release_ms / 1000.0 * sample_rate_hz)))
        self._hangover = 0  # samples remaining of forced-mute

    def gate(self, inhibit_active: np.ndarray) -> np.ndarray:
        """Given a boolean stream of inhibit-active samples, return a
        boolean stream where True means OUTPUT-MUTED (so it's
        `inhibit_active OR within release hangover`)."""
        n = inhibit_active.shape[0]
        muted = np.zeros(n, dtype=bool)
        hangover = self._hangover
        for i in range(n):
            if inhibit_active[i]:
                hangover = self.release_samples
                muted[i] = True
            elif hangover > 0:
                hangover -= 1
                muted[i] = True
            else:
                muted[i] = False
        self._hangover = hangover
        return muted


class FreezeAction:
    """`freeze(release)` — hold output at last non-frozen value while
    inhibit-active, plus hangover. The evaluator carries the last output
    per channel."""

    def __init__(self, *, release_ms: float, sample_rate_hz: float):
        self.release_samples = max(0, int(round(release_ms / 1000.0 * sample_rate_hz)))
        self._hangover = 0

    def gate(self, inhibit_active: np.ndarray) -> np.ndarray:
        """Same as MuteAction.gate: True means apply the action."""
        n = inhibit_active.shape[0]
        active = np.zeros(n, dtype=bool)
        hangover = self._hangover
        for i in range(n):
            if inhibit_active[i]:
                hangover = self.release_samples
                active[i] = True
            elif hangover > 0:
                hangover -= 1
                active[i] = True
        self._hangover = hangover
        return active


class FlagAction:
    """`flag()` — telemetry-only inhibit; never modifies output."""

    def gate(self, inhibit_active: np.ndarray) -> np.ndarray:
        return np.zeros_like(inhibit_active, dtype=bool)


# ---------------------------------------------------------------------------
# Bandpower (used as inhibit metrics, occasionally as derive inputs)
# ---------------------------------------------------------------------------


class BandpowerImpl(PrimitiveImpl):
    """`bandpower(input, band, window)` — sliding-window total power
    in a frequency band.

    Implementation: a band-limited Butterworth bandpass followed by a
    sliding-window RMS computation. Welch's method would be more
    accurate; this is the cheap streaming approximation that clinical
    NF software typically uses for EMG-style artifact gates.
    """

    def __init__(
        self,
        *,
        band: tuple[float, float],
        window_ms: float,
        sample_rate_hz: float,
    ):
        low, high = float(band[0]), float(band[1])
        nyq = sample_rate_hz / 2.0
        high = min(high, nyq * 0.99)
        if low >= high:
            raise ValueError(f"bandpower: empty band ({low}, {high}) Hz")
        self.sos = scisig.butter(
            4, [low / nyq, high / nyq], btype="band", output="sos"
        )
        self._zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)
        self.window_samples = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        self._buffer: deque[float] = deque(maxlen=self.window_samples)

    def step(self, x: np.ndarray) -> np.ndarray:
        # Filter to the band, then compute squared values; sliding-window
        # mean = power. Returned in the input unit's square.
        filtered, self._zi = scisig.sosfilt(self.sos, x, zi=self._zi, axis=0)
        out = np.empty(x.shape[0], dtype=np.float64)
        sq = filtered ** 2
        for i, v in enumerate(sq):
            self._buffer.append(float(v))
            out[i] = float(np.mean(self._buffer))
        return out


# ---------------------------------------------------------------------------
# Coherence — magnitude-squared coherence between two streams
# ---------------------------------------------------------------------------


class CoherenceImpl(PrimitiveImpl):
    """`coherence(input_a, input_b, band, window)` — band-averaged
    magnitude-squared coherence (MSC) between two streams.

    Implementation: streaming Welch's method via `scipy.signal.coherence`.
    Two `deque(maxlen=window_samples)` buffers hold the trailing window of
    each input. Per chunk, samples are appended; once enough data is
    present (≥ 2 segments), MSC is computed over the buffer, averaged
    over the requested frequency band, broadcast across the chunk's
    samples as a single scalar.

    Why per-chunk-constant: Welch is O(N log N) per segment; per-sample
    update would be 64× more expensive than necessary. Clinical coherence
    training has seconds-scale dynamics, so chunk-cadence (4 Hz at 64-
    sample chunks @ 256 Hz) is plenty.

    Critical math note: a single-segment Welch returns MSC ≡ 1.0 for
    any input — the formula `|Pxy|^2 = Pxx * Pyy` reduces trivially with
    one segment. This impl forces `nperseg ≤ window_samples / 2`
    (typically ~250 ms segments) so multi-segment averaging gives a
    real coherence estimate.

    Frequency resolution = `sample_rate / nperseg` ≈ 4 Hz with default
    settings, which is coarse for narrow clinical bands; use a longer
    `window` (e.g. 2 s with the same nperseg gives more segments rather
    than finer frequency bins) for tighter band averaging.
    """

    def __init__(
        self,
        *,
        band: tuple[float, float],
        window_ms: float,
        sample_rate_hz: float,
    ):
        low, high = float(band[0]), float(band[1])
        nyq = sample_rate_hz / 2.0
        if not (0 <= low < high <= nyq):
            raise ValueError(
                f"coherence: band ({low}, {high}) Hz must satisfy "
                f"0 <= low < high <= nyquist ({nyq} Hz)"
            )
        self.band = (low, high)
        self.sample_rate_hz = float(sample_rate_hz)
        self.window_samples = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        # 250 ms segments by default. nperseg MUST be < window_samples
        # for multi-segment Welch to produce meaningful MSC.
        self.nperseg = max(8, int(sample_rate_hz // 4))
        min_window_ms = 2 * self.nperseg * 1000.0 / sample_rate_hz
        if self.window_samples < self.nperseg * 2:
            raise ValueError(
                f"coherence: window must be ≥ {min_window_ms:.0f} ms at this "
                f"sample rate (got {window_ms} ms). A single-segment Welch "
                f"returns MSC = 1.0 trivially and cannot be used."
            )
        self.noverlap = self.nperseg // 2
        # We need enough samples for ≥ 2 segments before MSC is
        # meaningful — single-segment Welch returns 1.0 trivially.
        self._min_samples = self.nperseg + (self.nperseg - self.noverlap)
        self._buf_a: deque[float] = deque(maxlen=self.window_samples)
        self._buf_b: deque[float] = deque(maxlen=self.window_samples)

    def step(self, x_a: np.ndarray, x_b: np.ndarray) -> np.ndarray:
        n = x_a.shape[0]
        if x_b.shape[0] != n:
            raise ValueError(
                f"coherence: input_a and input_b chunk sizes differ "
                f"({n} vs {x_b.shape[0]})"
            )
        self._buf_a.extend(x_a.tolist())
        self._buf_b.extend(x_b.tolist())
        if len(self._buf_a) < self._min_samples:
            # Warm-up: not enough samples for multi-segment MSC. Match
            # BandpowerImpl's "return 0.0 during warm-up" convention.
            return np.zeros(n, dtype=np.float64)
        a = np.fromiter(self._buf_a, dtype=np.float64, count=len(self._buf_a))
        b = np.fromiter(self._buf_b, dtype=np.float64, count=len(self._buf_b))
        with np.errstate(invalid="ignore", divide="ignore"):
            f, Cxy = scisig.coherence(
                a, b,
                fs=self.sample_rate_hz,
                nperseg=self.nperseg,
                noverlap=self.noverlap,
                window="hann",
            )
        # Constant or all-zero inputs produce NaN entries (divide by
        # Pxx=0 or Pyy=0). Coerce to 0 so downstream comparisons behave.
        Cxy = np.nan_to_num(Cxy, nan=0.0, posinf=0.0, neginf=0.0)
        mask = (f >= self.band[0]) & (f <= self.band[1])
        if not mask.any():
            msc = 0.0
        else:
            msc = float(np.clip(np.mean(Cxy[mask]), 0.0, 1.0))
        return np.full(n, msc, dtype=np.float64)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_filter_impl(
    callee: str,
    static_args: dict[str, Any],
    *,
    sample_rate_hz: float,
    channel_names: tuple[str, ...] = (),
) -> PrimitiveImpl:
    """Instantiate the right impl class for a primitive callee + args.

    `static_args` is the dict of compile-time-knowable parameters
    extracted from the IRCall (number values, string values, tuples).
    `sample_rate_hz` and `channel_names` come from the source.
    """
    if callee == "bipolar":
        return BipolarImpl(
            plus=static_args["plus"],
            minus=static_args["minus"],
            channel_names=channel_names,
        )
    if callee == "referential":
        return ReferentialImpl(
            active=static_args["active"],
            reference=static_args["reference"],
            channel_names=channel_names,
        )
    if callee == "bandpass":
        return BandpassImpl(
            band=static_args.get("band"),
            center_hz=static_args.get("center"),
            bandwidth_ratio=static_args.get("bandwidth"),
            order=int(static_args.get("order", 4)),
            kind=static_args.get("kind", "butterworth"),
            attenuation_db=float(static_args.get("attenuation_db", 40)),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "hilbert":
        kind = static_args.get("kind", "fir")
        if kind == "fir":
            return HilbertFirImpl(
                taps=int(static_args.get("taps", 65)),
                sample_rate_hz=sample_rate_hz,
            )
        if kind == "iir_allpass":
            return HilbertIirAllpassImpl()
        raise ValueError(f"hilbert: unknown kind {kind!r}")
    if callee == "magnitude":
        return MagnitudeImpl()
    if callee == "rectify":
        return RectifyImpl()
    if callee == "smooth":
        return SmoothImpl(
            tau_ms=static_args["tau_ms"],
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "differentiate":
        return DifferentiateImpl(sample_rate_hz=sample_rate_hz)
    if callee == "percentile":
        return PercentileImpl(
            target_pct=float(static_args["target_pct"]),
            window_ms=float(static_args["window_ms"]),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "auto_range":
        low, high = static_args.get("percentile", (5.0, 95.0))
        return AutoRangeImpl(
            window_ms=float(static_args["window_ms"]),
            low_pct=float(low),
            high_pct=float(high),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "absolute":
        return AbsoluteThresholdImpl(value=float(static_args["value"]))
    if callee == "above":
        return AboveImpl()
    if callee == "below":
        return BelowImpl()
    if callee == "inside":
        return InsideImpl(
            low=float(static_args["low"]),
            high=float(static_args["high"]),
        )
    if callee == "all_of":
        return AllOfImpl()
    if callee == "any_of":
        return AnyOfImpl()
    if callee == "dwell":
        return DwellImpl(
            duration_ms=float(static_args["duration_ms"]),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "sigmoid":
        return SigmoidImpl(
            midpoint=float(static_args["midpoint"]),
            steepness=float(static_args["steepness"]),
        )
    if callee == "linear":
        return LinearImpl(
            midpoint=float(static_args["midpoint"]),
            slope=float(static_args["slope"]),
        )
    if callee == "bandpower":
        return BandpowerImpl(
            band=tuple(static_args["band"]),
            window_ms=float(static_args["window_ms"]),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "coherence":
        return CoherenceImpl(
            band=tuple(static_args["band"]),
            window_ms=float(static_args.get("window_ms", 1000.0)),
            sample_rate_hz=sample_rate_hz,
        )
    raise NotImplementedError(f"no evaluator impl for primitive {callee!r}")


def make_action(
    callee: str,
    static_args: dict[str, Any],
    *,
    sample_rate_hz: float,
):
    """Instantiate an inhibit-action object (mute/freeze/flag)."""
    if callee == "mute":
        return MuteAction(
            release_ms=float(static_args.get("release_ms", 200.0)),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "freeze":
        return FreezeAction(
            release_ms=float(static_args.get("release_ms", 200.0)),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "flag":
        return FlagAction()
    raise NotImplementedError(f"no action impl for {callee!r}")


__all__ = [
    "AboveImpl",
    "AbsoluteThresholdImpl",
    "AllOfImpl",
    "AnyOfImpl",
    "AutoRangeImpl",
    "BandpassImpl",
    "BandpowerImpl",
    "BelowImpl",
    "BipolarImpl",
    "CoherenceImpl",
    "DifferentiateImpl",
    "DwellImpl",
    "DwellResult",
    "FlagAction",
    "FreezeAction",
    "HilbertFirImpl",
    "HilbertIirAllpassImpl",
    "InsideImpl",
    "LinearImpl",
    "MagnitudeImpl",
    "MuteAction",
    "PercentileImpl",
    "PercentileThresholdImpl",
    "PrimitiveImpl",
    "RectifyImpl",
    "ReferentialImpl",
    "SigmoidImpl",
    "SmoothImpl",
    "make_action",
    "make_filter_impl",
]
