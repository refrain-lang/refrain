# Changelog

All notable changes to Refrain are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/) — minor
bumps are additive; major bumps may break compatibility.

## [Unreleased]

### Fixed

- **A bare number no longer takes the first letters of the next field name as
  its unit.** `;` between assignments is optional and whitespace is ignored,
  and the `UNIT` terminal matched a unit spelling wherever one could start,
  including at the front of an identifier. So the ordinary one-field-per-line
  layout

  ```refrain
  seed = percentile {
    from       = "emg_envelope"
    window     = 90 s
    target_pct = 50
    scale      = 3.0
  }
  ```

  parsed as `target_pct = 50 s` and a field named `cale`. The seed rule
  resolved with `scale = 1.0`, the limit was written at the warm-up median
  instead of three times it, and the inhibit it fed muted the session from
  start to finish, with no error or warning at any stage. The same happened
  to any field starting `s`, `C`, `min`, `ms`, `Hz` or `uV` that followed a
  unit-less number: `seed` became `eed`, `steepness` became `teepness`,
  `min_step` became `_step`, `Cz_gain` became `z_gain`.

  A unit is now a whole word. `50` followed by `scale` is the number and the
  field, on one line or two; `50 s` is still fifty seconds; and `5 seconds`,
  which used to be `5 s` and a stray `econds`, is a parse error where it is
  written. No `.refrain` file in this repository, in `refrain-protocols` or in
  the recorder parses differently — the hazard had been dodged by field order
  and semicolons, which is the part that could not last.

  Two resolver checks close the path that made it silent, whatever a future
  grammar does:

  - **A seed block refuses a field it does not define.** `cale`, or a
    misspelled `scale`, is a `ResolveError` naming the field and listing
    `from`, `scale`, `target_pct` and `window`. An autopilot block already
    did this, which is why the same mis-parse there failed loudly.
  - **`seed.target_pct` refuses a literal in a unit other than `%`.** The
    evaluator reads the literal's value and never its unit, so `50 s` ran as
    the 50th percentile.

  A protocol that relied on either leniency stops compiling. The first is a
  typo that was changing nothing; the second is a number that was already
  being read as a percentile.

  Recorded as open in
  `docs/superpowers/records/2026-10-30-rec-23-followups/grammar-ambiguity.md`.

## [0.27.0] — 2026-11-28

### Added

- **`band_envelope(kind: "weighted")`: the same accuracy at about half the
  declared latency, as an opt-in second design.** The question was whether a
  different filter moves the whole accuracy/latency curve, since within the
  shipped design every millisecond given back costs accuracy, smoothly, and
  the worst recording pays about three times what the median does.

  It does. Measured on 12 resting recordings (OpenNeuro ds003478, Cz against
  linked mastoids, eyes open and closed, four held-out minutes each), against
  a zero-phase reference of the same band, r as median / worst:

  | Declared | 12–16 Hz, default | 12–16 Hz, `"weighted"` |
  |---|---|---|
  | 160 ms | 0.598 / 0.480 | 0.942 / 0.912 |
  | 200 ms | 0.719 / 0.635 | 0.964 / 0.952 |
  | 240 ms | 0.813 / 0.765 | 0.970 / 0.966 |
  | 320 ms | 0.914 / 0.897 | 0.971 / 0.963 |
  | 400 ms | 0.955 / 0.944 | 0.977 / 0.970 |

  Recording by recording, the weighted design reaches each one's own default
  accuracy at 320 ms by 180 ms at the latest (median 140 ms), and the worst
  case no longer falls away faster than the median. The same holds at
  12–15 Hz, against a brick-wall reference, and on eyes-open and eyes-closed
  stretches separately.

  **The default does not change, and no existing protocol moves.** The
  weighted design is not uniformly better: its accuracy plateaus near
  r = 0.97–0.98 where the default keeps climbing, so past about two
  bandwidth-periods of latency the default measures better (from 260 ms at
  22–30 Hz, 360 ms at 13–19 Hz, about 520 ms at 12–16 Hz). It also gives up
  exact linear phase, a far stopband of −65 dB or lower (−31 to −61 dB
  instead, depending on latency) and a step response with no overshoot
  (5–8% of the step).
  `docs/PRIMITIVES.md` has the table.

  Two things the measurement said that the name does not:

  - **It is not the subject's spectrum.** The candidate going in was the
    paper's wcFIR, weighted by each subject's own PSD. Weights from the
    subject's training minute, from the other eleven subjects, and from a
    data-free `1/f²` model give medians within 0.01 of each other from 160 ms
    up (0.02 on the worst recording). So
    the design uses the model, which also bakes at resolve time where a
    measured spectrum cannot. The primary source agrees once read for it:
    Smetanin et al. report cFIR and wcFIR as "comparable" on alpha envelope
    correlation, at the declared delay, as a mean over ten subjects with no
    worst case — a different quantity from the one this project gates on, and
    not evidence either way for SMR.
  - **Most of the gain was never about weighting.** The default design tapers
    symmetrically about the delay tap, which throws away every tap after it —
    the filter's memory, which costs no latency. Keeping them takes 320 ms
    from 0.914 to 0.965 with no weighting at all. That is also why earlier
    sweeps found accuracy flat in the tap count: the extra taps were zero.

  The weights carry a white floor meeting the `1/f²` term at 20 Hz. Without
  it, 20 µV of added 60 Hz took the 160 ms envelope from 0.943 to 0.872
  (worst 0.792); with it, 0.925 (0.890).

  `"weighted"` refuses a latency under half a bandwidth-period (125 ms on a
  4 Hz band), where the output would lag by 25–50 ms more than declared.

  **No IR-JSON version change.** `kind` selects the taps Python bakes; both
  engines read them off the wire, so a core that ignores the argument runs
  the identical filter. A weighted protocol emits v0.5 like any other
  `band_envelope`, the Rust core is untouched, and
  `micro_16_band_envelope_weighted` is the golden vector that pins it. A
  protocol that does not write `kind` emits the IR it always did: every
  pinned protocol holds its structure and coefficients
  (`tests/test_ir_corpus_identity.py`), and no committed golden vector moved
  (`refrain-core/tools/verify_fixtures.py`). The Rust half of the equivalence
  gate was not run where this was written — no Rust toolchain — so
  `micro_16`'s cargo test is first exercised by CI.

  **Not yet measured:** the file-I/O corpus the 320 ms default was chosen on,
  including its one hard recording (`c008`); a dry-electrode amplifier; and
  whether a shorter delay at equal fidelity changes what a trainee learns.
  The twelve recordings here are one laboratory's, clean by construction, so
  their worst case is a mild one. The editor does not model `kind`: a
  protocol that writes it is out of the editor's subset, as one with explicit
  `taps` already is.

## [0.26.0] — 2026-11-24

### Added

- **`exempt` on an artifact inhibit: name the output channels its label does
  not pause.** The observability mask was one array handed to every output
  channel and to the reward stage, which made `kind = "artifact"` a
  protocol-wide statement. Two consumers of the same contaminated sample can
  want opposite things, and in the SMR spindle protocol they did: the reward
  criterion must not bank it, while an onset detector bound in `output { }`
  should still fire on it, because a burst rising through an alpha intrusion
  is still a burst rising. One declaration could not give them different
  answers.

  Measured on the real corpus, that cost 63 of 358 spindles. Labelling theta
  and alpha as artifacts took the criterion from 24.4 rewards/min to 17.1
  against a true spindle rate of 13–15 — the honest number — and dropped the
  detector from 67.0% of spindles caught to 55.2%, because the detector's own
  dwell froze in exactly the samples the label covered. On one recording, at
  identical 28.7% muting, the label alone cut raw detector fires from 189 to
  139; with `exempt = ["audio_chime"]` the same recording gives 189 again,
  matching a reference protocol carrying no label at all.

  ```
  inhibit "alpha" {
    action = flag()
    kind   = "artifact"
    exempt = ["audio_chime"]
  }
  ```

  **Absent means pause everything**, which is every protocol written before
  this and the conservative reading. The list is therefore a *denylist*: a
  channel added later pauses unless someone says otherwise, where an allowlist
  would silently stop pausing for anything new. `exempt` requires `kind =
  "artifact"`, since nothing else pauses a clock; `[]` is refused as a second
  spelling of the default; every entry must name a bound channel whose binding
  actually contains a `hold_for` / `dwell` / `dwell_rearm`. A channel bound to
  `reward.event` carries no hold of its own — the reward stage ran before the
  output loop and takes the shared mask — so exempting it would do nothing and
  is refused with that reason. The reward stage itself is never exempt: it is
  the consumer the label exists to protect.

  `exempt` frees the clock, never the audio. Under a `mute` or `freeze` the
  channel is still gated while the gate is up; its streak simply advances
  behind a closed gate. Legal, and narrower than it reads.

  A protocol with a non-empty `exempt` emits **IR-JSON 0.8**. This one is a
  dropped key of the plainest kind: an engine ignoring the field pauses every
  channel, so the detector the author freed freezes in exactly the samples
  they declared it should fire through. The 0.8 schema is a byte-for-byte copy
  of 0.7 — the key is additive — and the version exists only so an older
  engine refuses the document instead of mis-scoring it. `describe_protocol`
  reports a protocol carrying `exempt` as out of its subset rather than
  re-rendering it without the field, for the same reason.

- **`kind = "artifact"` is now legal on a `flag()` inhibit: pause a hold's
  clock without silencing the output.** `action` decides what happens to the
  audio; `kind` decides how the sample is scored. Those are two independent
  questions, and until now the second only worked when the answer to the first
  was "silence it" — `_compute_artifact_muted` built the observability mask
  from the gates `_compute_muted` had recorded, and it records none for a flag.
  So marking a sample unobservable and cutting the tone were the same act, and
  a protocol could not ask for one without the other. SPEC §4.6 now carries the
  full 2×2.

  **Why a protocol wants the new cell.** Contamination comes in two kinds. A
  jaw clench is not-brain: an ungated tone tracks raw amplitude, so a clench
  would arrive as a loud reward, and EMG must stay a `mute` with `kind =
  "artifact"` — pause the clock *and* cut the audio. Alpha intruding on an SMR
  band is brain signal that merely makes the reading unreliable; there the
  right declaration is "do not credit this sample, and do not silence the
  output either", which had no spelling. On one real-EEG protocol the four
  guards were gating two-thirds of the session, and an onset trigger bound in
  `output { }` lost a third of its events to gates that existed only to stop
  the *reward* crediting a contaminated sample.

  An artifact flag takes no `release`, and `flag()` still accepts no arguments:
  a release hangover exists to stop an audio gate chattering across its
  threshold, and a flag has no audio gate. A mask that dithers costs a hold
  nothing, because freezing only pauses. That is also the one observable
  difference from an artifact mute — the mute's hangover extends its mask, the
  flag's ends with the artifact — and both engines reproduce it sample for
  sample.

  `kind = "trained"` on a flag is **still** a resolve-time error, and for the
  original reason. A trained label asserts the sample resets a hold, which is
  what an unlabelled inhibit bound in `output { }` already does; a flag changes
  neither the gate nor the scoring, so there the label really is the silent
  no-op the check was written to catch.

### Changed

- **IR-JSON gains v0.7, which is a byte-for-byte copy of v0.6 but for the
  version string.** This is the first version bump that is not about a field
  the older schema has no slot for. Both `kind` and the flag action exist in
  0.6, so a 0.6 document carries the pair perfectly — and a 0.6 engine
  deserializes every byte of it and still runs a different protocol, because
  through 0.25.x it refused the pair at resolve time and builds its artifact
  mask only from inhibits that gate output. It would leave a hold running
  through exactly the samples the author declared unreadable, which is the
  failure the label exists to prevent. The version is the only thing that can
  carry that difference, so an artifact-labelled flag emits 0.7 and nothing
  else does. An unlabelled flag stays on whatever version it had: it modifies
  nothing on any engine that has ever existed, so moving it would be a refusal
  for documentation.

### Known gaps

- **The editor cannot round-trip an artifact flag yet.** `inhibit.artifact` in
  the editor catalog renders `action = mute(release: ...)` and nothing else, so
  a protocol declaring `flag()` with an artifact label falls out of the editor
  subset. It fails *loudly* — `describe_protocol` reports "artifact inhibit
  action is flag, not mute; this block renders only mute(release: ...), so
  accepting it would re-render the inhibit as a mute" — so nothing is silently
  dropped or silently rewritten. A new catalog block is wanted before any
  practitioner-editable protocol uses the pairing.

  A catalog block alone would not be enough for the protocol that motivated
  this change, and that part is **not** caused by it. Those guards read an
  existing derive through the `linear(..., slope: 1)` affine identity rather
  than computing a `bandpower`, and `inhibit.artifact` hardcodes the
  `bandpower` metric too. Checked on an artifact **mute** with a `linear`
  metric — no flag anywhere in it — and the matcher does not refuse cleanly,
  it raises `AttributeError: 'NoneType' object has no attribute 'value'`,
  which `describe_protocol` reports as the `reason` string. So the editor owes
  that shape a clean refusal before it owes anything a new block.

## [0.25.1] — 2026-11-19

**A release-infrastructure fix. No change to the engines, the language, the IR
or any protocol — 0.25.0 and 0.25.1 compile and run identically.** Cut because
0.25.0's Release page is missing two of its eight artifacts and cannot be
completed in place.

### Fixed

- **The macOS release artifacts were never built, and the release reported
  success anyway.** GitHub retired the `macos-14` runner image on 2026-11-02.
  A retired label does not queue and does not fail: the job is *refused*, with
  zero steps run, which surfaces as `cancelled`. Both macOS jobs — the
  `universal2` `refrain_core` wheel and the iOS `RefrainCore.xcframework.zip` —
  were refused on every attempt, so **v0.25.0 published 6 of its 8 assets** and
  its Release page looks finished. The three `runs-on: macos-14` sites (the
  wheel matrix and the `ios-xcframework` job in both `release.yml` and
  `mobile.yml`) now name `macos-15`.

  **Why that mattered rather than being untidy:** a Mac installing 0.25.0 gets
  the pure-Python `refrain` wheel and finds no matching `refrain_core` wheel,
  because the only one published is manylinux or win_amd64. `nf/backend.py` in
  the recorder selects the Rust backend whenever `refrain_core` imports, so the
  host silently falls back to the Python backend — the version-mismatch
  condition that produces NF backend failures looking like code defects, with
  nothing warning. iOS hosts get no 0.25.0 xcframework at all.

