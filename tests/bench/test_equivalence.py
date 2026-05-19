"""Equivalence checker: pass/fail for matching/mismatching stream dicts."""

from __future__ import annotations

import numpy as np
import pytest

from bench.harness.equivalence import EquivalenceFailure, assert_equivalent


def test_identical_streams_pass():
    a = {"x": np.arange(100, dtype=np.float64)}
    b = {"x": np.arange(100, dtype=np.float64)}
    report = assert_equivalent(a, b, warmup_samples=10)
    assert report.passed
    assert report.streams_checked == ("x",)


def test_streams_within_tolerance_pass():
    a = {"x": np.arange(100, dtype=np.float64)}
    b = {"x": np.arange(100, dtype=np.float64) + 1e-10}
    report = assert_equivalent(a, b, warmup_samples=10, atol=1e-9, rtol=1e-6)
    assert report.passed


def test_streams_outside_tolerance_fail():
    a = {"x": np.arange(100, dtype=np.float64)}
    b = {"x": np.arange(100, dtype=np.float64) + 1.0}
    with pytest.raises(EquivalenceFailure) as excinfo:
        assert_equivalent(a, b, warmup_samples=10, atol=1e-9, rtol=1e-6)
    assert "x" in str(excinfo.value)


def test_warmup_samples_skipped():
    a = {"x": np.zeros(100, dtype=np.float64)}
    b = {"x": np.zeros(100, dtype=np.float64)}
    b["x"][:5] = 999.0  # warmup region differs
    report = assert_equivalent(a, b, warmup_samples=10)
    assert report.passed


def test_missing_stream_in_b_fails():
    a = {"x": np.zeros(10), "y": np.zeros(10)}
    b = {"x": np.zeros(10)}
    with pytest.raises(EquivalenceFailure) as excinfo:
        assert_equivalent(a, b, warmup_samples=0)
    assert "y" in str(excinfo.value)


def test_extra_stream_in_b_ignored():
    """Extra streams in (b) are permitted — refrain may expose more streams
    than the baseline computes. The contract is: every refrain stream named
    in `streams_to_check` exists in (b) and matches."""
    a = {"x": np.zeros(10)}
    b = {"x": np.zeros(10), "y_unrequested": np.zeros(10)}
    report = assert_equivalent(a, b, warmup_samples=0)
    assert report.passed
    assert report.streams_checked == ("x",)


def test_shape_mismatch_fails():
    a = {"x": np.zeros(100)}
    b = {"x": np.zeros(50)}
    with pytest.raises(EquivalenceFailure) as excinfo:
        assert_equivalent(a, b, warmup_samples=0)
    assert "shape" in str(excinfo.value).lower()
