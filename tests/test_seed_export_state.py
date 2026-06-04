# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Ask 2 — compact-summary seed/export for stateful trackers.

`auto_range` and `percentile` build their window from an empty buffer every
session. This adds an opt-in seed (re-prime from a prior session's anchors)
and an export (read the final anchors out) so a user-adaptive ceiling
persists and rises across sessions. State is a compact, rate-independent
summary — not a raw buffer blob — and is runtime state, never IR.
"""

from __future__ import annotations

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.primitive_impls import AutoRangeImpl, PercentileImpl
from refrain.resolver import resolve

_HRV_PROTO = """
    protocol "seed_export_test" {
      meta { version = "1.0"; evidence = "demo"; description = "seed/export" }
      requires { sample_rate = ">= 4 Hz"; channels = ["tachogram"] }
      input "tach" { montage = passthrough() }
      derive "env" { from = "tach"; pipeline = [ rectify(), smooth(tau: 4 s) ] }
      derive "rs"  { from = "env";  pipeline = [ auto_range(window: 5 min, percentile: (1, 99)) ] }
      threshold "rs_t" { signal = "rs"; type = percentile(target_pct: 70, window: 5 min) }
      reward {
        continuous = "rs"
        event = dwell(condition: all_of([ above("rs", "rs_t") ]), duration: 5 s)
      }
      output { audio_gain = reward.continuous; audio_chime = reward.event }
    }
"""


def test_auto_range_export_after_run():
    impl = AutoRangeImpl(window_ms=5 * 60 * 1000, low_pct=1, high_pct=99, sample_rate_hz=4.0)
    rng = np.random.default_rng(0)
    impl.step(rng.uniform(0.01, 0.05, size=400))
    st = impl.export_state()
    assert set(st) == {"low", "high", "n_eff"}
    assert st["low"] < st["high"]
    assert st["n_eff"] == 400  # samples seen, capped at window


def test_auto_range_seed_reproduces_anchors():
    impl = AutoRangeImpl(window_ms=5 * 60 * 1000, low_pct=1, high_pct=99, sample_rate_hz=4.0)
    impl.seed({"low": 0.012, "high": 0.048, "n_eff": 1200})
    st = impl.export_state()
    # deterministic synthetic ramp reproduces the seeded anchors within tolerance
    assert st["low"] == pytest.approx(0.012, abs=2e-3)
    assert st["high"] == pytest.approx(0.048, abs=2e-3)
    assert st["n_eff"] == 1200


def test_unseeded_auto_range_is_cold_start():
    a = AutoRangeImpl(window_ms=1000, low_pct=5, high_pct=95, sample_rate_hz=4.0)
    b = AutoRangeImpl(window_ms=1000, low_pct=5, high_pct=95, sample_rate_hz=4.0)
    x = np.linspace(0, 1, 10)
    assert np.array_equal(a.step(x), b.step(x))  # no seed => identical to today


def test_percentile_export_and_seed_roundtrip():
    impl = PercentileImpl(target_pct=70, window_ms=5 * 60 * 1000, sample_rate_hz=4.0)
    impl.seed({"value": 0.04, "target_pct": 70, "n_eff": 1200})
    st = impl.export_state()
    assert set(st) == {"value", "target_pct", "n_eff"}
    assert st["value"] == pytest.approx(0.04, abs=1e-9)  # constant fill is exact
    assert st["target_pct"] == 70
    assert st["n_eff"] == 1200


def test_unseeded_percentile_is_cold_start():
    a = PercentileImpl(target_pct=70, window_ms=1000, sample_rate_hz=4.0)
    b = PercentileImpl(target_pct=70, window_ms=1000, sample_rate_hz=4.0)
    x = np.linspace(0, 1, 10)
    assert np.array_equal(a.step(x), b.step(x))  # no seed => identical to today


def test_evaluator_export_and_seed_continuity():
    ir = resolve(parse(_HRV_PROTO))
    ev = Evaluator.live(
        ir, sample_rate_hz=4.0, channel_names=("tachogram",), backend="python"
    )
    ev.start(skip_warmup=True)
    rng = np.random.default_rng(1)
    for _ in range(50):
        ev.step_chunk(rng.uniform(0.0, 0.1, size=(8, 1)))
    state = ev.export_state()
    assert "rs.auto_range" in state and "rs_t.percentile" in state
    assert set(state["rs.auto_range"]) == {"low", "high", "n_eff"}
    assert set(state["rs_t.percentile"]) == {"value", "target_pct", "n_eff"}

    # Seed a fresh run from the prior state — export is stable across the seed.
    ev2 = Evaluator.live(
        ir, sample_rate_hz=4.0, channel_names=("tachogram",),
        backend="python", seed_state=state,
    )
    ev2.start(skip_warmup=True)
    seeded = ev2.export_state()
    assert seeded["rs.auto_range"]["low"] == pytest.approx(
        state["rs.auto_range"]["low"], abs=2e-3)
    assert seeded["rs.auto_range"]["high"] == pytest.approx(
        state["rs.auto_range"]["high"], abs=2e-3)
    assert seeded["rs_t.percentile"]["value"] == pytest.approx(
        state["rs_t.percentile"]["value"], abs=1e-9)  # constant fill is exact


def test_unseeded_evaluator_export_is_empty_or_cold():
    """No seed_state => behavior identical to today; export reflects the
    cold-started trackers (n_eff grows from zero)."""
    ir = resolve(parse(_HRV_PROTO))
    ev = Evaluator.live(ir, sample_rate_hz=4.0, channel_names=("tachogram",), backend="python")
    ev.start(skip_warmup=True)
    state = ev.export_state()
    assert state["rs.auto_range"]["n_eff"] == 0  # nothing stepped yet


def _run_export(backend: str, seed: dict | None = None) -> dict:
    ir = resolve(parse(_HRV_PROTO))
    ev = Evaluator.live(
        ir, sample_rate_hz=4.0, channel_names=("tachogram",),
        backend=backend, seed_state=seed,
    )
    ev.start(skip_warmup=True)
    rng = np.random.default_rng(7)
    for _ in range(60):
        ev.step_chunk(rng.uniform(0.0, 0.1, size=(8, 1)))
    return ev.export_state()


def test_export_and_seed_parity_python_vs_rust():
    """Ask 2 parity: the Rust core reproduces the Python evaluator's
    export_state() — and a seeded run — to 1e-6, both keys and values."""
    pytest.importorskip(
        "refrain_core",
        reason="refrain_core wheel not installed — build with: "
        "cd refrain-core && maturin develop --release",
    )
    py, rs = _run_export("python"), _run_export("rust")
    assert set(py) == set(rs)
    for key in py:
        assert set(py[key]) == set(rs[key])
        for field in py[key]:
            assert py[key][field] == pytest.approx(rs[key][field], abs=1e-6, rel=1e-6)

    # Seed both backends from the same prior state; exports stay in lockstep.
    py2, rs2 = _run_export("python", seed=py), _run_export("rust", seed=py)
    for key in py2:
        for field in py2[key]:
            assert py2[key][field] == pytest.approx(rs2[key][field], abs=1e-6, rel=1e-6)
