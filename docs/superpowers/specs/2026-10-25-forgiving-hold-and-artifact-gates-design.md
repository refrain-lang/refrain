# Forgiving holds, artifact gates, and block re-seeding

- **Date:** 2026-10-25
- **Tracking:** REC-23 (the Linear ticket for this work)
- **Stage:** Stage 2 of the protocol feedback roadmap in refrain-protocols,
  `docs/superpowers/specs/2026-10-21-protocol-feedback-roadmap-design.md`
- **Target version:** refrain 0.25.0 (one PR, one release)

## Why this exists

The migration to `band_envelope` made refrain's envelopes much faster and, as a
side effect, much less forgiving. The roadmap's own table shows what that cost:
reward time collapsed across the live corpus — `smr_classic_baseline_staged_cz`
from 27.5 % to 8.9 %, `alpha_theta` from 39.9 % to 0.7 %, `high_beta_down` from
10.3 % to 0.4 %. Published trials that produced learning sat at 25–40 % reward
time (Arns 2014; the blinded ADHD trial at ~40 %; Nam & Choi 2020, where only
the ~80 % group learned). The corpus is now an order of magnitude below the
range anything is known to work in.

Measurement during design found where a large slice of that went. The old
four-stage chain (`bandpass -> hilbert -> magnitude -> smooth`) carried an
implicit dip tolerance nobody declared: with `tau = 250 ms` it would ride
through roughly 250 ms of total rhythm dropout without the envelope falling
below half its plateau. `band_envelope` at 360 ms latency rides through about
150 ms. The migration bought 208 ms of latency (568 ms down to 360 ms) and
silently paid about 100 ms of dip forgiveness for it.

So the headline change here is not a new idea. It makes explicit, and
controllable, a property the engine already had and lost.

The second change is a bug in disguise, and it takes a different shape on each
of refrain's two hold paths. Of the 45 live protocols, 44 put the hold in
`reward { event = dwell(...) }`; three also bind holds inside `output { }`
(`alpha_theta_narrowband`, `alpha_theta_narrowband_melodic`, `smr_classic_cz`).

- **In an `output` binding**, the mute is AND-ed into the hold's condition
  (`src/refrain/eval_.py:1813`), so a false sample zeroes the streak. A swallow
  wipes banked progress.
- **In `reward { event = ... }`**, no eligibility mask is applied at all. That
  dwell is stepped in `_eval_reward_event` (`eval_.py:1883`) on the raw
  condition, and that runs *before* `muted` is even computed (`eval_.py:1241`).
  The clock runs straight through the mute; the event is masked at the output
  stage instead (`gated_bool = values.astype(bool) & ~muted`, `eval_.py:1308`).

The second case is the one that matters, and it is worse than the first.
Measured: a genuine 2 s burst with a swallow 100 ms in, 250 ms hold, mute from
2.80 s to 3.65 s. The rising edge fires at 2.945 s — inside the blind window —
and the output gate eats it. `dwell` does not re-arm, so that burst never
produces another event. The trainee loses the reward for two full seconds of
good work and cannot tell why, because they hear the same gap in the tone
either way. On the `output`-bound path the same burst rewards at 3.898 s;
under the proposed pause rule, at 3.797 s. Pausing fixes both paths by moving
the rising edge out of the window where nobody can hear it. Paluch 2017 found 7 of 14 beta up-trainers learned to clench;
Bussalb 2019 found signal quality and artifact handling predicted outcome where
protocol choice did not. Scoring an unreadable moment as a failure both spends
reward budget we are trying to recover and teaches the wrong thing.

## What ships

Five items. Four add an optional declaration to the language; one is
verification of behaviour that already exists.

| # | Item | Shape |
|---|------|-------|
| 1 | `tolerance:` on `dwell`, `dwell_rearm`, `hold_for` | new optional argument |
| 2 | An inhibit declares what kind of inhibit it is; artifact gates pause the hold clock instead of resetting it | new optional field |
| 3 | A block may re-seed named controls at its boundary | new optional field |
| 4 | A per-person muscle-mute level seeded at warm-up | composable today, plus a new optional `scale` field on the seed rule |
| 5 | A mute silences the continuous output | already implemented; lock it with tests on both backends |

The last one shrank during design, because the capability turned out to be
present already:

- **Item 5 is done in the engine.** `src/refrain/eval_.py` gates every
  continuous output channel with `gated = np.where(muted, 0.0, clamped)`, and
  event channels with `values & ~muted`. What is missing is a test that says so,
  on both backends, at the `output/<channel>` tap. Note separately that
  `suppress_on_mute` on a `feedback` layer is carried into the IR JSON and into
  the Rust IR struct but is acted on by neither engine — it is a declaration for
  the downstream player, not an engine behaviour. That asymmetry should be
  documented, not quietly fixed here.
