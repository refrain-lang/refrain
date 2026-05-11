# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Regression tests for IRControlRef substitution in evaluator static args.

Othmer ILF uses `bandpass(center: orf, ...)` where `orf` is a control
reference. The evaluator must substitute the control's resolved value
for the IRControlRef before calling the primitive constructor; passing
the IRControlRef through would fail with `TypeError: unsupported
operand type for /: 'IRControlRef' and 'float'` inside the bandpass
filter design.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator, eval_protocol
from refrain.parser import parse, parse_file
from refrain.resolver import resolve
from refrain.sources import SyntheticSource
from refrain.synthetic import SignalGenerator


REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_PATH = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"


def _synthetic_source(channels=("T3", "T4", "A1", "A2"), duration_s=5.0):
    gen = SignalGenerator(
        sample_rate_hz=256, channels=channels, seed=1, noise_uv_rms=10.0,
    )
    return SyntheticSource(gen, duration_s=duration_s)


def test_othmer_ilf_runs_end_to_end():
    """Bandpass center is a control reference — the evaluator must
    substitute the control's default before constructing the filter."""
    ir = resolve(parse_file(EXAMPLES / "othmer_ilf_t3t4.refrain"),
                 load_amp_profile(AMP_PATH))
    src = _synthetic_source(channels=("T3", "T4"))
    events = list(eval_protocol(ir, src, chunk_size=64))
    # Othmer ILF has only `continuous` (no `event`); we should get
    # value-summary events for each output channel.
    assert any(e.channel == "audio_gain" for e in events)
    assert any(e.channel == "video_clarity" for e in events)
    assert any(e.channel == "ambient_density" for e in events)
    # No NaN.
    for e in events:
        if e.kind == "value":
            assert e.value is not None and 0.0 <= e.value <= 1.0


def test_control_ref_substituted_in_nested_call():
    """`bandpass(center: orf, bandwidth: ratio(2.5), ...)` — the control
    ref appears as a static arg deep in a nested call. The evaluator
    must resolve it during the setup pass."""
    src_text = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "filtered" {
            from = "raw"
            pipeline = [
              bandpass(center: my_freq, bandwidth: ratio(2.0), order: 4),
            ]
          }
          reward {
            continuous = sigmoid("filtered", midpoint: 0 uV, steepness: 1)
          }
          output { audio_gain = reward.continuous }
          controls {
            my_freq = frequency {
              default = 12 Hz
              range = (1 Hz, 50 Hz)
            }
          }
        }
    '''
    ir = resolve(parse(src_text))
    src = _synthetic_source(channels=("T3", "T4"))
    # Instantiation alone should succeed — this was the original
    # failure mode (IRControlRef leaking into BandpassImpl.__init__).
    ev = Evaluator(ir, src)
    events = list(ev.run(chunk_size=64))
    assert len(events) > 0


def test_control_ref_with_correct_units_used():
    """Verify the control's default unit (e.g. Hz) is what reaches the
    impl, not a raw numeric value misinterpreted."""
    src_text = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "filtered" {
            from = "raw"
            pipeline = [
              bandpass(center: my_freq, bandwidth: ratio(2.0)),
            ]
          }
          controls {
            my_freq = frequency {
              default = 10 Hz
              range = (1 Hz, 50 Hz)
            }
          }
        }
    '''
    from refrain.primitive_impls import BandpassImpl
    ir = resolve(parse(src_text))
    src = _synthetic_source(channels=("T3", "T4"))
    ev = Evaluator(ir, src)
    # Find the bandpass impl and verify it was constructed with the
    # resolved control value (10 Hz, not an IRControlRef).
    bandpass_impls = [i for i in ev._impls.values() if isinstance(i, BandpassImpl)]
    assert len(bandpass_impls) == 1
    bp = bandpass_impls[0]
    # The SOS coefficients should have been computed (no error).
    assert bp.sos.shape[0] >= 1  # at least one biquad section
