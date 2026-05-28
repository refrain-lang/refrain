# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Push-mode evaluator, lifecycle state, set_control wiring.

The validation harness in `test_eval_validation.py` skips warmup since
those tests are about burst/threshold behaviour. This file is the
counterpart that specifically exercises the lifecycle and embedding
surface used by host applications.

Push-mode (live) tests declare the `backend` fixture so they run on both the Python and Rust backends (see tests/conftest.py); set REFRAIN_EVAL_BACKEND=rust to exercise the Rust core.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator, eval_protocol
from refrain.parser import parse, parse_file
from refrain.resolver import resolve
from refrain.sources import SyntheticSource
from refrain.synthetic import SignalGenerator, SMRBurst

# conftest.py provides the ``backend`` fixture and RUST_BACKEND_ACTIVE flag.
from tests.conftest import RUST_BACKEND_ACTIVE


REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"
AMP_BRAINBIT = REPO / "src" / "refrain" / "amp_profiles" / "brainbit_flex.json"


@pytest.fixture(scope="module")
def smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"),
                   load_amp_profile(AMP_Q21))


@pytest.fixture(scope="module")
def smr_bb_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz_brainbit.refrain"),
                   load_amp_profile(AMP_BRAINBIT))


# ---------------------------------------------------------------------------
# Push-mode construction
# ---------------------------------------------------------------------------


