# Protocol fuzzer — metamorphic tier (noise-dominated gate) — design

> Status: approved approach (validated by an independent design review + a
> reproduction experiment), ready for the TDD plan after a gating validation task.
> Parent: [[2026-07-07-fuzzer-target-tiered-gate-design]] (Tier 2). Follows the
> calibrated-oracle gate finding (`docs/superpowers/ci/calibrated-oracle-gate-finding.md`):
> sample-exact assertions cannot survive a noise floor, so noise-dominated protocols
> need a noise-robust gate. This is that gate.

## Goal

Gate the **noise-dominated majority** of the real corpus (thresholds near/below the
synthetic noise floor — percentile single-leaf + low-absolute + their multi-leaf
combinations) with assertions that a specific noise realization cannot flip. Replace
the fragile, direction-blind, event-count metamorphic check with a principled
**same-noise-realization differential** on **time-in-reward**.

This also **fixes a pre-existing bug**: the merged `check_metamorphic_monotonic`
asserts non-*decreasing* firing for *every* swept threshold, which is sign-wrong for
`below`/inhibit leaves and produces false-positive violations on near-floor protocols
(verified: `smr_classic_cz_brainbit`'s `theta_t` sweep gives `[57,56,57,56]` etc.,
3/3 seeds "violate" today).

## The insight (why this is tractable where sample-exact wasn't)

The fuzzer **fixes the noise seed and varies only the injected tone amplitude**, so
the noise is **byte-identical across a sweep**. A sweep is therefore a **controlled
A/B on one noise realization**, not a comparison across independent noisy runs —
much stronger than classical metamorphic testing. On a fixed realization, "more
in-band signal drives the leaf harder" is a real, assertable ordering.

## Design (four changes to the sweep + check machinery)

### 1. Direction-aware sweeps
Classify each swept derive by its role in the reward condition
(`_all_leaves(surface.reward_condition)`):
- feeds only `above()` leaves → assert the metric is **non-decreasing** in amplitude;
- feeds only `below()` leaves → assert the metric is **non-increasing**;
- feeds both, or a composite/weighted reward → **do not assert** (record as
  no-crisp-assertion for that sweep; never a silent pass).
Fixes FM1 (the direction-blind false positives) and is correct for inhibit leaves.

### 2. Metric = time-in-reward, not event count
Event count during a spike = dwell re-trigger count = a pure noise artifact (every
momentary noise dip that recovers adds an event) → not monotone in amplitude (FM2,
~1/8 seeds flip). Instead measure **time-in-reward**: the fraction of the post-fill
spike window during which the reward output is actively holding, read from
`Evaluator(record_streams=True).last_streams()["reward.event.holds"]` (or the reward
stream) — the per-sample primitive the calibrated-oracle gate already validated.
Time-in-reward moves monotonically with drive under a fixed realization; it does not
count noise flicker.

### 3. Floor-straddling ladder + required contrast (fail-loud)
The current fixed 5/15/25/40 µV ladder sits entirely **above** a ~2 µV threshold, so
the whole sweep saturates and never probes the noise-dominated regime (FM3); and on
some baseline protocols the hold sweep is `[0,0,0,0,0]` — a **hollow pass** (FM4).
Instead set amplitudes **relative to the measured per-derive in-band noise envelope**
(compute the noise-floor envelope per derive once, e.g. via a quiet render + the
derive DSP): amplitudes at `{0.5, 1, 2, 4} × noise_median`. Then:
- require the **bottom rung near-silent** and the **top rung clearly firing**, and
  assert `top_metric − bottom_metric ≥ contrast_floor`;
- a flat sweep (`[0,0,0,0]` or `[k,k,k,k]`) **FAILS loud** as vacuous, not passes.
This makes the sweep actually straddle the floor and gives it assertion power.

### 4. No tolerance fudge
Do NOT add a `n[i] >= n[i-1] − k` slack knob (FM5): a `k` big enough to absorb the
inhibit inversion also hides real regressions. Robustness comes from the metric
(time-in-reward on a fixed realization) and the direction-awareness, not from slack.

### Escalation (only if needed)
If single-seed time-in-reward is still marginal for some protocols, assert
monotonicity of the **median over 3–5 fixed seeds** (or Spearman rank-correlation ≥
threshold). Use sparingly — it multiplies the (~15-min) corpus wall-clock.

## GATING validation task (task 0 of the plan)

Before building: run the **whole corpus with the new metamorphic tier ON, across ~5
fixed seeds, on the current (known-good) engine, and require zero metamorphic
violations and zero hollow passes across all seeds.** Today this goes red immediately
(FM1 deterministic on the ~18 `below()`+percentile protocols; FM2 stochastic on the
reward-leaf sweeps) — that red is the target to eliminate. Measure: violation count
per seed, hollow-pass count, and wall-clock. If the new design can't get that clean
on a correct engine, it is not shippable — stop and report, before touching the
generator further. (Same discipline that killed the calibrated oracle cheaply.)

## Components touched

- `src/refrain/fuzz/generate.py` — `generate_rank_sweep` (direction-aware +
  floor-straddling amplitudes), `generate_hold_duration_sweep` (fail-loud contrast).
- `src/refrain/fuzz/check.py` — `check_metamorphic_monotonic` → direction-aware,
  time-in-reward metric, contrast/fail-loud, no slack.
- `src/refrain/fuzz/runner.py` — surface the reward `holds` stream from the engine
  run (already runs the engine; add `record_streams=True` + capture the reward
  stream) so the check can read time-in-reward.
- `src/refrain/fuzz/surface.py` — per-derive noise-floor envelope (measured once) for
  the floor-straddling ladder; and (small) classify multi-leaf percentile so the
  detector doesn't fall through unguarded.
- `oracle.py` — no change (metamorphic is engine-vs-property, not oracle-vs-sample).

## Testing

- **Direction-aware unit test**: a below-leaf sweep asserts non-*increasing*; an
  above-leaf sweep non-*decreasing*; a mixed sweep asserts nothing (records
  no-crisp).
- **Time-in-reward metric**: a near-floor above-leaf protocol whose event-count is
  non-monotone across seeds has monotone time-in-reward (bit-exact from the reward
  stream).
- **Fail-loud contrast**: a flat sweep FAILS (not a hollow pass).
- **The 7 clear-margin protocols** still fuzz clean.
- **Task-0 corpus gate**: recorded per-seed violation/hollow-pass counts → zero on a
  correct engine.
- Full suite green; `src/refrain/fuzz/` ruff-clean.

## Success metric

The noise-dominated protocols move skipped/dirty → **fuzzed clean under the
metamorphic tier**, across ~5 seeds, with no false positives and no hollow passes;
the refrain-protocols batch becomes green-able for the structural + metamorphic tiers.
Re-probe and record the unlock.

## Out of scope

- Sample-exact assertions for noise-dominated protocols (proven intractable).
- Montage-aware synthetic channels (the eval-error protocols; a separate increment).
- The feature long tail (coherence/weighted/inhibit-metric/bandpower/staged).

## Risks / open questions for the plan

- **Does time-in-reward actually go monotone** on a fixed realization for the real
  protocols? Task 0 is the arbiter (the one thing to test first).
- **The floor-straddling ladder** depends on a good per-derive noise-envelope measure;
  confirm it's stable across seeds and cheap to compute.
- **Contrast_floor** must be principled (derived from the noise-vs-clear gap), not an
  arbitrary knob — else it becomes the percentile trap again.
- **Multi-leaf mixed sweeps that assert nothing** must not leave a protocol with *no*
  crisp assertion anywhere (fully vacuous) — such protocols are reported, not passed.
