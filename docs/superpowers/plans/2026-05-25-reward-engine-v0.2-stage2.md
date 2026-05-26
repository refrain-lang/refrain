# Reward Engine v0.2 — Stage 2 (Rust core consumes v0.2 + version-aware schema/gate) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the Rust core to deserialize and evaluate IR-JSON v0.2 weighted-composite rewards at machine-precision parity with the merged Python evaluator, publish the v0.2 JSON Schema, and extend the drift gate so a weighted protocol runs identically on `backend="rust"`.

**Architecture:** Extend the existing serde structs in `refrain-core/src/ir.rs` (`Reward` gains `#[serde(default)] combine` + `components`; a new `RewardComponent` struct) so the **same** `Protocol` deserializer accepts both v0.1 (no `combine`/`components`) and v0.2 (with them) — v0.1 fixtures deserialize byte-for-byte unchanged. In `refrain-core/src/eval.rs`, reuse the existing `build_node` / `CNode` / `Control` / env machinery: each component's `signal` compiles to a `CNode` and each `weight` resolves through the same control-ref path (`num_named_controllable`) so live `set_control` retunes it; `eval_chunk` computes the weighted composite **before** `reward.continuous`/`reward.event` (mirroring the Python ordering) and binds `reward.composite` + `reward.component.<name>` into the same `env` map the existing `CNode::Reward` lookup already reads, with the identical all-zero-weight guard. The schema becomes version-aware: a new `ir-json-v0.2.schema.json` (modeled on v0.1) is added, v0.1 is unchanged, and the schema test picks the schema by each golden doc's `refrain_ir_version`. A weighted bench protocol drives a new golden-vector fixture and a fixture-driven Rust parity test.

**Tech Stack:** Rust (serde, serde_json, the existing `refrain_core` crate), PyO3/maturin wheel, Python 3.14 + pytest + jsonschema, numpy. No grammar/resolver/IR-emitter changes (those shipped in Stage 1).

---

## Scope (Stage 2 only)

**IN:** `refrain-core/src/ir.rs` v0.2 deserialization (`RewardComponent`, `Reward.combine`/`.components`, accepting v0.1-or-v0.2 via `#[serde(default)]`); `refrain-core/src/eval.rs` weighted-composite computation + `reward.composite` / `reward.component.<name>` env binding + component-weight control wiring + live retune; a new fixture-driven Rust parity test (`refrain-core/tests/composite.rs`); a weighted bench protocol (`bench/protocols/composite_smr_theta.refrain`) + its `gen_fixtures.py` entry; `refrain-core/schema/ir-json-v0.2.schema.json`; version-aware schema selection in `tests/test_ir_json_schema.py`; extending `check_equivalence.py` so the v0.2 fixture is generated, the Rust parity test runs, and v0.2 goldens validate against the v0.2 schema; a dual-backend composite parity test so the gate's Step 4 exercises Rust.

**OUT (do NOT touch):** `combine = "independent"` and set-replication / fan-out per-site components (Stage 3); the package version bump to `0.6.0` and `IR_JSON_VERSION` (deferred to end of Stage 3 — leave `IR_JSON_VERSION = "0.1"`, the per-protocol selector already emits `"0.2"`); any change to `src/refrain/*.py` evaluator/resolver/emitter (Stage 1 already merged); the v0.1 schema file (`ir-json-v0.1.schema.json` stays byte-identical).

**DO NOT** add the `gate =` hard-gate sugar; Stage 1 kept the implemented hard-gate form (`metric`/`threshold`/`action`) and the suppress-band form (`signal`+`weight`). Stage 2 mirrors that exactly — hard gates remain `IRInhibit` → `Inhibit` in Rust (already handled); suppress bands are reward components with `role == "suppress"`.

---

## Ground-truth references (read before implementing)

These are the merged Stage 1 Python reference (the source of truth to MIRROR) and the Rust core to MODIFY.

**Python reference (mirror exactly — do not re-derive the math):**
- `src/refrain/ir.py:230-260` — `IRRewardComponent(name, canonical_name, role, signal, weight, loc)` (`role ∈ {"reward","suppress"}`, `weight: IRExpr | None`); `IRReward(continuous, event, combine="all", components=(), loc)`.
- `src/refrain/eval_.py:692-717` — the composite block in `_process_chunk`. The EXACT formula, computed BEFORE `reward.continuous` (line 719) and `reward.event` (line 727):
  ```python
  num = np.zeros(actual_chunk_size); weight_sum = np.zeros(actual_chunk_size)
  for comp in self.ir.reward.components:
      signal = np.clip(self._eval_expr(comp.signal, ...), 0.0, 1.0)
      reward_component_signals[comp.name] = signal
      w = self._component_weight_chunk(comp, control_chunks_cache, actual_chunk_size)
      success = signal if comp.role == "reward" else (1.0 - signal)
      num += w * success
      weight_sum += w
  reward_composite = np.where(weight_sum > 0.0, num / np.where(weight_sum > 0.0, weight_sum, 1.0), 0.0)
  ```
- `src/refrain/eval_.py:1225-1239` — `_component_weight_chunk`: `weight is None` → all-1.0; `IRControlRef` → reads `control_chunks[w.target]` (the LIVE control value, so `set_control` moves it); `IRNumberLit` → constant.
- `src/refrain/eval_.py:1064-1086` — `IRRewardField` eval: `field_path == "composite"` → `reward_composite` (or zeros if absent); `field_path.endswith(".signal")` → `reward_component_signals[name]` (or zeros). The composite/component signals thread through `_eval_reward_event` (`:1156-1164`) so a `dwell(condition: above(reward.composite, …))` event sees them.
- `src/refrain/eval_.py:787-790` — stream keys captured: `reward.composite` and `reward.component.<name>`.
- `src/refrain/eval_.py:895-898` — tap keys: `reward/composite` (last sample) and `reward/component[<name>]` (last sample).
- `src/refrain/ir_json.py:300-319` — `_emit_reward` v0.2 branch (the wire shape this plan deserializes): `reward.combine` (string) + `reward.components` = array of `{name, canonical_name, role, signal, weight}` where `signal`/`weight` are emitted `Expr` nodes (`signal` a `call` sigmoid node; `weight` a `control_ref` node or `null`). `reward.continuous`/`reward.event` reference the composite via a `reward_field` node with `field_path == "composite"` or `"<name>.signal"`.
- `src/refrain/ir_json.py:56-65` — `_protocol_ir_version`: v0.2 iff `components` non-empty OR `combine == "weighted"`.

**Rust core to MODIFY:**
- `refrain-core/src/ir.rs:89-95` — `struct Reward { continuous, event }` (extend with `combine` + `components`). `:142-199` — `enum Expr` (the `reward_field` variant `RewardField { field_path }` already exists; no Expr change needed — `composite`/`<name>.signal` are just new `field_path` values). `:155-166` — `ControlRef { target, default }`.
- `refrain-core/src/eval.rs:259-276` — `enum CNode` (incl. `CNode::Reward(String)` which does `env.get("reward.<field>")` at `:287-290`). `:543-547` — reward build in `Evaluator::new` (extend to compile components). `:567-591` — the `Evaluator { … }` struct-literal (add the compiled-components field; declare it on the struct at `:444-484`). `:714-747` — the reward block in `eval_chunk` (insert the composite computation BEFORE the `reward_continuous`/`reward_event` blocks). `:1167-1211` — `build_node` (`Expr::RewardField` → `CNode::Reward`, `Expr::ControlRef` → `CNode::Const(default)`). `:149-157` — `num_named_controllable` (reads a literal-or-control-ref numeric arg, returning `(value, Option<target>)`; reuse for weights). `:93-123` — `Control` enum + `apply`; `:140-142` — `BuildCtx::register`. `:810-838` — `coerce_streams` (env is copied verbatim into streams, so binding `reward.composite`/`reward.component.<name>` into `env` makes them appear in `last_streams` for free).
- `refrain-core/src/python.rs:52-59` — `RustEvaluator::new` deserializes the IR-JSON; no change needed (v0.2 deserializes through the same `serde_json::from_str` once `ir.rs` is extended).

