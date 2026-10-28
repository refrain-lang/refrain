# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""A forgiven dip behaves the same on both engines (SPEC §5.6).

`tolerance` is per-sample carried state, and the two engines carry it in two
separately written loops — `DwellImpl.step` in `src/refrain/primitive_impls.py`
and `Dwell`/`DwellRearm` in `refrain-core/src/dsp.rs`. These cases pin the
answers an author can actually observe: when a reward lands, whether gated
audio keeps sounding through the dip, and how many cues one burst earns.

The file name matches `tests/test_eval_*.py` on purpose: that glob is what the
dual-backend drift gate runs under `REFRAIN_EVAL_BACKEND=rust`.
"""

from __future__ import annotations

import numpy as np

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

# 200 Hz makes every duration below a round number of samples: the 50 ms
# `duration` is 10 samples, the 15 ms `tolerance` and `rearm` are 3 each.
RATE = 200
DWELL_SAMPLES = 10
TOLERANCE = "15 ms"
TOLERANCE_SAMPLES = 3

# `rectify()` over a passthrough montage is the identity for the positive
# levels below, so `above("level", "t")` is exactly the condition each case
# names — no filter settling stands between the input and the hold.
#
# `cue` is the re-arming form and `latch` the held-state form, so one run
# exercises all three hold primitives. `rearm` is deliberately as short as
# `tolerance`: that is the only configuration in which an engine that counted
# a forgiven dip toward re-arming would hand out a second cue, and the whole
# point of `cue` here is that it does not.
PROTOCOL = '''
protocol "forgiving hold" {
  meta { version = "1.0"; evidence = "experimental"; description = "dip tolerance" }
  requires { sample_rate = ">= 200 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "level" { from = "raw"; pipeline = [rectify()] }
  threshold "t" { signal = "level"; type = absolute(0.5 uV) }
  reward {
    event = dwell(condition: above("level", "t"), duration: 50 msTOL)
    continuous = sigmoid("level" / "t", midpoint: 1.0, steepness: 3)
  }
  output {
    cue         = dwell_rearm(condition: above("level", "t"), duration: 50 ms, rearm: 15 msTOL)
    latch       = hold_for(condition: above("level", "t"), duration: 50 msTOL)
    audio_chime = reward.event
    audio_gain  = reward.event.holds ? reward.continuous : 0
  }
}
'''


def _ir(tolerance: str | None):
    """`tolerance=None` is the protocol with no `tolerance` argument at all."""
    written = "" if tolerance is None else f", tolerance: {tolerance}"
    return resolve(parse(PROTOCOL.replace("TOL", written)))


def _condition(*runs: int, start: bool = True) -> np.ndarray:
    """`_condition(9, 3, 9)` is 9 true samples, then 3 false, then 9 true."""
    out: list[bool] = []
    on = start
    for length in runs:
        out.extend([on] * length)
        on = not on
    return np.asarray(out, dtype=bool)


def _run(
    backend: str,
    condition: np.ndarray,
    *,
    chunk: int = 64,
    tolerance: str | None = TOLERANCE,
) -> dict[str, np.ndarray]:
    """Feed a condition stream sample by sample; return the whole-run streams.

    A true sample is 1.0 and a false one 0.2 — above and below the 0.5 uV
    threshold, and both non-zero, so `reward.continuous` stays non-zero
    through a dip and a zero in `output/audio_gain` can only be the gate.
    """
    signal = np.where(condition, 1.0, 0.2).reshape(-1, 1)
    evaluator = Evaluator.live(
        _ir(tolerance), sample_rate_hz=RATE, channel_names=("Cz",),
        record_streams=True, backend=backend,
    )
    evaluator.start(skip_warmup=True)
    streams: dict[str, list[float]] = {}
    for start in range(0, len(signal), chunk):
        evaluator.step_chunk(signal[start:start + chunk])
        for key, values in evaluator.last_streams().items():
            streams.setdefault(key, []).extend(np.asarray(values).tolist())
    return {k: np.asarray(v) for k, v in streams.items()}


def _fired_at(stream: np.ndarray) -> list[int]:
    return np.flatnonzero(stream).tolist()


def test_a_dip_at_exactly_tolerance_rewards_on_both_backends(backend):
    """9 above-threshold samples, a 3-sample dip, then 9 more.

    Neither run of 9 reaches the 10-sample `duration` on its own, so a reward
    at all is proof the pre-dip streak survived — and the 10th sample is the
    first one after the dip, index 12.
    """
    streams = _run(backend, _condition(9, TOLERANCE_SAMPLES, 9))
    assert _fired_at(streams["output/audio_chime"]) == [12]
    assert _fired_at(streams["output/latch"]) == list(range(12, 21))


def test_a_dip_one_sample_past_tolerance_rewards_on_neither_backend(backend):
    """The same shape with a 4-sample dip. Nothing is earned: the streak
    resets, and the 9 samples after the dip are one short of the duration."""
    streams = _run(backend, _condition(9, TOLERANCE_SAMPLES + 1, 9))
    assert _fired_at(streams["output/audio_chime"]) == []
    assert _fired_at(streams["output/latch"]) == []


def test_a_forgiven_dip_spanning_a_chunk_boundary_rewards(backend):
    """The dip counter is the new carried state; chunk edges are where carried
    state drifts. The chunk size puts the boundary inside the 3-sample dip
    (samples 9, 10 | 11), and the whole run must come out sample for sample
    the way one chunk does.
    """
    condition = _condition(9, TOLERANCE_SAMPLES, 9)
    straddled = _run(backend, condition, chunk=11)
    one_chunk = _run(backend, condition, chunk=len(condition))
    assert _fired_at(straddled["output/audio_chime"]) == [12]
    for key, values in one_chunk.items():
        assert np.array_equal(straddled[key], values), key


def test_holds_gated_continuous_output_keeps_sounding_through_a_forgiven_dip(backend):
    """The `output { audio_gain = reward.event.holds ? reward.continuous : 0 }`
    pattern. The hold rises at sample 9, the dip is samples 12-14, and gated
    audio must keep sounding across it rather than cutting out three times a
    second. Without the tolerance the same input silences it.
    """
    dip = slice(12, 12 + TOLERANCE_SAMPLES)
    forgiving = _run(backend, _condition(12, TOLERANCE_SAMPLES, 6))
    strict = _run(backend, _condition(12, TOLERANCE_SAMPLES, 6), tolerance=None)

    assert forgiving["reward.event.holds"][dip].all()
    assert (forgiving["output/audio_gain"][dip] > 0.0).all()
    # The gate is open, so the gated channel is the continuous value itself.
    assert np.array_equal(
        forgiving["output/audio_gain"][dip], forgiving["reward.continuous"][dip]
    )
    # Not a protocol that simply never gates: the strict run cuts out here,
    # and its continuous value over the dip is the same non-zero number.
    assert not strict["reward.event.holds"][dip].any()
    assert (strict["output/audio_gain"][dip] == 0.0).all()
    assert (strict["reward.continuous"][dip] > 0.0).all()


def test_dwell_rearm_emits_one_event_per_burst_across_a_forgiven_dip(backend):
    """Two bursts separated by a real 15-sample break earn two cues, and the
    forgiven 3-sample dip inside the first burst earns nothing extra.

    `rearm` is 3 samples here, the same as `tolerance`, so an engine that let
    a forgiven dip count toward re-arming would re-arm during the dip and fire
    a third cue when the condition came back at sample 15.
    """
    condition = _condition(12, TOLERANCE_SAMPLES, 6, 15, 10)
    assert _fired_at(_run(backend, condition)["output/cue"]) == [9, 45]
    # One cue per burst is what the primitive promises with or without a
    # tolerance; the tolerance must not change the count.
    assert _fired_at(_run(backend, condition, tolerance=None)["output/cue"]) == [9, 45]


def test_tolerance_zero_is_sample_identical_to_no_tolerance(backend):
    """The "zero is identity" invariant, at the engine level rather than the
    state machine's: `tolerance: 0 ms` bakes to 0 samples and must produce the
    same streams as a protocol that never mentions `tolerance`. The input
    carries a dip of each kind, and a small chunk size so carried state is
    re-entered 15 times rather than once.
    """
    condition = _condition(9, 3, 9, 4, 12)
    zero = _run(backend, condition, chunk=7, tolerance="0 ms")
    absent = _run(backend, condition, chunk=7, tolerance=None)
    assert set(zero) == set(absent)
    for key, values in absent.items():
        assert np.array_equal(zero[key], values), key
    # And the shared answer is the un-forgiving one: neither 9-sample run
    # earns the reward, only the 12-sample run that follows the second dip.
    assert _fired_at(zero["output/audio_chime"]) == [34]
