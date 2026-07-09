# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Plan the metamorphic sweeps: direction, anchors, geometry, segments.

The fuzzer fixes the noise seed and varies only tone amplitude, so every member
of a sweep sees a BYTE-IDENTICAL noise realization (guarded by
tests/fuzz/test_engine.py::test_noise_is_bit_identical_across_amplitudes). A
sweep is therefore a controlled A/B on one realization, and "more in-band drive
pushes the leaf harder" is a real, assertable ordering.

Pure module: a function of the surface + a measured noise floor. No engine.
"""
from __future__ import annotations

from dataclasses import dataclass

from .oracle import bandpass_gain_at
from .scenario import BandSegment, PhaseOverride, Scenario, Tone
from .surface import (
    ConditionLeaf,
    DeriveSurface,
    LogicalSurface,
    derive_for,
    reward_leaves,
    threshold_for,
)

UP = "up"
DOWN = "down"
NONE = "none"

# Ladder rungs, in anchor units (the anchor is the leaf's decision level).
_LADDER = (0.5, 1.0, 2.0, 4.0)
# Drive applied to non-swept leaves to hold them favourable, in anchor units.
_HI = 4.0
# Hold-sweep rungs, in dwell units.
_HOLD_FRACTIONS = (0.5, 0.9, 1.5, 2.5, 5.0)

# The spike must stay a small fraction of the percentile's rolling buffer, or it
# shifts the percentile onto itself and the sweep flattens. `headroom` is the
# distance from the target percentile to the nearer end of the distribution.
_SPIKE_FRACTION_SAFETY = 2.5
_MIN_FILL_S = 8.0
_MIN_SPIKE_S = 4.0
_MIN_METRIC_WINDOW_S = 0.5
_TAIL_PAD_S = 2.0
_WARMUP_S = 2.0
_COOLDOWN_S = 0.5


@dataclass(frozen=True, slots=True)
class SweepGeometry:
    fill_s: float
    spike_s: float
    total_s: float
    metric_window_s: tuple[float, float]
    usable: bool = True
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SweepMember:
    scenario: Scenario
    index: int          # -1 = baseline (no drive); 0..n-1 = rungs, ascending drive


@dataclass(frozen=True, slots=True)
class SweepGroup:
    tag: str
    direction: str                       # UP | DOWN | NONE
    reason: str | None                   # why NONE
    members: tuple[SweepMember, ...]
    metric_window_s: tuple[float, float]


def sweep_direction(surface: LogicalSurface, derive_name: str) -> tuple[str, str | None]:
    """Which way the reward metric must move as this derive's drive increases.

    `above()` and `below()` are monotone in their signal, and `all_of`/`any_of`
    are monotone in each child's truth (there is no negation), so a derive that
    feeds only `above` leaves can only push the reward up, and one that feeds
    only `below` leaves can only push it down. Anything else asserts nothing.
    """
    leaves = [x for x in reward_leaves(surface) if x.signal == derive_name]
    if not leaves:
        return NONE, "derive does not feed the reward condition"
    ops = {x.op for x in leaves}
    if len(ops) > 1:
        return NONE, "derive feeds both above() and below() leaves"
    if len({x.threshold for x in leaves}) > 1:
        return NONE, "derive feeds several leaves with different thresholds"
    return (UP if ops == {"above"} else DOWN), None


def leaf_anchor_uv(leaf: ConditionLeaf, surface: LogicalSurface,
                   noise_floor: dict[str, float]) -> float | None:
    """The leaf's decision level in envelope microvolts — where its truth flips.

    absolute: the threshold itself. percentile: the measured noise floor, because
    a percentile over a mostly-quiet rolling window sits at the noise level.
    A ladder anchored anywhere else does not straddle the boundary."""
    thr = threshold_for(surface, leaf.threshold)
    if thr.kind == "absolute":
        return thr.absolute_uv
    if thr.kind == "percentile":
        return noise_floor.get(leaf.signal)
    return None


def _percentile_headroom(surface: LogicalSurface) -> float | None:
    """min(target, 100-target)/100 over the percentile thresholds feeding the
    reward, or None when there are none."""
    names = {x.threshold for x in reward_leaves(surface)}
    pcts = [t for t in surface.thresholds
            if t.name in names and t.kind == "percentile"
            and t.percentile_target is not None]
    if not pcts:
        return None
    return max(
        min(min(t.percentile_target, 100.0 - t.percentile_target) / 100.0 for t in pcts),
        0.02,
    )


def _longest_reward_percentile_window_s(surface: LogicalSurface) -> float | None:
    """Longest declared rolling window among the reward's percentile thresholds,
    or None when none of them declares one (then there is nothing to cap against)."""
    names = {x.threshold for x in reward_leaves(surface)}
    windows = [
        (t.percentile_window_ms or 0.0) / 1000.0
        for t in surface.thresholds
        if t.name in names and t.kind == "percentile" and t.percentile_window_ms
    ]
    return max(windows) if windows else None


def sweep_geometry(surface: LogicalSurface, *, collar_s: float) -> SweepGeometry:
    """Fill / spike / metric-window layout.

    `PercentileImpl.step` computes over its CURRENT buffer ("warm-up: short
    buffer is OK") at O(buffer) per sample, so cost grows as fill^2 and filling a
    declared 2-minute window is ~12x more expensive for no assertion power. We
    fill only long enough that (a) the percentile estimate is stable and (b) the
    spike stays a small fraction of the buffer. The protocol's declared window is
    never overridden — we simply stop waiting for it to fill."""
    dwell_s = surface.dwell_ms / 1000.0
    spike_s = max(_MIN_SPIKE_S, 2.0 * (collar_s + dwell_s))
    headroom = _percentile_headroom(surface)
    if headroom is None:
        fill_s = 0.0
    else:
        frac = headroom / _SPIKE_FRACTION_SAFETY
        fill_s = max(_MIN_FILL_S, spike_s * (1.0 / frac - 1.0))
        declared = _longest_reward_percentile_window_s(surface)
        if declared is not None:
            fill_s = min(fill_s, declared + 2.0)
        # If a short declared window capped the fill, shrink the spike to keep
        # the fraction invariant rather than silently polluting the percentile.
        spike_s = min(spike_s, frac / (1.0 - frac) * fill_s)
    # The metric window opens only after the settle collar and the dwell latency,
    # so the filter transient and the dwell ramp cannot bias time-in-reward.
    metric = (fill_s + collar_s + dwell_s, fill_s + spike_s)
    usable = True
    reason = None
    if metric[1] - metric[0] < _MIN_METRIC_WINDOW_S:
        # The fraction cap above can shrink spike_s AFTER metric_start was
        # already fixed by the settle+dwell collar, so metric_end can fall at
        # or below metric_start (even inverted) on a short declared window.
        # `time_in_reward` raises on an inverted/empty window, and
        # `_run_sweeps` measures every group's window regardless of
        # assertability -- so an inverted window here would ERROR the whole
        # protocol out instead of reporting a clean "unassertable" sweep.
        # Fall back to a window spanning the whole spike: spike_s > 0 always
        # (see above), so this is never inverted or empty.
        usable = False
        reason = "metric window shorter than the settle+dwell collar"
        metric = (fill_s, fill_s + spike_s)
    return SweepGeometry(fill_s=fill_s, spike_s=spike_s,
                         total_s=fill_s + spike_s + _TAIL_PAD_S,
                         metric_window_s=metric, usable=usable, reason=reason)


def _tone_amplitude_uv(derive: DeriveSurface, env_uv: float, fs: int) -> float:
    """Tone amplitude that yields `env_uv` of in-band envelope, via the baked
    bandpass gain at the derive's band center."""
    center = 0.5 * (derive.band[0] + derive.band[1])
    gain = bandpass_gain_at(derive.sos, freq_hz=center, fs=fs)
    return env_uv / max(gain, 1e-3)


def _seg(derive: DeriveSurface, env_uv: float, start_s: float, end_s: float,
         fs: int) -> BandSegment | None:
    if env_uv <= 0.0 or end_s <= start_s or derive.sos is None:
        return None
    return BandSegment(band=derive.band, channel=derive.channel,
                       start_s=start_s, end_s=end_s,
                       content=Tone(amplitude_uv=_tone_amplitude_uv(derive, env_uv, fs)))


def _prime_segments(surface, noise_floor, *, geom, exclude: str | None) -> list[BandSegment]:
    """Drive every percentile-`below` leaf HIGH across the fill.

    Quiet is not favourable for `below(x, percentile(x))`: the threshold tracks
    its own signal, so the leaf holds only ~p% of the time no matter how quiet x
    is — capping the whole condition and flattening any sweep of a DIFFERENT
    leaf. Raising x during the fill lifts its rolling percentile, so the quiet
    spike then sits clearly below it. The swept derive is never primed (that
    would flatten its own sweep)."""
    if geom.fill_s <= 0.0:
        return []
    out = []
    for leaf in reward_leaves(surface):
        if leaf.signal == exclude or leaf.op != "below":
            continue
        if threshold_for(surface, leaf.threshold).kind != "percentile":
            continue
        anchor = leaf_anchor_uv(leaf, surface, noise_floor)
        if not anchor:
            continue
        seg = _seg(derive_for(surface, leaf.signal), _HI * anchor, 0.0, geom.fill_s,
                   surface.sample_rate_hz)
        if seg is not None:
            out.append(seg)
    return out


def _favourable_segments(surface, noise_floor, *, exclude: str | None,
                         window: tuple[float, float]) -> list[BandSegment]:
    """Hold every non-swept reward leaf TRUE over `window`.

    above: drive high. below+percentile: nothing here (primed in the fill).
    below+absolute: nothing (quiet already sits below the threshold)."""
    out = []
    for leaf in reward_leaves(surface):
        if leaf.signal == exclude or leaf.op != "above":
            continue
        anchor = leaf_anchor_uv(leaf, surface, noise_floor)
        if not anchor:
            continue
        seg = _seg(derive_for(surface, leaf.signal), _HI * anchor, window[0], window[1],
                   surface.sample_rate_hz)
        if seg is not None:
            out.append(seg)
    return out


def _unfavourable_segments(surface, noise_floor, *,
                           window: tuple[float, float]) -> list[BandSegment]:
    """Force every reward leaf FALSE over `window` (used after a hold ends).

    below leaves: drive them above their threshold. above leaves: silence
    already does it. Doing both keeps `any_of` rewards false too."""
    out = []
    for leaf in reward_leaves(surface):
        if leaf.op != "below":
            continue
        anchor = leaf_anchor_uv(leaf, surface, noise_floor)
        if not anchor:
            continue
        seg = _seg(derive_for(surface, leaf.signal), _HI * anchor, window[0], window[1],
                   surface.sample_rate_hz)
        if seg is not None:
            out.append(seg)
    return out


def _phase(total_s: float) -> PhaseOverride:
    return PhaseOverride(_WARMUP_S, total_s - _WARMUP_S - _COOLDOWN_S, _COOLDOWN_S)


def _scenario(label, segments, *, total_s, fs, tag, seed) -> Scenario:
    return Scenario(label=label, duration_s=total_s, sample_rate_hz=fs,
                    segments=tuple(segments), controls={},
                    coverage_tags=frozenset({f"metamorphic:{tag}", label}),
                    phase_override=_phase(total_s), seed=seed)


def _rank_group(surface, noise_floor, *, geom, derive_name, seed) -> SweepGroup:
    fs = surface.sample_rate_hz
    tag = f"rank_sweep:{derive_name}"
    direction, reason = sweep_direction(surface, derive_name)
    leaves = [x for x in reward_leaves(surface) if x.signal == derive_name]
    anchor = leaf_anchor_uv(leaves[0], surface, noise_floor) if leaves else None
    if direction != NONE and not anchor:
        direction, reason = NONE, "no resolvable decision level for the swept leaf"
    if not geom.usable:
        direction, reason = NONE, geom.reason

    spike = (geom.fill_s, geom.fill_s + geom.spike_s)
    background = (_prime_segments(surface, noise_floor, geom=geom, exclude=derive_name)
                  + _favourable_segments(surface, noise_floor, exclude=derive_name,
                                         window=spike))
    derive = derive_for(surface, derive_name)
    members = [SweepMember(
        scenario=_scenario(f"{tag}:baseline", background, total_s=geom.total_s,
                           fs=fs, tag=tag, seed=seed),
        index=-1,
    )]
    for i, rung in enumerate(_LADDER):
        seg = _seg(derive, rung * (anchor or 0.0), spike[0], spike[1], fs)
        members.append(SweepMember(
            scenario=_scenario(f"{tag}:rung_{i}", background + ([seg] if seg else []),
                               total_s=geom.total_s, fs=fs, tag=tag, seed=seed),
            index=i,
        ))
    return SweepGroup(tag=tag, direction=direction, reason=reason,
                      members=tuple(members), metric_window_s=geom.metric_window_s)


def _hold_group(surface, noise_floor, *, geom, collar_s, seed) -> SweepGroup:
    """Sweep the hold duration with every leaf favourable, then unfavourable.

    Longer hold past dwell => more time in reward, on the same realization.

    Every hold is offset by the settle collar (`hold = collar + f*dwell`) and the
    metric window opens after it. Without that offset the collar eats the whole
    reward: at f=5 and dwell=250 ms the tone runs 1.25 s, a ~1 s filter collar
    leaves ~0 s of reward, every rung measures 0.0, and the flat sweep fails
    `no_contrast` on a perfectly good engine."""
    fs = surface.sample_rate_hz
    tag = "hold_duration_sweep"
    dwell_s = surface.dwell_ms / 1000.0
    max_reward_s = max(_HOLD_FRACTIONS) * dwell_s      # metric window length
    max_hold_s = collar_s + max_reward_s               # longest driven tone

    # Absolute-only protocols have no fill; still let the filters settle first.
    start_s = geom.fill_s if geom.fill_s > 0.0 else _WARMUP_S + 0.5

    headroom = _percentile_headroom(surface)
    if headroom is not None:
        frac = headroom / _SPIKE_FRACTION_SAFETY
        start_s = max(start_s, max_hold_s * (1.0 / frac - 1.0))
        declared = _longest_reward_percentile_window_s(surface)
        if declared is not None:
            start_s = min(start_s, declared + 2.0)
    total_s = start_s + max_hold_s + _TAIL_PAD_S
    metric = (start_s + collar_s, start_s + collar_s + max_reward_s)

    direction, reason = UP, None
    if not geom.usable:
        direction, reason = NONE, geom.reason
    if metric[1] - metric[0] < _MIN_METRIC_WINDOW_S or max_reward_s <= dwell_s:
        direction, reason = NONE, "hold window shorter than the settle+dwell collar"

    hold_geom = SweepGeometry(fill_s=start_s, spike_s=max_hold_s, total_s=total_s,
                              metric_window_s=metric)

    def member(hold_s: float, index: int, label: str) -> SweepMember:
        segs = _prime_segments(surface, noise_floor, geom=hold_geom, exclude=None)
        if hold_s > 0.0:
            segs += _favourable_segments(surface, noise_floor, exclude=None,
                                         window=(start_s, start_s + hold_s))
        segs += _unfavourable_segments(surface, noise_floor,
                                       window=(start_s + hold_s, total_s))
        return SweepMember(
            scenario=_scenario(label, segs, total_s=total_s, fs=fs, tag=tag, seed=seed),
            index=index,
        )

    members = [member(0.0, -1, f"{tag}:baseline")]
    for i, f in enumerate(_HOLD_FRACTIONS):
        members.append(member(collar_s + f * dwell_s, i, f"{tag}:rung_{i}"))
    return SweepGroup(tag=tag, direction=direction, reason=reason,
                      members=tuple(members), metric_window_s=metric)


def plan_sweeps(surface: LogicalSurface, *, noise_floor: dict[str, float],
                collar_s: float, seed: int) -> tuple[SweepGroup, ...]:
    """One rank-sweep group per reward-feeding derive, plus one hold sweep."""
    geom = sweep_geometry(surface, collar_s=collar_s)
    seen: list[str] = []
    for leaf in reward_leaves(surface):
        if leaf.signal not in seen:
            seen.append(leaf.signal)
    groups = [_rank_group(surface, noise_floor, geom=geom, derive_name=name, seed=seed)
              for name in seen]
    groups.append(_hold_group(surface, noise_floor, geom=geom, collar_s=collar_s, seed=seed))
    return tuple(groups)


__all__ = [
    "DOWN", "NONE", "UP", "SweepGeometry", "SweepGroup", "SweepMember",
    "leaf_anchor_uv", "plan_sweeps", "sweep_direction", "sweep_geometry",
]
