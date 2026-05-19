# Changelog

All notable changes to Refrain are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/) — minor
bumps are additive; major bumps may break compatibility.

## [Unreleased]

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

[Unreleased]: https://github.com/refrain-lang/refrain/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/refrain-lang/refrain/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/refrain-lang/refrain/releases/tag/v0.0.1
