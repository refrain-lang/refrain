# Changelog

All notable changes to Refrain are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/) — minor
bumps are additive; major bumps may break compatibility.

## [0.12.1] — 2026-07-07

Service plumbing for mode-variant baking (portal "resolve-time mode control"):
`POST /compile` now accepts `bindings` and echoes what it applied. No IR-JSON
schema changes; the no-bindings path emits byte-identical canonical IR (pinned
by test), so existing `content_hash` values do not move.

### Added
- **`bindings` on `POST /compile` and `compile_to_ir_json()`.** Passed verbatim
  to `resolve(composed, bindings=...)`, covering mode and placement controls.
  Invalid bindings surface as ordinary `stage="resolve"` diagnostics (HTTP 200).
- **`meta.bindings` echo.** The response `meta` reports exactly the applied
  bindings (`{}` when omitted). This is a capability gate for callers: pydantic
  ignores unknown request fields, so an older service image would silently
  return default-variant IR — callers baking variants must fail closed unless
  the echo matches what they sent.

### Changed
- **Unknown binding names are now rejected.** `resolve(..., bindings=...)`
  raises `ResolveError` when a key does not name a declared `mode` or
  `placement` control (previously it was silently ignored — a caller could
  mistake default IR for the requested variant). Bindings for other control
  kinds (session-time numeric controls) are rejected the same way.

## [0.12.0] — 2026-07-05

Additive — a new `mode` control that lets one protocol select a threshold's
`type` (percentile ↔ absolute, i.e. adaptive ↔ baseline) or an output binding
(gated ↔ ungated) from a value bound at resolve time. Modeled on the existing
`placement` control; the selection folds away during resolution, so the IR-JSON
schema and the Rust runtime are unchanged. No existing protocol changes behavior.

### Added
- **`mode` control kind.** `x = mode { choices = ["a", "b"]; default = "a" }` —
  a categorical, resolve-time-bound selector (never `live_tunable`), bound via
  the same `bindings=` mechanism as `placement`, validated against `choices`,
  honoring `final`.
- **Resolve-time selection folding.** A ternary whose condition compares a mode
  control to a string literal (`mode == "x" ? a : b`) is evaluated at resolve
  time and replaced by the chosen branch — wired into threshold `type` and output
  bindings. Non-mode ternaries are untouched and still resolve as runtime
  conditionals.
- **Host surfacing.** `describe_protocol` returns mode controls in a `modes`
  list; mode controls are excluded from the emitted IR-JSON `controls` map (they
  resolve away, like `placement`).

## [0.11.0] — 2026-06-21

Additive — a new mobile-binding method plus editor-catalog coverage that brings
the bundled BrainBit set to full clonability. No existing protocol changes
behavior; Python↔Rust parity (gated to 1e-6) and the IR-JSON schema are
unchanged. The editor changes are front-end catalog/model additions that
round-trip to identical IR; the mobile change mirrors an existing pyo3 binding.

### Added
- **`last_taps()` on the mobile (uniffi) binding.** Surfaces the evaluator's
  most-recent-chunk internal taps (envelope / threshold / reward / output) across
  the FFI as a Swift `[String: Double]` / Kotlin `Map`, mirroring the pyo3
  binding — for on-device clinician observation and live tuning. Regenerated the
  Swift and Kotlin bindings. Consumed by the Coherence Companion mobile
  neurofeedback feature.

### Editor catalog — full BrainBit clonability (20/20)
Front-end (refrain-editor) catalog/model additions only; each shape round-trips
to an identical IR. The bundled BrainBit set went from 3/20 → 20/20 protocols
that clone with clean IR round-trips.
- **Compound operant reward + EMG artifact inhibit + lossless `requires`**
  (3 → 11): `reward.operant_compound` (`dwell(all_of([...]))` + sigmoid
  continuous), an EMG-style `inhibit.artifact` (bandpower + percentile + mute),
  `reward.component_ratio` (weighted composite over a ratio sigmoid), and full
  preservation of `requires` (coupling/impedance/markers, not just
  sample_rate/channels).