**Gate / fixtures / schema:**
- `refrain-core/schema/ir-json-v0.1.schema.json` — the model for the new v0.2 schema (`Reward` def at `:154-171`; `Expr`/`ExprRewardField`/`ExprControlRef` at `:250-345`). `refrain_ir_version` is `{ "const": "0.1" }` at `:10-13`.
- `refrain-core/tools/gen_fixtures.py:162-238` — `generate(stem)` writes `<stem>.ir.json` + `<stem>.io.json`; the protocol stem list at `:226-238`; `EVENT_BEARING`/`TAP_BEARING` frozensets at `:154-159`; `_reference` at `:40-73`. Bench protocols live in `bench/protocols/<stem>.refrain` (`:163`).
- `refrain-core/tools/check_equivalence.py:54-105` — the 5 steps (gen_fixtures, cargo_test, build_wheel, dual_backend_pytest, schema_validation). Step 2 runs `cargo test` (the new `composite.rs` joins automatically). Step 4 globs `tests/test_eval_*.py` under `REFRAIN_EVAL_BACKEND=rust`.
- `tests/test_ir_json_schema.py:19-45` — single `SCHEMA_PATH` (v0.1) validates ALL `*.ir.json`. This must become version-aware (pick v0.1 or v0.2 schema by the doc's `refrain_ir_version`).
- `refrain-core/tests/equivalence.rs:12-71` — the fixture-driven parity harness (`Io` struct, `load_ir`/`load_io`, `check`, `run_protocol`) the new `composite.rs` is modeled on.
- `tests/test_eval_composite.py` — Stage 1's composite tests, ALL pinned `backend="python"`. Task 8 adds a dual-backend variant so the gate exercises Rust.

---

## Key design decisions (locked, with rationale)

1. **serde accepts v0.1 OR v0.2 in one struct.** Add `#[serde(default)] pub combine: String` (default `"all"`, via a `fn default_combine()` since `String::default()` is `""`) and `#[serde(default)] pub components: Vec<RewardComponent>` to `struct Reward`. A v0.1 doc has neither key → `combine = "all"`, `components = []` → identical runtime to today. A v0.2 doc populates both. No second deserializer, no version enum dispatch — serde's `default` is the entire back-compat mechanism. (`refrain_ir_version` itself stays unread by Rust, exactly as today; serde ignores it.)
2. **`RewardComponent` mirrors the Python IR.** `struct RewardComponent { name: String, role: String, signal: Expr, #[serde(default)] weight: Option<Expr> }`. `canonical_name` is in the wire JSON but the runtime does not need it (taps/streams key on the bare `name`, matching Python's `reward/component[<name>]` / `reward.component.<name>`), so it is left to serde's ignore-unknown — do NOT add a field the runtime never reads.
3. **Compile components with the EXISTING `build_node`.** Each component's `signal` compiles via `build_node(&c.signal, ctx)` → a `CNode` (a sigmoid/linear/pipeline). The weight resolves via the same `num_named_controllable`-style path used for sigmoid `midpoint`: a literal weight → constant; a `control_ref` weight → register a binding so `set_control` retunes it. Reuse, do not fork: a new `CNode` variant or a parallel evaluator is forbidden (owner rule).
4. **Weight as a live control — a dedicated `Control::Weight` cell.** A component's weight is a per-component `ControlCell` (an `Arc<Mutex<f64>>` via `control_cell(default)`). When the weight is a `control_ref`, register `Control::Weight { value: cell.clone() }` under its target so `set_control` writes the new value into the cell (mirroring Python reading `control_chunks[target]` fresh each chunk). At composite time, each chunk reads `*cell.lock().unwrap()` and broadcasts it across the chunk. This is the v0.2 analogue of `Control::Sigmoid { midpoint }` and uses the identical `BuildCtx::register` plumbing. (Python forwards weight changes via `control_chunks_cache`, not a DSP impl; the Rust cell is the equivalent shared mutable slot.)
5. **Composite computed BEFORE continuous/event, bound into `env`.** In `eval_chunk`, immediately after the inhibit/`muted` block and BEFORE `reward_continuous`/`reward_event`, compute `num`/`weight_sum`/`composite` exactly as Python, clip each component signal to `[0,1]`, apply the `success = signal | 1-signal` role rule, and the `where(weight_sum>0, num/weight_sum, 0)` guard. Insert `reward.composite` and each `reward.component.<name>` into `env` (as `Val::F`). The existing `CNode::Reward("composite")` / `CNode::Reward("<name>.signal")` lookups then resolve through the SAME `env.get("reward.<field>")` path already used for `reward.continuous`/`reward.event` — so `continuous = reward.composite`, `output = reward.composite`, and `dwell(condition: above(reward.composite, …))` all work without touching `CNode`.
6. **`field_path` for components is `<name>.signal`.** Python's `IRRewardField.field_path` for a component access is `"<name>.signal"` (e.g. `"smr.signal"`), emitted verbatim. The env key for a component is `reward.<name>.signal` (so `CNode::Reward("<name>.signal")` → `env.get("reward.<name>.signal")`). Bind under BOTH a stream-friendly key AND this lookup key: bind `reward.<name>.signal` for the `CNode::Reward` lookup, and ALSO bind the Python stream/tap names by emitting `reward.component.<name>` into streams and `reward/composite` + `reward/component[<name>]` into taps in `eval_chunk` (these are the keys the golden vectors compare).
7. **Streams come free from `coerce_streams`.** `coerce_streams` copies every `env` entry into the streams map verbatim. So `reward.composite` bound in `env` appears in `last_streams` as `reward.composite` — matching Python's stream key — automatically. The component stream key Python uses is `reward.component.<name>` (a different string from the `reward.<name>.signal` lookup key), so bind that SEPARATE key into `env` too (a value-only binding the CNode lookup never reads), so it round-trips into streams. Taps are added explicitly in `eval_chunk` (not auto-derived).
8. **Schema is version-aware by file.** `ir-json-v0.2.schema.json` = a copy of v0.1 with `refrain_ir_version` `const "0.2"` and the `Reward` def extended (`combine` enum, `components` array of a new `RewardComponent` def). v0.1 schema file is unchanged. The schema test resolves which schema a golden uses by reading the doc's `refrain_ir_version`.
9. **One weighted bench protocol → one golden fixture → one Rust parity test.** `composite_smr_theta.refrain`: a reward + a suppress band (distinct weights) + `combine="weighted"` + `output = reward.composite` and a gated event. `gen_fixtures.py` emits its `.ir.json` (v0.2) + `.io.json` (Python reference streams). `composite.rs` replays the seeded signal through the Rust `Evaluator` and asserts the `output/audio_gain` + `reward.composite` streams match within the same tol the equivalence harness uses.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `refrain-core/src/ir.rs` | Modify (`Reward`, add `RewardComponent`, `default_combine`) | v0.2 deserialization; accept v0.1-or-v0.2 via `#[serde(default)]`. |
| `refrain-core/src/eval.rs` | Modify (`Control`, `Evaluator` struct + `new`, `eval_chunk`, add `CompiledComponent` + `build_component`) | Weighted composite per chunk; `reward.composite`/`reward.component.<name>` env+stream+tap binding; weight-as-control live retune. |
| `refrain-core/tests/ir_deser.rs` | Add test | Pin that a v0.2 `.ir.json` deserializes (combine + components populated). |
| `refrain-core/tests/composite.rs` | Create | Fixture-driven Python↔Rust parity for the weighted protocol (mirrors `equivalence.rs`). |
| `bench/protocols/composite_smr_theta.refrain` | Create | A weighted-composite bench protocol (reward + suppress, distinct weights). |
| `refrain-core/tools/gen_fixtures.py` | Modify (stem list, EVENT_BEARING) | Generate the v0.2 golden `.ir.json` + `.io.json`. |
| `refrain-core/schema/ir-json-v0.2.schema.json` | Create | Published v0.2 schema (v0.1 + `combine`/`components`). |
| `tests/test_ir_json_schema.py` | Modify (version-aware schema selection) | v0.1 goldens → v0.1 schema; v0.2 goldens → v0.2 schema. |
| `refrain-core/tools/check_equivalence.py` | (No edit needed; verify) | cargo test (Step 2) picks up `composite.rs`; gen_fixtures (Step 1) emits the v0.2 fixture; schema_validation (Step 5) covers v0.2 via the version-aware test. |
| `tests/test_eval_composite.py` | Add dual-backend test | A `backend`-parametrized composite parity test so gate Step 4 exercises Rust. |

---

### Task 1: Rust IR — `RewardComponent` + `Reward.combine`/`.components` (v0.1-or-v0.2 deser)

**Files:**
- Modify: `refrain-core/src/ir.rs:89-95` (`struct Reward`), insert `RewardComponent` + `default_combine` near it
- Test: `refrain-core/tests/ir_deser.rs`

- [ ] **Step 1: Write the failing test**

Append to `refrain-core/tests/ir_deser.rs`:

```rust
#[test]
fn deserializes_v02_reward_with_components() {
    // Minimal v0.2 reward block (the shape `_emit_reward` produces): a
    // weighted combine + a reward component + a suppress component with a
    // control-ref weight. v0.1 docs (no combine/components) still deserialize
    // because both fields are `#[serde(default)]`.
    let json = r#"{
        "refrain_ir_version": "0.2",
        "sample_rate_hz": 256.0,
        "channels": ["Cz"],
        "inputs": {},
        "derives": {},
        "output": {},
        "topological_order": [],
        "reward": {
            "continuous": {"node": "reward_field", "field_path": "composite", "stream_type": {}},
            "event": null,
            "combine": "weighted",
            "components": [
                {"name": "smr", "canonical_name": "reward/smr", "role": "reward",
                 "signal": {"node": "call", "callee": "sigmoid", "args": []},
                 "weight": {"node": "control_ref", "target": "control/w_smr", "default": 1.0}},
                {"name": "theta", "canonical_name": "reward/theta", "role": "suppress",
                 "signal": {"node": "call", "callee": "sigmoid", "args": []},
                 "weight": null}
            ]
        }
    }"#;
    let p: Protocol = serde_json::from_str(json).unwrap();
    let r = p.reward.expect("reward present");
    assert_eq!(r.combine, "weighted");
    assert_eq!(r.components.len(), 2);
    assert_eq!(r.components[0].name, "smr");
    assert_eq!(r.components[0].role, "reward");
    assert!(r.components[0].weight.is_some());
    assert_eq!(r.components[1].role, "suppress");
    assert!(r.components[1].weight.is_none());
}

