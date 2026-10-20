# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""`sqrt()` running, on both backends.

Mean-square and variance already composed out of `x * x` and `smooth`; only
the root was missing, so one primitive unlocks RMS, standard deviation and
coefficient of variation together. These tests drive those compositions end to
end rather than calling the impl, because the drift gate re-runs every
`test_eval_*.py` under `REFRAIN_EVAL_BACKEND=rust` — which is what holds the
Python and Rust roots to the same numbers, clamp included.
"""

from __future__ import annotations

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve


CHANNELS = ("Cz", "A1", "A2")
FS = 100.0

# `tau` is short against the chunk lengths below so the moving averages settle
# well inside one test, and the asserted values are the settled ones.
RMS = '''
protocol "rms" {
  meta { version = "1.0"; evidence = "demo"; description = "RMS and SD via sqrt" }
  requires { sample_rate = ">= 100 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "square" { formula = "raw" * "raw" }
  derive "mean" { from = "raw"; pipeline = [ smooth(tau: 50 ms) ] }
  derive "mean_square" { from = "square"; pipeline = [ smooth(tau: 50 ms) ] }
  derive "rms" { from = "mean_square"; pipeline = [ sqrt() ] }
  derive "variance" { formula = "mean_square" - "mean" * "mean" }
  derive "sd" { from = "variance"; pipeline = [ sqrt() ] }
  output { audio_gain = "rms" }
}
'''

# Deliberately negative: 1x squared minus 2x squared is -3x², a uV2 stream
# whose value is below zero everywhere. There is no unary minus in the
# grammar, so this is how a test reaches the clamp on purpose.
NEGATIVE = '''
protocol "negative root" {
  meta { version = "1.0"; evidence = "demo"; description = "clamped root" }
  requires { sample_rate = ">= 100 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "small" { formula = "raw" * "raw" }
  derive "large" { formula = ("raw" * 2) * ("raw" * 2) }
  derive "negative" { formula = "small" - "large" }
  derive "root" { from = "negative"; pipeline = [ sqrt() ] }
  output { audio_gain = "root" }
}
'''


def _run(src: str, *, value: float, n: int, backend: str) -> dict[str, np.ndarray]:
    """Resolve `src`, drive `n` samples of a constant `value` on Cz, and
    return the recorded streams of the final chunk."""
    ev = Evaluator.live(
        resolve(parse(src)), sample_rate_hz=FS, channel_names=CHANNELS,
        record_streams=True, backend=backend,
    )
    ev.start(skip_warmup=True)
    block = np.zeros((n, len(CHANNELS)), dtype=np.float64)
    block[:, 0] = value
    ev.step_chunk(block)
    return ev.last_streams()


def test_rms_of_a_constant_is_the_constant(backend):
    """The headline composition: `x*x -> smooth -> sqrt` is RMS, and the RMS
    of a steady 3 uV is 3 uV."""
    streams = _run(RMS, value=3.0, n=200, backend=backend)
    assert float(streams["mean_square"][-1]) == pytest.approx(9.0, abs=1e-9)
    assert float(streams["rms"][-1]) == pytest.approx(3.0, abs=1e-9)


def test_rms_tracks_the_mean_square_sample_for_sample(backend):
    """Stronger than the settled value: the root must hold over the whole
    transient, which is where the two backends could disagree."""
    streams = _run(RMS, value=3.0, n=200, backend=backend)
    assert np.allclose(streams["rms"], np.sqrt(streams["mean_square"]), atol=1e-12)


def test_standard_deviation_of_a_constant_settles_to_zero_without_nan(backend):
    """`mean_square - mean*mean` on a steady signal cancels to a few float
    epsilons either side of zero. Rooting a negative epsilon unclamped puts
    NaN into the feedback path and silences the session; clamping gives 0."""
    streams = _run(RMS, value=3.0, n=600, backend=backend)
    sd = streams["sd"]
    assert not np.isnan(sd).any(), "a NaN standard deviation reached the stream"
    assert abs(float(sd[-1])) < 1e-6, float(sd[-1])


def test_a_negative_input_is_clamped_rather_than_rooted(backend):
    streams = _run(NEGATIVE, value=2.0, n=100, backend=backend)
    assert float(streams["negative"][-1]) == -12.0
    assert not np.isnan(streams["root"]).any()
    assert np.all(streams["root"] == 0.0)