- **Staged blocks + alpha-asymmetry derives** (→ 16): a `block "name" {...}` decl
  + per-phase `block = "..."` activation (new `blocks` model section),
  `derive.difference` (`"a" - "b"`) and `derive.rectify`, and `reward.operant_abs`
  (single-condition sigmoid-over-ref continuous).
- **Placement controls — site/montage editing** (→ 20): placement controls
  (active/set/bipolar/pair) with a new `placements` model section, inlined
  `groups`, `montage.bipolar` with `coh.a`/`coh.b` member-access slots, and a
  handful of placement-specific shapes (positional `absolute(8 uV)`,
  `combine = "all"` set reward, window-less `coherence(...)`, compound
  bare-ref-sigmoid reward).
- **Explicit-band envelope block (`derive.envelope_band`).** Recognizes the
  clinical fixed-band form `bandpass(band: (lo Hz, hi Hz), order: N)` as a second
  envelope shape alongside the center+ratio derive; band edges and order become
  editable model slots.
- **Golden-fixture generator (`tools/gen_editor_fixtures.py`).** Committed the
  parity-corpus generator that refrain-editor's `scripts/sync-vendor.sh` already
  referenced; recurses into `protocols/` subfolders so device-specific sets are
  included.

## [0.10.0] — 2026-06-12

Additive — a new primitive, a new fan-out axis, and a worked example. Python↔Rust
parity is preserved (gated to 1e-6); the IR-JSON schema gains an additive
`lag_samples` coeff field (v0.1 + v0.2). No existing protocol changes behavior.

### Added
- **`autocorr(lag, window)` primitive** — rolling lag-k Pearson autocorrelation,
  the critical-slowing-down early-warning indicator (Scheffer 2009; Maturana
  2020). Full shared-core: Python reference impl + Rust `Autocorr` `Stage` at
  machine-precision parity (`micro_10_autocorr` equivalence fixture) + baked
  `window_samples`/`lag_samples` coeffs.
- **`bands { }` block + band-axis fan-out** — declare named frequency bands and
  author a band-parameterized subgraph once (`bandpass(band: bands)`); the
  resolver replicates it per declared band (`<name>@<band>`). Composes with the
  per-site `set` fan-out for the **band × channel cross product**
  (`<name>@<band>@<site>`); inhibits now replicate on both axes. Front-end only;
  IR-JSON/Rust core unchanged. See SPEC §4.9.3.
- **`examples/critical_fluctuation_cue.refrain`** — a dynamical critical-fluctuation
  cue protocol: an early-warning detector (variance + autocorrelation) per band per
  site (e.g. 10 bands × 2 sites = 20 envelopes; any site(s), defaulting to one
  midline electrode) against a self-calibrating percentile threshold, and a tonic
  reward muted by per-envelope critical-fluctuation inhibits (the audio cue). Design
  spec: `docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md`;
  gap RFC: `docs/proposals/2026-06-11-dynamical-neurofeedback-gaps.md`.

## [0.9.0] — 2026-06-08

Additive — no existing protocol changes behavior; Python↔Rust parity and the
IR-JSON schema are unchanged (the new kind is front-end-only: it resolves to
`DIMENSIONLESS`, and the Rust core does not read control `type_kind`).

### Added
- **`number` control kind** — a unitless scalar control. The honest kind for
  relative weights and gains: same `DIMENSIONLESS` dims as `percent`, but a
  host renders it with **no unit** (where `percent` shows a misleading "%").
  Use it for weighted-composite weights and any dimensionless knob whose value
  is not actually a percentage. The value is used raw (no `/100`).

## [0.8.0] — 2026-06-04

