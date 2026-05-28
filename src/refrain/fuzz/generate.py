# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Directed scenario generator: walks the LogicalSurface and emits one
Scenario per pivotal coverage target. Each scenario carries `coverage_tags`
identifying which branches it intends to exercise."""
from __future__ import annotations

from collections.abc import Iterator

from .scenario import BandSegment, PhaseOverride, Scenario, Tone
from .surface import (
    ConditionLeaf, ConditionNode, DeriveSurface,
    LogicalSurface, ThresholdSurface,
)

# Default phase override for v1 — tractable runs without changing semantics.
# Percentile-warmup scenarios override this further.
_DEFAULT_WARMUP_S = 2.0
_DEFAULT_COOLDOWN_S = 0.5


def generate_directed_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    """Yield the directed-coverage scenario set for v1."""
    fs = surface.sample_rate_hz

    # Negative control: all quiet.
    yield Scenario(
        label="negative_control_quiet",
        duration_s=8.0,
        sample_rate_hz=fs,
        segments=(),
        controls={},
        coverage_tags=frozenset({"negative_control"}),
        phase_override=PhaseOverride(_DEFAULT_WARMUP_S, 5.5, _DEFAULT_COOLDOWN_S),
    )

    # Per-leaf pivotal: drive one leaf TRUE / FALSE with the others favourable.
    # For percentile leaves, "favourable" means a tractable post-fill window.
    for leaf in _all_leaves(surface.reward_condition):
        yield from _pivotal_scenarios_for_leaf(leaf, surface)

    # Dwell met + missed (uses an all-leaves-true configuration).
    yield from _dwell_scenarios(surface)

    # Percentile warm-up scenario for the longest percentile window.
    yield from _percentile_warmup_scenarios(surface)


def _all_leaves(node) -> Iterator[ConditionLeaf]:
    if isinstance(node, ConditionLeaf):
        yield node
        return
    for c in node.children:
        yield from _all_leaves(c)


def _pivotal_scenarios_for_leaf(
    leaf: ConditionLeaf, surface: LogicalSurface
) -> Iterator[Scenario]:
    """For a leaf, emit (TRUE-with-margin) and (FALSE-with-margin) scenarios.

    Strategy: TRUE-pivotal drives `leaf` to its TRUE side and leaves the other
    leaves at a favourable baseline (no specific suppression — quiet); FALSE-
    pivotal drives `leaf` to its FALSE side.

    The leaf is always evaluated post-window-fill, so percentile leaves use
    the long-form scenario; absolute leaves can use shorter scenarios.
    """
    fs = surface.sample_rate_hz
    leaf_id = f"leaf:{leaf.op}:{leaf.signal}:{leaf.threshold}"
    derive = next(d for d in surface.derives if d.name == leaf.signal)
    thr = next(t for t in surface.thresholds if t.name == leaf.threshold)

    # For percentile leaves we need a window-fill region first.
    needs_warmup = thr.kind == "percentile"
    fill_s = (thr.percentile_window_ms / 1000.0 + 2.0) if needs_warmup else 0.0
    spike_s = 6.0
    total_s = fill_s + spike_s + 2.0

    # TRUE scenario: drive the leaf TRUE (with margin).
    true_amp = _amplitude_for_truth(leaf.op, derive, thr, side="true", fs=fs)
    yield Scenario(
        label=f"{leaf_id}:true",
        duration_s=total_s,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=derive.band, channel=derive.channel,
                        start_s=fill_s, end_s=fill_s + spike_s,
                        content=Tone(amplitude_uv=true_amp)),
        ) if true_amp > 0 else (),
        controls={},
        coverage_tags=frozenset({f"{leaf_id}:true"}),
        phase_override=PhaseOverride(_DEFAULT_WARMUP_S, total_s - _DEFAULT_WARMUP_S - _DEFAULT_COOLDOWN_S, _DEFAULT_COOLDOWN_S),
    )

    # FALSE scenario: drive the leaf FALSE (with margin).
    false_amp = _amplitude_for_truth(leaf.op, derive, thr, side="false", fs=fs)
    yield Scenario(
        label=f"{leaf_id}:false",
        duration_s=total_s,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=derive.band, channel=derive.channel,
                        start_s=fill_s, end_s=fill_s + spike_s,
                        content=Tone(amplitude_uv=false_amp)),
        ) if false_amp > 0 else (),
        controls={},
        coverage_tags=frozenset({f"{leaf_id}:false"}),
        phase_override=PhaseOverride(_DEFAULT_WARMUP_S, total_s - _DEFAULT_WARMUP_S - _DEFAULT_COOLDOWN_S, _DEFAULT_COOLDOWN_S),
    )


def _amplitude_for_truth(
    leaf_op: str, derive: DeriveSurface, thr: ThresholdSurface, *,
    side: str, fs: int,
) -> float:
    """Pick a tone amplitude that drives the leaf clearly TRUE or FALSE.

    For absolute thresholds we use a 2× margin on each side; for percentile
    thresholds we choose amplitudes that produce a clearly high or clearly
    low rank within the window (the warmup-fill region is quiet, so any
    spike has high rank → TRUE for above; FALSE side uses zero amplitude
    so the rank stays low).
    """
    if thr.kind == "absolute":
        if leaf_op == "above":
            target_env = (thr.absolute_uv * 2.0) if side == "true" else (thr.absolute_uv * 0.25)
        else:  # below
            target_env = (thr.absolute_uv * 0.25) if side == "true" else (thr.absolute_uv * 2.0)
    else:  # percentile — pick amplitudes by rank intent
        if side == "true" and leaf_op == "above":
            target_env = 30.0   # clearly above the quiet-fill distribution
        elif side == "false" and leaf_op == "above":
            target_env = 0.0    # no spike → rank stays low
        elif side == "true" and leaf_op == "below":
            target_env = 0.0    # no spike → rank low → below TRUE
        else:
            target_env = 30.0   # high rank → below FALSE
    # Convert target envelope to required tone amplitude via the bandpass gain
    # at the derive's band center, evaluated on the surface's sample rate.
    from .oracle import bandpass_gain_at
    if target_env <= 0 or derive.sos is None:
        return 0.0
    center_hz = 0.5 * (derive.band[0] + derive.band[1])
    gain = bandpass_gain_at(derive.sos, freq_hz=center_hz, fs=fs)
    return target_env / max(gain, 1e-3)


def _dwell_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    fs = surface.sample_rate_hz
    # Hold the all-leaves-true configuration: SMR up, theta down (quiet), hbeta quiet.
    smr_derive = next(d for d in surface.derives if d.name == "smr_envelope")
    fill_s = 122.0   # post-fill 2-min window
    dwell_s = surface.dwell_ms / 1000.0
    settle_s = 1.0   # rough collar pad

    # MET: hold tone for 2× dwell (clearly long enough).
    hold_s_met = max(2.0 * dwell_s + settle_s, 1.0)
    total_met = fill_s + hold_s_met + 2.0
    yield Scenario(
        label="dwell_met",
        duration_s=total_met,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=smr_derive.band, channel=smr_derive.channel,
                        start_s=fill_s, end_s=fill_s + hold_s_met,
                        content=Tone(amplitude_uv=30.0)),
        ),
        controls={},
        coverage_tags=frozenset({"dwell:met"}),
        phase_override=PhaseOverride(_DEFAULT_WARMUP_S,
                                     total_met - _DEFAULT_WARMUP_S - _DEFAULT_COOLDOWN_S,
                                     _DEFAULT_COOLDOWN_S),
    )

    # MISSED: hold for dwell - 100 ms (clearly too short).
    hold_s_missed = max(0.1, dwell_s - 0.1)
    total_missed = fill_s + hold_s_missed + 2.0
    yield Scenario(
        label="dwell_missed",
        duration_s=total_missed,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=smr_derive.band, channel=smr_derive.channel,
                        start_s=fill_s, end_s=fill_s + hold_s_missed,
                        content=Tone(amplitude_uv=30.0)),
        ),
        controls={},
        coverage_tags=frozenset({"dwell:missed"}),
        phase_override=PhaseOverride(_DEFAULT_WARMUP_S,
                                     total_missed - _DEFAULT_WARMUP_S - _DEFAULT_COOLDOWN_S,
                                     _DEFAULT_COOLDOWN_S),
    )


def _percentile_warmup_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    """Long quiet fill then a high-rank spike. Asserts that the warmup region
    is DON'T-CARE (oracle's pre-fill) and the post-fill spike fires."""
    fs = surface.sample_rate_hz
    longest_window_ms = max(
        (t.percentile_window_ms for t in surface.thresholds if t.kind == "percentile"),
        default=0.0,
    )
    fill_s = longest_window_ms / 1000.0 + 2.0
    spike_s = 6.0
    total_s = fill_s + spike_s + 2.0

    smr_derive = next(d for d in surface.derives if d.name == "smr_envelope")
    yield Scenario(
        label="percentile_warmup_then_spike",
        duration_s=total_s,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=smr_derive.band, channel=smr_derive.channel,
                        start_s=fill_s, end_s=fill_s + spike_s,
                        content=Tone(amplitude_uv=40.0)),
        ),
        controls={},
        coverage_tags=frozenset({"percentile:warmup_then_spike"}),
        phase_override=PhaseOverride(_DEFAULT_WARMUP_S,
                                     total_s - _DEFAULT_WARMUP_S - _DEFAULT_COOLDOWN_S,
                                     _DEFAULT_COOLDOWN_S),
    )


__all__ = ["generate_directed_scenarios"]
