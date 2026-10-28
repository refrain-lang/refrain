# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The reward clock pauses while an artifact gate is blind — design §evidence.

`reward { event = dwell(...) }` is the path 44 of the 45 live protocols drive
feedback from, and until now it applied no mask at all: the dwell's clock ran
straight through a mute, so a trainee who swallowed 100 ms into a good burst
had the rising edge fire inside the blind window, where the output gate ate it,
and `dwell` does not re-arm — so a genuine two-second burst paid out nothing.

The scenario below is that measurement, at the numbers the design quotes: a
250 ms hold at 256 Hz, a 2 s burst starting at 2.699 s, and an artifact-labelled
EMG mute from 2.801 s to 3.652 s. The rising edge moves from 2.945 s (muted,
unheard) to 3.797 s (after the gate clears, heard).

Nothing moves without the `kind = "artifact"` label. An unlabelled inhibit and
a `"trained"` one must leave this path exactly as it is, and that is not the
same answer as the `output`-bound path in `tests/test_eval_inhibit_kind.py`:
there a muted sample has always been folded into the condition as a failure and
so resets the streak, while here no mask has ever been applied and a muted
sample has always been scored on its condition alone. Both halves are pinned
below, because making the two paths agree would be a behaviour change for every
existing protocol with nothing declared.

The file also covers the two places the mask has to reach that the headline
measurement does not touch: a reward bundle that is stepped every chunk while
its block is not live, and a `hold_for` nested inside a reward condition, which
must get no mask at all.

The file name matches `tests/test_eval_*.py` on purpose: that glob is what the
dual-backend drift gate runs under `REFRAIN_EVAL_BACKEND=rust`.
"""

from __future__ import annotations

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve
from tests._inhibit_fixtures import (  # verified one-inhibit fixtures
    NESTED_HOLD_CONDITION,
    reward_event_protocol,
)

# 256 Hz makes every duration in the fixture a whole number of samples, so no
# rounding stands between the written source and an asserted sample index: the
# 250 ms hold is 64 samples and the gate's 125 ms release is 32.
RATE = 256
DWELL_SAMPLES = 64          # 250 ms
RELEASE_SAMPLES = 32        # 125 ms
NESTED_HOLD_SAMPLES = 32    # 125 ms, the hold nested in the reward condition

# The design's own figures, in samples. 691/256 = 2.699 s, 717/256 = 2.801 s.
BURST_START = 691
BURST_SAMPLES = 512         # 2 s
MUTE_START = 717            # 26 samples (102 ms) into the burst
EMG_SAMPLES = 186
BANKED = MUTE_START - BURST_START                              # 26
MUTE_END = MUTE_START + EMG_SAMPLES + RELEASE_SAMPLES - 1      # 934, 3.648 s
RESUME = MUTE_END + 1                                          # 935, 3.652 s
BURST_END = BURST_START + BURST_SAMPLES                        # 1203
N_SAMPLES = 1280

# Today's answer: the clock ran through the blind window, so the edge lands
# 64 samples after the burst opened — inside the mute, where it is eaten.
RAW_ANSWER = BURST_START + DWELL_SAMPLES - 1                   # 754, 2.945 s
# Under the pause rule: 26 samples banked before the gate shut, 38 left to
# earn once it clears.
PAUSED_ANSWER = RESUME + (DWELL_SAMPLES - BANKED) - 1          # 972, 3.797 s
# With a 125 ms hold nested in the reward condition, the nested hold is
# unmasked (it is not on the output path) so it is already holding when the
# gate clears; the outer dwell then needs all 64 of its samples from there.
NESTED_ANSWER = RESUME + DWELL_SAMPLES - 1                     # 998, 3.898 s


def _run(
    backend: str,
    *,
    kind: str | None,
    condition: str = 'above("level", "t")',
    mode: str = "plain",
    artifact: bool = True,
    chunk: int = 64,
) -> tuple[dict[str, np.ndarray], list[tuple[int, int, dict[str, float]]]]:
    """Feed the swallow signal once; return the per-sample streams and a
    `(chunk_start, chunk_stop, last_taps())` triple for each chunk.

    A qualifying sample writes 1.0 into the trained column and a failing one
    0.2 — both above zero, so a 0.0 in `output/gain` can only be the output
    gate and never the signal. The muscle column is 1.0 for the length of the
    artifact and 0.0 otherwise; `artifact=False` leaves it silent throughout,
    which is the control run for "this mute changed nothing".
    """
    src = reward_event_protocol(kind=kind, condition=condition, mode=mode)
    ir = resolve(parse(src))
    level = np.full(N_SAMPLES, 0.2)
    level[BURST_START:BURST_END] = 1.0
    emg = np.zeros(N_SAMPLES)
    if artifact:
        emg[MUTE_START:MUTE_START + EMG_SAMPLES] = 1.0
    signal = np.column_stack((level, emg))

    evaluator = Evaluator.live(
        ir, sample_rate_hz=RATE, channel_names=("Cz", "A1"),
        record_streams=True, backend=backend,
    )
    evaluator.start(skip_warmup=True)
    streams: dict[str, list[float]] = {}
    taps: list[tuple[int, int, dict[str, float]]] = []
    for start in range(0, N_SAMPLES, chunk):
        evaluator.step_chunk(signal[start:start + chunk])
        for key, values in evaluator.last_streams().items():
            streams.setdefault(key, []).extend(np.asarray(values).tolist())
        taps.append((start, min(start + chunk, N_SAMPLES), evaluator.last_taps()))
    return {k: np.asarray(v) for k, v in streams.items()}, taps


def _at(sample: int | None) -> float | None:
    """A sample index as the time the evaluator would stamp an event with."""
    return None if sample is None else sample / RATE


def _first(stream: np.ndarray) -> int | None:
    hits = np.flatnonzero(stream)
    return int(hits[0]) if hits.size else None


def _first_raw_event_time(streams: dict[str, np.ndarray]) -> float | None:
    """When the reward dwell's rising edge fired, before the output gate."""
    return _at(_first(streams["reward.event"]))


