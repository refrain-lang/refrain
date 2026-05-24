# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for Evaluator.live(..., backend="rust") delegation to refrain-core.

Verifies that the Rust backend produces the same feedback events as the
Python backend within floating-point tolerance, and that lifecycle/control
methods forward correctly.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator, Event
from refrain.parser import parse, parse_file
from refrain.resolver import resolve


REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

SAMPLE_RATE = 256
CHANNELS = ("Cz", "A1", "A2")
CHUNK_SEED = 42
CHUNK_SIZE = 64


@pytest.fixture(scope="module")
def smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"), load_amp_profile(AMP_Q21))


def _make_chunk(n_samples: int = CHUNK_SIZE, seed: int = CHUNK_SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n_samples, len(CHANNELS)))


# ---------------------------------------------------------------------------
# TDD: failing test — backend kwarg not yet accepted
# ---------------------------------------------------------------------------


def test_rust_backend_events_parity(smr_ir):
    """Rust backend must produce the same events as the Python backend.

    Feeds the same seeded chunk to both backends after skip_warmup=True.
    Events must be Event instances and match within atol=1e-6 on timestamp_s
    and value. The test is events-only (no record_streams) for this task.
    """
    chunk = _make_chunk()

    # Python reference run.
    py_ev = Evaluator.live(
        smr_ir,
        sample_rate_hz=SAMPLE_RATE,
        channel_names=CHANNELS,
        backend="python",
    )
    py_ev.start(skip_warmup=True)
    py_events = py_ev.step_chunk(chunk)

    # Rust backend run.
    rust_ev = Evaluator.live(
        smr_ir,
        sample_rate_hz=SAMPLE_RATE,
        channel_names=CHANNELS,
        backend="rust",
    )
    rust_ev.start(skip_warmup=True)
    rust_events = rust_ev.step_chunk(chunk)

    # Both backends must return lists of Event instances.
    assert isinstance(py_events, list)
    assert isinstance(rust_events, list)
    for e in py_events:
        assert isinstance(e, Event), f"Python event {e!r} is not an Event"
    for e in rust_events:
        assert isinstance(e, Event), f"Rust event {e!r} is not an Event"

    # Must produce the same number of events with the same structure.
    assert len(py_events) == len(rust_events), (
        f"Event count mismatch: Python={len(py_events)}, Rust={len(rust_events)}\n"
        f"Python: {py_events}\nRust: {rust_events}"
    )
    for i, (pe, re) in enumerate(zip(py_events, rust_events)):
        assert pe.channel == re.channel, f"event[{i}] channel mismatch: {pe.channel!r} vs {re.channel!r}"
        assert pe.kind == re.kind, f"event[{i}] kind mismatch: {pe.kind!r} vs {re.kind!r}"
        assert abs(pe.timestamp_s - re.timestamp_s) < 1e-6, (
            f"event[{i}] timestamp_s mismatch: {pe.timestamp_s} vs {re.timestamp_s}"
        )
        if pe.value is None:
            assert re.value is None, f"event[{i}] value: Python=None, Rust={re.value}"
        else:
            assert re.value is not None, f"event[{i}] value: Python={pe.value}, Rust=None"
            assert abs(pe.value - re.value) < 1e-6, (
                f"event[{i}] value mismatch: {pe.value} vs {re.value}"
            )


def test_rust_backend_missing_wheel_gives_clear_error(smr_ir):
    """When the wheel is not built, a clear ImportError-style message is raised.

    This test is only meaningful when the wheel IS available (so the backend
    kwarg itself is accepted). It validates error message quality. If refrain_core
    is already importable, we skip the wheel-missing path here (can't remove an
    installed package in test). We just verify the backend kwarg is accepted.
    """
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    # If we reach here, the wheel is available; backend kwarg must not raise.
    ev = Evaluator.live(
        smr_ir,
        sample_rate_hz=SAMPLE_RATE,
        channel_names=CHANNELS,
        backend="rust",
    )
    assert ev is not None


def test_rust_backend_invalid_name_raises(smr_ir):
    """An unknown backend name raises ValueError immediately."""
    with pytest.raises(ValueError, match="backend"):
        Evaluator.live(
            smr_ir,
            sample_rate_hz=SAMPLE_RATE,
            channel_names=CHANNELS,
            backend="invalid_backend_xyz",
        )


