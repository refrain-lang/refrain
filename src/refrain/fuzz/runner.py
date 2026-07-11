# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Run the fuzzer over a single protocol and classify the outcome.

`fuzz_protocol` introspects + generates behind a guarded backstop (so an
unrepresentable shape becomes a typed SKIP rather than a crash), then runs
the evaluate -> oracle -> check loop OUTSIDE that backstop (so genuine
engine violations and generator bugs surface, never silently skipped)."""
from __future__ import annotations

import dataclasses
import os
from collections import Counter
from dataclasses import dataclass

from ..synthetic import channels_for_synthetic
from .check import VacuityError, check_scenario
from .engine import measure_quiet_envelopes, run_scenario, time_in_reward
from .errors import UnsupportedProtocol
from .generate import generate_characterization_probe, generate_directed_scenarios
from .metamorphic import check_metamorphic
from .oracle import predict, settle_time_s
from .report import render_report
from .scenario import Verdict
from .surface import METAMORPHIC, build_surface
from .sweep import plan_sweeps, sweep_geometry

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


def fuzz_protocol(ir, *, path: str, max_scenarios: int, chunk_size: int,
                  seed: int = 42) -> ProtocolOutcome:
    """Fuzz one resolved protocol. Raises VacuityError on a generator bug."""
    try:
        surface = build_surface(ir)
        collar_samples = _collar_samples(surface, chunk_size)
        collar_s = collar_samples / surface.sample_rate_hz
        channels = channels_for_synthetic(ir)
        oracle_scenarios = _oracle_corpus(surface, max_scenarios, seed)
    except UnsupportedProtocol as exc:
        return ProtocolOutcome(path=path, status=SKIPPED, reason=exc.reason)
    except _BACKSTOP_ERRORS as exc:
        return ProtocolOutcome(
            path=path, status=SKIPPED, reason=f"unclassified ({_short_reason(exc)})"
        )

    # --- evaluate -> oracle -> check: OUTSIDE the backstop ---
    results = []
    all_tags: set[str] = set()
    for scenario in oracle_scenarios:
        all_tags |= set(scenario.coverage_tags)
        results.append(_run_one_scenario(
            scenario, ir=ir, surface=surface, channels=channels,
            collar_samples=collar_samples, chunk_size=chunk_size,
        ))

    # The quiet-envelope distribution is where percentile leaves' decision
    # levels are read; measuring it needs one quiet engine run. Absolute-only
    # protocols read their anchors from `absolute_uv` and never index this
    # dict, so they need no probe.
    geom = sweep_geometry(surface, collar_s=collar_s)
    quiet_envelopes = (
        measure_quiet_envelopes(ir=ir, surface=surface, channels=channels,
                                chunk_size=chunk_size, fill_s=geom.fill_s, seed=seed)
        if geom.fill_s > 0.0 else
        {}
    )
    groups = plan_sweeps(surface, quiet_envelopes=quiet_envelopes, collar_s=collar_s, seed=seed)
    metrics = _run_sweeps(groups, ir=ir, surface=surface, channels=channels,
                          chunk_size=chunk_size)
    for g in groups:
        all_tags |= {f"metamorphic:{g.tag}"}
    violations, outcomes = check_metamorphic(list(groups), metrics)

    n_assertions = sum(r.n_crisp_assertions for r in results) + sum(
        1 for o in outcomes if o.assertable
    )
    if n_assertions == 0:
        raise VacuityError(
            f"protocol {surface.protocol_name!r}: zero crisp assertions anywhere "
            f"(no sample-exact assertion and no assertable sweep). This is a "
            f"generator bug, not a pass."
        )

    report = render_report(
        protocol_name=surface.protocol_name, tier=surface.tier, results=results,
        sweep_outcomes=outcomes, metamorphic_violations=violations,
        all_coverage_tags=all_tags,
    )
    has_violation = bool(violations) or any(
        r.verdict in (Verdict.MISSED, Verdict.SPURIOUS) for r in results
    )
    return ProtocolOutcome(
        path=path, status=FUZZED, passed=not has_violation, report=report
    )


def _oracle_corpus(surface, max_scenarios: int, seed: int):
    """Sample-exact scenarios. Empty for the metamorphic tier: where a percentile
    threshold decides firing, the oracle can only emit DON'T-CARE, and a
    DON'T-CARE that absorbs real noise-firing is a hollow pass, not coverage."""
    if surface.tier == METAMORPHIC:
        return []
    corpus = (list(generate_directed_scenarios(surface))
              + list(generate_characterization_probe(surface)))
    if max_scenarios > 0:
        corpus = corpus[:max_scenarios]
    return [dataclasses.replace(s, seed=seed) for s in corpus]


