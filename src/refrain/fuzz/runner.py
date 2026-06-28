# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Run the fuzzer over a single protocol and classify the outcome.

`fuzz_protocol` introspects + generates behind a guarded backstop (so an
unrepresentable shape becomes a typed SKIP rather than a crash), then runs
the evaluate -> oracle -> check loop OUTSIDE that backstop (so genuine
engine violations and generator bugs surface, never silently skipped)."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..eval_ import eval_protocol
from ..ir import IRPhase
from ..sources import SyntheticSource
from ..synthetic import channels_for_synthetic, render_scenario
from .check import (
    ActualEvent,
    check_metamorphic_monotonic,
    check_scenario,
)
from .errors import UnsupportedProtocol
from .generate import (
    generate_characterization_probe,
    generate_directed_scenarios,
    generate_hold_duration_sweep,
    generate_rank_sweep,
)
from .oracle import predict, settle_time_s
from .report import render_report
from .scenario import Verdict
from .surface import build_surface

FUZZED = "fuzzed"
SKIPPED = "skipped"
ERRORED = "errored"

# Introspection/generation failures we treat as "unclassified" skips. NOT a
# blanket `except Exception` — the evaluate/oracle/check loop runs outside this.
_BACKSTOP_ERRORS = (ValueError, KeyError, TypeError, AttributeError, IndexError)


@dataclass(frozen=True, slots=True)
class ProtocolOutcome:
    path: str
    status: str                 # FUZZED | SKIPPED | ERRORED
    passed: bool | None = None  # FUZZED: True=no violation, False=violation
    reason: str | None = None   # SKIPPED/ERRORED: the (short) reason
    report: str | None = None   # FUZZED: full single-file report text


def _short_reason(exc: Exception) -> str:
    msg = (str(exc).splitlines() or [""])[0] or type(exc).__name__
    return msg.removeprefix("surface: ")[:60]


def fuzz_protocol(ir, *, path: str, max_scenarios: int, chunk_size: int) -> ProtocolOutcome:
    """Fuzz one resolved protocol. Raises VacuityError on a generator bug."""
    try:
        surface = build_surface(ir)
        corpus = _build_corpus(surface)
        if max_scenarios > 0:
            corpus = corpus[:max_scenarios]
        collar_samples = _collar_samples(surface, chunk_size)
        channels = channels_for_synthetic(ir)
    except UnsupportedProtocol as exc:
        return ProtocolOutcome(path=path, status=SKIPPED, reason=exc.reason)
    except _BACKSTOP_ERRORS as exc:
        return ProtocolOutcome(
            path=path, status=SKIPPED, reason=f"unclassified ({_short_reason(exc)})"
        )

    # --- evaluate -> oracle -> check: OUTSIDE the backstop ---
    results = []
    all_tags: set[str] = set()
    for scenario in corpus:
        all_tags |= set(scenario.coverage_tags)
        results.append(_run_one_scenario(
            scenario, ir=ir, surface=surface, channels=channels,
            collar_samples=collar_samples, chunk_size=chunk_size,
        ))
    metamorphic = (
        check_metamorphic_monotonic(results, tag_prefix="metamorphic:rank_sweep:")
        + check_metamorphic_monotonic(results, tag_prefix="metamorphic:hold_duration_sweep")
    )
    report = render_report(
        protocol_name=surface.protocol_name, results=results,
        metamorphic_violations=metamorphic, all_coverage_tags=all_tags,
    )
    has_violation = bool(metamorphic) or any(
        r.verdict in (Verdict.MISSED, Verdict.SPURIOUS) for r in results
    )
    return ProtocolOutcome(
        path=path, status=FUZZED, passed=not has_violation, report=report
    )


# --- moved verbatim from cli.py (fuzz-only pipeline helpers) ---

def _build_corpus(surface):
    """Build the full directed + characterization + sweep scenario corpus."""
    return (
        list(generate_directed_scenarios(surface))
        + list(generate_characterization_probe(surface))
        + list(generate_rank_sweep(surface))
        + list(generate_hold_duration_sweep(surface))
    )


def _collar_samples(surface, chunk_size: int) -> int:
    """Widest derive settle-collar (mirrors oracle.predict), quantised to
    samples at the surface's sample rate."""
    fs = surface.sample_rate_hz
    chunk_s = chunk_size / fs
    candidates = [
        settle_time_s(sos=d.sos, tau_s=(d.smooth_tau_ms or 0.0) / 1000.0,
                      chunk_s=chunk_s, fs=fs)
        for d in surface.derives if d.sos is not None
    ]
    collar_s = max(candidates) if candidates else 0.0
    return int(round(collar_s * fs))


def _apply_phase_override(ir, phase_override):
    """Rebuild `ir.session.phases` from a fuzz `PhaseOverride` so the
    evaluator's warmup window matches what the oracle assumed.

    The override carries durations in seconds; `IRPhase.duration_ms` is in
    milliseconds, so we convert. Returns `ir` unchanged when there is no
    override. Zero-length phases are dropped; the evaluator tolerates an
    empty phases tuple, so no special-casing is needed."""
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
    new_session = dataclasses.replace(ir.session, phases=phases)
    return dataclasses.replace(ir, session=new_session)


def _run_one_scenario(scenario, *, ir, surface, channels, collar_samples, chunk_size):
    """Render + run + oracle-predict + check a single scenario. Returns a
    PerScenarioResult; may raise VacuityError (a generator bug)."""
    fs = surface.sample_rate_hz
    scenario_ir = _apply_phase_override(ir, scenario.phase_override)
    gen = render_scenario(scenario, channels=channels)
    source = SyntheticSource(gen, duration_s=scenario.duration_s)
    actual: list[ActualEvent] = []
    for ev in eval_protocol(scenario_ir, source, chunk_size=chunk_size):
        if ev.kind != "event":
            continue
        actual.append(ActualEvent(
            sample=int(round(ev.timestamp_s * fs)), kind=ev.kind, channel=ev.channel,
        ))
    expected = predict(scenario, surface)
    return check_scenario(
        scenario_label=scenario.label, expected=expected, actual=actual, fs=fs,
        collar_samples=collar_samples, coverage_tags=scenario.coverage_tags,
        total_samples=int(round(scenario.duration_s * fs)),
    )


__all__ = [
    "ERRORED", "FUZZED", "SKIPPED", "ProtocolOutcome", "fuzz_protocol",
]
