# Protocol fuzzer — calibrated (differential) oracle — design

> Status: approved approach (Approach A chosen), ready for the TDD plan after a
> gating validation task.
> Parent: [[2026-06-28-fuzzer-parity-roadmap-design]] (the deferred "calibrated
> vs analytic-margin oracle"). Builds on Increments 0 (#51) and 1 (#57).

## Goal

Make the fuzzer's oracle agree with the engine on protocols whose thresholds sit
**near or below the synthetic noise floor** — the dominant remaining slice of the
real `refrain-protocols` corpus. Today these produce **false-positive violations**
(the oracle predicts idealized silence; the engine fires on noise crossings), so
they cannot be fuzzed. This is the single highest-value lever left: it unlocks
percentile single-leaf (18) + near-noise-floor absolute (13+), ~**56%** of the
corpus, and is the prerequisite for a trustworthy refrain-protocols CI gate.

Two small coupled fixes ride along (both required for the unlock, both tiny):
- **Resolve control-ref absolute thresholds** in `surface.py` (the ~16 "baseline"
  protocols declare `absolute(value: <control>)`; the absolute branch currently
  only reads numeric literals, unlike the percentile branch which already resolves
  control-refs). Without this they crash generation / can't be supported.
- **Batch skip-not-crash** (`runner.py`): an evaluator-setup error on one protocol
  (e.g. a montage needing a channel the synthetic source lacks) currently **aborts
  the whole batch**, violating Increment 0's "the batch reports every protocol"
  contract. Per-protocol exceptions → ERRORED bucket (batch completes; still exits
  1 so real errors stay loud).

## The problem, precisely

`oracle._predicted_envelope_timeline` builds an **idealized, piecewise-constant**
envelope: a hardcoded `_noise_floor_envelope() == 2.0 µV` baseline plus each tone's
analytic steady-state. The engine computes the **actual noisy envelope** (bandpass
→ hilbert → magnitude → smooth of real synthetic EEG). When margins are clear (30 µV
tone vs 8 µV threshold) the idealization holds — that is what Increments 0/1 fuzz.
But the real corpus uses thresholds around **2 µV**, right at the noise floor
(measured: quiet envelope mean ≈ 1.9 µV vs a 2.0 µV threshold). There, firing is
decided **sample-by-sample by the specific noise realization**. No idealized value
and no statistical summary is *crisp* there — so the analytic oracle mispredicts
(false positives), and any prediction that isn't computed on the actual signal must
mark the region DON'T-CARE → vacuous. The v1 oracle's founding assumption ("clear
margins") is exactly what the real corpus violates.

## Decision: Approach A — a differential oracle

Chosen after weighing three options (see the brainstorm). B (analytic noise model)
and C (controlled synthetic) both go **vacuous** near/below the floor — the exact
regime we must reach — so they cannot close the gap. A is the only non-vacuous
option, and it is the standard technique for domains where first-principles
prediction is intractable (noise-dominated DSP): **differential testing against an
independent reference implementation.**

**The oracle computes the real envelope via the same DSP the engine uses, then
predicts the reward semantics (threshold/percentile/condition/dwell/reward) with its
own independent implementation and asserts the engine matches.** It is *calibrated*
on the signal and *independent* on the semantics.

### The independence position (what we keep vs give up)

- **Give up:** detecting *DSP* bugs (wrong filter application/envelope). Acceptable:
  the DSP is generic scipy already covered by the Rust↔Python equivalence gate
  (golden vectors), the primitive-impl unit tests, and the fuzzer's own
  band-characterization probe (asserts each derive peaks in its declared band).
  Note the v1 oracle already shares the *baked SOS* with the engine, so full DSP
  independence was never actually present.
- **Keep:** independent reference implementations of layers 2–4
  (`_ordinal_percentile_truth`, `predict_absolute_leaf_truth`, `_walk_condition`,
  `apply_dwell`, phase muting, event emission) — the Refrain-specific semantics the
  fuzzer uniquely exists to test. Approach A **swaps only the envelope source**;
  these stay untouched.
- **Risk — reference drift:** differential testing is only as strong as the
  reference's independence. The oracle's semantic layers must stay *separately
  authored* from the engine's eval code; a future "fix the oracle to match the
  engine" silently weakens the gate. This is a review discipline, called out here
  and to be enforced in code review.
- **Where behavior is genuinely noise-arbitrary**, a sample-exact SHOULD-FIRE is an
  implementation-consistency check (engine == reference), which is still valuable;
  and the **metamorphic** properties (more in-band signal ⇒ non-decreasing firing)
  remain the more meaningful assertion there. The oracle leans on metamorphic where
  crisp per-sample assertions are noise-sensitive.

## Architecture

### The linchpin: a sample-exact, bit-exact real envelope

The prototype proved the direction *and* the make-or-break detail: a coarse
(per-chunk) envelope leaves residual false positives because it misses sub-chunk
noise crossings. The differential oracle **must** feed layers 2–4 the engine's
**per-sample** envelope, bit-exact. Approach:

- The fuzzer computes each derive's envelope by running the **engine's own derive
  primitive impls** (`BandpassImpl`/`HilbertFirImpl`/`MagnitudeImpl`/`SmoothImpl`,
  reused from `primitive_impls.py`) over the **same rendered synthetic signal** the
  engine consumes (`render_scenario` is deterministic/seeded). Same impls + same
  signal ⇒ bit-exact with the engine's internal envelope.
- Reusing the impls (not reimplementing, not per-chunk taps, not scipy
  approximations) is required — it keeps the DSP shared *and* exact, and keeps all
  changes inside the fuzz package (no refrain-core change).
- `oracle.predict(scenario, surface)` grows a real-envelope source: replace
  `_predicted_envelope_timeline`'s idealized output with the reconstructed
  per-sample envelope per derive. Everything downstream (leaf truth, percentile
  rank, condition, dwell, collar, muting) consumes it unchanged.
- `_noise_floor_envelope`'s hardcoded 2.0 and the "clear margins" comment are
  retired.

### GATING validation task (task 0 of the plan)

Before building the full increment: implement the sample-exact real envelope, wire
it into `predict`, and run the **31 dirty/percentile protocols** (13 near-floor
absolute + 18 percentile) plus a **regression check on the 7 already-fuzzed
protocols** (must stay clean; the real envelope must not break the clear-margin
cases). Measure: clean-rate, the residual failures' causes, and wall-clock cost. If
clean-rate is high and regressions are zero → proceed. If not → the residuals reveal
which layer-2–4 assumption also needs calibrating, caught before full build. This is
the "measure, don't assume" gate that has repeatedly corrected this roadmap.

### Coupled fixes

- `surface._threshold_surface` absolute branch: read the `value:` named arg (or
  first positional) and resolve an `IRControlRef` via `_resolve_control_default`
  (mirroring the percentile branch). Detector then supports these once resolved.
- `runner.run_batch`: wrap per-protocol `fuzz_protocol` in a broad try → ERRORED
  outcome on any exception (batch completes; exit code still 1 on errors).

## Testing

- **Validation harness** (task 0): the 31-protocol clean-rate + 7-protocol
  regression, as a reproducible script + recorded numbers.
- **Real-envelope oracle** (`tests/fuzz/test_oracle_*`): for a near-floor absolute
  and a percentile fixture, the reconstructed envelope is bit-exact with the
  engine's (assert against a captured engine trace), and `predict` + check → PASS
  where v1 was SPURIOUS.
- **Clear-margin regression**: the 7 currently-fuzzed protocols (`realistic_smr`,
  `smr_cz`, the Inc-1 fixtures) still fuzz clean, byte-identical corpora where
  applicable.
- **Coupled fixes**: control-ref absolute resolves (unit test); batch over a dir
  with an eval-crashing protocol → ERRORED (not abort), batch completes, exit 1.
- **Metamorphic** still holds on the newly-clean protocols.
- Full suite green; `src/refrain/fuzz/` ruff-clean; CI gate clean.

## Success metric

Re-probe refrain-protocols: the ~31 near-floor/percentile protocols move from
skipped/dirty → **fuzzed clean**; the batch runs to completion (no aborts). Target:
corpus `fuzzed` jumps from ~0-clean to a large majority; refrain-protocols CI
becomes wire-able.

## Out of scope

- Detecting DSP/filter bugs via the oracle (covered by equivalence gate + band
  probe + unit tests, by design).
- Coherence / weighted-composite / inhibit / bandpower / staged (later increments).
- Montage-aware synthetic channels (the 2 montage protocols become clean ERRORED
  entries here; actually running them is a later synthetic-source increment).
- refrain-protocols CI wiring itself (enabled by this increment; its PR is a
  follow-up).

## Risks / open questions for the plan

- **Bit-exactness** of the reconstructed envelope vs the engine (impl reuse, warmup
  state, chunk boundaries). Task 0 asserts it against a captured engine trace; any
  divergence is the first thing to resolve.
- **Performance:** running the DSP in the oracle roughly doubles per-scenario DSP
  cost on an already-slow suite. Measure in task 0; if prohibitive, consider
  capturing the engine's per-sample envelope from its single run instead of
  reconstructing (a refrain-core trace API — larger blast radius, weigh then).
- **Residual noise-arbitrary scenarios:** some near-floor scenarios may have no
  crisp assertion even with the real envelope; lean on metamorphic and/or mark
  them explicitly (never silently vacuous).
- **Reference-drift discipline:** the plan must keep the oracle's semantic layers
  independently authored and say so in the review rubric.
