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
its block is not live, and a `hold_for` nested inside a reward condition.

The nested hold is the one case where the two masks part company. It gets the
observability mask, because "a streak of N samples always means N samples
genuinely above threshold and observable" does not stop applying when the hold
is written inside a reward condition. It does not get the eligibility mask,
because eligibility folds a mute into the condition as a failure and this stage
has never done that — unobservable freezes, it does not fail. Three distinct
sample indices separate the two masks from each other and from no mask at all,
which is what makes those cases able to tell the three apart.

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
from tests.conftest import RUST_BACKEND_ACTIVE

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
# --- The three answers for a 125 ms hold nested in the reward condition -----
#
# A nested hold is reached with ONLY the observability mask: the reward stage
# has never folded a mute into its condition and still does not, so the
# eligibility mask stops at the output loop. That gives three distinct sample
# indices, and keeping them distinct is what lets the cases below tell a frozen
# nested hold from an unmasked one.

# Each answer is "the sample the nested hold first reads true" plus the outer
# dwell's 64, counting the first of those 64 as the rise sample itself.
def _outer_fires_once_nested_holds_at(nested_rise: int) -> int:
    return nested_rise + DWELL_SAMPLES - 1


# Nothing masked. The nested hold banks 32 straight from the burst's opening
# and holds at 722; the outer dwell then takes its 64 from there. This is what
# an unlabelled mute, a `"trained"` one, a block-scoped one and no mute at all
# must all produce.
NESTED_RISE_UNMASKED = BURST_START + NESTED_HOLD_SAMPLES - 1             # 722
NESTED_UNMASKED = _outer_fires_once_nested_holds_at(NESTED_RISE_UNMASKED)  # 785

# Outer dwell masked, nested hold NOT. The nested hold banked straight through
# the blind window and was already holding when the gate cleared, so the outer
# dwell takes all 64 of its samples from 935. This is what the engine did
# before the nested hold was masked: a streak built on samples the engine had
# declared unreadable, which "forgive, don't credit" forbids. It is here as the
# answer that must NOT come back.
NESTED_CREDITED = RESUME + DWELL_SAMPLES - 1                              # 998

# Both masked — the correct answer. The nested hold banks 26 of its 32 before
# the gate shuts, freezes, and needs 6 more once the engine can read again, so
# it holds at 940; the outer dwell takes its 64 from there.
NESTED_RISE_FROZEN = RESUME + (NESTED_HOLD_SAMPLES - BANKED) - 1         # 940
NESTED_FROZEN = _outer_fires_once_nested_holds_at(NESTED_RISE_FROZEN)      # 1003

# What the eligibility mask would have cost, had it been allowed to cross to
# this path: a mute zeroes a streak rather than freezing it, so the nested hold
# would lose the 26 samples it banked and need all 32 again from 935.
NESTED_IF_ELIGIBILITY_CROSSED = _outer_fires_once_nested_holds_at(
    RESUME + NESTED_HOLD_SAMPLES - 1                                     # 966
)                                                                          # 1029

# --- A transport pause, with no mute anywhere ------------------------------
#
# A host pause is not an artifact: it does not mean "the engine could not read
# these samples", it means there were no samples. So a streak does not freeze
# across it, it dies — a streak accumulated across unobserved time is stale
# wherever the hold sits, which is "forgive, don't credit" applied to a
# transport gap.
#
# 704 is a chunk boundary 13 samples into the nested hold's 32, so the pause
# lands mid-streak with nothing banked that survives. The hold starts again
# from the first sample after the pause and holds 32 later; the outer dwell
# then takes its 64 from there.
PAUSE_AT = 704
NESTED_RISE_AFTER_PAUSE = PAUSE_AT + NESTED_HOLD_SAMPLES - 1             # 735
NESTED_AFTER_PAUSE = _outer_fires_once_nested_holds_at(
    NESTED_RISE_AFTER_PAUSE
)                                                                          # 798


