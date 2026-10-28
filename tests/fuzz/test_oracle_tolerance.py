# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The fuzzer's model of a forgiving hold (`dwell(..., tolerance: ...)`).

Governing rule, same as the engine's: forgive, don't credit. A streak of N
samples always means N samples genuinely above threshold; a forgiven dip
leaves the streak where it was and never advances it.

The first group pins `apply_dwell` on hand-built truth sequences. The second
group states the four invariants of the design as relations over a sweep, so
they hold for streams nobody wrote by hand. Every relation is built from
numbers where the right and the wrong behaviour give different answers: the
docstring on each names the specific bug it exists to catch.
"""
from __future__ import annotations

import random
from dataclasses import replace

import numpy as np

from refrain.eval_ import Evaluator
from refrain.fuzz.oracle import _tolerance_samples, apply_dwell
from refrain.fuzz.surface import build_surface
from refrain.parser import parse
from refrain.primitive_impls import DwellImpl
from refrain.resolver import resolve
from tests.fuzz._smr import resolved_smr_ir

T, F = True, False
DWELL = 10


def _truth(*runs, start=True):
    """`_truth(9, 3, 9)` is 9 True, 3 False, 9 True. Use None-runs via tuples."""
    out, on = [], start
    for length in runs:
        out.extend([on] * length)
        on = not on
    return out


def _fires(truth, *, tolerance=0, dwell=DWELL, collar_s=0.0, fs=200):
    return apply_dwell(
        truth, dwell_samples=dwell, fs=fs, collar_s=collar_s,
        muted_mask=[False] * len(truth), tolerance_samples=tolerance,
    ).should_fire_event_samples


# --------------------------------------------------------------- pinned cases

def test_zero_tolerance_predicts_exactly_what_it_predicted_before():
    truth = _truth(9, 1, 12, 3, 4, 2, 15)
    no_arg = apply_dwell(truth, dwell_samples=DWELL, fs=200, collar_s=0.0,
                         muted_mask=[False] * len(truth))
    zero = apply_dwell(truth, dwell_samples=DWELL, fs=200, collar_s=0.0,
                       muted_mask=[False] * len(truth), tolerance_samples=0)
    assert no_arg.should_fire_event_samples == zero.should_fire_event_samples
    assert zero.should_fire_event_samples == [19, 40]   # 1-sample dip is NOT forgiven


def test_a_sub_tolerance_false_run_is_forgiven_in_the_prediction():
    # 9 true, 3 false, 9 true. Neither run reaches 10 alone, so a fire at all
    # proves the streak survived; the 10th genuine sample is index 12.
    truth = _truth(9, 3, 9)
    assert _fires(truth, tolerance=3) == [12]
    assert _fires(truth, tolerance=2) == []        # dip one past tolerance


def test_a_forgiven_dip_is_not_credited():
    # 8 true, 2 false (forgiven), 9 true. Crediting the dip would reach 10 at
    # index 9, inside the dip. Forgiving it needs 2 more true samples: 10, 11.
    assert _fires(_truth(8, 2, 9), tolerance=3) == [11]


def test_holding_through_a_dip_does_not_fire_a_second_time():
    # Streak sits at exactly DWELL while the dip is forgiven; only the first
    # arrival at DWELL is a rising edge.
    assert _fires(_truth(10, 2, 5), tolerance=3) == [9]


def test_a_none_sample_inside_a_dip_stays_dont_care_rather_than_forgiven():
    """A 3-valued None is "the oracle cannot tell", which is not the same as
    "the signal dipped". Forgiveness applies to robust-False runs only."""
    dipped = [T] * 6 + [F] * 2 + [T] * 6
    unknown = [T] * 6 + [None] * 2 + [T] * 6
    assert _fires(dipped, tolerance=3) == [11]
    assert _fires(unknown, tolerance=3) == []      # asserts nothing it can't know


def test_tolerance_samples_is_rounded_and_never_floored_at_one():
    assert _tolerance_samples(100.0, 256) == 26
    assert _tolerance_samples(0.0, 256) == 0
    # 1 ms at 256 Hz is 0.256 samples: rounds to zero, and stays zero.
    assert _tolerance_samples(1.0, 256) == 0


# ------------------------------------------------------------ surface carries it

def _smr_with_tolerance(text_tolerance):
    from tests.fuzz._smr import SMR_PROTOCOL
    src = SMR_PROTOCOL.read_text()
    anchor = "duration: 250 ms"
    assert src.count(anchor) == 1
    added = "" if text_tolerance is None else f",\n      tolerance: {text_tolerance}"
    return resolve(parse(src.replace(anchor, anchor + added)))


def test_surface_reads_the_reward_events_tolerance():
    assert build_surface(_smr_with_tolerance("100 ms")).dwell_tolerance_ms == 100.0


def test_surface_without_a_tolerance_argument_is_zero():
    assert build_surface(resolved_smr_ir()).dwell_tolerance_ms == 0.0
    assert build_surface(_smr_with_tolerance("0 ms")).dwell_tolerance_ms == 0.0


def test_surface_tolerance_reaches_apply_dwell_as_whole_samples(monkeypatch):
    """`predict` bakes the surface's milliseconds into samples at the
    surface's own rate, the way the evaluator does."""
    import refrain.fuzz.oracle as oracle
    from refrain.fuzz.scenario import PhaseOverride, Scenario

    seen = {}
    real = oracle.apply_dwell

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(oracle, "apply_dwell", spy)
    base = build_surface(resolved_smr_ir())
    scenario = Scenario(
        label="quiet", duration_s=4.0, sample_rate_hz=base.sample_rate_hz,
        segments=(), controls={}, coverage_tags=frozenset(),
        phase_override=PhaseOverride(warmup_s=1.0, training_s=2.5, cooldown_s=0.5),
    )
    oracle.predict(scenario, replace(base, dwell_tolerance_ms=100.0))
    assert seen["tolerance_samples"] == round(0.1 * base.sample_rate_hz)
    oracle.predict(scenario, base)
    assert seen["tolerance_samples"] == 0