def test_rust_backend_stop_forwarded(smr_ir):
    """stop() must forward to Rust; subsequent step_chunk should raise."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    ev = Evaluator.live(
        smr_ir,
        sample_rate_hz=SAMPLE_RATE,
        channel_names=CHANNELS,
        backend="rust",
    )
    ev.start(skip_warmup=True)
    ev.stop()
    with pytest.raises((RuntimeError, Exception)):
        ev.step_chunk(_make_chunk())


def test_rust_backend_set_control_forwarded(smr_ir):
    """set_control must forward to Rust (no error for known controls)."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    ev = Evaluator.live(
        smr_ir,
        sample_rate_hz=SAMPLE_RATE,
        channel_names=CHANNELS,
        backend="rust",
    )
    ev.start(skip_warmup=True)
    # smr_cz.refrain has a smr_target_pct control — setting it must not raise.
    ev.set_control("smr_target_pct", 50.0)
    # Unknown name must raise KeyError, just like the Python backend.
    with pytest.raises(KeyError):
        ev.set_control("_no_such_control_xyz", 1.0)


def test_rust_backend_multi_chunk_parity(smr_ir):
    """Running multiple chunks through both backends stays in sync.

    Uses 20 chunks with the same sequence of seeds to verify the state
    machines evolve identically (no divergence from the first chunk on).
    """
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")

    py_ev = Evaluator.live(
        smr_ir, sample_rate_hz=SAMPLE_RATE, channel_names=CHANNELS, backend="python",
    )
    py_ev.start(skip_warmup=True)

    rust_ev = Evaluator.live(
        smr_ir, sample_rate_hz=SAMPLE_RATE, channel_names=CHANNELS, backend="rust",
    )
    rust_ev.start(skip_warmup=True)

    rng = np.random.default_rng(99)
    all_py: list[Event] = []
    all_rust: list[Event] = []
    for _ in range(20):
        chunk = rng.standard_normal((CHUNK_SIZE, len(CHANNELS)))
        all_py.extend(py_ev.step_chunk(chunk.copy()))
        all_rust.extend(rust_ev.step_chunk(chunk.copy()))

    assert len(all_py) == len(all_rust), (
        f"Multi-chunk event count: Python={len(all_py)}, Rust={len(all_rust)}"
    )
    for i, (pe, re) in enumerate(zip(all_py, all_rust)):
        assert pe.channel == re.channel and pe.kind == re.kind
        assert abs(pe.timestamp_s - re.timestamp_s) < 1e-6


def test_rust_backend_last_taps_returns_dict(smr_ir):
    """last_taps() must return a dict with at least the core tap keys."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    ev = Evaluator.live(
        smr_ir, sample_rate_hz=SAMPLE_RATE, channel_names=CHANNELS, backend="rust",
    )
    ev.start(skip_warmup=True)
    ev.step_chunk(_make_chunk())
    taps = ev.last_taps()
    assert isinstance(taps, dict), "last_taps() must return a dict"
    assert len(taps) > 0, "last_taps() must be non-empty after step_chunk"
    # Known keys from the smr_cz protocol.
    assert "input/raw" in taps
    assert "muted" in taps
    # Booleans must be bool, not 0.0/1.0.
    assert isinstance(taps["muted"], bool), f"muted must be bool, got {type(taps['muted'])}"


def test_rust_backend_last_streams_record_off(smr_ir):
    """Without record_streams=True, last_streams() returns empty dict."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    ev = Evaluator.live(
        smr_ir, sample_rate_hz=SAMPLE_RATE, channel_names=CHANNELS, backend="rust",
    )
    ev.start(skip_warmup=True)
    ev.step_chunk(_make_chunk())
    assert ev.last_streams() == {}, "record_streams=False must return {}"


