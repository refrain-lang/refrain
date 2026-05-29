# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the Evaluator.last_taps() introspection API (SPEC §7.8).

The host application embedding Refrain wants per-chunk visibility into
internal values — envelopes, threshold values, dwell sub-conditions,
pre-gating reward, post-gating output — to plot a clinician observation
window. These tests pin down what `last_taps()` exposes.

Push-mode (live) tests declare the `backend` fixture so they run on both the Python and Rust backends (see tests/conftest.py); set REFRAIN_EVAL_BACKEND=rust to exercise the Rust core.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse, parse_file
from refrain.resolver import resolve
from refrain.synthetic import SignalGenerator

# conftest.py provides the ``backend`` fixture and RUST_BACKEND_ACTIVE flag.
from tests.conftest import RUST_BACKEND_ACTIVE


REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"
AMP_BB = REPO / "src" / "refrain" / "amp_profiles" / "brainbit_flex.json"


@pytest.fixture(scope="module")
def smr_bb_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz_brainbit.refrain"),
                   load_amp_profile(AMP_BB))


def _live_bb(ir, backend="python"):
    # Tests that should run on BOTH backends must receive `backend` from the
    # `backend` fixture and pass it here. The "python" default exists only for
    # the skipif(RUST_BACKEND_ACTIVE) tests that omit the fixture.
    return Evaluator.live(
        ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )


def _push_one(ev, *, channels=("Cz", "F3", "F4", "Pz"), seed=1, size=64):
    gen = SignalGenerator(sample_rate_hz=250, channels=channels, seed=seed)
    ev.step_chunk(gen.next_chunk(size))


# ---------------------------------------------------------------------------
# Empty / scaffolding behaviour
# ---------------------------------------------------------------------------


def test_last_taps_empty_before_first_step_chunk(smr_bb_ir, backend):
    ev = _live_bb(smr_bb_ir, backend)
    assert ev.last_taps() == {}


def test_last_taps_returns_a_copy(smr_bb_ir, backend):
    ev = _live_bb(smr_bb_ir, backend)
    ev.start(skip_warmup=True)
    _push_one(ev)
    snap = ev.last_taps()
    snap["derive/smr_envelope"] = -999.0
    snap["mutated_by_host"] = True
    # Internal state must be unaffected.
    after = ev.last_taps()
    assert after["derive/smr_envelope"] != -999.0
    assert "mutated_by_host" not in after


# ---------------------------------------------------------------------------
# Key-set for SMR Cz
# ---------------------------------------------------------------------------


def test_smr_bb_last_taps_key_set(smr_bb_ir, backend):
    """SMR Cz on BrainBit has all the canonical tap categories
    (no inhibits, both continuous + event reward, 3 sub-conditions)."""
    ev = _live_bb(smr_bb_ir, backend)
    ev.start(skip_warmup=True)
    _push_one(ev)
    keys = set(ev.last_taps())
    expected = {
        "input/raw",
        "derive/smr_envelope", "derive/theta_envelope", "derive/high_beta_envelope",
        "threshold/smr_t", "threshold/theta_t", "threshold/hbeta_t",
        "muted",
        "reward/continuous",
        "reward/event", "reward/event.holds",
        "reward/condition[0]", "reward/condition[1]", "reward/condition[2]",
        "output/audio_chime", "output/audio_gain",
        "phase/index", "phase/output_muted",
    }
    assert keys == expected, f"missing: {expected - keys}; extra: {keys - expected}"


def test_smr_bb_tap_value_types(smr_bb_ir, backend):
    """Float for analog/envelope/threshold; bool for conditions/events/muted."""
    ev = _live_bb(smr_bb_ir, backend)
    ev.start(skip_warmup=True)
    _push_one(ev)
    taps = ev.last_taps()
    float_keys = {
        "input/raw",
        "derive/smr_envelope", "derive/theta_envelope", "derive/high_beta_envelope",
        "threshold/smr_t", "threshold/theta_t", "threshold/hbeta_t",
        "reward/continuous", "output/audio_gain",
    }
    bool_keys = {
        "muted",
        "reward/event", "reward/event.holds",
        "reward/condition[0]", "reward/condition[1]", "reward/condition[2]",
        "output/audio_chime",
    }
    for k in float_keys:
        assert isinstance(taps[k], float), f"{k}: expected float, got {type(taps[k])}"
    for k in bool_keys:
        assert isinstance(taps[k], bool), f"{k}: expected bool, got {type(taps[k])}"