# ---------------------------------------------------- the four design invariants
#
# Each relation runs over a fixed adversarial stream plus a seeded random
# sweep. The fixed stream is chosen so the specific bug named in the docstring
# produces a visibly different answer, not merely a different internal state.

TOLERANCES = (0, 1, 2, 3, 5, 8)


def _random_streams(count=60):
    for seed in range(count):
        rng = random.Random(seed)
        runs = [rng.randint(1, 14) for _ in range(rng.randint(3, 12))]
        yield _truth(*runs, start=bool(seed % 2))


# Dips of 2, 3 and 4 samples between runs of 9: every tolerance in the sweep
# sees a different mix of forgiven and unforgiven dips.
LADDER = _truth(9, 2, 9, 3, 9, 4, 9, 6, 12)


def _first(fires):
    return fires[0] if fires else float("inf")


def test_monotone_in_tolerance():
    """Raising tolerance moves a reward earlier or leaves it; never later,
    never away.

    Catches a forgiveness test written as `dip == tolerance` rather than
    `dip <= tolerance`: on `9T 2F 9T` a tolerance of 2 would reward and a
    tolerance of 3 would not."""
    for truth in [LADDER, _truth(9, 2, 9), *_random_streams()]:
        times = [_first(_fires(truth, tolerance=t)) for t in TOLERANCES]
        assert all(b <= a for a, b in zip(times, times[1:])), (truth, times)
    # The sweep is not vacuous: the ladder really does reward earlier as
    # tolerance grows, and does not reward at all with none.
    ladder = [_first(_fires(LADDER, tolerance=t)) for t in TOLERANCES]
    assert ladder[0] == 60 and ladder[-1] == 11


def _genuine_samples_behind(truth, fire, tolerance):
    """True samples in the unbroken hold that ends at `fire`: walk back to the
    nearest False run longer than `tolerance` and count what is above
    threshold after it. Written from the definition, not from the loop under
    test."""
    start, i = 0, fire
    while i >= 0:
        if truth[i] is False:
            j = i
            while j >= 0 and truth[j] is False:
                j -= 1
            if i - j > tolerance:
                start = i + 1
                break
            i = j
        else:
            i -= 1
    return sum(1 for x in truth[start:fire + 1] if x is True)


def test_no_free_rewards():
    """Every reward has exactly `duration` samples genuinely above threshold
    behind it, however many dips were forgiven on the way.

    Catches crediting a forgiven dip toward the streak: on `8T 2F 9T` a
    crediting oracle rewards at index 9 with only 8 true samples behind it."""
    seen = 0
    for truth in [LADDER, _truth(8, 2, 9), _truth(8, 3, 3, 3, 9), *_random_streams()]:
        for tol in TOLERANCES:
            for f in _fires(truth, tolerance=tol):
                assert _genuine_samples_behind(truth, f, tol) == DWELL, (truth, tol, f)
                seen += 1
    assert seen > 50   # the loop checked something


def _fill(truth, tolerance):
    """Set every False run no longer than `tolerance` that sits between two
    True samples to True - the dip-filling a person might do by hand."""
    out, i, n = list(truth), 0, len(truth)
    while i < n:
        if truth[i] is False:
            j = i
            while j < n and truth[j] is False:
                j += 1
            if 0 < i and j < n and j - i <= tolerance:
                out[i:j] = [True] * (j - i)
            i = j
        else:
            i += 1
    return out


