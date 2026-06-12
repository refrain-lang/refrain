# RFC: Dynamical-neurofeedback gaps surfaced by the critical-fluctuation cue protocol

> Status: proposal / discussion. Companion to the design spec
> `docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md`, the
> `autocorr` primitive, the `bands` fan-out, and `examples/critical_fluctuation_cue.refrain`.
> Captures what a dynamical, early-warning-signal protocol wants that Refrain does
> not yet express cleanly — including several gaps found while *implementing* the PoC.

## Context

`critical_fluctuation_cue.refrain` implements dynamical, early-warning-signal
neurofeedback — detect a **critical fluctuation** (an early-warning surge in EEG
variability) and deliver a brief audio **cue** (an interruption) at the critical
window — using two new language pieces (`autocorr`, band fan-out) plus existing
primitives. It runs end-to-end on both backends. Building it surfaced the gaps
below; none block the PoC, and each has a documented workaround in the shipped
example.

## A. Scope deferred from the design (known going in)

### A1. Session phases / macro-level adaptive flow

A clinical session often flows through phases (warm-up → letting-go → working-deeply
→ release), with an adaptive flow navigating between them and retuning
targets/sensitivity moment to moment. The PoC implements only the **micro** level of
that adaptation — the self-calibrating `percentile` threshold per envelope (the
adaptive, self-calibrating baseline). The **macro** arc (different band/sensitivity
configs over a session, with automatic transitions) is unmodeled. Refrain already
has `session { phases }` and staged/segmented protocols; a macro-level adaptive flow
would add **condition-driven** phase transitions and per-phase sensitivity. Out of
scope until a concrete clinical ask.

### A2. Infra-low / sub-Hz bands

Some dynamical-neurofeedback systems claim coverage to 0.001 Hz. The PoC caps at
1 Hz: variance and autocorrelation at sub-Hz need minute-to-many-minute windows,
impractical for a live cue. Refrain *can* do sub-Hz (the Othmer ILF example trains a
single slow band to 0.0001 Hz with DC coupling) — but as one slow reward band, not as
many fast variance/CSD detectors. Document the regime boundary; infra-low
early-warning detection is a distinct slow-detector design (long windows, DC
coupling, decimation).

## B. First-class cue feedback (the biggest ergonomic gap)

The PoC expresses pure negative feedback by a workaround: a contingency-free
`reward { continuous = 1.0 }` muted by per-envelope `inhibit … mute(release:)`.
This is clean and *verified* (the behavioral test confirms `mute` gates the
constant output on both backends), but semantically inverted — `inhibit` was
designed for artifact rejection, not as the primary feedback channel.

**Proposal:** a first-class output form for "on by default, briefly interrupted on
an event," e.g.

```refrain
output { audio_gain = cue(on: <event/boolean stream>, hold: 400 ms) }
```

`cue(on, hold)` = 1.0 normally, 0.0 for `hold` after each rising edge of
`on`. This removes the degenerate-reward requirement and, crucially, accepts an
**arbitrary boolean/event stream** — which also resolves B2 below.

### B1. Inhibit `metric` as a direct stream reference

An inhibit's `metric` must today be a **primitive call** (e.g. `bandpower(...)`),
not a reference to a pre-computed derive. The critical-fluctuation detector
thresholds a computed `score` derive, so the PoC writes `metric = rectify("score")`
— `rectify` is an identity on the `[0,1]` score that satisfies the "metric is a
call" rule. **Proposal:** allow `metric = "score"` (a stream-ref) directly. The
resolver already has `_resolve_string_as_reference`; the change is small on the
front end, but the evaluator (both backends) must also accept a stream-ref metric —
so it is its own slice, not free.

### B2. AND-combine over arbitrary booleans

The PoC tunes OR↔AND by fusing *normalized* indicators into one score and
thresholding that (the shipped fuse is the probabilistic-OR `a + b - a*b`). A
general "fire when condition A AND condition B, each with its own adaptive
threshold" needs a boolean → feedback path. `all_of`/`any_of` produce booleans but
the only boolean→mute path today is the inhibit metric/threshold form. The
`cue(on:)` output (B) closes this: `on = all_of([above(varN, tv), above(acN,
ta)])`.

## C. Expression / typing gaps (found during implementation)

### C1. `smooth`/`rectify` lose dims in *formula* position

`_identity_input` (and `_magnitude_output`) resolve a primitive's input via the
`__input__`/`signal`/`input` arg keys, which are only set in **pipeline** form.
Called as a *formula* function with a positional stream — e.g.
`smooth("env" * "env", tau: 1 s)` — the input isn't found and the output type
falls back to `uV`, dropping the squared unit. The PoC works around this by
computing squares in formula derives and running every `smooth` in **pipeline**
form (where dims track correctly), turning a 1-line variance into a 5-derive
chain. **Proposal:** make identity-typed primitives read a positional first
stream argument when no named input key is present.

### C2. Rust core rejects raw comparison binops

`(varN > acN) ? varN : acN` (a `max` idiom) resolves and runs on the Python
backend but panics on the Rust core (`apply_binop` rejects `<`, `>`, `==`;
protocols are expected to use `above`/`below`/`inside`). The PoC avoids it with
the arithmetic probabilistic-OR fuse. **Proposal:** either support comparison
binops in the Rust core, or document that raw comparisons are Python-only and
steer authors to `above`/`below` (and arithmetic fuses) for portability.

## D. Detector methodology (`autocorr` follow-ups)

- **`autocorr` detrend (deferred from the primitive).** v1 `autocorr(lag, window)`
  mean-centers within its window; it does not remove a slow linear trend. The EWS
  literature detrends (Gaussian-kernel or first-difference) first. Proposal: add
  `detrend: bool = true` (within-window linear detrend) and/or a `decimate`
  primitive so CSD can be computed at a chosen timescale without the
  oversampling-inflation footgun (lag-1-sample ≈ 1 at 256 Hz).
- **Higher-level `early_warning()` composite.** Optionally bundle variance + ρ₁
  into one indicator primitive once the two-indicator pattern is validated —
  collapsing the PoC's per-envelope EWS sub-chain.

## E. Fan-out generalization

The band fan-out (shipped) plus the per-site pass already produce the band ×
site cross product by composition (`<name>@<band>@<site>`). A general N-axis
fan-out (arbitrary author-declared replication axes, declared combine semantics
per axis) would subsume both; defer until a third axis is motivated.

## Non-goals

This RFC does not propose reproducing any vendor's proprietary, closed
dynamical-systems math. The critical-fluctuation cue work is a transparent,
citable implementation of the *mechanism*, not a clone, and is research software,
not a medical device.