def test_live_constructor_skips_source(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    assert ev.source is None
    assert ev.sample_rate_hz == 250.0
    assert ev.channel_names == ("Cz", "F3", "F4", "Pz")


def test_live_constructor_state_is_ready(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    assert ev.state == "ready"


def test_run_without_source_raises(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    with pytest.raises(RuntimeError, match="requires a Source"):
        list(ev.run())


def test_evaluator_rejects_both_source_and_explicit_args(smr_bb_ir):
    gen = SignalGenerator(sample_rate_hz=250, channels=("Cz",))
    src = SyntheticSource(gen, duration_s=1.0)
    with pytest.raises(ValueError, match="not both"):
        Evaluator(smr_bb_ir, src, sample_rate_hz=250, channel_names=("Cz",))


def test_push_mode_requires_both_rate_and_channels(smr_bb_ir):
    with pytest.raises(ValueError, match="both"):
        Evaluator(smr_bb_ir, sample_rate_hz=250)


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="asserts on Python-only `_warmup_samples` internal",
)
def test_start_enters_warmup_when_protocol_has_muted_phase(smr_bb_ir):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
    )
    # The BrainBit SMR example declares a 90s warmup phase.
    assert ev._warmup_samples == 90 * 250
    ev.start()
    assert ev.state == "warmup"
    assert ev.warmup_remaining_s == pytest.approx(90.0, abs=0.01)


def test_start_skip_warmup_jumps_to_run(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    ev.start(skip_warmup=True)
    assert ev.state == "run"
    assert ev.warmup_remaining_s == 0.0


def test_protocol_with_no_warmup_phase_starts_in_run(backend):
    src = '''
        protocol "no_warmup" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
          reward { continuous = sigmoid("env", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    ev = Evaluator.live(ir, sample_rate_hz=250, channel_names=("T3", "T4"), backend=backend)
    ev.start()
    assert ev.state == "run"


def test_start_twice_raises(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    ev.start(skip_warmup=True)
    with pytest.raises(RuntimeError, match="state 'run'"):
        ev.start()


def test_stop_blocks_further_step_chunk(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    ev.start(skip_warmup=True)
    ev.stop()
    assert ev.state == "stopped"
    with pytest.raises(RuntimeError, match="after stop"):
        ev.step_chunk(np.zeros((64, 4), dtype=np.float64))


# ---------------------------------------------------------------------------
# step_chunk produces same events as pull-mode run
# ---------------------------------------------------------------------------


def test_push_mode_matches_pull_mode_event_for_event(backend):
    src_text = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [bandpass(band: (12 Hz, 15 Hz)), hilbert(), magnitude(), smooth(tau: 100 ms)]
          }
          threshold "t" { signal = "env"; type = absolute(2 uV) }
          reward {
            event = dwell(condition: above("env", "t"), duration: 100 ms)
            continuous = sigmoid("env", midpoint: 2 uV, steepness: 1)
          }
          output {
            audio_chime = reward.event
            audio_gain = reward.continuous
          }
        }
    '''
    ir = resolve(parse(src_text))
    # Same generator, same seed → identical chunks.
    def gen():
        return SignalGenerator(sample_rate_hz=250, channels=("T3", "T4"), seed=7)

    pull_src = SyntheticSource(gen(), duration_s=4.0)
    pull_events = list(eval_protocol(ir, pull_src, chunk_size=64, skip_warmup=True))

    push_ev = Evaluator.live(ir, sample_rate_hz=250, channel_names=("T3", "T4"), backend=backend)
    push_ev.start(skip_warmup=True)
    push_gen = gen()
    push_events: list = []
    remaining = int(4.0 * 250)
    while remaining > 0:
        size = min(64, remaining)
        chunk = push_gen.next_chunk(size)
        push_events.extend(push_ev.step_chunk(chunk))
        remaining -= size

    # Identical events at identical timestamps and values.
    assert len(pull_events) == len(push_events)
    for a, b in zip(pull_events, push_events):
        assert a.channel == b.channel
        assert a.kind == b.kind
        assert a.timestamp_s == pytest.approx(b.timestamp_s)
        if a.kind == "value":
            assert a.value == pytest.approx(b.value)


def test_step_chunk_accepts_variable_chunk_sizes(smr_bb_ir, backend):
    """Live amps deliver chunks at whatever cadence they choose; the
    evaluator must accept arbitrary sizes without state corruption."""
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    ev.start(skip_warmup=True)
    gen = SignalGenerator(sample_rate_hz=250, channels=("Cz", "F3", "F4", "Pz"), seed=1)
    for size in (37, 64, 8, 192, 100):
        chunk = gen.next_chunk(size)
        events = ev.step_chunk(chunk)
        assert all(0.0 <= (e.value or 0.0) <= 1.0 for e in events if e.kind == "value")


def test_step_chunk_rejects_wrong_channel_count(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    ev.start(skip_warmup=True)
    # Wrong column count (3 instead of 4) should error clearly.
    with pytest.raises(ValueError, match="configured for 4"):
        ev.step_chunk(np.zeros((64, 3), dtype=np.float64))


# ---------------------------------------------------------------------------
# Warmup output suppression
# ---------------------------------------------------------------------------


def test_output_suppressed_during_warmup(smr_bb_ir, backend):
    """While the evaluator is in `warmup` state, no Events should be
    emitted (filter state still updates internally)."""
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    ev.start()  # default: enters warmup
    assert ev.state == "warmup"
    gen = SignalGenerator(
        sample_rate_hz=250, channels=("Cz", "F3", "F4", "Pz"),
        bursts=(SMRBurst(start_s=1.0, end_s=3.0, center_hz=13.5, amplitude_uv=50.0, channel="Cz"),),
        seed=42,
    )
    # 10s of input — entirely within the 90s warmup window.
    all_events = []
    for _ in range(40):  # 40 * 64 / 250 = ~10.2s
        all_events.extend(ev.step_chunk(gen.next_chunk(64)))
    assert ev.state == "warmup"
    assert all_events == [], f"warmup should suppress all output; got {len(all_events)} events"


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="uses Python-only `_warmup_samples` internal to size the chunk",
)
def test_warmup_transitions_to_run_after_window(smr_bb_ir):
    """Once enough samples have been pushed to satisfy the warmup
    window, the next step_chunk transitions to `run` and starts
    emitting events."""
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
    )
    ev.start()
    assert ev.state == "warmup"
    # Push exactly the warmup-worth of samples in one big chunk.
    chunk_size = ev._warmup_samples
    gen = SignalGenerator(sample_rate_hz=250, channels=("Cz", "F3", "F4", "Pz"), seed=1)
    ev.step_chunk(gen.next_chunk(chunk_size))
    assert ev.state == "run", "should transition to run once warmup samples pushed"


# ---------------------------------------------------------------------------
# set_control wiring
# ---------------------------------------------------------------------------


def test_set_control_unknown_name_raises(smr_bb_ir, backend):
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend=backend,
    )
    with pytest.raises(KeyError, match="no control named"):
        ev.set_control("nonexistent", 0.5)


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="asserts on Python-only `_controls` internal (rust forwards set_control to the core, not the Python dict)",
)
def test_set_control_updates_dict(smr_bb_ir):
    # Pinned to python: asserts on the Python engine's `_controls` dict
    # (rust forwards set_control to the core, not this dict). With the
    # default backend="auto" a bare call would pick rust where the wheel
    # is installed, so the backend must be explicit here.
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend="python",
    )
    assert ev._controls["control/smr_target_pct"] == 70.0
    ev.set_control("smr_target_pct", 55)
    assert ev._controls["control/smr_target_pct"] == 55.0


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="inspects Python-only `_impls` / PercentileImpl internals",
)
def test_set_control_propagates_to_percentile_impls(smr_bb_ir):
    """The BrainBit SMR protocol wires smr_target_pct into the smr_t
    threshold's percentile. set_control should forward the new value
    to the PercentileImpl backing that threshold."""
    from refrain.primitive_impls import PercentileImpl

    # Pinned to python: inspects the Python engine's `_impls` internals; the
    # default backend="auto" would otherwise pick rust where the wheel exists.
    ev = Evaluator.live(
        smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
        backend="python",
    )
    # Find all PercentileImpls.
    pct_impls = [i for i in ev._impls.values() if isinstance(i, PercentileImpl)]
    assert len(pct_impls) > 0
    initial_target_pcts = [p.target_pct for p in pct_impls]
    # Default for smr_target_pct is 70.
    assert 70.0 in initial_target_pcts

    ev.set_control("smr_target_pct", 55)
    # At least one impl should now have target_pct = 55.
    new_target_pcts = [p.target_pct for p in pct_impls]
    assert 55.0 in new_target_pcts


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="inspects Python-only `_impls` / AbsoluteThresholdImpl internals",
)
def test_set_control_propagates_to_absolute_threshold_impl():
    """An absolute(value: <control_ref>) threshold must update its
    `.value` when set_control is called for the bound voltage control.
    Without an update_control hook the host-side knob silently no-ops."""
    from refrain.primitive_impls import AbsoluteThresholdImpl

    src_text = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
          threshold "t" {
            signal = "env"
            type = absolute(value: smr_threshold_uv)
            live_tunable = true
          }
          reward {
            continuous = sigmoid("env" / "t", midpoint: 1.0, steepness: 3)
          }
          output { audio_gain = reward.continuous }
          controls {
            smr_threshold_uv = voltage {
              default = 4 uV
              range = (1 uV, 25 uV)
              live_tunable = true
            }
          }
        }
    '''
    ir = resolve(parse(src_text))
    ev = Evaluator.live(
        ir, sample_rate_hz=250, channel_names=("T3", "T4"), backend="python",
    )
    abs_impls = [i for i in ev._impls.values() if isinstance(i, AbsoluteThresholdImpl)]
    assert len(abs_impls) == 1
    assert abs_impls[0].value == pytest.approx(4.0)

    ev.set_control("smr_threshold_uv", 5.5)
    assert abs_impls[0].value == pytest.approx(5.5)


# --- backend="auto" selection (Phase D1) -----------------------------------


def test_auto_backend_falls_back_to_python_without_wheel(smr_bb_ir, monkeypatch):
    # When refrain_core is not importable, the default backend="auto" resolves
    # to the pure-Python engine — no ImportError. Simulated deterministically.
    import refrain.eval_ as eval_mod

    monkeypatch.setattr(eval_mod, "_refrain_core_available", lambda: False)
    ev = Evaluator.live(smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"))
    assert ev._backend == "python"


def test_auto_backend_uses_rust_when_available(smr_bb_ir):
    # Where the wheel is installed, the default backend="auto" picks rust.
    pytest.importorskip("refrain_core")
    ev = Evaluator.live(smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"))
    assert ev._backend == "rust"


def test_unknown_backend_raises(smr_bb_ir):
    with pytest.raises(ValueError, match="auto.*python.*rust|unknown backend"):
        Evaluator.live(
            smr_bb_ir, sample_rate_hz=250, channel_names=("Cz", "F3", "F4", "Pz"),
            backend="bogus",
        )
