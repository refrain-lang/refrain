# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Run a fuzz Scenario against the real evaluator and expose its per-sample streams.

The metamorphic tier measures TIME-IN-REWARD: the fraction of a window during
which the engine's reward output is actively holding. That primitive lives in
`Evaluator.last_streams()["reward.event.holds"]` — validated bit-exact by the
calibrated-oracle gate finding. `last_streams()` is a PER-CHUNK snapshot, so we
concatenate it across the run.

This module is INSTRUMENTATION, not an oracle. Its streams place the amplitude
ladder and measure a metric; they never predict what the engine ought to do.
Metamorphic assertions are engine-vs-property, not engine-vs-oracle, so reading
the engine's own envelope here does not weaken them. (DSP correctness is covered
by the Rust<->Python golden vectors and the band-characterization probe — see
docs/superpowers/specs/2026-07-07-fuzzer-target-tiered-gate-design.md.)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from ..eval_ import Evaluator
from ..ir import IRPhase
from ..sources import SyntheticSource
from ..synthetic import render_scenario
from .check import ActualEvent
from .scenario import PhaseOverride, Scenario

REWARD_HOLDS = "reward.event.holds"

# Seconds of a quiet probe discarded before taking the noise median, so the
# bandpass/smooth start-up transient cannot bias it.
_SETTLE_SKIP_S = 4.0


@dataclass(frozen=True, slots=True)
class RunResult:
    events: tuple[ActualEvent, ...]
    streams: dict[str, np.ndarray]


def apply_phase_override(ir, phase_override: PhaseOverride | None):
    """Rebuild `ir.session.phases` from a fuzz `PhaseOverride` so the evaluator's
    warmup window matches what the scenario assumed.

    The override carries durations in seconds; `IRPhase.duration_ms` is in
    milliseconds. Returns `ir` unchanged when there is no override. Zero-length
    phases are dropped; the evaluator tolerates an empty phases tuple."""
    if phase_override is None:
        return ir
    po = phase_override
    spec = [
        ("warmup", po.warmup_s, True),
        ("training", po.training_s, False),
        ("cooldown", po.cooldown_s, True),
    ]
    phases = tuple(
        IRPhase(name=name, duration_ms=dur_s * 1000.0, output_muted=muted)
        for name, dur_s, muted in spec
        if dur_s > 0
    )
    return dataclasses.replace(ir, session=dataclasses.replace(ir.session, phases=phases))


def run_scenario(scenario: Scenario, *, ir, channels, chunk_size: int) -> RunResult:
    """Render + run one scenario, returning its events and full per-sample streams."""
    scenario_ir = apply_phase_override(ir, scenario.phase_override)
    source = SyntheticSource(render_scenario(scenario, channels=channels),
                             duration_s=scenario.duration_s)
    ev = Evaluator(scenario_ir, source, record_streams=True)
    fs = scenario.sample_rate_hz
    events: list[ActualEvent] = []
    parts: dict[str, list[np.ndarray]] = {}
    for chunk in source.iter_chunks(chunk_size):
        for e in ev.step_chunk(chunk):
            if e.kind == "event":
                events.append(ActualEvent(sample=int(round(e.timestamp_s * fs)),
                                          kind=e.kind, channel=e.channel))
        for key, arr in ev.last_streams().items():
            parts.setdefault(key, []).append(np.asarray(arr).copy())
    streams = {k: np.concatenate(v) for k, v in parts.items()}
    return RunResult(events=tuple(events), streams=streams)


def time_in_reward(streams: dict[str, np.ndarray], *,
                   window_s: tuple[float, float], fs: int) -> float:
    """Fraction of `window_s` during which the reward is actively holding.

    This is the metamorphic metric. It is NOT event count: an event is a dwell
    RE-TRIGGER, so during a noisy spike every momentary dip that recovers emits
    another event. Event count is therefore a noise artifact and runs backwards
    in drive; time-in-reward does not count flicker."""
    holds = streams.get(REWARD_HOLDS)
    if holds is None:
        raise KeyError(
            f"{REWARD_HOLDS!r} missing from streams; the protocol has no dwell reward"
        )
    start = int(round(window_s[0] * fs))
    end = int(round(window_s[1] * fs))
    seg = np.asarray(holds)[start:end]
    if seg.size == 0:
        raise ValueError(f"time_in_reward: empty window {window_s} at fs={fs}")
    return float(seg.astype(bool).mean())


def measure_noise_floor(*, ir, surface, channels, chunk_size: int,
                        fill_s: float, seed: int) -> dict[str, float]:
    """Median in-band envelope of every derive during a quiet run.

    This is the DECISION LEVEL for percentile leaves: a percentile threshold over
    a mostly-quiet rolling window sits at the noise level, so the ladder must
    straddle the noise floor. (Absolute leaves anchor on their own threshold
    instead — see `sweep.leaf_anchor_uv`.)"""
    total_s = max(fill_s, _SETTLE_SKIP_S + 4.0) + 2.0
    probe = Scenario(
        label="noise_floor_probe", duration_s=total_s,
        sample_rate_hz=surface.sample_rate_hz, segments=(), controls={},
        coverage_tags=frozenset({"probe:noise_floor"}),
        phase_override=PhaseOverride(1.0, total_s - 1.5, 0.5), seed=seed,
    )
    res = run_scenario(probe, ir=ir, channels=channels, chunk_size=chunk_size)
    skip = int(round(_SETTLE_SKIP_S * surface.sample_rate_hz))
    floors: dict[str, float] = {}
    for d in surface.derives:
        arr = res.streams.get(d.name)
        if arr is None or arr[skip:].size == 0:
            continue
        floors[d.name] = float(np.median(arr[skip:]))
    return floors


__all__ = [
    "REWARD_HOLDS", "RunResult", "apply_phase_override", "measure_noise_floor",
    "run_scenario", "time_in_reward",
]