def _first_delivered_event_time(streams: dict[str, np.ndarray]) -> float | None:
    """When the trainee actually heard one, i.e. after `& ~muted`."""
    return _at(_first(streams["output/chime"]))


def _muted_span(streams: dict[str, np.ndarray]) -> list[int]:
    """The samples the output gate silenced, read off the constant channel."""
    return np.flatnonzero(streams["output/gain"] == 0.0).tolist()


def _holds_span(streams: dict[str, np.ndarray]) -> list[int]:
    return np.flatnonzero(streams["reward.event.holds"]).tolist()


# ---------------------------------------------------------------------------
# The headline case.
# ---------------------------------------------------------------------------


def test_a_swallow_inside_a_genuine_burst_still_earns_its_one_reward(backend):
    """Design §evidence. Today the rising edge fires at 2.945 s inside the
    blind window and the trainee hears nothing; under the pause rule it fires
    at 3.797 s, after the mute clears, and they hear it."""
    streams, _ = _run(backend, kind="artifact")
    # Witness the gate: without this a pass means nothing, because a run where
    # the mute never fired would land on the same answer as a working mask.
    assert _muted_span(streams) == list(range(MUTE_START, MUTE_END + 1))
    fired_at = _first_delivered_event_time(streams)
    assert fired_at == pytest.approx(3.797, abs=1 / RATE)
    assert fired_at == _at(PAUSED_ANSWER)
    # One reward for one burst, and it is the same event either side of the
    # gate: `dwell` does not re-arm, so a swallowed edge is not re-paid later.
    assert _first_raw_event_time(streams) == fired_at
    assert int(streams["reward.event"].sum()) == 1