#[test]
fn v01_reward_defaults_combine_all_no_components() {
    // A v0.1 reward (continuous/event only) must still deserialize: combine
    // defaults to "all", components to empty.
    let json = r#"{
        "sample_rate_hz": 256.0, "channels": ["Cz"], "inputs": {}, "derives": {},
        "output": {}, "topological_order": [],
        "reward": {"continuous": null, "event": null}
    }"#;
    let p: Protocol = serde_json::from_str(json).unwrap();
    let r = p.reward.unwrap();
    assert_eq!(r.combine, "all");
    assert!(r.components.is_empty());
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path refrain-core/Cargo.toml --test ir_deser`
Expected: FAIL — `error[E0609]: no field 'combine' on type 'Reward'` (the struct has only `continuous`/`event`).

- [ ] **Step 3: Write minimal implementation**

In `refrain-core/src/ir.rs`, replace `struct Reward` (lines 89-95) with:

```rust
/// `reward { continuous?, event?, combine?, components? }`. For a v0.1
/// single-reward protocol `combine` is absent (defaults to "all") and
/// `components` is empty — byte-identical runtime to before. A v0.2
/// weighted composite carries one `RewardComponent` per named reward/suppress
/// block and `combine == "weighted"`.
#[derive(Debug, Deserialize)]
pub struct Reward {
    #[serde(default)]
    pub continuous: Option<Expr>,
    #[serde(default)]
    pub event: Option<Expr>,
    #[serde(default = "default_combine")]
    pub combine: String,
    #[serde(default)]
    pub components: Vec<RewardComponent>,
}

fn default_combine() -> String {
    "all".to_string()
}

/// A named reward/suppress component of a v0.2 weighted composite
/// (`_emit_reward`'s `components[]` entries). `role` is "reward" (contributes
/// `signal`) or "suppress" (contributes `1 - signal`). `weight` is a
/// `control_ref` or `number` Expr; `None` ⇒ implicit weight 1.0. `canonical_name`
/// is in the wire JSON but the runtime keys taps/streams on `name`, so serde
/// ignores it (additionalProperties).
#[derive(Debug, Deserialize)]
pub struct RewardComponent {
    pub name: String,
    pub role: String,
    pub signal: Expr,
    #[serde(default)]
    pub weight: Option<Expr>,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --manifest-path refrain-core/Cargo.toml --test ir_deser`
Expected: PASS (both new tests + the existing `deserializes_micro_03_ir_json`).

- [ ] **Step 5: Confirm v0.1 fixtures still deserialize (no regression)**

Run: `cargo test --manifest-path refrain-core/Cargo.toml`
Expected: PASS — every existing golden-vector test (`equivalence`, `events`, `taps`, `set_control`, `ir_deser`) is green; the `#[serde(default)]` additions don't perturb v0.1 deserialization.

- [ ] **Step 6: Commit**

```bash
git add refrain-core/src/ir.rs refrain-core/tests/ir_deser.rs
git commit -m "feat(rust-core/ir): deserialize v0.2 reward components + combine (v0.1-compatible)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Rust eval — compile components (`CompiledComponent` + weight-as-control)

**Files:**
- Modify: `refrain-core/src/eval.rs:93-123` (`Control` enum + `apply`), add `CompiledComponent` struct + `build_component` fn, extend `Evaluator` struct (`:444-484`) + `Evaluator::new` (`:543-591`)
- Test: deferred to Task 3 (this task only compiles; Task 3 evaluates). Confirm via `cargo test` green.

- [ ] **Step 1: Add the `Control::Weight` variant**

In `refrain-core/src/eval.rs`, add a variant to `enum Control` (after `Sigmoid { midpoint: ControlCell }`, line 101):

```rust
    /// A reward component's weight (v0.2 composite). `set_control` writes the
    /// new weight into the shared cell; `eval_chunk` reads it fresh each chunk,
    /// mirroring the Python evaluator reading `control_chunks[target]`.
    Weight { value: ControlCell },
```

And a match arm in `Control::apply` (after the `Sigmoid` arm, line 120):

```rust
            Control::Weight { value } => {
                *value.lock().unwrap() = value_arg;
            }
```

where `value_arg` is the existing `value: f64` parameter — use the parameter name already in scope (`value`). Concretely the arm is:

```rust
            Control::Weight { value: cell } => {
                *cell.lock().unwrap() = value;
            }
```

- [ ] **Step 2: Add `CompiledComponent` + `build_component`**

Add this struct after the `RewardEvent` struct (after line 504), and the builder after `build_reward_event` (after line 1448):

```rust
/// One compiled v0.2 reward component: the bare `name` (tap/stream key), its
/// `signal` node (a sigmoid/linear/pipeline producing a [0,1] success metric),
/// the `role` (reward vs suppress), and the weight cell read each chunk. A
/// `control_ref` weight registers a `Control::Weight` binding so `set_control`
/// retunes it; a literal weight is a fixed cell; an absent weight is 1.0.
struct CompiledComponent {
    name: String,
    role: ComponentRole,
    signal: CNode,
    weight: ControlCell,
}

#[derive(Clone, Copy)]
enum ComponentRole {
    Reward,    // contributes `signal`
    Suppress,  // contributes `1 - signal`
}
```

```rust
/// Compile a v0.2 reward component (mirrors `_resolve_reward_component` +
/// `_component_weight_chunk`). The `signal` reuses `build_node`; the `weight`
/// reuses the literal-or-control-ref read used for sigmoid `midpoint`. An
/// absent weight is the implicit 1.0.
fn build_component(c: &crate::ir::RewardComponent, ctx: &mut BuildCtx) -> CompiledComponent {
    let role = match c.role.as_str() {
        "reward" => ComponentRole::Reward,
        "suppress" => ComponentRole::Suppress,
        other => panic!("unknown reward component role {other:?}"),
    };
    let signal = build_node(&c.signal, ctx);
    // Weight: a `control_ref` registers a live binding; a literal `number` is a
    // fixed cell; absent ⇒ 1.0.
    let weight = match &c.weight {
        Some(Expr::ControlRef { target, default }) => {
            let cell = control_cell(*default);
            ctx.register(target, Control::Weight { value: cell.clone() });
            cell
        }
        Some(Expr::Number { value }) => control_cell(*value),
        Some(other) => panic!("reward component weight must be a control_ref or number, got {other:?}"),
        None => control_cell(1.0),
    };
    CompiledComponent { name: c.name.clone(), role, signal, weight }
}
```

- [ ] **Step 3: Add the field to the `Evaluator` struct and populate it in `new`**

In `struct Evaluator` (after `reward_event: Option<RewardEvent>,`, line 450) add:

```rust
    /// Compiled v0.2 reward components (empty for v0.1 single-reward protocols).
    /// When non-empty, `eval_chunk` computes the weighted composite and binds
    /// `reward.composite` / `reward.component.<name>` into the env.
    reward_components: Vec<CompiledComponent>,
```

In `Evaluator::new`, extend the reward build block (lines 543-547) to also compile components:

```rust
        let (mut reward_continuous, mut reward_event) = (None, None);
        let mut reward_components: Vec<CompiledComponent> = Vec::new();
        if let Some(r) = &p.reward {
            // Components first so any control bindings register before the
            // continuous/event nodes (order is immaterial for correctness; this
            // mirrors the Python `_build_pipeline` instantiating component
            // signals alongside the reward expressions).
            reward_components = r.components.iter().map(|c| build_component(c, &mut ctx)).collect();
            reward_continuous = r.continuous.as_ref().map(|e| build_node(e, &mut ctx));
            reward_event = r.event.as_ref().map(|e| build_reward_event(e, &mut ctx));
        }
```

And add `reward_components,` to the `Evaluator { … }` struct literal (after `reward_event,`, line 573):

```rust
            reward_components,
```

- [ ] **Step 4: Build to verify it compiles, run the suite (no regression)**

Run: `cargo test --manifest-path refrain-core/Cargo.toml`
Expected: PASS — the new field/struct/builder compile; `reward_components` is empty for every existing v0.1 fixture so all golden-vector tests stay green. (Rust may warn `reward_components` is never read — that is expected; Task 3 consumes it. If `-D warnings` is set in CI, add `#[allow(dead_code)]` to the field temporarily and remove it in Task 3.)

- [ ] **Step 5: Commit**

```bash
git add refrain-core/src/eval.rs
git commit -m "feat(rust-core/eval): compile v0.2 reward components with weight-as-control

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Rust eval — compute the weighted composite per chunk + bind it

**Files:**
- Modify: `refrain-core/src/eval.rs:714-747` (the reward block in `eval_chunk`)
- Test: deferred to Task 4 (fixture-driven parity). This task wires the math; Task 4 pins it against Python.

- [ ] **Step 1: Insert the composite computation BEFORE continuous/event**

In `eval_chunk`, immediately BEFORE the `if let Some(node) = self.reward_continuous.as_mut()` block (line 714) and AFTER the `muted` tap insertion (line 712), insert:

```rust
        // Weighted composite (v0.2). Computed BEFORE reward.continuous /
        // reward.event so that `continuous = reward.composite` and a
        // `dwell(condition: above(reward.composite, …))` can reference it.
        // Mirrors `_process_chunk`'s composite block EXACTLY: per-component
        // [0,1]-clipped signal, role rule (reward → signal, suppress → 1-signal),
        // weighted average with the all-zero-weight guard → 0.0.
        if !self.reward_components.is_empty() {
            let mut num = vec![0.0_f64; n];
            let mut weight_sum = vec![0.0_f64; n];
            // Collect (name, signal) so we can bind component streams after the
            // borrow of `self.reward_components` ends.
            let mut component_signals: Vec<(String, Vec<f64>)> =
                Vec::with_capacity(self.reward_components.len());
            for comp in self.reward_components.iter_mut() {
                let signal: Vec<f64> = comp
                    .signal
                    .eval(&env, n)
                    .into_f()
                    .iter()
                    .map(|v| v.clamp(0.0, 1.0))
                    .collect();
                let w = *comp.weight.lock().unwrap();
                for i in 0..n {
                    let success = match comp.role {
                        ComponentRole::Reward => signal[i],
                        ComponentRole::Suppress => 1.0 - signal[i],
                    };
                    num[i] += w * success;
                    weight_sum[i] += w;
                }
                component_signals.push((comp.name.clone(), signal));
            }
            let composite: Vec<f64> = (0..n)
                .map(|i| if weight_sum[i] > 0.0 { num[i] / weight_sum[i] } else { 0.0 })
                .collect();

            // Taps: `reward/composite` + `reward/component[<name>]` (last sample),
            // matching `_capture_taps`.
            if let Some(&last) = composite.last() {
                taps.insert("reward/composite".to_string(), last);
            }
            for (name, sig) in component_signals.iter() {
                if let Some(&last) = sig.last() {
                    taps.insert(format!("reward/component[{name}]"), last);
                }
            }

            // env bindings: `reward.composite` (the CNode::Reward("composite")
            // lookup key AND the Python stream key), `reward.<name>.signal`
            // (the CNode::Reward("<name>.signal") lookup key), and
            // `reward.component.<name>` (the Python *stream* key — a value-only
            // binding for `coerce_streams`, never read by a CNode).
            env.insert("reward.composite".to_string(), Val::F(composite));
            for (name, sig) in component_signals {
                env.insert(format!("reward.{name}.signal"), Val::F(sig.clone()));
                env.insert(format!("reward.component.{name}"), Val::F(sig));
            }
        }
```

(`ComponentRole` is the enum from Task 2; `Val::F`, `taps`, `env`, `n` are all already in scope in `eval_chunk`.)

- [ ] **Step 2: Build and run the existing suite (no regression)**

Run: `cargo test --manifest-path refrain-core/Cargo.toml`
Expected: PASS — `reward_components` is empty for every existing fixture, so the new block is skipped and all golden vectors stay green. The `never read` warning from Task 2 is now resolved (remove any temporary `#[allow(dead_code)]`).

- [ ] **Step 3: Commit**

```bash
git add refrain-core/src/eval.rs
git commit -m "feat(rust-core/eval): compute weighted reward.composite per chunk, bind to env/taps/streams

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Weighted bench protocol + golden fixture + Rust parity test

**Files:**
- Create: `bench/protocols/composite_smr_theta.refrain`
- Modify: `refrain-core/tools/gen_fixtures.py:154` (`EVENT_BEARING`), `:226-238` (stem list)
- Create: `refrain-core/tests/composite.rs`

- [ ] **Step 1: Create the weighted bench protocol**

Create `bench/protocols/composite_smr_theta.refrain`:

```refrain
// composite_smr_theta.refrain
// v0.2 weighted-composite bench protocol (reward engine v0.2, Stage 2 golden
// vector). One reward component (SMR up) + one suppress band (theta down) with
// DISTINCT control weights; combine = "weighted". Drives the Python↔Rust
// composite parity test (refrain-core/tests/composite.rs).

protocol "composite_smr_theta" {