- **`publish` now ends the run red when an expected asset is absent.** It still
  runs with `if: always()` and still uploads whatever was built, which is
  deliberate — a partial release beats none when a single builder regresses.
  What was wrong is that a partial release looked complete. The job now checks
  the uploaded set against the eight expected artifacts, writes a GitHub error
  annotation naming each one missing, and exits non-zero. Verified against real
  data: silent on v0.24.0's complete set, and on v0.25.0's actual set it names
  exactly the `universal2` wheel and the xcframework.

**If you pinned 0.25.0 on macOS or iOS, move to 0.25.1** — not for a behaviour
change, but because 0.25.0 has no artifact for your platform. On Linux and
Windows the two releases carry identical builds and there is no reason to move.

## [0.25.0] — 2026-10-30

Four optional declarations — a forgiving hold clock, a label that makes
artifact-muted time unobservable, a multiplier on a baseline seed, and a
re-measurement at a block boundary — plus the IR-JSON `"0.6"` wire version they
share.

**All four are off by default, and a protocol that declares none of them is
unchanged.** Same compiled IR, same `refrain_ir_version`, same output sample
for sample on both engines. That is pinned rather than asserted:
`tests/test_ir_corpus_identity.py` compiles every protocol under `examples/`
and `bench/protocols/` (39 files; 34 that existed at 0.24.0, the 4 new benches
below, and `examples/othmer_ilf_cz_pz.refrain`, which needs an import resolver
and does not resolve standalone) and holds the 34 pre-existing ones to the
structure and the 5005 filter coefficients that **the v0.24.0 compiler itself
produces for the same files** — not to a recorded number, to the previous
release's own output. All 34 match: structure exactly, coefficients within
1e-9 relative. Of the committed golden vectors in
`refrain-core/tests/fixtures/`, only `advisor_trace.json` changed, and only by
gaining the new bench's trace.

**One correction to how that is said.** Through this release's first
publication the paragraph above claimed the IR is identical "byte for byte,
same `content_hash`". The guarantee is real but those two words overstate its
reach: the IR embeds scipy-designed filter coefficients at full double
precision, and `content_hash` is a `sha256` over that serialization, so **the
same protocol compiled on a different CPU architecture produces a different
`content_hash`.** This is not new in 0.25.0 and no behaviour depends on it —
both engines read the coefficients off the wire rather than re-deriving them,
which is why they stay sample-identical. But if you treat `content_hash` as a
portable identity for a protocol — a cache key, a conformance-bundle check, a
published hash a third-party runtime validates against — it holds only within
one platform. `docs/CONFORMANCE.md` does not yet say so; a fix that makes the
hash portable is tracked and will move every published hash once, so it is not
being slipped into a patch.

**If you arrived here from `unsupported refrain_ir_version: "0.6"` on a 0.24
runtime,** one of four things in your protocol asked for 0.6. The emitter tags
the lowest version that can represent the protocol, and it keys on the
*behaviour you declared*, not on the syntax you typed — so writing one of these
fields is not by itself enough to move the version:

| Bumps the document to `"0.6"` | Does **not** bump it |
|---|---|
| A hold whose `tolerance` bakes to **one or more samples** at the document's `sample_rate_hz` | A `tolerance` that rounds to zero samples at that rate: it forgives nothing, emits no field, and leaves the version where it was |
| An inhibit labelled `kind = "artifact"` | `kind = "trained"`, and an inhibit with no `kind` at all — both run identically on an engine that has never heard of the field, so refusing those documents would be a refusal over documentation |
| A seed rule whose `scale` differs from `1.0` | An omitted `scale`, and an explicitly written `scale = 1.0` |
| A block whose `reseed` list is non-empty | An absent `reseed` — no key is emitted, so every staged protocol written before this field keeps its bytes |

The fix is to upgrade the runtime or drop the declaration. There is no
downgrade that keeps the behaviour: an engine that ignored these keys would run
a measurably different protocol and say nothing, which is the whole reason the
load-time version gate exists. `SUPPORTED_IR_VERSIONS` in
`refrain-core/src/ir.rs` now reads `"0.1"` through `"0.6"`.

### Added

- **A hold can forgive a brief dip: `tolerance` on `dwell`, `dwell_rearm` and
  `hold_for`.** A duration literal with a time unit; it cannot bind a control.
  **Default: absent, which is exactly `0 ms`** and must produce sample-identical
  output to a hold with no `tolerance` at all. A run of consecutive samples on
  which the condition is false, no longer than `tolerance`, leaves the streak
  neither advanced nor reset; the first sample of a false run that exceeds
  `tolerance` resets it to zero. Forgiven samples are never credited toward
  `duration` — the streak counts only genuinely true samples — and where the
  hold was already satisfied, `.holds` stays true across the forgiven dip, so
  holds-gated continuous output keeps sounding through it instead of dropping
  out and back in. For `dwell_rearm` the re-arm counter does not advance on a
  forgiven sample, and on the first sample that breaks the tolerance it is
  credited with the whole false run counted from its first sample. The
  conversion is `round(tolerance_ms / 1000 × rate)` and, unlike `duration`, is
  *not* raised to a minimum of one sample. `tolerance` must be shorter than
  `duration`, checked at resolve time unless `duration` binds a control, where
  the comparison is not knowable; if you bind `duration`, keep the control's
  declared range above `tolerance`, because a `duration` retuned to at or below
  `tolerance` yields a hold that once satisfied never drops — every dip long
  enough to end it is first long enough to be forgiven. Implementations are not
  required to refuse that. No lookahead required, and the two engines are held
  sample-for-sample. SPEC §5.6, IR-JSON §3.4, bench
  `micro_14_tolerance.refrain`.

- **Artifact-muted time is *unobservable* rather than failed: `kind` on an
  inhibit.** `kind = "artifact"` says the inhibit fires because the engine could
  not read the signal — electrode pop, jaw clench, movement — rather than
  because the trainee did the wrong thing. A hold takes two per-sample inputs:
  a condition, which carries every reason a sample did not qualify, and an
  observability mask, which carries only whether an artifact-labelled inhibit
  was gating. The mask is checked **first**, before the condition is examined,
  so a sample the engine could not read scores as neither success nor failure:
  the streak and the dip counter both freeze. The ordering is the feature. An
  artifact mute also shows up in the ordinary mute gate, so such a sample
  arrives with its condition already false, and a mask merely AND-ed into the
  condition would let an artifact mute longer than `tolerance` reset the streak
  through the dip path. This applies on the output-hold path and on the
  `reward { event = ... }` path, including a hold nested inside a reward
  condition. **Default: absent.** Absence is *not* `"trained"` — it is recorded
  as *unlabelled*, because most unlabelled inhibits are artifact gates nobody
  has labelled yet; an unlabelled inhibit resets a hold bound in `output { }`
  and contributes nothing on the reward-event path, exactly as before. A
  compiler may advise on an unlabelled inhibit and must not refuse one.
  `print_cred_nf` now names each inhibit as `artifact`, `trained` or
  `unlabelled`, which is where a catalog-wide count of still-unlabelled
  inhibits is read from. `kind` is legal on `mute` and `freeze`, and a resolve
  error on `flag`, which gates nothing — there the label would be a silent
  no-op, and a silent no-op in a safety-shaped declaration is worse than a
  compile error. An unrecognised value is a resolve error too. SPEC §4.6,
  IR-JSON §3.5, bench `micro_15_artifact_gate.refrain`.

- **A limit can be set at a multiple of the person's own baseline: `scale` on a
  seed rule.** `seed = percentile { from, window, target_pct, scale }`, where
  `scale` is a positive dimensionless number literal multiplying the statistic
  before the control is written. **Default: `1.0`** — the statistic as measured,
  which is what every existing seed rule does. Not control-bindable: a value
  written once at the warmup→run edge could only be retuned before it had any
  effect or after it was too late to have one. A non-number, a number carrying a
  unit, and any value at or below zero are each resolve errors; zero is not
  clamped to something harmless, because `scale = 0` seeds a muscle limit at
  0 µV and mutes the whole session. The point is the combination
  `target_pct = 50; scale = 3.0` — "three times this person's warm-up median".
  A high percentile *is* its own trip rate: a gate seeded at the 95th percentile
  of warm-up trips on 5 % of warm-up by construction, and if the warm-up
  contains any of the thing being guarded against, the percentile rises with it
  and the limit never fires. `bench/protocols/seed_scale_emg.refrain` is the
  worked per-person muscle limit. `seed_report()` reports `value` already
  multiplied, with `scale` beside it, so a practitioner is never shown a scaled
  limit as a raw measurement. **The range clamp attaches to `scale`, not to
  seeding:** a seeded value is clamped to the control's declared `range` only
  when `scale != 1.0`. An unscaled seed is written through unclamped, as it
  always has been — there is no such clamp today, and adding one would move the
  seeded value, and therefore the reward behaviour, of a protocol that declared
  nothing new. SPEC §4.9.4, IR-JSON §3.6.

- **A staged block can re-measure a seeded control at its boundary: `reseed` on
  a `block`.** A list of seeded control names that re-measure themselves, from a
  trailing window of the signal that just ran, when the block goes active —
  instead of training the whole session against a number taken from the warm-up.
  **Default: empty, which is what every protocol written before this field says,
  and means nothing re-seeds.** Read the "off unless declared" paragraph in SPEC
  §4.13 before turning it on: re-seeding assumes the window before the boundary
  is a fair baseline for what the next block trains, and when consecutive blocks
  train materially different states — beta-up then alpha-up — the trailing
  window measures the *previous* state, so an alpha threshold seeded from the
  tail of beta training sits wherever beta training left alpha. A fixed,
  warm-up-seeded threshold is the conservative choice. Checked at resolve time:
  the control exists and has a `seed`; the seed was not folded out as dead; no
  duplicates; and the seed's `window` fits the unmuted training time before the
  boundary, refusing only when *every* phase that activates the block is short
  of the window, and standing down when a live `mode = "open"` phase makes the
  amount unknowable. A re-seed whose window has not filled keeps the value the
  control is already running and records the miss — it is not a session-ending
  fault. A re-seeded value is scaled and clamped by the same rules as a warm-up
  seed, and is live for the chunk the boundary falls in, not one chunk later.
  **A re-seed also restarts the hold clock** for holds downstream of the
  re-seeded control, since a streak banked against the old threshold says
  nothing about the new one. That set is resolved once in the front end and
  carried on the wire as `reseed_holds`, so both engines reset identical holds
  rather than each walking the dependency graph. Its reach is stated as reach
  rather than as a list of shapes, and three things are deliberately outside it:
  a control that reaches a hold only through a `derive` is not in the set; a
  site is a whole expression, so two holds in one reward event or one output
  channel are reset together even if only one reads the control; and a hold
  written into a reward's `continuous` rather than its `event` is not a site and
  is never reset. SPEC §4.13 names the shape that would bite first. SPEC §4.13,
  IR-JSON §3.7, bench `reseed_staged.refrain`.

- **IR-JSON `"0.6"`,** with `src/refrain/schema/ir-json-v0.6.schema.json` a
  superset of v0.5. New keys: `tolerance_samples` on a hold (emitted only when
  it bakes to one or more samples), `kind` on an inhibit (emitted only when
  declared), `scale` on a control's `seed` (emitted only when it differs from
  `1.0`), and `reseed` plus `reseed_holds` on a block (emitted together, and
  only when `reseed` is non-empty — stated with `dependentRequired` in both
  directions).

- **`seed_report()["history"]` — the trail behind a seeded value.** One record
  per seeding event, in order, warm-up seed first, each carrying time, old and
  new value, source derive, window, percentile, scale, and status. Purely
  additive: the existing keys and their meanings are untouched, and a control
  no block re-seeds gets a one-record history saying what it already said.
  Once a block can re-measure a control, "the seeded value" stops being one
  number and starts being something a practitioner can be shown.
  **This addition is Python and Rust-core only.** The uniffi `SeedReport`
  record that generates the Swift and Kotlin bindings does not carry `history`,
  so a mobile host still sees the single current value. That is a known,
  accepted gap, not an oversight, and it is the one host-facing part of this
  release that did not reach every surface.

- **The protocol editor round-trips all four declarations — in Python.**
  `src/refrain/editor/` models, validates, describes and re-renders
  `tolerance`, `kind`, `scale` and `reseed`, so a protocol carrying them
  survives a load-edit-render cycle instead of coming back with the
  declarations quietly gone. `catalog_version` 2 → 3. One refusal comes with
  it: an artifact inhibit whose `action` is `freeze(...)` rather than
  `mute(...)` is now reported **out of subset** instead of being re-rendered as
  a mute. `kind` is legal on both, so a `freeze` artifact gate is a more
  attractive thing to write than it used to be — and the editor's promise is to
  give back the source the author wrote. At runtime the two are the same gate
  (see **Fixed** below: neither engine holds the last value), so this is not a
  behaviour trap but a fidelity one: the author gets back a document they did
  not write, and the emitted IR's `action_kind` changes from `"freeze"` to
  `"mute"`. Nothing in the repository writes one, so this moves no existing
  protocol; it moves a future one from silently corrupted to correctly
  reported. Two honest limits.
  First,
  **the TypeScript editor cannot render these four and drops them silently**;
  the port is outstanding, and a guard test now fails the day a vendored
  protocol uses one, so the gap is loud rather than discovered in a session.
  "Editor support" in this release means the Python editor model and renderer.
  Second, **an explicitly authored `scale = 1.0` disappears from the
  re-rendered source.** That is inside the stated contract — `scale` is emitted
  only when it differs from `1.0`, which is exactly what keeps every existing
  protocol's IR byte-identical — but if you write the default out by hand and
  run the file through the editor, you get it back without that line. It is not
  data loss: the value is the default either way.

- **Four conformance bundles and a `skip_warmup` rule a third-party runtime has
  to honour.** New golden-vector families in `refrain-core/tests/fixtures/`:
  `micro_14_tolerance`, `micro_15_artifact_gate`, `seed_scale_emg` and
  `reseed_staged`. `docs/CONFORMANCE.md` §3 now names **four**
  `skip_warmup=False` bundles rather than two — `seed_smr_baseline`,
  `seed_exprpos`, `seed_scale_emg` and `reseed_staged` — because a seeded
  control measures its statistic only during warm-up, and `reseed_staged` needs
  the warm-up seed to have happened for a boundary re-seed to mean anything. A
  runtime self-validating one of those four must drive it with the
  lifecycle-aware entry point that advances the warmup→run cursor; the
  streams-only one never fires the seed and reads every `output/fb` sample as
  `0.0`. §1 of that document now also states the supported wire versions
  directly, instead of leaving a conforming runtime to find them in
  `docs/IR-JSON.md`.

### Changed

