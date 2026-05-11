# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Synthetic EEG signal generation for evaluator validation.

Real-time NF testing on live amps is expensive and slow. Synthetic
signals let us validate the evaluator deterministically: pink-noise
EEG with scheduled bursts of band-specific enhancement at known
timestamps. If the SMR protocol fires reward events during a 13 Hz
burst (sustained for the dwell duration) and not during quiet
intervals, the math is working.

`SignalGenerator` produces samples chunk-by-chunk. It carries time
state so successive chunks are continuous. Deterministic via seed.

The model:
  - Pink noise per channel (1/f power spectrum) at a clinically
    realistic baseline amplitude (~10 µV RMS)
  - Optional scheduled `SMRBurst` events: during a burst window,
    a narrowband signal at the burst's center frequency is added
    on top of the noise floor at the burst's amplitude
  - Channel-uncorrelated noise (simplification; real EEG has spatial
    correlation, but for protocol-validation purposes uncorrelated
    is honest and clearly synthetic)

For the SMR Cz protocol against this generator: bursts at 13 Hz at
20 µV peak for 1 second should produce sustained SMR-envelope values
above the 70th-percentile threshold, triggering reward dwell events
~250 ms after burst onset.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class SMRBurst:
    """A scheduled band-specific enhancement window.

    `start_s` / `end_s`  — burst time bounds in seconds
    `center_hz`          — frequency of the injected sinusoid
    `amplitude_uv`       — peak amplitude added on top of the noise floor
    `channel`            — channel name to inject into (None = all)
    """

    start_s: float
    end_s: float
    center_hz: float
    amplitude_uv: float = 20.0
    channel: str | None = None


class SignalGenerator:
    """Generates pink-noise EEG with scheduled band-specific bursts.

    Parameters
    ----------
    sample_rate_hz : int
        Output rate. Typical NF rate is 256 Hz.
    channels : tuple[str, ...]
        Channel labels. Each gets its own independent noise stream.
    bursts : tuple[SMRBurst, ...]
        Scheduled enhancement windows. Empty = pure pink noise.
    noise_uv_rms : float
        Per-channel pink-noise RMS amplitude. ~10 µV is realistic for
        scalp EEG at typical placements.
    seed : int
        Deterministic random seed for reproducible tests.
    """

    def __init__(
        self,
        *,
        sample_rate_hz: int = 256,
        channels: tuple[str, ...] = ("Cz",),
        bursts: tuple[SMRBurst, ...] = (),
        noise_uv_rms: float = 10.0,
        seed: int = 42,
    ):
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if not channels:
            raise ValueError("at least one channel required")
        self.sample_rate_hz = sample_rate_hz
        self.channels = tuple(channels)
        self.bursts = tuple(bursts)
        self.noise_uv_rms = float(noise_uv_rms)
        self._rng = np.random.default_rng(seed)
        self._sample_index = 0
        # Pre-compute a pink-noise IIR filter (Paul Kellet's three-pole
        # approximation — good enough for synthetic EEG, cheap, stateful).
        self._pink_state = np.zeros((len(self.channels), 3), dtype=np.float64)

    def next_chunk(self, n_samples: int) -> np.ndarray:
        """Yield `(n_samples, n_channels)` of synthetic EEG."""
        white = self._rng.standard_normal((n_samples, len(self.channels)))
        # Pink filter (Paul Kellet): three IIR poles per channel.
        out = np.empty_like(white)
        b0 = self._pink_state[:, 0]
        b1 = self._pink_state[:, 1]
        b2 = self._pink_state[:, 2]
        for t in range(n_samples):
            x = white[t]
            b0 = 0.99886 * b0 + x * 0.0555179
            b1 = 0.99332 * b1 + x * 0.0750759
            b2 = 0.96900 * b2 + x * 0.1538520
            out[t] = b0 + b1 + b2 + x * 0.1848
        self._pink_state[:, 0] = b0
        self._pink_state[:, 1] = b1
        self._pink_state[:, 2] = b2

        # Normalize to target RMS. The pink filter has an ~8x gain on
        # white noise; empirically the unnormalized RMS hovers around
        # 0.5, so we scale to hit noise_uv_rms.
        if self.noise_uv_rms > 0:
            current_rms = float(np.sqrt(np.mean(out**2))) or 1.0
            out *= self.noise_uv_rms / current_rms

        # Add bursts.
        start_s = self._sample_index / self.sample_rate_hz
        end_s = (self._sample_index + n_samples) / self.sample_rate_hz
        for burst in self.bursts:
            if burst.end_s <= start_s or burst.start_s >= end_s:
                continue
            t_axis = (
                np.arange(n_samples) + self._sample_index
            ) / self.sample_rate_hz
            mask = (t_axis >= burst.start_s) & (t_axis < burst.end_s)
            if not mask.any():
                continue
            sinusoid = burst.amplitude_uv * np.sin(
                2 * np.pi * burst.center_hz * t_axis[mask]
            )
            if burst.channel is None:
                out[mask, :] += sinusoid[:, None]
            elif burst.channel in self.channels:
                ch_idx = self.channels.index(burst.channel)
                out[mask, ch_idx] += sinusoid

        self._sample_index += n_samples
        return out


__all__ = ["SignalGenerator", "SMRBurst"]
