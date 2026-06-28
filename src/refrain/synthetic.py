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

import zlib
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfilt


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


@dataclass(frozen=True, slots=True)
class BandSegmentBurst:
    """A scheduled band-targeted injection. Generalises `SMRBurst`: instead of
    just a tone, supports a tone OR band-limited noise at a target in-band RMS,
    in any (low, high) band on any named channel. Used by the protocol fuzzer's
    `render_scenario`. Exactly one of `tone_amplitude_uv` / `noise_rms_uv` is set.

    `start_s` / `end_s`     — injection window in seconds
    `band`                  — (low_hz, high_hz); tones use the band center
    `channel`               — channel name to inject into
    `tone_amplitude_uv`     — peak amplitude of a pure sinusoid, or None
    `noise_rms_uv`          — target in-band RMS of band-limited noise, or None
    """

    start_s: float
    end_s: float
    band: tuple[float, float]
    channel: str
    tone_amplitude_uv: float | None = None
    noise_rms_uv: float | None = None


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
        band_segment_bursts: tuple[BandSegmentBurst, ...] = (),
    ):
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if not channels:
            raise ValueError("at least one channel required")
        self.sample_rate_hz = sample_rate_hz
        self.channels = tuple(channels)
        self.bursts = tuple(bursts)
        self.band_segment_bursts = tuple(band_segment_bursts)
        self.noise_uv_rms = float(noise_uv_rms)
        # Keep the base seed so band-noise bursts can derive stable,
        # reproducible per-burst RNG seeds (see next_chunk).
        self.seed = seed
        self._rng = np.random.default_rng(seed)
        self._sample_index = 0
        # Cache of pre-rendered, RMS-normalized band-noise per burst, keyed by
        # the burst's identity. Rendered once over the full burst window so the
        # in-band RMS normalization is global (no per-chunk filter transients).
        self._band_noise_cache: dict[int, np.ndarray] = {}
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

        # Add band-segment bursts (fuzzer path). Mirrors the SMRBurst tone
        # block intentionally; band-noise additionally supports a target RMS.
        if self.band_segment_bursts:
            nyq = self.sample_rate_hz / 2.0
            for i, bsb in enumerate(self.band_segment_bursts):
                if bsb.end_s <= start_s or bsb.start_s >= end_s:
                    continue
                if bsb.channel not in self.channels:
                    continue
                t_axis = (
                    np.arange(n_samples) + self._sample_index
                ) / self.sample_rate_hz
                mask = (t_axis >= bsb.start_s) & (t_axis < bsb.end_s)
                if not mask.any():
                    continue
                ch_idx = self.channels.index(bsb.channel)
                if bsb.tone_amplitude_uv is not None:
                    center = 0.5 * (bsb.band[0] + bsb.band[1])
                    sinusoid = bsb.tone_amplitude_uv * np.sin(
                        2 * np.pi * center * t_axis[mask]
                    )
                    out[mask, ch_idx] += sinusoid
                elif bsb.noise_rms_uv is not None:
                    band_noise = self._band_noise_for(i, bsb, nyq)
                    # Map global-window sample indices to this chunk's masked
                    # samples. The window starts at the first sample whose
                    # time >= start_s.
                    start_idx = int(np.ceil(bsb.start_s * self.sample_rate_hz))
                    abs_idx = (np.arange(n_samples) + self._sample_index)[mask]
                    rel = abs_idx - start_idx
                    valid = (rel >= 0) & (rel < band_noise.size)
                    if valid.any():
                        chunk_mask = np.where(mask)[0][valid]
                        out[chunk_mask, ch_idx] += band_noise[rel[valid]]

        self._sample_index += n_samples
        return out

    def _band_noise_for(
        self, burst_index: int, bsb: BandSegmentBurst, nyq: float
    ) -> np.ndarray:
        """Render (and cache) band-limited noise for a burst's full window,
        normalized to the target in-band RMS.

        Determinism: the per-burst RNG seed is derived from `self.seed` plus a
        stable CRC32 of the burst's fields (no Python `hash()`, so it is
        independent of PYTHONHASHSEED). Two `SignalGenerator`s built from the
        same Scenario therefore produce byte-identical band-noise.
        """
        cached = self._band_noise_cache.get(burst_index)
        if cached is not None:
            return cached
        start_idx = int(np.ceil(bsb.start_s * self.sample_rate_hz))
        end_idx = int(np.ceil(bsb.end_s * self.sample_rate_hz))
        length = max(end_idx - start_idx, 1)
        key = repr(
            (bsb.start_s, bsb.end_s, bsb.band, bsb.channel, bsb.noise_rms_uv)
        ).encode("utf-8")
        burst_seed = (int(self.seed) * 2_654_435_761 + zlib.crc32(key)) % (2**32)
        burst_rng = np.random.default_rng(burst_seed)
        wn = burst_rng.standard_normal(length)
        sos = butter(
            4, [bsb.band[0] / nyq, bsb.band[1] / nyq], btype="band", output="sos"
        )
        band_lim = sosfilt(sos, wn)
        current_rms = float(np.sqrt(np.mean(band_lim**2))) or 1.0
        band_lim *= bsb.noise_rms_uv / current_rms
        self._band_noise_cache[burst_index] = band_lim
        return band_lim


def render_scenario(scenario, *, channels: tuple[str, ...]) -> "SignalGenerator":
    """Build a `SignalGenerator` that, when iterated, produces the scenario's EEG.

    Each `BandSegment` becomes a `BandSegmentBurst` (tone OR band-noise). The
    existing `SMRBurst` path is left empty; the fuzzer drives everything through
    `band_segment_bursts`.
    """
    from .fuzz.scenario import BandNoise, Tone  # late import to avoid cycle

    bursts: list[BandSegmentBurst] = []
    for seg in scenario.segments:
        if isinstance(seg.content, Tone):
            bursts.append(
                BandSegmentBurst(
                    start_s=seg.start_s,
                    end_s=seg.end_s,
                    band=seg.band,
                    channel=seg.channel,
                    tone_amplitude_uv=seg.content.amplitude_uv,
                )
            )
        elif isinstance(seg.content, BandNoise):
            bursts.append(
                BandSegmentBurst(
                    start_s=seg.start_s,
                    end_s=seg.end_s,
                    band=seg.band,
                    channel=seg.channel,
                    noise_rms_uv=seg.content.rms_uv,
                )
            )
        else:  # pragma: no cover
            raise TypeError(f"unsupported BandSegment.content: {type(seg.content)}")

    return SignalGenerator(
        sample_rate_hz=scenario.sample_rate_hz,
        channels=channels,
        bursts=(),
        seed=scenario.seed,
        band_segment_bursts=tuple(bursts),
    )


__all__ = ["BandSegmentBurst", "SignalGenerator", "SMRBurst", "render_scenario"]