HRV biofeedback support for the Coherence Recorder (M1). All additive — no
existing protocol changes behavior, the IR-JSON schema is unchanged, and
Python↔Rust parity is preserved (gated to 1e-6). Consumers opt in by moving
their pin.

### Added
- **`passthrough()` montage.** A first-class single-channel identity montage —
  carries one raw channel through unchanged (e.g. a non-EEG HRV tachogram),
  replacing the `referential(reference: "device")` workaround. Implemented in
  both the Python evaluator (`PassthroughImpl`) and the Rust core
  (`Montage::Passthrough`), with the `micro_passthrough_identity` parity
  fixture. Requires a single-channel source.
- **Cross-session adaptive-state seed/export.** `Evaluator.export_state()`
  returns a compact, rate-independent summary of every `auto_range` /
  `percentile` tracker (`{low, high, n_eff}` / `{value, target_pct, n_eff}`,
  keyed `"<entity>.<callee>"`); `Evaluator.live(..., seed_state=...)` (and the
  `RustEvaluator` constructor) re-primes a run from a prior session's export.
  This is the longitudinal "user-adaptive ceiling that rises across sessions"
  signal. **Runtime state, not IR** — the protocol IR-JSON is unchanged.
  Available on both backends with parity; seeding pre-fills the rolling window
  with a deterministic synthetic distribution.
- **Low-Fs envelope guidance.** `rectify() + smooth(tau)` is documented and
  validated as the sanctioned low-latency envelope for low-sample-rate signals
  (e.g. a 4 Hz tachogram), where the FIR Hilbert's 8 s group delay is
  prohibitive.

### Changed
- `hilbert(kind="iir_allpass")` now raises a clear, actionable error pointing to
  `rectify()+smooth()` (low Fs) / the default FIR (mid-band). It remains
  intentionally unimplemented: a low-latency IIR Hilbert is accurate only
  mid-band and useless near DC, where all real NF/HRV bands sit (see
  `docs/DESIGN-NOTES.md` §7a).

### Notes
- The Rust core's PyO3 (wheel) surface gained `export_state()` and an optional
  `seed_state` constructor argument. The uniffi (Swift/Kotlin mobile) exposure
  of seed/export is deferred as an additive follow-up; existing mobile call
  sites are unaffected.

## [0.7.0] — 2026-05-29

### Added
- **Staged / segmented protocols (R1–R4 + R6).** A `.refrain` protocol can now
  describe a clinical session as N short training blocks with rests between
  them, taken against one baseline up front, with blocks training different
  things in one sitting (e.g. beta-up then alpha-up).
  - **N-phase runtime + three phase types.** `session.phases` is now a real
    runtime state machine (not metadata past the first phase). Each phase
    carries a `mode`: `timed` (auto-advance after `duration`), `open` (no
    clock; the host advances it), or `timed_with_floor` (auto-advance, but the
    host may end it early or extend it). Mid-session `output_muted` rests now
    actually mute (previously only the first phase did).
  - **Host transport API** (Python `Evaluator` + Rust core, pyo3 + uniffi):
    `advance_phase()` (clinician "Next →"), `hold(held=True)` (extend a
    `timed_with_floor` block; `hold(False)` re-arms), `set_clock_frozen(bool)`
    (pause/resume the phase clock on any phase type, orthogonal to output
    muting), and `current_phase()` introspection (index/name/mode/block/
    remaining_s/clock_frozen/held), aligned with `last_taps`. New numeric
    `phase/index` + `phase/output_muted` taps. Generalises the offline-only
    `skip_warmup` into a supported live control.
  - **Named blocks + reward bundles.** `block "<name>" { threshold; reward;
    output; inhibit }` selects which named threshold / reward bundle / output
    channels / inhibits are live during a phase; reward bundles are declared as
    `reward "<name>" { continuous?; event? }` (disambiguated from weighted
    components by field shape). Derives and threshold impls stay global and
    always-computed — a block gates *emission/selection*, never *computation*,
    so signals stay warm across block boundaries.
  - **Adaptive `percentile()` windows freeze across muted rests (R6):** the
    warmup phase populates the window and active phases ingest, but later
    `output_muted` rests do not, so rest-period artifact can't pollute a later
    block's window.
  - IR-JSON gains `phase.mode`/`phase.block`, top-level `blocks` and
    `reward_bundles` (all additive; the v0.2 schema is extended, not bumped). A
    protocol using blocks/bundles emits `refrain_ir_version: "0.2"`.
  - New example `examples/staged_beta_alpha.refrain`; full Python↔Rust parity
    suite for staged sessions (`tests/test_staged_parity.py`).

