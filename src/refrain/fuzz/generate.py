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

    The leaf is always evaluated post-window-fill, so percentile leaves use
    the long-form scenario; absolute leaves can use shorter scenarios.
    """
    fs = surface.sample_rate_hz
    leaf_id = f"leaf:{leaf.op}:{leaf.signal}:{leaf.threshold}"
    derive = next(d for d in surface.derives if d.name == leaf.signal)
    thr = next(t for t in surface.thresholds if t.name == leaf.threshold)

    # For percentile leaves we need a window-fill region first.
    needs_warmup = thr.kind == "percentile"
    fill_s = (thr.percentile_window_ms / 1000.0 + _FILL_PAD_S) if needs_warmup else 0.0
    total_s = fill_s + _SPIKE_S + _TAIL_PAD_S

    for side in ("true", "false"):
        amp = _amplitude_for_truth(leaf.op, derive, thr, side=side, fs=fs)
        segments = (
            (BandSegment(band=derive.band, channel=derive.channel,
                         start_s=fill_s, end_s=fill_s + _SPIKE_S,
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


def _dwell_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    """Hold the all-leaves-TRUE configuration (SMR up, theta/hbeta quiet) for a
    clearly-long vs clearly-short duration, to exercise the dwell boundary."""
    fs = surface.sample_rate_hz
    # TODO(v2): assumes the smr_cz layout (smr_envelope is the driven derive);
    # generalize to the output-relevant derive for arbitrary protocols.
    smr_derive = next(d for d in surface.derives if d.name == "smr_envelope")
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
        segments=(
            BandSegment(band=smr_derive.band, channel=smr_derive.channel,
                        start_s=fill_s, end_s=fill_s + hold_s_met,
                        content=Tone(amplitude_uv=30.0)),
        ),
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
        segments=(
            BandSegment(band=smr_derive.band, channel=smr_derive.channel,
                        start_s=fill_s, end_s=fill_s + hold_s_missed,
                        content=Tone(amplitude_uv=30.0)),
        ),
        controls={},
        coverage_tags=frozenset({"dwell:missed"}),
        phase_override=_training_phase(total_missed),
    )


def _percentile_warmup_scenarios(surface: LogicalSurface) -> Iterator[Scenario]:
    """Long quiet fill then a high-rank spike. Asserts that the warmup region
    is DON'T-CARE (oracle's pre-fill) and the post-fill spike fires."""
    fs = surface.sample_rate_hz
    fill_s = _longest_percentile_window_s(surface) + _FILL_PAD_S
    total_s = fill_s + _SPIKE_S + _TAIL_PAD_S

    # TODO(v2): assumes the smr_cz layout (see _dwell_scenarios).
    smr_derive = next(d for d in surface.derives if d.name == "smr_envelope")
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
    # TODO(v2): smr_cz-specific driven derive (see _dwell_scenarios).
    smr_derive = next(d for d in surface.derives if d.name == "smr_envelope")
    fill_s = _longest_percentile_window_s(surface) + _FILL_PAD_S
    for f in fractions:
        hold_s = dwell_s * f
        total_s = fill_s + hold_s + _TAIL_PAD_S
        yield Scenario(
            label=f"hold_sweep:{f:g}x_dwell",
            duration_s=total_s,
            sample_rate_hz=fs,
            segments=(
                BandSegment(band=smr_derive.band, channel=smr_derive.channel,
                            start_s=fill_s, end_s=fill_s + hold_s,
                            content=Tone(amplitude_uv=30.0)),
            ),
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
