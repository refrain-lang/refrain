# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the oracle's 3-valued logic + dwell prediction."""
from __future__ import annotations

import pytest

from refrain.fuzz.oracle import (
    ExpectedTimeline,
    SHOULD_FIRE,
    SHOULD_NOT_FIRE,
    DONT_CARE,
    predict_absolute_leaf_truth,
    combine_condition_tree,
    apply_dwell,
)
from refrain.fuzz.scenario import DontCareReason


def test_absolute_leaf_truth_three_zones():
    assert predict_absolute_leaf_truth(env=10.0, threshold=8.0, margin=1.0, op="above") is True
    assert predict_absolute_leaf_truth(env=5.0,  threshold=8.0, margin=1.0, op="above") is False
    assert predict_absolute_leaf_truth(env=8.3, threshold=8.0, margin=1.0, op="above") is None


def test_combine_condition_tree_all_of_three_valued():
    T, F, U = True, False, None
    assert combine_condition_tree("all_of", [T, T, T]) is True
    assert combine_condition_tree("all_of", [T, F, T]) is False
    assert combine_condition_tree("all_of", [T, U, T]) is None
    assert combine_condition_tree("any_of", [F, F, F]) is False
    assert combine_condition_tree("any_of", [F, T, F]) is True
    assert combine_condition_tree("any_of", [F, U, F]) is None


def test_apply_dwell_opens_should_fire_after_dwell_samples():
    fs = 256
    dwell_samples = 64
    n = 4 * fs
    truth = [False] * fs + [True] * (2 * fs) + [False] * fs
    tl = apply_dwell(truth, dwell_samples=dwell_samples, fs=fs,
                     collar_s=0.0,
                     muted_mask=[False] * n)
    event_sample = fs + dwell_samples - 1
    assert tl.should_fire_event_samples == [event_sample]


def test_apply_dwell_does_not_fire_if_condition_breaks_early():
    fs = 256
    dwell_samples = 64
    n = 2 * fs
    truth = [False] * fs + [True] * 32 + [False] * (fs - 32)
    tl = apply_dwell(truth, dwell_samples=dwell_samples, fs=fs,
                     collar_s=0.0, muted_mask=[False] * n)
    assert tl.should_fire_event_samples == []


def test_phase_muted_suppresses_event_at_output():
    fs = 256
    dwell_samples = 64
    n = 4 * fs
    truth = [False] * fs + [True] * (2 * fs) + [False] * fs
    muted = [True] * n
    tl = apply_dwell(truth, dwell_samples=dwell_samples, fs=fs,
                     collar_s=0.0, muted_mask=muted)
    assert tl.should_fire_event_samples == []
    assert tl.dont_care_intervals
    reasons = {iv.reason for iv in tl.dont_care_intervals}
    assert DontCareReason.PHASE_MUTED in reasons
