# M5 — Validation, Schema & Reproducibility Docs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make IR-JSON a versioned, independently-verifiable wire contract — a human-readable spec, a machine-readable JSON Schema gated against the committed golden vectors, a conformance-suite doc over the existing fixtures, and a CRED-nf reproducibility doc — with **no evaluator behavior changes**.

**Architecture:** Documentation + one JSON Schema artifact + one test + one drift-gate step. The schema is kept honest by validating every committed `refrain-core/tests/fixtures/*.ir.json` against it (test + gate). The conformance suite is the *existing* fixtures corpus, documented in place (no duplication). Design spec: `docs/superpowers/specs/2026-05-25-m5-validation-schema-reproducibility-design.md`.

**Tech Stack:** Python (`jsonschema` draft 2020-12, pytest), Markdown docs. Source of truth for the wire format: `src/refrain/ir_json.py` (emitter), `refrain-core/src/ir.rs` (consumer serde structs), the committed fixtures.

**Branch:** `m5-conformance-spec` (off current `main`, which has the complete core). The M5 design spec is already committed here (`770dde4`).

---

## File Structure

- Create `refrain-core/schema/ir-json-v0.1.schema.json` — the machine-readable wire schema.
- Create `tests/test_ir_json_schema.py` — validates every golden `.ir.json` against the schema; rejects a malformed sample.
- Create `docs/IR-JSON.md` — human-readable versioned wire contract.
- Create `docs/CONFORMANCE.md` — the conformance suite documented over the fixtures.
- Create `docs/REPRODUCIBILITY.md` — CRED-nf reproducibility-by-construction.
- Modify `pyproject.toml` — add `jsonschema` to the `dev` optional-dependencies.
- Modify `refrain-core/tools/check_equivalence.py` — add a schema-validation gate step.
- Modify `docs/DESIGN-NOTES.md` — behavior-neutral coefficient-baking note.
- Modify `CHANGELOG.md` — entry flagging the new spec/schema/conformance artifacts.

---

## Task 1: JSON Schema + golden-vector validation test

This is the verifiable backbone: the test is the executable acceptance criterion; the schema is authored until it passes.

**Files:**
- Modify: `pyproject.toml` (the `dev = [...]` optional-dependencies list)
- Create: `tests/test_ir_json_schema.py`
- Create: `refrain-core/schema/ir-json-v0.1.schema.json`

- [ ] **Step 1: Add `jsonschema` to dev deps and install**

In `pyproject.toml`, add `"jsonschema >= 4.0"` to the `dev = [ ... ]` list (alongside `pytest`, `ruff`, etc.). Then install:

Run: `VIRTUAL_ENV=.venv .venv/bin/pip install "jsonschema>=4.0"`
Expected: installs jsonschema (and deps).

- [ ] **Step 2: Write the failing validation test**

Create `tests/test_ir_json_schema.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The committed IR-JSON golden vectors must validate against the published
JSON Schema (refrain-core/schema/ir-json-v0.1.schema.json). This keeps the
schema honest: it cannot drift from the wire format the emitter actually
produces, because every fixture is checked against it (and this test is wired
into the check_equivalence drift gate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "refrain-core" / "schema" / "ir-json-v0.1.schema.json"
FIXTURES = REPO / "refrain-core" / "tests" / "fixtures"
IR_JSON_FILES = sorted(FIXTURES.glob("*.ir.json"))


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA_PATH.read_text())
    # Draft 2020-12; check the schema itself is a valid schema first.
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"missing schema: {SCHEMA_PATH}"


def test_corpus_is_nonempty():
    assert IR_JSON_FILES, "no *.ir.json fixtures found — corpus path wrong?"


@pytest.mark.parametrize("ir_path", IR_JSON_FILES, ids=lambda p: p.stem)
def test_golden_ir_json_validates(validator, ir_path):
    doc = json.loads(ir_path.read_text())
    errors = sorted(validator.iter_errors(doc), key=lambda e: e.path)
    assert not errors, "schema rejected golden vector:\n" + "\n".join(
        f"  {list(e.path)}: {e.message}" for e in errors
    )


def test_malformed_ir_json_is_rejected(validator):
    # A document missing required top-level fields and with a bad Expr node
    # must fail — proves the schema is not vacuously permissive.
    bad = {"refrain_ir_version": "0.1", "output": {"x": {"node": "not_a_real_node"}}}
    assert not validator.is_valid(bad)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_ir_json_schema.py -q`