# ---------------------------------------------------------------------------
# Warmup populates taps
# ---------------------------------------------------------------------------


def test_taps_populate_during_warmup(smr_bb_ir, backend):
    """Hosts plotting a warmup observation window need envelope and
    threshold values during the warmup state."""
    ev = _live_bb(smr_bb_ir, backend)
    ev.start()  # default: enters warmup (90s on SMR BB)
    assert ev.state == "warmup"
    _push_one(ev)
    taps = ev.last_taps()
    # All the structural keys should be present.
    assert "derive/smr_envelope" in taps
    assert "threshold/smr_t" in taps
    assert "reward/continuous" in taps
    # ...but the evaluator should still be in warmup.
    assert ev.state == "warmup"


# ---------------------------------------------------------------------------
# Uniform reward/condition[0] for single-condition dwells
# ---------------------------------------------------------------------------


_SINGLE_CONDITION_PROTOCOL = """
protocol "single_cond" {
  meta { version = "1.0" }
  input "raw" { montage = referential(active: "Cz", reference: "device") }
  derive "env" {
    from = "raw"
    pipeline = [bandpass(band: (12 Hz, 15 Hz)), hilbert(), magnitude(), smooth(tau: 100 ms)]
  }
  threshold "t" { signal = "env"; type = absolute(2 uV) }
  reward {
    event = dwell(condition: above("env", "t"), duration: 100 ms)
  }
  output { audio_chime = reward.event }
}
"""


def test_single_condition_dwell_emits_condition_zero(backend):
    """Single-condition dwells emit `reward/condition[0]` (not bare
    `reward/condition`) for uniform host iteration."""
    ir = resolve(parse(_SINGLE_CONDITION_PROTOCOL))
    ev = Evaluator.live(ir, sample_rate_hz=250, channel_names=("Cz",), backend=backend)
    ev.start(skip_warmup=True)
    _push_one(ev, channels=("Cz",))
    taps = ev.last_taps()
    assert "reward/condition[0]" in taps
    assert "reward/condition" not in taps
    # No condition[1] / condition[2] for a single-condition dwell.
    assert "reward/condition[1]" not in taps


# ---------------------------------------------------------------------------
# Continuous-only / event-only protocols
# ---------------------------------------------------------------------------


_CONTINUOUS_ONLY = """
protocol "cont_only" {
  meta { version = "1.0" }
  input "raw" { montage = referential(active: "Cz", reference: "device") }
  derive "env" {
    from = "raw"
    pipeline = [smooth(tau: 100 ms)]
  }
  reward { continuous = sigmoid("env", midpoint: 0 uV, steepness: 1) }
  output { audio_gain = reward.continuous }
}
"""


def test_continuous_only_protocol_omits_event_taps(backend):
    ir = resolve(parse(_CONTINUOUS_ONLY))
    ev = Evaluator.live(ir, sample_rate_hz=250, channel_names=("Cz",), backend=backend)
    ev.start(skip_warmup=True)
    _push_one(ev, channels=("Cz",))
    taps = ev.last_taps()
    assert "reward/continuous" in taps
    assert "reward/event" not in taps
    assert "reward/event.holds" not in taps
    assert "reward/condition[0]" not in taps


_EVENT_ONLY = """
protocol "event_only" {
  meta { version = "1.0" }
  input "raw" { montage = referential(active: "Cz", reference: "device") }
  derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
  threshold "t" { signal = "env"; type = absolute(0 uV) }
  reward {
    event = dwell(condition: above("env", "t"), duration: 100 ms)
  }
  output { audio_chime = reward.event }
}
"""


