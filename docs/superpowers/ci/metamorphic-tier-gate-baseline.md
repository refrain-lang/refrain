# Metamorphic-tier gate — RED baseline (2026-07-09)

Captured on the merged (pre-metamorphic-tier) fuzzer at commit `6d2ef59`, before
any behaviour change. This is the red the new tier must eliminate. Two
independent failure modes, plus a cost finding (R5) and a silent gap:

- **FM1 (deterministic, currently MASKED):** `check_metamorphic_monotonic`
  asserts non-DECREASING firing for every swept threshold, which is sign-wrong
  for `below`/inhibit leaves. The direction-blindness is plainly visible in
  `src/refrain/fuzz/check.py` (the comparison `series[i][1] < series[i-1][1]`
  ignores the leaf's op). It does **not** fire today, for two compounding
  reasons: the probe's `VacuityError` aborts multi-leaf percentile protocols
  before the sweep runs (Step 1), and where the sweep *does* run, the saturated
  ladder holds the below-leaf series flat so it never decreases (Step 4). The
  plan-era reproduction `[57, 56, 57, 56]` was not reproducible on this HEAD.
  FM1 becomes live the moment the ladder is fixed — see Step 4.
- **FM2 (systematic):** the metric is event count, which counts dwell
  re-triggers. Every noise dip that recovers adds an event, so event count runs
  BACKWARDS in drive. Measured on `micro_single_pct`: [12, 16, 9, 9] — the
  weakest drive fires the most events. Non-monotone on 5/5 seeds (10/10 across
  both single-leaf micros). Recorded in the spec addendum:
  `docs/superpowers/specs/2026-07-08-fuzzer-metamorphic-tier-design.md`.

## Step 1 — `smr_classic_cz_brainbit` (percentile `above` + `below` leaves)

```
$ .venv/bin/python -m refrain.cli fuzz \
    /Users/jcroall/git/refrain-protocols/protocols/brainbit/smr_classic_cz_brainbit.refrain \
    --library /Users/jcroall/git/refrain-protocols/lib
GENERATOR BUG: scenario 'probe:tone_13.5hz': zero crisp assertions (no SHOULD-FIRE samples and the timeline is fully DON'T-CARE). This is a generator bug, not a pass.
# exit status: 2
# wall clock: 2:05.87 (125.48s user) — and it ABORTED at the probe, before the rank sweeps
```

That single line is the entire output. `fuzz_protocol` runs scenarios in order
directed → probe → rank_sweep → hold_sweep and lets `VacuityError` propagate
out of the per-scenario loop, so the hollow-pass abort on `probe:tone_13.5hz`
pre-empts `check_metamorphic_monotonic` entirely. Today's red on this protocol
is therefore a **hollow pass** (generator-bug), which the new gate harness
counts as a gate failure in its own right.

## Step 2 — `micro_single_pct` (single percentile-leaf reward)

```
$ .venv/bin/python -m refrain.cli fuzz bench/protocols/micro_single_pct.refrain
SKIPPED (unsupported: single percentile-leaf reward (needs calibrated oracle))
# exit status: 0
# wall clock: 0.65s
```

Exit 0 on a skip — this protocol is **silently unfuzzed** today. Removing this
skip (by giving percentile-leaf protocols a real metamorphic tier) is the point
of this increment.

## Step 3 — R5 cost evidence

One protocol with 2-minute percentile windows costs **125.5 s of CPU** and it
did not even reach its sweep scenarios (aborted at the probe). The pre-change
fuzzer fills every declared percentile window before each spike, ~11 s of wall
clock per scenario at ~25 scenarios per such protocol; the full 42-protocol
corpus is hours of wall clock. That cost is itself a finding this increment
fixes (spec addendum R5), so the full-corpus red was not captured — the
decisive per-protocol reds above stand in for it.

## Step 4 — the sweeps, run directly (bypassing the probe abort)

The probe's `VacuityError` hides what the sweep machinery actually does, so the
rank sweeps were driven through the **current, merged** `generate_rank_sweep` +
`check_metamorphic_monotonic` with the probe skipped. `realistic_smr` has both a
percentile `above` leaf (`smr_t`) and a percentile `below` leaf (`theta_t`):

```
rank_sweep:smr_t:amp_5     n_events=10
rank_sweep:smr_t:amp_15    n_events=10
rank_sweep:smr_t:amp_25    n_events=9
rank_sweep:smr_t:amp_40    n_events=9
rank_sweep:theta_t:amp_5   n_events=7
rank_sweep:theta_t:amp_15  n_events=7
rank_sweep:theta_t:amp_25  n_events=7
rank_sweep:theta_t:amp_40  n_events=7

OLD check_metamorphic_monotonic -> 1 violation(s):
  [metamorphic:rank_sweep:smr_t] amp_5=10 -> amp_15=10 -> amp_25=9 -> amp_40=9
```

Two failure modes, on one protocol, in one run:

- **FM2 false-fails an `above` leaf.** `smr_t`'s direction is *correct* and it
  still violates, because the metric is event count: more drive means fewer
  dwell re-triggers, so firing *decreases* with drive (10, 10, 9, 9). The
  reported violation is spurious — the engine is fine.
- **FM3/FM4 hollow-pass the `below` leaf.** `theta_t` is dead flat: [7, 7, 7, 7].
  The fixed 5/15/25/40 µV ladder sits entirely above theta's ~2.7 µV noise
  floor, so every rung saturates the leaf to FALSE and the reward never moves.
  A sweep that cannot move the metric asserts nothing, and the merged checker
  scores it as a pass.

**FM1 is masked by FM3.** The direction-blind assertion (non-decreasing for
*every* swept threshold) cannot fire on `theta_t` while the ladder keeps its
series flat. Anchor the ladder at the derive's real decision level and the
below-leaf series becomes decreasing — at which point the old assertion would
false-fail it on every seed. The two bugs must therefore be fixed together:
fixing the ladder alone would *introduce* a visible FM1 red where there is
currently a silent hollow pass.

## Step 5 — why the suite never caught any of this

`tests/fuzz/test_runner.py:24` defaults to `max_scenarios=2`, and
`tests/fuzz/test_batch.py` passes `--max-scenarios 2`. The corpus is built
directed → probe → rank_sweep → hold_sweep, so a cap of 2 truncates *before the
first sweep scenario*. The merged test suite never executes a rank sweep at all,
which is why a spurious violation and a hollow pass both sit green in CI.

The cap also silently drops members from a sweep group, which would corrupt the
monotonicity comparison. In the new design the cap applies to oracle scenarios
only, and never truncates a sweep group.

## Gate harness status

`tools/fuzz_corpus_gate.py` is committed alongside this doc, written against
the *target* API. On current code it fails as expected:

```
$ .venv/bin/python tools/fuzz_corpus_gate.py --corpus ... --seeds 41
TypeError: run_batch() got an unexpected keyword argument 'seed'
```

It starts working at Task 6 (seeded `run_batch`) and must print `RESULT: PASS`
(exit 0) at Task 8: zero metamorphic violations and zero hollow passes across
seeds 41–45, with parse/resolve/eval errors counted separately as pre-existing
corpus gaps.
