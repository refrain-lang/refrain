# Protocol fuzzer — the target: a tiered corpus-correctness gate

> Status: north-star / charter spec (what we're building toward). Not a single
> increment — it defines the destination that each increment serves, and states
> honestly what the fuzzer does and does not verify. Supersedes the framing (not
> the content) of the feature-list [[2026-06-28-fuzzer-parity-roadmap-design]],
> which ordered work by feature frequency; hard experience (the calibrated-oracle
> gate finding) showed the real target is the tiered gate below.

## Why this spec exists

The roadmap framed the goal as "support more protocol features until the fuzzer
covers the library." Two things we learned in flight changed the framing:

1. Features are a **per-protocol conjunction** — a protocol only fuzzes when *all*
   its features are supported, so feature-frequency is the wrong ordering axis.
2. Most of the real corpus is **noise-dominated** (thresholds near/below the
   synthetic noise floor). There, firing is decided by the noise realization, and
   **sample-exact prediction is impossible** — an idealized oracle either mispredicts
   or hides the truth behind DON'T-CARE. (Documented in
   `docs/superpowers/ci/calibrated-oracle-gate-finding.md`.)

So "what we want" is not "one oracle that crisply predicts everything." It is a
**gate whose assertions are matched to what is actually assertable per protocol.**

## Purpose

Give both **refrain** and **refrain-protocols** a CI gate that catches regressions in
how Refrain **evaluates** a protocol — for the *whole* protocol library — by
generating synthetic EEG with known content, independently predicting expected
behaviour, running the evaluator, and asserting agreement. The fuzzer's distinctive
value is the **reward-evaluation semantics** (thresholds, percentile ranking,
condition combination, dwell, phase muting, event emission), not generic DSP.

## The target: a three-tier gate

Every protocol lands in exactly one semantic tier, chosen by how much is crisply
assertable; **all** protocols get the structural tier.

### Tier 0 — Structural (every protocol) — DONE (Inc 0 + PR #60)
Runs to completion without crashing; unsupported shapes skip with a specific,
feature-mapped reason; parse/resolve/generation/eval errors are classified, not
fatal; the batch always reports every protocol. This alone is a real gate (catches
crashes, resolve errors, generation bugs, and coverage regressions across the
corpus).

### Tier 1 — Sample-exact (clear-margin / structural protocols) — DONE for supported shapes
Crisp per-sample assertions: SHOULD-FIRE / SHOULD-NOT-FIRE, dwell timing, condition
combination, phase gating. Valid **only where the signal clears the noise by a real
margin.** This is the v1 oracle; it works and stays for these protocols.

### Tier 2 — Metamorphic (noise-dominated majority) — the remaining work
For protocols whose thresholds sit near/below the noise floor, assert **noise-robust
properties** instead of sample-exact fires — primarily **firing-rate monotonicity**
(more in-band signal ⇒ non-decreasing firing across an amplitude/hold sweep), which
holds regardless of the noise realization. The fuzzer already generates these sweeps;
the target makes them the **primary** gate for these protocols (with a noise-tolerant
comparison), and suppresses sample-exact assertions where the margin is below noise
(never silently — such regions are reported, not vacuously passed).

## What the fuzzer verifies — and what it does NOT

- **Verifies:** the reward-evaluation semantics (Tiers 1–2) and structural integrity
  (Tier 0) across the whole corpus.
- **Does NOT verify (by design):** the DSP/filter layer itself. The oracle shares the
  baked SOS (and, where a future crisp-margin tier needs it, the engine's real
  envelope via `record_streams`). DSP correctness is covered by the Rust↔Python
  equivalence gate (golden vectors), the primitive-impl unit tests, and the fuzzer's
  own band-characterization probe (each derive peaks in its declared band).
- **Honest limit:** where behaviour is genuinely noise-arbitrary, the fuzzer asserts
  *trends and implementation-consistency*, not that a specific event is "correct."
  That is the correct assertion for that regime, and it is stated, not hidden.
- **Reference-drift discipline:** the oracle's independent semantic implementations
  must stay separately authored from the engine's eval code, or the differential
  power silently erodes.

## Coverage & success metrics

- **Coverage:** every refrain-protocols protocol is either **fuzzed** (Tier 1 or 2) or
  **cleanly skipped** with a feature-mapped reason — no crashes, no
  `unclassified (<traceback>)`.
- **Green-able CI in both repos:** the batch exits 0 on a clean corpus; a genuine
  engine regression (a real violation, or a metamorphic monotonicity break) fails it.
  False positives from noise are eliminated (that is what Tier 2 buys).
- **Trustworthy coverage number:** `fuzzed / total`, rising as increments land, with
  a by-reason skip breakdown that maps to remaining work. No hollow passes (the
  calibrated-oracle finding: idealized DON'T-CARE that hides real firing is a defect,
  not coverage).

## Where we are vs. the target

- **Tier 0:** done — Inc 0 (#51) + the robustness fixes (PR #60) bring it to the whole
  corpus; the batch runs to completion.
- **Tier 1:** done for the supported shapes — single-condition + center/bandwidth
  (Inc 1, #57); refrain-repo `fuzzed 7 / 26`, CI green.
- **Tier 2:** NOT started — the metamorphic-first increment is the immediate next step
  and the tractable path to the noise-dominated majority.
- **Blocking the corpus, in priority order after Tier 2:** montage-aware synthetic
  channels (the `C3 not in source` eval-errors), then the feature long tail
  (coherence, weighted-composite, inhibit, bandpower, staged) — each a small Inc-1-
  style increment.

## Non-goals

- A single crisp oracle for noise-dominated protocols (proven intractable).
- Detecting DSP/filter bugs via the fuzzer (covered elsewhere).
- Randomised/shrinking fuzzing, Rust-backend parity fuzzing (later substrate work).
- refrain-editor as a fuzz target.

## Immediate next increment (the first step toward Tier 2)

Re-brainstorm and build the **metamorphic-first semantic gate**: detect noise-dominated
protocols; gate them on the existing rank/hold sweeps with a noise-tolerant
monotonicity check; keep sample-exact only where a genuine crisp margin exists; report
(never silently pass) regions with no crisp assertion. Its own spec → plan → build.