- **A host write now disarms a re-seeding control — permanently, and only for a
  control some block re-seeds.** This is a behaviour change, not an addition.
  `set_control` on such a control disarms its seeding for the rest of the
  session, before *or* after the warm-up seed has fired, and its
  `seed_report()` status becomes `disarmed_by_host`. Before this release
  nothing disarmed any seed latch after it fired. A practitioner who moved a
  threshold by hand during block one does not have block three take it back. For
  a control no block re-seeds, nothing moves: `set_control`'s disarm stays
  pre-fire only, exactly as it has always been. No protocol in this repository
  declares a `reseed`, so no existing protocol's report changes — but a host
  that writes controls mid-session should know the rule exists before someone
  authors one.

- **Accepted advice disarms a re-seeding control; the autopilot applying its own
  advice does not.** The second door to the rule above, and the asymmetry is
  deliberate. `apply_advice(id, by = "practitioner")` disarms the control's
  re-seeding, because the practitioner did not type that number but did take
  responsibility for it — and a protocol with an advisor is exactly the kind
  that re-seeds. `apply_advice(id, by = "autopilot")` writes the value and
  leaves re-seeding **armed**, so the next boundary re-measures and the advisor
  resumes from the new baseline: nobody took responsibility for an automatic
  apply, and between two automatic mechanisms a fresh measurement of the person
  is more authoritative than an inference drawn from reward rate. An advisor
  whose suggestion a re-seed overwrites has lost nothing it knew. For a control
  no block re-seeds, accepted advice disarms nothing, as before. The same
  distinction governs the re-seed freshness counter: it restarts when the engine
  *measures* a control, so a `set_control` or an autopilot apply moves the value
  without resetting the count.

### Fixed

- **The IR corpus-identity test only passed on the machine that wrote its
  fixture.** As first merged, `tests/test_ir_corpus_identity.py` hashed
  `json.dumps(ir_to_json_obj(...))` verbatim and pinned the result. The IR
  carries scipy-designed filter coefficients at full double precision, those
  differ in the last bits between CPU architectures, and so the test passed on
  macOS/arm64 and failed on Linux/x86_64 — on every supported Python version.
  The split was exact: of the 21 bench protocols, the 17 that embed
  coefficients all hashed differently, the 4 that embed none all matched.
  Rounding the floats before hashing was tried and rejected, because rounding
  has boundaries and a protocol with 190 coefficients gets 190 chances to land
  on one: at 12 significant digits a 64-ULP perturbation still moved 23 of the
  34 pinned protocols. The test now compares structure and values separately —
  every key, string, int, bool and float *position* exactly, through a hash of
  the IR with floats blinded out, and every float by 1e-9 relative tolerance.
  It is silent at 100,000 ULP and fires on a 1e-8 relative change. **The pinned
  values were re-baselined once, against what the v0.24.0 compiler produces for
  the same files rather than against the previous fixture**, so the guarantee
  the test exists to defend was re-established rather than reset: all 34
  pre-existing protocols match v0.24.0 on structure and on all 5005
  coefficients. Nothing in the engines or the language changed; this is the
  test, the fixture and the claim in this document.

