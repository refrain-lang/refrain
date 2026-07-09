# Metamorphic-tier gate — Task-0 result: **RED** (2026-07-09)

The gating validation ran the whole refrain-protocols corpus under the new
metamorphic tier, across 5 fixed seeds, on the current known-good engine. The
spec's Task-0 clause requires this to be clean before the tier ships.

**It is not clean. Stopping here, per the spec, before touching the generator further.**

```
tools/fuzz_corpus_gate.py --corpus .../protocols --library .../lib --seeds 41,42,43,44,45

seed 41 (361.0s)  fuzzed 25 / skipped 14 / errored 3   VIOLATIONS: 3   hollow: 0
seed 42 (374.5s)  fuzzed 25 / skipped 14 / errored 3   VIOLATIONS: 2   hollow: 0
seed 43 (402.3s)  fuzzed 25 / skipped 14 / errored 3   VIOLATIONS: 1   hollow: 0
seed 44 (331.8s)  fuzzed 25 / skipped 14 / errored 3   VIOLATIONS: 11  hollow: 0
seed 45 (331.3s)  fuzzed 25 / skipped 14 / errored 3   VIOLATIONS: 5   hollow: 0

violations across 5 seeds: 22
hollow passes: 0
RESULT: FAIL
```

Wall-clock ≈ 30 min for 5 seeds (~6 min/seed), which validates the bounded-fill
change (R5): the same corpus at full declared percentile windows would have taken
hours.

## What is NOT wrong

- **Zero hollow passes, zero generator-bugs, across all 5 seeds.** The fail-loud
  machinery works: nothing passed vacuously.
- **The 3 `errored` protocols are pre-existing corpus gaps** (montage channels the
  synthetic source lacks), not gate failures, and unchanged by this work.
- **The tier catches real engine regressions.** `tests/fuzz/test_engine_regression.py`
  injects three and all are caught: inverting `above()` → `VIOLATION:MONOTONICITY`;
  latching dwell on → `VIOLATION:NO_CONTRAST` (baseline 1.000); killing dwell →
  `VIOLATION:NO_CONTRAST` (baseline 0.000).
- Coverage rose: **fuzzed 25 / 42** (percentile protocols unskipped; `realistic_smr`'s
  probe `GENERATOR BUG` gone).

The 22 violations are **false positives on a correct engine**. Three distinct causes.

---

## Cause A — multi-leaf reward conditions never validate their leaves

`smr_up_c4_baseline_brainbit` violates on **all 5 seeds**. Its report:

```
[NO ASSERTION] rank_sweep:smr_envelope: no resolvable decision level for the swept leaf
     series (recorded, not asserted): 0.000 -> 0.000 -> 0.000 -> 0.000
[DOWN] rank_sweep:high_beta_envelope: baseline 0.000 | 0.000 -> 0.000 -> 0.000 -> 0.000
[UP  ] hold_duration_sweep:           baseline 0.000 | 0.000 -> ... -> 0.000
  [VIOLATION:NO_CONTRAST] rank_sweep:high_beta_envelope — baseline is already silent
  [VIOLATION:NO_CONTRAST] hold_duration_sweep — top rung moved 0.0000
```

