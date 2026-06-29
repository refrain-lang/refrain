# Reproducibility by construction

Refrain achieves CRED-nf-grade signal-to-feedback reproducibility by
implementing the transformation **once**, validating it against a canonical
reference, and compiling the same code everywhere.

---

## 1. Thesis

A clinical neurofeedback system is reproducible when every deployment of the
same protocol computes the same feedback from the same signal — independent
of language, operating system, or device.

Refrain enforces this by construction:

1. The Python front-end (`parse` → `resolve` → `refrain.ir_json`) compiles a
   `.refrain` protocol into a fully-resolved, coefficient-baked **IR-JSON**
   asset. This is the only step that runs SciPy or touches authoring concerns.

2. The **Rust core** (`refrain-core`) is a single implementation of the
   real-time evaluator. It loads an IR-JSON asset and processes EEG chunks
   into feedback events. It is compiled into:
   - a **PyO3** extension module (`refrain_core`) for desktop tooling and the
     Python embedding path;
   - a **uniffi**-generated **Swift** package (iOS `xcframework`) and
     **Kotlin** library (Android AAR) for mobile apps;
   - a **staticlib** for any other host.

   All targets share the same Rust source. There is no separate mobile
   implementation.

3. The **conformance suite** (`refrain-core/tests/fixtures/`) pins the Rust
   core's behavior to the Python reference evaluator at machine precision.

---

## 2. How reproducibility is enforced

### (a) The conformance suite pins behavior to machine precision

`refrain-core/tools/gen_fixtures.py` runs each protocol in the corpus through
the Python reference evaluator (`src/refrain/eval_.py`) over a seeded
pseudo-random signal and records:

- the IR-JSON wire input,
- the seeded raw signal,
- the reference output streams, feedback events, and `last_taps()` snapshots.

The Rust test suite (`cargo test` in `refrain-core/`) replays each bundle and
asserts agreement within `atol=1e-6, rtol=1e-4`. The observed worst-case
deviation across the entire corpus is approximately **1e-13** (machine
precision). Boolean event and condition streams are compared exactly.

See `docs/CONFORMANCE.md` for the full corpus description and per-fixture
feature coverage.

### (b) The dual-backend drift gate catches behavioral divergence

`refrain-core/tools/check_equivalence.py` is a four-step gate:

1. Regenerates all fixtures from the *current* Python evaluator.
2. Runs the full Rust test suite against the freshly generated fixtures.
3. Builds the `refrain_core` PyO3 wheel from current Rust source.
4. Runs the behavioral evaluator suite (`tests/test_eval_*.py`) under
   `REFRAIN_EVAL_BACKEND=rust`, which routes every Python-path call through
   the Rust core instead.

All four steps must pass. Step 4 catches behavioral drift at the Python API
level (not just at the golden-vector level), making it impossible for
Python↔Rust disagreement to hide behind fixture staleness. This gate runs in
CI as `rust-equivalence`.

### (c) The wire format is pinned by schema and documentation

`src/refrain/schema/ir-json-v0.1.schema.json` (JSON Schema Draft 2020-12)
structurally validates every IR-JSON document. The schema is closed on
`Coeffs` (the one object that must not drift) and open on everything else
(consumers MUST ignore unknown fields).

`docs/IR-JSON.md` documents the full wire contract at the field level: every
`Expr` node variant, all `Coeffs` fields, the compatibility policy (additive
changes are minor-compatible; discriminator or required-field changes are
breaking), and the two host rules that must not be violated (sample-rate
baking and physical channel layout).

---

## 3. CRED-nf framing

For any deployment using Refrain:

- The **protocol identity** is the IR-JSON asset: fully resolved, type-checked,
  with baked DSP coefficients. Two sessions that load the same IR-JSON asset
  and receive the same signal will compute identical feedback, regardless of
  platform.

- The **conformance vectors** are sufficient to verify this claim for any
  specific deployment. A mobile app integrating the Rust core can replay the
  fixture bundles on-device (the same `cargo test` suite, or a thin host-side
  harness) and confirm that the on-device build matches the reference outputs
  within the stated tolerance.

- **Independent of language, OS, and device:** the Rust core is the same
  binary logic on macOS (desktop), iOS, and Android. The uniffi bindings expose
  the same surface (`step_chunk_events`, `set_control`, `last_taps`,
  `start`/`stop`). No platform-specific signal-processing path exists.

A protocol's IR-JSON asset together with the conformance vectors constitutes
a complete, verifiable specification of the feedback computation.

---

## 4. Cross-links

- `docs/IR-JSON.md` — the versioned wire contract: node types, field names,
  coefficient semantics, compatibility policy, and host rules.
- `docs/CONFORMANCE.md` — the golden-vector conformance suite: bundle format,
  self-validation procedure, tolerance methodology, and per-fixture coverage.
- `docs/RUST-CORE-HOST-BRIEF.md` — integration brief for mobile host engineers
  (Swift/Kotlin): the uniffi surface, asset pipeline, and channel-layout rules.