- **A spec reversal: `action = freeze` does not hold the output at its last
  value, and never did.** Through 0.24 three sentences said it does — §4.6's
  action list ("hold reward at last value"), §7.4 ("Inhibits with
  `action = freeze` hold the previous output value") and `docs/PRIMITIVES.md`'s
  inhibit-actions list ("hold output at last value"). All three were wrong
  about both engines, from the beginning: `FreezeAction.gate` is the same
  hangover state machine as `MuteAction.gate`, and the Rust core maps
  `"mute" | "freeze"` to a single `InhibitGate`. A `freeze` inhibit zeroes
  every continuous output channel and falsifies every event channel, release
  hangover included, exactly as a `mute` does. All three sentences now say so,
  and `tests/test_eval_mute_gating.py` pins it on both backends — the claim had
  no behavioural test on either engine until now. **If you implemented a
  conformant runtime from SPEC 0.24 and made `freeze` hold its last value, that
  runtime does not match refrain's engines and should be changed.** If you
  authored a protocol with a `freeze` inhibit expecting a held tone, you have
  been getting silence; `flag()` is the action that modifies nothing.

- **A host clock freeze now restarts a hold nested in a reward condition in the
  Rust core too.** `Evaluator.reset_held_outputs()` — which
  `set_clock_frozen(true)` calls, and which `docs/PRIMITIVES.md` tells a host to
  call after an input gap — invalidates held streaks, because a streak
  accumulated across samples that never arrived is stale. The Rust core walked
  only the holds reachable from `output { }`; the Python reference walks every
  `hold_for` in the protocol. On
  `dwell(condition: hold_for(... 125 ms), duration: 250 ms)` at 256 Hz with a
  freeze mid-streak, Python delivered the first chime 250 ms later than Rust.
  The two now agree on the whole set: every `hold_for` wherever it is written
  (an `output` binding, a reward event's condition, `reward { continuous }`, a
  weighted composite's component signal, a derive formula, an inhibit metric),
  plus a `dwell`/`dwell_rearm` bound directly in `output { }`. The lines
  predate 0.25.0, but this release made the nested reward hold a specified,
  masked surface and wrote the freeze behaviour into §7.4 normatively, so the
  divergence became a conformance-visible one. Pinned on both backends by
  `tests/test_eval_artifact_mask_reward.py::test_a_transport_pause_restarts_a_hold_nested_in_a_reward_condition`.
  **Open and deliberately unchanged:** a freeze does *not* restart a
  `reward { event = dwell(...) }`'s own dwell, on either engine. That is the
  shape most live protocols use, nothing in the engines ever restarted it, and
  no declaration gates it — so changing it would move almost every protocol in
  the library at release time. §7.4's "What a freeze reaches" table states the
  three-way split rather than leaving it to be deduced.

### Documentation

- **§7.4's forgiveness table said a freeze resets "the hold", which was false
  for the dominant hold path on both engines.** Row 6 and the matching
  paragraph in `docs/PRIMITIVES.md` now say which holds a freeze restarts and
  which it leaves running, surface by surface — a conformance implementor
  building from the old row would have implemented a reset neither refrain
  engine performs.
- **§7.4 said "Three surfaces are inside" the observability mask; there are
  four.** A weighted composite's component signal (`reward "<name>"
  { signal = ... }`) is evaluated inside the same mask window as the event
  condition, a hold nested in it and `reward { continuous }`, identically on
  both engines. The count and the enumeration are now four.
- **§7.4 attributed another repository's protocol count to this one.** "44 of
  the 45 protocols in this repository" is now "in the refrain-protocols live
  corpus"; this repository has 40 `.refrain` files, 39 of them in the pinned
  corpus.
- **Two comments still said three `skip_warmup=False` fixture stems.**
  `refrain-core/tools/gen_fixtures.py` and
  `bench/protocols/seed_scale_emg.refrain` now say four, matching
  `docs/CONFORMANCE.md` §3, which already did.

## [0.24.0] — 2026-10-20

### Added
- **`sqrt()`.** Square root of a squared quantity, which makes RMS, standard
  deviation and the coefficient of variation expressible — mean-square and
  variance already composed out of `*` and `smooth`, and only the root was
  missing. One primitive, three measures, and the coefficient of variation is
  the headline diagnostic in the pelvic-floor sEMG tradition. Every exponent of
  the input's dimension must be even: `sqrt(uV2)` is `uV`, `sqrt(uV)` is a
  resolve error naming the stream, because there is no half-power of a volt to
  return. Negative input is clamped to zero rather than rooted — defined
  behaviour on both backends, because `mean_square - mean*mean` on a steady
  signal cancels to a few float epsilons either side of zero and a NaN there
  would travel down the whole feedback path. It is a pipeline stage only: a
  `formula = sqrt("x")` call hands its argument to a primitive with no
  parameter slot for it, so the stage cannot see what it is rooting, and every
  other zero-parameter primitive silently falls back to `uV` in that position
  — harmless for `rectify`, a wrong unit asserted confidently for a root. That
  shape is refused at resolve naming the pipeline spelling, which also closes
  the "resolves, then dies at the first chunk" variant. Python and Rust, held
  to sample-for-sample parity by the drift gate. Additive in IR-JSON: a
  pipeline stage is a call with a callee name, so no schema field and no
  version bump.

- **A temperature dimension, spelled `C`.** Temperature is now a base
  dimension of its own rather than a unit of voltage, with three pieces that
  only work together: the `C` surface unit, a `temperature { }` control kind,
  and `unit = "<surface unit>"` on an `input`. The last one is what makes the
  dimension reachable — every montage returns microvolts, which is right for
  an electrode and wrong for a thermopile, so a shipped skin-temperature
  protocol had to write every target in `uV` and mean degrees Celsius. It
  type-checked while lying, and the resolver printed `default = 1.5 uV` under a
  control labelled "Target rise (deg C)". That protocol can now type in
  Celsius from the input through to the threshold, and mixing Celsius with
  microvolts is caught by the dimension checks Refrain already had. An input
  without a `unit` field is microvolts exactly as before, and the temperature
  exponent is omitted from IR-JSON when it is zero, so no existing protocol's
  wire form moves. Fahrenheit is deliberately *not* a unit: Celsius and
  Fahrenheit differ by an offset as well as a scale, which an exponent vector
  cannot express, so conversion is the host's job.

  The protocol editor refuses an input that carries a `unit` declaration, as
  out-of-subset. An `input` round-trips through the editor as its montage
  alone, so the declaration would be dropped on render and the signal would
  come back as microvolts with every number unchanged — measured: the
  input's dimensions went C to uV and the derive's behind it, and nothing in
  the rendered file looked wrong. An out-of-subset protocol is visible to the
  author; a retyped signal is not. Teaching the editor to carry the unit, and
  to render a `temperature` control, is the follow-up.

- **The protocol editor can now author the flagship EEG protocols.** `smr`,
  `alpha_up`, `beta_attention` and `high_beta_down` were outside the editor's
  describable subset, so the editor silently refused the four protocols the
  product leads with. Six independent obstacles, each invisible until the
  previous one was removed: band edges given as paired `frequency` controls
  rather than literals (both in `derive` pipelines and in an artifact
  inhibit's `bandpower`), a mode-gated conditional for an inhibit's
  `target_pct`, `render_slot` having no way to emit a carried-through
  expression, and a placement control losing its authored position on render.
  The corpus of parity fixtures goes from 37 to 41 of 44 protocols.

- **`derive.envelope_declared`, the editor block for `band_envelope`.** Slots
  `band_low_hz`, `band_high_hz` (both control-bindable, because the protocols
  drive band edges from live-tunable paired controls) and `latency_ms` (not
  bindable: it sizes a filter whose coefficients are baked when the protocol
  resolves). A derive that passes `taps` explicitly is reported as out of
  subset rather than round-tripped without it. `catalog_version` 1 → 2.

- **`describe_protocol` now returns `reason`.** Out-of-subset protocols said
  only `in_subset: False`, and the deliberately broad `except` that keeps the
  editor from crashing on a resolvable protocol made a genuine bug in a
  matcher indistinguishable from a deliberate exclusion. `reason` carries a
  `_NotInSubset` message as-is and prefixes anything else with its exception
  type, so a bug reads as a bug. Two of the six fixes above were hidden by
  exactly that ambiguity.

### Changed
- **A bare number where a dimension belongs now warns at resolve, and still
  resolves.** `sigmoid("<uV stream>", midpoint: 5.0)` means 5 µV and reads as
  "five of something"; the resolver now says which unit it read it as, at the
  source line, through a new `ResolveWarning`. Deliberately a warning and not
  an error (refrain#95): 23 of the 26 `midpoint:` values the corpus ships are
  bare, so requiring the unit means editing every protocol file for no new
  capability, and a corpus-wide edit is itself a way for mistakes to enter.
  Three shipped bench protocols warn — `micro_09_inhibit`,
  `micro_11_control_expr` and `micro_12_control_param_slots` — and are left as
  they are, which is the whole point of the decision. A literal carrying the
  *wrong* unit stays an error, and a bare number against a genuinely
  dimensionless signal (`midpoint: 1.0` on a ratio derive) says nothing and
  warns about nothing.
- **Unary minus.** `steepness: -3` parses. `-3` folds in the parser to a single
  negative literal, so every slot that reads a compile-time constant accepts it
  and the unparser round-trips (it had always *emitted* `-3` for a negative
  value; re-parsing that output used to fail). `-x` on anything that is not a
  literal desugars to `-1 * x` rather than `0 - x`, because `-` is
  dimension-checked and a bare zero does not share dimensions with a microvolt
  stream. SPEC §3's EBNF still has to grow the production; DESIGN-NOTES §1.3
  records the divergence.
### Changed
- **`refrain check` now parses, resolves and steps one chunk, and says so.**
  It used to parse and stop, and print a bare `OK`. `OK` was read as a verdict
  on the protocol rather than on the parser, and that is how a protocol that
  had never run reached a downstream repository: `refrain check` said `OK`,
  `refrain fuzz` said `SKIPPED (unsupported)`, and production crashed on the
  first chunk. Parsing was not even the whole of what it claimed — it printed
  `OK` for protocols the resolver rejects on the next line. `check` now runs
  the per-file form of the property the corpus is held to in
  `tests/test_resolves_implies_runs.py`: anything that resolves must survive
  `step_chunk`, with no third outcome. Its verdict line names all three stages
  and the rate and channel count it stepped at, and there are now three
  distinct failures — `parse failed`, `resolve failed`, and `resolves but does
  not run`, the last carrying the evaluator's own message, which is the only
  thing that says which primitive refused. `check` takes `--amp` and
  `--library` like `resolve`, since resolving a protocol with an `extends`
  needs the library search path. (#97)


### Fixed

- **Two checks that were right against the tree they were written on** (#114).
  #110, #111, #112 and #113 each passed every CI check on its own branch; with
  all four merged, six of seven jobs on `main` failed. A per-PR gate cannot
  catch this, because every branch is green against its own base.

  `refrain check`'s "resolves but cannot be stepped" test (#110) used as its
  fixture an inhibit reading a derive through `passthrough()` — one of the four
  shapes #111 fixed. The protocol runs now, `check` correctly prints OK, and
  the test failed. The step failure is injected instead, which pins what
  `check` must do with any step-time failure and cannot rot the next time a
  primitive is fixed; the old protocol stays as a pin asserting it runs.

  The golden-vector drift comparison ignored the `warmup_samples` each fixture
  declares, and **the committed vectors hold values that are not reproducible
  across architectures.** A warm-started bandpass rejects DC, so on a standing
  level it sits at its steady-state response to within rounding and the first
  envelope samples are ~1e-23; `micro_05_reward`'s reward is
  `sigmoid("smr_envelope" / "smr_t")`, one dust value over another. On arm64
  that ratio is 1.14e-23/7.95e-24 and the sigmoid reads 0.783; on x86-64 it
  underflows to zero and reads `sigmoid(-3)` = 0.047. Both are correct
  arithmetic on numerical noise, which is why the gate passed on every
  developer machine and failed in CI. The comparison now mirrors `events.rs`:
  identity always compared — key sets, array lengths, types, an event's
  channel, kind and timestamp — and only values strictly inside the window
  released, with a note saying how many were not compared so a window-only
  drift cannot pass silently. 512 of 4096 samples, so 87.5% of every stream is
  still compared.

- **The editor model schema rejected the slot shape #109 introduced** (#114).
  #109 added `{"expr": "<source text>"}` — a slot the editor carries through
  rather than models — and made the four flagship EEG protocols describable
  with it, without widening `$defs.slotValue`, which allowed only a scalar or
  `{"bind": ...}`. So all four flagships described to a model `validate_model`
  rejected: a host that validates before rendering refused exactly the
  protocols #109 existed to admit. Found by the TypeScript mirror, where the
  vendored copy of the schema failed identically. It hid because every test in
  that file validated a hand-written model and nothing checked what the
  describer emits — which is now a test.

- **Four protocol shapes passed `refrain check` and then crashed when the
  evaluator was built** (refrain#94). Each one is the evaluator ignoring
  something the resolver had already worked out correctly, and each had been
  reproduced by a checked-in `xfail(strict=True)`:

  - `sigmoid(..., steepness: 0 - 3)`. Down-training wants a decreasing
    sigmoid, and with no unary minus in the grammar `0 - 3` was the only
    spelling available. It reached `SigmoidImpl` as an `IRBinaryOp` in a slot
    that reads only a literal. **Arithmetic over two numeric literals now
    folds at resolve**, so it never reaches such a slot, and the IR a host
    serializes holds the number the author meant instead of a tree the host
    would have to evaluate. (Both halves landed: see unary minus above.)
  - `absolute(value: "<a derive>")` — a threshold whose level tracks another
    stream rather than sitting at a constant. The resolver put the right
    reference in the IR; `_instantiate_call` discarded it and tried to
    `float()` it.
  - `percentile(target_pct:, window:)` as a **derive pipeline stage**, which
    makes a running percentile a signal in its own right. The resolver threads
    a stage's input in positionally, exactly as it does for `auto_range`;
    `percentile` classified that input as a static argument and then called
    `.step()` with nothing. This turns the 10th/50th/90th-percentile exposure
    measures occupational EMG work needs into first-class derives.
  - `inhibit { metric = passthrough(input: "<a derive>") }` — reading one
    derive as an inhibit's metric. An inhibit's metric has to be a primitive
    call, so there is no way to name the derive without wrapping it, and
    `passthrough` is the only wrapper that leaves the value alone (`rectify`,
    the other call the corpus uses this way, takes the absolute value). It
    resolved cleanly, with `arg input -> IRStreamRef derive/skin_rate` in the
    IR, and then died on `passthrough() requires a single-channel source (got
    3 channels)` — because the primitive was built as a *montage* over the raw
    multi-channel source rather than as an identity over the stream the
    reference names. Found while spiking a declarative contact-loss detector,
    which was built outside the protocol language instead of waiting.

  Rejecting these at resolve was considered and declined: three of the four
  are ordinary things to write, and for the `passthrough` one there is no
  other spelling, so refusing it would have removed a capability rather than
  caught a mistake.

  **Backend coverage:** the fold is resolve-time and therefore backend-neutral
  — `steepness: -3` and `steepness: 0 - 3` produce identical IR-JSON and run on
  both backends. The other three run under `backend="python"` only; the Rust
  core refuses them at evaluator construction with a catchable `RuntimeError`
  naming the fallback. See `docs/MIGRATING-TO-RUST-BACKEND.md` §2.

- **The editor could render a protocol that the language then refused.** A
  placement control with `allowed` omitted — legal, and meaning any channel —
  was described as `allowed: []` and rendered back as `allowed = []`, which
  the resolver rejects. Four corpus protocols were affected and every existing
  gate was green, because `tools/gen_editor_fixtures.py` wrote
  `render_protocol(model)` to disk without ever asking the resolver to read it
  back. The generator now re-resolves each rendered protocol and compares IR
  with the original, failing loudly on either a `ResolveError` or a mismatch,
  and takes an optional list of protocol stems that must be in the subset — a
  fixture count cannot do that job, because the total is reached with or
  without some of the fixes when a protocol is held out by a second,
  unrelated obstacle.

- **`coherence()` reported a single bin as a band average** (#92). The Welch
  segment length was `max(8, sample_rate // 4)` — a quarter-second whatever
  band was asked for, so about 4 Hz between bins at any rate at or above
  32 Hz. For the shipped `band: (8 Hz, 12 Hz)` that was **one** bin inside the
  band at 250 Hz (8.06 Hz) and **two** at 256 Hz, both of them the band edges
  with nothing between. A band narrower than the spacing and falling between
  bin centres returned exactly `0.0`, which reads as "no coupling" rather than
  "cannot be computed" — the repo's own `test_d2_narrow_eeg_bands_also_collapse`
  had this checked in for SMR (13, 15) Hz and narrow alpha (9, 11) Hz.

  The segment length now comes from the band: the **shortest** segment that
  really holds three bin centres inside it, floored at the historical 250 ms.
  Shortest matters, because every sample of segment costs segments — a
  `window` holds `window / (nperseg / 2)` of them at 50% overlap, and MSC
  averaged over `n` segments has a noise floor of about `1/n`, so a longer
  segment buys frequency resolution with dynamic range.

  **Counted, not bounded**, and the distinction is worth 1.5x the segments.
  `spacing <= W / 3` is *sufficient* — it holds whatever the alignment — but
  not *necessary*: three centres need two gaps, so `nperseg >= 2 * fs / W` is
  the weakest a segment can be, and between `2 * fs / W` and `3 * fs / W` only
  counting can say whether the bins land in the band. For the shipped
  `(8 Hz, 12 Hz)` the sufficient bound gives 188 samples at 250 Hz — 752 ms,
  first bin 0.021 Hz *below* the 8 Hz edge, 4 segments in the declared 2 s
  window. Counting gives 125 — 500 ms, bins exactly on **8, 10 and 12 Hz**,
  and 6 segments. At 256 Hz it is 128 samples, 500 ms, the same three bins,
  7 segments. So the band reported is the band the author declared, and the
  estimate is quieter. Bands wider than about 12 Hz are unaffected and keep
  their existing numbers and segment count.

  **This moves recorded numbers.** Measured on a matched input against the
  shipped behaviour (250 ms segments, one bin inside the band at 250 Hz and
  two band edges at 256 Hz):

  | scenario | 250 Hz | 256 Hz |
  |---|---|---|
  | alpha coherent across the band | 0.922 → 0.927 | 0.932 → 0.933 |
  | coupled at 11 Hz only | 0.782 → 0.726 | 0.828 → 0.752 |
  | coupled at 8.5 Hz only | 0.887 → 0.675 | 0.782 → 0.646 |

  The broadband case barely moves; the single-frequency cases move a lot and
  in both directions, which is the point — a one-bin average could not tell
  where in the band the coupling was. Segments per window go 15 → 6 (250 Hz)
  and 15 → 7 (256 Hz), so the floor rises: independent noise averages MSC
  0.160 at 250 Hz and 0.147 at 256 Hz, tracking `1/n`. Warm-up lengthens with
  the segment, to ~750 ms at 250 Hz. Python-only: `nperseg` has been baked
  into the IR since v0.1 and the Rust core reads the baked value, so no schema
  change and no IR version bump.

- **Filters cold-started at zero, so a signal with a standing offset read
  wrong for seconds** (#93). `SmoothImpl` set `_state = 0.0` and `BandpassImpl`
  / `BandpowerImpl` zeroed their `sosfilt` state, so every filter's output
  began at zero and climbed to wherever the signal was. Harmless-ish for
  zero-mean EEG; wrong for skin temperature, skin conductance or a respiration
  belt, where a "phasic" derive written as `signal - smooth(signal)` reported
  most of the tonic level as a phantom response. On a constant 33 °C input at
  15 Hz that phantom read 23.6 at t = 1 s and took 15 s to fall within 1% (276 s
  at τ = 60 s); a 4th-order 0.05 Hz high-pass rang above the standing level
  itself for 37 s.

  All three now seed their state on the first sample to the steady state that
  sample's level would hold, so a constant input gives its steady-state
  response from sample one and those transients are gone, not bounded.
  Mirrored in the Rust core (`Biquad`, `Smooth`), because the two engines have
  to warm-start identically or the drift gate fails. The report that prompted
  this issue gave settling figures roughly 30× too long; the measured ones are
  in `test_d7_cold_start_magnitudes_as_measured`.

### Changed
- **`coherence()` refuses a band too narrow to average, at resolve time**
  (#92). When the window cannot hold two segments long enough to put three
  Welch bins inside the band, the protocol is rejected with a message naming
  the band, the segment length it implies, the window it would need and the
  band width that would fit the window as written. Both shipped coherence
  protocols satisfy it; `coherence(band: (13 Hz, 15 Hz), window: 2 s)`, which
  used to be accepted and return `0.0`, now errors. The refusal ships in the
  same commit as the adaptive segment length deliberately: on its own it would
  reject the two protocols it exists to protect.

- **`coherence()`'s default `window` follows the band** (#92). `window` is
  optional and defaulted to a flat 1 s, which held four 250 ms segments. With
  the segment derived from the band a flat 1 s is *shorter than the minimum*
  for any band narrower than about 5 Hz, so a protocol that simply omitted
  `window` would have resolved and then refused to build — the
  resolves-then-crashes shape (#94) this release is trying to remove. The
  default is now the band's own minimum where that exceeds 1 s: 2000 ms for a
  2 Hz band at 256 Hz, and still 1000 ms for a 4 Hz band and anything wider,
  because counting the bins makes a 4 Hz band's segment 500 ms and two of
  those fit a flat second exactly. No corpus protocol omits `window`, so no
  recorded number moves.

- **Three equivalence tests compared values the framework already knew were
  not comparable.** `equivalence.rs` has always skipped the fixtures'
  `warmup_samples` window; `events.rs`, `taps.rs` and `set_control.rs` never
  read that field. Inside it a derive's value is the filters' own settling,
  and with the warm start a bandpass fed a standing level sits at its
  steady-state zero from sample one — correctly, and to within rounding,
  which leaves the first samples of any envelope downstream as numerical
  dust (`micro_05_reward`'s reads 1.1e-23 at sample 1). That protocol's
  reward divides one dust value by another, so its warmup values differ
  between SciPy's `sosfilt` and the Rust recurrence, and between
  architectures. Event identity — count, channel, kind, timestamp — and the
  whole tap key set are still asserted across the entire run; only values
  inside the window are let go, and a guard refuses a run where every value
  would be skipped.

- **`test_sos_unit_zi_matches_scipy` used a fixed tolerance against an
  ill-conditioned reference.** Per second-order section the warm-start state
  solves a 2x2 system whose determinant `1 + a1 + a2` is the denominator of
  the section's DC gain — 4.4e-5 for a 0.0159 Hz corner — so agreement
  between any two correct implementations is bounded by about `eps / det`,
  and SciPy reaches that bound by a LAPACK solve where the closed form
  reaches it by Cramer's rule. Measured: 1.2e-15 at det 3.9e-2, 1.4e-13 at
  4.4e-4, 1.5e-12 at 4.4e-5. The tolerance is derived from the determinant
  now, and the robust property — the filter sits at `x[0] * H(1)` from
  sample one, to 1e-15 relative on every version and architecture tried — is
  its own test.
- **The golden-vector drift gate can now fail.** `check_equivalence.py` ran
  `gen_fixtures.py` over the committed fixtures and then pointed `cargo test`
  at the result, so the regeneration overwrote the baseline the comparison was
  supposed to read. Python↔Rust parity was still proven; the golden property —
  today matches a known-good past — was not, because the expected values were
  regenerated in the same run meant to check them. Nothing read the committed
  contents, and they drifted: fifteen fixtures carried a
  `sample_rate_chosen_hz` of 2048 Hz, a rate no amp profile in the tree
  supports, across several releases with the local gate and the
  `rust-equivalence` CI job green throughout. (The field itself was corrected
  in passing by #103; the hole that hid it was not.) Regeneration and
  comparison are now separate: `gen_fixtures.py --out-dir DIR` is the
  regenerate mode, the new `verify_fixtures.py` is the verify mode, and it
  regenerates into a throwaway directory so the committed fixtures stay on
  disk to be compared against — and to be what `cargo test` is held to. CI
  gains a `fixture-drift` job so a stale baseline and a disagreeing Rust core
  are told apart at a glance, and `git status` is clean after a local gate run
  instead of training people to ignore a dirty fixture directory. Fixtures
  compare within the parity bar `equivalence.rs` already uses (atol 1e-6,
  rtol 1e-4) for floats, since they are scipy output committed from one
  machine and regenerated on another, and exactly for everything else —
  including integers, which is what the 2048 was. (#100)


## [0.23.0] — 2026-10-17

### Added
- `hold_for(condition, duration)` and `at_or_above(signal, threshold)` for
  protocol-defined held output states. The Python and Rust evaluators reset
  held states during inhibited or muted output, a frozen phase clock, and an
  explicit host-requested signal interruption. Live duration changes reset
  the affected hold.

- **`band_envelope(band:, latency:)` — a band's amplitude envelope in one
  stage, with the feedback delay as a declared parameter.** The EEG protocols
  all measure an envelope as `bandpass(order: 4) -> hilbert() -> magnitude()
  -> smooth(tau)`. Each of those four stages contributes its own lag
  additively, so the delay a person actually feels is an emergent sum that no
  author chose, no single stage is at fault, and there was no way to write
  down "this latency, best available accuracy." Four individually reasonable
  choices add up to 468 ms against a published recommendation of 250–350 ms,
  which is why this went unnoticed across ~40 protocol files rather than in
  one badly written one. The new primitive replaces the four with a single
  least-squares complex FIR whose group delay *is* the number in the source
  (Smetanin et al. 2020, *J Neural Eng* 17(4) 046022). Measured on 11 corpus
  EEG recordings at Cz, 12–15 Hz, against a zero-phase reference envelope: a
  median of **206 ms saved at equal accuracy** (range 102–266 ms); on 8–12 Hz
  at P4, a median of 265 ms. Cheaper on CPU as well, since one FIR replaces an
  order-4 SOS cascade, a 65-tap FIR Hilbert and an exponential smoother.
  Validated against the source at a matched band width and matched delay —
  r = 0.84 against the paper's reported 0.85 ± 0.1 — because at a *mismatched*
  width the same filter gives r = 0.37 and the comparison would mean nothing.
  The envelope is calibrated to the same amplitude as `hilbert() +
  magnitude()`, so an `absolute(6 uV)` threshold means the same thing before
  and after a conversion.

  Two sharp edges are handled rather than documented. The filter span must be
  at least ~2× the declared delay or accuracy collapses with no symptom other
  than the number (r = 0.873 at 2×, 0.437 at 1.4×), so `taps` is derived from
  `latency` and an undersized span is refused at build time; deriving it also
  means one protocol source works on a 250 Hz and a 500 Hz amp, where a fixed
  tap count would be accepted on one and refused on the other. And the filter
  needs its full span of valid history — about 0.8 s — before its output means
  anything, which makes the cold-start behaviour tracked in #93 slightly
  worse; a protocol with a `phase "warmup"` absorbs that for free, and one
  without will show a rising envelope for the first second, so give it one.

  Additive for existing protocols: `dwell`, `smooth` and the HRV path are
  untouched, and every protocol that does not use the new primitive keeps a
  byte-identical IR-JSON and hash. See PRIMITIVES.md "`band_envelope`" and
  DESIGN-NOTES §7b, which records why the EEG case is a different question
  from the low-sample-rate HRV case §7a settled (all of §7a's evidence was at
  4 Hz on a 0.1 Hz rhythm) without reopening it.

- **`band_envelope` runs on the portable Rust core, not just the Python
  evaluator.** Shipping it Python-first would have been the wrong place to
  stop: the recorder picks the Rust backend whenever the `refrain_core` wheel
  imports, so a Python-only stage meant the primitive would fail in the
  configuration the product actually runs — and fail by crashing the session,
  since an unrecognised compute primitive reaches a `panic!`. Filter *design*
  stays in Python, as it does for every other stage: the core receives the
  finished complex taps and runs the convolution, so the closed-form inverse
  transform, the Hanning taper and the amplitude calibration exist in exactly
  one place. The two backends are held to sample-for-sample parity by the
  existing drift gate, which now covers this stage through a new golden-vector
  pair (`micro_13_band_envelope`, deliberately the same band and channel as
  the four-stage `micro_03_envelope`) and a dual-backend test asserting that
  the Rust path realizes the latency the author declared.

- **`fir_taps_re` / `fir_taps_im` coefficient fields (IR-JSON v0.5).** Complex
  FIR taps on the wire, split into two real arrays — JSON has no complex type,
  and the pair mirrors the runtime's own complex-signal layout, so the core
  reads it without a deinterleaving pass. The schema requires the two arrays to
  appear together, since a document carrying one without the other would design
  a different filter than the emitter intended and the only symptom would be a
  wrong envelope. These ride the v0.5 bump this release already makes for typed
  feedback declarations rather than adding a version of their own; they cannot
  go in v0.4, whose `Coeffs` definition closes itself to additional properties.
  `_protocol_ir_version()` now returns `"0.5"` for any of the three features
  that need it, and a protocol using none of them still emits a byte-identical
  document at its existing version.

- **`derive.level` in the protocol editor.** Tonic-level signals — skin
  conductance, temperature — train a smoothed level rather than band power, so
  their derive is a bare `smooth(tau:)` with no band anywhere in it. The editor
  had no block for that shape and read such protocols as out-of-subset.
  Editor-catalog only — no compiler, IR, or runtime change.

- **`derive.difference_control` in the protocol editor.** A signal minus a
  control, for protocols that train a value against an author-set reference
  rather than against a percentile of its own history. Editor-catalog only —
  no compiler, IR, or runtime change.

### Fixed
- **`at_or_above` had no `###` heading in the primitive reference,** which the
  new docs/registry parity test counts as undocumented. The comparison
  section's signature block and prose already describe it; only the heading
  omitted it, so it joins the grouped `above`/`below`/`inside` heading the way
  the reference already groups siblings. Worth noting how this got in: the
  parity test and `at_or_above` arrived in different pull requests, each green
  on its own, and the pair only goes red once both are on main.

- **Stale golden-vector fixtures.** The committed `*.ir.json` fixtures carried
  `sample_rate_chosen_hz: 2048` for 15 protocols, from before the q21 amp
  profile was CANbus-capped to 256 Hz in v0.16.1. The drift gate regenerates
  fixtures before comparing, so it never noticed the committed copies had gone
  stale — they only differed from what the current emitter produces, which is
  invisible to a gate that overwrites them first. Regenerated; the only change
  is that one field.

- **Nine defects reported against v0.22.0 by an embedding application, six of
  them fixed here.** Every one was a construct that resolved cleanly and then
  did the wrong thing: `phase { pace = 0.108 Hz }` compiled and silently
  dropped the field (`_resolve_phase` and `_resolve_session` now reject unknown
  fields as `_resolve_block` already did); `above`, `below`, `inside`,
  `sigmoid`, `linear` and `absolute` did not dimension-check the slot compared
  against a signal, and `steepness` / `slope` are now required to be
  dimensionless; a derive reading a `threshold` is refused at resolve with an
  error naming the threshold and both ways out; band edges are checked against
  Nyquist for `bandpass`, `bandpower` and `coherence`; `align_to`,
  `select_channel` and `decimate` are marked absent rather than appearing to
  exist. The four that were design questions rather than defects are tracked as
  #91, #92, #93 and #94.

  Three new test mechanisms came with it: one test per reported defect with the
  unfixed ones `xfail(strict=True)` so fixing one forces its marker off in the
  same commit; a gate asserting that any protocol which resolves also survives
  `step_chunk`, over 40 protocols with both allowlists empty; and a parity gate
  requiring `PRIMITIVES.md` and the primitive registry to agree in both
  directions.

- **A control's seed no longer disappears when mode-folding leaves the control
  unreferenced.** The resolver deliberately withholds such a seed, which is
  right for the runtime — nothing should compute it — but the editor is a
  different audience. `describe_protocol` still listed the control while
  silently losing its seeding rule, so re-rendering wrote a protocol whose
  other mode had lost it for good. The editor now reads that seed off the AST
  when the IR withholds it.

- **The editor fixture corpus covered 10 of 46 protocols and said nothing about
  the other 36.** `gen_editor_fixtures.py` described every protocol with no amp
  profile, but `amp.reference` is an error without one and 35 of the 46
  protocols in the reference library use it, so they failed to resolve and were
  skipped in silence. Each protocol is now tried against no amp first — the HRV
  protocols need a tachogram channel no EEG profile has — then against each
  built-in profile, taking the first that resolves. Coverage goes to 37 of 46,
  the remaining 9 are genuinely outside the editor's subset, and anything
  skipped is printed with the reason. The amp decides only whether a protocol
  resolves, never what it resolves to: of the 34 that resolve under two or more
  profiles, all 34 produce a byte-identical model, and the generator now
  asserts that rather than trusting it.

### Changed
- **`refrain check` cost estimate** counts `band_envelope` derives. The
  envelope-band driver finds derives by looking for a `bandpass` call, so a
  protocol that adopted the one-stage primitive would otherwise have read as
  having no envelope cost at all. The new coefficient is uncalibrated and does
  not yet scale with `taps`. Composed-latency *reporting* is still absent:
  `cost.py` measures the axis running at 0.46 % of budget and says nothing
  about the one that was ~2× over the recommendation.

## [0.22.0] — 2026-09-28

### Added
- **Protocol-declared autopilot.** A protocol can now name its reward checks
  (`above(...) as "crossover"`), declare an `autopilot { }` block (target
  reward band, phases, evidence window, cadence, guard ceilings, provenance
  — `evidence` uses the protocol library's `established` / `probable` /
  `exploratory` tiers) and give each tunable control a policy (`fixed_step`, `proportional_step`,
  `rebaseline`; direction, limits, auto vs suggest). The compiler refuses
  unsafe or ambiguous policies at compile time: a knob that cannot affect the
  check it claims to fix, a reversed direction, or auto-adjusting a guard is
  a `ResolveError`, not a runtime surprise — and check names must be unique
  **protocol-wide** (not just within one bundle), because `fixes`, `limiter`
  and `tighten_first` refer to a check by name alone. Both engines run the
  same deterministic advisor and emit one structured advice result per chunk
  (`Evaluator.advice()`), with `apply_advice`, `dismiss_advice`,
  `mark_equipment_change`, `drain_advice_events` and `autopilot_policy`.
  Protocols without a policy stay manual and receive observations and
  direction hints. IR-JSON **0.4** (emitted only when the new keys are used —
  every existing protocol keeps its IR-JSON and hash). Python<->Rust parity is
  gated over tracer output, scripted scenarios and a whole session. Hosts
  embedding the advice API should read docs/EMBEDDING.md "Autopilot advice";
  protocol authors should read docs/AUTOPILOT-AUTHORING.md.
- **`advisor.rs`** — a Rust port of the Python autopilot advisor
  (`src/refrain/advisor.py`), mirrored function-for-function: same
  snake_case names, same order of operations, same message strings byte for
  byte. Verified against the Python reference over tracer output, scripted
  scenarios, and a whole staged session. (refrain-core)
- **New `Evaluator` advice accessors**, exposed over both PyO3 and uniffi:
  `advice()`, `apply_advice(id, by)`, `dismiss_advice(id)`,
  `mark_equipment_change()`, `drain_advice_events()`, `autopilot_policy()`.
  Advice stays off `last_taps()` — the strict tap key-set contract
  (`taps.rs`) is untouched. (refrain-core)
- **`RefrainError::Advice { message }`** — a new variant on the uniffi-exposed
  `RefrainError` enum, returned when `apply_advice`/`dismiss_advice` fails.
  **This is a uniffi enum change: mobile consumers (Swift/Kotlin) must
  regenerate their bindings** before picking up this release. (refrain-core)
- **IR-JSON 0.4 deserialization.** The Rust IR loader accepts the new
  `autopilot` block, per-control `autopilot` policy, and `reward.check_names`
  fields; `SUPPORTED_IR_VERSIONS` gains `"0.4"`, so a core built without this
  change refuses a `"0.4"` document at load instead of silently ignoring
  fields it doesn't understand. (refrain-core)

### Changed
- **Framing.** Refrain is described as a language for *biosignal training
  paradigms* rather than specifically clinical neurofeedback. EEG
  neurofeedback remains the most developed worked example. No language,
  IR-JSON, or embedding-API change — every existing `.refrain` file and its
  compiled IR keeps running unchanged.
- **Positioning.** The clinical-use disclaimer is now a general-wellness and
  research positioning statement: not a medical device, no diagnostic or
  therapeutic claim.
- **`montage`** is documented as one kind of signal-source binding.
  `passthrough()` is the identity binding for single-channel non-EEG sources.
  The keyword itself is unchanged.
- CRED-nf is described as one supported reporting standard rather than the
  language's reason for existing.
- Docs refer to a **practitioner** and to **the person training**, replacing
  "clinician" and "patient" across README, CONCEPT, SPEC, TOUR, PRIMITIVES,
  EMBEDDING, IR-JSON, the Rust-core host brief, and the package docstring.
- **`examples/`** no longer declare `indication`, `population`,
  `safety_monitoring`, or `outcome_measures`, matching what the reference
  protocol library distributes. Their `evidence` values now use the library's
  tiers. Citations keep their real paper titles — provenance is honest.
- `tests/test_framing.py` pins the retired category phrases *and* the retired
  role words across the living user-facing set, so neither can quietly return
  and no two docs can disagree about the same contract.

## [0.21.0] — 2026-07-26

### Added
- **Mode-conditional thresholds in the protocol editor
  (`threshold.conditional`), plus `mode` and `boolean` controls.** The editor's
  model could not represent
  `type = threshold_style == "baseline" ? absolute(...) : percentile(...)` — the
  adaptive/baseline collapse nearly every configurable protocol uses — because
  `_match_threshold` accepted only a bare `percentile(...)`/`absolute(...)` call.
  Measured across the `refrain-protocols` corpus, that single gap accounted for
  **20 of the 22** out-of-subset protocols; the subset goes from **16/38 to
  36/38** with zero round-trip mismatches. The two branches reuse the existing
  threshold node shapes, and the node carries the referenced mode's declaration:
  the resolver folds `mode == "x" ? a : b` by looking up the *declared* control,
  so rendering without it makes `resolve()` fail outright, not merely hurt
  editability. `mode` and `boolean` join `RENDERABLE_CONTROL_KINDS`
  (`boolean` renders `true`/`false`, never `1`/`0`), and modes are now threaded
  into the model so an unreferenced one is no longer dropped on render.
  Reversed-operand (`"x" == mode`) and `!=` conditions are deliberately left out
  of subset — narrower than the resolver accepts, because admitting a shape that
  round-trips lossily would silently rewrite a clinician's protocol.
  Editor-catalog only — no compiler, IR, or runtime change. Both `refrain` and
  `refrain-core` advance to 0.21.0 (version lockstep).

## [0.20.0] — 2026-07-23

### Added
- **Any-of compound rewards in the protocol editor (`reward.operant_compound_anyof`).**
  The catalog editor can now author `any_of([...])` compound rewards: a new
  `reward.operant_compound_anyof` block (identical to `reward.operant_compound`
  except the id and `any_of` in the template) plus a `describe_protocol` branch
  that routes `any_of(...)` compound rewards to it (bare-ref-continuous `any_of`
  stays out of subset). Compound condition args must now be string refs — numeric
  literals (`above("smr", 2.5)`) were silently re-quoted into refs on round-trip
  (a semantic change) and are now out-of-subset in both editor implementations.
  `any_of` already resolved in the language, so this is editor-catalog only — no
  compiler, IR, or runtime change. Enables the goal-authoring "Any of them"
  combine in refrain-editor. Both `refrain` and `refrain-core` advance to 0.20.0
  (version lockstep).

## [0.19.0] — 2026-07-20

### Added
- **Literal bipolar montages in the protocol editor (`montage.bipolar_lit`).** The
  catalog editor can now author `bipolar(plus: "T3", minus: "T4")` montages: a new
  `montage.bipolar_lit` block (slots `plus`/`minus`, template
  `bipolar(plus: {plus}, minus: {minus})`) plus a `describe_protocol` parse rule
  that disambiguates the literal form from the placement-bound `bipolar(pair: …)`
  (unchanged). `bipolar(plus, minus)` already resolved in the language, so this is
  editor-catalog only — no compiler, IR, or runtime change. Enables the
  goal-authoring "Bipolar site" surface in refrain-editor. Both `refrain` and
  `refrain-core` advance to 0.19.0 (version lockstep).

## [0.18.0] — 2026-07-20

### Added
- **Protocol editor round-trips control baseline seeds.** The catalog editor's
  `describe_protocol` / `render_protocol` pair previously dropped a control's
  first-class `seed = percentile { … }` block (shipped 0.15.0): the model carried
  no seed and the re-rendered protocol had none, silently reverting a
  baseline-seeded protocol's emitted IR from 0.3 to 0.2 — so editing such a
  protocol in the catalog UI stripped the patient's warmup-derived threshold.
  `describe` now emits a `seed` view (surfacing `from` and a bound `target_pct`
  as bare names, a literal `target_pct` as a number); `render` emits the
  `seed = percentile { from; window; target_pct }` block inside the control body;
  and `protocol-model.schema.json` documents it. Additive and editor-only — a
  non-seeded protocol round-trips byte-identically.

### Fixed
- **`refrain` / `refrain-core` version lockstep restored.** 0.17.1 bumped the
  root `pyproject.toml` to 0.17.1 but left `refrain-core` at 0.17.0, tripping
  `test_version_lockstep`; both are realigned (and advanced to 0.18.0 here).

## [0.17.1] — 2026-07-19

### Fixed
- **`brainbit_flex` amp profile now declares the full 10-20 roster.** The
  bundled profile shipped a 4-site placeholder roster (`Cz, F3, F4, Pz`), so
  resolving any protocol placed elsewhere (`C4`, `Fz`, `O1`, `O2`, …) against
  `brainbit_flex` fail-closed with "missing required channels", and the
  compiler service's per-amp bake skipped that combination — brainbit_flex
  clients silently fell back to amp-neutral IR. Because the Flex's 4 electrodes
  are user-placeable, the profile now lists the positions the device can be
  placed at: the standard clinical 19-channel 10-20 set (same nomenclature as
  `q21.json`), with `max_simultaneous_channels` (4) carrying the real hardware
  limit. `reference` stays `device`. Additive — every site that already hosted
  still hosts.

## [0.17.0] — 2026-07-19

### Added
- **Compiler service `/compile` accepts an optional `amp` profile.** The
  `amp.reference` abstraction (0.15.0) folds a protocol's montage reference to
  the connected amplifier's real reference at resolve time — but only when
  `resolve()` is given an amp profile, and the compiler service hardcoded
  `resolve(amp=None)`. An `amp.reference` protocol therefore could not compile
  through the service at all: it hit the fail-closed "requires an amp profile"
  error. `CompileRequest` and `compile_to_ir_json` now take an optional `amp`
  naming a **bundled** profile (`brainbit_flex` / `q21` / `openbci_cyton`); the
  protocol resolves against it, so `reference: amp.reference` folds to the
  device's real reference (`device` for BrainBit, `linked_ears` for Q21 and
  OpenBCI) and the site is validated against the amp's channels. The name is
  checked against the bundled profile set and never joined onto a path, so
  `..`, path separators, and absolute paths are rejected as unknown names. An
  unknown name returns a typed 400 (`{"error": "unknown_amp", ...}`), distinct
  from a protocol that fails to compile (200 + diagnostics) and a compiler bug
  (500). `meta.amp` echoes the applied profile name (or `null`), exactly
  parallel to `meta.bindings`, as the caller's capability probe — an older
  image drops the field and returns no echo, so callers fail closed. Omitting
  `amp` is unchanged: `resolve(amp=None)` as before, so every existing
  literal-reference protocol compiles byte-identically. As a byproduct this is
  the flatline fix at the compile layer: an amp-neutral protocol whose site the
  amp cannot host (e.g. an Fz protocol on brainbit_flex) is rejected with the
  resolver's "missing required channels" error at compile, instead of silently
  flatlining at session start. Known follow-up (WOR-165): the bundled BrainBit
  profile lists placeholder channels overridable at session start via
  `Evaluator.live(channel_names=...)`, so compile-time site validation uses
  those declared channels; inline per-session channel placement is a later
  enhancement.

## [0.16.1] — 2026-07-18

### Fixed
- **Q21 amp profile advertised sample rates it cannot stream.** `q21.json`
  declared `sample_rates_hz: [256, 512, 1024, 2048]`, but the Neurofield Q21 is
  CANbus-bandwidth-capped at 256 Hz (21 channels × 24 bit at higher rates
  exceeds the bus). Because the resolver picks the *highest* advertised rate at
  or above a protocol's minimum, every protocol requiring `>= 250/256 Hz`
  resolved against the Q21 to 2048 Hz — 8× the real rate. Corrected to `[256]`.
  This removes an ~64× slowdown in the protocol fuzzer (whose percentile
  stepping is O(fs²)), drops unrealistic runtime CPU, and makes q21-resolved
  coefficients bake at the rate the hardware actually runs. Amp profiles must
  declare only rates the hardware can reach; the other shipped profiles were
  already single-rate and unaffected. Note: this changes the chosen rate — and
  therefore any q21-keyed `content_hash` / baked coefficients — for affected
  protocols.

## [0.16.0] — 2026-07-18

### Added
- **First-class baseline seeding.** A control can now declare
  `seed = percentile { from, window, target_pct }` to derive its value from
  the patient's own baseline instead of a fixed default — the standard
  "set the threshold from the first two minutes of eyes-closed baseline"
  clinical pattern, expressed in the protocol itself instead of host glue
  code. During warmup the engine measures the `target_pct` percentile of the
  named `from` signal over a trailing `window`, writes the control exactly
  once at the warmup→run edge, and holds it for the rest of the session. The
  resolver validates that `from` names a real derive, refuses a window that
  can never fill inside a timed warmup phase, and drops a seed whose control
  folds out of the resolved IR entirely (dead-seed elimination). If the
  measurement can't complete — not enough warmup samples, an all-NaN input —
  the seed fails closed: the control keeps its declared default rather
  than writing a bogus value, and the session's reward output stays muted
  for the rest of the run rather than proceeding on that default. A
  clinician who writes the seeded control
  before it fires disarms the seed outright: `set_control` during warmup
  means the clinician just took responsibility for the value, and the seed
  never overwrites a live tune. Implemented in both the Python evaluator and
  the Rust core, verified at 1e-9 parity.
- **`Evaluator.seed_report()`** reports the outcome of every seeded control,
  keyed by bare control name: status (`pending` / `seeded` /
  `insufficient_samples` / `disarmed_by_host`), the value it wrote, the
  source entity, the resolved `target_pct`, the sample count and window it
  measured over, and the time it fired. Deliberately not a tap — the strict
  `last_taps()` key-set contract is untouched — and available in Python, over
  PyO3, and over the uniffi mobile bindings.
- **IR-JSON `0.3`.** A control's `seed` block (`statistic` / `from` /
  `window_samples` / `target_pct`) is emitted when the control declares one,
  and any protocol that uses baseline seeding is tagged
  `refrain_ir_version: "0.3"`. Protocols that don't use it are unaffected and
  keep emitting `"0.1"` / `"0.2"` exactly as before. `refrain-core`'s
  load-time version gate (shipped in 0.15.0) now accepts `"0.3"`.

### Fixed
- **The seed window baked to samples at the resolver's chosen rate instead
  of the actual emit rate.** Every other rate-dependent coefficient in the
  IR is rebaked at emit time; the seed window skipped that step, so
  compiling at an overridden sample rate — e.g. the compiler service's
  `sample_rate_hz` override — silently produced the wrong buffer size and a
  permanently fail-closed seed. `IRControlSeed` now carries a
  rate-independent `window_ms`, and both the emitter and the pure-Python
  evaluator bake it to samples at their actual runtime rate.

## [0.15.0] — 2026-07-18

### Added
- **`amp.reference` — montage reference from the connected amp profile.** A
  protocol may write `referential(active: site, reference: amp.reference)`; the
  resolver folds it to the connected profile's `reference` (`device` /
  `linked_ears` / `common_average` / a channel) at resolve time, producing an IR
  identical to a literal-authored one. `amp` is a new resolver namespace root
  (allow-list: `reference`). Fails closed: `resolve(amp=None)`, a missing field,
  or a non-allow-listed field is a `ResolveError`. `AmpProfile` gains an optional
  `reference` field (no schema bump); the three shipped profiles declare it.

### Fixed
- **`control_ref` in a parameter slot now uses the control's baked default**, not
  the primitive's. A recognised param-slot `control_ref` returned no value and the
  call fell back to the primitive default, silently discarding the control's own
  default from chunk 0 — so the Rust core disagreed with Python (e.g.
  `sigmoid(steepness:)` off by ~37×, and `inside(low:/high:)` panicked). (refrain-core)
- **The runtime now refuses IR newer than it supports** (SPEC §9.3). The core never
  checked `refrain_ir_version`, so an old core silently ignored fields a newer
  protocol needed; it now rejects at load with a clear diagnostic. (refrain-core)
- **An expression-position `control_ref` is live, not frozen.** It compiled to a
  constant, making `set_control` a silent no-op; it now binds over a shared cell so
  retunes take effect, matching the Python evaluator. (refrain-core)

## [0.14.1] — 2026-07-16

### Fixed
- **IR-JSON emitted `"channels": []` for any protocol that omits
  `requires.channels`**, which trips the emitter's own schema (`minItems: 1`).
  `compile_to_ir_json` reported it as `schema_error` and the server mapped it to
  an opaque HTTP 500 — the failure the Coherence portal hits on compile-on-upload
  for the BrainBit `placement_*` protocols (a `placement` control substitutes its
  bound sites into the montage at resolve time, so those protocols name their
  electrodes nowhere else). The emitter now derives the channel set from the input
  montages when `requires.channels` is omitted, via a shared
  `ir_json.effective_channels` / `montage_channels` that the synthetic source
  (v0.14.0) also uses — one derivation, both consumers. Plain single-site
  protocols with no `channels` line compile identically; nothing changes for
  protocols that do declare `requires.channels`.

## [0.14.0] — 2026-07-11

### Added
- **Montage-aware synthetic channels.** The synthetic source now generates every
  electrode a protocol's input montages name, not just the ones listed in
  `requires.channels`. A `placement` control substitutes its bound electrodes
  into the montage at resolve time, so for the BrainBit `placement_*` protocols
  — which declare no `requires.channels` at all — the montage is the only place
  those electrodes appear, and the evaluator died at construction with
  `bipolar: plus channel 'C3' not in source`. Which montage args name electrodes
  is read from the primitive registry (`PrimitiveSpec`/`ParamSpec`), so a new
  montage primitive needs no change in `synthetic.py`. Montage electrodes are
  appended after the declared channels and the ear channels, which keeps every
  pre-existing protocol's synthetic signal bit-for-bit unchanged (a channel's
  noise depends on its index in the generated block).

  On the refrain-protocols corpus this takes `refrain fuzz protocols/ --library
  lib --seed 42` from **exit 1 (2 errored)** to **exit 0 (0 errored)**, coverage
  24/38 → **26/38 fuzzed, 0 violations** — enough to turn on that repo's fuzz CI
  gate.

### Changed
- **`refrain` and `refrain-core` are now versioned in lockstep.** Both packages
  are built from the same commit by `release.yml` on every `v*` tag and are
  Rust↔Python equivalence-gated per commit, so one version number names the
  guarantee the project actually makes, in place of a compatibility matrix whose
  off-diagonal entries were never built or tested. `refrain-core` therefore goes
  0.11.0 → 0.14.0 and will bump on every release from here, including
  Python-only ones. `tests/test_version_lockstep.py` fails the build if the two
  `pyproject.toml` versions drift, and `CONTRIBUTING.md` documents the release
  procedure.

### Fixed
- A stray `## [Unreleased]` heading stranded mid-CHANGELOG since v0.4.0, which
  labelled the M5 IR-JSON wire-spec work and the `Evaluator.live` `"auto"`
  backend default as unreleased when both shipped in v0.4.0. Folded into that
  release's section.

## [0.13.0] — 2026-07-11

### Added
- **Fuzzer metamorphic tier (Tier 2).** Protocols whose reward uses a percentile
  threshold are noise-dominated: firing is decided by the noise realization, so
  sample-exact prediction is impossible. They are now gated on **same-noise-realization
  sweeps of time-in-reward**. The fuzzer fixes the noise seed and varies only tone
  amplitude, so noise is byte-identical across a sweep — a controlled A/B on one
  realization. Each reward-feeding derive gets a sweep anchored at its own decision
  level, with the other leaves held favourable, and must show a required **contrast**
  between the undriven baseline and a far-field drive. A flat sweep FAILS as vacuous
  rather than passing. Percentile single-leaf protocols are no longer skipped.
- **Direction-aware monotonicity, asserted where it is valid.** `above` leaves must be
  non-decreasing in drive and `below` leaves non-increasing — but only where the
  decision boundary sits *far above* the noise, i.e. on absolute thresholds. On a
  percentile boundary the fuzzer asserts presence-of-response, not ordering (see the
  honest limit below); the report states this explicitly rather than omitting it.
- **`refrain fuzz --seed N`** selects the noise realization. Every scenario in a
  sweep shares it; vary it to re-check a violation against another realization.
- **`tools/fuzz_corpus_gate.py`** — runs the whole protocol corpus across several
  fixed seeds and requires zero metamorphic violations and zero hollow passes.

### Fixed
- **`check_metamorphic_monotonic` was direction-blind.** It asserted non-*decreasing*
  firing for *every* swept threshold, which is sign-wrong for `below`/inhibit
  leaves. The check is now direction-aware (`above` → non-decreasing, `below` →
  non-increasing, mixed → asserts nothing and is reported, never silently passed).
- **The metamorphic metric was event count**, i.e. dwell re-triggers — a noise
  artifact that runs *backwards* in drive (measured on `micro_single_pct`:
  `[12, 16, 9, 9]`, non-monotone on 5/5 seeds; on `realistic_smr`'s `smr_t` sweep
  it produced a spurious violation from `10, 10, 9, 9`). It is now time-in-reward,
  read from the engine's `reward.event.holds` stream, monotone on 5/5 seeds.
- **The sweep amplitude ladder never straddled the decision boundary.** The fixed
  5/15/25/40 µV rungs sat entirely above the ~2.7 µV noise floor of an inhibit
  derive, so its sweep was dead flat (`7, 7, 7, 7`) — a hollow pass. Rungs are now
  anchored on each leaf's own decision level (its absolute threshold, or the
  measured per-derive noise floor for a percentile leaf).
- **`--max-scenarios` truncated sweep groups**, silently dropping rungs and
  corrupting the monotonicity comparison. Because the tests defaulted to
  `--max-scenarios 2`, the merged suite never executed a rank sweep at all. The
  cap now applies to oracle scenarios only.
- **Multi-leaf sweeps had no contrast.** Holding a percentile-`below` leaf "quiet"
  does not make it favourable — a percentile threshold tracks its own signal, so
  the leaf holds only ~p% of the time however quiet the signal is, capping the
  whole condition. Such leaves are now driven high during the fill.

### Changed
- Metamorphic-tier protocols no longer run the sample-exact oracle scenarios or the
  characterization probe. Where a percentile threshold decides firing, the oracle
  could only emit DON'T-CARE, and a DON'T-CARE that absorbs real noise-firing is a
  hollow pass rather than coverage. (`realistic_smr` fires 7 events on the quiet
  negative control; its former sample-exact "pass" was partly vacuous.)
- Multi-leaf reward conditions now validate **every** leaf. Previously only single-leaf
  rewards were classified, so a control-valued `absolute(value: <control>)` threshold
  reached the scenario generator with no resolvable decision level. Such protocols now
  skip cleanly with a feature-mapped reason.

### Known limits
- **Percentile boundaries: presence-of-response, not ordering.** A percentile threshold's
  decision level is a percentile of the trainee's own quiet envelope, so it sits *inside*
  the noise — measured at 1.24× the noise median, against ~7.2× for the absolute
  thresholds in the corpus. Adding coherent in-band drive at that level narrows the
  envelope distribution (Rayleigh → Rician) and can *reduce* threshold exceedance even as
  the mean envelope rises. There is therefore no drive amplitude that is both near a
  percentile boundary and dominant over the noise, and per-realization monotonicity across
  such a boundary is unattainable. On percentile leaves the fuzzer asserts contrast only.
  This is a property of the signal model, not of the implementation. See
  `docs/superpowers/ci/metamorphic-tier-gate-result.md`.

## [0.12.1] — 2026-07-07

Service plumbing for mode-variant baking (portal "resolve-time mode control"):
`POST /compile` now accepts `bindings` and echoes what it applied. No IR-JSON
schema changes; the no-bindings path emits byte-identical canonical IR (pinned
by test), so existing `content_hash` values do not move.

### Added
- **`bindings` on `POST /compile` and `compile_to_ir_json()`.** Passed verbatim
  to `resolve(composed, bindings=...)`, covering mode and placement controls.
  Invalid bindings surface as ordinary `stage="resolve"` diagnostics (HTTP 200).
- **`meta.bindings` echo.** The response `meta` reports exactly the applied
  bindings (`{}` when omitted). This is a capability gate for callers: pydantic
  ignores unknown request fields, so an older service image would silently
  return default-variant IR — callers baking variants must fail closed unless
  the echo matches what they sent.

### Changed
- **Unknown binding names are now rejected.** `resolve(..., bindings=...)`
  raises `ResolveError` when a key does not name a declared `mode` or
  `placement` control (previously it was silently ignored — a caller could
  mistake default IR for the requested variant). Bindings for other control
  kinds (session-time numeric controls) are rejected the same way.

## [0.12.0] — 2026-07-05

Additive — a new `mode` control that lets one protocol select a threshold's
`type` (percentile ↔ absolute, i.e. adaptive ↔ baseline) or an output binding
(gated ↔ ungated) from a value bound at resolve time. Modeled on the existing
`placement` control; the selection folds away during resolution, so the IR-JSON
schema and the Rust runtime are unchanged. No existing protocol changes behavior.

### Added
- **`mode` control kind.** `x = mode { choices = ["a", "b"]; default = "a" }` —
  a categorical, resolve-time-bound selector (never `live_tunable`), bound via
  the same `bindings=` mechanism as `placement`, validated against `choices`,
  honoring `final`.
- **Resolve-time selection folding.** A ternary whose condition compares a mode
  control to a string literal (`mode == "x" ? a : b`) is evaluated at resolve
  time and replaced by the chosen branch — wired into threshold `type` and output
  bindings. Non-mode ternaries are untouched and still resolve as runtime
  conditionals.
- **Host surfacing.** `describe_protocol` returns mode controls in a `modes`
  list; mode controls are excluded from the emitted IR-JSON `controls` map (they
  resolve away, like `placement`).

## [0.11.0] — 2026-06-21

Additive — a new mobile-binding method plus editor-catalog coverage that brings
the bundled BrainBit set to full clonability. No existing protocol changes
behavior; Python↔Rust parity (gated to 1e-6) and the IR-JSON schema are
unchanged. The editor changes are front-end catalog/model additions that
round-trip to identical IR; the mobile change mirrors an existing pyo3 binding.

### Added
- **`last_taps()` on the mobile (uniffi) binding.** Surfaces the evaluator's
  most-recent-chunk internal taps (envelope / threshold / reward / output) across
  the FFI as a Swift `[String: Double]` / Kotlin `Map`, mirroring the pyo3
  binding — for on-device clinician observation and live tuning. Regenerated the
  Swift and Kotlin bindings. Consumed by the Coherence Companion mobile
  neurofeedback feature.

### Editor catalog — full BrainBit clonability (20/20)
Front-end (refrain-editor) catalog/model additions only; each shape round-trips
to an identical IR. The bundled BrainBit set went from 3/20 → 20/20 protocols
that clone with clean IR round-trips.
- **Compound operant reward + EMG artifact inhibit + lossless `requires`**
  (3 → 11): `reward.operant_compound` (`dwell(all_of([...]))` + sigmoid
  continuous), an EMG-style `inhibit.artifact` (bandpower + percentile + mute),
  `reward.component_ratio` (weighted composite over a ratio sigmoid), and full
  preservation of `requires` (coupling/impedance/markers, not just
  sample_rate/channels).
- **Staged blocks + alpha-asymmetry derives** (→ 16): a `block "name" {...}` decl
  + per-phase `block = "..."` activation (new `blocks` model section),
  `derive.difference` (`"a" - "b"`) and `derive.rectify`, and `reward.operant_abs`
  (single-condition sigmoid-over-ref continuous).
- **Placement controls — site/montage editing** (→ 20): placement controls
  (active/set/bipolar/pair) with a new `placements` model section, inlined
  `groups`, `montage.bipolar` with `coh.a`/`coh.b` member-access slots, and a
  handful of placement-specific shapes (positional `absolute(8 uV)`,
  `combine = "all"` set reward, window-less `coherence(...)`, compound
  bare-ref-sigmoid reward).
- **Explicit-band envelope block (`derive.envelope_band`).** Recognizes the
  clinical fixed-band form `bandpass(band: (lo Hz, hi Hz), order: N)` as a second
  envelope shape alongside the center+ratio derive; band edges and order become
  editable model slots.
- **Golden-fixture generator (`tools/gen_editor_fixtures.py`).** Committed the
  parity-corpus generator that refrain-editor's `scripts/sync-vendor.sh` already
  referenced; recurses into `protocols/` subfolders so device-specific sets are
  included.

## [0.10.0] — 2026-06-12

Additive — a new primitive, a new fan-out axis, and a worked example. Python↔Rust
parity is preserved (gated to 1e-6); the IR-JSON schema gains an additive
`lag_samples` coeff field (v0.1 + v0.2). No existing protocol changes behavior.

### Added
- **`autocorr(lag, window)` primitive** — rolling lag-k Pearson autocorrelation,
  the critical-slowing-down early-warning indicator (Scheffer 2009; Maturana
  2020). Full shared-core: Python reference impl + Rust `Autocorr` `Stage` at
  machine-precision parity (`micro_10_autocorr` equivalence fixture) + baked
  `window_samples`/`lag_samples` coeffs.
- **`bands { }` block + band-axis fan-out** — declare named frequency bands and
  author a band-parameterized subgraph once (`bandpass(band: bands)`); the
  resolver replicates it per declared band (`<name>@<band>`). Composes with the
  per-site `set` fan-out for the **band × channel cross product**
  (`<name>@<band>@<site>`); inhibits now replicate on both axes. Front-end only;
  IR-JSON/Rust core unchanged. See SPEC §4.9.3.
- **`examples/critical_fluctuation_cue.refrain`** — a dynamical critical-fluctuation
  cue protocol: an early-warning detector (variance + autocorrelation) per band per
  site (e.g. 10 bands × 2 sites = 20 envelopes; any site(s), defaulting to one
  midline electrode) against a self-calibrating percentile threshold, and a tonic
  reward muted by per-envelope critical-fluctuation inhibits (the audio cue). Design
  spec: `docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md`;
  gap RFC: `docs/proposals/2026-06-11-dynamical-neurofeedback-gaps.md`.

## [0.9.0] — 2026-06-08

Additive — no existing protocol changes behavior; Python↔Rust parity and the
IR-JSON schema are unchanged (the new kind is front-end-only: it resolves to
`DIMENSIONLESS`, and the Rust core does not read control `type_kind`).

### Added
- **`number` control kind** — a unitless scalar control. The honest kind for
  relative weights and gains: same `DIMENSIONLESS` dims as `percent`, but a
  host renders it with **no unit** (where `percent` shows a misleading "%").
  Use it for weighted-composite weights and any dimensionless knob whose value
  is not actually a percentage. The value is used raw (no `/100`).

## [0.8.0] — 2026-06-04

HRV biofeedback support for the Coherence Recorder (M1). All additive — no
existing protocol changes behavior, the IR-JSON schema is unchanged, and
Python↔Rust parity is preserved (gated to 1e-6). Consumers opt in by moving
their pin.

### Added
- **`passthrough()` montage.** A first-class single-channel identity montage —
  carries one raw channel through unchanged (e.g. a non-EEG HRV tachogram),
  replacing the `referential(reference: "device")` workaround. Implemented in
  both the Python evaluator (`PassthroughImpl`) and the Rust core
  (`Montage::Passthrough`), with the `micro_passthrough_identity` parity
  fixture. Requires a single-channel source.
- **Cross-session adaptive-state seed/export.** `Evaluator.export_state()`
  returns a compact, rate-independent summary of every `auto_range` /
  `percentile` tracker (`{low, high, n_eff}` / `{value, target_pct, n_eff}`,
  keyed `"<entity>.<callee>"`); `Evaluator.live(..., seed_state=...)` (and the
  `RustEvaluator` constructor) re-primes a run from a prior session's export.
  This is the longitudinal "user-adaptive ceiling that rises across sessions"
  signal. **Runtime state, not IR** — the protocol IR-JSON is unchanged.
  Available on both backends with parity; seeding pre-fills the rolling window
  with a deterministic synthetic distribution.
- **Low-Fs envelope guidance.** `rectify() + smooth(tau)` is documented and
  validated as the sanctioned low-latency envelope for low-sample-rate signals
  (e.g. a 4 Hz tachogram), where the FIR Hilbert's 8 s group delay is
  prohibitive.

### Changed
- `hilbert(kind="iir_allpass")` now raises a clear, actionable error pointing to
  `rectify()+smooth()` (low Fs) / the default FIR (mid-band). It remains
  intentionally unimplemented: a low-latency IIR Hilbert is accurate only
  mid-band and useless near DC, where all real NF/HRV bands sit (see
  `docs/DESIGN-NOTES.md` §7a).

### Notes
- The Rust core's PyO3 (wheel) surface gained `export_state()` and an optional
  `seed_state` constructor argument. The uniffi (Swift/Kotlin mobile) exposure
  of seed/export is deferred as an additive follow-up; existing mobile call
  sites are unaffected.

## [0.7.0] — 2026-05-29

### Added
- **Staged / segmented protocols (R1–R4 + R6).** A `.refrain` protocol can now
  describe a clinical session as N short training blocks with rests between
  them, taken against one baseline up front, with blocks training different
  things in one sitting (e.g. beta-up then alpha-up).
  - **N-phase runtime + three phase types.** `session.phases` is now a real
    runtime state machine (not metadata past the first phase). Each phase
    carries a `mode`: `timed` (auto-advance after `duration`), `open` (no
    clock; the host advances it), or `timed_with_floor` (auto-advance, but the
    host may end it early or extend it). Mid-session `output_muted` rests now
    actually mute (previously only the first phase did).
  - **Host transport API** (Python `Evaluator` + Rust core, pyo3 + uniffi):
    `advance_phase()` (clinician "Next →"), `hold(held=True)` (extend a
    `timed_with_floor` block; `hold(False)` re-arms), `set_clock_frozen(bool)`
    (pause/resume the phase clock on any phase type, orthogonal to output
    muting), and `current_phase()` introspection (index/name/mode/block/
    remaining_s/clock_frozen/held), aligned with `last_taps`. New numeric
    `phase/index` + `phase/output_muted` taps. Generalises the offline-only
    `skip_warmup` into a supported live control.
  - **Named blocks + reward bundles.** `block "<name>" { threshold; reward;
    output; inhibit }` selects which named threshold / reward bundle / output
    channels / inhibits are live during a phase; reward bundles are declared as
    `reward "<name>" { continuous?; event? }` (disambiguated from weighted
    components by field shape). Derives and threshold impls stay global and
    always-computed — a block gates *emission/selection*, never *computation*,
    so signals stay warm across block boundaries.
  - **Adaptive `percentile()` windows freeze across muted rests (R6):** the
    warmup phase populates the window and active phases ingest, but later
    `output_muted` rests do not, so rest-period artifact can't pollute a later
    block's window.
  - IR-JSON gains `phase.mode`/`phase.block`, top-level `blocks` and
    `reward_bundles` (all additive; the v0.2 schema is extended, not bumped). A
    protocol using blocks/bundles emits `refrain_ir_version: "0.2"`.
  - New example `examples/staged_beta_alpha.refrain`; full Python↔Rust parity
    suite for staged sessions (`tests/test_staged_parity.py`).

### Notes
- The one-shot baseline measure-then-freeze is recorder-side (control-backed
  `absolute(value: <control>)` + `set_control` at the single warmup→run); the
  engine's obligation is that derives/threshold impls stay global so a
  `set_control` seeding an inactive block's threshold takes effect — guarded by
  the parity suite.

## [0.6.3] — 2026-05-28

### Fixed
- **`absolute(value: <control_ref>)` is now accepted by the Rust core, in
  parity with the Python evaluator.** Previously the Rust core's
  `absolute_value` only matched a literal `number` for the `value:` arg and fell
  through to the positional path, panicking at `refrain-core/src/eval.rs:1153`
  with `missing positional arg 0` whenever a clinician knob was bound into a
  baseline-fixed threshold (the reported repro: `threshold "smr_t" { signal =
  "smr_envelope"; type = absolute(value: smr_threshold_uv); live_tunable = true }`
  with a `voltage`-typed control). Affects threshold blocks AND inhibits, since
  both share the same constructor parser.
- **`Evaluator.set_control(...)` now retunes an `absolute(value: <control_ref>)`
  threshold's constant** on both backends. Previously
  `AbsoluteThresholdImpl` had no `update_control` method, so the Python
  evaluator's `_control_deps` loop silently no-op'd and the threshold stayed at
  its construction-time default. The Rust core now binds a `Control::Const`
  sharing the threshold's cell so a live retune is observed in the next chunk
  without rebuilding state (parallel to `Control::Percentile` / `Sigmoid` /
  `Smooth` / `Weight`).

## [0.6.2] — 2026-05-26

### Changed
- **Rust-backend panics are now catchable.** Any panic in the Rust core (e.g. an
  unsupported-in-Rust construct reached on `backend="rust"`/`"auto"`) is converted
  to a Python `RuntimeError` at the PyO3 boundary instead of propagating as an
  uncatchable `pyo3_runtime.PanicException` — so an embedding host can `except`
  it and fall back to `backend="python"` rather than having the panic take down
  the process.

### Documentation
- Documented the v0.2 weighted-reward tap/stream keys — `reward/composite`,
  `reward/component[<name>]` (and the dotted `last_streams` equivalents
  `reward.composite` / `reward.component.<name>`) — in the EMBEDDING.md tap table
  and SPEC §7.8.
- Marked EMBEDDING.md's "Research mode" host API (`chunk_transformer=`, `sham=`,
  `evaluator.allocation_token`) as **forthcoming** — it is not part of the shipped
  0.6.x `live()` signature (which is `record_streams=` + `backend=` beyond the
  required args).

## [0.6.1] — 2026-05-26

### Fixed
- `coherence(...)` is now runnable in **live/push mode** with **positional**
  stream inputs (e.g. `coherence("a","b", band: …)`) on both backends. It
  previously resolved but crashed on the first `step_chunk(...)` — python:
  `CoherenceImpl.step() missing x_a/x_b`; rust: `coherence: missing baked
  coeffs` (an uncatchable panic). The resolver now canonicalizes coherence's
  positional inputs to named (`input_a`/`input_b`), so the positional form
  resolves to the same IR as the explicit named form. (The named form was
  already fine.) Python↔Rust parity confirmed at machine precision.

## [0.6.0] — 2026-05-25

### Added
- **Weighted multi-component reward engine** (the standard z-score / summary
  multi-metric model): a protocol may declare multiple named `reward "<name>"`
  and suppress-`inhibit "<name>"` components, each with a `signal` (a `[0,1]`
  success metric) and a `weight` (an ordinary numeric control — so author
  `default`, `resolve(bindings=)` override, and live `set_control` retune all
  work). The top-level `reward { combine = "weighted" }` aggregates them into
  `reward.composite` — a weighted average of per-component success (reward →
  `signal`, suppress → `1 − signal`) in `[0,1]`; `event` / `continuous` /
  `output` may reference `reward.composite` and `reward.<name>.signal`.
  Hard-gate inhibits (`metric` / `threshold` / `action`) keep their v0.1
  semantics and gate the whole composite.
- **IR-JSON v0.2** (`refrain-core/schema/ir-json-v0.2.schema.json`) — the first
  wire-format bump. A protocol using the new features emits
  `refrain_ir_version: "0.2"` with a `reward.components` array + `combine`;
  single-reward protocols still emit v0.1 byte-identically. The Rust core
  deserializes and evaluates v0.2 at machine-precision parity with the Python
  reference; both schema versions are validated and gated dual-backend.

### Notes
- Back-compatible: every existing v0.1 protocol parses, resolves, emits, and
  runs unchanged on both backends. `combine = "independent"` (per-site
  independent feedback) is planned as a follow-up.

## [0.5.0] — 2026-05-25

### Added
- **Named `allowed` groups** (`groups { … }` block): authors can declare named
  channel-name lists and reference them by bare identifier in a placement
  control's `allowed` and a `set` control's `default`. Groups expand at
  resolve time to the same channel tuples as an inline list; the IR-JSON wire
  format and `IR_JSON_VERSION` are unchanged. Validation: empty group, duplicate
  channel within a group, unknown group reference, and group-name collision with
  a control all raise `ResolveError`. The `groups` block merges across `extends`
  (child overrides parent same-named group by re-declaration). See `docs/SPEC.md §4.10`.

## [0.4.0] — 2026-05-25

### Changed
- `Evaluator.live(...)` default backend is now `"auto"`: prefer the compiled
  Rust core when the `refrain_core` wheel is importable, otherwise fall back to
  the pure-Python engine. Non-breaking — the wheel is not a hard dependency, and
  the two backends are gated to machine precision in CI. Pass `backend="python"`
  or `backend="rust"` to force a specific engine.

### Added (M5 — IR-JSON wire spec + conformance)
- `docs/IR-JSON.md` — normative IR-JSON wire format specification: field
  layout for every node family (filter, envelope, threshold, reward, inhibit,
  controls, taps), versioning, and the baked-coefficient contract that keeps
  the portable runtime free of filter-design dependencies.
- `refrain-core/schema/ir-json-v0.1.schema.json` — machine-readable JSON
  Schema for `ir-json/v0.1`; validated in CI against every committed
  `*.ir.json` golden vector via `tests/test_ir_json_schema.py`.
- `docs/CONFORMANCE.md` — published conformance suite: required and optional
  capabilities, golden-vector families, how to claim conformance, and the
  tolerance budget for floating-point comparisons across runtimes.
- `docs/REPRODUCIBILITY.md` — reproducibility contract: deterministic
  fixture regeneration, schema-pinning policy, and the guarantee that
  `gen_fixtures.py → IR-JSON → cargo test` round-trips are stable across
  Python and Rust re-runs given the same seed and SciPy version.
- `docs/DESIGN-NOTES.md` §5a — note on the filter-coefficient baking
  boundary: SciPy runs at design time, baked coefficients travel in IR-JSON,
  the portable runtime never links SciPy/BLAS.
- Drift gate (`refrain-core/tools/check_equivalence.py`) extended to five
  steps: schema-validation step added after dual-backend pytest, gating every
  CI run on `test_ir_json_schema.py`.

### Added
- Placement `kind="pair"` — coherence pairs; two-leg binding via `.a`/`.b`
  member access (`coh.a`, `coh.b`) in montage channel slots. Default and
  override are 2-tuples of channel strings; `allowed` is a list of pairs or
  `"any"`. `requires.channels = [coh]` expands to both legs. Validation:
  bound pair must be in `allowed` and both legs device-capable. The `final`
  lock applies (Mode 3-compatible).
- Placement `kind="set"` — multi-site set declaration; `default` is a list of
  channel strings, `allowed` is a list of channels or `"any"`, optional `min`
  and `max` bound the set size. Bound via `resolve(bindings={"sites": [...]})`.
  Each member validated against `allowed` and the connected device; count
  checked against `min`/`max`.
- Mode 2a per-site replication (implicit fan-out) — binding a `set` placement
  into an input montage slot triggers an AST-level fan-out pre-pass: the
  protocol is rewritten to N per-site inputs, derives, and thresholds
  (`<name>@<site>`) before resolution. The reward condition is combined as
  `all_of`/`any_of` per the new `reward.combine = "all" | "any"` field
  (default `"all"`). Single-site bindings degenerate cleanly. The emitted IR
  is a flat N-input graph using only existing IR-JSON node types.
- `reward.combine` field — `"all"` (default) or `"any"`, selects whether the
  per-site reward conditions are combined with `all_of` or `any_of` during
  Mode 2a fan-out replication.
- Scoping guards: a `reward.continuous` expression that depends on a
  per-site replicated stream raises `ResolveError` ("see Mode 2b"); an
  ambiguous replication boundary (a derive mixing a per-site stream with a
  non-replicated one) also raises `ResolveError`.

### Notes
- IR-JSON schema unchanged (v0.1). `pair`/`set` are placement controls →
  the shipped `type_kind != "placement"` guard already omits them. The
  fan-out-unrolled IR is a flat graph of existing node types; no new emitter
  logic was required. `IR_JSON_VERSION` and the JSON Schema are unchanged;
  `check_equivalence` stays PASS.
- 481 tests passing (5 skipped / infrastructure). No breaking changes.
  Existing v0.0–v0.3 protocols continue to parse and resolve unchanged.

## [0.3.0] — 2026-05-25

### Added
- `placement` control type — resolve-time site binding; kinds `active` (single
  electrode) and `bipolar` (coupled plus/minus pair). Mode 1 (default +
  per-deploy override via `resolve(bindings=...)`) and Mode 3 (fixed site,
  locked with `final = true`). One `.refrain` artifact, any compatible site.
- `resolve(bindings=...)` keyword argument — binds placement controls to
  concrete channel names at resolve time. Fail-fast validation: bound value
  must be in `allowed` and present on the connected device.
- `final` on controls — a `final` placement control cannot be overridden by
  a `bindings` value, and child protocols cannot redeclare it via composition.
- Placement references accepted in montage channel slots
  (`referential(active: site, ...)`, `bipolar(plus: a, minus: b)`) and in
  `requires.channels = [site]`; the `bipolar(pair: site)` montage form for
  a coupled bipolar placement.

### Notes
- IR-JSON schema unchanged (v0.1). The emitter omits `placement` controls
  from the wire `controls` section (they are resolve-time-only); a
  placement-bound protocol's IR-JSON is byte-shaped identically to its
  hardcoded-site equivalent. `IR_JSON_VERSION` and the JSON Schema are
  unchanged; `check_equivalence` stays PASS.
- 460 tests passing (5 skipped / infrastructure). No breaking changes.
  Existing v0.0–v0.1 protocols continue to parse and resolve unchanged.

## [0.1.0] — 2026-05-13

### Added
- `coherence(input_a, input_b, band, window)` primitive — magnitude-
  squared coherence between two streams via streaming Welch's method.
  Returns `stream<scalar in [0, 1]>`. Enables coherence-training
  protocols (e.g., inter-hemispheric alpha for deep-state / flow
  integration).
- `CoherenceImpl` streaming evaluator implementation, with the
  multi-segment-Welch guard against the single-segment MSC ≡ 1.0
  identity trap.
- 10 new tests (8 unit, 2 integration) for the coherence primitive.

### Documentation
- `docs/PRIMITIVES.md` gains the canonical `coherence` entry under
  Spectral operators; "(planned for v0.1)" note removed from the
  Coverage section.

### Notes
- 353 tests passing on Python 3.10–3.14.
- Coverage extension; no breaking changes. Existing v0.0r1 protocols
  continue to parse and resolve unchanged.

## [0.0.1] — 2026-05-11

### Added
- `Evaluator.last_taps() -> dict[str, float | bool]` — per-chunk last-
  sample values of internal computations for clinician observation
  windows. Exposes envelope traces (`derive/<name>`), threshold lines
  (`threshold/<name>`), dwell sub-conditions (`reward/condition[i]`),
  pre-gating reward (`reward/continuous`), post-gating output values
  (`output/<channel>`), inhibit booleans, combined `muted` gate.
- SPEC §7.8 establishing the runtime-SHOULD-expose contract for tap
  introspection, including canonical naming scheme so cross-runtime
  implementations can claim conformance.
- 14 new tests for the tap API.

### Documentation
- `docs/EMBEDDING.md` gains an "Introspection: live taps" section
  with host-side usage example and the full tap-key table.
- `docs/DESIGN-NOTES.md` §4e documents the design decisions.

### Notes
- 343 tests passing.
- First versioned release (the initial 0.0.0 was an unreleased
  placeholder).

## [0.0.0] — 2026-05-10 *(unreleased, scaffold only)*

Initial scaffold establishing the project structure. Reference
implementation development started here. Not tagged or published.

Implementation bundle that landed pre-0.0.1:

### Parser + AST
- Full v0.0r1 surface from `docs/SPEC.md` §3 — protocol decl,
  section blocks, named decls (input/derive/threshold/inhibit/custom),
  composition (`extends`/`amend`/`remove`/`final`), expressions with
  unit literals (Hz/ms/s/min/uV/uV²/%), tuples, arrays, ternary,
  member access, block expressions.
- Source-location attribution on every AST node (excluded from
  equality/repr so round-trip identity holds).

### Resolver + IR
- Type checker with dimensional unit math.
- Composition pass (`extends`/`amend`/`remove`/`final`) applied
  before resolution; merged AST feeds the resolver unchanged.
- Hardware validation against amp profile JSON; resource budget
  accounting.
- IR pretty-printer + CRED-nf supplement table generator.

### Evaluator + primitive library
- Streaming evaluator with `Evaluator.live(...)` push-mode API for
  host embedding. Lifecycle: `ready → warmup → run → stopped`.
- Live control tuning via `set_control(name, value)` with warm-restart
  for percentile thresholds, smoothing time constants, and sigmoid
  midpoints.
- 24 streaming primitives covering SMR, Othmer ILF, alpha-theta:
  acquisition (bipolar, referential with linked-ears / common-average /
  device), spectral (bandpass with Butterworth/Bessel/Chebyshev II,
  hilbert FIR, bandpower), time-series (magnitude, rectify, smooth,
  differentiate), statistics (auto_range, percentile), mappings
  (sigmoid, linear), conditions (above, below, inside, all_of, any_of),
  events (dwell), inhibit actions (mute, freeze, flag).

### Input sources
- `FifSource`, `EdfSource`, `XdfSource` via `mne` and `pyxdf`
  (optional `[eval]` extra).
- `SyntheticSource` with `SignalGenerator` for deterministic
  scheduled-burst test signals.

### Amp profiles
- Neurofield Q21 (21-channel research-grade EEG).
- OpenBCI Cyton (8-channel consumer EEG).
- BrainBit Flex (4-channel consumer EEG with hardware reference).

### Examples
- `examples/smr_cz.refrain` — operant SMR/theta-beta at Cz.
- `examples/othmer_ilf_t3t4.refrain` — Othmer ILF, T3-T4 bipolar.
- `examples/alpha_theta.refrain` — alpha-theta (Peniston-style).
- `examples/smr_cz_brainbit.refrain` — SMR Cz adapted for BrainBit.
- `examples/library/othmer/ilf_base.refrain` — base for composition.
- `examples/othmer_ilf_cz_pz.refrain` — Cz-Pz variant via `extends`.

### CLI
- `refrain check` — parse-only validation.
- `refrain resolve` — parse + resolve + type check; print IR or
  CRED-nf supplement.
- `refrain run` — execute against a synthetic or recorded source.

### Documentation
- `docs/CONCEPT.md`, `docs/SPEC.md`, `docs/TOUR.md`, `docs/PRIMITIVES.md`,
  `docs/EMBEDDING.md`, `docs/HOST-PLUGIN-BRIEF.md`,
  `docs/DESIGN-NOTES.md`.

[0.14.1]: https://github.com/refrain-lang/refrain/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/refrain-lang/refrain/compare/v0.13.0...v0.14.0
[0.1.0]: https://github.com/refrain-lang/refrain/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/refrain-lang/refrain/releases/tag/v0.0.1