Expected: FAIL — `test_schema_file_exists` fails / fixture-validation errors because `refrain-core/schema/ir-json-v0.1.schema.json` does not exist yet.

- [ ] **Step 4: Author the JSON Schema**

Create `refrain-core/schema/ir-json-v0.1.schema.json` (JSON Schema **draft 2020-12**). Derive it from three sources of truth and expand until Step 5 passes:
- the emitter `src/refrain/ir_json.py` (what is produced),
- the consumer structs in `refrain-core/src/ir.rs` (`Protocol`, `Coeffs`, `Arg`, `Input`, `Derive`, `Threshold`, `Inhibit`, `Reward`, `Session`, `Phase`, `ControlDecl`, and the `Expr` enum), and
- the committed `refrain-core/tests/fixtures/*.ir.json` (the ground truth).

Requirements the schema MUST encode:
- **Top-level object** with the emitted keys: `refrain_ir_version` (const `"0.1"`), `name`, `extends`, `sample_rate_hz` (number), `channels` (array of string), `requires`, `meta`, `inputs`, `derives`, `thresholds`, `inhibits`, `reward`, `output`, `controls`, `session`, `topological_order` (array of string). Mark as required the ones every fixture has — at minimum `refrain_ir_version`, `sample_rate_hz`, `channels`, `output`, `topological_order` (verify against the fixtures; do not over-require). Use `"additionalProperties": true` at the top level (the wire format carries fields the Rust core ignores).
- **`Expr`** as a discriminated union on the `"node"` tag (`oneOf` with `if`/`then` on `node`, or per-variant `properties.node.const`). Variants and their fields (from `ir.rs` `#[serde(tag = "node")] enum Expr`):
  - `number` → `{value: number}`
  - `string` → `{value: string}`
  - `bool` → `{value: boolean}`
  - `stream_ref` → `{target: string}`
  - `threshold_ref` → `{target: string}`
  - `control_ref` → `{target: string, default: <value>}` (the M3c `control_ref{target,default}` shape — confirm field names against a fixture that uses a control, e.g. `micro_07_ilf` / `realistic_smr`)
  - `reward_field` → `{field_path: string}`
  - `call` → `{callee: string, args: array of Arg, coeffs?: Coeffs}` (confirm arg/coeffs field names against fixtures)
  - `array` → `{elements: array of Expr}`
  - `tuple` → `{elements: array of Expr}`
  - `binop` → `{op: string, left: Expr, right: Expr}` (confirm field names)
  - `conditional` → `{cond: Expr, then: Expr, else: Expr}` (note the `else` rename)
  - `block` → `{kind?: string, fields: object<string, Expr>}`
- **`Coeffs`** sub-schema: optional `sos` (array of array of number), `fir_taps` (array of number), `group_delay`/`window_samples`/`dwell_samples`/`nperseg`/`noverlap` (integer), `alpha`/`dt` (number) — all optional (`ir.rs` has them `#[serde(default)]`).
- Use `$defs` for `Expr`, `Coeffs`, `Arg`, and the per-section node types; reference with `$ref`.
- Set `"$schema": "https://json-schema.org/draft/2020-12/schema"`, an `"$id"`, and a top-level `"title"`/`"description"` naming this as IR-JSON v0.1.

