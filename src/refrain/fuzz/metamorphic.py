# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Check the metamorphic properties of a measured sweep.

Up to two assertions per assertable group, and NO tolerance knob:

1. MONOTONICITY, direction-aware, and ONLY where `group.assert_monotonic`.
   `above` leaves push the reward up, `below` leaves push it down. The merged
   implementation asserted non-decreasing firing for every swept threshold,
   which is sign-wrong for inhibit leaves and false-failed every near-floor
   `below` protocol.

   HONEST LIMIT: `assert_monotonic` is False when the swept leaf's decision
   level is a percentile of the quiet envelope rather than an absolute value.
   Measured: a percentile(p70) decision level sits 1.24x the quiet-noise
   median; an absolute threshold sits ~7.2x it (`micro_single_above` /
   `micro_single_below`). A percentile boundary is therefore INSIDE the noise,
   and a coherent tone added there turns the envelope Rician, which thins the
   upper tail the percentile sits in -- exceedance can fall even as the tone
   grows. No drive amplitude is both near a percentile boundary and dominant
   over the noise, so per-realization monotonicity across that boundary is not
   a real property, not merely a hard-to-satisfy one. See "Iteration 2" / "The
   structural finding" in docs/superpowers/ci/metamorphic-tier-gate-result.md.
   Do not restore this assertion for percentile leaves thinking its absence is
   an oversight.

2. CONTRAST, always, for every assertable group (percentile leaves included).
   The top rung must close at least half the gap from the measured baseline to
   saturation. A flat sweep proves nothing and FAILS LOUD rather than passing
   vacuously — the calibrated-oracle gate finding was exactly a family of
   hollow passes. Contrast retains full differential power on its own: every
   engine mutant in tests/fuzz/test_engine_regression.py is caught via
   NO_CONTRAST, independent of whether monotonicity is asserted.

A slack term (`m[i] >= m[i-1] - k`) is deliberately absent: any k large enough to
absorb an inhibit inversion also hides a real regression. Robustness comes from
the metric (time-in-reward on a fixed noise realization) and the direction, not
from loosening the comparison.
"""
from __future__ import annotations

from dataclasses import dataclass

from .sweep import NONE, UP, SweepGroup

# The top rung must close at least this fraction of the baseline->saturation gap.
_CONTRAST_FRACTION = 0.5
# Metrics are means of a boolean array; only float noise needs absorbing.
_EPS = 1e-12
# A baseline plus at least one rung is the minimum shape an assertion needs.
_MIN_ASSERTABLE_MEMBERS = 2


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    tag: str
    direction: str
    baseline: float | None
    series: tuple[tuple[str, float], ...]
    assertable: bool
    reason: str | None
    # Mirrors SweepGroup.assert_monotonic. False on an assertable group means
    # only contrast was checked -- report.py must say so explicitly, never omit
    # it silently (see the module docstring's HONEST LIMIT).
    monotonic_asserted: bool


@dataclass(frozen=True, slots=True)
class MetamorphicViolation:
    tag: str
    kind: str                              # "monotonicity" | "no_contrast"
    direction: str
    baseline: float
    series: tuple[tuple[str, float], ...]
    detail: str


def _is_monotone(direction: str, values: list[float]) -> bool:
    if direction == UP:
        return all(values[i] >= values[i - 1] - _EPS for i in range(1, len(values)))
    return all(values[i] <= values[i - 1] + _EPS for i in range(1, len(values)))


def _contrast(direction: str, baseline: float, last: float) -> tuple[bool, str]:
    """Did the top rung move the metric at least half way to saturation?

    Saturation is 1.0 for an `up` sweep and 0.0 for a `down` one. The degenerate
    baselines are guarded explicitly: without that, `base == 1.0` on an `up`
    sweep satisfies `0 >= 0` — a reward that already holds on pure noise would
    pass. That is the hollow pass this tier exists to catch."""
    if direction == UP:
        if baseline >= 1.0 - _EPS:
            return False, ("baseline is already saturated (reward holds on noise "
                           "alone) — the sweep cannot demonstrate contrast")
        need = _CONTRAST_FRACTION * (1.0 - baseline)
        got = last - baseline
    else:
        if baseline <= _EPS:
            return False, ("baseline is already silent — the sweep cannot "
                           "demonstrate contrast")
        need = _CONTRAST_FRACTION * baseline
        got = baseline - last
    ok = got >= need - _EPS
    return ok, f"top rung moved {got:.4f}; needs >= {need:.4f} from baseline {baseline:.4f}"


def check_metamorphic(
    groups: list[SweepGroup], metrics: dict[str, float],
) -> tuple[list[MetamorphicViolation], list[SweepOutcome]]:
    """Evaluate every sweep group against its measured metrics.

    `metrics` maps scenario label -> time-in-reward. A missing metric raises
    KeyError: a sweep member that did not run must never be silently dropped."""
    violations: list[MetamorphicViolation] = []
    outcomes: list[SweepOutcome] = []
    for g in groups:
        rungs = sorted((m for m in g.members if m.index >= 0), key=lambda m: m.index)
        base_member = next((m for m in g.members if m.index < 0), None)
        series = tuple((m.scenario.label, metrics[m.scenario.label]) for m in rungs)
        baseline = metrics[base_member.scenario.label] if base_member else None

        if g.direction == NONE or baseline is None or len(series) < _MIN_ASSERTABLE_MEMBERS:
            outcomes.append(SweepOutcome(
                tag=g.tag, direction=g.direction, baseline=baseline, series=series,
                assertable=False, monotonic_asserted=False,
                reason=g.reason or "sweep has no baseline or too few rungs",
            ))
            continue

        values = [v for _, v in series]
        if g.assert_monotonic and not _is_monotone(g.direction, values):
            expected = "non-decreasing" if g.direction == UP else "non-increasing"
            violations.append(MetamorphicViolation(
                tag=g.tag, kind="monotonicity", direction=g.direction,
                baseline=baseline, series=series,
                detail=f"time-in-reward must be {expected} in drive",
            ))
        ok, detail = _contrast(g.direction, baseline, values[-1])
        if not ok:
            violations.append(MetamorphicViolation(
                tag=g.tag, kind="no_contrast", direction=g.direction,
                baseline=baseline, series=series, detail=detail,
            ))
        outcomes.append(SweepOutcome(
            tag=g.tag, direction=g.direction, baseline=baseline, series=series,
            assertable=True, reason=None, monotonic_asserted=g.assert_monotonic,
        ))
    return violations, outcomes


__all__ = ["MetamorphicViolation", "SweepOutcome", "check_metamorphic"]