def _stitch(truth, tolerance):
    """The same stream with those dips cut out, plus where each surviving
    sample came from. Forgive-don't-credit says the held stream is this one."""
    kept, origin, i, n = [], [], 0, len(truth)
    while i < n:
        if truth[i] is False:
            j = i
            while j < n and truth[j] is False:
                j += 1
            if not (0 < i and j < n and j - i <= tolerance):
                kept.extend(truth[i:j])
                origin.extend(range(i, j))
            i = j
        else:
            kept.append(truth[i])
            origin.append(i)
            i += 1
    return kept, origin


def test_idempotent_under_dip_filling():
    """Once the dips are filled by hand there is nothing left to forgive, so
    the tolerance stops mattering; and a forgiving stream is the stitched
    stream, not the filled one.

    The literal "filled stream rewards when the original does" is NOT the
    rule: filling credits the dip, forgiving does not. On `8T 2F 9T` the
    filled stream rewards at index 9 and the forgiving one at 11 - exactly the
    two forgiven samples later. Stitching the dip out gives the right answer.
    Catches crediting (stitch relation fails) and tolerance applied to a
    stream with no dips left in it (idempotence fails)."""
    for truth in [LADDER, _truth(8, 2, 9), *_random_streams()]:
        for tol in TOLERANCES:
            filled = _fill(truth, tol)
            assert _fires(filled, tolerance=tol) == _fires(filled, tolerance=0), (truth, tol)
            stitched, origin = _stitch(truth, tol)
            assert _fires(truth, tolerance=tol) == \
                [origin[f] for f in _fires(stitched, tolerance=0)], (truth, tol)
    assert _fires(_fill(_truth(8, 2, 9), 3), tolerance=0) == [9]
    assert _fires(_truth(8, 2, 9), tolerance=3) == [11]


# ----------------------------------------------- the engine, sample for sample

RATE = 200   # 50 ms duration is 10 samples; 10 ms is 2 samples.
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
  output { audio_chime = reward.event
           audio_gain = reward.event.holds ? reward.continuous : 0 }
}
'''


def _engine(truth, tolerance):
    written = "" if tolerance is None else f", tolerance: {tolerance}"
    ir = resolve(parse(PROTOCOL.replace("TOL", written)))
    signal = np.where(np.asarray(truth, dtype=bool), 1.0, 0.2).reshape(-1, 1)
    ev = Evaluator.live(ir, sample_rate_hz=RATE, channel_names=("Cz",),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    streams = {}
    for s in range(0, len(signal), 64):
        ev.step_chunk(signal[s:s + 64])
        for k, v in ev.last_streams().items():
            streams.setdefault(k, []).extend(np.asarray(v).tolist())
    return {k: np.asarray(v) for k, v in streams.items()}


def test_zero_is_identity_end_to_end():
    """`tolerance: 0 ms` and no tolerance at all give sample-identical engine
    output - and that output is the unforgiving one, not merely the same one.

    Catches a zero tolerance floored to one sample (as `dwell_samples` is):
    both runs would agree with each other, so they are also compared against
    the answer worked out by hand. The 1-sample dip in `9T 1F 9T` is the
    discriminator - floored, it rewards at index 10; unfloored, it never does."""
    truth = _truth(9, 1, 9, 3, 9, 2, 12)
    explicit = _engine(truth, "0 ms")
    absent = _engine(truth, None)
    assert explicit.keys() == absent.keys()
    for key in explicit:
        assert np.array_equal(explicit[key], absent[key]), key
    # By hand: every dip resets, so only the final 12-run rewards, at its 10th
    # sample: 9+1+9+3+9+2 = 33 samples precede it, so index 42.
    assert np.flatnonzero(absent["output/audio_chime"]).tolist() == [42]
    # And the same stream is sensitive to a real tolerance (2 samples).
    assert not np.array_equal(
        _engine(truth, "10 ms")["output/audio_chime"], absent["output/audio_chime"])


def test_oracle_agrees_with_the_engine_on_every_swept_stream():
    """The oracle's loop and `DwellImpl.step` are separate transcriptions of
    one rule. This holds them to each other over the whole sweep."""
    for truth in [LADDER, *_random_streams()]:
        for tol in TOLERANCES:
            impl = DwellImpl(duration_ms=1000.0 * DWELL / RATE,
                             sample_rate_hz=RATE,
                             tolerance_ms=1000.0 * tol / RATE)
            events = impl.step(np.asarray(truth, dtype=bool)).events
            assert _fires(truth, tolerance=tol) == np.flatnonzero(events).tolist(), \
                (truth, tol)