  meta {
    version     = "1.0.0"
    evidence    = "clinical"
    description = "SMR-up / theta-down weighted composite at Cz, linked-ears reference"
  }

  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }

  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }

  derive "smr_envelope" {
    from = "raw"
    pipeline = [
      bandpass(band: (12 Hz, 15 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 250 ms),
    ]
  }

  derive "theta_envelope" {
    from = "raw"
    pipeline = [
      bandpass(band: (4 Hz, 8 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 250 ms),
    ]
  }

  reward  "smr"   { signal = sigmoid("smr_envelope",   midpoint: 6 uV, steepness: 1); weight = w_smr }
  inhibit "theta" { signal = sigmoid("theta_envelope", midpoint: 8 uV, steepness: 1); weight = w_theta }

  reward {
    combine    = "weighted"
    continuous = reward.composite
    event      = dwell(condition: above(reward.composite, 0.5), duration: 250 ms)
  }

  output {
    audio_gain  = reward.composite
    audio_chime = reward.event
  }

  controls {
    w_smr = percent {
      default      = 1.0
      range        = (0, 4)
      label        = "SMR reward weight"
      live_tunable = true
    }
    w_theta = percent {
      default      = 0.6
      range        = (0, 4)
      label        = "Theta suppress weight"
      live_tunable = true
    }
  }
}
```

- [ ] **Step 2: Verify the protocol resolves and emits v0.2 (sanity, no fixture yet)**

Run:
```bash
PYTHONPATH="$PWD" ./.venv/bin/python -c "
from pathlib import Path
from refrain.amp_profile import load_amp_profile
from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.ir_json import ir_to_json_obj
amp = load_amp_profile(Path('src/refrain/amp_profiles/q21.json'))
ir = resolve(parse_file(Path('bench/protocols/composite_smr_theta.refrain')), amp)
obj = ir_to_json_obj(ir, sample_rate_hz=256.0)
print('version', obj['refrain_ir_version'])
print('combine', obj['reward']['combine'])
print('components', [c['name'] + ':' + c['role'] for c in obj['reward']['components']])
print('continuous field_path', obj['reward']['continuous']['field_path'])
"
```
Expected: `version 0.2`, `combine weighted`, `components ['smr:reward', 'theta:suppress']`, `continuous field_path composite`. (If resolve errors, the protocol syntax is wrong — fix before proceeding; do NOT weaken the protocol.)

- [ ] **Step 3: Wire the protocol into gen_fixtures.py**

In `refrain-core/tools/gen_fixtures.py`, add `"composite_smr_theta"` to `EVENT_BEARING` (line 154) so its event list is captured:

```python
EVENT_BEARING = frozenset({"micro_05_reward", "realistic_smr", "micro_09_inhibit", "composite_smr_theta"})
```

And add `"composite_smr_theta",` to the stem tuple in `__main__` (after `"realistic_smr",`, line 237):

```python
        "realistic_smr",
        "composite_smr_theta",
```

- [ ] **Step 4: Generate the fixtures**

Run: `PYTHONPATH="$PWD" ./.venv/bin/python refrain-core/tools/gen_fixtures.py`
Expected: prints `composite_smr_theta: ir+io+events written; …`, and creates `refrain-core/tests/fixtures/composite_smr_theta.ir.json`, `.io.json`, `.events.json`. Confirm the IR-JSON is v0.2:

```bash
PYTHONPATH="$PWD" ./.venv/bin/python -c "import json; d=json.load(open('refrain-core/tests/fixtures/composite_smr_theta.ir.json')); print(d['refrain_ir_version'], d['reward']['combine'], len(d['reward']['components']))"
```
Expected: `0.2 weighted 2`.

- [ ] **Step 5: Write the failing Rust parity test**

Create `refrain-core/tests/composite.rs`:

```rust
//! v0.2 weighted-composite golden-vector parity: the Rust core, fed the v0.2
//! IR-JSON the Python emitter produces, must reproduce the Python evaluator's
//! `reward.composite` and gated output streams within the same tolerance the
//! equivalence harness uses (atol=1e-6, rtol=1e-4, after warmup). This is the
//! Stage-2 exit criterion: a weighted protocol runs identically on backend=rust.

use std::collections::BTreeMap;

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;
use serde::Deserialize;

#[derive(Deserialize)]
struct Io {
    sample_rate_hz: f64,
    channels: Vec<String>,
    chunk_size: usize,
    warmup_samples: usize,
    input: Vec<Vec<f64>>,
    streams: BTreeMap<String, Vec<f64>>,
}

fn load_ir(stem: &str) -> Protocol {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.ir.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn load_io(stem: &str) -> Io {
    let s = std::fs::read_to_string(format!("tests/fixtures/{stem}.io.json")).unwrap();
    serde_json::from_str(&s).unwrap()
}

fn check(name: &str, got: &[f64], want: &[f64], warmup: usize, atol: f64, rtol: f64) -> f64 {
    assert_eq!(got.len(), want.len(), "stream {name}: length mismatch");
    let mut max_abs = 0.0_f64;
    for i in warmup..got.len() {
        let d = (got[i] - want[i]).abs();
        assert!(
            d <= atol + rtol * want[i].abs(),
            "stream {name}: divergence at sample {i} (got {}, want {}); |diff|={d:e}",
            got[i], want[i]
        );
        max_abs = max_abs.max(d);
    }
    max_abs
}

#[test]
fn composite_smr_theta_equivalent() {
    let p = load_ir("composite_smr_theta");
    let io = load_io("composite_smr_theta");

    // The reward block must have deserialized as v0.2.
    {
        let r = p.reward.as_ref().expect("reward present");
        assert_eq!(r.combine, "weighted");
        assert_eq!(r.components.len(), 2);
    }

    let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
    let mut out: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    for chunk in io.input.chunks(io.chunk_size) {
        for (k, v) in ev.step_chunk(chunk) {
            out.entry(k).or_default().extend(v);
        }
    }

    // Must include the composite stream and the gated output channel.
    let mut checked = 0;
    let mut saw_composite = false;
    for (name, want) in &io.streams {
        if let Some(got) = out.get(name) {
            let max_abs = check(name, got, want, io.warmup_samples, 1e-6, 1e-4);
            eprintln!("  composite_smr_theta :: {name:<24} max|diff| = {max_abs:e}");
            if name == "reward.composite" {
                saw_composite = true;
            }
            checked += 1;
        }
    }
    assert!(saw_composite, "reward.composite stream not produced by the Rust core");
    assert!(checked >= 2, "expected to check multiple streams, got {checked}");
}
```

- [ ] **Step 6: Run the parity test**

Run: `cargo test --manifest-path refrain-core/Cargo.toml --test composite -- --nocapture`
Expected: PASS — the printed `max|diff|` for `reward.composite` and `output/audio_gain` are all `<= 1e-6 + 1e-4*|want|` (target ~1e-13 for the composite arithmetic itself; the envelope DSP carries the equivalence harness's existing tolerance). If `reward.composite` is missing from `io.streams`, the Python reference run didn't record it — confirm the protocol has `output = reward.composite` (it does) and `record_streams=True` (gen_fixtures sets it).

- [ ] **Step 7: Run the full Rust suite (no regression)**

Run: `cargo test --manifest-path refrain-core/Cargo.toml`
Expected: PASS — all of `equivalence`, `events`, `taps`, `set_control`, `ir_deser`, `composite`.

- [ ] **Step 8: Commit**

```bash
git add bench/protocols/composite_smr_theta.refrain refrain-core/tools/gen_fixtures.py refrain-core/tests/composite.rs refrain-core/tests/fixtures/composite_smr_theta.ir.json refrain-core/tests/fixtures/composite_smr_theta.io.json refrain-core/tests/fixtures/composite_smr_theta.events.json
git commit -m "feat(rust-core): v0.2 weighted-composite golden vector + Rust parity test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Publish the v0.2 JSON Schema

**Files:**
- Create: `refrain-core/schema/ir-json-v0.2.schema.json`

- [ ] **Step 1: Create the v0.2 schema (v0.1 + combine/components)**

Create `refrain-core/schema/ir-json-v0.2.schema.json` — identical to v0.1 except (a) `$id`/`title`/`description` say v0.2, (b) `refrain_ir_version` const is `"0.2"`, and (c) the `Reward` def gains `combine` + `components` and a new `RewardComponent` def. Write the full file (do not abbreviate):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://refrainlang.org/schema/ir-json-v0.2.schema.json",
  "title": "Refrain IR-JSON v0.2",
  "description": "Machine-readable schema for the Refrain IR-JSON wire format (v0.2: weighted-composite rewards). Produced by refrain.ir_json; consumed by refrain-core. Draft 2020-12.",
  "type": "object",
  "required": ["refrain_ir_version", "sample_rate_hz", "channels", "inputs", "derives", "output", "topological_order"],
  "additionalProperties": true,
  "properties": {
    "refrain_ir_version": {
      "const": "0.2",
      "description": "Wire-format version tag. Must be the string '0.2'."
    },
    "name": { "type": ["string", "null"] },
    "extends": { "type": ["string", "null"] },
    "sample_rate_hz": { "type": "number", "exclusiveMinimum": 0 },
    "channels": { "type": "array", "items": { "type": "string" }, "minItems": 1 },
    "requires": { "$ref": "#/$defs/Requires" },
    "meta": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Expr" } },
    "inputs": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Input" } },
    "derives": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Derive" } },
    "thresholds": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Threshold" } },
    "inhibits": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Inhibit" } },
    "reward": {
      "oneOf": [
        { "$ref": "#/$defs/Reward" },
        { "type": "null" }
      ]
    },
    "output": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Expr" } },
    "controls": { "type": "object", "additionalProperties": { "$ref": "#/$defs/ControlDecl" } },
    "session": {
      "oneOf": [
        { "$ref": "#/$defs/Session" },
        { "type": "null" }
      ]
    },
    "topological_order": { "type": "array", "items": { "type": "string" } }
  },

  "$defs": {
    "Requires": {
      "type": "object",
      "additionalProperties": true,
      "properties": {
        "coupling": { "type": ["string", "null"] },
        "sample_rate_min_hz": { "type": "number" },
        "sample_rate_chosen_hz": { "type": "number" },
        "channels": { "type": "array", "items": { "type": "string" } },
        "impedance": { "type": "string" },
        "markers": { "type": "string" }
      }
    },

    "StreamType": {
      "type": "object",
      "additionalProperties": true,
      "properties": {
        "value_kind": { "type": "string" },
        "dimensions": { "type": "object" },
        "vector_size": { "type": ["integer", "null"] }
      }
    },

    "Input": {
      "type": "object",
      "required": ["canonical_name", "montage"],
      "additionalProperties": true,
      "properties": {
        "canonical_name": { "type": "string" },
        "stream_type": { "$ref": "#/$defs/StreamType" },
        "montage": { "$ref": "#/$defs/Expr" }
      }
    },

    "Derive": {
      "type": "object",
      "required": ["canonical_name", "expression"],
      "additionalProperties": true,
      "properties": {
        "canonical_name": { "type": "string" },
        "stream_type": { "$ref": "#/$defs/StreamType" },
        "expression": { "$ref": "#/$defs/Expr" },
        "upstream": { "type": "array", "items": { "type": "string" } }
      }
    },

    "Threshold": {
      "type": "object",
      "required": ["canonical_name", "signal", "threshold_call"],
      "additionalProperties": true,
      "properties": {
        "canonical_name": { "type": "string" },
        "signal": { "type": "string" },
        "threshold_call": { "$ref": "#/$defs/Expr" },
        "stream_type": { "$ref": "#/$defs/StreamType" },
        "live_tunable": { "type": "boolean" }
      }
    },

    "Inhibit": {
      "type": "object",
      "required": ["canonical_name", "metric", "threshold", "action_kind"],
      "additionalProperties": true,
      "properties": {
        "canonical_name": { "type": "string" },
        "metric": { "$ref": "#/$defs/Expr" },
        "threshold": { "$ref": "#/$defs/Expr" },
        "action_kind": { "type": "string" },
        "action_release_ms": { "type": ["number", "null"] }
      }
    },

    "Reward": {
      "type": "object",
      "additionalProperties": true,
      "properties": {
        "continuous": {
          "oneOf": [
            { "$ref": "#/$defs/Expr" },
            { "type": "null" }
          ]
        },
        "event": {
          "oneOf": [
            { "$ref": "#/$defs/Expr" },
            { "type": "null" }
          ]
        },
        "combine": {
          "type": "string",
          "enum": ["all", "any", "weighted"]
        },
        "components": {
          "type": "array",
          "items": { "$ref": "#/$defs/RewardComponent" }
        }
      }
    },

    "RewardComponent": {
      "type": "object",
      "required": ["name", "role", "signal"],
      "additionalProperties": true,
      "properties": {
        "name": { "type": "string" },
        "canonical_name": { "type": "string" },
        "role": { "type": "string", "enum": ["reward", "suppress"] },
        "signal": { "$ref": "#/$defs/Expr" },
        "weight": {
          "oneOf": [
            { "$ref": "#/$defs/Expr" },
            { "type": "null" }
          ]
        }
      }
    },

    "ControlDecl": {
      "type": "object",
      "required": ["canonical_name"],
      "additionalProperties": true,
      "properties": {
        "canonical_name": { "type": "string" },
        "type_kind": { "type": "string" },
        "dims": { "type": "object" },
        "default": { "$ref": "#/$defs/Expr" },
        "range_low": { "$ref": "#/$defs/Expr" },
        "range_high": { "$ref": "#/$defs/Expr" },
        "log_scale": { "type": "boolean" },
        "label": { "type": ["string", "null"] },
        "live_tunable": { "type": "boolean" },
        "tune_strategy": { "type": ["string", "null"] }
      }
    },

    "Session": {
      "type": "object",
      "additionalProperties": true,
      "properties": {
        "phases": { "type": "array", "items": { "$ref": "#/$defs/Phase" } }
      }
    },

    "Phase": {
      "type": "object",
      "required": ["name", "duration_ms"],
      "additionalProperties": true,
      "properties": {
        "name": { "type": "string" },
        "duration_ms": { "type": "number" },
        "output_muted": { "type": "boolean" }
      }
    },

    "Arg": {
      "type": "object",
      "required": ["value"],
      "additionalProperties": true,
      "properties": {
        "name": { "type": ["string", "null"] },
        "value": { "$ref": "#/$defs/Expr" }
      }
    },

    "Coeffs": {
      "title": "Coeffs",
      "description": "Baked filter/stat coefficients (designed by Python/SciPy; the runtime only runs the recurrence). All fields optional.",
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "sos": { "type": "array", "items": { "type": "array", "items": { "type": "number" } } },
        "fir_taps": { "type": "array", "items": { "type": "number" } },
        "group_delay": { "type": "integer" },
        "alpha": { "type": "number" },
        "dt": { "type": "number" },
        "window_samples": { "type": "integer" },
        "dwell_samples": { "type": "integer" },
        "nperseg": { "type": "integer" },
        "noverlap": { "type": "integer" }
      }
    },

    "Expr": {
      "title": "Expr",
      "description": "IR expression node; a tagged union discriminated by the `node` field.",
      "type": "object",
      "required": ["node"],
      "additionalProperties": true,
      "properties": { "node": { "type": "string" } },
      "oneOf": [
        { "$ref": "#/$defs/ExprNumber" },
        { "$ref": "#/$defs/ExprString" },
        { "$ref": "#/$defs/ExprBool" },
        { "$ref": "#/$defs/ExprStreamRef" },
        { "$ref": "#/$defs/ExprThresholdRef" },
        { "$ref": "#/$defs/ExprControlRef" },
        { "$ref": "#/$defs/ExprRewardField" },
        { "$ref": "#/$defs/ExprCall" },
        { "$ref": "#/$defs/ExprArray" },
        { "$ref": "#/$defs/ExprTuple" },
        { "$ref": "#/$defs/ExprBinop" },
        { "$ref": "#/$defs/ExprConditional" },
        { "$ref": "#/$defs/ExprBlock" }
      ]
    },

    "ExprNumber": {
      "type": "object",
      "required": ["node", "value"],
      "additionalProperties": true,
      "properties": { "node": { "const": "number" }, "value": { "type": "number" } }
    },
    "ExprString": {
      "type": "object",
      "required": ["node", "value"],
      "additionalProperties": true,
      "properties": { "node": { "const": "string" }, "value": { "type": "string" } }
    },
    "ExprBool": {
      "type": "object",
      "required": ["node", "value"],
      "additionalProperties": true,
      "properties": { "node": { "const": "bool" }, "value": { "type": "boolean" } }
    },
    "ExprStreamRef": {
      "type": "object",
      "required": ["node", "target"],
      "additionalProperties": true,
      "properties": { "node": { "const": "stream_ref" }, "target": { "type": "string" } }
    },
    "ExprThresholdRef": {
      "type": "object",
      "required": ["node", "target"],
      "additionalProperties": true,
      "properties": { "node": { "const": "threshold_ref" }, "target": { "type": "string" } }
    },
    "ExprControlRef": {
      "type": "object",
      "required": ["node", "target", "default"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "control_ref" },
        "target": { "type": "string" },
        "default": { "type": "number" }
      }
    },
    "ExprRewardField": {
      "type": "object",
      "required": ["node", "field_path"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "reward_field" },
        "field_path": { "type": "string" }
      }
    },
    "ExprCall": {
      "type": "object",
      "required": ["node", "callee"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "call" },
        "callee": { "type": "string" },
        "args": { "type": "array", "items": { "$ref": "#/$defs/Arg" } },
        "coeffs": {
          "oneOf": [
            { "$ref": "#/$defs/Coeffs" },
            { "type": "null" }
          ]
        }
      }
    },
    "ExprArray": {
      "type": "object",
      "required": ["node", "elements"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "array" },
        "elements": { "type": "array", "items": { "$ref": "#/$defs/Expr" } }
      }
    },
    "ExprTuple": {
      "type": "object",
      "required": ["node", "elements"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "tuple" },
        "elements": { "type": "array", "items": { "$ref": "#/$defs/Expr" } }
      }
    },
    "ExprBinop": {
      "type": "object",
      "required": ["node", "op", "left", "right"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "binop" },
        "op": { "type": "string" },
        "left": { "$ref": "#/$defs/Expr" },
        "right": { "$ref": "#/$defs/Expr" }
      }
    },
    "ExprConditional": {
      "type": "object",
      "required": ["node", "cond", "then", "else"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "conditional" },
        "cond": { "$ref": "#/$defs/Expr" },
        "then": { "$ref": "#/$defs/Expr" },
        "else": { "$ref": "#/$defs/Expr" }
      }
    },
    "ExprBlock": {
      "type": "object",
      "required": ["node", "fields"],
      "additionalProperties": true,
      "properties": {
        "node": { "const": "block" },
        "kind": { "type": ["string", "null"] },
        "fields": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Expr" } }
      }
    }
  }
}
```

- [ ] **Step 2: Verify the schema is well-formed AND validates the v0.2 golden**

Run:
```bash
PYTHONPATH="$PWD" ./.venv/bin/python -c "
import json, jsonschema
schema = json.load(open('refrain-core/schema/ir-json-v0.2.schema.json'))
jsonschema.Draft202012Validator.check_schema(schema)
v = jsonschema.Draft202012Validator(schema)
doc = json.load(open('refrain-core/tests/fixtures/composite_smr_theta.ir.json'))
errs = sorted(v.iter_errors(doc), key=lambda e: list(e.path))
assert not errs, [(list(e.path), e.message) for e in errs]
print('v0.2 schema valid; golden validates; version =', doc['refrain_ir_version'])
"
```
Expected: `v0.2 schema valid; golden validates; version = 0.2`. (If the golden fails, the schema's `Reward`/`RewardComponent`/`Expr` defs diverge from the emitted shape — fix the schema to match `_emit_reward`, not the fixture.)

- [ ] **Step 3: Confirm the v0.1 schema file is untouched**

Run: `git diff --stat refrain-core/schema/ir-json-v0.1.schema.json`
Expected: empty output (no changes to v0.1).

- [ ] **Step 4: Commit**

```bash
git add refrain-core/schema/ir-json-v0.2.schema.json
git commit -m "feat(schema): publish ir-json-v0.2.schema.json (reward combine + components)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Version-aware schema validation in the gate

