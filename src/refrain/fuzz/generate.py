# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Directed scenario generator: walks the LogicalSurface and emits one
Scenario per pivotal coverage target. Each scenario carries `coverage_tags`
identifying which branches it intends to exercise."""
from __future__ import annotations

from collections.abc import Iterator

from .oracle import bandpass_gain_at
from .scenario import BandSegment, PhaseOverride, Scenario, Tone
from .surface import ConditionLeaf, DeriveSurface, LogicalSurface, ThresholdSurface

# Default phase override for v1 — tractable runs without changing semantics.
# Percentile-warmup scenarios override this further.
_DEFAULT_WARMUP_S = 2.0
_DEFAULT_COOLDOWN_S = 0.5

# How long a pivotal/warmup tone is held. Comfortably past filter settle + dwell
# so the leaf's truth value is unambiguous within the spike window.
_SPIKE_S = 6.0

# Seconds added after a percentile window fills, so the rolling window is fully
# populated before the spike (the oracle treats the fill region as DON'T-CARE).
_FILL_PAD_S = 2.0
# Trailing buffer after the spike so the scenario doesn't end mid-event.
_TAIL_PAD_S = 2.0


def _training_phase(total_s: float) -> PhaseOverride:
    """Phase override that mutes a warmup head + cooldown tail, leaving the
    middle as the (unmuted) training phase."""
    training_s = total_s - _DEFAULT_WARMUP_S - _DEFAULT_COOLDOWN_S
    return PhaseOverride(_DEFAULT_WARMUP_S, training_s, _DEFAULT_COOLDOWN_S)


def _longest_percentile_window_s(surface: LogicalSurface) -> float:
    """Longest percentile rolling-window across the surface's thresholds, in
    seconds (0.0 if there are no percentile thresholds)."""
    longest_ms = max(
        (t.percentile_window_ms for t in surface.thresholds if t.kind == "percentile"),
        default=0.0,
    )
    return longest_ms / 1000.0


def generate_directed_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    """Yield the directed-coverage scenario set for v1."""
    fs = surface.sample_rate_hz

    # Negative control: all quiet. Run long enough that the longest percentile
    # window fills, leaving a crisp post-fill SHOULD-NOT-FIRE tail; an 8 s run
    # would sit entirely inside the oracle's PRE_WINDOW_FILL DON'T-CARE region
    # and be vacuous (the checker fails loud on that).
    neg_fill_s = _longest_percentile_window_s(surface) + _FILL_PAD_S
    neg_total_s = neg_fill_s + _SPIKE_S + _TAIL_PAD_S
    yield Scenario(
        label="negative_control_quiet",
        duration_s=neg_total_s,
        sample_rate_hz=fs,
        segments=(),
        controls={},
        coverage_tags=frozenset({"negative_control"}),
        phase_override=_training_phase(neg_total_s),
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

    Every pivotal scenario fills the longest percentile window across the
    surface before the spike (see the comment below) so the oracle's pre-fill
    DON'T-CARE region cannot swallow the whole scenario — this holds even when
    the pivoted leaf is absolute and needs no warmup of its own.
    """
    fs = surface.sample_rate_hz
    leaf_id = f"leaf:{leaf.op}:{leaf.signal}:{leaf.threshold}"
    derive = next(d for d in surface.derives if d.name == leaf.signal)
    thr = next(t for t in surface.thresholds if t.name == leaf.threshold)

    # The reward condition (an all_of/any_of tree) is predicted as a whole, so
    # its truth at any sample depends on EVERY leaf — including percentile leaves
    # whose pre-window-fill region the oracle marks DON'T-CARE. Even when the
    # pivoted leaf is absolute (its own threshold needs no warmup), we must still
    # fill the LONGEST percentile window across the surface before the spike;
    # otherwise a short (e.g. 8 s) scenario sits entirely inside the oracle's
    # pre-fill DON'T-CARE region and is vacuous (the checker fails loud on that —
    # see the negative-control note above and _dwell/_percentile_warmup).
    surface_fill_s = _longest_percentile_window_s(surface)
    fill_s = (surface_fill_s + _FILL_PAD_S) if surface_fill_s > 0.0 else 0.0
    total_s = fill_s + _SPIKE_S + _TAIL_PAD_S

    for side in ("true", "false"):
        amp = _amplitude_for_truth(leaf.op, derive, thr, side=side, fs=fs)
        # For a `below` FALSE scenario the tail (after the spike) brings the
        # condition back to True, creating a tail SHOULD-FIRE the oracle defers
        # to the collar end. The engine fires earlier (within the collar) and
        # stays held, so the collar-end expected event is MISSED. Fix: extend
        # the tone to the end of the scenario so the condition stays False
        # throughout and no tail expected event is produced. Safe for multi-
        # leaf protocols too: the all_of is already False without a tail fire.
        seg_end_s = (
            total_s if (leaf.op == "below" and side == "false") else fill_s + _SPIKE_S
        )
        segments = (
            (BandSegment(band=derive.band, channel=derive.channel,
                         start_s=fill_s, end_s=seg_end_s,
                         content=Tone(amplitude_uv=amp)),)
            if amp > 0 else ()
        )
        yield Scenario(
            label=f"{leaf_id}:{side}",
            duration_s=total_s,
            sample_rate_hz=fs,
            segments=segments,
            controls={},
            coverage_tags=frozenset({f"{leaf_id}:{side}"}),
            phase_override=_training_phase(total_s),
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
        # A spike → high rank → "above" TRUE / "below" FALSE; no spike → low rank.
        wants_high_rank = (side == "true") == (leaf_op == "above")
        target_env = 30.0 if wants_high_rank else 0.0
    if target_env <= 0 or derive.sos is None:
        return 0.0
    # Convert target envelope to required tone amplitude via the bandpass gain
    # at the derive's band center, evaluated on the surface's sample rate.
    center_hz = 0.5 * (derive.band[0] + derive.band[1])
    gain = bandpass_gain_at(derive.sos, freq_hz=center_hz, fs=fs)
    return target_env / max(gain, 1e-3)


def _driven_leaf(surface: LogicalSurface) -> ConditionLeaf:
    """The leaf the reward-positive scenarios drive: the first `above` leaf, else
    the first leaf (a sole `below`)."""
    leaves = list(_all_leaves(surface.reward_condition))
    for leaf in leaves:
        if leaf.op == "above":
            return leaf
    return leaves[0]


def _reward_positive_segments(
    surface: LogicalSurface, *, start_s: float, hold_s: float
) -> tuple:
    """BandSegments that hold the reward's driven leaf TRUE for `hold_s` from
    `start_s`. above: a 30 uV tone over [start, start+hold] (unchanged behavior).
    below: a PRE-ROLL tone (clearly above threshold => below FALSE, resets dwell)
    over [start-preroll, start], quiet over the hold window (below TRUE), then an
    END-ROLL tone from [start+hold, start+hold+preroll]. The end-roll creates a
    second condition transition so that the two settle collars overlap and cancel
    the dwell prediction — mirroring how the `above` tone's start/end collars work
    — so the actual engine event lands in a DON'T-CARE region and the check passes.
    (preroll == _TAIL_PAD_S == 2.0 s, so the end-roll always terminates at total_s.)
    """
    leaf = _driven_leaf(surface)
    d = next(dv for dv in surface.derives if dv.name == leaf.signal)
    if leaf.op != "below":
        return (BandSegment(band=d.band, channel=d.channel,
                            start_s=start_s, end_s=start_s + hold_s,
                            content=Tone(amplitude_uv=30.0)),)
    thr = next(t for t in surface.thresholds if t.name == leaf.threshold)
    # Gain-compensated amplitude that clearly drives `below` FALSE (band above thr).
    false_amp = _amplitude_for_truth("below", d, thr, side="false", fs=surface.sample_rate_hz)
    preroll = 2.0
    segs: list = []
    if false_amp > 0:
        if start_s > 0.0:
            segs.append(BandSegment(band=d.band, channel=d.channel,
                                    start_s=max(0.0, start_s - preroll), end_s=start_s,
                                    content=Tone(amplitude_uv=false_amp)))
        # End-roll: restores below FALSE after the hold, creating a second transition
        # whose settle collar overlaps the first and cancels the dwell prediction.
        segs.append(BandSegment(band=d.band, channel=d.channel,
                                start_s=start_s + hold_s,
                                end_s=start_s + hold_s + preroll,
                                content=Tone(amplitude_uv=false_amp)))
    return tuple(segs)


def _dwell_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    """Hold the all-leaves-TRUE configuration (SMR up, theta/hbeta quiet) for a
    clearly-long vs clearly-short duration, to exercise the dwell boundary."""
    fs = surface.sample_rate_hz
    fill_s = _longest_percentile_window_s(surface) + _FILL_PAD_S  # post-fill window
    dwell_s = surface.dwell_ms / 1000.0
    settle_s = 1.0   # rough collar pad

    # MET: hold tone for 2× dwell (clearly long enough).
    hold_s_met = max(2.0 * dwell_s + settle_s, 1.0)
    total_met = fill_s + hold_s_met + _TAIL_PAD_S
    yield Scenario(
        label="dwell_met",
        duration_s=total_met,
        sample_rate_hz=fs,
        segments=_reward_positive_segments(surface, start_s=fill_s, hold_s=hold_s_met),
        controls={},
        coverage_tags=frozenset({"dwell:met"}),
        phase_override=_training_phase(total_met),
    )

    # MISSED: hold for dwell - 100 ms (clearly too short).
    hold_s_missed = max(0.1, dwell_s - 0.1)
    total_missed = fill_s + hold_s_missed + _TAIL_PAD_S
    yield Scenario(
        label="dwell_missed",
        duration_s=total_missed,
        sample_rate_hz=fs,
        segments=_reward_positive_segments(surface, start_s=fill_s, hold_s=hold_s_missed),
        controls={},
        coverage_tags=frozenset({"dwell:missed"}),
        phase_override=_training_phase(total_missed),
    )


def _percentile_warmup_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    """Long quiet fill then a high-rank spike. Asserts that the warmup region
    is DON'T-CARE (oracle's pre-fill) and the post-fill spike fires."""
    if _longest_percentile_window_s(surface) <= 0.0:
        return  # no percentile threshold — warmup scenario is vacuous/wrong
    fs = surface.sample_rate_hz
    fill_s = _longest_percentile_window_s(surface) + _FILL_PAD_S
    total_s = fill_s + _SPIKE_S + _TAIL_PAD_S

    # Use the first reward-condition leaf's signal as the driven derive (see _dwell_scenarios).
    first_leaf = next(_all_leaves(surface.reward_condition))
    smr_derive = next(d for d in surface.derives if d.name == first_leaf.signal)
    yield Scenario(
        label="percentile_warmup_then_spike",
        duration_s=total_s,
        sample_rate_hz=fs,
        segments=(
            BandSegment(band=smr_derive.band, channel=smr_derive.channel,
                        start_s=fill_s, end_s=fill_s + _SPIKE_S,
                        content=Tone(amplitude_uv=40.0)),  # extra headroom over pivotal 30 µV
        ),
        controls={},
        coverage_tags=frozenset({"percentile:warmup_then_spike"}),
        phase_override=_training_phase(total_s),
    )


def generate_characterization_probe(surface: LogicalSurface) -> Iterator[Scenario]:
    """Tone sweep at one frequency per derive's band center (v1 minimum). Each
    scenario injects a single tone; the checker asserts the corresponding
    derive's envelope is high and others low — verifying each band peaks where
    declared."""
    if isinstance(surface.reward_condition, ConditionLeaf):
        return  # single-leaf: pivotal scenarios already drive the reward band
    fs = surface.sample_rate_hz
    duration = 6.0
    for derive in surface.derives:
        center = 0.5 * (derive.band[0] + derive.band[1])
        yield Scenario(
            label=f"probe:tone_{center:.1f}hz",
            duration_s=duration,
            sample_rate_hz=fs,
            segments=(
                BandSegment(band=derive.band, channel=derive.channel,
                            start_s=1.0, end_s=duration - 0.5,
                            content=Tone(amplitude_uv=20.0)),
            ),
            controls={},
            coverage_tags=frozenset({
                f"probe:tone_{center:.1f}hz",
                f"probe:band:{derive.name}",
            }),
            # Short warmup/cooldown: a characterization probe only needs the
            # envelope to settle, not a percentile window to fill, so it uses a
            # tighter collar than the _training_phase default.
            phase_override=PhaseOverride(0.5, duration - 1.0, 0.5),
        )


def generate_rank_sweep(surface: LogicalSurface) -> Iterator[Scenario]:
    """For each percentile threshold, emit a series of scenarios with
    monotonically increasing tone amplitudes (→ increasing rank within the
    post-fill window). The checker asserts firing rate is non-decreasing."""
    fs = surface.sample_rate_hz
    amplitudes = (5.0, 15.0, 25.0, 40.0)
    for thr in surface.thresholds:
        if thr.kind != "percentile":
            continue
        derive = next(d for d in surface.derives if d.name == thr.signal)
        fill_s = thr.percentile_window_ms / 1000.0 + _FILL_PAD_S
        total_s = fill_s + _SPIKE_S + _TAIL_PAD_S
        for amp in amplitudes:
            segments = (
                (BandSegment(band=derive.band, channel=derive.channel,
                             start_s=fill_s, end_s=fill_s + _SPIKE_S,
                             content=Tone(amplitude_uv=amp)),)
                if amp > 0 else ()
            )
            yield Scenario(
                label=f"rank_sweep:{thr.name}:amp_{amp:g}",
                duration_s=total_s,
                sample_rate_hz=fs,
                segments=segments,
                controls={},
                coverage_tags=frozenset({
                    f"metamorphic:rank_sweep:{thr.name}",
                    f"rank_sweep:amp_{amp:g}",
                }),
                phase_override=_training_phase(total_s),
            )


def generate_hold_duration_sweep(surface: LogicalSurface) -> Iterator[Scenario]:
    """Sweep the hold duration for the all-leaves-TRUE configuration. Firing
    rate must be non-decreasing as hold lengthens past dwell."""
    fs = surface.sample_rate_hz
    dwell_s = surface.dwell_ms / 1000.0
    fractions = (0.5, 0.9, 1.5, 2.5, 5.0)
    fill_s = _longest_percentile_window_s(surface) + _FILL_PAD_S
    for f in fractions:
        hold_s = dwell_s * f
        total_s = fill_s + hold_s + _TAIL_PAD_S
        yield Scenario(
            label=f"hold_sweep:{f:g}x_dwell",
            duration_s=total_s,
            sample_rate_hz=fs,
            segments=_reward_positive_segments(surface, start_s=fill_s, hold_s=hold_s),
            controls={},
            coverage_tags=frozenset({
                "metamorphic:hold_duration_sweep",
                f"hold_sweep:{f:g}x_dwell",
            }),
            phase_override=_training_phase(total_s),
        )


__all__ = [
    "generate_directed_scenarios",
    "generate_characterization_probe",
    "generate_rank_sweep",
    "generate_hold_duration_sweep",
]