def test_todays_answer_is_a_burst_the_trainee_never_hears(backend):
    """The other half of the same measurement, and the reason the change is
    worth making: unlabelled, the edge fires at 2.945 s with the gate shut, the
    output stage eats it, and two seconds of genuine work pay out nothing at
    all for the rest of the burst."""
    streams, _ = _run(backend, kind=None)
    assert _first_raw_event_time(streams) == pytest.approx(2.945, abs=1 / RATE)
    assert _first_delivered_event_time(streams) is None
    assert int(streams["output/chime"].sum()) == 0


# ---------------------------------------------------------------------------
# Absence reproduces today, sample for sample.
# ---------------------------------------------------------------------------


def test_an_unlabelled_mute_leaves_the_reward_event_path_exactly_as_today(backend):
    """Today this path applies NO mask at all. Absence must reproduce that,
    not the output path's reset — the two differ, and goal 5 forbids moving
    either without a declaration present."""
    streams, _ = _run(backend, kind=None)
    assert _first_raw_event_time(streams) == pytest.approx(2.945, abs=1 / RATE)
    assert _first_raw_event_time(streams) == _at(RAW_ANSWER)
    # Not the output path's answer. There a muted sample folds into the
    # condition as a failure and zeroes the streak, which would put the edge
    # 64 samples after the gate cleared instead of 64 after the burst opened.
    assert _at(RAW_ANSWER) != _at(RESUME + DWELL_SAMPLES - 1)
    assert _holds_span(streams) == list(range(RAW_ANSWER, BURST_END))


def test_a_trained_mute_leaves_the_reward_event_path_exactly_as_today(backend):
    """`kind = "trained"` is a declaration that the inhibit watches something
    the protocol trains against — drifting toward sleep is scored, not
    forgiven. So it must read identically to no label at all, stream for
    stream, not merely land on the same event time."""
    trained, _ = _run(backend, kind="trained")
    unlabelled, _ = _run(backend, kind=None)
    assert _first_raw_event_time(trained) == _at(RAW_ANSWER)
    assert sorted(trained) == sorted(unlabelled)
    for key, values in trained.items():
        assert np.array_equal(values, unlabelled[key]), key


# ---------------------------------------------------------------------------
# Block scoping: an inhibit that gates nothing makes nothing unobservable.
# ---------------------------------------------------------------------------


def test_a_block_scoped_artifact_inhibit_only_masks_inside_its_block(backend):
    """An inhibit outside the active block's `inhibit` set does not gate
    output, so it must not make the reward clock unobservable either.

    The label is on `"emg"` and `"emg"` fires over the same 186 samples as
    every other case here; the block names only `"idle"`, which never fires.
    So the answer has to be today's — and, because nothing is muted, the edge
    is delivered this time rather than eaten.
    """
    streams, _ = _run(backend, kind="artifact", mode="block_scoped")
    assert _muted_span(streams) == []
    assert _first_raw_event_time(streams) == _at(RAW_ANSWER)
    assert _first_delivered_event_time(streams) == _at(RAW_ANSWER)
    # And the mask really is the thing being scoped out: the same label with
    # no block around it moves the edge by 218 samples.
    unscoped, _ = _run(backend, kind="artifact")
    assert _first_raw_event_time(unscoped) == _at(PAUSED_ANSWER)


# ---------------------------------------------------------------------------
# Reward bundles. Every bundle is stepped on every chunk so its dwell timer
# stays warm, and the mask reaches all of them, live or not — an artifact is an
# artifact whichever block is running.
# ---------------------------------------------------------------------------

PHASE_BOUNDARY = 896        # 3.5 s, inside the mute


