# Metamorphic-tier gate — RED baseline (2026-07-09)

Captured on the merged (pre-metamorphic-tier) fuzzer at commit `6d2ef59`, before
any behaviour change. This is the red the new tier must eliminate. Two
independent failure modes, plus a cost finding (R5) and a silent gap:

- **FM1 (deterministic):** `check_metamorphic_monotonic` asserts non-DECREASING
  firing for every swept threshold, which is sign-wrong for `below`/inhibit
  leaves. The plan-era reproduction was `smr_classic_cz_brainbit`'s `theta_t`
  sweep = [57, 56, 57, 56]. **Not reproduced verbatim today** — see below: on
  current HEAD the run aborts with a hollow-pass `VacuityError` on the
  characterization probe *before* the metamorphic sweep executes, so the
  vacuity abort masks the FM1 violation. Still red (exit 2), via a different
  door. The direction-blindness itself is unchanged in
  `src/refrain/fuzz/check.py` (`check_metamorphic_monotonic`).
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