Iterate: run the test (Step 5) and read the error paths it prints; tighten/loosen the schema until all golden vectors pass and the malformed sample is rejected. Do NOT weaken the schema to `additionalProperties: true` everywhere just to pass — keep `Expr` variants and `Coeffs` honestly typed (that's what makes `test_malformed_ir_json_is_rejected` meaningful).

- [ ] **Step 5: Run the test to verify it passes**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_ir_json_schema.py -q`
Expected: PASS — every `*.ir.json` validates; `test_malformed_ir_json_is_rejected` passes (malformed rejected).

- [ ] **Step 6: Confirm the full suite is unaffected**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q`
Expected: PASS — prior count plus the new schema tests; no regressions.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml tests/test_ir_json_schema.py refrain-core/schema/ir-json-v0.1.schema.json
git commit -m "feat(m5): IR-JSON JSON Schema + golden-vector validation test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `docs/IR-JSON.md` — human-readable wire contract

**Files:**
- Create: `docs/IR-JSON.md`
- (Reference only, do not modify: `src/refrain/ir_json.py` already says "See `docs/IR-JSON.md` for the schema" — this task makes that pointer resolve.)

- [ ] **Step 1: Write the spec document**

Create `docs/IR-JSON.md` with these sections and concrete content (draw exact details from `src/refrain/ir_json.py`, `refrain-core/src/ir.rs`, and a real fixture; do not invent field names):

1. **Overview & version.** IR-JSON is the boundary between the Python front-end (parser→resolver→emitter) and the portable Rust core. Current `refrain_ir_version` is `"0.1"`. State the compatibility policy: additive/optional fields are minor-compatible; changing a `node` discriminator, removing/renaming a required field, or changing coefficient semantics is a breaking (major) change; consumers should ignore unknown top-level fields (the Rust core does).
2. **Two host rules** (formalize from `refrain-core/README.md`):
   - *Sample rate is baked.* Coefficients are designed for the runtime sample rate, which can differ from the resolver's `sample_rate_chosen_hz` (e.g. q21 chooses 2048 Hz; the bench runs 256 Hz). The protocol declares only a *minimum*; ship/emit the IR-JSON variant matching the amp's rate and pass that same rate to the runtime.
   - *`channels` is the physical acquisition layout.* The runtime `channels` (e.g. `["Cz","A1","A2"]`, including reference electrodes) is the host's acquisition layout, NOT the protocol's required channels (e.g. `["Cz"]`).
3. **Top-level object.** Table of the emitted keys (`refrain_ir_version`, `name`, `extends`, `sample_rate_hz`, `channels`, `requires`, `meta`, `inputs`, `derives`, `thresholds`, `inhibits`, `reward`, `output`, `controls`, `session`, `topological_order`) with type + one-line meaning. Note `output` preserves declaration order (significant — drives event-emission order) and `topological_order` drives derive evaluation order.
4. **The `Expr` tagged union.** Document the `"node"` discriminator and every variant with its fields: `number`, `string`, `bool`, `stream_ref`, `threshold_ref`, `control_ref` (`{target, default}`), `reward_field`, `call` (`{callee, args, coeffs}`), `array`, `tuple`, `binop`, `conditional` (note `else`), `block`.
5. **Baked-coefficient fields.** Document `Coeffs` (`sos` SOS biquad cascade, `fir_taps` Hilbert FIR + `group_delay`, plus `alpha`/`dt`/`window_samples`/`dwell_samples`/`nperseg`/`noverlap`). State the contract: Python owns SciPy filter *design*; the wire format carries the *designed* coefficients; the runtime only runs the deterministic recurrence/convolution — it never reimplements SciPy.
6. **Worked example.** Embed a trimmed, annotated excerpt of a committed fixture (e.g. `refrain-core/tests/fixtures/realistic_smr.ir.json`) showing a montage input → a `derive` with a `call` pipeline carrying `coeffs` → a percentile `threshold` → `reward` → an `output` expression.
7. **Validation pointers.** Link the JSON Schema (`refrain-core/schema/ir-json-v0.1.schema.json`) for structural validation and `docs/CONFORMANCE.md` for behavioral validation.

- [ ] **Step 2: Verify accuracy against the source**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -c "import json; d=json.load(open('refrain-core/tests/fixtures/realistic_smr.ir.json')); print(sorted(d.keys()))"`
Cross-check every key/field name you documented appears in the emitter (`src/refrain/ir_json.py`) and/or `ir.rs`. Fix any mismatch. (No automated test for prose — this manual cross-check is the gate.)

- [ ] **Step 3: Commit**

```bash
git add docs/IR-JSON.md
git commit -m "docs(m5): IR-JSON.md — versioned human-readable wire contract

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `docs/CONFORMANCE.md` — conformance suite over the fixtures

**Files:**
- Create: `docs/CONFORMANCE.md`

- [ ] **Step 1: Write the conformance document**

Create `docs/CONFORMANCE.md` declaring `refrain-core/tests/fixtures/` the canonical conformance corpus (no duplication). Sections:

1. **What it is.** The golden vectors any runtime implementing the IR-JSON evaluator must reproduce. Generated from the canonical Python reference evaluator by `refrain-core/tools/gen_fixtures.py` and gated by `refrain-core/tools/check_equivalence.py`.
2. **Bundle format** (per protocol `<stem>`): `<stem>.ir.json` (the IR-JSON wire input), `<stem>.io.json` (the seeded input signal + the reference output *streams*, plus `sample_rate_hz`/`channels`), `<stem>.events.json` (reference feedback `Event`s, where the protocol emits them), `<stem>.taps.json` (per-chunk `last_taps()` snapshots, for tap-bearing protocols), and the `realistic_smr_setcontrol.{events,taps,schedule}.json` live-retuning scenario. Give the JSON shape of each (inspect a real file to document exact keys).
3. **How a runtime self-validates.** Deserialize the `.ir.json`; construct the evaluator with the `sample_rate_hz` and `channels` from the `.io.json`; feed the seeded input in chunks; compare produced streams/events/taps to the references.
4. **Tolerance methodology.** Floating comparisons use `atol=1e-6, rtol=1e-4` (the bench-harness tolerance); observed worst case across the corpus is ~1e-13 (machine precision); boolean event/condition streams must match **exactly**.
5. **Coverage table.** One row per protocol → primitive(s)/feature exercised:
   - `micro_01_passthrough` — referential montage passthrough
   - `micro_02_bandpass` — bandpass (biquad SOS cascade)
   - `micro_03_envelope` — hilbert + magnitude + smooth (envelope)
   - `micro_04_threshold` — rolling percentile threshold
   - `micro_05_reward` — dwell + sigmoid + reward outputs (events)
   - `micro_06_coherence` — coherence (Welch MSC)
   - `micro_07_ilf` — bipolar montage + control-ref bandpass center (Othmer ILF)
   - `micro_08_bandpower` — bandpower
   - `micro_09_inhibit` — inhibit gate (mute/freeze) + output muting (events + taps)
   - `realistic_smr` — full clinical: 3 bands, percentile+absolute thresholds, dwell+all_of, sigmoid, conditional outputs, control-ref (events + taps)
   - `realistic_smr_setcontrol` — live `set_control` retuning scenario (events + taps + schedule)
6. **Structural validation.** Point to `refrain-core/schema/ir-json-v0.1.schema.json` + `docs/IR-JSON.md` for validating the *input* IR-JSON shape (complements the behavioral vectors).

- [ ] **Step 2: Verify the bundle-format claims against real files**

Run: `ls refrain-core/tests/fixtures/ | sort` and inspect one of each suffix (`.ir.json`, `.io.json`, `.events.json`, `.taps.json`, `_setcontrol.schedule.json`) to confirm the documented JSON shapes match. Fix any mismatch.

- [ ] **Step 3: Commit**

```bash
git add docs/CONFORMANCE.md
git commit -m "docs(m5): CONFORMANCE.md — golden-vector conformance suite

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `docs/REPRODUCIBILITY.md` — CRED-nf reproducibility-by-construction

**Files:**
- Create: `docs/REPRODUCIBILITY.md`

- [ ] **Step 1: Write the reproducibility document**

Create `docs/REPRODUCIBILITY.md` (standalone, citable). Content:

1. **Thesis.** There is exactly one canonical signal→feedback transformation, implemented once in the Rust core (`refrain-core`) and compiled everywhere: PyO3 (desktop/tooling), uniffi (Swift/Kotlin), staticlib (mobile). Reproducibility is *by construction*, not by convention.
2. **How it's enforced.** (a) The conformance suite (`docs/CONFORMANCE.md`) pins behavior to the Python reference at machine precision; (b) the dual-backend drift gate (`check_equivalence.py` / CI `rust-equivalence`) runs the behavioral evaluator suite through both `backend="python"` and `backend="rust"` and fails on any divergence; (c) the JSON Schema (`docs/IR-JSON.md`) pins the wire format. Together: same IR-JSON in, same feedback out, on every platform.
3. **CRED-nf framing.** Tie to reproducibility of neurofeedback computation: a protocol's IR-JSON + the conformance vectors are sufficient to verify that any deployment computes feedback identically — independent of language, OS, or device.
4. **Cross-links.** `docs/IR-JSON.md`, `docs/CONFORMANCE.md`, `docs/RUST-CORE-HOST-BRIEF.md`.

- [ ] **Step 2: Commit**

```bash
git add docs/REPRODUCIBILITY.md
git commit -m "docs(m5): REPRODUCIBILITY.md — one core, validated by conformance

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Gate the schema test + DESIGN-NOTES note + CHANGELOG

**Files:**
- Modify: `refrain-core/tools/check_equivalence.py`
- Modify: `docs/DESIGN-NOTES.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a schema-validation step to the drift gate**

In `refrain-core/tools/check_equivalence.py`, add a step (reuse the existing `_run` helper + `results` dict; do NOT duplicate logic) that runs the schema test, placed before or after the dual-backend step. Use:

```python
    # Step N — IR-JSON golden vectors validate against the published JSON Schema.
    results["schema_validation"] = _run(
        "IR-JSON golden vectors vs. JSON Schema (pytest)",
        [sys.executable, "-m", "pytest", str(WORKTREE / "tests" / "test_ir_json_schema.py"), "-q"],
        cwd=WORKTREE,
        extra_env={"PYTHONPATH": str(WORKTREE)},
    )
```

Update the module docstring's step list and the summary header to reflect the added step (it becomes a five/-step gate). Keep the all-steps-must-pass exit logic.

- [ ] **Step 2: Run the gate to verify it passes (now includes schema validation)**

Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py`
Expected: `RESULT: PASS` with the summary listing the new `schema_validation` step as PASS alongside the others.

- [ ] **Step 3: Add the coefficient-baking note to DESIGN-NOTES**

Append a short, behavior-neutral note to `docs/DESIGN-NOTES.md`: Python designs filters with SciPy and bakes the resulting coefficients (`sos`, `fir_taps`, group delay, etc.) into IR-JSON; the portable runtime only runs the deterministic recurrence/convolution and never links SciPy/BLAS — this is what makes the core portable (no eigendecomposition/filter-design dependencies). Cross-link `docs/IR-JSON.md`.

- [ ] **Step 4: Add a CHANGELOG entry**

Add an entry to `CHANGELOG.md` (match the file's existing style/heading convention) flagging: the IR-JSON wire spec (`docs/IR-JSON.md`) + JSON Schema (`refrain-core/schema/ir-json-v0.1.schema.json`), the published conformance suite (`docs/CONFORMANCE.md`), and the reproducibility doc (`docs/REPRODUCIBILITY.md`).

- [ ] **Step 5: Commit**

```bash
git add refrain-core/tools/check_equivalence.py docs/DESIGN-NOTES.md CHANGELOG.md
git commit -m "feat(m5): gate IR-JSON schema validation + DESIGN-NOTES/CHANGELOG

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Definition of done

- `tests/test_ir_json_schema.py` green: every committed `*.ir.json` validates; malformed rejected.
- `check_equivalence.py` passes with the new `schema_validation` step; CI `rust-equivalence` green.
- Default `pytest -q` green; `cargo test` green; `cargo build --all-targets` 0 warnings — no behavior change.
- `docs/IR-JSON.md`, `docs/CONFORMANCE.md`, `docs/REPRODUCIBILITY.md` exist, cross-link, and the `ir_json.py` pointer resolves.
- Independent-validation bar: the spec + schema + CONFORMANCE.md + fixtures reference only published artifacts (a third party needs no repo source to validate a runtime).
