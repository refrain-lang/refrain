# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Time an artifact gate could not read is neither a success nor a failure.

These cases cover the hold bound inside `output { }` — the path where the
eligibility mask already existed and a muted sample zeroed the streak. An
inhibit labelled `kind = "artifact"` now reaches that hold as a second,
separate mask: the condition still says "not qualifying", the observability
mask says "not observed", and the hold checks observability first.

Every row of the design's forgiveness table is covered, and each test names
the row it covers in its docstring. The three rows that say "reset, as today"
(a phase mute, an inactive block channel, a frozen transport clock) are pinned
here too, with an artifact label declared but its gate idle: those are the rows
that break the moment a reason the engine *scored* a sample is mistaken for a
reason it could not *read* one, which is the easiest way to build this mask
wrong. They pass before this change as well as after — they are regression
pins, not new behaviour.

The file name matches `tests/test_eval_*.py` on purpose: that glob is what the
dual-backend drift gate runs under `REFRAIN_EVAL_BACKEND=rust`.
"""

from __future__ import annotations

import numpy as np

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

# 200 Hz makes every duration a round number of samples: the 50 ms hold is 10,
# the 15 ms tolerance is 3, and the inhibit's 5 ms release is exactly 1 sample
# of hangover. One sample is deliberate rather than zero: `release: 0 ms` is
# read as "unset" by the Python evaluator (`ih.action_release_ms or 200.0`) and
# as zero by the Rust core, so a zero-release protocol would not be a parity
# fixture at all.
RATE = 200
HOLD_SAMPLES = 10          # 50 ms
TOLERANCE = "15 ms"
TOLERANCE_SAMPLES = 3
RELEASE_SAMPLES = 1        # 5 ms

# `rectify()` is `abs()`, so the two inputs below reach `level` and
# `emg_level` sample for sample with no filter standing in between: the
# condition is exactly "this sample was written above 0.5" and the inhibit is
# exactly "this sample's muscle column was written above 0.5".
#
# `gain` is a constant 0.5. The output stage zeroes a muted channel, so a 0.0
# in `output/gain` is the mute and nothing else — which is how each test below
# pins that the mute actually fired, and over exactly which samples. Without
# that witness a test named for a mute passes just as happily when no mute
# ever happens.
PROTOCOL = '''
protocol "artifact gate" {
  meta { version = "1.0"; evidence = "experimental"; description = "inhibit kind" }
  requires { sample_rate = ">= 200 Hz"; channels = ["Cz", "A1"] }
  input "trained" { montage = referential(active: "Cz", reference: "device") }
  input "muscle"  { montage = referential(active: "A1", reference: "device") }
  derive "level"     { from = "trained"; pipeline = [rectify()] }
  derive "emg_level" { from = "muscle";  pipeline = [rectify()] }
  threshold "t" { signal = "level"; type = absolute(0.5 uV) }
  inhibit "gate" {
    metric    = rectify("emg_level")
    threshold = absolute(0.5 uV)
    action    = mute(release: RELEASE)
    KIND
  }
  reward { continuous = sigmoid("level" / "t", midpoint: 1.0, steepness: 3) }
  output {
    latch = hold_for(condition: above("level", "t"), duration: 50 msTOL)
    gain  = 0.5
  }
  SESSION
}
'''

# A mid-session rest (row 4) and an inactive-block channel (row 5) need a
# session; 1 s phases at 200 Hz put the boundaries at samples 200 and 400.
#
# Each of the two comes with a control that differs in exactly one word — the
# rest is not output-muted, the middle phase keeps the channel live — so the
# test can show that it is the rest and the block, and not the shape of the
# signal, that moved the answer.
REST_SESSION = '''session {
    phases = [
      phase { name = "p1";   duration = 1 s },
      phase { name = "rest"; duration = 1 s; output_muted = REST_MUTED },
      phase { name = "p3";   duration = 2 s },
    ]
  }'''

BLOCK_SESSION = '''block "both"      { output = ["latch", "gain"] }
  block "gain_only" { output = ["gain"] }
  session {
    phases = [
      phase { name = "p1"; duration = 1 s; block = "both" },
      phase { name = "p2"; duration = 1 s; block = "BLOCK2" },
      phase { name = "p3"; duration = 2 s; block = "both" },
    ]
  }'''

PHASE_BOUNDARY = 200       # samples per 1 s phase at 200 Hz
GAP_LEAD_IN = PHASE_BOUNDARY - 9   # 9 qualifying samples before the boundary


def _ir(kind: str | None, tolerance: str | None, session: str, release: str):
    src = PROTOCOL.replace("KIND", "" if kind is None else f'kind = "{kind}"')
    src = src.replace("TOL", "" if tolerance is None else f", tolerance: {tolerance}")
    src = src.replace("RELEASE", release)
    return resolve(parse(src.replace("SESSION", session)))


def _run(
    backend: str,
    level: np.ndarray,
    emg: np.ndarray,
    *,
    kind: str | None,
    tolerance: str | None = None,
    session: str = "",
    chunk: int = 64,
    freeze_before: int | None = None,
    release: str = "5 ms",
) -> dict[str, np.ndarray]:
    """Feed one `level`/`emg` pair; return the whole run's per-sample streams.

    A qualifying sample writes 1.0 into the trained column and a failing one
    0.2 — both above zero, so `reward.continuous` never goes to zero on its
    own and a 0.0 in `output/gain` can only be the output gate. The muscle
    column is 1.0 while the artifact is present and 0.0 otherwise.
    """
    signal = np.column_stack((np.where(level, 1.0, 0.2), np.where(emg, 1.0, 0.0)))
    evaluator = Evaluator.live(
        _ir(kind, tolerance, session, release), sample_rate_hz=RATE,
        channel_names=("Cz", "A1"), record_streams=True, backend=backend,
    )
    evaluator.start(skip_warmup=True)
    streams: dict[str, list[float]] = {}
    for start in range(0, len(signal), chunk):
        if freeze_before is not None and start == freeze_before:
            evaluator.set_clock_frozen(True)
        evaluator.step_chunk(signal[start:start + chunk])
        for key, values in evaluator.last_streams().items():
            streams.setdefault(key, []).extend(np.asarray(values).tolist())
    return {k: np.asarray(v) for k, v in streams.items()}


def _first_hold(streams: dict[str, np.ndarray]) -> int | None:
    """The first sample on which the `output`-bound hold reads true."""
    held = np.flatnonzero(streams["output/latch"])
    return int(held[0]) if held.size else None


def _muted_samples(streams: dict[str, np.ndarray]) -> list[int]:
    """The samples the output gate silenced, read off the constant channel."""
    return np.flatnonzero(streams["output/gain"] == 0.0).tolist()


# ---------------------------------------------------------------------------
# A swallow: 9 qualifying samples, an artifact mute over a signal dropout,
# then qualifying samples again. The mute spans samples 9..14 inclusive
# (5 active + 1 release), so sample 15 is the first one the engine can read
# again — and the 10th qualifying sample of the streak if it survived.
# ---------------------------------------------------------------------------

SWALLOW_LENGTH = 120
MUTE_START = 9
MUTE_ACTIVE = 5
MUTE_END = MUTE_START + MUTE_ACTIVE + RELEASE_SAMPLES - 1   # 14
RESUME = MUTE_END + 1                                       # 15
HELD_ANSWER = RESUME                                        # streak survived: 9 + 1
RESET_ANSWER = RESUME + HOLD_SAMPLES - 1                    # streak zeroed: 10 more


def _swallow() -> tuple[np.ndarray, np.ndarray]:
    level = np.ones(SWALLOW_LENGTH, dtype=bool)
    emg = np.zeros(SWALLOW_LENGTH, dtype=bool)
    level[MUTE_START:MUTE_START + MUTE_ACTIVE] = False
    emg[MUTE_START:MUTE_START + MUTE_ACTIVE] = True
    return level, emg


def test_an_artifact_mute_holds_the_output_bound_hold_clock(backend):
    """Row 2 — an inhibit labelled `"artifact"` is muting: held for as long as
    the mute lasts. The streak reaches 10 on the first readable sample after
    the gate clears, not 10 samples later."""
    streams = _run(backend, *_swallow(), kind="artifact")
    assert _muted_samples(streams) == list(range(MUTE_START, MUTE_END + 1))
    assert _first_hold(streams) == HELD_ANSWER


def test_a_trained_mute_resets_the_output_bound_hold_clock(backend):
    """Row 3 — an inhibit labelled `"trained"` is muting: today's behaviour,
    which on this path is a reset. Drifting toward sleep is the thing the
    protocol trains against, so it is scored."""
    streams = _run(backend, *_swallow(), kind="trained")
    assert _muted_samples(streams) == list(range(MUTE_START, MUTE_END + 1))
    assert _first_hold(streams) == RESET_ANSWER


def test_an_unlabelled_mute_resets_it_exactly_as_today(backend):
    """Row 3, the unlabelled half — absence reproduces today's behaviour
    sample for sample, which is what lets the corpus land unchanged."""
    unlabelled = _run(backend, *_swallow(), kind=None)
    trained = _run(backend, *_swallow(), kind="trained")
    assert _first_hold(unlabelled) == RESET_ANSWER
    for key, values in unlabelled.items():
        assert np.array_equal(values, trained[key]), key


def test_an_artifact_mute_spanning_a_chunk_boundary_holds_the_clock(backend):
    """Row 2 across a chunk seam. The dip counter and the streak are carried
    state, so a mask rebuilt per chunk is where a cross-chunk mute loses the
    freeze. Chunks of 12 put the seam at sample 12, inside the mute."""
    spanning = _run(backend, *_swallow(), kind="artifact", chunk=12)
    whole = _run(backend, *_swallow(), kind="artifact", chunk=64)
    assert _first_hold(spanning) == HELD_ANSWER
    for key, values in spanning.items():
        assert np.array_equal(values, whole[key]), key


# ---------------------------------------------------------------------------
# A clench: the muscle burst leaks into the trained band, so the condition
# reads true all the way through a long mute. Held means held — the streak
# must not climb either.
#
# The lead-in here is 3 qualifying samples, not 9, and that is the whole
# reason this case can see a credited streak at all. The output stage zeroes
# `output/latch` while the gate is shut, so whatever the hold does during the
# mute is invisible until it clears; what is visible is how much streak it
# comes out with. A 3-sample lead-in leaves three answers that differ:
# credited (the streak passed 10 inside the mute, so the hold reads true on
# the first readable sample), frozen (3 banked, 7 more needed) and reset
# (10 more needed). With a 9-sample lead-in the first two collapse into the
# same number and the case is worthless against crediting.
# ---------------------------------------------------------------------------

CLENCH_START = 3
CLENCH_ACTIVE = 20
CLENCH_END = CLENCH_START + CLENCH_ACTIVE + RELEASE_SAMPLES - 1   # 23
CLENCH_RESUME = CLENCH_END + 1                                    # 24
CLENCH_FROZEN = CLENCH_RESUME + HOLD_SAMPLES - CLENCH_START - 1   # 30
CLENCH_CREDITED = CLENCH_RESUME                                   # 24
CLENCH_RESET = CLENCH_RESUME + HOLD_SAMPLES - 1                   # 33


def test_a_clench_under_an_artifact_mute_banks_nothing(backend):
    """Design §evidence, "On the muscle learner": muscle leaks into 12-15 Hz,
    so the clock must not climb on contaminated signal.

    Row 2 again, from the other side. The condition is true for all 21 muted
    samples. A hold that credited them would come out of the gate already
    holding and read true at sample 24, rewarding a clench; one that reset
    would need all 10 samples again and read true at 33. Frozen, the streak
    is still the 3 it banked before the gate, so it needs 7 more and reads
    true at 30.
    """
    level = np.ones(SWALLOW_LENGTH, dtype=bool)
    emg = np.zeros(SWALLOW_LENGTH, dtype=bool)
    emg[CLENCH_START:CLENCH_START + CLENCH_ACTIVE] = True
    streams = _run(backend, level, emg, kind="artifact")
    assert CLENCH_CREDITED < CLENCH_FROZEN < CLENCH_RESET   # three answers, not two
    assert _muted_samples(streams) == list(range(CLENCH_START, CLENCH_END + 1))
    assert _first_hold(streams) == CLENCH_FROZEN


# ---------------------------------------------------------------------------
# The gate's release hangover is a state machine, so it has to be stepped once
# per chunk however many masks are built off it.
# ---------------------------------------------------------------------------

LONG_RELEASE = "100 ms"
LONG_RELEASE_SAMPLES = 20
LONG_MUTE_END = MUTE_START + MUTE_ACTIVE + LONG_RELEASE_SAMPLES - 1   # 33


def test_the_artifact_mask_never_outlives_the_mute_gate(backend):
    """An artifact-muted sample is always a muted sample: the two masks come
    off one gate, stepped once.

    `MuteAction.gate` runs the release hangover down as a side effect, so a
    second call per chunk runs it down twice as fast and the artifact mask
    clears while the mute gate is still shut — leaving samples that are muted
    but counted as readable, which reset the streak. A 100 ms release and
    chunks of 8 put whole chunks inside the hangover with no active sample in
    them, which is the only shape where the double step shows: the hold must
    resume at 34, the first readable sample, not 10 samples later.
    """
    level = np.ones(SWALLOW_LENGTH, dtype=bool)
    emg = np.zeros(SWALLOW_LENGTH, dtype=bool)
    emg[MUTE_START:MUTE_START + MUTE_ACTIVE] = True
    streams = _run(backend, level, emg, kind="artifact",
                   release=LONG_RELEASE, chunk=8)
    assert _muted_samples(streams) == list(range(MUTE_START, LONG_MUTE_END + 1))
    assert _first_hold(streams) == LONG_MUTE_END + 1


# ---------------------------------------------------------------------------
# Review Focus 1: a dip and an artifact mute overlapping.
# ---------------------------------------------------------------------------

DIP_ACTIVE = 10
DIP_END = MUTE_START + DIP_ACTIVE + RELEASE_SAMPLES - 1   # 19
DIP_RESUME = DIP_END + 1                                  # 20


def test_a_dip_inside_an_artifact_mute_does_not_count_against_tolerance(backend):
    """Review Focus 1, at the engine level: unobservable wins over false.

    The hold declares a 3-sample tolerance and the mute runs 11 samples with
    the condition false throughout. If the dip counter advanced on unreadable
    samples it would pass tolerance on the fourth of them and zero the streak
    through the dip path, so the `kind` label would buy nothing: the answer
    would be 29 rather than 20.
    """
    level = np.ones(SWALLOW_LENGTH, dtype=bool)
    emg = np.zeros(SWALLOW_LENGTH, dtype=bool)
    level[MUTE_START:MUTE_START + DIP_ACTIVE] = False
    emg[MUTE_START:MUTE_START + DIP_ACTIVE] = True
    streams = _run(backend, level, emg, kind="artifact", tolerance=TOLERANCE)
    assert DIP_ACTIVE + RELEASE_SAMPLES > TOLERANCE_SAMPLES
    assert _muted_samples(streams) == list(range(MUTE_START, DIP_END + 1))
    assert _first_hold(streams) == DIP_RESUME


# ---------------------------------------------------------------------------
# The three rows that stay as they are. In each the artifact label is
# declared and the gate is idle, so nothing but a mask built from the wrong
# source could make these forgiving.
# ---------------------------------------------------------------------------

STAGED_LENGTH = 800


def _staged_signal() -> tuple[np.ndarray, np.ndarray]:
    """Qualifying from 9 samples before the first phase boundary, onward.

    The muscle column is silent throughout: the only thing stopping the clock
    is the phase or the block, and the artifact label is along for the ride.
    """
    level = np.zeros(STAGED_LENGTH, dtype=bool)
    level[GAP_LEAD_IN:] = True
    return level, np.zeros(STAGED_LENGTH, dtype=bool)


def test_a_phase_mute_still_resets_the_clock(backend):
    """Row 4 of the design §1 forgiveness table — a mid-session rest is not an
    artifact.

    9 qualifying samples, then a 1 s output-muted rest with the condition
    still true, then the training phase again. The streak is zeroed by the
    rest, so the hold needs all 10 samples after sample 400 and first reads
    true at 409. The control run is the same session with the rest not
    output-muted: there nothing stops the clock and the hold reads true at
    sample 200, which is what a forgiving rest would have given.
    """
    rest = _run(backend, *_staged_signal(), kind="artifact", chunk=50,
                session=REST_SESSION.replace("REST_MUTED", "true"))
    control = _run(backend, *_staged_signal(), kind="artifact", chunk=50,
                   session=REST_SESSION.replace("REST_MUTED", "false"))
    assert _muted_samples(rest) == list(range(PHASE_BOUNDARY, 2 * PHASE_BOUNDARY))
    assert _muted_samples(control) == []
    assert _first_hold(control) == PHASE_BOUNDARY
    assert _first_hold(rest) == 2 * PHASE_BOUNDARY + HOLD_SAMPLES - 1


def test_an_inactive_block_channel_still_resets_the_clock(backend):
    """Row 5 — the channel is outside the active block's outputs: reset, as
    today. A hidden streak that survived the dead phase would emerge as an
    immediate hold the moment the block came back.

    The control run is the same session with the middle phase keeping `latch`
    live, where the hold reads true at sample 200.
    """
    dropped = _run(backend, *_staged_signal(), kind="artifact", chunk=50,
                   session=BLOCK_SESSION.replace("BLOCK2", "gain_only"))
    control = _run(backend, *_staged_signal(), kind="artifact", chunk=50,
                   session=BLOCK_SESSION.replace("BLOCK2", "both"))
    assert _first_hold(control) == PHASE_BOUNDARY
    assert _first_hold(dropped) == 2 * PHASE_BOUNDARY + HOLD_SAMPLES - 1


def test_a_frozen_transport_clock_still_resets_the_clock(backend):
    """Row 6 — the transport clock is frozen, or a data gap: reset, as today,
    via `reset_held_outputs()`.

    9 qualifying samples, then the clock freezes, then qualifying samples
    again with nothing muted at all. `reset_held_outputs()` discards the
    streak, so the hold first reads true 10 samples later, at 18; left
    standing it would read true at 9.
    """
    level = np.ones(60, dtype=bool)
    emg = np.zeros(60, dtype=bool)
    frozen = _run(backend, level, emg, kind="artifact", chunk=9, freeze_before=9)
    running = _run(backend, level, emg, kind="artifact", chunk=9)
    assert _muted_samples(frozen) == []
    assert _first_hold(running) == 9
    assert _first_hold(frozen) == 9 + HOLD_SAMPLES - 1
