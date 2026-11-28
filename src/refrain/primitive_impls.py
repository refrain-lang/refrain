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

import math
from collections import deque
from typing import Any

import numpy as np
from scipy import linalg as scilinalg
from scipy import signal as scisig
from scipy import special as scispecial


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class PrimitiveImpl:
    """Base class. Subclasses implement `step(*chunks) -> np.ndarray`."""

    def step(self, *chunks: np.ndarray) -> np.ndarray:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Warm-starting IIR filter state
# ---------------------------------------------------------------------------
#
# Every IIR stage used to begin with zeroed state, which means its output
# starts at 0 and climbs to wherever the signal actually is. For zero-mean
# EEG that climb is short and small. For a signal with a standing offset —
# skin temperature sitting at 33 °C, skin conductance, a respiration belt —
# the filter has to settle through the whole standing level first, so a
# "phasic" derive written as `signal - smooth(signal)` reports most of the
# tonic level as a phantom response for as long as the settling lasts.
#
# Instead, each stage seeds its state on its very first sample to the steady
# state that sample's level would produce. A constant input then yields that
# input's steady-state response immediately, with no transient at all. The
# first sample is the only information available at that point, which is why
# the seed is scaled by it.
#
# `_sos_unit_zi` is the state for a unit-step input; multiply by the first
# sample. It is `scipy.signal.sosfilt_zi`, kept as a closed form so the Rust
# core (refrain-core/src/dsp.rs) can compute the same numbers from the same
# baked `sos` without a linear-algebra dependency — the two engines have to
# warm-start identically or the drift gate fails.


def _sos_unit_zi(sos: np.ndarray) -> np.ndarray:
    """`scipy.signal.sosfilt_zi(sos)` — the `sosfilt` state that holds a
    unit-step input at its steady-state response.

    Written out rather than called so `refrain-core` can mirror it exactly:
    per second-order section, `lfilter_zi` reduces to a 2x2 solve
    `[[1 + a1, -1], [a2, 1]] @ zi = [b1 - b0*a1, b2 - b0*a2]`, and the
    sections compose by carrying each one's DC gain `H(1)` forward as a
    scale.

    `det` is that DC gain's denominator, so it is tiny whenever a pole sits
    near DC (4.4e-5 for a 0.0159 Hz corner at 15 Hz) and the solve is badly
    conditioned there: no two correct implementations of this agree better
    than about `eps / det`, which is why
    `tests/test_primitive_impls.py::test_sos_unit_zi_matches_scipy` derives
    its tolerance from `det` rather than fixing one. The conditioning is in
    the state and not in the behaviour — seeded and run, the filter sits at
    `x[0] * H(1)` to 1e-15 relative, which is what
    `test_warm_start_holds_a_standing_level` pins.
    """
    sos = np.asarray(sos, dtype=np.float64)
    zi = np.empty((sos.shape[0], 2), dtype=np.float64)
    scale = 1.0
    for s in range(sos.shape[0]):
        b0, b1, b2, a0, a1, a2 = (float(v) for v in sos[s])
        b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
        rhs0 = b1 - b0 * a1
        rhs1 = b2 - b0 * a2
        det = 1.0 + a1 + a2
        zi[s, 0] = scale * (rhs0 + rhs1) / det
        zi[s, 1] = scale * ((1.0 + a1) * rhs1 - a2 * rhs0) / det
        scale *= (b0 + b1 + b2) / det
    return zi


class _WarmStartSos:
    """Mixin: one-shot seeding of `sosfilt` state from the first sample.

    Holds `_zi` (live state), `_unit_zi` (the unit-step steady state) and
    `_primed`. Call `_warm_start(x)` at the top of `step` before filtering.
    """

    _zi: np.ndarray
    _unit_zi: np.ndarray
    _primed: bool

    def _init_warm_start(self, sos: np.ndarray) -> None:
        self._unit_zi = _sos_unit_zi(sos)
        self._zi = np.zeros((np.asarray(sos).shape[0], 2), dtype=np.float64)
        self._primed = False

    def _warm_start(self, x: np.ndarray) -> None:
        if not self._primed and x.shape[0]:
            self._zi = self._unit_zi * float(x[0])
            self._primed = True


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


# The `reference` slot is overloaded: besides an electrode name it accepts
# these re-referencing schemes, which are resolved against whatever channels
# the source carries (see ReferentialImpl._resolve_reference). They are not
# electrodes, so a source must never be asked to produce a channel named after
# one — `refrain.synthetic.channels_for_synthetic` filters on this set.
REFERENCE_KEYWORDS = frozenset({"linked_ears", "common_average", "device"})


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


class PassthroughImpl(PrimitiveImpl):
    """`passthrough()` — first-class identity montage.

    Emits the source's single channel verbatim, with no software
    re-referencing. The sanctioned replacement for the
    `referential(reference="device")` single-channel workaround. Requires
    exactly one source channel (for a multi-channel source, name the
    channel with `referential`/`bipolar` instead)."""

    def __init__(self, *, channel_names: tuple[str, ...]):
        if len(channel_names) != 1:
            raise ValueError(
                "passthrough() requires a single-channel source "
                f"(got {len(channel_names)} channels: {list(channel_names)}); "
                "use referential/bipolar to select a channel from a multi-channel source"
            )
        self.channel_names = channel_names

    def step(self, raw_chunk: np.ndarray) -> np.ndarray:
        # raw_chunk shape: (n_samples, 1). Output shape: (n_samples,).
        return raw_chunk[:, 0]


# ---------------------------------------------------------------------------
# Bandpass (with filter-family dispatch)
# ---------------------------------------------------------------------------


