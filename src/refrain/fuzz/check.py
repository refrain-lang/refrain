# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Checker: align actual events to oracle timeline; aggregate coverage;
fail loud on vacuity."""
from __future__ import annotations

from dataclasses import dataclass

from .oracle import DontCareInterval, ExpectedTimeline
from .scenario import Verdict


class VacuityError(AssertionError):
    """A scenario produced zero crisp assertions — the test was vacuous.
    By design, vacuous scenarios fail loud rather than silently passing."""


@dataclass(frozen=True, slots=True)
class ActualEvent:
    sample: int            # sample index within the scenario's timeline
    kind: str              # "event" | "value" | ...
    channel: str           # output name, e.g. "audio_chime"


@dataclass(frozen=True, slots=True)
class PerScenarioResult:
    label: str
    verdict: Verdict        # worst case across sub-verdicts
    n_events: int
    n_crisp_assertions: int
    n_dont_care_intervals: int
    coverage_tags: frozenset[str]
    details: tuple = ()     # optional per-sample/per-event diagnostics


def check_scenario(
    *, scenario_label: str,
    expected: ExpectedTimeline,
    actual: list[ActualEvent],
    fs: int,
    collar_samples: int,
    coverage_tags: frozenset[str],
    total_samples: int | None = None,
) -> PerScenarioResult:
    """Classify a scenario's events against the oracle's expected timeline.

    Returns the worst per-event verdict. Raises VacuityError if the
    scenario made zero crisp assertions (no should-fire windows AND its
    entire timeline was DON'T-CARE).

    `total_samples` is the scenario's timeline length in samples. Pass it so
    the vacuity check knows the true end of the timeline: a DON'T-CARE region
    that does not reach the end still leaves a crisp SHOULD-NOT-FIRE tail. When
    omitted it is estimated from the furthest DON'T-CARE interval, which can
    UNDER-count the crisp tail — callers that have the real length (the CLI
    driver) should always pass it. `fs` is currently unused; kept for API
    stability and future sample/time conversions.
    """
    n_crisp = len(expected.should_fire_event_samples)
    if not expected.dont_care_intervals:
        # No DON'T-CARE regions: the entire timeline is crisp SHOULD-NOT-FIRE,
        # which is itself an assertion (an event anywhere is SPURIOUS).
        has_non_dont_care_region = True
    else:
        dc_extent = max(iv.end_sample for iv in expected.dont_care_intervals)
        # Prefer the caller-supplied length; never go below the DON'T-CARE
        # extent (so a fully-covered timeline is still detected as vacuous).
        total = max(total_samples, dc_extent) if total_samples is not None else dc_extent
        has_non_dont_care_region = _has_crisp_should_not_fire(expected, total)
    if n_crisp == 0 and not has_non_dont_care_region:
        raise VacuityError(
            f"scenario {scenario_label!r}: zero crisp assertions "
            f"(no SHOULD-FIRE samples and the timeline is fully DON'T-CARE). "
            f"This is a generator bug, not a pass."
        )

    worst: Verdict = Verdict.PASS
    matched_fire_samples = set()
    for ev in actual:
        if _in_dont_care(ev.sample, expected.dont_care_intervals):
            continue
        match = _nearest_should_fire(ev.sample, expected.should_fire_event_samples,
                                     collar_samples)
        if match is not None:
            matched_fire_samples.add(match)
        else:
            worst = _max_verdict(worst, Verdict.SPURIOUS)

    for sf in expected.should_fire_event_samples:
        if sf not in matched_fire_samples:
            worst = _max_verdict(worst, Verdict.MISSED)

    crisp_assertions = n_crisp + (1 if has_non_dont_care_region else 0)
    return PerScenarioResult(
        label=scenario_label,
        verdict=worst,
        n_events=len(actual),
        n_crisp_assertions=crisp_assertions,
        n_dont_care_intervals=len(expected.dont_care_intervals),
        coverage_tags=coverage_tags,
    )


def _has_crisp_should_not_fire(expected: ExpectedTimeline, total: int) -> bool:
    if total == 0:
        return False
    covered = 0
    sorted_iv = sorted(expected.dont_care_intervals, key=lambda iv: iv.start_sample)
    cursor = 0
    for iv in sorted_iv:
        if iv.end_sample <= cursor:
            continue
        s = max(cursor, iv.start_sample)
        covered += max(0, iv.end_sample - s)
        cursor = max(cursor, iv.end_sample)
    return covered < total


def _in_dont_care(sample: int, intervals: list[DontCareInterval]) -> bool:
    return any(iv.start_sample <= sample < iv.end_sample for iv in intervals)


def _nearest_should_fire(sample: int, fires: list[int], collar_samples: int) -> int | None:
    """The should-fire sample closest to `sample` within the collar, or None.

    Picking the geometrically nearest (not the first within range) avoids a
    false MISSED when two should-fire samples sit closer together than the
    collar and an event lands between them."""
    candidates = [sf for sf in fires if abs(sample - sf) <= collar_samples]
    if not candidates:
        return None
    return min(candidates, key=lambda sf: abs(sample - sf))


def _max_verdict(a: Verdict, b: Verdict) -> Verdict:
    order = {Verdict.PASS: 0, Verdict.DONT_CARE: 1,
             Verdict.MISSED: 2, Verdict.SPURIOUS: 2}
    return a if order[a] >= order[b] else b


__all__ = [
    "ActualEvent",
    "PerScenarioResult",
    "VacuityError",
    "check_scenario",
]
