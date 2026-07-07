# Calibrated oracle — Task-0 gate finding (2026-07-07)

The calibrated-oracle plan front-loaded a cheap **gating validation** (Task 0): before
building anything, feed the engine's real per-sample envelope (via
`Evaluator(record_streams=True).last_streams()`) to the oracle's existing layers 2–4
and measure the clean-rate. **The gate failed — and that is the deliverable.**

## Result

Over the refrain-protocols single-leaf targets + 7 clear-margin regressors, at 6
scenarios each (~15 min throwaway compute):

```
CLEAN 0 / 14 single-leaf protocols  (0%)
REGRESSORS: 6 of 7 clear-margin protocols went DIRTY (only smr_cz_brainbit clean)
```

The regressors regressing is the decisive signal: the harness changed **only** the
envelope source (same oracle code), so the real envelope is the cause.

## Root cause

The oracle's downstream layers were built assuming a **clean, piecewise-constant**
envelope (hardcoded 2.0-µV floor + analytic tone steady-state). On a quiet control,
that flat envelope makes the percentile **undefined → DON'T-CARE for the whole
scenario**, which silently **absorbs the engine's real noise-firing** (e.g. `realistic_smr`
fires 7 events on the "quiet" negative control — its percentile leaves are true ~30% of
the time on noise). So the idealized oracle was **passing those scenarios vacuously.**

Feeding the real (noisy, with settle transient) envelope removes that artificial
cushion and exposes:
- **VACUITY** — where the real signal yields no crisp margin, whole scenarios go
  DON'T-CARE and the fail-loud invariant fires.
- **MISSED/SPURIOUS** — the oracle's percentile/threshold implementation and the
  engine's diverge at individual noise crossings.

## What this means

Approach A as scoped ("swap the envelope, layers 2–4 unchanged") **does not work**.
Making it work would require real layer-2–4 recalibration for a noisy input — bigger
and less certain than the spec assumed. More fundamentally: **near-floor / percentile
behavior is noise-dominated, and sample-exact assertions cannot survive noise.**

A deeper truth surfaced: some of the fuzzer's *current* "clean" passes on
noise-sensitive protocols are partly hollow — the idealized envelope manufactures
DON'T-CARE that hides real noise-firing.

## Direction for the next design: metamorphic-first

The fuzzer already generates **metamorphic** assertions (rank / hold sweeps: more
in-band signal ⇒ non-decreasing firing rate) that are inherently **noise-robust**. The
next calibrated-oracle brainstorm should make these the **primary** gate for
noise-dominated protocols and reserve sample-exact assertions for genuine crisp
margins. Target shape — a **tiered corpus gate**:

1. **Structural** (every protocol): runs / skips cleanly, no crashes. (Inc 0 + the two
   fixes in this PR bring this to the whole corpus.)
2. **Sample-exact** (clear-margin / structural protocols): today's crisp oracle.
3. **Metamorphic** (noise-dominated majority): firing-rate monotonicity, noise-robust;
   possibly needing a noise-tolerant tweak to the monotonicity check.

## Validated by-product

The differential mechanism itself works: `Evaluator(record_streams=True)` +
`last_streams()` yields a **bit-exact per-sample envelope** per derive — the right
primitive if a future design needs the real envelope for the crisp-margin tier.