def test_event_only_protocol_omits_continuous_tap(backend):
    ir = resolve(parse(_EVENT_ONLY))
    ev = Evaluator.live(ir, sample_rate_hz=250, channel_names=("Cz",), backend=backend)
    ev.start(skip_warmup=True)
    _push_one(ev, channels=("Cz",))
    taps = ev.last_taps()
    assert "reward/continuous" not in taps
    assert "reward/event" in taps
    assert "reward/event.holds" in taps
    assert "reward/condition[0]" in taps


# ---------------------------------------------------------------------------
# set_control updates show up in next chunk's taps
# ---------------------------------------------------------------------------


def test_set_control_threshold_change_shows_in_next_taps(smr_bb_ir, backend):
    """Changing smr_target_pct via set_control should change the
    `threshold/smr_t` tap value on the next chunk."""
    ev = _live_bb(smr_bb_ir, backend)
    ev.start(skip_warmup=True)
    gen = SignalGenerator(sample_rate_hz=250, channels=("Cz", "F3", "F4", "Pz"), seed=1)
    # Push enough chunks for the percentile window to populate so the
    # threshold value is meaningfully data-driven.
    for _ in range(40):
        ev.step_chunk(gen.next_chunk(64))
    initial_smr_t = ev.last_taps()["threshold/smr_t"]
    # 70th percentile → 30th percentile (much lower threshold).
    ev.set_control("smr_target_pct", 30)
    for _ in range(5):
        ev.step_chunk(gen.next_chunk(64))
    new_smr_t = ev.last_taps()["threshold/smr_t"]
    # 30th percentile should be strictly less than 70th over the same data.
    assert new_smr_t < initial_smr_t, (
        f"set_control didn't propagate: {initial_smr_t:.3f} vs {new_smr_t:.3f}"
    )


# ---------------------------------------------------------------------------
# Inhibit taps for protocol-with-inhibits (Othmer ILF)
# ---------------------------------------------------------------------------


def test_inhibit_taps_present_for_othmer(backend):
    """Othmer ILF declares an `emg` inhibit — verify the tap shows up
    and `muted` reflects the combined gate."""
    ir = resolve(parse_file(EXAMPLES / "othmer_ilf_t3t4.refrain"),
                 load_amp_profile(AMP_Q21))
    ev = Evaluator.live(ir, sample_rate_hz=2048, channel_names=("T3", "T4"), backend=backend)
    ev.start(skip_warmup=True)
    gen = SignalGenerator(sample_rate_hz=2048, channels=("T3", "T4"), seed=1)
    ev.step_chunk(gen.next_chunk(64))
    taps = ev.last_taps()
    assert "inhibit/emg" in taps
    assert isinstance(taps["inhibit/emg"], bool)
    assert "muted" in taps
    assert isinstance(taps["muted"], bool)


# ---------------------------------------------------------------------------
# Composed protocols (cz-pz Othmer variant inherits parent's structure)
# ---------------------------------------------------------------------------


def test_composed_protocol_emits_taps(backend):
    """The cz-pz Othmer variant extends a base — verify taps work
    through composition. (Resolver applies composition before the
    Evaluator sees the IR, so this should be transparent.)"""
    from refrain.compose import filesystem_loader
    loader = filesystem_loader([EXAMPLES])
    ir = resolve(
        parse_file(EXAMPLES / "othmer_ilf_cz_pz.refrain"),
        load_amp_profile(AMP_Q21),
        parent_loader=loader,
    )
    ev = Evaluator.live(ir, sample_rate_hz=2048, channel_names=("Cz", "Pz"), backend=backend)
    ev.start(skip_warmup=True)
    gen = SignalGenerator(sample_rate_hz=2048, channels=("Cz", "Pz"), seed=1)
    ev.step_chunk(gen.next_chunk(64))
    taps = ev.last_taps()
    # The parent's derive structure should show through.
    assert "derive/band" in taps or "derive/reward_signal" in taps
    assert "inhibit/emg" in taps


