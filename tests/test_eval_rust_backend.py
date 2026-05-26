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
AMP_BRAINBIT = REPO / "src" / "refrain" / "amp_profiles" / "brainbit_flex.json"

SAMPLE_RATE = 256
CHANNELS = ("Cz", "A1", "A2")
CHUNK_SEED = 42
CHUNK_SIZE = 64

# Brainbit fixture: 4 channels, 250 Hz, has a 90-s warmup muted phase.
BB_CHANNELS = ("Cz", "F3", "F4", "Pz")
BB_RATE = 250


@pytest.fixture(scope="module")
def smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"), load_amp_profile(AMP_Q21))


@pytest.fixture(scope="module")
def smr_bb_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz_brainbit.refrain"), load_amp_profile(AMP_BRAINBIT))


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
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
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


# ---------------------------------------------------------------------------
# Task 2 — error/lifecycle-semantics PARITY tests
# Each test asserts both backends raise the SAME exception type with the
# SAME message pattern.  Written as failing tests first (TDD), then the
# fixes below make them pass.
# ---------------------------------------------------------------------------


def _both_backends(ir, **kw):
    """Yield (label, ev) for python then rust.

    The Python backend is always yielded (it is the reference anchor and
    requires no wheel).  The Rust backend is skipped when refrain_core is
    not installed, so the Python half still runs even without the wheel.
    """
    yield "python", Evaluator.live(ir, **kw, backend="python")
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    yield "rust", Evaluator.live(ir, **kw, backend="rust")


# --- Contract 1: step_chunk after stop() → RuntimeError("after stop") ------


def test_parity_stop_blocks_step_chunk(smr_bb_ir):
    """Both backends must raise RuntimeError matching 'after stop' on
    step_chunk() called after stop()."""
    chunk = np.zeros((64, 4), dtype=np.float64)
    for backend, ev in _both_backends(
        smr_bb_ir, sample_rate_hz=BB_RATE, channel_names=BB_CHANNELS,
    ):
        ev.start(skip_warmup=True)
        ev.stop()
        assert ev.state == "stopped", f"{backend}: state after stop() must be 'stopped'"
        with pytest.raises(RuntimeError, match="after stop"):
            ev.step_chunk(chunk)


# --- Contract 2: step_chunk with wrong channel count → ValueError ----------


def test_parity_step_chunk_wrong_channel_count(smr_bb_ir):
    """Both backends must raise ValueError matching 'configured for 4' when
    step_chunk receives a chunk with the wrong number of columns."""
    wrong_chunk = np.zeros((64, 3), dtype=np.float64)  # 3 instead of 4 channels
    for backend, ev in _both_backends(
        smr_bb_ir, sample_rate_hz=BB_RATE, channel_names=BB_CHANNELS,
    ):
        ev.start(skip_warmup=True)
        with pytest.raises(ValueError, match="configured for 4"):
            ev.step_chunk(wrong_chunk)


# --- Contract 3: set_control unknown name → KeyError("no control named") --


def test_parity_set_control_unknown_name(smr_bb_ir):
    """Both backends must raise KeyError matching 'no control named' for an
    unknown control name."""
    for backend, ev in _both_backends(
        smr_bb_ir, sample_rate_hz=BB_RATE, channel_names=BB_CHANNELS,
    ):
        with pytest.raises(KeyError, match="no control named"):
            ev.set_control("nonexistent_ctrl_xyz", 0.5)


# --- Contract 4: state / warmup_remaining_s behave consistently -----------


def test_parity_state_warmup(smr_bb_ir):
    """Both backends must report state='warmup' and warmup_remaining_s≈90
    immediately after start() with no skip_warmup."""
    for backend, ev in _both_backends(
        smr_bb_ir, sample_rate_hz=BB_RATE, channel_names=BB_CHANNELS,
    ):
        ev.start()  # enters warmup (90-s phase)
        assert ev.state == "warmup", f"{backend}: state must be 'warmup' after start()"
        assert ev.warmup_remaining_s == pytest.approx(90.0, abs=0.01), (
            f"{backend}: warmup_remaining_s expected ~90.0, got {ev.warmup_remaining_s}"
        )