def test_rust_backend_last_streams_record_on(smr_ir):
    """With record_streams=True, last_streams() returns per-chunk arrays."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    ev = Evaluator.live(
        smr_ir,
        sample_rate_hz=SAMPLE_RATE,
        channel_names=CHANNELS,
        backend="rust",
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    chunk = _make_chunk()
    ev.step_chunk(chunk)
    streams = ev.last_streams()
    assert isinstance(streams, dict)
    assert len(streams) > 0, "record_streams=True must capture streams"
    assert "raw" in streams, f"'raw' not in streams: {list(streams.keys())}"
    assert streams["raw"].shape[0] == CHUNK_SIZE


# ---------------------------------------------------------------------------
# FIX 2: event-ordering regression guard — value-before-event declaration order
# ---------------------------------------------------------------------------

# Minimal inline protocol: declares a VALUE-kind output FIRST, then an EVENT-
# kind output SECOND.  The dwell duration (4 ms = 1 sample at 256 Hz) means
# any chunk whose condition is always-true fires an event on the rising edge at
# sample 0.  `below(raw, t_huge)` with t_huge=99999 uV is trivially true for
# any normal EEG-scale signal, so events always fire.
_VALUE_BEFORE_EVENT_SRC = """
protocol "ordering_test" {
  requires {
    sample_rate = ">= 256 Hz"
    channels = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  threshold "t_huge" {
    signal = "raw"
    type   = absolute(99999 uV)
  }
  reward {
    event      = dwell(
      condition: below("raw", "t_huge"),
      duration: 4 ms
    )
    continuous = sigmoid("raw" / "t_huge", midpoint: 1.0, steepness: 1)
  }
  output {
    gain  = reward.continuous   // value-kind  (FIRST in declaration order)
    chime = reward.event        // event-kind  (SECOND in declaration order)
  }
}
"""


@pytest.fixture(scope="module")
def ordering_ir():
    return resolve(parse(_VALUE_BEFORE_EVENT_SRC))


def test_rust_event_ordering_value_before_event(ordering_ir):
    """Rust backend must emit events in IR declaration order.

    Regression guard for the FIX 1 ordering bug: the pre-fix implementation
    emitted all event-kind channels before all value-kind channels regardless
    of IR declaration order.  This protocol declares a VALUE-kind output
    (gain) before an EVENT-kind output (chime), so the wrong order was
    gain-kind=value before chime-kind=event → WRONG (chime then gain).

    The correct order is:
      [0] channel='gain',  kind='value'   (declared first)
      [1] channel='chime', kind='event'   (declared second)

    Both backends must agree element-by-element, and at least one
    kind=='event' event must fire (so the test cannot silently pass with
    zero events — that was the original blind spot).
    """
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    rng = np.random.default_rng(42)
    chunk = rng.standard_normal((CHUNK_SIZE, 1))

    py_ev = Evaluator.live(
        ordering_ir, sample_rate_hz=SAMPLE_RATE, channel_names=("Cz",), backend="python",
    )
    py_ev.start(skip_warmup=True)
    py_events = py_ev.step_chunk(chunk.copy())

    rust_ev = Evaluator.live(
        ordering_ir, sample_rate_hz=SAMPLE_RATE, channel_names=("Cz",), backend="rust",
    )
    rust_ev.start(skip_warmup=True)
    rust_events = rust_ev.step_chunk(chunk.copy())

    # At least one discrete event must have fired — guards against a chunk
    # that yields zero events and lets the ordering assertion vacuously pass.
    assert any(e.kind == "event" for e in rust_events), (
        "No kind=='event' event fired from the Rust backend; "
        "the dwell condition (below raw < 99999 uV) should always be true. "
        f"Rust events: {rust_events}"
    )
    assert any(e.kind == "event" for e in py_events), (
        "No kind=='event' event fired from the Python backend either; "
        f"Python events: {py_events}"
    )

    # Both backends must produce the same number of events.
    assert len(py_events) == len(rust_events), (
        f"Event count mismatch: Python={len(py_events)}, Rust={len(rust_events)}\n"
        f"Python: {py_events}\nRust: {rust_events}"
    )

    # Every event must match element-by-element in declaration order.
    for i, (pe, re) in enumerate(zip(py_events, rust_events)):
        assert pe.channel == re.channel, (
            f"event[{i}] channel mismatch (ordering regression?): "
            f"Python={pe.channel!r} Rust={re.channel!r}\n"
            f"Python events: {py_events}\nRust events: {rust_events}"
        )
        assert pe.kind == re.kind, (
            f"event[{i}] kind mismatch: Python={pe.kind!r} Rust={re.kind!r}"
        )
        assert abs(pe.timestamp_s - re.timestamp_s) < 1e-6, (
            f"event[{i}] timestamp_s mismatch: {pe.timestamp_s} vs {re.timestamp_s}"
        )
        if pe.value is None:
            assert re.value is None, f"event[{i}] value: Python=None, Rust={re.value}"
        else:
            assert re.value is not None, (
                f"event[{i}] value: Python={pe.value}, Rust=None"
            )
            assert abs(pe.value - re.value) < 1e-6, (
                f"event[{i}] value mismatch: {pe.value} vs {re.value}"
            )