def _run(
    backend: str,
    *,
    kind: str | None,
    condition: str = 'above("level", "t")',
    mode: str = "plain",
    artifact: bool = True,
    chunk: int = 64,
    pause_at: int | None = None,
    pause_via: str = "clock",
) -> tuple[dict[str, np.ndarray], list[tuple[int, int, dict[str, float]]]]:
    """Feed the swallow signal once; return the per-sample streams and a
    `(chunk_start, chunk_stop, last_taps())` triple for each chunk.

    A qualifying sample writes 1.0 into the trained column and a failing one
    0.2 — both above zero, so a 0.0 in `output/gain` can only be the output
    gate and never the signal. The muscle column is 1.0 for the length of the
    artifact and 0.0 otherwise; `artifact=False` leaves it silent throughout,
    which is the control run for "this mute changed nothing".

    `pause_at` is the sample index of a chunk boundary at which the host
    interrupts the session before that chunk is fed — the transport pause a
    practitioner causes from the console. `pause_via="clock"` does it with
    `set_clock_frozen(True)` then `(False)`, which is what a pause button
    calls; `pause_via="reset"` calls `reset_held_outputs()` directly, which is
    what `docs/PRIMITIVES.md` tells a host to do after an input gap. Both
    reach the same invalidation and both must give the same answer.
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
        if pause_at is not None and start == pause_at:
            if pause_via == "clock":
                evaluator.set_clock_frozen(True)
                evaluator.set_clock_frozen(False)
            else:
                evaluator.reset_held_outputs()
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
# A hold nested inside the reward condition. It gets the observability mask and
# nothing else: a streak must only ever count samples the engine could read, so
# an artifact-gated sample freezes it; but the reward stage has never folded a
# mute into its condition as a failure, and still does not, so the eligibility
# mask stops at the output loop.
#
# Two earlier bugs lived here and both are pinned below. The masks used to be
# left standing on the evaluator after the output loop, so a nested hold read
# the PREVIOUS chunk's masks in Python and none at all in Rust — a live
# divergence, plus a hard shape error the first time a chunk arrived shorter
# than the one before it. Then, once they were cleared, a nested hold got no
# mask at all and banked samples the engine had declared unreadable.
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


def test_a_hold_nested_in_a_reward_condition_freezes_under_an_artifact_mute(backend):
    """A nested hold must not bank samples the engine could not read.

    "A streak of N samples always means N samples genuinely above threshold and
    observable" is unconditional — it does not become optional because the hold
    happens to be written inside a reward condition rather than inside an
    `output` binding. Here the nested hold's own 32 samples would complete at
    722, INSIDE the blind window, on six samples nothing could be read from.
    Frozen, it banks the 26 real ones, waits out the gate, and holds at 940;
    the outer dwell then takes its 64 from there.

    Three distinct answers, which is what makes this case able to tell them
    apart at all:

    * 785 — nothing masked: the nested hold held at 722 and the outer dwell
      ran straight through the gate too.
    * 998 — the outer dwell masked but the nested hold not: a streak built on
      unreadable samples, reporting "held" the instant the gate cleared. This
      is the answer the engine used to give and the one that must not return.
    * 1003 — both masked, which is this assertion.
    """
    streams, _ = _run(backend, kind="artifact", condition=NESTED_HOLD_CONDITION)
    assert _muted_span(streams) == list(range(MUTE_START, MUTE_END + 1))
    assert _first_raw_event_time(streams) == _at(NESTED_FROZEN)
    assert _first_delivered_event_time(streams) == _at(NESTED_FROZEN)
    # The three answers have to stay three, or this case cannot discriminate.
    assert len({NESTED_UNMASKED, NESTED_CREDITED, NESTED_FROZEN}) == 3
    assert _first_raw_event_time(streams) not in (
        _at(NESTED_UNMASKED), _at(NESTED_CREDITED),
    )
    # And the question is live: the nested hold's own 32 samples would complete
    # INSIDE the blind window, so whether it is masked decides whether it is
    # holding at all when the gate clears.
    assert MUTE_START <= BURST_START + NESTED_HOLD_SAMPLES - 1 <= MUTE_END
    # Only the observability mask crossed over. Eligibility would have zeroed
    # the nested streak on every muted sample instead of freezing it, costing
    # the 26 samples it had banked and pushing the answer out to 1029.
    #
    # This assertion records the arithmetic; it is not what catches the mask
    # crossing over. Under an artifact label `muted` and `artifact_muted` are
    # the same vector, so eligibility would zero the streak on exactly the
    # samples observability freezes — and freezing wins, because the hold
    # checks it first. The two cancel and the answer stays 1003. The cases
    # that do catch it are the unlabelled and `"trained"` ones below, where
    # there is no observability mask to do the cancelling.
    assert NESTED_FROZEN < NESTED_IF_ELIGIBILITY_CROSSED
    assert _first_raw_event_time(streams) != _at(NESTED_IF_ELIGIBILITY_CROSSED)


@pytest.mark.parametrize("pause_via", ["clock", "reset"])
def test_a_transport_pause_restarts_a_hold_nested_in_a_reward_condition(backend, pause_via):
    """A streak cannot survive a gap in the signal, wherever the hold is written.

    `set_clock_frozen(True)` and `reset_held_outputs()` both say the same
    thing: the samples that would have come next did not come, so no streak
    standing at that moment means what it says any more. A hold nested inside
    a reward condition is in exactly that position, so it restarts — the same
    rule the `output`-bound holds have always followed, and the same rule
    `tests/test_eval_inhibit_kind.py`'s frozen-clock row pins there.

    No mute fires in either run here: the muscle column is silent, so the
    masks are out of the picture and the pause is the only thing that can move
    the answer. It moves it from 785 to 798 — the nested hold had banked 13 of
    its 32 samples when the pause landed, loses them, needs all 32 again from
    the first sample after, and the outer dwell takes its 64 from there.

    Rust reset only the holds reachable from `output { }`, so it answered 785
    with the pause and without it, 13 samples earlier than Python and on a
    streak built partly on samples from before the gap. That is the divergence
    this case exists to pin, and the reason it asserts the delivered event and
    not an evaluator attribute: a parity claim has to be readable from the
    stream a trainee hears.
    """
    paused, _ = _run(backend, kind=None, condition=NESTED_HOLD_CONDITION,
                     artifact=False, pause_at=PAUSE_AT, pause_via=pause_via)
    running, _ = _run(backend, kind=None, condition=NESTED_HOLD_CONDITION,
                      artifact=False)
    # Witness that nothing was muted in either run, so the only difference
    # between them is the pause.
    assert _muted_span(paused) == []
    assert _muted_span(running) == []
    assert _first_raw_event_time(running) == _at(NESTED_UNMASKED)
    assert _first_raw_event_time(paused) == _at(NESTED_AFTER_PAUSE)
    assert _first_delivered_event_time(paused) == _at(NESTED_AFTER_PAUSE)
    assert int(paused["reward.event"].sum()) == 1
    # The pause has to land inside the nested hold's streak, or the case
    # observes nothing: before the burst it would have no streak to kill and
    # after 722 the hold would already be satisfied.
    assert BURST_START < PAUSE_AT <= NESTED_RISE_UNMASKED
    # And the two answers have to differ, or it cannot tell a restarted hold
    # from one that ran straight through the gap.
    assert NESTED_AFTER_PAUSE != NESTED_UNMASKED


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="reads the Python evaluator's own attributes; the Rust twin keeps "
           "the same pair as env keys and is held to it by the drift gate",
)
def test_neither_hold_mask_outlives_the_stage_that_set_it():
    """Each mask is cleared by the stage that set it, every chunk.

    The masks are per-stage and per-chunk, so one left standing is read by
    whatever evaluates next — a chunk late, and at the previous chunk's length.
    That has already gone wrong once here, and this is the invariant rather
    than one of its symptoms.

    The second protocol writes `output { }` empty — a legal analysis-only
    document that computes a reward and plays nothing — so the per-channel
    output loop never runs a single iteration. The clear after it is outside
    that loop and must still happen; if it is ever moved inside, this is where
    that shows, and no stream-level case would catch it because a protocol
    with no outputs has no streams to compare.

    Note what this does NOT pin: the reward stage's own clear. With the clear
    after the output loop in place, a reward-stage mask left standing is
    overwritten before anything can read it, in every protocol shape including
    this one. That clear is correct and deliberate, and no behavioural test can
    distinguish it — see `task-7b-report.md`.
    """
    level = np.full(N_SAMPLES, 0.2)
    level[BURST_START:BURST_END] = 1.0
    emg = np.zeros(N_SAMPLES)
    emg[MUTE_START:MUTE_START + EMG_SAMPLES] = 1.0
    signal = np.column_stack((level, emg))

    for with_outputs in (True, False):
        src = reward_event_protocol(
            kind="artifact", condition=NESTED_HOLD_CONDITION,
            with_outputs=with_outputs,
        )
        ir = resolve(parse(src))
        assert bool(ir.output) is with_outputs
        evaluator = Evaluator.live(
            ir, sample_rate_hz=RATE, channel_names=("Cz", "A1"),
            record_streams=True, backend="python",
        )
        evaluator.start(skip_warmup=True)
        assert evaluator._hold_for_eligible is None
        assert evaluator._hold_for_observable is None
        # Chunk sizes that change mid-session: a leaked mask is the previous
        # chunk's length as well as its value, so this is where it shows.
        start = 0
        for size in (64, 100, 48, 128, 37, 256, 64, 100, 48, 128, 37, 256):
            evaluator.step_chunk(signal[start:start + size])
            start += size
            where = (with_outputs, start)
            assert evaluator._hold_for_eligible is None, where
            assert evaluator._hold_for_observable is None, where
        assert start < N_SAMPLES


def test_an_unlabelled_mute_does_not_freeze_a_nested_hold(backend):
    """Absence changes nothing here either. No label means no mask on this
    path at all, so the nested hold banks straight through the mute and the
    answer is the one a run with no mute gives."""
    streams, _ = _run(backend, kind=None, condition=NESTED_HOLD_CONDITION)
    assert _muted_span(streams) == list(range(MUTE_START, MUTE_END + 1))
    assert _first_raw_event_time(streams) == _at(NESTED_UNMASKED)
    assert _first_raw_event_time(streams) != _at(NESTED_FROZEN)


def test_a_trained_mute_does_not_freeze_a_nested_hold(backend):
    """`"trained"` reads as no label on this path, nested holds included —
    stream for stream, not merely the same event time."""
    trained, _ = _run(backend, kind="trained", condition=NESTED_HOLD_CONDITION)
    unlabelled, _ = _run(backend, kind=None, condition=NESTED_HOLD_CONDITION)
    assert _first_raw_event_time(trained) == _at(NESTED_UNMASKED)
    assert sorted(trained) == sorted(unlabelled)
    for key, values in trained.items():
        assert np.array_equal(values, unlabelled[key]), key


def test_a_block_scoped_artifact_inhibit_does_not_freeze_a_nested_hold(backend):
    """An inhibit the active block has excluded gates no output, so it makes
    nothing unobservable — for a nested hold exactly as for the outer dwell.
    The label is on `"emg"` and `"emg"` fires over the same 186 samples; the
    block names only `"idle"`, which never fires."""
    streams, _ = _run(
        backend, kind="artifact", condition=NESTED_HOLD_CONDITION,
        mode="block_scoped",
    )
    assert _muted_span(streams) == []
    assert _first_raw_event_time(streams) == _at(NESTED_UNMASKED)
    assert _first_delivered_event_time(streams) == _at(NESTED_UNMASKED)
    # Same label, no block around it: 218 samples of the burst become
    # unreadable and the answer moves.
    unscoped, _ = _run(backend, kind="artifact", condition=NESTED_HOLD_CONDITION)
    assert _first_raw_event_time(unscoped) == _at(NESTED_FROZEN)


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
    assert _first_raw_event_time(uneven) == _at(NESTED_FROZEN)
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
