# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Shared `Scenario` contract between the generator, renderer, oracle, and checker.

A scenario is a piecewise band-content-over-time specification: each
BandSegment injects either a pure Tone or band-limited noise of given RMS
into a target band/channel/time window. Bands not covered by any segment
stay at the pink-noise floor. The same Scenario is consumed independently
by the renderer (→ EEG samples) and the oracle (→ 3-valued expected event
timeline); neither consumes the other. Don't-care intervals carry a
reason code so the report can explain why the oracle stayed silent.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class Tone:
    """A pure sinusoid added at center frequency of the segment's band.

    For sharp envelope prediction (absolute thresholds, characterization).
    """
    amplitude_uv: float


@dataclass(frozen=True, slots=True)
class BandNoise:
    """Band-limited noise at a target in-band RMS amplitude.

    For shaping percentile-window distributions and realistic stimuli.
    """
    rms_uv: float


BandContent = Tone | BandNoise


@dataclass(frozen=True, slots=True)
class BandSegment:
    band: tuple[float, float]          # (low_hz, high_hz)
    channel: str
    start_s: float
    end_s: float
    content: BandContent

    def __post_init__(self) -> None:
        if not (self.band[0] < self.band[1]):
            raise ValueError(f"band must be (low<high); got {self.band}")
        if not (0.0 <= self.start_s < self.end_s):
            raise ValueError(f"need 0 <= start < end; got ({self.start_s}, {self.end_s})")

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def center_hz(self) -> float:
        return 0.5 * (self.band[0] + self.band[1])


@dataclass(frozen=True, slots=True)
class PhaseOverride:
    """Test-time override of `session.phases`. v1 default: warmup 3 s,
    training = duration_s - 3 s, cooldown 0 s — makes percentile-window
    scenarios tractable without changing protocol semantics under test."""
    warmup_s: float
    training_s: float
    cooldown_s: float


@dataclass(frozen=True, slots=True)
class Scenario:
    label: str
    duration_s: float
    sample_rate_hz: int
    segments: tuple[BandSegment, ...]
    controls: dict[str, float]
    coverage_tags: frozenset[str]
    phase_override: PhaseOverride | None = None
    seed: int = 42

    def __post_init__(self) -> None:
        if self.duration_s <= 0:
            raise ValueError(f"duration_s must be > 0; got {self.duration_s}")
        if self.sample_rate_hz <= 0:
            raise ValueError(f"sample_rate_hz must be > 0; got {self.sample_rate_hz}")


class DontCareReason(str, Enum):
    NEAR_BOUNDARY = "near_boundary"
    SETTLE_COLLAR = "settle_collar"
    PRE_WINDOW_FILL = "pre_window_fill"
    PHASE_MUTED = "phase_muted"
    INHIBIT_AMBIGUOUS = "inhibit_ambiguous"


class Verdict(str, Enum):
    PASS = "pass"
    MISSED = "missed"        # SHOULD-FIRE window had no event
    SPURIOUS = "spurious"    # event in a SHOULD-NOT-FIRE window
    DONT_CARE = "dont_care"  # event/no-event in a don't-care interval; counted, not asserted


__all__ = [
    "BandContent", "BandNoise", "BandSegment", "DontCareReason",
    "PhaseOverride", "Scenario", "Tone", "Verdict",
]
