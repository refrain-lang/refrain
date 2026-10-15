# Typed Feedback Declaration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refrain protocols can declare the meaning and presentation requirements of existing output channels through a validated, versioned feedback contract.

**Architecture:** A `feedback { }` section describes existing `output` channels without reimplementing signal calculations. The resolver produces typed, immutable declarations; IR-JSON 0.5 carries them to hosts and Rust. The evaluator continues to emit the existing output values, so Python and Rust use the same channel names and timing. A subsequent, separate plan adds event rearm semantics and Recorder's generic renderer.

**Tech Stack:** Refrain grammar/Lark, Python dataclass IR and resolver, JSON Schema, Rust serde, pytest, cargo test.

**Spec:** `docs/superpowers/specs/2026-10-09-protocol-owned-feedback-and-review-design.md`

## Global Constraints

- The feedback declaration is optional; existing v0.1–v0.4 protocols and evaluator outputs retain their meaning.
- A feedback entry references exactly one already-declared `output` channel. It never names a local file or user URL.
- A host must reject a feedback contract version or role it cannot render before training begins; it must not silently drop a cue.
- Signal guards, gaps, and muted phases retain the evaluator's existing output reset behavior. Do not infer alpha/theta state in a host.
- Authored test streams prove software semantics, not client outcomes or clinical efficacy.

## Review Focus

1. A misspelled output reference fails resolution with the entry name and source location (Task 1 test).
2. Repeated roles or priorities fail resolution instead of allowing nondeterministic layering (Task 1 test).
3. An event output declared as an analog layer, or the reverse, fails resolution (Task 1 test).
4. A v0.5 document is rejected by an older Rust runtime and accepted by the updated one; a v0.4 document stays compatible (Task 2 test).
5. Composition via `extends`/`amend feedback` retains one effective declaration and serializes it deterministically (Task 2 test).

---

## File structure

- `src/refrain/grammar.lark`: allow the new top-level `feedback` section.
- `src/refrain/ir.py`: `IRFeedback` and `IRFeedbackEntry` types attached to `IRProtocol`.
- `src/refrain/resolver.py`: parse the section, resolve output references and types, and validate roles, priorities, durations, and asset IDs.
- `src/refrain/unparser.py`: round-trip the section using existing section rendering.
- `src/refrain/ir_json.py` and `src/refrain/schema/ir-json-v0.5.schema.json`: deterministic versioned wire representation.
- `refrain-core/src/ir.rs`: matching serde types and v0.5 version support.
- `tests/test_feedback_declaration.py`, `tests/test_ir_json.py`, and `refrain-core/tests/ir_deser.rs`: parser/resolver/wire compatibility checks.

### Task 1: Typed source declaration and resolver validation

**Files:** Modify `src/refrain/grammar.lark`, `src/refrain/ir.py`, `src/refrain/resolver.py`, `src/refrain/unparser.py`; create `tests/test_feedback_declaration.py`.

**Interfaces:**
- Source syntax: `feedback { version = 1; external_background = false; approach = layer { output = "feedback_approach"; role = "approach"; label = "Approach"; priority = 10; asset_role = "approach"; attack = 4 s; release = 8 s; preview = true; suppress_on_inhibit = true; suppress_on_mute = true; reset_on_gap = true } }`. `event { ... }` uses `rearm` and `min_interval` instead of attack/release.
- Produces: `IRFeedback(version: int, external_background: bool, entries: tuple[IRFeedbackEntry, ...])` and `IRFeedbackEntry(name, output, kind, role, label, priority, asset_role, preview, suppress_on_inhibit, suppress_on_mute, reset_on_gap, attack_ms, release_ms, rearm_ms, min_interval_ms)`. `IRProtocol.feedback: IRFeedback | None = None`.
- Allowed `kind`: `layer`, `event`. Role and asset-role are nonempty identifiers; unique role and integer priority per protocol. `layer` accepts dimensionless scalar or Boolean output; `event` accepts only event-stream output. Durations must be nonnegative and at most 60 seconds; `min_interval` is a desired host interval, not a hard safety limit.

- [ ] **Step 1: Write failing tests.** Parse/resolve one layer and one event and assert their typed fields and ordering. Assert missing output, duplicate role/priority, incompatible output type, bad version, negative/overlong duration, and a local-path asset role all fail with a useful `ResolveError`. Assert a protocol without `feedback` still resolves.
- [ ] **Step 2: Run red tests.** `pytest -q tests/test_feedback_declaration.py`; expect missing grammar/IR behavior.
- [ ] **Step 3: Implement the syntax and resolver.** Use the existing typed-block expression and duration helpers; do not create a second expression evaluator. Preserve source order in `entries`. Require `version = 1`; reject unknown entry fields and unknown feedback settings.
- [ ] **Step 4: Run green tests.** `pytest -q tests/test_feedback_declaration.py tests/test_parser_examples.py`; expect pass.
- [ ] **Step 5: Commit.** `git add src/refrain/grammar.lark src/refrain/ir.py src/refrain/resolver.py src/refrain/unparser.py tests/test_feedback_declaration.py && git commit -m "feat: validate protocol feedback declarations"`.

### Task 2: Versioned IR-JSON and Rust parity

**Files:** Modify `src/refrain/ir_json.py`, `refrain-core/src/ir.rs`, `tests/test_ir_json.py`, `refrain-core/tests/ir_deser.rs`; create `src/refrain/schema/ir-json-v0.5.schema.json`.

**Interfaces:**
- Consumes `IRProtocol.feedback` from Task 1.
- Produces IR-JSON `"feedback": {"version":1,"external_background":false,"entries":[...]}` only when the section exists. Any such protocol emits `refrain_ir_version: "0.5"`; otherwise retain the lowest existing version.
- Rust `Protocol.feedback: Option<FeedbackDecl>` with typed `FeedbackEntry`; accept IR versions through 0.5 while rejecting newer versions.

- [ ] **Step 1: Write failing wire tests.** Assert full feedback JSON, source-order preservation, schema validation, content-hash stability, Rust deserialization of v0.5, rejection of v0.6, and continued loading of an older v0.4 fixture. Add an `extends`/`amend feedback` round-trip test.
- [ ] **Step 2: Run red tests.** `pytest -q tests/test_ir_json.py` and `cargo test --manifest-path refrain-core/Cargo.toml --test ir_deser`; expect the new assertions to fail.
- [ ] **Step 3: Implement the wire contract.** Add the schema by evolving v0.4, requiring the exact typed fields for each entry. Include `feedback` in deterministic JSON and its content hash. Add matching Rust serde structs and extend `SUPPORTED_IR_VERSIONS` to 0.5.
- [ ] **Step 4: Run green tests and version checks.** `pytest -q tests/test_feedback_declaration.py tests/test_ir_json.py tests/test_version_lockstep.py` and `cargo test --manifest-path refrain-core/Cargo.toml --test ir_deser`; expect pass.
- [ ] **Step 5: Commit.** `git add src/refrain/ir_json.py src/refrain/schema/ir-json-v0.5.schema.json refrain-core/src/ir.rs tests/test_ir_json.py refrain-core/tests/ir_deser.rs && git commit -m "feat: emit versioned feedback declarations"`.

## Completion boundary

This plan ships a typed declaration and wire contract only. Do not claim that it changes audible feedback. Next plans cover the general once-per-hold/rearm event primitive, protocol-library declarations, Recorder's generic renderer/preview adapter, and release parity. The host must reject v0.5 until that adapter is ready; do not merge a v0.5-dependent protocol first.