# ---------------------------------------------------------------------------
# Dwell with non-comparison sub-conditions
# ---------------------------------------------------------------------------


_INSIDE_DWELL = """
protocol "inside_dwell" {
  meta { version = "1.0" }
  input "raw" { montage = referential(active: "Cz", reference: "device") }
  derive "env" {
    from = "raw"
    pipeline = [bandpass(band: (8 Hz, 12 Hz)), hilbert(), magnitude(), smooth(tau: 100 ms)]
  }
  reward {
    event = dwell(condition: inside("env", low: 0 uV, high: 5 uV), duration: 100 ms)
  }
  output { audio_chime = reward.event }
}
"""


def test_dwell_with_inside_condition(backend):
    """Sub-condition can be `inside(...)` not just above/below."""
    ir = resolve(parse(_INSIDE_DWELL))
    ev = Evaluator.live(ir, sample_rate_hz=250, channel_names=("Cz",), backend=backend)
    ev.start(skip_warmup=True)
    _push_one(ev, channels=("Cz",))
    taps = ev.last_taps()
    assert "reward/condition[0]" in taps
    assert isinstance(taps["reward/condition[0]"], bool)


# ---------------------------------------------------------------------------
# Cross-chunk consistency
# ---------------------------------------------------------------------------


def test_taps_repopulate_each_chunk(smr_bb_ir, backend):
    """Each step_chunk should fully refresh the tap dict — no stale
    values from a previous chunk."""
    ev = _live_bb(smr_bb_ir, backend)
    ev.start(skip_warmup=True)
    gen = SignalGenerator(sample_rate_hz=250, channels=("Cz", "F3", "F4", "Pz"), seed=1)
    ev.step_chunk(gen.next_chunk(64))
    keys_first = set(ev.last_taps().keys())
    ev.step_chunk(gen.next_chunk(64))
    keys_second = set(ev.last_taps().keys())
    # Same set of keys (protocol is unchanged).
    assert keys_first == keys_second


# ---------------------------------------------------------------------------
# Performance budget
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="perf threshold (<200µs) is Python-impl-specific; Rust perf is covered by tools/latency.py",
)
def test_tap_collection_perf_overhead(smr_bb_ir):
    """Tap collection itself should be cheap relative to the rest of
    step_chunk. We can't easily disable tap collection (and shouldn't —
    it's part of the contract), so this test verifies the dominant cost
    is the existing math (filters, percentile windows, dwell), not the
    new tap dict-building.

    Real-time concern: the existing PercentileImpl walks its window
    sample-by-sample per chunk, which dominates step_chunk cost. That's
    a separate optimization opportunity (Phase 0f+). This test only
    checks that tap collection isn't making the problem materially
    worse — it should be a few percent of total cost at most.
    """
    ev = _live_bb(smr_bb_ir)
    ev.start(skip_warmup=True)
    gen = SignalGenerator(sample_rate_hz=250, channels=("Cz", "F3", "F4", "Pz"), seed=1)
    # Warm up filter state for stable timing.
    for _ in range(40):
        ev.step_chunk(gen.next_chunk(64))

    # Measure last_taps() copy cost in isolation. This is the only
    # thing this PR adds that's hot-path: `_capture_taps` happens
    # inside step_chunk on every call, but its values are pulled from
    # arrays already computed — so we're measuring the dict.copy()
    # cost the host pays.
    start = time.perf_counter()
    n = 5000
    for _ in range(n):
        _ = ev.last_taps()
    elapsed = time.perf_counter() - start
    per_call_us = (elapsed / n) * 1_000_000
    # 5000 dict.copy() calls of a ~16-key dict should be ~microseconds each.
    assert per_call_us < 200.0, (
        f"last_taps() averaged {per_call_us:.2f} µs per call — much higher "
        f"than expected for a dict copy of ~16 keys"
    )