def _run_sweeps(groups, *, ir, surface, channels, chunk_size) -> dict[str, float]:
    """Measure time-in-reward for every member of every sweep group.

    Unassertable groups are still RUN (their series is reported), so a reader can
    see why nothing was asserted rather than seeing silence."""
    fs = surface.sample_rate_hz
    metrics: dict[str, float] = {}
    for g in groups:
        for m in g.members:
            res = run_scenario(m.scenario, ir=ir, channels=channels, chunk_size=chunk_size)
            metrics[m.scenario.label] = time_in_reward(
                res.streams, window_s=g.metric_window_s, fs=fs)
    return metrics


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


def _run_one_scenario(scenario, *, ir, surface, channels, collar_samples, chunk_size):
    """Run + oracle-predict + check a single scenario. Returns a
    PerScenarioResult; may raise VacuityError (a generator bug)."""
    fs = surface.sample_rate_hz
    res = run_scenario(scenario, ir=ir, channels=channels, chunk_size=chunk_size)
    expected = predict(scenario, surface)
    return check_scenario(
        scenario_label=scenario.label, expected=expected, actual=list(res.events), fs=fs,
        collar_samples=collar_samples, coverage_tags=scenario.coverage_tags,
        total_samples=int(round(scenario.duration_s * fs)),
    )


def discover_protocols(paths: list[str]) -> list[str]:
    """Return a sorted, de-duplicated list of *.refrain files.

    Directories are walked recursively; plain file paths are taken as-is."""
    found: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    if fn.endswith(".refrain"):
                        found.append(os.path.join(root, fn))
        else:
            found.append(p)
    return sorted(dict.fromkeys(found))


def run_batch(paths, *, max_scenarios, chunk_size, resolve_fn,
              seed: int = 42) -> list[ProtocolOutcome]:
    """`resolve_fn(path) -> ir | str`: returns the resolved IR, or an error
    string (parse/resolve diagnostic) which becomes an ERRORED outcome."""
    outcomes: list[ProtocolOutcome] = []
    for path in discover_protocols(paths):
        resolved = resolve_fn(path)
        if isinstance(resolved, str):
            outcomes.append(ProtocolOutcome(path=path, status=ERRORED, reason=resolved))
            continue
        try:
            outcomes.append(fuzz_protocol(
                resolved, path=path, max_scenarios=max_scenarios, chunk_size=chunk_size,
                seed=seed,
            ))
        except VacuityError as exc:
            outcomes.append(ProtocolOutcome(
                path=path, status=ERRORED, reason=f"generator-bug: {_short_reason(exc)}"))
        except Exception as exc:  # noqa: BLE001 — batch must never abort on one protocol
            # An evaluator-setup error on one protocol (e.g. a montage needing a
            # channel the synthetic source lacks) must not crash the whole batch.
            # Classify it as ERRORED so the batch completes and still exits 1.
            outcomes.append(ProtocolOutcome(
                path=path, status=ERRORED, reason=f"eval-error: {_short_reason(exc)}"))
    return outcomes


def batch_exit_code(outcomes) -> int:
    """Return 1 if any outcome is ERRORED or a FUZZED outcome with passed=False."""
    bad = sum(
        1 for o in outcomes
        if o.status == ERRORED or (o.status == FUZZED and o.passed is False)
    )
    return 1 if bad else 0


def render_batch_report(outcomes, total) -> str:
    """Render a human-readable summary of batch fuzzing outcomes."""
    fuzzed = [o for o in outcomes if o.status == FUZZED]
    skipped = [o for o in outcomes if o.status == SKIPPED]
    errored = [o for o in outcomes if o.status == ERRORED]
    n_pass = sum(1 for o in fuzzed if o.passed)
    n_fail = len(fuzzed) - n_pass
    pct = int(round(100 * len(fuzzed) / total)) if total else 0
    lines = [
        f"fuzzed {len(fuzzed)} (pass {n_pass} / fail {n_fail}) "
        f"/ skipped {len(skipped)} / errored {len(errored)}",
        f"coverage: fuzzed {len(fuzzed)} / total {total} ({pct}%)",
    ]
    if skipped:
        lines.append("skips by reason:")
        for reason, n in sorted(Counter(o.reason for o in skipped).items()):
            lines.append(f"  {reason}: {n}")
    if errored:
        lines.append("errors:")
        for o in errored:
            lines.append(f"  {o.path}: {o.reason}")
    if n_fail:
        lines.append("violations:")
        for o in fuzzed:
            if o.passed is False:
                lines.append(f"  [VIOLATION] {o.path}")
    return "\n".join(lines)


__all__ = [
    "ERRORED", "FUZZED", "SKIPPED", "ProtocolOutcome", "fuzz_protocol",
    "discover_protocols", "run_batch", "render_batch_report", "batch_exit_code",
]