def test_a_bundle_that_is_not_live_yet_still_has_its_clock_paused(backend):
    """A warm bundle's clock must pause too, or it comes out of the blind
    window already holding.

    The `"warm"` bundle becomes live at 3.5 s, inside the mute and between the
    two answers: a clock that ran through the window was already holding when
    the switch happened, so the trainee gets a reward state they never saw
    earned, and the rising edge that produced it fired out of sight — so no
    chime is ever delivered for the burst at all. A paused clock is still
    below threshold at the switch, rises at 972 where the host can see it, and
    the chime lands.
    """
    paused, _ = _run(backend, kind="artifact", mode="staged")
    assert _muted_span(paused) == list(range(MUTE_START, MUTE_END + 1))
    assert _holds_span(paused) == list(range(PAUSED_ANSWER, BURST_END))
    assert _first_raw_event_time(paused) == _at(PAUSED_ANSWER)
    assert _first_delivered_event_time(paused) == _at(PAUSED_ANSWER)

    # Unlabelled, on the same session: already holding at the switch, and the
    # edge is gone for good.
    blind, _ = _run(backend, kind=None, mode="staged")
    assert _holds_span(blind) == list(range(PHASE_BOUNDARY, BURST_END))
    assert _first_raw_event_time(blind) is None
    assert _first_delivered_event_time(blind) is None
    # The switch is what makes the warm bundle's state readable, so it has to
    # land between the two answers or this case sees nothing.
    assert RAW_ANSWER < PHASE_BOUNDARY < PAUSED_ANSWER


def test_a_trained_bundle_mute_leaves_the_warm_clock_exactly_as_today(backend):
    """`"trained"` reads as no label on this path, bundles included."""
    trained, _ = _run(backend, kind="trained", mode="staged")
    unlabelled, _ = _run(backend, kind=None, mode="staged")
    assert sorted(trained) == sorted(unlabelled)
    for key, values in trained.items():
        assert np.array_equal(values, unlabelled[key]), key


# ---------------------------------------------------------------------------
# A hold nested inside the reward condition. Python used to leave the output
# loop's two masks on `self` after the loop ended, so such a hold read the
# PREVIOUS chunk's masks — while Rust removes both env keys at that point and
# applied none. A live divergence, plus a hard shape error the first time a
# chunk arrived shorter than the one before it.
# ---------------------------------------------------------------------------


def test_a_hold_nested_in_a_reward_condition_ignores_an_unlabelled_mute_entirely(backend):
    """The two output masks belong to the output loop and must not outlive it.

    With no label, nothing on the reward path is masked at all, so an
    unlabelled mute has to leave the whole reward stage identical to a run
    where the muscle column stayed silent and no mute ever happened. The two
    runs below differ in exactly that: one mutes over 218 samples, the other
    over none, and `reward.event` and `reward.event.holds` must still match
    sample for sample.

    Left on `self` after the output loop, the eligibility mask reached this
    nested hold a chunk late and zeroed its streak on samples nothing was
    wrong with, which is precisely the difference these two runs would then
    show. Rust applies no mask here, so Python applying a stale one was a live
    divergence as well as wrong on its own terms.
    """
    muted_run, _ = _run(backend, kind=None, condition=NESTED_HOLD_CONDITION)
    clean_run, _ = _run(
        backend, kind=None, condition=NESTED_HOLD_CONDITION, artifact=False
    )
    assert _muted_span(muted_run) == list(range(MUTE_START, MUTE_END + 1))
    assert _muted_span(clean_run) == []
    assert _first_raw_event_time(muted_run) == _first_raw_event_time(clean_run)
    assert _first_raw_event_time(muted_run) is not None
    for key in ("reward.event", "reward.event.holds", "reward/condition[0]"):
        if key in muted_run:
            assert np.array_equal(muted_run[key], clean_run[key]), key