Its `smr_t` is `absolute` with `absolute_uv = None` — a **control-valued threshold**
(`absolute(value: <control>)`), which the surface only extracts as a numeric literal.
`surface._classify_single_leaf` rejects exactly this shape ("absolute threshold value
did not resolve to a literal") — but it is **only called for single-leaf conditions**.
`_reward_condition_from_ir` returns a multi-leaf `ConditionNode` without classifying
any child, so unsupported leaf shapes reach the sweeps.

Consequence: the favourable-background driver cannot hold that leaf TRUE (no anchor),
so the `all_of` never fires, every other sweep reads 0.000, and we then *assert* on
those dead sweeps.

This is a plain defect and a **pre-existing one** (multi-leaf leaves were never
validated). The correct behaviour is a clean, feature-mapped SKIP.

---

## Cause B — sub-anchor monotonicity is PHYSICALLY FALSE (the spec's core claim)

The spec asserts: *"On a fixed realization, 'more in-band signal drives the leaf
harder' is a real, assertable ordering."* **That is false below the decision level.**

`smr_up_c4` is a plain single-leaf `above` + `percentile(70)`. On seed 41 the metric
*decreases* as drive increases, and the 1×-anchor rung sits **below the no-drive
baseline**:

```
seed 41  baseline 0.076 | rungs 0.042 -> 0.007 -> 0.753 -> 1.000   VIOLATION
seed 44  baseline 0.445 | rungs 0.449 -> 0.353 -> 0.745 -> 1.000   VIOLATION
```

Direct measurement on one fixed noise realization (micro_single_pct, seed 41),
sweeping tone amplitude finely — `frac` is the fraction of spike samples with
`envelope > threshold`:

| k × floor | mean env | mean thr | frac > thr | time-in-reward |
|---|---|---|---|---|
| 0.00 | 0.980 | 1.486 | **0.170** | 0.060 |
| 0.25 | 0.977 | 1.485 | **0.134** | 0.044 |
| 0.50 | 1.038 | 1.484 | **0.119** | 0.030 |
| 0.75 | 1.148 | 1.483 | **0.107** | 0.017 |
| 1.00 | 1.301 | 1.516 | 0.172 | 0.062 |
| 1.50 | 1.673 | 1.572 | 0.646 | 0.402 |
| 2.00 | 2.112 | 1.602 | 0.869 | 0.780 |
| 3.00 | 3.115 | 1.615 | 1.000 | 1.000 |
| 4.00 | 4.201 | 1.620 | 1.000 | 1.000 |

The mean envelope **rises** (0.980 → 1.148) while exceedance **falls** (0.170 → 0.107),
and the threshold is essentially constant (1.486 → 1.483) — so this is *not* the
percentile adapting to its own signal.

**Mechanism.** Band-limited noise has a Rayleigh-distributed envelope with a heavy
upper tail. Adding a *coherent* tone of comparable amplitude makes it Rician, which
*narrows the relative spread* and thins that upper tail. A p70 threshold computed over
a quiet window sits in that tail, so exceedance is a tail event: thinning the tail
lowers it even as the mean rises. Only once the tone dominates the noise does the mean
shift carry the whole distribution across the threshold.

This is deterministic and per-realization, so it is **not** cured by more seeds, a
longer window, or a median-over-seeds escalation. It is a real boundary on where the
metamorphic property is assertable:

> Per-realization monotonicity of time-in-reward holds only where the injected drive
> is at or above the leaf's decision level. Below it, adding coherent in-band signal
> can *reduce* exceedance of an upper-tail threshold.

Restricting the asserted rungs to `≥ 1× anchor` restores monotonicity — measured on
`smr_up_c4` with rungs `(1, 2, 4, 8)`:

```
seed 41: 0.072 -> 0.791 -> 1.000 -> 1.000   OK
seed 42: 0.426 -> 1.000 -> 1.000 -> 1.000   OK
seed 43: 0.516 -> 0.876 -> 1.000 -> 1.000   OK
seed 44: 0.453 -> 0.784 -> 1.000 -> 1.000   OK
seed 45: 0.314 -> 0.655 -> 1.000 -> 1.000   OK
=> 5/5 monotone
```

---

## Cause C — the percentile anchor is the wrong statistic

`leaf_anchor_uv` uses the **median** of the quiet envelope as a percentile leaf's
decision level. The decision level is actually the **p-th percentile** of that quiet
envelope, because that is literally what the engine's threshold computes.

- For `above` + p70, the median **undershoots**: the 1× rung is still below threshold
  (table above: env 1.301 vs thr 1.516), which parks an asserted rung inside the
  ambiguous band from Cause B.
- For `below` + p30, the median **overshoots**: the leaf is already dead at rung 1.
  `theta_down_cz` with rungs `(1, 2, 4, 8) × median` measures `0.000` at every rung on
  every seed — the sweep cannot resolve the boundary at all.

Fix: anchor on `numpy.percentile(quiet_envelope, target_pct)` — available from the same
quiet probe run that already measures the floor. No new engine runs.

---

## Cause D (secondary) — the hold sweep is noise-dominated on this tier

`smr_up_c4` seed 45: `hold_duration_sweep  baseline 0.484 | 0.722 -> 0.625 -> 0.816 -> 1.000 -> 1.000`.

For a noise-dominated protocol the *quiet* state already holds reward ~40–50 % of the
time, and the hold-sweep metric window is only `5 × dwell` (1.25 s at dwell=250 ms).
The tone-driven contribution therefore does not dominate the noise contribution, and
the series wiggles. The hold sweep measures dwell timing, which is only assertable
where the quiet state does *not* fire.

Note this assertion is not load-bearing for dwell regressions: `test_engine_regression.py`
shows a latched or dead dwell is caught by the **rank** sweep's contrast check
(`NO_CONTRAST`, baseline 1.000 / 0.000).

---

---

# Iteration 2 — corrected probe (A–D applied): still RED, 5 violations / 5 seeds

Causes A–D were fixed (leaf validation; anchor = quiet p-th percentile; ladder starts at
the decision level; hold sweep recorded-not-asserted on this tier). The gate improved
sharply but is **not clean**:

```
seed 41 (319.5s)  fuzzed 24 / skipped 16 / errored 2   VIOLATIONS: 0   hollow: 0
seed 42 (310.1s)  fuzzed 24 / skipped 16 / errored 2   VIOLATIONS: 0   hollow: 0
seed 43 (317.1s)  fuzzed 24 / skipped 16 / errored 2   VIOLATIONS: 0   hollow: 0
seed 44 (320.6s)  fuzzed 24 / skipped 16 / errored 2   VIOLATIONS: 5   hollow: 0
seed 45 (325.1s)  fuzzed 24 / skipped 16 / errored 2   VIOLATIONS: 0   hollow: 0

violations across 5 seeds: 5      (was 22)
hollow passes: 0
RESULT: FAIL
```

Cause A is confirmed fixed (`smr_up_c4_baseline_brainbit` now SKIPs:
`absolute threshold value did not resolve to a literal`), and differential power is
retained (all three injected engine mutants still caught, now via `NO_CONTRAST`).

**All 5 residual violations are on one realization (seed 44), and every one is a
percentile leaf.** Two shapes:

```
theta_down_cz     below/p30   seed 44:  baseline 0.000 | 0.000 -> 0.000 -> 0.000 -> 0.000
                                        [VIOLATION:NO_CONTRAST] baseline already silent
                              seed 45:  baseline 0.248 | 0.000 -> ...            (clean)

peak_alpha_up_pz  above/p70   seed 44:  baseline 0.872 | 0.647 -> 0.322 -> 1.000 -> 1.000
                                        [VIOLATION:MONOTONICITY]
                              seed 43:  baseline 0.249 | 0.734 -> 1.000 -> ...   (clean)
```

## The structural finding

**For a percentile threshold, the decision level IS the noise level** — it is by
construction a percentile *of the quiet envelope*. Measured:

| threshold | decision level | quiet noise median | ratio |
|---|---|---|---|
| `percentile(p70)` (`micro_single_pct`) | 1.399 µV | 1.128 µV | **1.24×** |
| `absolute(20 µV)` (`micro_single_below`) | 20.0 µV | 2.777 µV | **7.20×** |
| `absolute(8 µV)` (`micro_single_above`) | 8.0 µV | 1.102 µV | **7.26×** |

R6 established that per-realization monotonicity fails while the injected tone is
comparable to the noise (Rician tail-thinning). Combine the two facts:

> There is **no drive amplitude that is both near a percentile boundary and dominant
> over the noise.** The ambiguous band always contains the boundary. Therefore
> per-realization monotonicity of time-in-reward *across a percentile decision boundary*
> is unattainable — not by more seeds, not by a longer window, not by a better ladder.

`peak_alpha_up_pz` seed 44 is exactly this: a realization whose quiet baseline is already
0.872, so rungs at 1× and 2× the anchor sit inside the Rician band and *reduce* the metric
(0.647, 0.322) before the far field carries it to 1.0. Absolute leaves never show this
because their boundary sits ~7× above the noise, in the far field.

Second, smaller finding: for a **`below` + percentile** leaf the no-drive baseline is a
*dwell lottery*. The leaf is true ~30 % of samples by construction, in short bursts;
whether any burst sustains the 250 ms dwell is realization-luck (measured baselines across
seeds: 0.000, 0.014, 0.248). A baseline of 0.000 makes the sweep vacuous, and the
degenerate-baseline guard correctly reports `NO_CONTRAST`. The reward-on state for such a
leaf does not exist without **priming the swept derive's fill** — which `_prime_segments`
deliberately never does (priming the swept derive would flatten its own sweep).

## What survives

- **Contrast is robust.** On seed 44, `peak_alpha_up_pz` passes the contrast test
  (`1.000 − 0.872 = 0.128 ≥ 0.5 × (1 − 0.872) = 0.064`); only monotonicity fails.
- **Contrast alone retains all differential power.** All three injected engine
  regressions (inverted `above()`, latched dwell, dead dwell) are caught by
  `NO_CONTRAST`, per `tests/fuzz/test_engine_regression.py`.
- **Monotonicity is sound for absolute leaves** (far-field boundary): every
  absolute-threshold protocol in the corpus is clean on all 5 seeds.

## Verdict

The tier's *machinery* is sound — fail-loud works, no hollow passes, real engine
regressions are caught, and the wall-clock is tractable. But the design as specified
asserts a property outside its domain of validity (Cause B) using the wrong decision
level (Cause C), and carries one assertion that its own tier cannot support (Cause D),
on top of a pre-existing classification gap (Cause A).

Per the spec: **stop and report before touching the generator further.** Causes B and C
change the spec's stated approach (ladder placement and anchor definition), so they are
a design decision, not an implementation detail. Do not add slack to force the gate
green — that is precisely how the calibrated oracle would have died quietly instead of
loudly.
