# Changelog

All notable changes to Refrain are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/) — minor
bumps are additive; major bumps may break compatibility.

## [Unreleased]

### Added
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
