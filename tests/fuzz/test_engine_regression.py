# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Prove the metamorphic tier actually catches a broken engine.

`bench/protocols/micro_single_pct.refrain` is metamorphic-tier (its only
threshold is a percentile), so `runner._oracle_corpus` gives it ZERO oracle
scenarios -- any violation reported here can only come from the metamorphic
sweeps' monotonicity/contrast assertions, never from the sample-exact oracle.
That makes it the right fixture to prove the tier has teeth: the whole PR
claims "a genuine engine regression fails the gate," and until this file
existed nothing tested it.

Each mutation is applied via `monkeypatch.setattr` so it is always undone,
even if the test fails or the engine raises. Every `fuzz_protocol` call on
this protocol runs every sweep member through the real engine and takes
roughly 6-8 s, so this file is kept to the control plus the three cheapest
mutants that demonstrate both assertions (monotonicity and contrast).
"""
from __future__ import annotations

from pathlib import Path

from refrain.fuzz.runner import FUZZED, fuzz_protocol
from refrain.parser import parse_file
from refrain.primitive_impls import AboveImpl, DwellImpl
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = "bench/protocols/micro_single_pct.refrain"


def _run():
    ir = resolve(parse_file(REPO_ROOT / PROTOCOL), None)
    return fuzz_protocol(ir, path=PROTOCOL, max_scenarios=0, chunk_size=64, seed=42)


def test_unmutated_engine_passes_the_metamorphic_gate():
    out = _run()
    assert out.status == FUZZED
    assert out.passed is True


def test_inverted_above_is_caught_by_degenerate_baseline_contrast(monkeypatch):
    """Flip `signal > threshold` to `signal < threshold`. The swept leaf's
    truth is now the complement of what the sweep's ascending drive assumes.
    The ladder's rungs (1x, 2x, 4x, 8x the decision level -- see Cause B/`sweep._LADDER`)
    start AT the decision level, so the inverted leaf is already false at
    every rung (time-in-reward 0.000 at rung 0 onward) while the un-driven
    baseline sits above the (now-inverted) threshold almost throughout
    (baseline 1.000, seed 42). A flat 0-series is trivially non-decreasing,
    so monotonicity alone would wave this through -- it is the degenerate-
    baseline contrast guard that catches it, exactly like the latched-dwell
    mutant below."""
    def inverted_step(self, signal, threshold):
        return signal < threshold

    monkeypatch.setattr(AboveImpl, "step", inverted_step)
    out = _run()
    assert out.passed is False
    assert "VIOLATION:NO_CONTRAST" in out.report
    assert "baseline 1.000 | 0.000 -> 0.000 -> 0.000 -> 0.000" in out.report


def test_dwell_latched_true_is_caught_by_degenerate_baseline_contrast(monkeypatch):
    """Latch `holds` permanently True: the reward now fires on pure noise, so
    every rung of every sweep measures baseline == top rung == 1.0. That
    series is perfectly monotone (a constant is non-decreasing), so
    monotonicity alone would wave this through -- ONLY the contrast check
    catches it, and only because `metamorphic._contrast` has an explicit
    degenerate-baseline guard. Without that guard, the contrast inequality
    `last - base >= 0.5*(1 - base)` degenerates to `0 >= 0` at base == 1.0
    and would PASS. This test is that guard's reason to exist."""
    original_step = DwellImpl.step

    def always_holding(self, condition):
        result = original_step(self, condition)
        result.holds[:] = True
        return result

    monkeypatch.setattr(DwellImpl, "step", always_holding)
    out = _run()
    assert out.passed is False
    assert "VIOLATION:NO_CONTRAST" in out.report
    assert "baseline 1.000" in out.report


def test_dwell_latched_false_is_caught_by_degenerate_baseline_contrast(monkeypatch):
    """The mirror-image mutant: `holds` permanently False. Every rung
    measures 0.0 (a flat, perfectly monotone series again), and the
    down-sweep's degenerate-baseline guard (`baseline <= 0`) is what fails it."""
    original_step = DwellImpl.step

    def never_holding(self, condition):
        result = original_step(self, condition)
        result.holds[:] = False
        return result

    monkeypatch.setattr(DwellImpl, "step", never_holding)
    out = _run()
    assert out.passed is False
    assert "VIOLATION:NO_CONTRAST" in out.report
    assert "baseline 0.000" in out.report