**Files:**
- Modify: `tests/test_ir_json_schema.py:19-45`

- [ ] **Step 1: Write the failing test (a v0.2 golden must validate against the v0.2 schema)**

Replace the schema-path / validator block (lines 19-45) of `tests/test_ir_json_schema.py` with a version-aware selector. Replace from `SCHEMA_PATH = …` (line 19) through the end of `test_golden_ir_json_validates` (line 45) with:

```python
SCHEMA_DIR = REPO / "refrain-core" / "schema"
SCHEMA_BY_VERSION = {
    "0.1": SCHEMA_DIR / "ir-json-v0.1.schema.json",
    "0.2": SCHEMA_DIR / "ir-json-v0.2.schema.json",
}
FIXTURES = REPO / "refrain-core" / "tests" / "fixtures"
IR_JSON_FILES = sorted(FIXTURES.glob("*.ir.json"))


@pytest.fixture(scope="module")
def validators():
    """One validator per published schema version, keyed by version string."""
    out = {}
    for version, path in SCHEMA_BY_VERSION.items():
        schema = json.loads(path.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        out[version] = jsonschema.Draft202012Validator(schema)
    return out


def test_schema_files_exist():
    for version, path in SCHEMA_BY_VERSION.items():
        assert path.exists(), f"missing v{version} schema: {path}"


def test_corpus_is_nonempty():
    assert IR_JSON_FILES, "no *.ir.json fixtures found — corpus path wrong?"


def test_corpus_covers_both_versions():
    # The corpus must exercise both wire versions, or the v0.2 schema is never
    # validated by the gate. (A v0.1 fixture and the composite_smr_theta v0.2
    # fixture both exist after Stage 2.)
    versions = {json.loads(p.read_text()).get("refrain_ir_version") for p in IR_JSON_FILES}
    assert "0.1" in versions, "no v0.1 golden vectors found"
    assert "0.2" in versions, "no v0.2 golden vectors found (composite fixture missing?)"


@pytest.mark.parametrize("ir_path", IR_JSON_FILES, ids=lambda p: p.stem)
def test_golden_ir_json_validates(validators, ir_path):
    doc = json.loads(ir_path.read_text())
    version = doc.get("refrain_ir_version")
    assert version in validators, f"{ir_path.name}: unknown refrain_ir_version {version!r}"
    validator = validators[version]
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, f"v{version} schema rejected {ir_path.name}:\n" + "\n".join(
        f"  {list(e.path)}: {e.message}" for e in errors
    )
```