- **Item 4 needs no new primitive**, only one new field on the existing seed
  rule (`scale`, see §4). An inhibit metric may name a derive
  (`metric = rectify("emg_envelope")`, as `examples/critical_fluctuation_cue.refrain`
  does), an inhibit threshold may be an absolute against a control
  (`threshold = absolute(55 uV2)`, as `bench/protocols/micro_09_inhibit.refrain`
  does), and a control may carry `seed = percentile { from = "<derive>"; window;
  target_pct }`. Composing those three gives a per-person, warm-up-seeded,
  absolute muscle limit today. The autopilot restriction on controls that feed an
  inhibit (`apply = "auto"` is refused) does not apply to seeding.

## Goals

1. A dip short enough to be an artifact of the envelope rather than a real
   interruption does not break a hold, and the interrupted burst still earns the
   one reward it would have earned.
2. Time the engine could not observe is scored as neither success nor failure.
3. A protocol can hold a trainee's own measured muscle level as an absolute
   limit, set once at warm-up.
4. A protocol can ask for its thresholds to be re-measured at a block boundary,
   and every such re-measurement is recorded.
5. Every existing protocol compiles to byte-identical IR and behaves
   identically. All three new declarations are optional, and absence reproduces
   today's behaviour exactly.
6. The Python and Rust engines stay bit-identical, and the IR version gate
   refuses a document an older engine would silently mis-run.

## Non-goals

Each of these was considered during design and set aside for a stated reason.
They are not oversights.