### Notes
- The one-shot baseline measure-then-freeze is recorder-side (control-backed
  `absolute(value: <control>)` + `set_control` at the single warmup→run); the
  engine's obligation is that derives/threshold impls stay global so a
  `set_control` seeding an inactive block's threshold takes effect — guarded by
  the parity suite.

## [0.6.3] — 2026-05-28

### Fixed
- **`absolute(value: <control_ref>)` is now accepted by the Rust core, in
  parity with the Python evaluator.** Previously the Rust core's
  `absolute_value` only matched a literal `number` for the `value:` arg and fell
  through to the positional path, panicking at `refrain-core/src/eval.rs:1153`
  with `missing positional arg 0` whenever a clinician knob was bound into a
  baseline-fixed threshold (the reported repro: `threshold "smr_t" { signal =
  "smr_envelope"; type = absolute(value: smr_threshold_uv); live_tunable = true }`
  with a `voltage`-typed control). Affects threshold blocks AND inhibits, since
  both share the same constructor parser.
- **`Evaluator.set_control(...)` now retunes an `absolute(value: <control_ref>)`
  threshold's constant** on both backends. Previously
  `AbsoluteThresholdImpl` had no `update_control` method, so the Python
  evaluator's `_control_deps` loop silently no-op'd and the threshold stayed at
  its construction-time default. The Rust core now binds a `Control::Const`
  sharing the threshold's cell so a live retune is observed in the next chunk
  without rebuilding state (parallel to `Control::Percentile` / `Sigmoid` /
  `Smooth` / `Weight`).

## [0.6.2] — 2026-05-26

### Changed
- **Rust-backend panics are now catchable.** Any panic in the Rust core (e.g. an
  unsupported-in-Rust construct reached on `backend="rust"`/`"auto"`) is converted
  to a Python `RuntimeError` at the PyO3 boundary instead of propagating as an
  uncatchable `pyo3_runtime.PanicException` — so an embedding host can `except`
  it and fall back to `backend="python"` rather than having the panic take down
  the process.

### Documentation
- Documented the v0.2 weighted-reward tap/stream keys — `reward/composite`,
  `reward/component[<name>]` (and the dotted `last_streams` equivalents
  `reward.composite` / `reward.component.<name>`) — in the EMBEDDING.md tap table
  and SPEC §7.8.
- Marked EMBEDDING.md's "Research mode" host API (`chunk_transformer=`, `sham=`,
  `evaluator.allocation_token`) as **forthcoming** — it is not part of the shipped
  0.6.x `live()` signature (which is `record_streams=` + `backend=` beyond the
  required args).

## [0.6.1] — 2026-05-26

### Fixed
- `coherence(...)` is now runnable in **live/push mode** with **positional**
  stream inputs (e.g. `coherence("a","b", band: …)`) on both backends. It
  previously resolved but crashed on the first `step_chunk(...)` — python:
  `CoherenceImpl.step() missing x_a/x_b`; rust: `coherence: missing baked
  coeffs` (an uncatchable panic). The resolver now canonicalizes coherence's
  positional inputs to named (`input_a`/`input_b`), so the positional form
  resolves to the same IR as the explicit named form. (The named form was
  already fine.) Python↔Rust parity confirmed at machine precision.

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