Also update `test_valid_envelope_is_accepted` / the rejection tests (lines 48-81) to use the v0.1 validator explicitly. Change `_valid_envelope`'s callers and the `validator` fixture references: replace each `validator` parameter in `test_valid_envelope_is_accepted`, `test_unknown_expr_node_is_rejected`, `test_missing_required_top_level_field_is_rejected` with `validators` and select v0.1:

```python
def test_valid_envelope_is_accepted(validators):
    assert validators["0.1"].is_valid(_valid_envelope())


def test_unknown_expr_node_is_rejected(validators):
    doc = _valid_envelope()
    doc["output"]["x"] = {"node": "not_a_real_node"}
    assert not validators["0.1"].is_valid(doc)


def test_missing_required_top_level_field_is_rejected(validators):
    doc = _valid_envelope()
    del doc["sample_rate_hz"]
    assert not validators["0.1"].is_valid(doc)
```

(The old `test_schema_file_exists` is renamed to `test_schema_files_exist`; delete the single-`SCHEMA_PATH` definition entirely.)

- [ ] **Step 2: Run the schema-validation suite**

Run: `PYTHONPATH="$PWD" ./.venv/bin/python -m pytest tests/test_ir_json_schema.py -v`
Expected: PASS — every v0.1 golden validates against v0.1; `composite_smr_theta` (v0.2) validates against v0.2; `test_corpus_covers_both_versions` confirms both are present.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ir_json_schema.py
git commit -m "test(schema): version-aware golden-vector validation (v0.1 + v0.2)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Build the wheel + confirm Python↔Rust composite parity end-to-end

**Files:**
- (No source edits — this task builds the wheel and proves the FFI path.)

- [ ] **Step 1: Build + install the refrain_core wheel from current Rust source**