| Not doing | Why |
|-----------|-----|
| A second, lower threshold line ("two-line threshold", the ticket's "and/or") | Measurement killed it. Through a 360 ms envelope a total dropout of 50 / 100 / 150 ms lands at 81 % / 67 % / 51 % of plateau. A lower line at 0.8 / 0.67 / 0.5 of the upper **is** a 50 / 100 / 150 ms tolerance — the same feature in a worse unit, because the ratio shifts with band and with latency while the time does not. One time parameter expresses it and travels between protocols. |
| A depth floor on the tolerance (forgive only shallow dips) | Unreachable. Depth follows duration through the envelope, so for the 50–150 ms range we care about, a depth condition is the duration condition with extra parameters. |
| A spindle-detector primitive, or counting spindles in the engine | Costly and not needed for feedback. The user's position: "I'm not sure I care about being able to report spindles, I care more about effective feedback." |
| Changing any protocol file | Stage 3. The agreed split is infrastructure now, protocols later. |
| Shortening declared latency below the bandwidth-time floor | The floor is `1000 / bandwidth` ms, enforced by refrain-protocols' `tests/test_declared_latency.py`. 12–15 Hz gives a 333 ms floor, hence 360 ms. Changing it is a separate argument with its own evidence. |
| Recorder gain smoothing | Stage 2b. |
| Picking per-protocol tolerance values | See "The replay, and what it is for" below. |

## Anti-goals

Failure even if it technically works:

- **Changing what counts as a reward as a side effect.** The roadmap states
  this: "Every reward change is a recorded design decision in the protocol's
  header and in the PR." This design nearly violated it. An earlier version made
  the pause-instead-of-reset rule global, which would have altered the reward
  behaviour of every protocol that pairs a mute with a hold. A sweep found eight:
  `alpha_theta` (2 mutes, 1 hold), `alpha_theta_narrowband` (2, 4),
  `alpha_theta_narrowband_melodic` (2, 3), `alpha_up` (1, 1), `beta_attention`
  (1, 1), `high_beta_down` (1, 1), `smr` (1, 1),
  `smr_classic_baseline_staged_cz` (1, 1). The slow protocols would have been
  hit hardest: `alpha_theta` pairs a 1000 ms dwell with a delta mute whose
  release is 1 s. Hence the label, and hence the default.
- **A silent threshold change.** A re-seed that a practitioner cannot see
  afterwards is worse than no re-seed. Sherlin 2011's critique of
  auto-thresholding is the reason re-seeding is off by default and recorded when
  on.
- **Python and Rust drifting.** A tolerance implemented twice with a
  one-sample difference is worse than no tolerance, because the drift gate is
  the thing that makes every other number in this repo trustworthy.

## Constraints

From the roadmap and the repo, all still binding:

- Work against `main`. One PR, one release — refrain CI runs 12–28 minutes a
  round, so changes batch.
- `legacy/` is frozen. Retire by moving, never deleting.
- Never widen a band.
- Every live protocol stays in the editor round-trip subset unless listed in
  `KNOWN_GAPS`.
- Capture `pytest`'s exit code immediately, never after a pipe. No `-q` on
  refrain's suite.
- refrain and refrain-core are versioned in lockstep; `tests/test_version_lockstep.py`
  fails the build if the two `pyproject.toml` versions drift.

## Decisions already made

These were settled in conversation and are not open:

1. **Brief dips (~50–150 ms) should be invisible, and the interrupted burst
   counts as one success** — not two, not zero. (User, in the brief.)
2. **One time parameter, no depth floor, no second threshold line.** (Follows
   from the measurements above; accepted.)
3. **An artifact mute pauses the reward clock rather than resetting it** —
   option A of the two put to the user, chosen as "I think A makes more sense"
   and confirmed as "I like A, let's try it for now."
4. **The behaviour hangs on the inhibit, not on the engine**, as a declaration
   of what kind of inhibit it is, defaulting to today's behaviour. (Settled after
   the user raised the risk to the slow protocols: "I don't want to fuck up the
   slow protocols.")
5. **A re-seed resets the hold clock explicitly.** Narrowed from "a block
   boundary resets it" — see the calls section for why a blanket boundary reset
   would have broken the staged protocols.
6. **Block re-seeding is off by default, and every re-seed is recorded.**
7. **The muscle limit is an absolute, per-person level, seeded at warm-up** —
   not a rolling percentile. Paluch 2017's finding is about an absolute ceiling
   that interrupts training.
8. **Infrastructure in this release, protocol changes later.** (User: "so
   infrastructure here and actual protocols later.")
9. **Tolerance defaults belong to a protocol family, not to the primitive.**
   (Settled after the user's correction that the 12–15 Hz Cz protocol is not a
   general-purpose "raise 12–15 Hz somewhere" protocol, so one number cannot
   serve every family.)
10. **An artifact-labelled mute pauses the clock on both hold paths**, including
   the `reward { event = ... }` path that 44 of 45 live protocols use. The
   consequence was put to the user and accepted: when Stage 3 labels a
   protocol's mutes, that protocol's reward rate moves, and each one carries its
   before-and-after in the protocol header and the PR.
11. **A seed rule gains a `scale` multiplier**, so a muscle limit can be stated
   as a multiple of the warm-up median rather than a high percentile. Accepted
   as a fourth language change after the percentile-only form was shown to trip
   the gate by construction.
12. **The replay report does not block the 0.25.0 release**; it moves to Stage 3,
   where the per-family numbers are actually written. This keeps Stage 2 to one
   PR in one repo.

## Design

### 1. `tolerance:` on the hold primitives

```
reward {
  event = dwell(above("smr"), duration: 250 ms, tolerance: 100 ms)
}
```

Accepted on `dwell`, `dwell_rearm` and `hold_for`. A duration, default absent.
Absent means zero, which is today's behaviour exactly.

**Semantics: forgive, don't credit.** A run of false samples no longer than
`tolerance` leaves the streak exactly where it was — it neither advances nor
resets it. When a dip exceeds `tolerance`, the streak resets to zero.

The consequence worth stating plainly: a streak of N samples always means N
samples genuinely above threshold. The hold's `duration` keeps its literal
meaning, and reward-time numbers stay comparable with the roadmap's table. The
alternative — crediting dip samples toward the hold — reads more like "invisible"
but has a pathology: a condition true for one sample in every 100 ms would
accrue a 250 ms hold in 250 ms of wall clock on 10 ms of real signal. Under
forgive-don't-credit the same pattern takes 2.6 s and carries 100 ms of real
signal, and the invariant below makes that boundary case explicit rather than
accidental.

**Causality.** The rule needs no lookahead, which is what makes identical Rust
and Python implementations straightforward. While a dip is in progress the
streak is held; the moment the dip's length passes `tolerance` the streak is
zeroed. Nothing is deferred and no latency is added.

**The re-arm clock, and one ding per burst.** For `dwell_rearm`, the re-arm
countdown is held through a forgiven dip, so a dip cannot re-arm the event and
hand out a second reward for one burst. When a dip exceeds `tolerance`, the
re-arm counter is credited with the whole dip so far, counted from the dip's
first sample — the condition was genuinely false from then, and crediting only
from the moment tolerance expired would make re-arming slower than it is today.

**A forgiven dip keeps the reward sounding.** If the hold was already satisfied
when the dip began, `holds` stays true straight through it and drops only if the
dip exceeds `tolerance`. A protocol that gates continuous audio on
`reward.event.holds` — the pattern `alpha_theta` calls out by name — therefore
keeps playing the reward tone across the dip. That is what "invisible" has to
mean: if the tone drops out, the dip was not invisible, it was merely
unpunished. Holds-gated continuous output needs its own parity test, since it is
what the trainee actually hears. *(Designer's call — see the calls section.)*

**`tolerance` is a fixed duration, not control-bindable.** It follows
`dwell_rearm`'s `rearm` rather than `dwell`'s `duration`
(`src/refrain/resolver.py:3094-3099`). Two reasons: a control-bound tolerance
makes the version-gate predicate below unknowable at emit time, and
`update_control` on these implementations calls `reset()`, which would zero a
streak mid-dip. *(Designer's call.)*

**What `tolerance` forgives, and what it does not.** `tolerance` is about the
signal dipping, never about the engine being unable to see. Everything else
keeps the behaviour it has today, which is what makes this release safe:

| Why the condition is false | Forgiven by `tolerance`? | Clock |
|---|---|---|
| The envelope dipped — real signal | yes, if no longer than `tolerance` | held through the dip |
| An inhibit labelled `"artifact"` is muting | no, handled by the observability mask | held for as long as the mute lasts |
| An inhibit labelled `"trained"`, or unlabelled, is muting | no | today's behaviour, which differs by hold path |
| A phase mutes output — a mid-session rest | no | reset, as today |
| The channel is outside the active block's outputs | no | reset, as today |
| The transport clock is frozen, or a data gap | no | reset, as today, via `reset_held_outputs()` |

The second and third rows are the point of the label. A trained mute is never
forgiven by `tolerance`, so `alpha_theta`'s delta mute still bites: a trainee
drifting toward sleep is not having a brief dip, they are failing the thing the
protocol trains. *(Designer's call.)*

**Invariants for the fuzzer** (these are the specification, as much as the prose
is):

- *Monotone in tolerance.* Raising `tolerance` can only move a reward earlier in
  the sample stream or leave it where it was. It can never move one later or
  remove one.
- *No free rewards.* The number of samples genuinely above threshold behind any
  reward is at least `duration`.
- *Zero is identity.* `tolerance: 0 ms` produces sample-identical output to the
  same protocol with no `tolerance` at all.
- *Idempotent under dip-filling.* A condition stream that contains no
  sub-tolerance dips is insensitive to `tolerance`: once those dips have been
  filled in by hand, the reward times are the same as with tolerance 0.
  (The original wording, "the filled stream produces the same reward times as
  the original does with that tolerance", was found false during
  implementation: filling a dip credits it toward the streak, forgiving it does
  not. With 8 true, 2 false, 9 true, dwell 10 and tolerance 2 samples, the
  forgiving stream rewards at index 11 and the hand-filled one at index 9.)

### 2. An inhibit says what kind of inhibit it is

```
inhibit "emg" {
  metric    = rectify("emg_envelope")
  threshold = absolute(emg_limit_uv)
  action    = mute(release: 350 ms)
  kind      = "artifact"
}
```

`kind` is optional, with two values:

- `"artifact"` — the signal is unreadable right now. Muted time is unobservable:
  the hold clock is held, neither advancing nor resetting, exactly as a forgiven
  dip is held. There is no length limit on this hold; the clock resumes whenever
  the gate clears.
- `"trained"` — the trainee is doing the thing this protocol trains away from.
  Failing it is the point, so it resets the clock. This is today's behaviour.

**Absence is not `"trained"`.** An inhibit with no `kind` behaves exactly as
today (reset), but is recorded as unlabelled rather than asserted to be a
trained condition — because most of them are mislabelled artifact gates. The
catalog should be able to report how many inhibits are still unlabelled, which
gives Stage 3 a worklist. The compiler may advise on an unlabelled inhibit; it
must not refuse one.

That distinction is clinical, not bookkeeping. A clench tells you nothing about
the trainee's SMR, so scoring it is measurement error. `alpha_theta`'s delta
mute is the opposite case: rising delta means the trainee is falling asleep,
which is a real state worth scoring against them. Those two should never have
shared one behaviour, and today they do.

**Implementation, on the `output`-bound path.** The mask already exists.
`src/refrain/eval_.py:1281` builds `_hold_for_eligible` and line 1813 AND-s it
into the hold's condition; `refrain-core/src/eval.rs:430-450` is the exact Rust
twin, with the mask carried as `__hold_for_eligible` (inserted at `:1722`,
removed at `:1731`). The change is to pass two masks instead of one — a
condition and an observability mask — and have the hold implementations hold
rather than reset when unobservable.

**Implementation, on the reward-event path.** This path has no mask today, so it
needs wiring, not just a semantic change. The ordering cost is small: inhibits
are already evaluated at `eval_.py:1142-1158`, well before the reward stage at
`:1200`, so `_compute_muted` can simply move above the reward stage in both
engines. Rust has the same shape (`refrain-core/src/eval.rs:1700-1725`).

**What absence means on each path.** An unlabelled inhibit must reproduce
today's behaviour, and today's behaviour differs by path: in an `output`
binding it resets, and on the reward-event path it does nothing at all. So the
observability mask reaches the reward stage *only* for inhibits labelled
`"artifact"`. Unlabelled and `"trained"` inhibits contribute nothing there, as
now. No existing protocol changes until one is labelled.

**This is a reward change where it applies, and must be recorded as one.** The
measurement above shows a labelled artifact gate turning a lost reward into a
delivered one. That is the intended effect and the reason for the release, but
it means Stage 3's labelling pass moves reward rates on live protocols, and each
one needs its before-and-after in the protocol header and the PR.

**Which actions may carry a `kind`.** `mute` and `freeze` may: both are OR-ed
into the same output gate, so the label has a meaning. `flag` may not — it gates
nothing, so `kind = "artifact"` on a flag inhibit would be a silent no-op, and
a silent no-op in a safety-shaped declaration is worse than a compile error.
Refuse it at resolve time with a message that says why. *(Designer's call.)*

**Only `"artifact"` bumps the document version.** An inhibit labelled
`"trained"`, and an unlabelled one, run identically on an older engine, so
neither should push a document to IR 0.6 and get it refused at load. The version
predicate is behaviour-based, not syntax-based. This matters for Stage 3: when
the corpus gets labelled, the protocols whose labels are documentation only stay
on their current version. *(Designer's call.)*

### 3. Block-boundary re-seeding

```
block "two" {
  reseed = ["smr_threshold_uv"]
}
```

Absent or empty means no re-seed, which is today's behaviour. Each named control
must already carry a `seed` rule; the re-seed re-runs that same rule at the
block boundary, measuring over the rule's trailing `window` ending at the
boundary, and reusing `PercentileImpl` so the result is bit-exact across
backends — the same machinery baseline seeding shipped with in v0.15.0.

**Why a trailing window and not a fresh measurement period.** The reason to
re-seed at all is that the person has drifted over the session, so the best
estimate at the boundary is the most recent data. Measuring afresh over the
first N seconds of the new block would leave the threshold stale exactly when a
fresh one was wanted. The cost is real and should be documented: if blocks train
materially different states, the trailing window reads the previous block's
signal. That is a reason for a protocol author to leave re-seeding off, which is
the default.

**Where the samples come from, which is more work than it reads.** The
percentile buffer behind a seeded control only ingests while the evaluator is in
`warmup`, and stops for good once the latch fires
(`src/refrain/eval_.py:864-902`). At a block boundary it is empty. Re-seeding
needs it ingesting through `run` as well — and then the question is which
samples count. The rule: ingest during `run`, but freeze ingestion while an
artifact-labelled inhibit is muting and during any output-muted rest phase. That
mirrors the `set_ingesting` freeze refrain already applies to rolling percentile
thresholds, for the same reason — an EMG-contaminated sample would push an EMG
limit or an SMR threshold up at exactly the moment it must not. *(Designer's
call.)*

**A short window keeps the old value; it does not mute the session.** A warm-up
seed that cannot fill its window fails closed: status `insufficient_samples` and
output suppressed for the rest of the session (`eval_.py:896-899`). A re-seed
must not inherit that. At warm-up there is no known-good value, so failing
closed is right; at a re-seed there is one — the value that has been running all
session. Killing the remainder of a session because block two started 40 s into
a 60 s window is not proportionate. So a re-seed whose window is short keeps the
previous value, records the miss in the report, and the session continues.
*(Designer's call.)*

Alongside that, refuse at compile time a re-seed whose window cannot fit inside
the preceding block, mirroring the existing check against the warm-up phase
(`src/refrain/resolver.py:1611-1619`). Phase and block durations are numeric
literals, so this is always knowable.

**A re-seed never overrides a practitioner.** A host write to a seeded control
permanently disarms its seed today — "the clinician just took responsibility for
the value" (`eval_.py:1602-1610`). That stays true for re-seeds: if the
practitioner moved a threshold by hand during block one, block three does not
take it back. *(Designer's call.)*

**A block entered more than once re-seeds on every entry**, each one recorded.
The point is recalibration to the person as they are now, and that argument does
not weaken on a second visit. *(Designer's call.)*

**Every re-seed is recorded.** `seed_report()` grows additively: each
per-control entry keeps the shape hosts already parse — a dict in Python, a
typed `BTreeMap<String, SeedReportEntry>` in Rust
(`refrain-core/src/eval.rs:874-890`), documented at `docs/EMBEDDING.md:465`
— and carries the current value as it does now. A `history` array is added
holding one record per seeding event: time, old value, new value, source derive,
window, percentile, scale, and status. Additive, so nothing host-facing breaks
at 0.25.0. Carrying re-seed buffers across a session resume
(`export_state` / seed-state round-trip) is out of scope; a resumed session
starts its re-seed buffers empty, and that is documented rather than hidden.
*(Designer's call.)*

**A re-seed resets the hold clock** for holds downstream of the re-seeded
control — not every hold, and not at every block boundary. A re-seed changes what
counts as success, so progress banked against the old threshold is not
meaningful against the new one. "Downstream" is a dependency walk from the
control through thresholds and conditions to each hold, computed at resolve time
and baked into the IR so both engines read the identical set rather than
rediscovering it at runtime. *(Designer's call — see the calls section for why
this is narrower than the decision it came from.)*

### 4. The per-person muscle limit

**The calibration problem, and the one language change it needs.** Seeding the
limit at a high percentile of the person's own warm-up muscle level is wrong in
a way that is easy to miss: a 95th percentile gate is tripping 5 % of warm-up by
construction, and if the warm-up contains any clenching at all, the percentile
rises with it and the limit never fires. That is the failure the roadmap already
complained about — a guard that "mutes 20–26 % of the time even with no muscle
present". The roadmap's own suggestion was a multiple of the warm-up median, and
the seed rule has no vocabulary for it: `percentile { from, window, target_pct }`
is the whole language, with no scale or offset (`IRControlSeed` in
`src/refrain/ir.py`, SPEC §4.9.4).

So the seed rule gains one optional field:

```
seed = percentile { from = "emg_envelope"; window = 60 s; target_pct = 50; scale = 3.0 }
```

`scale` is a plain dimensionless multiplier applied to the statistic's result,
default 1.0, absent meaning 1.0 so existing seeds emit byte-identical IR. With
`target_pct = 50` it reads as "three times the warm-up median", which is what
makes the limit robust: the median ignores a few clenches, and the multiplier
puts the limit clearly above normal activity instead of inside its upper tail.
The seeded value is still clamped to the control's declared range, so a scale
cannot push a limit outside what the protocol declared as sane.

The rest of the item is a worked fixture protocol plus tests that lock the
composition in place, so Stage 3 can rely on it:

```
derive "emg_envelope" = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)

controls {
  emg_limit_uv = voltage {
    default = 12 uV
    label   = "Muscle limit (seeded at warm-up)"
    seed = percentile { from = "emg_envelope"; window = 60 s; target_pct = 50; scale = 3.0 }
  }
}

inhibit "emg" {
  metric    = rectify("emg_envelope")
  threshold = absolute(emg_limit_uv)
  action    = mute(release: 350 ms)
  kind      = "artifact"
}
```

**Verified during design — this composition works end to end today.** The open
risk was that the seed source is a derive feeding *only* an inhibit, which could
have been folded out before the seed ever saw it. It is not:

- Every declared derive is evaluated on every chunk, warm-up included, with no
  reference to who consumes it (`src/refrain/eval_.py:1098-1101`), so the
  envelope is in `stream_values` for the seed latch to ingest.
- `_referenced_control_targets()` walks each inhibit's metric *and* threshold
  (`src/refrain/resolver.py:1561-1562`), so a control referenced only by an
  inhibit threshold counts as referenced and its seed attaches rather than being
  dropped as a dead seed (`resolver.py:1618-1620`).
- `seed.from` must name a declared derive (`resolver.py:1591`), which
  `derive "emg_envelope"` satisfies.
- `threshold = absolute(<control>)` on an inhibit is already exercised by
  `bench/protocols/micro_09_inhibit.refrain`.

So the composition itself needs no engine change. The only engine change this
item carries is the `scale` field above.

**One constraint that Stage 3 must know about, because it is harsh.** A seeded
control's window has to fit inside a timed, output-muted warm-up phase 0 or the
compiler refuses the protocol (`resolver.py:1611-1619`), and if the measurement
does not complete at run time — a short warm-up, or an early advance — the seed
fails closed by muting the output for the rest of the session
(`_seed_failed_mute`, `src/refrain/eval_.py:897-898`, applied at `:1108`). A
trainee whose warm-up got cut short hears nothing at all for the whole session.
That is defensible for a threshold, and considerably more consequential for a
muscle limit, which a practitioner might reasonably expect to degrade to a fixed
default instead. Whether fail-closed is still the right policy for a seeded
*inhibit* level is a Stage 3 protocol decision, not a Stage 2 engine change, but
the test suite should pin the current behaviour so that decision is made with
eyes open rather than discovered in a clinic.

### 5. A mute silences the continuous output

Verification only. Tests on both backends asserting that while a mute is
active, every continuous `output/<channel>` tap reads 0.0 and every event
channel reads false, and that the values resume afterwards. Also assert the
asymmetry noted above — that `suppress_on_mute` on a `feedback` layer reaches
the IR and the Rust struct but is acted on by neither engine — so the next
reader does not mistake it for a bug.

## Parity, versioning, and the editor

These carry the same weight as the hold. A tolerance that is right in Python and
one sample off in Rust is worse than no tolerance at all.

### Dual-backend parity

Every new behaviour gets a case in the backend-parametrized suite (the
`backend` fixture in `tests/conftest.py`, which threads `python` or `rust` into
`Evaluator.live`). Minimum coverage:

- A dip exactly at `tolerance`, and one exactly one sample longer. The
  off-by-one at the boundary is the likely drift, so test both sides of it.
- A forgiven dip spanning a chunk boundary, since the dip counter is the new
  carried state and chunk edges are where carried state drifts.
- `dwell_rearm` with a forgiven dip, asserting one event, and a dip past
  tolerance, asserting the re-arm counter was credited from the dip's start.
- An artifact mute spanning a chunk boundary with a hold in progress.
- A re-seed at a block boundary, asserting the seeded value matches to the last
  bit and that `seed_report()` agrees across backends.
- The `muted` tap keyset is pinned by an exact-equality parity test; if any tap
  is added, it is added to both backends in the same commit.

### IR version and the load-time gate

Per-protocol IR versioning currently reaches `"0.5"`
(`src/refrain/ir_json.py:151-189`). This release adds `"0.6"`, returned when a
declaration is present that would actually make an older engine run the protocol
differently:

- a hold with a nonzero `tolerance`;
- an inhibit labelled `kind = "artifact"` — but **not** one labelled
  `"trained"`, and not an unlabelled one, since those run identically on an
  older engine;
- a block with a non-empty `reseed`;
- a seed rule with a `scale` other than 1.0.

A new `src/refrain/schema/ir-json-v0.6.schema.json` admits those fields; older
schema files are untouched, and the load-time refusal of SPEC §9.3 keeps an
older engine from silently mis-running a 0.6 document.

A protocol using none of them keeps its current version and emits
byte-identical JSON. **That should be an explicit test**, not an assumption: the
whole corpus's IR, before and after, unchanged. It is the single strongest
guarantee that no protocol's behaviour moved, and it is what makes "infrastructure
now, protocols later" true rather than hopeful.

Bump both `pyproject.toml` versions to 0.25.0 together.

### The editor, and refrain-editor

- `src/refrain/editor/protocol-model.schema.json` gains the three fields.
  `validate_model()` (`src/refrain/editor/catalog.py:72`) validates against it;
  `tests/test_editor_model_schema.py` covers it.
- Editor round-trip: a protocol carrying all three declarations must survive
  load, edit and save unchanged, and every live protocol stays in the
  round-trip subset unless listed in `KNOWN_GAPS`.
- refrain-editor (checked out adjacent, on `main` at `aa3dae0`): resync
  `vendor/protocol-model.schema.json` and the parity fixtures via
  `npm run sync-vendor`.
- **Fix the gap found during design.** refrain-editor vendors roughly 41
  protocol models across 123 fixture files, and **not one of them is ever passed
  through `validateModel`**. `test/validate.test.ts` covers hand-written good and
  bad cases; `test/describe-export.test.ts` validates a single inline protocol.
  Add a test that loops every vendored fixture model through `validateModel`.
  This is what the ticket's "fixtures validated against it" asks for, and it is
  currently not happening at all.

## The gate

Nothing merges until all of these pass:

1. refrain's full suite, exit code captured immediately and not after a pipe, no `-q`.
2. The dual-backend drift gate.
3. Fuzzer scenarios for the new hold, covering the four invariants in §1.
4. The whole-corpus byte-identical IR test.
5. An editor round-trip over a protocol using all three declarations.
6. refrain-editor: vendored schema and fixtures resynced, and every vendored
   fixture model validated against the vendored schema.
7. `tests/test_version_lockstep.py` green with both versions at 0.25.0.
8. A test for each row of the forgiveness table in §1, on both backends — the
   table is the contract, so an untested row is an unspecified one.
9. A test that holds-gated continuous output keeps sounding through a forgiven
   dip, on both backends.
10. A test pinning the warm-up seed's fail-closed behaviour, so the asymmetry
    with a re-seed's keep-the-old-value rule is deliberate on the record rather
    than discovered in a clinic.

## The replay, and what it is for

The ticket asks for the default tolerance to be chosen by replaying sessions
with refrain-protocols' session report tool (`python -m tools.session_report`).
**It is not a blocker for this release** — it moves to Stage 3, where the
per-family numbers are actually written. Two notes on why.

**It produces evidence, not a default in the primitive.** The primitive's
absence-means-zero default is what keeps every existing protocol untouched, and
one tolerance value cannot serve every family — that was settled after the
correction that a 12–15 Hz Cz protocol is not a general-purpose band-raising
protocol. So the Stage 2 deliverable is a committed replay report giving, per
protocol family, the reward time at a sweep of tolerance values against real
sessions. Stage 3 writes the chosen numbers into protocol headers as recorded
decisions. The starting point for the sweep is ~100 ms, which is roughly what
the old chain was already giving away.

**It has an unmerged dependency.** `tools/session_report/` is not on
refrain-protocols' `main`. It lives on branch `session-report-plan-a55318f5`
(commits `d12b360`, `8c6e283`, `55f39a4`, `bcedc8a`, `cda005a`), which added
`tools/session_report/{__main__,metrics,muscle,truth}.py`. Its `true_episodes()`
in `truth.py` finds true target episodes for any protocol's reward condition,
which is the function the sweep needs. Merging that branch is a second PR in a
second repo, which is why the replay does not gate a release constrained to one
PR. Separately, `lib/sessions/` in refrain-protocols holds only a README, so the
recordings to sweep are not in the repo — locating them is a Stage 3 prerequisite
and an open question for the protocol owner.

## Calls I made, so you can check them while you read

Thirteen questions came back from the builder review that a sensible builder
could settle without changing what gets built. I settled them rather than
sending them all back. Each is marked in place above; this is the list, with the
two that deserve a second look first.

1. **A re-seed resets the hold clock, not every block boundary.** This narrows
   decision 5. Every reward bundle is stepped on every chunk today, precisely to
   keep dwell timers warm across a block switch (`src/refrain/eval_.py:1217-1229`),
   so a blanket boundary reset would change what `smr_classic_baseline_staged_cz`
   and `beta_focus_staged_fz` already do, with no new declaration present — the
   one thing goal 5 forbids. Worth your eye because it is the only place I
   narrowed something you decided.
2. **A short re-seed window keeps the old value instead of muting the session.**
   This deliberately breaks symmetry with warm-up seeding, which fails closed.
   Worth your eye because it is a safety-shaped choice: I judged that a
   known-good running value makes failing closed disproportionate, but you know
   the clinical stakes better than I do.
3. A forgiven dip keeps `holds` true, so holds-gated audio keeps sounding
   through it.
4. `tolerance` is a fixed duration, not control-bindable.
5. `tolerance` forgives signal dips only; mutes, rests, inactive channels, clock
   freezes and data gaps keep today's behaviour.
6. `kind` is legal on `mute` and `freeze`, refused on `flag`.
7. Only `kind = "artifact"` bumps the document to IR 0.6.
8. Re-seed buffers ingest during `run`, frozen during artifact mutes and
   output-muted rests.
9. Compile-time refusal of a re-seed window that cannot fit its preceding block.
10. A re-seed never overrides a control a practitioner moved by hand.
11. A block entered twice re-seeds both times, each recorded.
12. `seed_report()` grows additively with a `history` array; cross-session
    resume of re-seed buffers is out of scope.
13. The `scale` multiplier applies to any statistic and the result is still
    clamped to the control's declared range.

## Left to the builder

- The exact spelling of the three new declarations, if `tolerance`, `kind` and
  `reseed` read badly in context. The semantics above are the contract; the
  surface syntax is not precious. Note that `kind` is already used internally
  for placement controls and for `action_kind`, though not on an inhibit's DSL
  surface.
- Where the dip counter lives in each implementation, and how it is carried
  across chunk boundaries — as long as both backends carry it identically.
- Whether the compiler advisory on an unlabelled inhibit is worth the noise in
  this release, or waits for Stage 3 when there is a worklist to act on.
- The shape of the committed replay report, and which sessions it covers.
- Test file placement and naming, following the existing conventions.

## Evidence appendix

All synthetic unless stated, at 256 Hz, 12–15 Hz band, 13.5 Hz carrier, measured
during design.

**Dip depth follows dip duration** (envelope value as a fraction of plateau,
after a total rhythm dropout of the given length, through a 360 ms
`band_envelope`):

| Dropout | 25 ms | 50 ms | 100 ms | 150 ms | 300 ms | 500 ms |
|---------|-------|-------|--------|--------|--------|--------|
| Fraction of plateau | 92 % | 81 % | 67 % | 51 % | 17 % | 1 % |

**The old chain's implicit dip tolerance** (longest total dropout that never
takes the envelope below the stated threshold):

| Configuration | vs 50 % of plateau | vs 70 % of plateau |
|---------------|--------------------|--------------------|
| Old chain, `tau = 250 ms` | ~250 ms | ~140 ms |
| Old chain, `tau = 500 ms` | ~410 ms | ~220 ms |
| `band_envelope`, 360 ms | ~150 ms | ~80 ms |
| `band_envelope`, 260 ms | ~110 ms | ~70 ms |
| `band_envelope`, 160 ms | ~70 ms | ~40 ms |

**Reachable hold per burst** (time a single burst spends above a 50 % threshold):
at 360 ms latency a 150 ms burst never crosses at all, and a 250 ms burst yields
exactly 250 ms — zero margin against a 250 ms hold. At 260 ms latency a 150 ms
burst yields 121 ms; at 160 ms, 148 ms. Rewarding short bursts argues for a
shorter hold, and possibly for revisiting latency later.

**A swallow 100 ms into a genuine 2 s burst** (250 ms hold, mute from 2.80 s to
3.65 s, traces from the actual primitive implementations rather than drawn).
This is the measurement that corrected the design's original premise:

| Hold path | Rising edge fires | Trainee hears |
|---|---|---|
| Today, `reward { event = dwell }` — 44 of 45 protocols | 2.945 s, inside the blind window | nothing at all |
| Today, hold bound in `output { }` — 3 protocols | 3.898 s | 3.898 s |
| Proposed pause rule | 3.797 s | 3.797 s |

On the dominant path the clock is never reset by a mute; it runs through, the
rising edge lands where nobody can hear it, and because `dwell` does not re-arm
the burst never produces another event. Two full seconds of good work, no
reward, no way for the trainee to know why. Pausing moves the edge out of the
blind window on both paths.

**On the muscle learner.** Under the pause rule a clench banks nothing, because
an artifact-labelled mute holds the clock rather than letting it climb on
contaminated signal — and muscle does leak into 12–15 Hz (Goncharova 2003;
Whitham 2007). Today, on the dominant path, the clock *does* climb through a
clench; the clench earns no delivered reward only because the event it triggers
fires inside the mute window and is masked, which also consumes the burst's one
event. Protection comes from the mute firing at an absolute per-person level,
never from the reset.

**Literature anchors.** Belinskaya 2020 (latency: a change at 244 ms, none at
744 ms). Paluch 2017 (7 of 14 beta up-trainers learned to clench; an absolute
per-person muscle limit that interrupts training). Sherlin 2011 (reward a
~0.25 s spindle; critique of auto-thresholding). Arns 2014 (25–40 % reward
time). Nam & Choi 2020 (only the ~80 % group learned). Bussalb 2019 (EEG quality
and artifact correction predicted outcome; protocol type did not). Goncharova
2003 (forehead muscle peaks at 20–30 Hz). Whitham 2007 (scalp power above 20 Hz
falls 10–200x under paralysis). Strehl 2014 (continuous information plus a
separate reward). Galang 2025 (43 of 45 studies used continuous feedback).
BrainMaster's sustained-reward default of 500 ms plus a refractory period; patent
US 11116440, "preferably 100 to 600 milliseconds".

**Open question in the review, unresolved by evidence.** Open question 7 of
`docs/research/2026-10-21-feedback-design-by-protocol.md` (in refrain-protocols)
reads: "Mute against inhibit for muscle. No trial compares pausing all feedback
with withholding reward." No trial settles the pause-versus-reset choice. It was
made on credit-assignment grounds, and the label in §2 is what keeps it
reversible per protocol.
