# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Checker: align actual events to oracle timeline; aggregate coverage;
fail loud on vacuity; evaluate metamorphic monotonicity over sweep groups."""
from __future__ import annotations

import re
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
) -> PerScenarioResult:
    """Classify a scenario's events against the oracle's expected timeline.

    Returns the worst per-event verdict. Raises VacuityError if the
    scenario made zero crisp assertions (no should-fire windows AND its
    entire timeline was DON'T-CARE).
    """
    n_crisp = len(expected.should_fire_event_samples)
    if not expected.dont_care_intervals:
        # No DON'T-CARE regions: the entire timeline is crisp SHOULD-NOT-FIRE,
        # which is itself an assertion (an event anywhere is SPURIOUS).
        has_non_dont_care_region = True
    else:
        total_samples_estimated = max(iv.end_sample for iv in expected.dont_care_intervals)
        has_non_dont_care_region = _has_crisp_should_not_fire(
            expected, total_samples_estimated
        )
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
    for sf in fires:
        if abs(sample - sf) <= collar_samples:
            return sf
    return None


def _max_verdict(a: Verdict, b: Verdict) -> Verdict:
    order = {Verdict.PASS: 0, Verdict.DONT_CARE: 1,
             Verdict.MISSED: 2, Verdict.SPURIOUS: 2}
    return a if order[a] >= order[b] else b


def _series_sort_key(label: str) -> tuple[int, str]:
    """Order a sweep series by its intended numeric magnitude.

    The reference implementation sorted members lexically, but Task-8 sweep
    labels carry their magnitude as a trailing integer (e.g. ``amp_5``,
    ``amp_15``, ``amp_25``). A pure lexical sort puts ``amp_15`` before
    ``amp_5`` (because the character ``'1'`` < ``'5'``), which scrambles the
    sweep's intended order and would falsely flag a monotonicity violation.

    We instead extract the trailing integer (the digits after the last
    underscore, or the last run of digits) and sort by it numerically, falling
    back to lexical order when no trailing integer is present so the ordering
    is still total and deterministic.
    """
    m = re.search(r"(\d+)\D*$", label)
    if m is None:
        return (0, label)
    return (int(m.group(1)), label)


@dataclass(frozen=True, slots=True)
class MetamorphicViolation:
    tag_group: str
    series: tuple[tuple[str, int], ...]   # (label, n_events) in series order


def check_metamorphic_monotonic(
    results: list[PerScenarioResult], *, tag_prefix: str,
) -> list[MetamorphicViolation]:
    """For each metamorphic group (tag starting with `tag_prefix`), assert that
    `n_events` is non-decreasing in the sweep's intended numeric order of the
    series members. Returns a list of violations (empty = all monotonic).

    Members are ordered by the trailing integer of their label (see
    ``_series_sort_key``) so that, e.g., ``amp_5`` precedes ``amp_15``
    precedes ``amp_25`` — the magnitude order the sweep intended — rather
    than the misleading lexical order.
    """
    groups: dict[str, list[PerScenarioResult]] = {}
    for r in results:
        for tag in r.coverage_tags:
            if tag.startswith(tag_prefix):
                groups.setdefault(tag, []).append(r)
    violations: list[MetamorphicViolation] = []
    for tag, members in groups.items():
        ordered = sorted(members, key=lambda r: _series_sort_key(r.label))
        series = tuple((m.label, m.n_events) for m in ordered)
        for i in range(1, len(series)):
            if series[i][1] < series[i - 1][1]:
                violations.append(MetamorphicViolation(tag_group=tag, series=series))
                break
    return violations


__all__ = [
    "ActualEvent",
    "MetamorphicViolation",
    "PerScenarioResult",
    "VacuityError",
    "check_metamorphic_monotonic",
    "check_scenario",
]