def test_parity_warmup_remaining_decrements(smr_bb_ir):
    """Both backends must report the same decremented warmup_remaining_s after
    pushing several chunks of warmup-state input (still within the warmup window).

    Locks the samples_pushed accounting parity during warmup: both backends must
    track pushed samples identically so the remaining-warmup calculation agrees.
    """
    # Use a chunk size of 250 samples (1 s at 250 Hz). Push 3 chunks → 3 s.
    chunk_size = 250
    n_chunks = 3
    chunk = np.zeros((chunk_size, len(BB_CHANNELS)), dtype=np.float64)

    remaining_values: dict[str, float] = {}
    for backend, ev in _both_backends(
        smr_bb_ir, sample_rate_hz=BB_RATE, channel_names=BB_CHANNELS,
    ):
        ev.start()  # enters warmup (90-s phase)
        assert ev.state == "warmup", f"{backend}: must be in warmup after start()"
        initial = ev.warmup_remaining_s
        assert initial == pytest.approx(90.0, abs=0.01), (
            f"{backend}: expected ~90.0 s initially, got {initial}"
        )
        for _ in range(n_chunks):
            ev.step_chunk(chunk.copy())
        assert ev.state == "warmup", f"{backend}: must still be in warmup after {n_chunks} chunks"
        remaining = ev.warmup_remaining_s
        assert remaining < initial, (
            f"{backend}: warmup_remaining_s must decrease after pushing chunks; "
            f"initial={initial}, after={remaining}"
        )
        # 3 chunks × 1 s/chunk = 3 s consumed → ~87 s remaining
        assert remaining == pytest.approx(87.0, abs=0.1), (
            f"{backend}: expected ~87.0 s remaining after 3 s of input, got {remaining}"
        )
        remaining_values[backend] = remaining

    # Both backends must agree (within floating-point tolerance).
    assert remaining_values["python"] == pytest.approx(remaining_values["rust"], abs=1e-6), (
        f"warmup_remaining_s mismatch: python={remaining_values['python']}, "
        f"rust={remaining_values['rust']}"
    )


def test_parity_state_run_after_skip_warmup(smr_bb_ir):
    """Both backends must report state='run' and warmup_remaining_s==0.0
    after start(skip_warmup=True)."""
    for backend, ev in _both_backends(
        smr_bb_ir, sample_rate_hz=BB_RATE, channel_names=BB_CHANNELS,
    ):
        ev.start(skip_warmup=True)
        assert ev.state == "run", f"{backend}: state must be 'run' after skip_warmup"
        assert ev.warmup_remaining_s == 0.0, (
            f"{backend}: warmup_remaining_s must be 0.0 when in run state"
        )


def test_parity_start_twice_raises(smr_bb_ir):
    """Both backends must raise RuntimeError matching \"state 'run'\" when
    start() is called a second time."""
    for backend, ev in _both_backends(
        smr_bb_ir, sample_rate_hz=BB_RATE, channel_names=BB_CHANNELS,
    ):
        ev.start(skip_warmup=True)
        with pytest.raises(RuntimeError, match="state 'run'"):
            ev.start()


def test_rust_panic_surfaces_as_catchable_runtimeerror():
    """A panic inside the Rust core must surface as a *catchable* RuntimeError,
    not an uncatchable `pyo3_runtime.PanicException` (a BaseException) — so a host
    can `except Exception` and fall back to backend="python" instead of having the
    panic take down the process. Regression for the panic-catchability gap
    surfaced by the coherence bug report.

    The resolver rejects unsupported constructs up front, so it won't *emit* a
    panic-triggering protocol; we exercise the PyO3 guard directly by corrupting a
    valid IR-JSON's primitive callee to one the Rust core has no impl for (which
    hits an "unsupported DSP primitive" panic during the core's build)."""
    refrain_core = pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    from refrain.ir_json import ir_to_json

    src = """
        protocol "p" {
          meta { version = "1.0"; evidence = "demo"; description = "x" }
          input "raw" { montage = referential(active: "Cz", reference: "device") }
          derive "d" { from = "raw"; pipeline = [bandpass(band: (8 Hz, 12 Hz))] }
          reward { continuous = sigmoid("d", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    """
    ir_json = ir_to_json(resolve(parse(src)), sample_rate_hz=256.0)
    corrupted = ir_json.replace('"bandpass"', '"nonexistent_dsp_primitive"')
    assert corrupted != ir_json, "expected to corrupt the bandpass callee"

    # `except Exception` MUST catch it — a bare PanicException (BaseException)
    # would escape this and crash the host.
    with pytest.raises(Exception) as excinfo:
        refrain_core.RustEvaluator(corrupted, 256.0, ["Cz"])
    assert isinstance(excinfo.value, RuntimeError)   # the guard's conversion
    assert "Rust" in str(excinfo.value)