def test_a_hold_nested_in_a_reward_condition_is_not_frozen_by_the_artifact_mask(backend):
    """The mask is the outer dwell's, not the nested hold's.

    The nested `hold_for` is evaluated as part of the reward condition, before
    any output channel runs, so it gets neither the eligibility mask nor the
    observability mask — exactly as Rust does. It is therefore already holding
    when the outer dwell's gate clears, and the outer dwell needs all 64 of
    its samples from there: 998, not 972.
    """
    streams, _ = _run(backend, kind="artifact", condition=NESTED_HOLD_CONDITION)
    assert _muted_span(streams) == list(range(MUTE_START, MUTE_END + 1))
    assert _first_raw_event_time(streams) == _at(NESTED_ANSWER)
    assert _first_delivered_event_time(streams) == _at(NESTED_ANSWER)
    # The nested hold being unmasked is what puts the answer here rather than
    # at the top-level condition's 972 — three distinct answers, so the test
    # can tell a frozen nested hold from an unmasked one.
    assert NESTED_ANSWER != PAUSED_ANSWER != RAW_ANSWER
    # And the question is live: the nested hold's own 32 samples complete
    # INSIDE the blind window, so whether it is masked decides whether it is
    # holding at all when the gate clears.
    assert MUTE_START <= BURST_START + NESTED_HOLD_SAMPLES - 1 <= MUTE_END


def test_a_hold_nested_in_a_reward_condition_survives_a_changing_chunk_size(backend):
    """A mask left on `self` after the output loop is the previous chunk's, so
    it is also the previous chunk's LENGTH. 1280 samples in chunks of 100 ends
    on a chunk of 80, which used to raise a broadcast error outright.

    Chunk size is not a parameter of this protocol — every primitive in it is
    per-sample — so the two runs must agree on every stream, not merely both
    finish.
    """
    uneven, _ = _run(
        backend, kind="artifact", condition=NESTED_HOLD_CONDITION, chunk=100
    )
    whole, _ = _run(
        backend, kind="artifact", condition=NESTED_HOLD_CONDITION, chunk=64
    )
    assert _first_raw_event_time(uneven) == _at(NESTED_ANSWER)
    assert sorted(uneven) == sorted(whole)
    for key, values in uneven.items():
        assert np.array_equal(values, whole[key]), key


# ---------------------------------------------------------------------------
# Taps.
# ---------------------------------------------------------------------------


def test_the_reward_event_holds_tap_agrees_across_backends(backend):
    """`reward/event.holds` is the last sample of the chunk, and the paused
    clock moves when it first reads true. Both engines must put the rise at
    972 and the fall at the end of the burst, and the tap must be the stream's
    last sample on every chunk — a tap read off a separately-kept copy of the
    held state is how the two drift apart without any stream moving.
    """
    streams, taps = _run(backend, kind="artifact")
    assert _holds_span(streams) == list(range(PAUSED_ANSWER, BURST_END))
    holds = streams["reward.event.holds"]
    events = streams["reward.event"]
    for start, stop, snapshot in taps:
        assert bool(snapshot["reward/event.holds"]) == bool(holds[stop - 1]), stop
        # `reward/event` is "did the dwell fire anywhere in this chunk", not a
        # last sample, so the one chunk holding sample 972 is the only true one.
        assert bool(snapshot["reward/event"]) == bool(events[start:stop].any()), stop
    firing = [stop for start, stop, snap in taps if snap["reward/event"]]
    assert len(firing) == 1 and firing[0] > PAUSED_ANSWER >= firing[0] - 64


def test_the_muted_tap_keyset_is_unchanged(backend):
    """No tap is added by this release; the exact-equality keyset parity test
    must still pass. `artifact_muted` is an internal mask, not a `muted`
    sibling — a host that wanted it would be reading a key no 0.24 engine has.
    """
    _, taps = _run(backend, kind="artifact")
    snapshot = taps[-1][2]
    assert set(snapshot) == {
        "input/trained", "input/muscle",
        "derive/level", "derive/emg_level",
        "threshold/t",
        "inhibit/emg", "muted",
        "reward/condition[0]", "reward/event", "reward/event.holds",
        "output/chime", "output/gain",
        "phase/index", "phase/output_muted",
    }
    # Labelled and unlabelled expose the same keys, so a host cannot tell from
    # the tap API that anything was declared.
    _, plain_taps = _run(backend, kind=None)
    assert set(plain_taps[-1][2]) == set(snapshot)
