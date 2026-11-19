# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Re-measuring a seeded control when a block goes active, on both engines.

A `seed` rule measures a control once, from the warm-up, and the session trains
against that number for the next forty minutes. `reseed` on a block says: when
this block goes active, measure it again from the signal that just ran. This
file is the run-time half — the boundary fire, the trailing window it reads,
the two freezes that keep contaminated and unobserved time out of that window,
what happens when the window has not filled, and what a practitioner's own
`set_control` does to all of it.

**Why the assertions go through `seed_report()` and the taps, never through
`Evaluator._seed_latches`.** Under the Rust backend the Python latch objects
are still constructed but never stepped — a run the Rust core seeds correctly
leaves the Python latch reading `armed=True, fired=False, status='pending'`. A
test written against latch internals therefore passes under
`REFRAIN_EVAL_BACKEND=rust` whatever the Rust core does, which is a green test
that observes nothing. `seed_report()` is populated by both engines, so every
`(backend)`-parametrized test below genuinely observes the backend it names.
The one property that is only visible in Python internals — that a protocol
with no `reseed` does no ingestion work after its latch fires — carries an
explicit skip instead of a silent vacuous pass.

The file name matches `tests/test_eval_*.py` on purpose: that glob is what the
dual-backend drift gate (`refrain-core/tools/check_equivalence.py` step 4)
runs under `REFRAIN_EVAL_BACKEND=rust`.
"""

from __future__ import annotations

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve
from tests.conftest import RUST_BACKEND_ACTIVE

RATE = 256.0
CHUNK = 256          # 1 s per chunk, so every phase length below is in chunks
WINDOW_SAMPLES = 512  # the 2 s seed window these protocols declare, in samples

# `magnitude()` straight off a `passthrough()` input makes the derive equal the
# input sample for sample, so a constant-fill segment makes the percentile
# exact by construction: the median of 512 copies of 5.0 is 5.0, with no
# rounding to argue about across two engines.
#
# Two reward bundles with the same condition, one per block, because that is
# the shape a staged protocol has — and because both are stepped on every
# chunk (dwell timers stay warm), which is what makes "whose streak gets
# restarted" a real question rather than a bookkeeping one.
#
# `lamp` is the control case for that question: a `hold_for` over a threshold
# that is a plain literal, so nothing a re-seed touches reaches it. Its 2 s
# duration is deliberate — at 1 s a reset at the start of a 1 s chunk would be
# invisible in the last-sample tap, since the streak would refill inside the
# same chunk.
#
# `probe` reads the control in EXPRESSION position (a bare operand, not an
# impl's parameter slot), which is the one shape that can read a stale value
# for a whole chunk: a parameter slot is refreshed through `update_control`,
# while an expression-position reference reads this chunk's control broadcast.
# The warm-up seed path has a committed golden vector for exactly that
# (`bench/protocols/seed_exprpos.refrain`); this is the re-seed's.
PROTO = '''protocol "reseed_run" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "re-seed at a block boundary" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "env" { from = "raw"; pipeline = [ magnitude() ] }
  threshold "thr"   { signal = "env"; type = absolute(value: smr_threshold_uv) }
  threshold "floor" { signal = "env"; type = absolute(value: 1 uV) }
  ROLLING
  INHIBIT
  reward "bundle_one" { event = dwell(condition: above("env", "thr"), duration: 100 ms) }
  reward "bundle_two" { event = dwell(condition: above("env", "thr"), duration: 100 ms) }
  output {
    audio = reward.event
    lamp  = hold_for(condition: above("env", "floor"), duration: 2 s)
    probe = "env" / smr_threshold_uv
  }
  block "one" { reward = "bundle_one"; output = ["audio", "lamp", "probe"] }
  block "two" { reward = "bundle_two"; output = ["audio", "lamp", "probe"]RESEED }
  controls {
    smr_threshold_uv = voltage {
      default = 9.9 uV; range = (0.5 uV, 50 uV); live_tunable = true
      seed = percentile { from = "env"; window = WINDOW; target_pct = 50SCALE }
    }
  }
  session { phases = [ PHASES ] }
}'''

# Phase spellings. Every phase is `timed` and long enough that nothing
# auto-advances: the tests drive the cursor with `advance_phase()`, so a
# boundary happens exactly where the script says it does and nowhere else.
WARMUP = 'phase { name = "warmup"; duration = WS; output_muted = true }'
BLOCK_ONE = 'phase { name = "a"; duration = 60 s; block = "one" }'
BLOCK_TWO = 'phase { name = "b"; duration = 60 s; block = "two" }'
MUTED_REST = 'phase { name = "rest"; duration = 60 s; output_muted = true }'
# The live counterpart to MUTED_REST. It has to name a block — the resolver
# refuses a non-muted phase with no block once any block is declared — so it
# names block one, which is also what makes it a fair comparison: the same
# wall clock, training rather than resting.
LIVE_REST = 'phase { name = "rest"; duration = 60 s; block = "one" }'

# An inhibit that trips on the training signal itself, so a test can hold the
# gate open for a whole block by choosing the fill. `rectify` is an identity on
# an already-positive envelope; an inhibit metric has to be a call, so that is
# how a derive is named as one (as `bench/protocols/micro_15_artifact_gate.refrain`
# does). `release: 200 ms` rather than 0 ms: a zero release is a known
# divergence between the engines, out of scope here.
ARTIFACT_INHIBIT = '''inhibit "artifact_gate" {
    metric    = rectify("env")
    threshold = absolute(4 uV)
    action    = mute(release: 200 ms)
    kind      = "artifact"
  }'''
# The same gate with no `kind`. An unlabelled mute is a sample the engine read
# and scored against the trainee, not one it could not read, so it does not
# freeze re-seed ingestion — which is the asymmetry the pair of tests below
# pins. Also the shape the global constraints warn about: under the artifact
# label the streak-zeroing mask and the freezing mask are the same vector, so
# a test that needs the two apart must use an unlabelled mute.
PLAIN_INHIBIT = ARTIFACT_INHIBIT.replace('\n    kind      = "artifact"', "")

# A rolling percentile threshold, present only in the resume test's variant.
# It is what makes `export_state()` non-empty, so that test can show the
# seed-state round trip working on the trackers it does carry before asserting
# that it does not carry a re-seed buffer.
ROLLING_THRESHOLD = (
    'threshold "rolling" { signal = "env"; type = percentile(target_pct: 50, window: 2 s) }'
)


def _ir(
    *,
    phases=(WARMUP, BLOCK_ONE, BLOCK_TWO),
    warmup_s: int = 3,
    window: str = "2 s",
    inhibit: str = "",
    rolling: str = "",
    reseed: bool = True,
    scale: float | None = None,
):
    src = PROTO.replace("PHASES", ",\n    ".join(phases))
    src = src.replace("WS", f"{warmup_s} s")
    src = src.replace("WINDOW", window)
    src = src.replace("INHIBIT", inhibit)
    src = src.replace("ROLLING", rolling)
    src = src.replace("RESEED", '; reseed = ["smr_threshold_uv"]' if reseed else "")
    src = src.replace("SCALE", "" if scale is None else f"; scale = {scale!r}")
    return resolve(parse(src))


# A second, self-contained protocol for the advisor cases. Separate from
# PROTO rather than another slot in it: a `fixes` target has to be a NAMED
# reward check, check names must be unique across the whole protocol, and
# adding named checks to PROTO would move what every other test in this file
# observes.
#
# The only autopilot knob is the re-seeded control itself, so an `adjust` can
# only ever target it — the test never has to hope the advisor picked the
# control the test is about. Short `watch` / `between_moves` so two seconds of
# block one reach an adjust: measured, the advisor proposes 2.2 uV at the
# second block-one chunk on both engines, against a re-seed that would write
# 5.0, so the two outcomes are never confusable.
ADVICE_PROTO = '''protocol "reseed_advice" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "re-seed plus advisor" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "env" { from = "raw"; pipeline = [ magnitude() ] }
  threshold "thr" { signal = "env"; type = absolute(value: smr_threshold_uv) }
  reward "bundle_one" {
    event = dwell(condition: all_of([above("env", "thr") as "level"]), duration: 100 ms)
  }
  reward "bundle_two" {
    event = dwell(condition: all_of([above("env", "thr") as "level_two"]), duration: 100 ms)
  }
  output { audio = reward.event }
  block "one" { reward = "bundle_one"; output = ["audio"] }
  block "two" { reward = "bundle_two"; output = ["audio"]RESEED }
  controls {
    smr_threshold_uv = voltage {
      default = 9.9 uV; range = (0.5 uV, 50 uV); live_tunable = true
      seed = percentile { from = "env"; window = 2 s; target_pct = 50 }
      autopilot = proportional_step { fixes = "level"; step = 10%; higher_is = "harder"
                                      apply = "auto"; round_to = 0.01 uV }
    }
  }
  autopilot {
    evidence         = "exploratory"
    citation         = "Test policy"
    rationale        = "Short windows so two seconds of block one reach an adjust."
    reward_target    = (20%, 40%)
    watch            = 2 s
    between_moves    = 2 s
    equipment_settle = 1 s
    tighten_first    = ["level"]
  }
  session { phases = [ PHASES ] }
}'''


def _advice_ir(*, reseed: bool = True):
    src = ADVICE_PROTO.replace("PHASES", ",\n    ".join((WARMUP, BLOCK_ONE, BLOCK_TWO)))
    src = src.replace("WS", "3 s")
    src = src.replace("RESEED", '; reseed = ["smr_threshold_uv"]' if reseed else "")
    return resolve(parse(src))


class Session:
    """One driven session, with the per-chunk observations the tests read.

    Everything here is host-facing: `seed_report()`, `last_taps()` and the
    `Event` list `step_chunk` returns. Nothing reaches into the evaluator.
    """

    def __init__(self, ev: Evaluator):
        self.ev = ev
        self.audio_events: list[int] = []      # audio events per fed chunk
        self.taps: list[dict] = []             # last_taps() per fed chunk
        self.phase: list[int] = []             # phase index per fed chunk

    def feed(self, n: int, fill: float) -> Session:
        for _ in range(n):
            events = self.ev.step_chunk(np.full((CHUNK, 1), fill, dtype=np.float64))
            self.audio_events.append(sum(1 for e in events if e.channel == "audio"))
            self.taps.append(dict(self.ev.last_taps()))
            self.phase.append(int(self.ev.current_phase()["index"]))
        return self

    def advance(self) -> Session:
        self.ev.advance_phase()
        return self

    def set_control(self, name: str, value: float) -> Session:
        self.ev.set_control(name, value)
        return self

    def accept_advice(self, by: str) -> float:
        """Apply the advisor's current `adjust` and return the value it wrote.

        Asserts the advice really is an adjust on the re-seeded control before
        applying it, so a test can never pass by applying advice about
        something else — the disarm is looked up by the control the advice
        actually wrote, not by the control the test had in mind.
        """
        advice = self.ev.advice()
        assert advice["state"] == "adjust", advice
        assert advice["control"]["name"] == "smr_threshold_uv", advice
        event = self.ev.apply_advice(advice["id"], by=by)
        assert event["kind"] == "applied" and event["by"] == by, event
        return float(advice["control"]["proposed"])

    def freeze_clock(self, frozen: bool = True) -> Session:
        self.ev.set_clock_frozen(frozen)
        return self

    @property
    def report(self) -> dict:
        return self.ev.seed_report()["smr_threshold_uv"]

    @property
    def triggers(self) -> list[str]:
        return [h["trigger"] for h in self.report["history"]]

    def chunks_in_phase(self, index: int) -> list[int]:
        """Indices of the fed chunks that ran under phase `index`."""
        return [i for i, p in enumerate(self.phase) if p == index]


def _session(backend: str, ir=None, *, skip_warmup: bool = False, seed_state=None) -> Session:
    ev = Evaluator.live(
        ir if ir is not None else _ir(),
        sample_rate_hz=RATE, channel_names=("Cz",), backend=backend,
        seed_state=seed_state,
    )
    ev.start(skip_warmup=skip_warmup)
    return Session(ev)


def _warmup_then_block_one(backend: str, ir=None, *, fill: float = 5.0) -> Session:
    """The common opening: 3 chunks of 2.0 uV warm-up (so the warm-up seed
    measures 2.0), then 3 chunks of `fill` under block one — enough to fill a
    2 s re-seed window twice over.

    The warm-up seed fires on the FIRST chunk of block one, not on the last
    warm-up chunk: the cursor advances at the end of the third warm-up chunk,
    so the fourth chunk is the first one in `run`.
    """
    return _session(backend, ir).feed(3, 2.0).feed(3, fill)


# ---------------------------------------------------------------------------
# The boundary fire
# ---------------------------------------------------------------------------


def test_a_reseed_at_a_block_boundary_rewrites_the_control(backend):
    """Constant-fill segments make the percentile exact: the warm-up runs at
    2.0 uV and seeds 2.0, block one runs at 5.0 uV, and at block two's
    boundary the trailing window is all 5.0 — so the 50th percentile re-seed
    writes 5.0 over a threshold that had been 2.0 all session."""
    s = _warmup_then_block_one(backend).advance().feed(1, 5.0)
    assert s.report["value"] == pytest.approx(5.0)
    assert s.report["status"] == "seeded"
    assert s.triggers == ["warmup", "block:two"]
    # The threshold itself moved, this chunk — not next chunk. `threshold/thr`
    # is the live control value, so this is the number the trainee is being
    # scored against.
    assert s.taps[-1]["threshold/thr"] == pytest.approx(5.0)
    record = s.report["history"][-1]
    assert record["old_value"] == pytest.approx(2.0)
    assert record["new_value"] == pytest.approx(5.0)
    assert record["status"] == "seeded"
    assert record["at_time_s"] == pytest.approx(6.0)   # 3 s warm-up + 3 s block one
    assert record["source"] == "derive/env"
    assert record["target_pct"] == pytest.approx(50.0)
    assert record["scale"] == pytest.approx(1.0)
    assert record["window_s"] == pytest.approx(2.0)


def test_the_reseeded_value_is_live_for_the_boundary_chunk_itself(backend):
    """An expression-position consumer reads the new threshold on the chunk the
    boundary falls in, not one chunk later. Block two runs at 2 uV against a
    threshold re-seeded to 5 uV, so `probe` reads 0.4 — a stale read would
    give 1.0, the ratio against the warm-up's 2 uV.

    Worth its own case because the two consumer shapes are refreshed by
    different code: an impl parameter slot comes good through
    `update_control`, while an expression-position reference reads the chunk's
    control broadcast, which the fire has to rewrite itself."""
    s = _warmup_then_block_one(backend).advance().feed(2, 2.0)
    boundary, after = s.chunks_in_phase(2)
    assert s.taps[boundary]["output/probe"] == pytest.approx(0.4)
    assert s.taps[after]["output/probe"] == pytest.approx(0.4)


def test_the_warmup_seed_is_in_the_history_as_the_first_record(backend):
    """The history is the whole seeding trail, not just the re-seeds: a host
    showing a practitioner "what has this threshold been" needs the warm-up
    measurement in the same list."""
    s = _warmup_then_block_one(backend)
    assert s.triggers == ["warmup"]
    record = s.report["history"][0]
    # Null, not the declared 9.9 uV: `old_value` is the measurement this event
    # replaced, and a warm-up seed replaces no measurement. The declared
    # default is in the protocol, which the host already has; what the live
    # control happened to be is a number the Rust core cannot reproduce
    # faithfully, so it is not what this field means.
    assert record["old_value"] is None
    assert record["new_value"] == pytest.approx(2.0)
    assert record["status"] == "seeded"
    assert record["at_time_s"] == pytest.approx(3.0)
    # Three warm-up chunks of 256 samples, counted once each. Pinned because
    # the warm-up ingest and the re-seed ingest feed the SAME buffer from two
    # different places: a buffer fed from both during warm-up would fill at
    # double rate over duplicated samples, which a constant-fill percentile
    # would hide completely.
    assert record["fresh_samples"] == 3 * CHUNK


def test_the_reseed_uses_a_trailing_window_ending_at_the_boundary(backend):
    """A step through block one: 2 s at 3.0 uV then 2 s at 7.0 uV. The
    re-seeded value is 7.0 — the last `window` of block one. The median of the
    whole block would be 5.0 and its first half 3.0, so all three answers are
    distinguishable."""
    s = _session(backend).feed(3, 2.0).feed(2, 3.0).feed(2, 7.0).advance().feed(1, 7.0)
    assert s.report["value"] == pytest.approx(7.0)


def test_a_block_entered_twice_reseeds_on_every_entry(backend):
    """A block is a stage, not a one-off: every entry re-measures. Each
    segment runs at a different level so the two re-seeds are told apart by
    their values as well as by their count."""
    s = (
        _session(backend, _ir(phases=(WARMUP, BLOCK_ONE, BLOCK_TWO, BLOCK_ONE, BLOCK_TWO)))
        .feed(3, 2.0)        # warm-up          -> seeds 2.0
        .feed(3, 5.0)        # block one
        .advance().feed(1, 5.0)    # block two  -> re-seeds 5.0
        .advance().feed(3, 8.0)    # block one again
        .advance().feed(1, 8.0)    # block two again -> re-seeds 8.0
    )
    assert s.triggers == ["warmup", "block:two", "block:two"]
    assert [h["new_value"] for h in s.report["history"]] == [
        pytest.approx(2.0), pytest.approx(5.0), pytest.approx(8.0)
    ]
    assert s.report["value"] == pytest.approx(8.0)


def test_the_freshness_counter_restarts_at_every_seeding_event(backend):
    """What the window means is "since the last time this control was
    measured", not "since the session started". A 3 s window, a block-two
    entry one second after the previous re-seed, and the second entry must
    come up short — a buffer that is full of samples the previous re-seed
    already spent is not a fresh measurement."""
    s = (
        _session(backend, _ir(
            phases=(WARMUP, BLOCK_ONE, BLOCK_TWO, BLOCK_ONE, BLOCK_TWO),
            warmup_s=4, window="3 s",
        ))
        .feed(4, 2.0)              # warm-up (4 s, so the 3 s window fills)
        .feed(4, 5.0)              # block one
        .advance().feed(1, 5.0)    # block two -> re-seeds 5.0, freshness back to 0
        .advance().feed(1, 8.0)    # block one, one second only
        .advance().feed(1, 8.0)    # block two -> 2 s of fresh signal, 3 s needed
    )
    assert s.triggers == ["warmup", "block:two", "block:two"]
    assert s.report["history"][-1]["status"] == "insufficient_samples"
    assert s.report["history"][-1]["new_value"] is None
    assert s.report["value"] == pytest.approx(5.0)     # the first re-seed's value stands
    assert s.taps[-1]["threshold/thr"] == pytest.approx(5.0)


def test_a_reseed_applies_the_seed_rules_scale(backend):
    """A re-seed is the warm-up measurement taken again, so it goes through the
    same multiplier: block one's median is 5.0 uV and `scale = 0.5` writes
    2.5. Pinned separately from the warm-up seed's `scale`, because the two
    fire paths are two pieces of code and a multiplier dropped from one of
    them would leave a muscle limit at a third of where the protocol put it."""
    s = _warmup_then_block_one(backend, _ir(scale=0.5)).advance().feed(1, 5.0)
    assert s.report["value"] == pytest.approx(2.5)
    assert s.report["history"][-1]["scale"] == pytest.approx(0.5)
    assert s.taps[-1]["threshold/thr"] == pytest.approx(2.5)


def test_a_scaled_reseed_is_clamped_to_the_controls_range(backend):
    """And through the same clamp, which `scale` brings with it: 5.0 times 20
    is 100 uV, and the control declares 50 uV as the top. Without the clamp a
    re-seed could put a threshold somewhere the protocol says is not sane,
    mid-session, with nobody watching."""
    s = _warmup_then_block_one(backend, _ir(scale=20.0)).advance().feed(1, 5.0)
    assert s.report["value"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# The holds a re-seed restarts
# ---------------------------------------------------------------------------


def test_a_reseed_resets_the_hold_clock_for_downstream_holds_only(backend):
    """A streak banked against the old threshold says nothing about the new
    one, so the reward dwell restarts at the boundary and earns its event
    again from zero. A hold that reads neither the re-seeded control nor a
    threshold it moves keeps its streak: `lamp` holds over a literal floor,
    and its 2 s clock is not restarted by someone else's re-measurement.
    """
    s = _warmup_then_block_one(backend).advance().feed(2, 20.0)
    boundary, after = s.chunks_in_phase(2)

    # The restarted dwell: its condition is still true (20 uV over a re-seeded
    # 5 uV), so with the streak cleared it re-earns `holds` 100 ms into the
    # boundary chunk and fires exactly one event. Without the reset it would
    # have been holding continuously since block one and fire nothing.
    assert s.audio_events[boundary] == 1
    assert s.audio_events[after] == 0

    # The hold that is nobody's business: true before the boundary, true
    # across it. A wrongful reset would need the whole 2 s back and would read
    # 0.0 here, since the chunk is 1 s long.
    assert s.taps[boundary - 1]["output/lamp"] == pytest.approx(1.0)
    assert s.taps[boundary]["output/lamp"] == pytest.approx(1.0)


def test_a_short_reseed_window_keeps_the_old_value_and_records_the_miss(backend):
    """Deliberately asymmetric with warm-up seeding, which fails closed. At a
    re-seed there IS a known-good value — the one that has been running all
    session — so killing the session is not proportionate. One second of block
    one against a two-second window: the threshold stays where the warm-up put
    it and the attempt is on the record."""
    s = _session(backend).feed(3, 2.0).feed(1, 5.0).advance().feed(1, 5.0)
    assert s.report["value"] == pytest.approx(2.0)          # unchanged
    assert s.report["status"] == "seeded"                    # not a failure
    assert s.report["history"][-1]["status"] == "insufficient_samples"
    assert s.report["history"][-1]["new_value"] is None
    assert s.report["history"][-1]["old_value"] == pytest.approx(2.0)
    assert s.taps[-1]["threshold/thr"] == pytest.approx(2.0)


def test_a_reseed_that_comes_up_short_resets_nothing(backend):
    """The reset belongs to the re-measurement, not to the boundary. A
    boundary that keeps the old value has changed nothing a streak was
    measured against, so taking a trainee's streak away would be a penalty
    with no cause."""
    s = (
        _session(backend)
        .feed(3, 2.0)
        .feed(1, 5.0)              # one second only: a 2 s window cannot fill
        .advance().feed(2, 20.0)
    )
    boundary, after = s.chunks_in_phase(2)
    assert s.report["history"][-1]["status"] == "insufficient_samples"
    # Block two's bundle has been holding since block one and keeps holding:
    # no rising edge, no event.
    assert s.audio_events[boundary] == 0
    assert s.audio_events[after] == 0


# ---------------------------------------------------------------------------
# The two freezes
# ---------------------------------------------------------------------------


def test_ingestion_is_frozen_while_an_artifact_inhibit_is_muting(backend):
    """An EMG-contaminated sample would push the threshold up at exactly the
    moment it must not. The gate here trips on everything above 4 uV, so the
    whole of block one is unreadable, the window never fills, and the re-seed
    keeps the value the session has been running."""
    s = (
        _warmup_then_block_one(backend, _ir(inhibit=ARTIFACT_INHIBIT))
        .advance().feed(1, 5.0)
    )
    assert s.taps[-1]["muted"] == pytest.approx(1.0)   # the gate really is open
    assert s.report["history"][-1]["status"] == "insufficient_samples"
    assert s.report["history"][-1]["fresh_samples"] == 0
    assert s.report["value"] == pytest.approx(2.0)


def test_an_unlabelled_mute_does_not_freeze_reseed_ingestion(backend):
    """The other half, and the reason the first half is about the label rather
    than about muting. An inhibit with no `kind` is a sample the engine read
    and scored against the trainee, not one it could not read — so those
    samples are part of the baseline and the window fills normally."""
    s = (
        _warmup_then_block_one(backend, _ir(inhibit=PLAIN_INHIBIT))
        .advance().feed(1, 5.0)
    )
    assert s.taps[-1]["muted"] == pytest.approx(1.0)   # same gate, same mute
    assert s.report["history"][-1]["status"] == "seeded"
    assert s.report["value"] == pytest.approx(5.0)


def test_ingestion_is_frozen_during_an_output_muted_rest_phase(backend):
    """Mirrors the `set_ingesting` freeze refrain already applies to rolling
    percentile thresholds during a mid-session rest, and the compile-time
    window check in `resolver._reseed_budget_ms` assumes exactly this: it
    leaves output-muted phases out of the time available to fill a window. A
    window that would fill only by counting rest time must not seed.

    One second of live block one, then three seconds of muted rest — four
    seconds of wall clock against a two-second window, of which only one
    second is signal anybody trained on."""
    s = (
        _session(backend, _ir(phases=(WARMUP, BLOCK_ONE, MUTED_REST, BLOCK_TWO)))
        .feed(3, 2.0)
        .feed(1, 5.0)              # 1 s live
        .advance().feed(3, 5.0)    # 3 s of output-muted rest
        .advance().feed(1, 5.0)    # block two
    )
    assert s.report["history"][-1]["status"] == "insufficient_samples"
    assert s.report["history"][-1]["fresh_samples"] == CHUNK   # the live second only
    assert s.report["value"] == pytest.approx(2.0)


def test_a_live_phase_of_the_same_length_does_fill_the_window(backend):
    """The direction that proves the test above is about muting and not about
    the shape of the phase list: the same four phases, the same four seconds,
    the same window — with the third phase live training instead of a muted
    rest, the window fills and the re-seed fires."""
    s = (
        _session(backend, _ir(phases=(WARMUP, BLOCK_ONE, LIVE_REST, BLOCK_TWO)))
        .feed(3, 2.0)
        .feed(1, 5.0)
        .advance().feed(3, 5.0)
        .advance().feed(1, 5.0)
    )
    assert s.report["history"][-1]["status"] == "seeded"
    assert s.report["value"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# A practitioner's own write
# ---------------------------------------------------------------------------


def test_a_host_write_permanently_disarms_the_reseed_too(backend):
    """If the practitioner moved a threshold by hand during block one, block
    two does not take it back. Nothing disarmed a seed latch after it had
    fired before this feature: the disarm was gated on `armed && !fired`, so a
    mid-session write left the latch armed and a re-seed would have overwritten
    the practitioner's number. The disarm is gated on the control being named in
    some block's `reseed`, which is why no existing protocol's report moves —
    see the test below."""
    s = (
        _warmup_then_block_one(backend)
        .set_control("smr_threshold_uv", 7.5)
        .advance().feed(1, 5.0)
    )
    assert s.report["status"] == "disarmed_by_host"
    assert s.triggers == ["warmup"]
    # The number the trainee is scored against is the practitioner's, not the
    # re-measurement's 5.0 — which is the whole point.
    assert s.taps[-1]["threshold/thr"] == pytest.approx(7.5)


def test_a_host_write_to_a_control_no_block_reseeds_reports_what_it_does_today(backend):
    """The pin on the gate. Extending the post-fire disarm to every seeded
    control would change `seed_report()["status"]` from `"seeded"` to
    `"disarmed_by_host"` for every existing protocol in which a practitioner
    retunes a seeded control mid-session — a host-facing report change with no
    new declaration present. With nothing re-seeding, a mid-session write
    reports exactly what it reported before this release."""
    s = (
        _warmup_then_block_one(backend, _ir(reseed=False))
        .set_control("smr_threshold_uv", 7.5)
        .advance().feed(1, 5.0)
    )
    assert s.report["status"] == "seeded"
    assert s.report["value"] == pytest.approx(2.0)
    assert s.triggers == ["warmup"]
    assert s.taps[-1]["threshold/thr"] == pytest.approx(7.5)


def test_a_host_write_before_the_warmup_seed_fires_still_disarms(backend):
    """The pre-fire disarm is untouched: a write during warm-up disarms the
    warm-up seed itself, so the history stays empty — the control was never
    measured at all."""
    s = (
        _session(backend)
        .feed(1, 2.0)
        .set_control("smr_threshold_uv", 7.5)
        .feed(2, 2.0)
        .feed(3, 5.0)
        .advance().feed(1, 5.0)
    )
    assert s.report["status"] == "disarmed_by_host"
    assert s.report["history"] == []
    assert s.taps[-1]["threshold/thr"] == pytest.approx(7.5)


def test_a_warmup_seed_that_failed_closed_is_never_re_measured(backend):
    """A failed warm-up seed is terminal, and the status has to stay honest.

    The warm-up here is the right length — three seconds for a two-second
    window — but the electrode is off for the first two of them, so the
    engine ingests 256 finite samples where it needs 512 and the seed fails
    closed: the control keeps its default and every output channel is muted
    for the rest of the session (§4.9.4). That is an ordinary clinical event,
    not a replay artefact: `fresh` and the percentile buffer count *finite*
    samples, so a full-length warm-up with a dead stretch in it fails exactly
    as a truncated one does.

    What must not then happen is the boundary quietly fixing it up. Block one
    is clean, so the buffer would refill and the re-seed would write a real
    value and flip `status` to `"seeded"` — while the trainee stays silent,
    because the session-wide mute is a separate latch. The one field a host
    reads to explain the silence would say everything is fine.
    """
    s = (
        _session(backend)
        .feed(2, float("nan"))     # electrode off: two of the three warm-up seconds
        .feed(1, 2.0)              # one good second — 256 finite samples, 512 needed
        .feed(3, 5.0)              # block one, perfectly clean
        .advance().feed(2, 5.0)    # block two: the boundary must change nothing
    )
    # 1. the status still explains the silence
    assert s.report["status"] == "insufficient_samples"
    # 2. the trainee is still hearing nothing — not one event all session
    assert sum(s.audio_events) == 0
    assert all(t["output/lamp"] == pytest.approx(0.0) for t in s.taps)
    # 3. the value never moved off "no measurement"
    assert s.report["value"] is None
    assert s.taps[-1]["threshold/thr"] == pytest.approx(9.9)   # the declared default
    # 4. one seeding event ever, and it is the failure
    assert len(s.report["history"]) == 1
    record = s.report["history"][0]
    assert record["trigger"] == "warmup"
    assert record["status"] == "insufficient_samples"
    assert record["new_value"] is None
    assert record["fresh_samples"] == CHUNK      # the one good second, counted once


# ---------------------------------------------------------------------------
# The second door: advice a practitioner accepts
# ---------------------------------------------------------------------------
#
# `set_control` is one way a practitioner sets a threshold by hand; accepting
# the advisor's suggestion is the other, and a protocol with an advisor is
# exactly the sophisticated one where re-seeding gets used. These three cases
# are the asymmetry: a practitioner's acceptance disarms, the autopilot's own
# application does not, and neither moves a protocol that declares no
# `reseed`.


def _advice_session(backend: str, *, reseed: bool = True) -> Session:
    """Warm-up, then two seconds of block one — which is where the advisor
    reaches an `adjust` on the re-seeded control."""
    return _session(backend, _advice_ir(reseed=reseed)).feed(3, 2.0).feed(2, 5.0)


def test_advice_a_practitioner_accepts_disarms_the_reseed_too(backend):
    """The practitioner did not type the number, they accepted it — which is
    the same act of responsibility, and SPEC §4.9.4 promises a re-seed never
    takes back a value a practitioner set by hand. Before this, the promise
    held at `set_control` and failed here."""
    s = _advice_session(backend)
    accepted = s.accept_advice("practitioner")
    assert accepted == pytest.approx(2.2)          # 2.0 uV, one 10% step harder
    s.feed(1, 5.0).advance().feed(1, 5.0)          # cross into block two
    assert s.report["status"] == "disarmed_by_host"
    assert s.triggers == ["warmup"]
    # The number the trainee is scored against is the accepted one, not the
    # re-measurement's 5.0 — which is the whole point.
    assert s.taps[-1]["threshold/thr"] == pytest.approx(accepted)


def test_advice_the_autopilot_applies_itself_does_not_disarm_the_reseed(backend):
    """The documented asymmetry, and it is deliberate: nobody took
    responsibility for an automatic apply, and between two automatic
    mechanisms a fresh measurement of the person beats an inference from
    reward rate. The boundary re-measures, and the advisor resumes nudging
    from the new baseline."""
    s = _advice_session(backend)
    accepted = s.accept_advice("autopilot")
    assert accepted == pytest.approx(2.2)
    # One chunk to read it back: the taps belong to the chunk last fed, and
    # the advice applied after that one.
    s.feed(1, 5.0)
    assert s.taps[-1]["threshold/thr"] == pytest.approx(2.2)   # it did apply
    s.advance().feed(1, 5.0)
    assert s.report["status"] == "seeded"
    assert s.triggers == ["warmup", "block:two"]
    assert s.report["value"] == pytest.approx(5.0)
    assert s.taps[-1]["threshold/thr"] == pytest.approx(5.0)
    # And the re-seed replaced the autopilot's value, which the trail records.
    assert s.report["history"][-1]["old_value"] == pytest.approx(2.0)


def test_accepted_advice_on_a_control_no_block_reseeds_reports_what_it_does_today(backend):
    """The half that says no existing protocol moved. Three protocols in the
    repository give a control an autopilot policy and two of those controls
    are seeded; none declares a `reseed`. For them this door stays exactly as
    it was — a practitioner accepting advice mid-session leaves the seed
    report reading `seeded`, because there is no later write to take anything
    back."""
    s = _advice_session(backend, reseed=False)
    accepted = s.accept_advice("practitioner")
    s.feed(1, 5.0).advance().feed(1, 5.0)
    assert s.report["status"] == "seeded"
    assert s.report["value"] == pytest.approx(2.0)
    assert s.triggers == ["warmup"]
    assert s.taps[-1]["threshold/thr"] == pytest.approx(accepted)


# ---------------------------------------------------------------------------
# The host clock, which is not a first-chunk marker
# ---------------------------------------------------------------------------


def test_a_frozen_clock_at_a_reseeding_boundary_fires_the_reseed_once(backend):
    """`phase_elapsed == 0` is not "the first chunk of this phase". Both
    engines skip the elapsed increment while the clock is frozen, and freezing
    the clock is a public host API (a transport pause), so a practitioner who
    paused at the start of block two would re-fire the re-seed and restart
    every downstream hold on every chunk of the pause — identically on both
    engines, so the drift gate would never see it. The marker is explicit: one
    fire per entry into the phase, pause or no pause."""
    s = (
        _warmup_then_block_one(backend)
        .advance().freeze_clock(True).feed(4, 20.0)
    )
    assert s.triggers == ["warmup", "block:two"]
    boundary, *rest = s.chunks_in_phase(2)
    # One hold restart, not four: the reward dwell re-earns its event once.
    assert s.audio_events[boundary] == 1
    assert [s.audio_events[i] for i in rest] == [0, 0, 0]


# ---------------------------------------------------------------------------
# Absence, and what a resumed session carries
# ---------------------------------------------------------------------------


def test_a_protocol_with_no_reseed_holds_the_warmup_value_all_session(backend):
    """Absence reproduces today's behaviour: the same block boundary, the same
    signal step, and the threshold stays where the warm-up put it."""
    s = _warmup_then_block_one(backend, _ir(reseed=False)).advance().feed(1, 20.0)
    assert s.triggers == ["warmup"]
    assert s.report["value"] == pytest.approx(2.0)
    assert s.taps[-1]["threshold/thr"] == pytest.approx(2.0)


@pytest.mark.skipif(
    RUST_BACKEND_ACTIVE,
    reason="counts ingestion work in the Python latch's own buffer; the Rust "
           "core owns its latches and the Python ones are never stepped under "
           "the rust backend, so this would pass vacuously there. The "
           "host-visible half of the same property is "
           "test_a_protocol_with_no_reseed_holds_the_warmup_value_all_session, "
           "which both backends really run.",
)
def test_a_protocol_with_no_reseed_does_not_ingest_after_the_latch():
    """Absence must reproduce today's behaviour exactly, including the cost.
    A protocol that declares no `reseed` must not keep feeding a percentile
    buffer for the rest of the session — the per-chunk work has to stay where
    it was, not merely the per-chunk answer."""
    s = _warmup_then_block_one("python", _ir(reseed=False))
    latch = s.ev._seed_latches["control/smr_threshold_uv"]
    seen_at_fire = latch.buffer._seen
    s.advance().feed(3, 20.0)
    assert latch.buffer._seen == seen_at_fire
    assert latch.fresh == 0


def test_a_resumed_session_starts_its_reseed_buffers_empty(backend):
    """Out of scope by design, documented rather than hidden: `export_state()`
    carries the adaptive trackers behind `percentile` / `auto_range` across
    sessions, and it does not carry a re-seed buffer. So a resumed session
    measures its first re-seed window from this session's signal only.

    The first half of the test is there to keep the second half honest: it
    shows the round trip really did carry the rolling tracker, so the miss
    below is the re-seed buffer starting empty and not the resume doing
    nothing at all."""
    ir = _ir(rolling=ROLLING_THRESHOLD)
    first = _warmup_then_block_one(backend, ir).advance().feed(1, 5.0)
    assert first.report["history"][-1]["status"] == "seeded"
    state = first.ev.export_state()
    assert state["rolling.percentile"]["n_eff"] == WINDOW_SAMPLES

    resumed = _session(backend, ir, seed_state=state)
    assert resumed.ev.export_state()["rolling.percentile"]["n_eff"] == WINDOW_SAMPLES
    resumed.feed(3, 2.0).feed(1, 5.0).advance().feed(1, 5.0)
    assert resumed.report["history"][-1]["status"] == "insufficient_samples"
    assert resumed.report["history"][-1]["fresh_samples"] == CHUNK


# ---------------------------------------------------------------------------
# The two engines, side by side
# ---------------------------------------------------------------------------


def _both_backends(build) -> tuple[dict, dict]:
    """Run `build(backend)` on each engine and return (python, rust) reports.

    Parametrizing over `backend` asks each engine the same question in
    separate runs; this asks them in one, which is the only way a test can
    assert the two agree rather than that each is self-consistent.
    """
    pytest.importorskip(
        "refrain_core",
        reason="refrain_core wheel not installed — build with: "
               "cd refrain-core && maturin develop --release",
    )
    return build("python").report, build("rust").report


def test_the_reseeded_value_is_bit_identical_across_backends():
    """Not `approx`: the re-seeded value is a number a trainee is scored
    against, computed by two implementations of the same percentile over the
    same window, and the drift gate holds them to bits."""
    py, rs = _both_backends(
        lambda b: _warmup_then_block_one(b).advance().feed(1, 5.0)
    )
    # Both re-seeded, before asking whether they agree: two engines that both
    # did nothing also "agree", and this test passed on that reading before
    # the feature existed.
    assert py["value"] == pytest.approx(5.0)
    assert py["value"] == rs["value"]
    assert py["at_time_s"] == rs["at_time_s"]
    assert py["history"] == rs["history"]

    # And again with a multiplier, because `scale` is a second arithmetic step
    # on this path and the committed golden vector declares none: every other
    # cross-engine comparison here covers the plain fire, so without this the
    # multiplier would only ever be checked one engine at a time, each against
    # its own `approx`. 5.0 * 0.5 is exact in IEEE-754, so this is an equality.
    half = 0.5
    py_scaled, rs_scaled = _both_backends(
        lambda b: _warmup_then_block_one(b, _ir(scale=half)).advance().feed(1, 5.0)
    )
    assert py_scaled["value"] == pytest.approx(2.5)
    assert py_scaled["value"] == rs_scaled["value"]
    assert py_scaled["scale"] == rs_scaled["scale"] == half
    assert py_scaled["history"] == rs_scaled["history"]


def test_seed_report_history_agrees_across_backends():
    """The whole trail, record for record: a warm-up seed, a re-seed that
    fires, and a re-seed that comes up short, across a five-phase session."""
    def build(b):
        return (
            _session(b, _ir(phases=(WARMUP, BLOCK_ONE, BLOCK_TWO, MUTED_REST, BLOCK_TWO)))
            .feed(3, 2.0)
            .feed(3, 5.0)
            .advance().feed(1, 5.0)       # re-seeds
            .advance().feed(3, 5.0)       # muted rest: nothing accumulates
            .advance().feed(1, 5.0)       # comes up short
        )

    py, rs = _both_backends(build)
    assert [h["trigger"] for h in py["history"]] == ["warmup", "block:two", "block:two"]
    assert [h["status"] for h in py["history"]] == [
        "seeded", "seeded", "insufficient_samples"
    ]
    assert py["history"] == rs["history"]
    assert py == rs
