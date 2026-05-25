# M5 — Validation, schema & reproducibility docs (design)

> Status: approved design, ready for an implementation plan.
> Milestone M5 of `docs/superpowers/plans/2026-05-24-rust-core-production-roadmap.md`.
> Builds on `main` (commit 0c95697), which has the complete Rust core (M1a–M4 + M3c).

## Goal

Make the IR-JSON wire format a **versioned, independently-verifiable contract**, and publish the
existing golden vectors as the **conformance suite** any future runtime must pass — so a third party
can validate any implementation of the signal→feedback transformation without our code. This is
documentation + one schema artifact + one gate step; **no evaluator behavior changes.**

## Scope boundary

In scope: a human-readable wire spec, a machine-readable JSON Schema (validated against the committed
golden vectors and gated in CI), a conformance-suite doc over the *existing* `refrain-core/tests/fixtures/`
corpus (no file duplication), a standalone CRED-nf reproducibility doc, and a behavior-neutral
DESIGN-NOTES note + CHANGELOG entry.

Out of scope: any change to the evaluator, `ir_json.py` emission, the Rust core, or the fixtures
themselves; copying/relocating the golden vectors; a new bundling/export tool; bumping
`IR_JSON_VERSION` (stays `"0.1"`).

## Current state (what exists)

- `src/refrain/ir_json.py` emits `refrain_ir_version: "0.1"` (`IR_JSON_VERSION`) and its module docstring
  already says *"See `docs/IR-JSON.md` for the schema"* — but that doc does **not** exist yet.
- The conformance corpus already lives at `refrain-core/tests/fixtures/`: per protocol,
  `<stem>.ir.json` (the wire input), `<stem>.io.json` (seeded input signal + reference output streams),
  and where applicable `<stem>.events.json`, `<stem>.taps.json`, plus the `realistic_smr_setcontrol.*`
  scenario (events/taps/schedule). The Rust tests (`equivalence.rs`, `events.rs`, `taps.rs`,
  `set_control.rs`, `ir_deser.rs`) already replay these.
- `refrain-core/tools/check_equivalence.py` is the four-step drift gate (gen_fixtures → cargo test →
  build wheel → dual-backend pytest), run by the CI `rust-equivalence` job.
- `docs/` already has SPEC.md, DESIGN-NOTES.md, EMBEDDING.md, RUST-CORE-HOST-BRIEF.md, etc.

## Deliverables

### D1 — `docs/IR-JSON.md` (new): human-readable versioned wire contract

Sections:
- **Version & compatibility policy.** What `refrain_ir_version "0.1"` denotes; the rule for when the
  minor/major bumps (additive/optional fields vs. breaking discriminator/semantics changes).
- **Two host rules** (formalized from the README): (1) the sample rate is *baked* — coefficients are
  designed for the runtime rate, which can differ from the resolver's `sample_rate_chosen_hz`; the
  protocol only declares a minimum. (2) `channels` passed at runtime is the *physical acquisition
  layout* (incl. reference electrodes), not the protocol's required channels.
- **Top-level object** (`Protocol`): name, `refrain_ir_version`, `sample_rate_hz`, `channels`, inputs,
  derives, thresholds, inhibits, reward, output, session/phases, controls, `topological_order`, meta.
- **The `Expr` tagged union**: enumerate each discriminator/variant the Rust `ir.rs` deserializes
  (call, binop, conditional, array, stream/threshold/control refs, literals, reward-field access,
  member access) with field tables.
- **Baked-coefficient fields**: the `Coeffs` shapes — biquad SOS cascades, FIR (Hilbert) taps — and
  `control_ref { target, default }` for live-tunable args. State that Python owns SciPy filter *design*;
  the wire format carries the *designed* coefficients; the runtime only runs the recurrence/convolution.
- **Worked example**: an annotated excerpt of a real committed `*.ir.json` (e.g. `realistic_smr.ir.json`)
  walking through a montage → derive(bandpass+hilbert+magnitude+smooth) → percentile threshold →
  reward → output.
- **Pointers** to the JSON Schema (D2) and the conformance suite (D3).

### D2 — JSON Schema (new): `refrain-core/schema/ir-json-v0.1.schema.json`