Run:
```bash
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 ./.venv/bin/python -m pip install --force-reinstall --no-deps refrain-core/
```
Expected: `Successfully installed refrain_core-…`. (Same install path the gate's Step 3 uses; the Rust `target/` cache makes it incremental.)

- [ ] **Step 2: Confirm a weighted protocol runs on `backend="rust"` and matches Python**

Run:
```bash
PYTHONPATH="$PWD" ./.venv/bin/python -c "
from pathlib import Path
import numpy as np
from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

amp = load_amp_profile(Path('src/refrain/amp_profiles/q21.json'))
ir = resolve(parse_file(Path('bench/protocols/composite_smr_theta.refrain')), amp)
rng = np.random.default_rng(0)
sig = rng.standard_normal((1024, 1)) * 10.0  # 'Cz' only — protocol requires ['Cz']

def run(backend):
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=('Cz',),
                        record_streams=True, backend=backend)
    ev.start(skip_warmup=True)
    last = None
    for i in range(0, len(sig), 32):
        ev.step_chunk(sig[i:i+32])
        last = ev.last_streams().get('reward.composite')
    return last

py = run('python'); rs = run('rust')
assert py is not None and rs is not None, 'reward.composite missing'
maxdiff = float(np.max(np.abs(np.asarray(py) - np.asarray(rs))))
print('max|python - rust| reward.composite =', maxdiff)
assert maxdiff < 1e-9, maxdiff
print('PARITY OK')
"
```
Expected: `max|python - rust| reward.composite = <~1e-13>` then `PARITY OK`. (The channel layout is `('Cz',)` because the protocol `requires.channels == ["Cz"]`; the referential `linked_ears` reference falls back to common-average when no ear channels are present — IDENTICAL in both backends, so parity holds. If the run errors on a missing channel, supply the same layout to both backends; do NOT special-case one backend.)

- [ ] **Step 3: Commit (no-op safety — nothing to commit; this is a verification gate)**

No file changes. If Step 2 surfaced a divergence, STOP and debug (systematic-debugging skill) — do not proceed; the parity target is ~1e-13.

---

### Task 8: Dual-backend composite parity test (so the gate's Step 4 exercises Rust)

**Files:**
- Modify: `tests/test_eval_composite.py` (add a `backend`-parametrized parity test)

- [ ] **Step 1: Add the dual-backend test**

Append to `tests/test_eval_composite.py` (the existing python-pinned tests stay as-is; this adds a NEW test that runs on BOTH backends so `check_equivalence.py` Step 4, which runs `test_eval_*.py` under `REFRAIN_EVAL_BACKEND=rust`, exercises the Rust composite path):

```python
@pytest.mark.parametrize("backend", ["python", "rust"])
def test_composite_parity_across_backends(amp, backend):
    """The weighted composite must compute identically on python and rust.
    Pinned per-backend so the drift gate (which re-runs this file under
    REFRAIN_EVAL_BACKEND=rust) exercises the Rust v0.2 path. Skips rust when
    the wheel is not installed (a local-without-wheel convenience; CI/gate
    always has it)."""
    if backend == "rust":
        import importlib.util
        if importlib.util.find_spec("refrain_core") is None:
            pytest.skip("refrain_core wheel not installed")

    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend=backend)
    ev.start(skip_warmup=True)
    # Cz=5, linked_ears=0 → raw=5 (referential active-minus-reference) → both
    # sigmoids ≈ 1 → composite = (1*1 + 1*0)/(1+1) = 0.5 on BOTH backends.
    chunk = np.column_stack([np.full(64, 5.0), np.zeros(64)]).astype(np.float64)
    ev.step_chunk(chunk)
    comp = ev.last_streams()["reward.composite"]
    assert np.allclose(comp, 0.5, atol=1e-9), (backend, float(np.max(comp)), float(np.min(comp)))


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_composite_reweight_parity_across_backends(amp, backend):
    """Live set_control on a weight moves the composite identically on both
    backends (the Rust `Control::Weight` cell mirrors the Python control read)."""
    if backend == "rust":
        import importlib.util
        if importlib.util.find_spec("refrain_core") is None:
            pytest.skip("refrain_core wheel not installed")

    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend=backend)
    ev.start(skip_warmup=True)
    chunk = np.column_stack([np.full(64, 5.0), np.zeros(64)]).astype(np.float64)
    ev.set_control("w_theta", 0.0)  # drop suppress weight → composite = 1.0
    ev.step_chunk(chunk)
    comp = ev.last_streams()["reward.composite"]
    assert np.allclose(comp, 1.0, atol=1e-9), (backend, float(np.max(comp)), float(np.min(comp)))
```

- [ ] **Step 2: Run the dual-backend test on BOTH backends**

Run (python, default): `PYTHONPATH="$PWD" ./.venv/bin/python -m pytest tests/test_eval_composite.py -v`
Expected: PASS — all existing python tests + the new parity tests (the `rust` params run too since the wheel is installed from Task 7).

Run (force rust, as the gate does): `REFRAIN_EVAL_BACKEND=rust PYTHONPATH="$PWD" ./.venv/bin/python -m pytest tests/test_eval_composite.py -v`
Expected: PASS — under `REFRAIN_EVAL_BACKEND=rust`, the `backend="python"`-pinned Stage 1 tests still pin python (explicit `backend=` wins over the env var — confirm in `Evaluator.live`), and the parity tests run rust. If a Stage-1 python-pinned test breaks under the env var, that means `backend=` is NOT respected over the env var — in that case, leave the Stage-1 tests' explicit `backend="python"` as the source of truth (they were green pre-Stage-2) and confirm `Evaluator.live` precedence at `eval_.py:344-376`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_eval_composite.py
git commit -m "test(eval): dual-backend composite parity (gate exercises rust v0.2 path)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Run the full drift gate end-to-end

**Files:**
- (No source edits — verifies all five gate steps pass with v0.2 in the corpus. Verify `check_equivalence.py` needs no edit: Step 1 regenerates the v0.2 fixture (Task 4 added the stem), Step 2 `cargo test` runs `composite.rs`, Step 4 runs the dual-backend test, Step 5 runs the version-aware schema test.)

- [ ] **Step 1: Read check_equivalence.py to confirm no edit is required**

Read `refrain-core/tools/check_equivalence.py:54-105`. Confirm: Step 1 calls `gen_fixtures.py` (now emits `composite_smr_theta`), Step 2 runs `cargo test` (auto-discovers `composite.rs`), Step 4 globs `tests/test_eval_*.py` (includes `test_eval_composite.py`), Step 5 runs `test_ir_json_schema.py` (now version-aware). No code change needed. (If a future maintainer wants an explicit comment, add one to the module docstring noting v0.2 coverage — optional, not required for the gate to pass.)

- [ ] **Step 2: Run the full gate**

Run:
```bash
PYTHONPATH="$PWD" ./.venv/bin/python refrain-core/tools/check_equivalence.py
```
Expected: all five steps PASS:
```
  PASS  gen_fixtures
  PASS  cargo_test
  PASS  build_wheel
  PASS  dual_backend_pytest
  PASS  schema_validation
RESULT: PASS — …
```

- [ ] **Step 3: Run the full Python suite once more (catch any cross-suite regression)**

Run: `PYTHONPATH="$PWD" ./.venv/bin/python -m pytest -q`
Expected: PASS — all tests green (Stage 1 python tests, the new dual-backend tests, schema tests).

- [ ] **Step 4: Confirm git working tree is clean (fixtures committed, no stray regen diffs)**

Run: `git status --porcelain`
Expected: empty — gen_fixtures (Step 2 above re-ran it) produced byte-identical fixtures to what Task 4 committed. If a fixture changed, the regeneration is non-deterministic or a source edit slipped in — investigate before declaring done.

- [ ] **Step 5: Commit (only if Step 4 surfaced a legitimate fixture refresh)**

If and only if `git status` shows a fixture change that is a correct regeneration (e.g. you tweaked the protocol), commit it:

```bash
git add refrain-core/tests/fixtures/
git commit -m "chore(fixtures): refresh v0.2 golden vectors

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Otherwise, no commit — the gate run is the verification.

---

## Self-Review

### 1. Spec coverage (each Stage-2 requirement → task)

| Spec / Stage-2 requirement | Task |
|---|---|
| Rust core deserializes v0.2 (components + combine) | Task 1 (`Reward.combine`/`.components`, `RewardComponent`) |
| Rust accepts BOTH v0.1 and v0.2 in one deserializer | Task 1 (`#[serde(default)]` + `default_combine`); Task 1 Step 5 regression |
| v0.1 IR-JSON runs byte-identically in Rust; existing golden vectors + `cargo test` stay green | Task 1 Step 5, Task 2 Step 4, Task 3 Step 2 (all `cargo test`) |
| Weighted composite = same weighted-average formula, suppress = 1−signal | Task 3 (math mirrors `eval_.py:692-717`) |
| Same runtime all-zero guard (→0.0, no NaN) | Task 3 (`if weight_sum[i] > 0.0 { … } else { 0.0 }`) |
| Composite-before-continuous ordering | Task 3 (inserted BEFORE the `reward_continuous`/`reward_event` blocks) |
| Composite threaded into the event/dwell condition | Task 3 (env binding read by `CNode::Reward` in the dwell sub-condition); Task 4 protocol has `dwell(condition: above(reward.composite, …))` |
| `reward.composite` / `reward.<name>.signal` field eval | Task 3 (env keys `reward.composite`, `reward.<name>.signal`) reusing `CNode::Reward` |
| Weights read via control ref; live `set_control` moves the composite | Task 2 (`Control::Weight` cell + `BuildCtx::register`), Task 3 (reads cell per chunk), Task 8 (`test_composite_reweight_parity_across_backends`) |
| Parity ~1e-13 | Task 4 (Rust golden parity), Task 7 (FFI parity `< 1e-9`, expect ~1e-13) |
| v0.2 schema published; v0.1 schema unchanged | Task 5 (new file + Step 3 confirms v0.1 untouched) |
| Drift gate validates BOTH versions, dual-backend | Task 6 (version-aware schema test), Task 8 (dual-backend eval), Task 9 (full gate) |
| A weighted protocol runs identically on `backend="rust"` | Task 7 (FFI), Task 8 (dual-backend), Task 9 (gate Step 4) |
| Reuse existing Rust machinery (control-ref reads, dwell, reward-field dispatch); no parallel evaluator | Task 2 reuses `build_node`/`control_cell`/`BuildCtx::register`; Task 3 reuses `CNode::Reward`/`env`/`coerce_streams`/`Dwell` |
| v0.2 fixture comes from a weighted bench protocol via gen_fixtures | Task 4 |

**Explicitly OUT of Stage 2 (correctly absent):** `combine = "independent"` + fan-out per-site components (Stage 3); the package version bump + `IR_JSON_VERSION` change (deferred to end of Stage 3 — `IR_JSON_VERSION` stays `"0.1"`, per-protocol selector already emits `"0.2"`). The v0.1 schema file is not edited (Task 5 Step 3 asserts the empty diff).

### 2. Placeholder scan

No "TBD"/"TODO"/"implement later"/"add error handling" placeholders. Every Rust/Python/JSON step shows the complete content. The v0.2 schema (Task 5) is written in full (not "copy v0.1 and change X" — the entire file is in the step). The two conditional steps (Task 8 Step 2's env-var precedence note, Task 9 Step 5's conditional fixture commit) are gated on an observed outcome and state the exact action, not a vague instruction.

### 3. Type consistency

- `Reward.combine: String` (default `"all"`) and `Reward.components: Vec<RewardComponent>` are consistent across Task 1 (def), Task 2 (`r.components.iter()`), Task 4 (`r.combine`, `r.components.len()`).
- `RewardComponent { name, role, signal, weight }` fields are identical across Task 1 (def + deser test), Task 2 (`c.name`, `c.role`, `c.signal`, `c.weight`).
- `CompiledComponent { name, role, signal, weight }` and `ComponentRole { Reward, Suppress }` are defined in Task 2 and consumed in Task 3 (`comp.role`, `comp.signal.eval`, `comp.weight.lock()`, `comp.name`) — names match.
- `Control::Weight { value: ControlCell }` is defined in Task 2 (enum variant + `apply` arm) and registered in `build_component` (Task 2) — variant name and field name (`value`) match; `ControlCell`/`control_cell` are the existing `dsp` re-exports (`eval.rs:16`).
- Env key conventions match the Python reference exactly: lookup keys `reward.composite` and `reward.<name>.signal` (read by `CNode::Reward` via `env.get("reward.<field>")`, `eval.rs:287-290`); stream keys `reward.composite` and `reward.component.<name>` (Python `eval_.py:787-790`); tap keys `reward/composite` and `reward/component[<name>]` (Python `eval_.py:895-898`). Task 3 binds all three families.
- Tolerances match the existing harness: `atol=1e-6, rtol=1e-4` (Task 4, mirroring `equivalence.rs:65`); the composite arithmetic parity target is ~1e-13 (Task 7 asserts `< 1e-9`, comfortably above the expected machine-precision agreement).
- Schema `RewardComponent` def (Task 5: `name`/`canonical_name`/`role`/`signal`/`weight`) matches the emitted shape (`ir_json.py:309-318`); `Reward.combine` enum `["all","any","weighted"]` matches the resolver's accepted set.

### Notes for the executor

- **Divergence from the spec's literal syntax (hard gates):** identical to Stage 1 — the implemented hard-gate form is `inhibit "<n>" { metric/threshold/action }` (→ `Inhibit` in Rust, already handled), and a suppress band is `inhibit "<n>" { signal + weight }` (→ a `role="suppress"` reward component). The Stage-1 emitter already routes these correctly; Stage 2 only consumes the emitted JSON. No `gate =` sugar.
- **`refrain_ir_version` is unread by Rust — by design.** serde ignores it (the `Protocol` struct has no such field, `additionalProperties` are dropped). Back-compat is purely `#[serde(default)]`. Do NOT add version dispatch to `python.rs`/`ir.rs`; that would be reinventing what serde defaults already give.
- **Where the Rust reality diverges from the prompt's assumptions:**
  1. **Composite is bound into `env`, not threaded as function args.** The prompt anticipated a Python-style `reward_composite=` kwarg threading. The Rust evaluator instead resolves `reward.*` via `CNode::Reward(field)` → `env.get("reward.<field>")` (`eval.rs:287-290`). So the cleanest, reuse-faithful approach is to bind `reward.composite` / `reward.<name>.signal` into `env` (Task 3) — the existing dispatch then handles `continuous`, `output`, and dwell conditions with ZERO new plumbing. This is strictly less code than mirroring the kwarg path and is noted as the key Rust decision.
  2. **Component STREAM key ≠ component LOOKUP key.** Python exposes the component as the stream `reward.component.<name>` but the field access resolves `reward.<name>.signal`. Rust must bind BOTH env keys (Task 3): `reward.<name>.signal` for `CNode::Reward`, and `reward.component.<name>` purely so `coerce_streams` surfaces the Python stream name. Missing the second key means the `reward.component.<name>` stream is absent from `last_streams` and a future component-stream golden would fail.
  3. **Weight live-tune needs a NEW `Control::Weight` cell.** Python reads `control_chunks[target]` fresh each chunk with no dedicated impl; the Rust analogue is a shared `ControlCell` written by `set_control`. There is no existing `Control` variant for a bare value (the existing three retune `target_pct`/`alpha`/`midpoint` *inside* a stage), so a `Control::Weight { value }` cell is the minimal faithful addition. (`set_control`'s "known control with no bound stage = no-op" semantics still hold for a literal weight, which registers no binding.)
- **Top Stage-2 risks:**
  1. **Composite NaN / float-equality at the `weight_sum == 0` boundary.** Python uses the doubled `np.where(weight_sum>0, num/np.where(weight_sum>0, weight_sum, 1), 0)` to avoid a `0/0` warning; the Rust `if weight_sum[i] > 0.0 { num/weight_sum } else { 0.0 }` is the exact branch equivalent. The risk is a per-chunk weight that is *exactly* 0.0 on some samples but not others (only possible via a partial-chunk `set_control` mid-chunk — which neither backend does, set_control lands between chunks) — so in practice the whole chunk shares one weight. Keep the per-sample form anyway to mirror Python bit-for-bit.
  2. **Signal `[0,1]` clamp ordering.** Python clamps each component signal with `np.clip(…, 0, 1)` BEFORE the role rule and the weighted sum. Task 3 clamps in the same place (per-element `v.clamp(0.0,1.0)` before `success`). A sigmoid is already in range so the clamp is a no-op there, but a `linear` signal is NOT clamped by its impl — getting the clamp position wrong would diverge on a linear-signal component. The chosen bench protocol uses only sigmoids, so the golden won't catch a misplaced clamp; the clamp-before-role ordering must be preserved by inspection (cross-check against `eval_.py:702-713`).
  3. **`backend=` vs `REFRAIN_EVAL_BACKEND` precedence under the gate.** The gate's Step 4 sets `REFRAIN_EVAL_BACKEND=rust` and runs ALL `test_eval_*.py`. Stage 1's composite tests pin `backend="python"` explicitly. If `Evaluator.live` lets the env var override an explicit `backend=`, those Stage-1 tests would run on Rust under the gate and could behave differently. Task 8 Step 2 verifies this precedence; the expectation (per `eval_.py:344-376`) is that an explicit `backend=` wins. If it does NOT, the Stage-1 python-pinned tests are the ones at risk, not the new parity tests — flag and confirm before declaring the gate green.
  4. **`linked_ears` reference with a Cz-only channel layout.** The bench protocol `requires.channels == ["Cz"]`, but `referential(reference: "linked_ears")` needs ≥2 ear channels (`A1`/`A2`/…). With only `Cz`, `Referential::new` returns `ref_indices = None` → common-average over the single channel = the channel itself → `active - active = 0`?? No: common-average of `[Cz]` is `Cz`, so `active - refv = 0`. The Python `ReferentialImpl` must behave identically (it does — same fallback). Risk: if the two backends diverge on the single-channel common-average fallback, the composite diverges. Task 7 uses `('Cz',)` for BOTH and asserts parity; the gen_fixtures harness uses `CHANNELS = ("Cz", "A1", "A2")` (so `linked_ears` resolves to A1+A2 there) — meaning the GOLDEN fixture and the Task-7 ad-hoc check use different layouts. This is fine (each is internally Python↔Rust consistent), but do NOT cross-compare the Task-7 numbers against the golden. The golden (3-channel layout) is the authoritative parity vector.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-25-reward-engine-v0.2-stage2.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
