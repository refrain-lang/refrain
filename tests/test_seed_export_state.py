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

from refrain.primitive_impls import AutoRangeImpl, PercentileImpl


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