- JSON Schema **draft 2020-12**, modeling the IR-JSON structure (objects, the `Expr` discriminated
  union via `oneOf`/`if-then` on the tag field, required fields, coefficient shapes). It validates
  *structure/shape*, complementing the conformance vectors which validate *runtime behavior*.
- **New Python test** `tests/test_ir_json_schema.py`: load the schema and assert **every committed
  `refrain-core/tests/fixtures/*.ir.json` validates** against it (using the `jsonschema` library).
  Also assert a hand-built malformed sample is rejected (so the schema isn't vacuously permissive).
  Add `jsonschema` to the `dev` optional-dependencies in `pyproject.toml`.
- The schema describes `refrain_ir_version "0.1"`; filename carries the version so future versions add
  a sibling file.

### D3 — `docs/CONFORMANCE.md` (new): the conformance suite, documented in place

- Declares `refrain-core/tests/fixtures/` the **canonical conformance corpus** (no copy → no drift).
- **Bundle format** per protocol: the `.ir.json` / `.io.json` / `.events.json` / `.taps.json` /
  `_setcontrol.*` files and exactly what each contains (and the JSON shape of each).
- **How any runtime self-validates**: deserialize the `.ir.json` at the `sample_rate_hz` and `channels`
  given in the `.io.json`; feed the seeded input chunked; compare produced streams/events/taps against
  the references.
- **Tolerance methodology**: `atol=1e-6, rtol=1e-4` (the bench harness tolerance); observed worst case
  across the corpus ~1e-13 (machine precision); boolean event/condition streams compared exactly.
  Note these are regenerated from the *current* Python evaluator by `gen_fixtures.py` and gated by
  `check_equivalence.py`.
- **Coverage table**: map each `micro_01..09` + `realistic_smr` (+ the `set_control` scenario) to the
  primitive(s)/feature it exercises.

### D4 — `docs/REPRODUCIBILITY.md` (new, standalone): CRED-nf reproducibility-by-construction

- The argument: there is *one* canonical signal→feedback transformation, implemented once in the Rust
  core and compiled everywhere (PyO3 for desktop/tooling, uniffi for Swift/Kotlin, staticlib for
  mobile), validated by the conformance suite (D3) and the dual-backend gate (Python `backend="python"`
  vs `backend="rust"`). Reproducibility is *by construction*, not by convention.
- Frame in CRED-nf terms (reproducibility of neurofeedback computation); cross-link IR-JSON.md,
  CONFORMANCE.md, and the host brief.

### D5 — Gate the schema + spec hygiene

- Extend `refrain-core/tools/check_equivalence.py` with a step that runs the schema-validation test
  (`tests/test_ir_json_schema.py`) — reuse the existing `_run` helper + `results` dict, so a
  schema/golden mismatch fails the gate (and thus CI's `rust-equivalence` job). The plain `test` job
  picks the test up via normal pytest collection too.
- Add the **behavior-neutral coefficient-baking note** to `docs/DESIGN-NOTES.md` (Python designs filters
  with SciPy; coefficients are baked into IR-JSON; the runtime only runs the recurrence — no SciPy in
  the core).
- Add a `CHANGELOG.md` entry flagging the new IR-JSON spec + conformance artifacts.

## Verification / exit criteria

- `tests/test_ir_json_schema.py` green: every committed `*.ir.json` validates against the schema; a
  malformed sample is rejected.
- `check_equivalence.py` passes with the new schema step (now five steps); CI `rust-equivalence` green.
- Default `pytest -q` still green; `cargo test` green; 0 warnings — no behavior change.
- `docs/IR-JSON.md` no longer a dangling reference; CONFORMANCE.md + REPRODUCIBILITY.md exist and
  cross-link.
- **Independent-validation check (the real exit bar):** the spec + schema + CONFORMANCE.md + the
  fixtures are sufficient for a third party to validate an arbitrary runtime with no access to this
  repo's source — confirm by self-review that the docs reference only published artifacts.

## Risks / notes

- The schema must track the *real* wire format, including the M3c `control_ref{target,default}` and the
  declaration-order-preserving `output` object — the golden-vector validation test is what keeps it
  honest (and is gated).
- `jsonschema` is a new `dev` dependency; it is test-only (not a runtime dep of `refrain` or
  `refrain_core`).
- This is a documentation milestone: the implementation plan should be executable largely in parallel
  doc-writing tasks, with the schema + its test as the one code-bearing task.
