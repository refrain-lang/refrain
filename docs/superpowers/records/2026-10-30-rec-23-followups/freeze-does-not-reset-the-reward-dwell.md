# Follow-up: a transport pause does not restart a reward event's own dwell

**Status:** open question. No code change in 0.25.0, deliberately. Written because
fixing the Critical in the final review (`final-review.md` C1) left the behaviour
coherent in two of three places and this is the third.

**Reach of the measurements below:** both engines, at 256 Hz, on protocols built from
`tests/_inhibit_fixtures.py`'s `reward_event_protocol` and on hand-written variants for
the derive / component / inhibit-metric surfaces. Every number is a first-rising-edge
sample index read off `output/chime` or `output/fb`, with and without a
`set_clock_frozen(True)` / `(False)` pair at a chunk boundary mid-streak. Python backend
and Rust backend run the same protocol source through the same chunking.

---

## What a pause does now, surface by surface

After the 0.25.0 fix, the two engines agree everywhere. What they agree on is not one
rule:

| Where the hold is written | A freeze or `reset_held_outputs()` | Both engines? |
|---|---|---|
| `hold_for(...)` bound in `output { }` | restarts it | yes |
| `dwell(...)` / `dwell_rearm(...)` bound in `output { }` | restarts it | yes |
| `hold_for(...)` nested in a reward event's condition | restarts it | yes, as of this release |
| `hold_for(...)` in `reward { continuous = ... }` | restarts it | yes |
| `hold_for(...)` in a weighted composite's component `signal` | restarts it | yes |
| `hold_for(...)` in a derive formula or an inhibit metric | restarts it | yes |
| the `dwell` of `reward { event = dwell(...) }`, top-level or per-bundle | **leaves it running** | yes |

The last row is the incoherence. Measured, on
`reward { event = dwell(condition: above("level","t"), duration: 250 ms) }` with an
always-qualifying signal and a freeze at sample 64: the first chime lands at sample **63
with the freeze and 63 without it**, on both backends. The freeze is invisible. On the
same protocol with a 125 ms `hold_for` nested in the condition, the first chime moves
from 94 to 158 — the nested hold restarts, the dwell above it does not.

So three things are true at once:

1. A hold bound in `output { }` dies on a pause. Always has.
2. A hold nested inside a reward condition dies on a pause. Since this release, in both
   engines.
3. A reward event's own dwell survives a pause, in both engines, and always has.

## What the principle implies

"Forgive, don't credit" says a streak of N samples means N samples genuinely above
threshold. `tolerance` forgives a dip *in the signal*; a transport pause is not a dip,
it is an absence — the samples that would have come next did not come. A streak standing
when the transport stopped and still standing when it resumes was built partly before a
gap of unknown length. Nothing about where the hold is written changes that, which is
exactly the argument that settled the Critical (ruling R64). Applied to row 7 it says
the reward dwell should restart too.

The counter-argument that would make row 7 principled rather than accidental is about
what a pause *is*: a practitioner pausing the transport to adjust an electrode has not
decided the trainee's two-second burst did not happen, and a reward dwell is the thing
that pays the trainee. Zeroing it takes away work already done. But that argument
applies with equal force to an `output`-bound `hold_for`, which has been zeroed since
before this release — so it is not the reason row 7 reads as it does. Row 7 reads as it
does because `reset_held_outputs` was written to reset *outputs*, and the reward event's
dwell was never in that set.

## What changing it would cost

- **The blast radius is almost the whole library.** `reward { event = dwell(...) }` is
  the path 44 of the 45 live protocols drive feedback from. Every one of them would
  change what a session delivers after a practitioner pause, with nothing in their
  source asking for it.
- **No declaration gates it.** The four declarations this release added are each opt-in,
  and `docs/SPEC.md` §7.4 is explicit that an unlabelled protocol's reward timing does
  not move. A reset here would move it with no label, which is the one thing the release
  set out not to do.
- **It would be invisible to the fixture corpus.** No golden vector drives
  `set_clock_frozen`, so the change would land green and show up in a clinic.
- **The reward dwell's state is partly unobservable anyway.** Where a pause lands while
  a nested hold is mid-streak, the nested hold's restart already zeroes the dwell above
  it on the next sample, because the dwell's condition goes false and its tolerance is
  zero. So the two answers coincide on the shape the new test uses, and a test that
  distinguished them would need a reward dwell over a condition with no hold in it.

## If it is taken up

The code is small on both sides, which is the reason to decide it on its merits rather
than on cost:

- **Python** (`src/refrain/eval_.py`, `reset_held_outputs`): the loop matches
  `impls.HoldForImpl` only; the reward event's `DwellImpl` would have to be looked up
  from `ir.reward.event` and each `ir.reward_bundles[...].event`, the same way the
  output arm looks up `ir.output`.
- **Rust** (`refrain-core/src/eval.rs`, `reset_held_outputs`): change
  `event.reset_condition_holds()` to `event.reset_holds()` in the two reward arms. Both
  methods already exist — `reset_holds` is what a re-seed uses, and it resets the
  event's own dwell precisely because a re-seed moves the control the hold reads.
- **The test that would pin it**: a `backend`-parametrized case with a reward dwell over
  a bare `above(...)` condition — no nested hold — a pause mid-streak, and the first
  rising edge asserted at `pause + duration - 1` instead of `burst_start + duration - 1`.
  Without the "no nested hold" part the two answers coincide and the test cannot see its
  subject.

If it is declined, §7.4's "What a freeze reaches" table is already the honest statement
and needs nothing further.
