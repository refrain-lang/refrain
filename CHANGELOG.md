# Changelog

All notable changes to Refrain are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/) — minor
bumps are additive; major bumps may break compatibility.

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

## [Unreleased]

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

## [0.4.0] — 2026-05-25

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

[Unreleased]: https://github.com/refrain-lang/refrain/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/refrain-lang/refrain/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/refrain-lang/refrain/releases/tag/v0.0.1