class BandpassImpl(_WarmStartSos, PrimitiveImpl):
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
        # Warm-started on the first sample (see `_sos_unit_zi`): the output
        # is the steady-state response to that level straight away instead of
        # ringing its way there. For a Butterworth or Bessel bandpass that
        # response is ~0, because they reject DC outright; a `chebyshev2`
        # keeps `10 ** (-attenuation_db / 20)` of it (0.01 at the default
        # 40 dB), which is the filter doing what it was asked to.
        self._init_warm_start(self.sos)

    def step(self, x: np.ndarray) -> np.ndarray:
        self._warm_start(x)
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
    """`hilbert(kind="iir_allpass")` — intentionally not implemented.

    A low-latency two-all-pass IIR Hilbert is accurate only mid-band; near DC
    it collapses to ~0 dB image rejection (see docs/DESIGN-NOTES.md §"IIR
    Hilbert"). Every realistic NF/HRV band sits near DC at typical sample
    rates (EEG 4-20 Hz @256 Hz, HRV 0.04-0.15 Hz @4 Hz), so this path cannot
    deliver a usable low-latency analytic envelope. Use the documented
    alternatives instead."""

    def __init__(self, **_kwargs: Any):
        raise NotImplementedError(
            'hilbert(kind="iir_allpass") is intentionally not implemented: a '
            "low-latency IIR Hilbert is inaccurate near DC, where NF/HRV bands "
            "sit. For a low-sample-rate (e.g. 4 Hz) envelope use rectify() + "
            'smooth(tau); otherwise use the default hilbert(kind="fir"). '
            "See docs/DESIGN-NOTES.md."
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
# Band envelope (one stage, one declared delay)
# ---------------------------------------------------------------------------

# The filter span must be at least 2x the declared delay or envelope accuracy
# collapses (measured on real EEG at 256 Hz with a 360 ms delay, 12-15 Hz:
# r=0.873 at 2x, 0.743 at 1.75x, 0.437 at 1.4x). 2x is the cliff edge, so the
# auto-sized default keeps a margin above it. 2.2x puts a 350 ms delay at
# 250 Hz on 195 taps, essentially the 193 that `ResourceBudget(state_kb=8)`
# was measured at (4.5 KB: complex coefficients plus a real delay line). State
# scales with latency x sample rate, so a long delay on a fast amp exceeds
# that figure; the declared budget covers the recommended 250-350 ms range at
# EEG rates.
#
# Sizing from the delay rather than fixing a tap count is deliberate: the
# declared latency is wall-clock, so the same protocol source must work on a
# 250 Hz and a 500 Hz amp. A fixed 193 taps would carry 384 ms at 250 Hz but
# only 192 ms at 500 Hz, and the protocol would be refused on the faster amp.
BAND_ENVELOPE_SIZING_MARGIN = 2.2

# A declared delay shorter than this cannot be tapered around (see __init__).
BAND_ENVELOPE_MIN_DELAY_SAMPLES = 3


# `band_envelope(kind:)`. "tapered" is the design every protocol written before
# the argument existed gets: exactly linear phase, no ringing, a deep far
# stopband, and wide transition skirts. "weighted" trades those three for a
# sharp transition at a short declared delay. See `BandEnvelopeImpl`.
BAND_ENVELOPE_KINDS = ("tapered", "weighted")

# The weighted design's model of what it will be fed: power falling as 1/f^2
# above a 1 Hz knee, on a white floor that meets it at 20 Hz. The 1/f^2 term is
# what EEG looks like and is where the accuracy comes from — it spends the
# filter's stopband on delta, theta and alpha, where the power is. The floor is
# what keeps it safe: without it the design buys low-frequency rejection with
# high-frequency rejection it then does not have, and 20 uV of mains took the
# 12-16 Hz envelope at 160 ms from r=0.943 to 0.872 (worst recording 0.792).
# With it, 0.925 (0.890) under the same mains against 0.942 (0.912) clean. It
# also bounds the weight's dynamic range at ~400:1, so the normal equations are
# well conditioned and the taps reproduce across architectures.
BAND_ENVELOPE_WEIGHT_KNEE_HZ = 1.0
BAND_ENVELOPE_WEIGHT_FLOOR_HZ = 20.0

# Taps kept after the delay tap, in periods of the bandwidth (1/B seconds).
# They are the filter's memory, and unlike the taps before the delay they cost
# no latency. Three periods is where the measured curve stops moving: against a
# Butterworth reference 2 reads marginally better and against a brick-wall one
# 4 does, by under 0.01 either way, which is the reference's edge shape talking
# rather than the filter. Below one period the kernel's main lobe is cut.
BAND_ENVELOPE_WEIGHTED_TAIL_PERIODS = 3.0
BAND_ENVELOPE_WEIGHTED_MIN_TAIL_PERIODS = 1.0

# Below half a bandwidth-period the weighted design cannot realize the delay it
# is asked for: the lag measured on real EEG ran 25-50 ms over the declaration
# at 0.4-0.5 periods (12-15 Hz at 100-160 ms; 3-5 Hz at 200 ms), 12-16 ms over
# at 0.64, and within 4-8 ms from 0.8 up. A declared latency the output does
# not have is the failure this primitive exists to prevent, so it is refused.
BAND_ENVELOPE_WEIGHTED_MIN_LATENCY_PERIODS = 0.5


def _weighted_band_fir(
    low: float, high: float, delay: int, n_taps: int, sample_rate_hz: float
) -> np.ndarray:
    """Weighted least-squares complex FIR for `band_envelope(kind: "weighted")`.

    Solves Smetanin et al. (2020) eq. 10-11,

        b = argmin  integral S(f) |B(f) - H_D(f)|^2 df,

    with `H_D = 2 e^{-j 2 pi f D / fs}` on [low, high] and 0 elsewhere, over the
    whole of [-fs/2, fs/2] so the negative frequencies are stopband and the
    output is analytic. The paper takes `S` from the subject's own spectrum;
    here it is a fixed model, `1/max(|f|, knee)^2 + 1/floor^2`, because a
    measured spectrum is not available when the taps bake and, measured, buys
    nothing a model does not (see the class docstring).

    The normal equations are `R b = p` with `R[m, n] = r[m - n]`,

        r[k] = integral S(f) e^{ j 2 pi f k / fs} df            over [-fs/2, fs/2]
        p[m] = 2 * integral S(f) e^{ j 2 pi f (m - D) / fs} df  over [low, high]

    Both are evaluated in closed form through the sine and cosine integrals,
    for the reason the tapered design is: on a frequency grid a narrow band
    lands on a handful of points and the taps then move with the grid.
    """
    fs = float(sample_rate_hz)
    knee = BAND_ENVELOPE_WEIGHT_KNEE_HZ
    floor = 1.0 / BAND_ENVELOPE_WEIGHT_FLOOR_HZ**2
    nyq = fs / 2.0

    def exp_over_f2(alpha: np.ndarray, f1: float, f2: float) -> np.ndarray:
        """integral_{f1}^{f2} e^{j alpha f} / f^2 df, elementwise in alpha; 0 < f1 < f2."""
        out = np.full(alpha.shape, 1.0 / f1 - 1.0 / f2, dtype=np.complex128)
        nz = alpha != 0.0
        a = alpha[nz]
        mag = np.abs(a)
        si2, ci2 = scispecial.sici(mag * f2)
        si1, ci1 = scispecial.sici(mag * f1)
        # d/df [Ci(|a| f) + j sgn(a) Si(|a| f)] = e^{j a f} / f
        e_int = (ci2 - ci1) + 1j * np.sign(a) * (si2 - si1)
        out[nz] = (
            -np.exp(1j * a * f2) / f2 + np.exp(1j * a * f1) / f1 + 1j * a * e_int
        )
        return out

    def exp_flat(alpha: np.ndarray, f1: float, f2: float) -> np.ndarray:
        """integral_{f1}^{f2} e^{j alpha f} df, elementwise in alpha."""
        out = np.full(alpha.shape, f2 - f1, dtype=np.complex128)
        nz = alpha != 0.0
        a = alpha[nz]
        out[nz] = (np.exp(1j * a * f2) - np.exp(1j * a * f1)) / (1j * a)
        return out

    def weighted_exp(alpha: np.ndarray, f1: float, f2: float) -> np.ndarray:
        """integral_{f1}^{f2} S(f) e^{j alpha f} df for 0 <= f1 < f2."""
        total = floor * exp_flat(alpha, f1, f2)
        if f1 < knee:
            total = total + exp_flat(alpha, f1, min(f2, knee)) / knee**2
        if f2 > knee:
            total = total + exp_over_f2(alpha, max(f1, knee), f2)
        return total

    k = np.arange(n_taps, dtype=np.float64)
    # S is even, so r is real: twice the real part of the one-sided integral.
    r = 2.0 * np.real(weighted_exp(2.0 * np.pi * k / fs, 0.0, nyq))
    p = 2.0 * weighted_exp(2.0 * np.pi * (k - delay) / fs, low, high)
    return scilinalg.solve(scilinalg.toeplitz(r), p, assume_a="pos")


class BandEnvelopeImpl(PrimitiveImpl):
    """`band_envelope(band:, latency:)` — analytic envelope in one stage.

    Least-squares complex FIR, per Smetanin, Belinskaya, Lebedev & Ossadtchi
    (2020), "Digital filters for low-latency quantification of brain rhythms
    in real time", J Neural Eng 17(4) 046022, doi:10.1088/1741-2552/ab890f.

    The desired response is `e^{-jωD}` inside the band and 0 outside, over
    positive frequencies only so the output is analytic. Its inverse transform
    — evaluated in closed form rather than on a DFT grid, for the reason given
    in `__init__` — is used directly as a causal FIR, tapered around tap `D`;
    the magnitude of its output is the envelope.

    `D` — the group delay — is a **design input**. In
    `bandpass -> hilbert -> magnitude -> smooth` the delay is instead an
    emergent sum of four stages that no protocol author chose. Truncating the
    ideal two-sided response at small `D` is the accuracy cost, and making
    that cost explicit is the point of the primitive.

    Two designs, chosen by `kind`. They are different trades, not a better and
    a worse one, which is why the choice is the author's:

    `"tapered"` (the default, and the only design before `kind` existed) is
    the ideal response under a Hann taper reaching `D` taps either side of tap
    `D`. Exactly linear phase, no ringing, a far stopband below -60 dB — and
    transition skirts as wide as the taper is short: at 320 ms a 12–16 Hz
    filter is only 13 dB down at 11 Hz, and at 200 ms only 7 dB. Its accuracy
    keeps rising with the declared latency, which makes it the right design
    when the latency is generous for the band (about two bandwidth-periods
    and up).

    `"weighted"` is the paper's weighted form (wcFIR, eq. 10–11) with a model
    spectrum in place of a measured one. It keeps the taps *after* `D` — the
    filter's memory, which costs no latency and which the symmetric taper
    throws away — and lets least squares place the stopband where EEG has its
    power: 18 dB down at 11 Hz at 160 ms and 21 dB from 200 ms. Measured on 12
    resting recordings at Cz, 12–16 Hz, against a zero-phase reference (r
    median / worst): tapered 0.914 / 0.897 at 320 ms; weighted 0.942 / 0.912
    at 160 ms and 0.964 / 0.952 at 200 ms. What it gives up: the far stopband
    is -31 to -61 dB at 35 and 60 Hz, depending on latency, rather than below
    -65; the in-band group delay is not constant (it ripples mid-band from
    about 40 ms under `D` to slightly over it and rises to roughly 100–150 ms
    over `D` toward the band edges, so the envelope's realised lag is `D` plus
    0–16 ms on a 4 Hz band and up to 24 ms on a 3 Hz one); a step in amplitude
    overshoots by 5–8% of the step; and its
    accuracy plateaus near r=0.97–0.98 instead of climbing with latency — so
    past about two bandwidth-periods the tapered design measures better and
    should be used.

    The weights are a fixed model, not the subject's spectrum. Measured on the
    same recordings, weights from the subject's own training minute, from the
    other eleven subjects, and from `1/f^2` with no data at all give medians
    within 0.01 of each other at every latency from 160 ms up (0.02 on the
    worst recording): the gain is in being weighted and one-sided, not in
    whose spectrum it is. A model also bakes at resolve time, where a measured
    spectrum cannot.
    """

    def __init__(
        self,
        *,
        band: tuple[float, float],
        latency_ms: float,
        taps: int | None = None,
        kind: str | None = None,
        sample_rate_hz: float,
    ):
        kind = "tapered" if kind is None else kind
        if kind not in BAND_ENVELOPE_KINDS:
            raise ValueError(
                f"band_envelope: unknown kind {kind!r}; expected one of "
                f"{', '.join(repr(k) for k in BAND_ENVELOPE_KINDS)}"
            )
        low, high = float(band[0]), float(band[1])
        nyq = sample_rate_hz / 2.0
        if not (0 < low < high < nyq):
            raise ValueError(
                f"band_envelope: band ({low}, {high}) Hz must satisfy "
                f"0 < low < high < nyquist ({nyq} Hz)"
            )
        latency_ms = float(latency_ms)
        if latency_ms <= 0.0:
            raise ValueError(
                f"band_envelope: latency must be positive (got {latency_ms} ms); it is "
                "the delay the envelope is designed to have, not an upper bound"
            )
        delay = int(round(latency_ms / 1000.0 * sample_rate_hz))
        if delay < BAND_ENVELOPE_MIN_DELAY_SAMPLES:
            floor_ms = BAND_ENVELOPE_MIN_DELAY_SAMPLES / sample_rate_hz * 1000.0
            raise ValueError(
                f"band_envelope: a latency of {latency_ms:g} ms is {delay} sample(s) at "
                f"{sample_rate_hz:g} Hz, too few to taper the response around. Declare at "
                f"least {floor_ms:.0f} ms at this rate. Below that the filter's energy "
                "wraps to the far end of the window, so the delay the output actually "
                "realizes is the filter length rather than the latency asked for."
            )
        if kind == "weighted":
            self._init_weighted(low, high, latency_ms, delay, taps, sample_rate_hz)
            return

        # The sizing rule. The taper is centred on tap `delay` and reaches
        # `delay` taps either side, so a filter shorter than 2·delay+1 truncates
        # it — and the only symptom is the accuracy number.
        required_taps = 2 * delay + 1
        n_taps = (
            int(math.ceil(BAND_ENVELOPE_SIZING_MARGIN * delay)) if taps is None else int(taps)
        )
        if n_taps % 2 == 0:
            n_taps += 1  # need odd length, as the FIR Hilbert does
        if n_taps < required_taps:
            raise ValueError(
                f"band_envelope: {n_taps} taps cannot carry a {latency_ms:g} ms latency at "
                f"{sample_rate_hz:g} Hz. The filter span must be at least ~2x the declared "
                f"delay ({delay} samples), so this needs taps >= {required_taps}. A shorter "
                "filter truncates the taper and envelope accuracy collapses silently "
                "(measured on real EEG at 256 Hz / 360 ms, 12-15 Hz: r=0.873 at 2x, "
                "0.743 at 1.75x, 0.437 at 1.4x)."
            )

        # The ideal response, in closed form: the inverse transform of
        # `e^{-jwD}` over [low, high] and 0 elsewhere, positive frequencies
        # only so the output is analytic.
        #
        #   b[n] = (1/fs) * integral_low^high e^{j2.pi.f(n-D)/fs} df
        #
        # Evaluated analytically rather than by sampling an N-point DFT grid.
        # The grid form is what the feature request proposed, but a 3 Hz band
        # at 250 Hz lands on only two or three grid points, and their jitter
        # with N makes accuracy swing with the tap count instead of with the
        # declared latency: measured r ran 0.80-0.94 across tap counts for one
        # fixed delay. In closed form it is 0.873 at every length from 2.0x to
        # 5.6x the delay, so the latency the author declares is the only thing
        # that sets accuracy. That is the property the primitive is for.
        taps_n = np.arange(n_taps)
        omega = 2.0 * np.pi * (taps_n - delay) / sample_rate_hz
        coeffs = np.empty(n_taps, dtype=np.complex128)
        away = omega != 0.0
        coeffs[away] = (
            np.exp(1j * omega[away] * high) - np.exp(1j * omega[away] * low)
        ) / (1j * omega[away] * sample_rate_hz)
        coeffs[~away] = (high - low) / sample_rate_hz  # the n == D limit

        # Taper centred on tap `delay`. `half` is bounded by `delay` so the
        # taper never reaches back past tap 0, which would make the filter
        # non-causal; the sizing rule above guarantees the other two bounds
        # leave it at `delay`.
        half = min(delay, n_taps - delay - 1, n_taps // 2)
        window = np.zeros(n_taps, dtype=np.float64)
        window[delay - half : delay + half + 1] = np.hanning(2 * half + 1)
        coeffs = coeffs * window

        # Calibrate to the analytic-signal convention — |H| = 2 at band centre,
        # so a sinusoid of amplitude A reads A, the same as
        # `bandpass -> hilbert -> magnitude`. The windowed ideal response has a
        # passband gain of about 2/3 of that, which would put the envelope a
        # constant factor low and fire every `absolute(uV)` threshold in a
        # converted protocol at the wrong amplitude. Correlation against a
        # reference envelope cannot see a scale error, so it is pinned by test.
        centre_hz = 0.5 * (low + high)
        gain = float(
            np.abs(
                np.sum(
                    coeffs * np.exp(-1j * 2.0 * np.pi * centre_hz * taps_n / sample_rate_hz)
                )
            )
        )
        if gain == 0.0:  # pragma: no cover - the guards above exclude it
            raise ValueError(
                f"band_envelope: band ({low}, {high}) Hz and latency {latency_ms:g} ms "
                f"at {sample_rate_hz:g} Hz design a filter with no passband gain"
            )

        self._finish(coeffs * (2.0 / gain), low, high, latency_ms, delay, "tapered")

    def _init_weighted(
        self,
        low: float,
        high: float,
        latency_ms: float,
        delay: int,
        taps: int | None,
        sample_rate_hz: float,
    ) -> None:
        period_samples = sample_rate_hz / (high - low)  # one bandwidth-period
        periods = (delay / sample_rate_hz) * (high - low)
        if periods < BAND_ENVELOPE_WEIGHTED_MIN_LATENCY_PERIODS:
            floor_ms = BAND_ENVELOPE_WEIGHTED_MIN_LATENCY_PERIODS / (high - low) * 1000.0
            raise ValueError(
                f'band_envelope: kind "weighted" cannot realize a {latency_ms:g} ms latency '
                f"on a {high - low:g} Hz wide band. Declare at least {floor_ms:.0f} ms "
                "(half a bandwidth-period), or widen the band. Below that the output "
                "lags by more than the latency declared: measured 25-50 ms over on real "
                "EEG at 0.4-0.5 periods, and within 4-8 ms from 0.8 periods up."
            )
        tail = int(math.ceil(BAND_ENVELOPE_WEIGHTED_TAIL_PERIODS * period_samples))
        min_tail = int(math.ceil(BAND_ENVELOPE_WEIGHTED_MIN_TAIL_PERIODS * period_samples))
        n_taps = delay + tail + 1 if taps is None else int(taps)
        if n_taps % 2 == 0:
            n_taps += 1  # odd, as the tapered design is, so the two bake alike
        required_taps = delay + min_tail + 1
        if n_taps < required_taps:
            raise ValueError(
                f'band_envelope: {n_taps} taps cannot carry kind "weighted" at a '
                f"{latency_ms:g} ms latency on a {high - low:g} Hz wide band at "
                f"{sample_rate_hz:g} Hz. The taps after the delay are the filter's memory "
                f"and must span at least one bandwidth-period ({min_tail} samples), so "
                f"this needs taps >= {required_taps}. Left out, taps defaults to "
                f"{delay + tail + 1 + ((delay + tail + 1) % 2 == 0)}."
            )
        coeffs = _weighted_band_fir(low, high, delay, n_taps, sample_rate_hz)

        # Same calibration as the tapered design, for the same reason: a
        # sinusoid of amplitude A at band centre reads A.
        centre_hz = 0.5 * (low + high)
        gain = float(
            np.abs(
                np.sum(
                    coeffs
                    * np.exp(-1j * 2.0 * np.pi * centre_hz * np.arange(n_taps) / sample_rate_hz)
                )
            )
        )
        if gain == 0.0:  # pragma: no cover - the guards above exclude it
            raise ValueError(
                f"band_envelope: band ({low}, {high}) Hz and latency {latency_ms:g} ms "
                f"at {sample_rate_hz:g} Hz design a filter with no passband gain"
            )
        self._finish(coeffs * (2.0 / gain), low, high, latency_ms, delay, "weighted")

    def _finish(
        self,
        h_complex: np.ndarray,
        low: float,
        high: float,
        latency_ms: float,
        delay: int,
        kind: str,
    ) -> None:
        # Named `h_complex`, not `h`: `ir_json._extract_coeffs` duck-types on
        # the attribute name, and `h` is the real-tap FIR branch (HilbertFirImpl).
        # The complex branch bakes as a split re/im pair for the portable core.
        self.h_complex = h_complex
        self.taps = len(h_complex)
        self.band = (low, high)
        self.latency_ms = latency_ms
        self.group_delay = delay
        self.kind = kind
        # The coefficients carry the complex part, so the delay line stays
        # real — the same split HilbertFirImpl uses for its real branch.
        self._buf = np.zeros(self.taps - 1, dtype=np.float64)

    def step(self, x: np.ndarray) -> np.ndarray:
        analytic, self._buf = _fir_stream(self.h_complex, x, self._buf)
        return np.abs(analytic)


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


class SqrtImpl(PrimitiveImpl):
    """`sqrt()` — `sqrt(max(x, 0))` for a real stream.

    Negative input is clamped rather than passed to the root. The resolver
    only admits a stream whose dimensions are even powers, so the input is a
    squared quantity whose true value cannot be negative — but the usual
    spelling of variance, `mean_square - mean*mean`, cancels to a few float
    epsilons either side of zero on a steady signal, and a NaN there would
    travel down the whole feedback path and silence the session. Clamping
    keeps the one honest answer, 0.
    """

    def step(self, x: np.ndarray) -> np.ndarray:
        return np.sqrt(np.maximum(np.asarray(x, dtype=np.float64), 0.0))


class SmoothImpl(PrimitiveImpl):
    """`smooth(tau)` — one-pole IIR low-pass.

    α = 1 − exp(−1 / (τ · rate)). State: previous output value.
    Supports live retuning of `tau` via `update_control`; the running
    output value carries across to avoid an audible step.

    Warm-started: `_state` is seeded to the first sample, which is the
    steady state of a one-pole low-pass for a constant input at that level.
    A tonic/phasic pair (`x - smooth(x)`) therefore reads ~0 phasic from
    sample one rather than reporting the standing level as a response.
    """

    def __init__(self, *, tau_ms: float, sample_rate_hz: float):
        if tau_ms <= 0:
            raise ValueError("smooth: tau must be positive")
        self._sample_rate_hz = float(sample_rate_hz)
        self._set_tau(tau_ms)
        self._state = 0.0
        self._primed = False

    def _set_tau(self, tau_ms: float) -> None:
        tau_s = tau_ms / 1000.0
        self.tau_ms = tau_ms
        self.alpha = 1.0 - np.exp(-1.0 / (tau_s * self._sample_rate_hz))

    def step(self, x: np.ndarray) -> np.ndarray:
        if not self._primed and x.shape[0]:
            self._state = float(x[0])
            self._primed = True
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
        # When False, `step` still EMITS the current percentile but does NOT
        # ingest incoming samples into the rolling buffer. The evaluator drives
        # this via `set_ingesting` to freeze the window during mid-session
        # muted rests so rest-period artifact can't pollute a later block's
        # window ("one baseline up front").
        self._ingesting = True
        self._seen = 0  # samples ingested; reported (capped) as n_eff
        # Track which control names map to which parameters, populated
        # via update_control. The map is empty until the evaluator has
        # registered control->impl deps; PercentileImpl supports
        # updating `target_pct` from any incoming control change.
        self._control_param_map: dict[str, str] = {}

    def set_ingesting(self, ingesting: bool) -> None:
        self._ingesting = bool(ingesting)

    def ingest(self, x: np.ndarray) -> None:
        """Append finite samples to the window WITHOUT computing a percentile
        (seed-latch warmup ingest). Non-finite samples are skipped — never
        appended, never counted — so both engines share one NaN-skip rule and
        the full-window check (`n_eff >= window_samples`) is exact."""
        if not self._ingesting:
            return
        for v in x:
            fv = float(v)
            if math.isfinite(fv):
                self._buffer.append(fv)
                self._seen += 1

    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(x.shape[0], dtype=np.float64)
        for i, v in enumerate(x):
            if self._ingesting:
                self._buffer.append(float(v))
                self._seen += 1
            # Compute percentile over current buffer (warm-up: short buffer is OK).
            # A frozen window still emits over its existing buffer.
            arr = np.fromiter(self._buffer, dtype=np.float64) if len(self._buffer) else np.zeros(1)
            out[i] = float(np.percentile(arr, self.target_pct))
        return out

    def export_state(self) -> dict:
        """Compact summary for cross-session persistence (Ask 2): the current
        threshold value at `target_pct` plus the effective sample count."""
        if len(self._buffer):
            value = float(np.percentile(
                np.fromiter(self._buffer, dtype=np.float64), self.target_pct))
        else:
            value = 0.0
        return {
            "value": value,
            "target_pct": float(self.target_pct),
            "n_eff": int(min(self._seen, self.window_samples)),
        }

    def seed(self, state: dict) -> None:
        """Fill the window with `value` repeated, so percentile(target_pct) ==
        value exactly at session start; real samples then displace it. Both
        backends apply the identical constant pre-fill (parity by construction)."""
        value = float(state["value"])
        n = int(min(state.get("n_eff", self.window_samples), self.window_samples))
        n = max(n, 1)
        self._buffer.clear()
        self._buffer.extend([value] * n)
        self._seen = n

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
        self._seen = 0  # samples ingested; reported (capped) as n_eff

    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(x.shape[0], dtype=np.float64)
        for i, v in enumerate(x):
            self._buffer.append(float(v))
            self._seen += 1
            arr = np.fromiter(self._buffer, dtype=np.float64)
            low, high = np.percentile(arr, [self.low_pct, self.high_pct])
            span = max(high - low, 1e-9)
            out[i] = float(np.clip((v - low) / span, 0.0, 1.0))
        return out

    def export_state(self) -> dict:
        """Compact summary for cross-session persistence (Ask 2): the current
        low/high anchors plus the effective sample count. Rate-independent and
        human-readable — the patient-record ceiling that rises week to week."""
        if len(self._buffer):
            arr = np.fromiter(self._buffer, dtype=np.float64)
            low, high = np.percentile(arr, [self.low_pct, self.high_pct])
        else:
            low = high = 0.0
        return {
            "low": float(low),
            "high": float(high),
            "n_eff": int(min(self._seen, self.window_samples)),
        }

    def seed(self, state: dict) -> None:
        """Pre-fill the rolling window with a deterministic synthetic ramp whose
        `low_pct`/`high_pct` percentiles reproduce the seeded anchors. For a
        uniform ramp a..b, percentile(p) = a + (p/100)(b-a); solve so
        percentile(low_pct)=low and percentile(high_pct)=high. Because the seed
        is expressed as initial buffer contents, `step()` is unchanged and both
        backends apply the identical pre-fill (parity by construction)."""
        low, high = float(state["low"]), float(state["high"])
        n = int(min(state.get("n_eff", self.window_samples), self.window_samples))
        n = max(n, 1)
        span_pct = max(self.high_pct - self.low_pct, 1e-9)
        b_minus_a = (high - low) * 100.0 / span_pct
        a = low - (self.low_pct / 100.0) * b_minus_a
        ramp = np.linspace(a, a + b_minus_a, n)
        self._buffer.clear()
        self._buffer.extend(float(v) for v in ramp)
        self._seen = n


# ---------------------------------------------------------------------------
# Threshold-type "constructors" — wrap a percentile tracker / constant
# ---------------------------------------------------------------------------


class AbsoluteThresholdImpl(PrimitiveImpl):
    """`absolute(value)` — emits a constant stream."""

    def __init__(self, *, value: float):
        self.value = float(value)

    def step(self, x: np.ndarray) -> np.ndarray:
        return np.full(x.shape[0], self.value, dtype=np.float64)

    def update_control(self, target: str, value: float) -> None:
        """`absolute(value: <control_ref>)` binds the threshold's only
        parameter to the control, so any incoming change retargets `value`."""
        self.value = float(value)


class PercentileThresholdImpl(PercentileImpl):
    """Same machinery as PercentileImpl, just named for clarity in IR
    contexts where it's used as a threshold constructor."""


class IdentityImpl(PrimitiveImpl):
    """Carries its input chunk through unchanged.

    What a primitive reduces to once one of its slots turns out to hold a
    reference to another stream rather than a constant — see
    `eval_._REFERENCE_SLOTS`. Two spellings reach it:

      `absolute(value: "<a derive>")` — a threshold that tracks another
      stream instead of sitting at a fixed level.

      `passthrough(input: "<a derive>")` — an inhibit reading one derive.
      An inhibit's metric has to be a primitive call, so there is no way to
      name the derive without wrapping it in something, and this is that
      something. Distinct from the *montage* form `passthrough()`, which
      selects the single channel of a raw source (`PassthroughImpl`).
    """

    def step(self, x: np.ndarray) -> np.ndarray:
        return x


# ---------------------------------------------------------------------------
# Conditions (boolean output)
# ---------------------------------------------------------------------------


class AboveImpl(PrimitiveImpl):
    def step(self, signal: np.ndarray, threshold: np.ndarray) -> np.ndarray:
        return signal > threshold


class AtOrAboveImpl(PrimitiveImpl):
    def step(self, signal: np.ndarray, threshold: np.ndarray) -> np.ndarray:
        return signal >= threshold


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


def _require_matching_mask(
    name: str, n: int, observable: np.ndarray | None
) -> None:
    """Refuse an observability mask that does not line up with the condition.

    Unchecked, a short mask raises a bare `IndexError` from inside the hold
    loop in Python and panics in Rust, and a long one is silently accepted
    with its tail ignored in both. The hold paths are held bit-identical, so
    they need one defined contract — stated here, before Task 6 starts
    passing real masks.
    """
    if observable is not None and observable.shape[0] != n:
        raise ValueError(
            f"{name}: observable has {observable.shape[0]} samples but "
            f"condition has {n}"
        )


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
    """`dwell(condition, duration, tolerance)` state machine.

    Maintains `samples_true_streak`. When `condition` is True the counter
    increments; when False it resets to 0, unless the false run so far is
    no longer than `tolerance_samples`, in which case the streak is held
    exactly where it was. `holds` is true when the streak meets or exceeds
    `dwell_samples`; the rising-edge `event` fires on the transition from
    not-holding to holding.

    Forgive, don't credit: a forgiven dip never advances the streak, so a
    streak of N samples always means N samples genuinely above threshold
    and observable. A sample the engine could not observe is scored as
    neither — streak and dip counter both freeze.
    """

    def __init__(
        self,
        *,
        duration_ms: float,
        sample_rate_hz: float,
        tolerance_ms: float = 0.0,
    ):
        if duration_ms <= 0:
            raise ValueError("dwell: duration must be positive")
        self.dwell_samples = max(1, int(round(duration_ms / 1000.0 * sample_rate_hz)))
        # Deliberately not floored at 1, unlike `dwell_samples`: a tolerance
        # too short to reach one sample at this rate forgives nothing, which
        # is what it asked for. Flooring it at 1 would invent forgiveness.
        self.tolerance_samples = max(
            0, int(round(tolerance_ms / 1000.0 * sample_rate_hz))
        )
        self._streak = 0
        self._dip = 0
        self._was_holding = False

    def reset(self) -> None:
        self._streak = 0
        self._dip = 0
        self._was_holding = False

    def step(
        self,
        condition: np.ndarray,
        observable: np.ndarray | None = None,
    ) -> DwellResult:
        """`observable=None` means every sample is observable.

        The per-sample rule below needs no lookahead, which is what lets
        `Dwell` in `refrain-core/src/dsp.rs` transcribe it line for line.
        The two are held bit-identical by the drift gate: change one and
        change the other in the same breath.
        """
        n = condition.shape[0]
        _require_matching_mask("dwell", n, observable)
        events = np.zeros(n, dtype=bool)
        holds = np.zeros(n, dtype=bool)
        streak = self._streak
        dip = self._dip
        was_holding = self._was_holding
        for i in range(n):
            if observable is not None and not observable[i]:
                pass                                  # streak and dip frozen
            elif condition[i]:
                streak += 1
                dip = 0
            else:
                dip += 1
                if dip > self.tolerance_samples:
                    streak = 0
            is_holding = streak >= self.dwell_samples
            holds[i] = is_holding
            if is_holding and not was_holding:
                events[i] = True
            was_holding = is_holding
        self._streak = streak
        self._dip = dip
        self._was_holding = was_holding
        return DwellResult(events=events, holds=holds)


class DwellRearmImpl(DwellImpl):
    """One pulse per hold, rearmed only after continuous time out."""

    def __init__(
        self,
        *,
        duration_ms: float,
        rearm_ms: float,
        sample_rate_hz: float,
        tolerance_ms: float = 0.0,
    ):
        super().__init__(
            duration_ms=duration_ms,
            sample_rate_hz=sample_rate_hz,
            tolerance_ms=tolerance_ms,
        )
        if rearm_ms <= 0:
            raise ValueError("dwell_rearm: rearm must be positive")
        self.rearm_samples = max(1, int(round(rearm_ms / 1000.0 * sample_rate_hz)))
        self._sample_rate_hz = sample_rate_hz
        self._armed = True
        self._out_streak = 0

    def update_control(self, target: str, value: float) -> None:
        if value <= 0:
            raise ValueError("dwell_rearm duration must be positive")
        self.dwell_samples = max(1, round(value / 1000.0 * self._sample_rate_hz))
        self.reset()

    def reset(self) -> None:
        super().reset()
        self._armed = True
        self._out_streak = 0

    def step(
        self,
        condition: np.ndarray,
        observable: np.ndarray | None = None,
    ) -> DwellResult:
        """Same per-sample rule as `DwellImpl.step`, plus the re-arm clock.

        The re-arm countdown is held through a forgiven dip, so a dip can
        never re-arm the event and hand out a second reward for one burst.
        """
        n = condition.shape[0]
        _require_matching_mask("dwell_rearm", n, observable)
        events = np.zeros(n, dtype=bool)
        holds = np.zeros(n, dtype=bool)
        streak = self._streak
        dip = self._dip
        out_streak = self._out_streak
        armed = self._armed
        for i in range(n):
            if observable is not None and not observable[i]:
                pass                       # streak, dip and out_streak frozen
            elif condition[i]:
                streak += 1
                dip = 0
                out_streak = 0
            else:
                dip += 1
                if dip > self.tolerance_samples:
                    streak = 0
                    # Credit the whole dip the first time tolerance is
                    # exceeded, then one sample per false sample after that.
                    # The condition was genuinely false from the dip's first
                    # sample, so counting only from the moment tolerance
                    # expired would make re-arming slower than it is with no
                    # tolerance — whenever `tolerance` is shorter than
                    # `rearm`. Where it is not, the forgiven window itself
                    # delays re-arming; nothing refuses that, and no output
                    # follows from it, because the streak is frozen through
                    # the window so no hold can rise while `armed` is false.
                    out_streak += dip if dip == self.tolerance_samples + 1 else 1
                    if out_streak >= self.rearm_samples:
                        armed = True
            is_holding = streak >= self.dwell_samples
            holds[i] = is_holding
            if is_holding and armed:
                events[i] = True
                armed = False
        self._streak = streak
        self._dip = dip
        self._out_streak = out_streak
        self._armed = armed
        return DwellResult(events=events, holds=holds)


class HoldForImpl(DwellImpl):
    """Boolean held view of an independent dwell accumulator."""

    def __init__(
        self,
        *,
        duration_ms: float,
        sample_rate_hz: float,
        tolerance_ms: float = 0.0,
    ):
        super().__init__(
            duration_ms=duration_ms,
            sample_rate_hz=sample_rate_hz,
            tolerance_ms=tolerance_ms,
        )
        self._sample_rate_hz = sample_rate_hz

    def update_control(self, target: str, value: float) -> None:
        if value <= 0:
            raise ValueError("hold_for duration must be positive")
        self.dwell_samples = max(1, round(value / 1000.0 * self._sample_rate_hz))
        self.reset()

    def step(
        self,
        condition: np.ndarray,
        observable: np.ndarray | None = None,
    ) -> np.ndarray:
        return super().step(condition, observable).holds


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
    """`freeze(release)` — gate output to zero while inhibit-active, plus
    hangover, exactly as `MuteAction` does. Neither engine holds the last
    value; the name is historical and `docs/SPEC.md` §4.6 says so."""

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


class BandpowerImpl(_WarmStartSos, PrimitiveImpl):
    """`bandpower(input, band, window)` — sliding-window total power
    in a frequency band.

    Implementation: a band-limited Butterworth bandpass followed by a
    sliding-window RMS computation. Welch's method would be more
    accurate; this is the cheap streaming approximation that clinical
    NF software typically uses for EMG-style artifact gates.

    The internal bandpass is warm-started on the first sample for the same
    reason `BandpassImpl` is, and it has to be: the Rust core's `Bandpower`
    is built on the same `Biquad` as its `bandpass`, so leaving this one
    cold would make the two engines disagree.
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
        self._init_warm_start(self.sos)
        self.window_samples = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        self._buffer: deque[float] = deque(maxlen=self.window_samples)

    def step(self, x: np.ndarray) -> np.ndarray:
        # Filter to the band, then compute squared values; sliding-window
        # mean = power. Returned in the input unit's square.
        self._warm_start(x)
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
#
# Welch segment length used to be `max(8, sample_rate // 4)` — a 250 ms
# segment whatever band was asked for, so about 4 Hz between bins at any
# rate at or above 32 Hz. "Band average" then meant "the mean of however
# many bins happened to land inside", which for the shipped
# `band: (8 Hz, 12 Hz)` was one bin at 8.06 Hz at 250 Hz, and two bins at
# 256 Hz that were the band edges themselves with nothing in between. A band
# narrower than the bin spacing and falling between two bin centres got
# exactly 0.0, which reads as "no coupling" rather than "cannot be measured".
#
# The segment length is now derived from the band instead: long enough to put
# at least `MIN_BINS_IN_BAND` bins inside it, never shorter than the old
# 250 ms default (a longer segment buys frequency resolution at the cost of
# fewer segments to average over, so there is no reason to coarsen a band
# that was already well resolved). Bands wider than
# `MIN_BINS_IN_BAND * 4 Hz` keep exactly the numbers they had.
#
# Where the window cannot hold two segments that long, that is a protocol
# that cannot measure what it claims to measure, and it is refused — here as
# a backstop and at resolve time in `resolver._validate_coherence_bins`,
# which is what an author actually sees.

# The fewest Welch bins that make "the average over the band" mean anything.
# Two is the band edges with nothing between them; one is a single bin
# wearing a band's name.
MIN_BINS_IN_BAND = 3

# The segment length the primitive used before the band drove it: 250 ms,
# i.e. about 4 Hz between bins. Kept as a floor so a well-resolved wide band
# keeps its existing numbers and its existing segment count.
_DEFAULT_NPERSEG_DIVISOR = 4


def coherence_nperseg(band: tuple[float, float], sample_rate_hz: float) -> int:
    """Welch segment length for `band` at `sample_rate_hz`, in samples.

    The shortest segment that really holds `MIN_BINS_IN_BAND` bin centres
    inside the band, floored at the historical 250 ms default so a
    well-resolved wide band keeps its existing numbers and segment count.

    Shortest, because every sample of segment costs segments: a `window` holds
    `window / (nperseg / 2)` of them at 50% overlap, and magnitude-squared
    coherence averaged over `n` segments has a noise floor of about `1/n`. A
    longer segment buys frequency resolution with dynamic range.

    **Counted, not bounded.** `spacing <= W / MIN_BINS` is *sufficient* — it
    guarantees the bins regardless of where the grid falls — but it is not
    *necessary*, and the gap is not small. Three bin centres need two gaps
    between them, so `spacing <= W / 2`, i.e. `nperseg >= 2 * fs / W`, is the
    weakest a segment can possibly be; between `2 * fs / W` and
    `3 * fs / W` whether the bins land in the band depends on the grid, which
    only counting can tell. Alpha at 250 Hz is the case that matters: the
    sufficient bound gives 188 samples (752 ms, the first bin 0.021 Hz below
    the 8 Hz edge, 4 segments in a 2 s window), and counting gives 125 (500 ms,
    bins exactly on 8, 10 and 12 Hz, 7 segments). Same three bins, 1.75x the
    segments, and the band it reports is the band the author declared.

    The scan is bounded both ways and so always terminates: it starts at the
    necessary bound, where nothing shorter can qualify, and ends at the
    sufficient one, which always does.
    """
    low, high = float(band[0]), float(band[1])
    width = high - low
    default = max(8, int(sample_rate_hz // _DEFAULT_NPERSEG_DIVISOR))
    if width <= 0:
        return default
    # Necessary: below this the spacing exceeds W/2 and at most two centres
    # fit, whatever the alignment. Sufficient: at this the floor argument
    # holds regardless of alignment.
    necessary = int(math.ceil((MIN_BINS_IN_BAND - 1) * sample_rate_hz / width))
    sufficient = int(math.ceil(MIN_BINS_IN_BAND * sample_rate_hz / width))
    lo = max(8, default, necessary)
    hi = max(8, default, sufficient)
    for nperseg in range(lo, hi):
        if len(coherence_bins_in_band(band, sample_rate_hz, nperseg)) >= MIN_BINS_IN_BAND:
            return nperseg
    return hi


def coherence_bins_in_band(
    band: tuple[float, float], sample_rate_hz: float, nperseg: int
) -> list[float]:
    """The one-sided Welch bin centres that fall inside `band`.

    Mirrors the mask the impl applies (`f >= low` and `f <= high`) over
    `scipy.signal.coherence`'s grid `f[k] = k * fs / nperseg`,
    `k = 0 .. nperseg // 2`. Counted rather than assumed, so the guard
    reports what the band really holds.
    """
    low, high = float(band[0]), float(band[1])
    spacing = sample_rate_hz / nperseg
    return [
        k * spacing
        for k in range(nperseg // 2 + 1)
        if low <= k * spacing <= high
    ]


def coherence_min_window_ms(
    band: tuple[float, float], sample_rate_hz: float
) -> float:
    """Shortest `window` that can hold two band-resolving segments, in ms.

    Two segments is the existing floor: a single-segment Welch returns
    MSC = 1.0 for any input.
    """
    nperseg = coherence_nperseg(band, sample_rate_hz)
    return 2 * nperseg * 1000.0 / sample_rate_hz


# `window` is optional on `coherence`. Its default used to be a flat 1 s,
# which held four 250 ms segments. Now that the segment comes from the band,
# a flat 1 s would be *shorter than the minimum* for any band narrower than
# about 5 Hz — so a protocol that simply omitted `window` would resolve and
# then refuse to build. The default follows the band instead.
_COHERENCE_DEFAULT_WINDOW_MS = 1000.0


def coherence_default_window_ms(
    band: tuple[float, float], sample_rate_hz: float
) -> float:
    """`window` when the author did not write one: the historical 1 s, or the
    band's own minimum where that is longer."""
    return max(
        _COHERENCE_DEFAULT_WINDOW_MS,
        coherence_min_window_ms(band, sample_rate_hz),
    )


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
    one segment. This impl forces `nperseg ≤ window_samples / 2` so
    multi-segment averaging gives a real coherence estimate.

    Frequency resolution is `sample_rate / nperseg`, and `nperseg` comes
    from the band (`coherence_nperseg`): long enough for at least
    `MIN_BINS_IN_BAND` bins inside it, so the band average is an average.
    A 4 Hz band needs 1.33 Hz resolution, i.e. 750 ms segments, of which a
    2 s window holds four at 50% overlap. A band too narrow for two such
    segments to fit the window is refused rather than reported.
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
        # Segment length from the band, not from the sample rate — see
        # `coherence_nperseg`. nperseg MUST be <= window_samples / 2 for
        # multi-segment Welch to produce meaningful MSC.
        self.nperseg = coherence_nperseg(self.band, self.sample_rate_hz)
        min_window_ms = 2 * self.nperseg * 1000.0 / sample_rate_hz
        if self.window_samples < self.nperseg * 2:
            raise ValueError(
                f"coherence: averaging the ({low:g}, {high:g}) Hz band needs "
                f"at least {MIN_BINS_IN_BAND} Welch bins inside it, which at "
                f"{sample_rate_hz:g} Hz means {self.nperseg}-sample "
                f"({self.nperseg / sample_rate_hz * 1000.0:.0f} ms) segments, "
                f"so window must be ≥ {min_window_ms:.0f} ms (got "
                f"{window_ms:g} ms). A single-segment Welch returns MSC = 1.0 "
                f"trivially and cannot be used. Lengthen `window` or widen "
                f"`band`."
            )
        bins = coherence_bins_in_band(self.band, self.sample_rate_hz, self.nperseg)
        if len(bins) < MIN_BINS_IN_BAND:
            # Defensive: the derivation above guarantees this, so reaching
            # here means the arithmetic is wrong rather than the protocol.
            raise ValueError(
                f"coherence: the ({low:g}, {high:g}) Hz band holds only "
                f"{len(bins)} Welch bin(s) at {sample_rate_hz:g} Hz with "
                f"{self.nperseg}-sample segments ({MIN_BINS_IN_BAND} needed to "
                f"average); averaging it would report a single bin as a band."
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


class AutocorrImpl(PrimitiveImpl):
    """`autocorr(lag, window)` — rolling lag-k Pearson autocorrelation.

    Single-input pipeline stage. A `deque(maxlen=window_samples)` holds the
    trailing window; per input sample the lag-`k` autocorrelation of the buffer
    ending at that sample is emitted. Returns 0.0 until `lag + 2` samples
    accumulate (warm-up, matching the BandpowerImpl/CoherenceImpl convention)
    and 0.0 for a constant window (zero variance, avoiding NaN). Output in
    [-1, 1]. This is the "critical slowing down" early-warning indicator: as a
    system nears a phase-state transition its lag-1 autocorrelation rises
    (Scheffer et al., Nature 2009; Maturana et al., Nat. Commun. 2020).

    O(window) per sample (the same complexity class as BandpowerImpl's rolling
    mean) — the Rust core mirrors this math exactly for parity.
    """

    def __init__(
        self,
        *,
        window_ms: float,
        sample_rate_hz: float,
        lag_ms: float | None = None,
        lag_samples: int | None = None,
    ):
        ws = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        if lag_ms is not None:
            ls = max(1, int(round(lag_ms / 1000.0 * sample_rate_hz)))
        elif lag_samples is not None:
            ls = max(1, int(lag_samples))
        else:
            raise ValueError("autocorr: one of lag_ms / lag_samples is required")
        if ws < ls + 2:
            raise ValueError(
                f"autocorr: window ({ws} samples) must be >= lag+2 ({ls + 2}) "
                f"for a meaningful lag-{ls} autocorrelation"
            )
        self.window_samples = ws
        self.lag = ls
        self._buffer: deque[float] = deque(maxlen=ws)

    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(x.shape[0], dtype=np.float64)
        buf = self._buffer
        lag = self.lag
        for i in range(x.shape[0]):
            buf.append(float(x[i]))
            n = len(buf)
            if n < lag + 2:
                # Warm-up: not enough samples for a lag-k estimate.
                out[i] = 0.0
                continue
            arr = np.fromiter(buf, dtype=np.float64, count=n)
            d = arr - arr.mean()
            den = float(np.dot(d, d))
            if den == 0.0:
                # Constant window: zero variance -> undefined; report 0.0.
                out[i] = 0.0
                continue
            num = float(np.dot(d[lag:], d[:-lag]))
            out[i] = max(-1.0, min(1.0, num / den))
        return out


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
    if callee == "passthrough":
        return PassthroughImpl(channel_names=channel_names)
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
    if callee == "band_envelope":
        return BandEnvelopeImpl(
            band=tuple(static_args["band"]),
            latency_ms=float(static_args["latency_ms"]),
            taps=static_args.get("taps"),
            kind=static_args.get("kind"),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "magnitude":
        return MagnitudeImpl()
    if callee == "rectify":
        return RectifyImpl()
    if callee == "sqrt":
        return SqrtImpl()
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
    if callee == "at_or_above":
        return AtOrAboveImpl()
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
            tolerance_ms=float(static_args.get("tolerance_ms", 0.0)),
        )
    if callee == "dwell_rearm":
        return DwellRearmImpl(
            duration_ms=float(static_args["duration_ms"]),
            rearm_ms=float(static_args["rearm_ms"]),
            sample_rate_hz=sample_rate_hz,
            tolerance_ms=float(static_args.get("tolerance_ms", 0.0)),
        )
    if callee == "hold_for":
        return HoldForImpl(
            duration_ms=float(static_args["duration_ms"]),
            sample_rate_hz=sample_rate_hz,
            tolerance_ms=float(static_args.get("tolerance_ms", 0.0)),
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
        coh_band = tuple(static_args["band"])
        return CoherenceImpl(
            band=coh_band,
            window_ms=float(
                static_args.get(
                    "window_ms",
                    coherence_default_window_ms(coh_band, sample_rate_hz),
                )
            ),
            sample_rate_hz=sample_rate_hz,
        )
    if callee == "autocorr":
        return AutocorrImpl(
            window_ms=float(static_args["window_ms"]),
            lag_ms=float(static_args["lag_ms"]),
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
    "IdentityImpl",
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
    "SqrtImpl",
    "make_action",
    "make_filter_impl",
]
