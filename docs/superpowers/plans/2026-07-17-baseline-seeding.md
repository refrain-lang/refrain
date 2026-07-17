# Baseline Seeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a protocol declare that a control derives its value from a percentile of a named signal, measured during warmup and held for the run, so every host gets identical baseline-seeding from the engine with no host code.

**Architecture:** A control gains an optional `seed = percentile { from, window, target_pct }` block (no grammar change — it reuses `NAME = kind { ... }`). The resolver bakes it into a new `IRControlSeed` on `IRControl`, validated against derives/warmup and dead-seed-eliminated. The emitter writes `seed` only when present and tags seeding protocols `refrain_ir_version = "0.3"`. Both evaluators (Python `eval_.py`, Rust `eval.rs`) run a per-control **polled latch**: ingest the `from` derive during `warmup`, and at the first `run` chunk — before any threshold steps — compute the percentile and write the control through the existing `set_control` → `update_control` path, then hold. A `seed_report()` accessor (not a tap) surfaces the outcome. Old engines refuse `0.3` protocols at load (SPEC §9.3 version gate).

**Tech Stack:** Python 3 (dataclasses, NumPy), Rust (serde, PyO3, uniffi), pytest, `cargo test`. `refrain` and `refrain-core` live in one repo and ship lockstep.

## Global Constraints

Copied verbatim from `docs/superpowers/specs/2026-07-16-baseline-seeding-design.md`. Every task's requirements implicitly include this section.

- **Lockstep release.** `refrain` and `refrain-core` are lockstep since v0.14.0: this feature ships as **v0.15.0** by bumping BOTH `pyproject.toml` files, `refrain-core/Cargo.toml`, and BOTH CHANGELOGs in one `release:` PR, then tagging the merge commit. Never tag before the bump PR merges.
- **No grammar changes.** `seed = percentile { ... }` is `assignment: NAME "=" expression` over `block_expr: NAME? block`. No new parser rule, no new keyword.
- **The statistic is the block kind.** `seed = percentile { ... }` — a future `median`/`mean` is a resolver case, not syntax. v1 supports **only** `percentile`.
- **`window = 60 s`, not `last 60 s`.** Reuse the existing `percentile(window: 2 min)` convention; a window is the trailing N seconds.
- **`target_pct` binds a control, never a copy.** It resolves to a `number` node or a `percent` control_ref (e.g. `reward_pct`). No second copy of the percentile may exist.
- **IR union is closed. Never add an `Expr` node discriminator.** `Expr` is an internally-tagged serde enum (`refrain-core/src/ir.rs:209`); an unknown *variant* fails the whole document load (`tests/test_ir_json_schema.py:95`). New *fields* are free; new *node kinds* are fatal. `target_pct` reuses existing `control_ref`/`number` nodes.
- **Omit-when-unused wire idiom.** `"seed"` is emitted **only when present** (`ir_json.py` `_emit_control`). Consequence — **every protocol that does not seed keeps a byte-identical `content_hash`**, and stays at IR version `0.1`/`0.2`.
- **Key on `state`, not `phase_index`.** `state` goes `warmup → run` exactly once (tested invariant `test_state_never_returns_to_warmup`). One baseline anchors all staged blocks.
- **Fire at the top of the chunk.** `_advance_if_due` runs *after* `_process_chunk` (`eval_.py:894`); the flip to `run` lands between chunks. The seed fires at the top of the first `run` chunk — before any threshold steps — so the seeded value is live with no one-chunk lag.
- **NaN skipping is not optional.** Rust `percentile_linear` *panics* on NaN (`dsp.rs:533`, `.expect("NaN in percentile buffer")`); Python propagates. The latch skips non-finite samples on ingest on **both** backends.
- **Fail closed, and `disarmed ≠ failed`.** A measurement that could not happen (skip_warmup, early advance, short open warmup, `< window_samples` finite samples) ⇒ suppress reward output for the session via the existing per-chunk `suppress_output` path. A clinician who wrote the control during warmup ⇒ **disarm** (run normally). Never conflate them.
- **Do not hand-roll storage.** Reuse `PercentileImpl` (Python) / `Percentile` (Rust) for the window; the deque idiom is already open-coded 6× per backend.
- **`seed_report()` is deliberately not a tap.** `refrain-core/tests/taps.rs:70` asserts exact tap key-set equality; a new tap key would force regenerating every `*.taps.json`. Keep it a separate accessor, exactly as v0.8.0 did for `export_state()`.
- **Surface syntax is block-delimited with quoted names.** Every `.refrain` protocol is `protocol "name" { meta{…} requires{…} input "x"{ montage=… } derive "y"{ from=…; pipeline=[…] } threshold "z"{ signal=…; type=… } reward{…} output{…} controls{…} session{ phases=[ phase{ name=…; duration=…; output_muted=… }, … ] } }`. Fields inside a block are separated by newlines **or** `;`; list elements by `,`. A control's type is `<name> = <kind> { … }`; a mode-conditional is a **ternary** `cond ? then : else` (e.g. `type = threshold_style == "baseline" ? absolute(value: thr_uv) : percentile(target_pct: reward_pct, window: 2 min)`). The `seed = percentile { … }` sub-block **parses today** and is ignored at resolve until the feature lands (verified). **Use the Verified Protocol Fixtures below verbatim** — they were compiled against the live compiler. Note two hazards proven during plan authoring: (1) Python `str.format()` breaks on a protocol body full of `{}` — use `%`-substitution (`BASE % {"seed": …}`); (2) the `[0,1]` output clamp masks a retune when the signal saturates (see [[project_refrain_parity_fixtures]]) — runtime tests feed a small input so scaled values stay in range.

## Verified Protocol Fixtures

**Task 4 creates `tests/_seed_fixtures.py` with exactly this content.** Every later Python task imports from it (`from tests._seed_fixtures import …`) rather than re-embedding a protocol string. `tests/` is already a package (`tests/__init__.py`); the leading `_` keeps pytest from collecting it. All strings below compiled with **zero errors** against the live compiler, and the runtime path (`parse → resolve → Evaluator.live(backend="python") → start → step_chunk`) was exercised. Notes: resolve/emit fixtures use a `referential` montage (env value irrelevant there); `SEED_PROTO` uses `passthrough()` so a constant input gives a **nonzero** constant `env` (a `referential` montage cancels a constant to 0 — verified); the ternary mode-conditional and the `seed` sub-block (parsed-and-ignored pre-feature) are confirmed.

```python
# tests/_seed_fixtures.py — protocol fixtures, compile-verified against the
# real surface syntax. Do not hand-edit the syntax; if a change is needed,
# re-compile via refrain.compile_json.compile_to_ir_json and confirm no errors.

SEEDING = '''protocol "seed_demo" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "seeding demo" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "thr" { signal = "env"; type = absolute(value: thr_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 90 s; output_muted = true },
    phase { name = "run";    duration = 300 s },
  ] }
}'''

# Identical minus the seed line (control declaration survives; no seed emitted).
NON_SEEDING = SEEDING.replace(
    '\n      seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }', '')

# Resolve-validation template. Substitute ONE seed line via `%` (NOT .format —
# the body is full of literal braces): BASE % {"seed": '<seed line or empty>'}.
BASE = '''protocol "seed_demo" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "seeding demo" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "thr" { signal = "env"; type = absolute(value: thr_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      %(seed)s
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 90 s; output_muted = true },
    phase { name = "run";    duration = 300 s },
  ] }
}'''

GOOD = BASE % {"seed": 'seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }'}

# Short-warmup runtime protocol (3 s warmup, 2 s window); thr_uv default 9.9 uV
# so a seed to ~5 visibly moves it. `magnitude()`-only derive -> constant env
# for a constant input (exact percentile parity).
SEED_PROTO = '''protocol "seed_run" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "runtime seed" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "env" { from = "raw"; pipeline = [ magnitude() ] }
  threshold "thr" { signal = "env"; type = absolute(value: thr_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 9.9 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 2 s; target_pct = reward_pct }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 3 s; output_muted = true },
    phase { name = "run";    duration = 5 s },
  ] }
}'''

# Mode-conditional for dead-seed elimination (adapted from tests/test_compile_json.py
# MODE_SRC). bindings={"threshold_style":"adaptive"} folds the absolute(thr_uv)
# branch out -> thr_uv unreferenced -> its seed must be dropped. "baseline" keeps it.
MODE_SRC = '''protocol "seed_mode" {
  meta { version = "1.0"; evidence = "clinical"; description = "x" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "env_t" {
    signal = "env"
    type = threshold_style == "baseline"
             ? absolute(value: thr_uv)
             : percentile(target_pct: reward_pct, window: 2 min)
  }
  reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_gain = reward.continuous }
  controls {
    threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 90 s; output_muted = true },
    phase { name = "run";    duration = 300 s },
  ] }
}'''
```

Two `.refrain` files (Task 1 `exprpos_control`, Task 16 `seed_smr_baseline`) are given verbatim in their tasks, in the same verified block syntax.

## Scope

This plan is the **`refrain` repo only** (Python engine + Rust core + docs) — spec §2 (the feature), §3 (both prerequisites), and the docs. It produces working, testable software on its own: after it lands, `seed { }` compiles and both engines execute it.

The spec's other repos are **out of scope** and become follow-on plans once v0.15.0 is tagged (they cannot compile `seed { }` until the grammar exists here):

- **refrain-protocols** — add `seed { }` to 21 baseline protocols (the user-visible fix: the 16 generic protocols get their first correct percentile via `reward_pct`).
- **coherence-recorder** — delete `baseline_seed.py` + `_BASELINE_SEED_PERCENTILES`.
- **coherence-companion** — delete the dead `BASELINE_SEED_PERCENTILES` port; read `seed_report()`. Build no stopgap.
- **coherence-portal** — populate `baseline_seeds` from `seed_report()`; fix the `ready`-state widget divergence.

**Ordering within this plan:** the two prerequisites (Tasks 1–2) are independent bug fixes that stand alone and land first. The feature (Tasks 3–17) then builds IR → resolve → Python runtime → Rust runtime → parity → docs. Task 18 is the release bump.

## File Structure

**Python (`src/refrain/`)**
- `ir.py` — add `IRControlSeed` dataclass; add `seed` field to `IRControl`. (Data shapes.)
- `ir_json.py` — emit `seed` when present; bump per-protocol version to `0.3`. (Wire format.)
- `resolver.py` — parse the `seed` block; a `_resolve_control_seeds` post-pass (validate, bake window, warmup-fits, dead-seed elimination). (Compile-time semantics.)
- `primitive_impls.py` — add `PercentileImpl.ingest()` (append-only, NaN-skipping). (Reused window storage.)
- `eval_.py` — seed latch: build, per-chunk ingest/fire, fail-closed/disarm, `seed_report()`, `_apply_control` refactor. (Runtime.)
- `compile_json.py` — echo `meta.seeds`. (Compile response metadata.)

**Rust (`refrain-core/src/`)**
- `ir.rs` — add `ControlSeed` struct; add `seed` field to `ControlDecl`. (Wire deser.)
- `dsp.rs` — add `Percentile::ingest()`, `Percentile::value_at()`, `Percentile::n_eff()`. (Reused window storage.)
- `eval.rs` — the version gate; the seed latch (mirror of Python); `apply_control_value` refactor; `seed_report()`. (Runtime + load refusal.)
- `python.rs` / `mobile.rs` — expose `seed_report()`; regenerate Swift/Kotlin bindings.

**Schemas / docs**
- `src/refrain/schema/ir-json-v0.3.schema.json` — new.
- `docs/SPEC.md`, `docs/IR-JSON.md`, `docs/PRIMITIVES.md`, `docs/DESIGN-NOTES.md`, `CHANGELOG.md`, `refrain-core/CHANGELOG.md`.

**Tests**
- Python: `tests/test_ir_json_seed.py`, `tests/test_resolve_seed.py`, `tests/test_eval_seed.py`, additions to `tests/test_compile_json.py`.
- Rust: `refrain-core/tests/seed.rs`, additions to `refrain-core/tests/ir_deser.rs`, `refrain-core/tests/set_control.rs`, `refrain-core/tests/version_gate.rs`.
- Conformance: `refrain-core/tools/gen_fixtures.py` + `bench/protocols/seed_smr_baseline.refrain` + `refrain-core/tests/seed_parity.rs`.

---

## Task 1: Prereq A — Rust expression-position control-ref fix

The bug: `refrain-core/src/eval.rs:1902` compiles a `control_ref` in a plain value position to `CNode::Const(*default)` — frozen forever, `set_control` a no-op success. Python evaluates it live (`eval_.py`, via `_control_deps`). All four control refs in the parity fixtures sit in recognised parameter slots, so the corpus structurally cannot catch it. Fix it and add a fixture with an expression-position control ref. Independent of the feature; lands first.

**Files:**
- Modify: `refrain-core/src/eval.rs:1894-1938` (`build_node`)
- Test: `refrain-core/tests/set_control.rs`
- Fixture protocol: `bench/protocols/exprpos_control.refrain` (create)
- Fixture generation: `refrain-core/tools/gen_fixtures.py` (register stem)

**Interfaces:**
- Consumes: `BuildCtx { sample_rate_hz, controls }` (`eval.rs:146`); `Control::Const { value: ControlCell }` (`eval.rs:111`); `CNode::ConstCell(ControlCell)` (`eval.rs:294`); `ctx.register(target, Control)` (used at `eval.rs:2089`, `:2128`).
- Produces: an expression-position `control_ref` that responds to `set_control`, matching Python.

- [ ] **Step 1: Write the failing Rust test**

In `refrain-core/tests/set_control.rs`, add:

```rust
// Add a fixture loader if set_control.rs has none — reuse the pattern from
// refrain-core/tests/equivalence.rs (read tests/fixtures/<stem>.ir.json,
// serde_json::from_str::<Protocol>). The output stream key is `output/<name>`;
// confirm against the returned map keys if this assert can't find it.
#[test]
fn expression_position_control_ref_is_live() {
    // reward.continuous = gain * "env", output x = reward.continuous, with `gain`
    // a control_ref in a plain value position (not a percentile/smooth/sigmoid
    // slot). Before the fix this baked to a frozen Const; set_control was a silent
    // no-op. Feed a SMALL input so gain*env stays inside the [0,1] output clamp
    // for both gain values (0.1 -> 0.1, then 3x -> 0.3); a saturating input would
    // clamp both to 1.0 and mask the retune (see project_refrain_parity_fixtures).
    let ir = load_ir("exprpos_control");
    let mut ev = Evaluator::new(&ir, 256.0, &["Cz".into()]);
    ev.start(false);
    let before = ev.step_chunk(&vec![vec![0.1_f64]; 8]);
    ev.set_control("gain", 3.0).unwrap();
    let after = ev.step_chunk(&vec![vec![0.1_f64]; 8]);
    let x_before = *before["output/x"].last().unwrap();
    let x_after = *after["output/x"].last().unwrap();
    assert!(
        x_before > 1e-6 && (x_after - 3.0 * x_before).abs() < 1e-9,
        "set_control on an expression-position control_ref must retune live: \
         before={x_before}, after={x_after}"
    );
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd refrain-core && cargo test --test set_control expression_position_control_ref_is_live`
Expected: FAIL — `x_after` equals `x_before` (frozen at the baked default), not `3 * x_before`.

- [ ] **Step 3: Make the control_ref live in `build_node`**

Replace `eval.rs:1902` (`Expr::ControlRef { default, .. } => CNode::Const(*default),`) with a shared-cell registration mirroring the recognised-slot path at `eval.rs:2128-2129`:

```rust
        Expr::ControlRef { target, default } => {
            // A control_ref in a plain value position must retune live, exactly
            // like Python's `_control_deps` forwarding. Register a Const binding
            // sharing the same cell the node reads each chunk (mirrors the
            // recognised `absolute(value: <ref>)` slot below), instead of
            // freezing the baked default.
            let cell = control_cell(*default);
            ctx.register(target, Control::Const { value: cell.clone() });
            CNode::ConstCell(cell)
        }
```

Confirm `control_cell` is in scope (it is the `dsp::control_cell` used at `eval.rs`/`dsp.rs`); add `use crate::dsp::control_cell;` to the imports at the top of `eval.rs` only if the symbol is not already imported.

- [ ] **Step 4: Create the fixture protocol**

Create `bench/protocols/exprpos_control.refrain` — verified block syntax; `gain` sits in a plain value position (`gain * "env"`), not a recognised DSP slot:

```refrain
protocol "exprpos_control" {
  meta { version = "1.0.0"; evidence = "demo"; description = "expr-position control ref" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"; pipeline = [ magnitude() ] }
  reward { continuous = gain * "env" }
  output { x = reward.continuous }
  controls {
    gain = frequency { default = 1.0 Hz; range = (0.5 Hz, 5 Hz); live_tunable = true }
  }
}
```

Register the stem in `gen_fixtures.py`'s generation list (the tuple at `refrain-core/tools/gen_fixtures.py:235-249`). This fixture has no `session`, so the reference run uses `skip_warmup=True` like the rest of the corpus (no warmup needed — the test drives `set_control` directly). No `SETCONTROL_STEM` schedule is required.

- [ ] **Step 5: Regenerate fixtures and run the full Rust suite**

Run (per `docs/CONFORMANCE.md` §1, from the worktree root, with the documented PYTHONPATH/scipy pin):
```bash
PYTHONPATH=src python refrain-core/tools/gen_fixtures.py
cd refrain-core && cargo test --test set_control && cargo test --test equivalence
```
Expected: PASS — `expression_position_control_ref_is_live` passes, and `equivalence` still passes for all fixtures including `exprpos_control`.

- [ ] **Step 6: Commit**

```bash
git add refrain-core/src/eval.rs refrain-core/tests/set_control.rs \
        bench/protocols/exprpos_control.refrain refrain-core/tools/gen_fixtures.py \
        refrain-core/tests/fixtures/exprpos_control.*
git commit -m "fix(core): expression-position control_ref must retune live (prereq A)"
```

---

## Task 2: Prereq B — SPEC §9.3 version gate

The Rust core ignores unknown fields and **never reads `refrain_ir_version`** — an old engine handed a `0.3` protocol would run it at the placeholder default, silently (the incident, bit for bit). Implement SPEC §9.3: refuse a protocol whose schema is newer than the runtime supports, loudly, at load.

**Files:**
- Modify: `refrain-core/src/ir.rs:12-47` (`Protocol` struct)
- Modify: `refrain-core/src/python.rs:79-95` (`RustEvaluator::new`), `refrain-core/src/mobile.rs:113` (load path)
- Modify: `refrain-core/src/eval.rs` (add a `const MAX_SUPPORTED_IR_VERSION` and a version-check helper)
- Test: `refrain-core/tests/version_gate.rs` (create)

**Interfaces:**
- Consumes: `serde_json::from_str::<Protocol>` at `python.rs:85` and `mobile.rs:113`.
- Produces: `fn check_ir_version(p: &Protocol) -> Result<(), String>` — `Ok` for supported, `Err(diagnostic)` for newer.

- [ ] **Step 1: Write the failing test**

Create `refrain-core/tests/version_gate.rs`:

```rust
use refrain_core::eval::{check_ir_version, MAX_SUPPORTED_IR_VERSION};
use refrain_core::ir::Protocol;

fn minimal(version: &str) -> String {
    format!(
        r#"{{"refrain_ir_version":"{version}","sample_rate_hz":256.0,
            "channels":["Cz"],"inputs":{{}},"derives":{{}}}}"#
    )
}

#[test]
fn refuses_a_newer_schema_version() {
    let p: Protocol = serde_json::from_str(&minimal("99.0")).unwrap();
    let err = check_ir_version(&p).unwrap_err();
    assert!(err.contains("99.0"), "diagnostic must name the version: {err}");
    assert!(err.contains(MAX_SUPPORTED_IR_VERSION));
}

#[test]
fn accepts_supported_versions() {
    for v in ["0.1", "0.2", "0.3"] {
        let p: Protocol = serde_json::from_str(&minimal(v)).unwrap();
        assert!(check_ir_version(&p).is_ok(), "version {v} must load");
    }
}

#[test]
fn missing_version_defaults_to_supported() {
    // Pre-version protocols omit the field; treat as the floor (0.1), not a refusal.
    let p: Protocol = serde_json::from_str(
        r#"{"sample_rate_hz":256.0,"channels":["Cz"],"inputs":{},"derives":{}}"#,
    ).unwrap();
    assert!(check_ir_version(&p).is_ok());
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd refrain-core && cargo test --test version_gate`
Expected: FAIL — `check_ir_version` / `MAX_SUPPORTED_IR_VERSION` do not exist; `Protocol` has no `refrain_ir_version` field.

- [ ] **Step 3: Add the field to `Protocol`**

In `refrain-core/src/ir.rs`, add to `struct Protocol` (after line 14, `pub sample_rate_hz`):

```rust
    /// Wire schema version (`_protocol_ir_version`). Absent on pre-versioning
    /// protocols; defaults to the floor so they keep loading. Read ONLY by the
    /// load-time version gate (SPEC §9.3) — the interpreter never branches on it.
    #[serde(default = "default_ir_version")]
    pub refrain_ir_version: String,
```

And add the default helper near `default_phase_mode` (`ir.rs:81`):

```rust
fn default_ir_version() -> String {
    "0.1".to_string()
}
```

- [ ] **Step 4: Add the gate to `eval.rs`**

Near the top of `refrain-core/src/eval.rs` (module level, with the other consts):

```rust
/// Highest IR-JSON schema version this runtime understands. Bumped in lockstep
/// with `refrain.ir_json._protocol_ir_version`. A protocol tagged higher is
/// refused at load (SPEC §9.3) rather than run at silent defaults.
pub const MAX_SUPPORTED_IR_VERSION: &str = "0.3";

/// Refuse a protocol whose schema is newer than this runtime supports. Compares
/// the dotted `major.minor` numerically so "0.10" > "0.9". A newer version is a
/// loud, load-time error, before a patient is connected.
pub fn check_ir_version(p: &crate::ir::Protocol) -> Result<(), String> {
    fn parse(v: &str) -> (u32, u32) {
        let mut it = v.split('.');
        let major = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
        let minor = it.next().and_then(|s| s.parse().ok()).unwrap_or(0);
        (major, minor)
    }
    if parse(&p.refrain_ir_version) > parse(MAX_SUPPORTED_IR_VERSION) {
        return Err(format!(
            "protocol requires IR-JSON schema {} but this runtime supports at \
             most {}. Update the engine.",
            p.refrain_ir_version, MAX_SUPPORTED_IR_VERSION
        ));
    }
    Ok(())
}
```

- [ ] **Step 5: Call the gate at both load sites**

In `refrain-core/src/python.rs:85-86`, after the `serde_json::from_str` and before constructing the evaluator:

```rust
        let p: Protocol = serde_json::from_str(ir_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("IR-JSON: {e}")))?;
        crate::eval::check_ir_version(&p)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
```

In `refrain-core/src/mobile.rs:113`, after the corresponding `serde_json::from_str`, add the same check mapped to that surface's error type (mirror the existing `.map_err` there).

- [ ] **Step 6: Run tests**

Run: `cd refrain-core && cargo test --test version_gate && cargo test`
Expected: PASS — all three gate tests pass; the existing suite is unaffected (every current fixture is `0.1`/`0.2`).

- [ ] **Step 7: Commit**

```bash
git add refrain-core/src/ir.rs refrain-core/src/eval.rs refrain-core/src/python.rs \
        refrain-core/src/mobile.rs refrain-core/tests/version_gate.rs
git commit -m "feat(core): refuse newer IR-JSON schema at load (SPEC §9.3, prereq B)"
```

---

## Task 3: `IRControlSeed` dataclass + `IRControl.seed`

**Files:**
- Modify: `src/refrain/ir.py:263-287` (`IRControl`); add `IRControlSeed` just before it
- Test: `tests/test_ir_json_seed.py` (create)

**Interfaces:**
- Produces: `IRControlSeed(statistic: str, from_entity: str, window_samples: int, target_pct: IRExpr, loc=None)`; `IRControl.seed: IRControlSeed | None = None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ir_json_seed.py`:

```python
from refrain.ir import IRControl, IRControlSeed, IRNumberLit
from refrain.dims import Dimensions  # adjust import if Dimensions lives elsewhere


def test_ircontrol_carries_an_optional_seed():
    seed = IRControlSeed(
        statistic="percentile",
        from_entity="derive/env",
        window_samples=15360,
        target_pct=IRNumberLit(value=70.0, dims=Dimensions(), unit=None),
    )
    ctrl = IRControl(
        name="thr_uv", canonical_name="control/thr_uv", type_kind="voltage",
        dims=Dimensions(), default=None, range_low=None, range_high=None,
        log_scale=False, label=None, live_tunable=True, tune_strategy=None,
        seed=seed,
    )
    assert ctrl.seed.window_samples == 15360
    assert ctrl.seed.from_entity == "derive/env"


def test_seed_defaults_to_none():
    ctrl = IRControl(
        name="reward_pct", canonical_name="control/reward_pct", type_kind="percent",
        dims=Dimensions(), default=None, range_low=None, range_high=None,
        log_scale=False, label=None, live_tunable=True, tune_strategy=None,
    )
    assert ctrl.seed is None
```

Confirm the correct import for `Dimensions` by grepping (`grep -n "class Dimensions" src/refrain/*.py`) and fix the import line to match; `IRNumberLit` is already exported from `ir.py`.

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_ir_json_seed.py -v`
Expected: FAIL — `ImportError: cannot import name 'IRControlSeed'`.

- [ ] **Step 3: Add the dataclass and field**

In `src/refrain/ir.py`, immediately before `class IRControl` (line 263), add:

```python
@dataclass(frozen=True, slots=True)
class IRControlSeed:
    """A control's baseline-seed rule (SPEC §baseline-seeding): derive the
    control's value from a percentile of a named signal measured during warmup,
    then hold it for the run."""

    statistic: str          # "percentile" — v1's only statistic; from the block kind
    from_entity: str        # canonical source, e.g. "derive/env"
    window_samples: int     # trailing window, baked at the compile rate
    target_pct: IRExpr      # a `number` node or a `percent` control_ref
    loc: Loc | None = None
```

Add `seed` to `IRControl` (append after `default_mode` at line 286, so every existing construction site stays valid — all optional fields are defaulted):

```python
    seed: IRControlSeed | None = None    # baseline-seed rule, or None
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_ir_json_seed.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/refrain/ir.py tests/test_ir_json_seed.py
git commit -m "feat(ir): add IRControlSeed and IRControl.seed"
```

---

## Task 4: Emit `seed` when present + per-protocol IR version 0.3 + schema

**Files:**
- Modify: `src/refrain/ir_json.py:123-138` (`_protocol_ir_version`), `:397-409` (`_emit_control`); add `_emit_seed`
- Create: `src/refrain/schema/ir-json-v0.3.schema.json`
- Create: `tests/_seed_fixtures.py` (verified protocol fixtures — copy from the "Verified Protocol Fixtures" section)
- Test: `tests/test_ir_json_seed.py` (extend)

**Interfaces:**
- Consumes: `IRControlSeed` (Task 3); `_emit_expr(expr, ctx)` (`ir_json.py:238`); `_EmitCtx` (`ir_json.py:141`).
- Produces (for later tasks): `tests/_seed_fixtures.py` exporting `SEEDING`, `NON_SEEDING`, `BASE`, `GOOD`, `SEED_PROTO`, `MODE_SRC`.
- Produces: wire `"seed": {"statistic","from","window_samples","target_pct"}` on a control, present only when seeded; `refrain_ir_version == "0.3"` for seeding protocols.

- [ ] **Step 1: Create the verified fixtures module, then write the failing unit tests**

First create `tests/_seed_fixtures.py` with **exactly** the content from the plan's "Verified Protocol Fixtures" section (`SEEDING`, `NON_SEEDING`, `BASE`, `GOOD`, `SEED_PROTO`, `MODE_SRC`). These are compile-verified block-syntax protocols; Tasks 5–12 import from this module. Do not retype the protocols — copy them verbatim from that section.

Then append the direct-emit unit tests to `tests/test_ir_json_seed.py`. They build an `IRControl` directly, so they are green the moment `_emit_seed` exists — **no dependency on the resolver** (Tasks 5–6). The end-to-end assertions that compile `SEEDING`/`NON_SEEDING` live in Task 6, where the resolver makes them pass.

```python
from refrain.ir import IRControl, IRControlSeed, IRNumberLit
from refrain.ir_json import _emit_control, _EmitCtx
from refrain.dims import Dimensions  # match Task 3's import


def _ctx():
    return _EmitCtx(sample_rate_hz=256.0, channel_names=("Cz",), controls={})


def test_emit_control_includes_seed_when_present():
    seed = IRControlSeed("percentile", "derive/env", 15360,
                         IRNumberLit(value=70.0, dims=Dimensions(), unit=None))
    ctrl = IRControl("thr_uv", "control/thr_uv", "voltage", Dimensions(),
                     None, None, None, False, None, True, None, seed=seed)
    out = _emit_control(ctrl, _ctx())
    assert out["seed"]["from"] == "derive/env"
    assert out["seed"]["window_samples"] == 15360


def test_emit_control_omits_seed_when_absent():
    ctrl = IRControl("reward_pct", "control/reward_pct", "percent", Dimensions(),
                     None, None, None, False, None, True, None)
    assert "seed" not in _emit_control(ctrl, _ctx())
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_ir_json_seed.py -k emit_control -v`
Expected: FAIL — `_emit_seed` does not exist yet, so `_emit_control` omits `seed` (the "includes" test fails).

- [ ] **Step 3: Implement `_emit_seed` + wire into `_emit_control` and `_protocol_ir_version`**

Add `_emit_seed` near `_emit_control` in `ir_json.py`:

```python
def _emit_seed(seed: IRControlSeed, ctx: _EmitCtx) -> dict:
    return {
        "statistic": seed.statistic,
        "from": seed.from_entity,
        "window_samples": seed.window_samples,
        "target_pct": _emit_expr(seed.target_pct, ctx),
    }
```

In `_emit_control` (`ir_json.py:397`), build the dict then append `seed` only when present (omit-when-unused idiom):

```python
def _emit_control(c: IRControl, ctx: _EmitCtx) -> dict:
    out = {
        "canonical_name": c.canonical_name,
        "type_kind": c.type_kind,
        "dims": _emit_dims(c.dims),
        "default": _emit_expr(c.default, ctx) if c.default is not None else None,
        "range_low": _emit_expr(c.range_low, ctx) if c.range_low is not None else None,
        "range_high": _emit_expr(c.range_high, ctx) if c.range_high is not None else None,
        "log_scale": c.log_scale,
        "label": c.label,
        "live_tunable": c.live_tunable,
        "tune_strategy": c.tune_strategy,
    }
    if c.seed is not None:
        out["seed"] = _emit_seed(c.seed, ctx)
    return out
```

In `_protocol_ir_version` (`ir_json.py:123`), a seed is the newest feature — check it first:

```python
def _protocol_ir_version(ir: IRProtocol) -> str:
    if any(c.seed is not None for c in ir.controls.values()):
        return "0.3"
    if (
        ir.reward.components
        or ir.reward.combine == "weighted"
        or ir.blocks
        or ir.reward_bundles
    ):
        return "0.2"
    return IR_JSON_VERSION
```

Import `IRControlSeed` at the top of `ir_json.py` (add to the existing `from .ir import (...)` block).

- [ ] **Step 4: Create the v0.3 schema**

Create `src/refrain/schema/ir-json-v0.3.schema.json` by copying `ir-json-v0.2.schema.json` and adding an optional `seed` to the control definition. The control object gains:

```jsonc
"seed": {
  "type": "object",
  "required": ["statistic", "from", "window_samples", "target_pct"],
  "properties": {
    "statistic": { "const": "percentile" },
    "from": { "type": "string" },
    "window_samples": { "type": "integer", "minimum": 1 },
    "target_pct": { "$ref": "#/$defs/expr" }
  },
  "additionalProperties": false
}
```

Add `"0.3"` to whatever `refrain_ir_version` enum/const the validator selects on (`grep -n "refrain_ir_version" src/refrain/schema/*.json src/refrain/*.py` to find the version→schema mapping in `_validate`/`compile_json.py`, and register the new file there).

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src pytest tests/test_ir_json_seed.py -v`
Expected: PASS — all tests in the file, including the two direct-emit unit tests and the Task 3 dataclass tests. (The end-to-end compile assertions are added in Task 6.)

- [ ] **Step 6: Commit**

```bash
git add src/refrain/ir_json.py src/refrain/schema/ir-json-v0.3.schema.json \
        tests/_seed_fixtures.py tests/test_ir_json_seed.py
git commit -m "feat(ir-json): emit control seed when present; tag seeding protocols v0.3"
```

---

## Task 5: Parse the `seed` block in `_resolve_control`

The seed's `from` (a derive) and warmup phase are resolved **after** controls (`resolver.py:209` controls, `:212` derives, `:216` session). So parse the block now, defer validation. Capture into a pending map.

**Files:**
- Modify: `src/refrain/resolver.py:129-160` (`__init__` — add `_pending_seeds`), `:1007-1052` (`_resolve_control`); add `_parse_control_seed` and `_PendingSeed`
- Test: `tests/test_resolve_seed.py` (create)

**Interfaces:**
- Consumes: `A.BlockExpr`, `A.StringLit`, `A.NumberLit` (`resolver.py` uses `from . import ast as A`); `self._assignments_dict` (used at `resolver.py:1010`); `_to_milliseconds` (`resolver.py:1765`).
- Produces: `self._pending_seeds: dict[str, _PendingSeed]` where `_PendingSeed(statistic, from_raw, window_ms, target_pct_ast, loc)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_resolve_seed.py`:

```python
from refrain.compile_json import compile_to_ir_json
from tests._seed_fixtures import BASE, GOOD  # verified block-syntax fixtures (Task 4)

# BASE is a `%`-template: substitute ONE seed line via `BASE % {"seed": "..."}`.
# NEVER str.format() — the protocol body is full of literal `{}`.


def test_seed_block_rejects_unknown_statistic():
    src = BASE % {"seed": 'seed = median { from = "env"; window = 60 s; target_pct = reward_pct }'}
    res = compile_to_ir_json(src)
    assert res.errors and "percentile" in res.errors[0].message


def test_seed_block_requires_duration_window():
    src = BASE % {"seed": 'seed = percentile { from = "env"; window = 60; target_pct = reward_pct }'}
    res = compile_to_ir_json(src)
    assert res.errors and "window" in res.errors[0].message
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py -v`
Expected: FAIL — the seed block is currently ignored (no error raised), so `res.errors` is empty.

- [ ] **Step 3: Add `_PendingSeed` and the parser**

At module scope in `resolver.py` (near the top, after imports), add:

```python
@dataclass(frozen=True)
class _PendingSeed:
    statistic: str
    from_raw: str
    window_ms: float
    target_pct_ast: "A.Expr"
    loc: "Loc | None"
```

Add `from dataclasses import dataclass, replace` to the imports (grep first — `dataclass` may already be imported; add `replace`).

In `_Resolver.__init__` (`resolver.py:129`), initialise the map:

```python
        self._pending_seeds: dict[str, _PendingSeed] = {}
```

Add the parser method (defers all cross-section validation to Task 6):

```python
    def _parse_control_seed(self, name: str, seed_ast: A.Expr) -> _PendingSeed:
        if not isinstance(seed_ast, A.BlockExpr) or seed_ast.name is None:
            raise ResolveError(
                f"control {name!r}.seed must be a typed block "
                "(e.g. `seed = percentile {{ ... }}`)",
                loc=seed_ast.loc,
            )
        statistic = seed_ast.name
        if statistic != "percentile":
            raise ResolveError(
                f"control {name!r}.seed: unsupported statistic {statistic!r} "
                "(v1 supports only `percentile`)",
                loc=seed_ast.loc,
            )
        fields = self._assignments_dict(seed_ast.body)
        from_expr = fields.get("from")
        if not isinstance(from_expr, A.StringLit):
            raise ResolveError(
                f"control {name!r}.seed.from must be a quoted derive name",
                loc=seed_ast.loc,
            )
        window_expr = fields.get("window")
        if not isinstance(window_expr, A.NumberLit) or window_expr.unit not in ("ms", "s", "min"):
            raise ResolveError(
                f"control {name!r}.seed.window must be a duration literal (ms/s/min)",
                loc=seed_ast.loc,
            )
        if "target_pct" not in fields:
            raise ResolveError(
                f"control {name!r}.seed needs a `target_pct` field", loc=seed_ast.loc)
        return _PendingSeed(
            statistic=statistic,
            from_raw=from_expr.value,
            window_ms=_to_milliseconds(window_expr),
            target_pct_ast=fields["target_pct"],
            loc=seed_ast.loc,
        )
```

- [ ] **Step 4: Capture the seed in `_resolve_control`**

In `_resolve_control` (`resolver.py:1017`, the numeric-control path), just before `return IRControl(...)` at line 1039, capture a `seed` field if present:

```python
        if "seed" in fields:
            self._pending_seeds[name] = self._parse_control_seed(name, fields["seed"])
```

(The returned `IRControl` keeps `seed=None`; Task 6's post-pass fills it via `replace`.)

- [ ] **Step 5: Run tests**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py -v`
Expected: PASS (both parser-rejection tests). `GOOD` is not yet asserted end-to-end (Task 6).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolve_seed.py
git commit -m "feat(resolve): parse control seed block into a pending map"
```

---

## Task 6: `_resolve_control_seeds` post-pass — validate `from`/`target_pct`, bake window

Runs after requires + derives + session are resolved. Validates the derive, validates `target_pct`, bakes `window_samples` at the compile rate, and writes the `IRControlSeed` onto the control.

**Files:**
- Modify: `src/refrain/resolver.py:176-243` (`resolve` — wire the post-pass), add `_resolve_control_seeds`, `_validate_seed_target_pct`
- Test: `tests/test_resolve_seed.py` (extend)

**Interfaces:**
- Consumes: `_PendingSeed` (Task 5); `requires_ir.sample_rate_chosen_hz` (`ir.py` `IRRequires`); `self.derives` (dict keyed `"derive/<name>"` per `resolver.py:429`); `self.controls`; `self._resolve_value_expr` (`resolver.py:1775`); `IRControlRef`/`IRNumberLit` (already imported); `replace` (Task 5).
- Produces: `self.controls[name]` with a populated `IRControlSeed`, or unchanged if the seed is dead (Task 8).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resolve_seed.py`:

```python
def test_good_seed_resolves_and_bakes_window():
    obj = compile_to_ir_json(GOOD).ir_json
    seed = obj["controls"]["thr_uv"]["seed"]
    assert seed["from"] == "derive/env"
    assert seed["window_samples"] == 60 * 256


def test_unknown_from_is_a_resolve_error():
    src = BASE % {"seed": 'seed = percentile { from = "nope"; window = 60 s; target_pct = reward_pct }'}
    res = compile_to_ir_json(src)
    assert res.errors and "nope" in res.errors[0].message


def test_target_pct_must_be_a_percent_control_or_number():
    # bind to a voltage control (thr_uv) -> rejected
    src = BASE % {"seed": 'seed = percentile { from = "env"; window = 60 s; target_pct = thr_uv }'}
    res = compile_to_ir_json(src)
    assert res.errors and "percent" in res.errors[0].message


def test_target_pct_number_literal_is_accepted():
    src = BASE % {"seed": 'seed = percentile { from = "env"; window = 60 s; target_pct = 40 }'}
    obj = compile_to_ir_json(src).ir_json
    assert obj["controls"]["thr_uv"]["seed"]["target_pct"]["node"] == "number"
```

Also add the **end-to-end emitter assertions** here — they were deferred from Task 4 because they need the resolver, which this task completes. Append to `tests/test_ir_json_seed.py`:

```python
from refrain.compile_json import compile_to_ir_json
from tests._seed_fixtures import SEEDING, NON_SEEDING  # verified fixtures (Task 4)


def test_seeding_protocol_emits_seed_and_v03():
    res = compile_to_ir_json(SEEDING)
    assert not res.errors, res.errors
    obj = res.ir_json
    assert obj["refrain_ir_version"] == "0.3"
    seed = obj["controls"]["thr_uv"]["seed"]
    assert seed["statistic"] == "percentile"
    assert seed["from"] == "derive/env"
    assert seed["window_samples"] == int(round(60 * 256))  # baked at 256 Hz
    assert seed["target_pct"]["node"] == "control_ref"
    assert seed["target_pct"]["target"] == "control/reward_pct"


def test_non_seeding_control_omits_seed_and_keeps_low_version():
    obj = compile_to_ir_json(NON_SEEDING).ir_json
    assert "seed" not in obj["controls"]["thr_uv"]
    assert obj["refrain_ir_version"] == "0.1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py -k "good_seed or unknown_from or target_pct" tests/test_ir_json_seed.py -k "seeding_protocol or non_seeding" -v`
Expected: FAIL — the post-pass does not exist, so `thr_uv` has no `seed` key, unknown `from` is not caught, and the emitted protocol has no `seed`/`0.3`.

- [ ] **Step 3: Wire the post-pass into `resolve`**

In `resolve` (`resolver.py`), after `self._validate_staging(session_ir)` (line 217) and before `meta_ir = self._resolve_meta()` (line 218):

```python
        self._resolve_control_seeds(requires_ir, session_ir)
```

- [ ] **Step 4: Implement `_resolve_control_seeds` and `_validate_seed_target_pct`**

```python
    def _resolve_control_seeds(self, requires_ir: IRRequires, session_ir: IRSession) -> None:
        if not self._pending_seeds:
            return
        rate = float(requires_ir.sample_rate_chosen_hz)
        for name, pend in self._pending_seeds.items():
            # 1. `from` must name a real derive.
            derive_canon = f"derive/{pend.from_raw}"
            if derive_canon not in self.derives:
                raise ResolveError(
                    f"control {name!r}.seed.from={pend.from_raw!r} is not a "
                    "declared derive",
                    loc=pend.loc,
                )
            # 2. target_pct is a number or a `percent` control ref (resolved now,
            #    when every control exists — order-independent).
            target_pct = self._resolve_value_expr(pend.target_pct_ast)
            self._validate_seed_target_pct(name, target_pct)
            # 3. Bake the window at the compile (chosen) rate.
            window_samples = max(1, int(round(pend.window_ms / 1000.0 * rate)))
            # (Task 7 inserts the warmup-fits check here.)
            # (Task 8 inserts dead-seed elimination here.)
            seed = IRControlSeed(
                statistic=pend.statistic,
                from_entity=derive_canon,
                window_samples=window_samples,
                target_pct=target_pct,
                loc=pend.loc,
            )
            self.controls[name] = replace(self.controls[name], seed=seed)

    def _validate_seed_target_pct(self, name: str, target_pct: IRExpr) -> None:
        if isinstance(target_pct, IRNumberLit):
            return
        if isinstance(target_pct, IRControlRef):
            ref_name = target_pct.target.removeprefix("control/")
            ref = self.controls.get(ref_name)
            if ref is not None and ref.type_kind == "percent":
                return
            got = ref.type_kind if ref is not None else "unknown"
            raise ResolveError(
                f"control {name!r}.seed.target_pct must bind a `percent` control "
                f"(got {got!r})",
                loc=target_pct.loc,
            )
        raise ResolveError(
            f"control {name!r}.seed.target_pct must be a number or a percent control",
            loc=getattr(target_pct, "loc", None),
        )
```

Add `IRControlSeed`, `IRRequires`, `IRSession`, `IRExpr` to the `from .ir import (...)` block if not already present (grep to confirm; `IRControlRef`, `IRNumberLit` are already imported per `resolver.py:53-54`).

- [ ] **Step 5: Run tests (including the deferred end-to-end emitter tests)**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py tests/test_ir_json_seed.py -v`
Expected: PASS — including `test_seeding_protocol_emits_seed_and_v03` and `test_non_seeding_control_omits_seed_and_keeps_low_version` (added in this task's Step 1), which now go green.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolve_seed.py
git commit -m "feat(resolve): resolve control seeds — validate from/target_pct, bake window"
```

---

## Task 7: Warmup-fits `ResolveError`

Phase durations are numeric literals (`resolver.py:1760`), so the compiler always knows warmup's length. A `window` longer than a timed, output-muted phase 0 can never fill — refuse at compile.

**Files:**
- Modify: `src/refrain/resolver.py` (`_resolve_control_seeds` loop from Task 6)
- Test: `tests/test_resolve_seed.py` (extend)

**Interfaces:**
- Consumes: `session_ir.phases[0]` (`IRPhase.duration_ms`, `.output_muted`, `.mode`, `.name` — `ir.py:289`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_resolve_seed.py`:

```python
_SEED = 'seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }'


def test_window_longer_than_warmup_is_a_resolve_error():
    # shrink the 90 s warmup below the 60 s window (replace before %-substitution)
    tmpl = BASE.replace('duration = 90 s; output_muted = true',
                        'duration = 30 s; output_muted = true')
    res = compile_to_ir_json(tmpl % {"seed": _SEED})
    assert res.errors and "warmup" in res.errors[0].message.lower()


def test_window_equal_to_warmup_is_allowed():
    tmpl = BASE.replace('duration = 90 s; output_muted = true',
                        'duration = 60 s; output_muted = true')
    assert not compile_to_ir_json(tmpl % {"seed": _SEED}).errors
```

- [ ] **Step 2: Run to verify the first fails**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py -k window_longer -v`
Expected: FAIL — a 60 s window against a 30 s warmup currently compiles.

- [ ] **Step 3: Add the check**

In `_resolve_control_seeds`, at the `# (Task 7 ...)` marker (right after `window_samples` is computed):

```python
            phases = session_ir.phases
            first = phases[0] if phases else None
            if first is not None and first.output_muted and first.mode != "open":
                warmup_samples = int(round(first.duration_ms / 1000.0 * rate))
                if window_samples > warmup_samples:
                    raise ResolveError(
                        f"control {name!r}.seed.window "
                        f"({pend.window_ms / 1000:.1f}s) exceeds the warmup phase "
                        f"{first.name!r} ({first.duration_ms / 1000:.1f}s); the "
                        "buffer can never fill",
                        loc=pend.loc,
                    )
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py -k window -v`
Expected: PASS — 60 s > 30 s refused; 60 s == 60 s allowed.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolve_seed.py
git commit -m "feat(resolve): refuse a seed window longer than the warmup phase"
```

---

## Task 8: Dead-seed elimination

Control declarations survive mode folding even when unreferenced (`ir_json.py:414` region / the emitter includes all non-placement/mode controls). Without this, an *adaptive* artifact (where `thr_uv` folded away) carries a live seed for an orphaned control — and under fail-closed a noisy warmup would mute an adaptive session that never wanted a baseline. Drop any seed whose control has no surviving `control_ref` in the resolved IR.

**Files:**
- Modify: `src/refrain/resolver.py` (`_resolve_control_seeds`); add `_referenced_control_targets`
- Test: `tests/test_resolve_seed.py` (extend)

**Interfaces:**
- Consumes: `self.derives`, `self.thresholds`, `self.inhibits`, `self.reward_ir`, `self._reward_bundles`, `self.output`; `IRControlRef`, `IRCall`, `IRBinaryOp`, `IRConditional`, `IRArray`, `IRTuple`, `IRBlockExpr` (mirror the traversal in `_instantiate_expr`, `eval_.py:692-705`).
- Produces: seeds present only for controls actually referenced by the pipeline.

- [ ] **Step 1: Write the failing test**

A `mode` control that folds `thr_uv` out of the adaptive branch must yield zero seeds in the adaptive artifact. Append to `tests/test_resolve_seed.py`:

`MODE_SRC` (verified fixtures, Task 4) uses a `threshold_style = mode { choices = ["adaptive", "baseline"] }` control and a ternary threshold `type = threshold_style == "baseline" ? absolute(value: thr_uv) : percentile(target_pct: reward_pct, window: 2 min)`. Binding `adaptive` deletes the `absolute(value: thr_uv)` branch at AST level, so `thr_uv` has no surviving `control_ref`. Append to `tests/test_resolve_seed.py`:

```python
from tests._seed_fixtures import MODE_SRC  # verified ternary mode-conditional


def test_seed_dropped_when_control_folded_out():
    # adaptive -> percentile branch survives, absolute(thr_uv) is deleted, so
    # thr_uv is unreferenced. Its seed must be dropped from the resolved IR.
    obj = compile_to_ir_json(MODE_SRC, bindings={"threshold_style": "adaptive"}).ir_json
    assert "seed" not in obj["controls"].get("thr_uv", {})


def test_seed_kept_when_control_referenced():
    obj = compile_to_ir_json(MODE_SRC, bindings={"threshold_style": "baseline"}).ir_json
    assert "seed" in obj["controls"]["thr_uv"]
```

Confirmed against the live compiler: `adaptive` → threshold `env_t` callee `percentile` (thr_uv folded out); `baseline` → callee `absolute` (thr_uv referenced).

- [ ] **Step 2: Run to verify the first fails**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py -k folded -v`
Expected: FAIL — the seed survives on the orphaned `thr_uv`.

- [ ] **Step 3: Add the reference scan and the elimination**

Add the traversal helper:

```python
    def _referenced_control_targets(self) -> set[str]:
        """Canonical targets of every control_ref surviving in the resolved
        pipeline (derives/thresholds/inhibits/reward/output/bundles). Mirrors
        the expression walk in `_instantiate_expr`. Seed `target_pct` refs are
        NOT walked (they live inside control blocks), so a seed can't keep its
        own control alive."""
        found: set[str] = set()

        def walk(expr) -> None:
            if expr is None:
                return
            if isinstance(expr, IRControlRef):
                found.add(expr.target)
            elif isinstance(expr, IRCall):
                for a in expr.args:
                    walk(a.value)
            elif isinstance(expr, IRBinaryOp):
                walk(expr.left); walk(expr.right)
            elif isinstance(expr, IRConditional):
                walk(expr.cond); walk(expr.then_branch); walk(expr.else_branch)
            elif isinstance(expr, (IRArray, IRTuple)):
                for e in expr.elements:
                    walk(e)
            elif isinstance(expr, IRBlockExpr):
                for e in expr.fields.values():
                    walk(e)

        for d in self.derives.values():
            walk(d.expression)
        for t in self.thresholds.values():
            walk(t.threshold_call)
        for ih in self.inhibits.values():
            walk(ih.metric); walk(ih.threshold)
        if self.reward_ir is not None:
            walk(self.reward_ir.continuous); walk(self.reward_ir.event)
            for c in self.reward_ir.components:
                walk(c.signal); walk(c.weight)
        for rb in self._reward_bundles.values():
            walk(rb.continuous); walk(rb.event)
        for expr in self.output.values():
            walk(expr)
        return found
```

In `_resolve_control_seeds`, compute the set once before the loop and skip dead seeds at the `# (Task 8 ...)` marker:

```python
        referenced = self._referenced_control_targets()
        ...
            if f"control/{name}" not in referenced:
                continue   # dead seed: control folded out — do not attach
```

Confirm `IRCall`, `IRBinaryOp`, `IRConditional`, `IRArray`, `IRTuple`, `IRBlockExpr` are imported in `resolver.py` (grep the `from .ir import` block; add any missing). Confirm `IRThreshold.threshold_call` / `IRInhibit.metric`/`.threshold` / `IRReward.components[i].signal`/`.weight` attribute names against `ir.py` and fix if they differ.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_resolve_seed.py -v`
Expected: PASS — seed dropped for `adaptive`, kept for `baseline`, and all prior seed-resolve tests still green.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolve_seed.py
git commit -m "feat(resolve): drop seeds whose control folds out of the resolved IR"
```

---

## Task 9: `PercentileImpl.ingest` + `_apply_control` refactor + latch construction (Python)

Build the latch machinery: an append-only ingest on the reused percentile buffer, a control-write path that does **not** self-disarm, and one `_SeedLatch` per seeded control.

**Files:**
- Modify: `src/refrain/primitive_impls.py:395-476` (`PercentileImpl` — add `ingest`)
- Modify: `src/refrain/eval_.py:1346-1370` (`set_control` — extract `_apply_control`), evaluator `__init__` (build latches), add `_SeedLatch`
- Test: `tests/test_eval_seed.py` (create)

**Interfaces:**
- Consumes: `impls.PercentileImpl(target_pct, window_ms, sample_rate_hz)` (`primitive_impls.py:407`); `PercentileImpl.export_state()` → `{"value","target_pct","n_eff"}` (`:447`); `PercentileImpl.update_control(target, value)` (`:472`); `self._controls`, `self._control_deps` (`eval_.py:345`); `IRControlRef`, `IRNumberLit`.
- Produces: `PercentileImpl.ingest(x)`; `self._apply_control(name, value)`; `self._seed_latches: dict[str, _SeedLatch]`; `self._seed_failed_mute: bool`; `_SeedLatch` fields `control_name, control_target, from_entity, target_pct, window_samples, buffer, armed, fired, status, value, n_samples, at_time_s`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_seed.py`:

```python
import math
import numpy as np
from refrain.primitive_impls import PercentileImpl


def test_ingest_appends_without_computing_and_skips_nonfinite():
    p = PercentileImpl(target_pct=70.0, window_ms=1000.0, sample_rate_hz=10.0)  # 10 samples
    p.ingest(np.array([1.0, 2.0, np.nan, 3.0, np.inf, 4.0]))
    st = p.export_state()
    assert st["n_eff"] == 4                    # nan/inf skipped, not counted
    assert st["value"] == np.percentile([1.0, 2.0, 3.0, 4.0], 70.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py::test_ingest_appends_without_computing_and_skips_nonfinite -v`
Expected: FAIL — `AttributeError: 'PercentileImpl' object has no attribute 'ingest'`.

- [ ] **Step 3: Add `PercentileImpl.ingest`**

In `primitive_impls.py` (after `set_ingesting`, before `step`), add — reusing the existing `deque` buffer and `_seen` counter, no new storage:

```python
    def ingest(self, x: np.ndarray) -> None:
        """Append finite samples to the window WITHOUT computing a percentile
        (seed-latch warmup ingest). Non-finite samples are skipped — never
        appended, never counted — so both engines share one NaN-skip rule and
        the full-window check (`n_eff >= window_samples`) is exact."""
        if not self._ingesting:
            return
        for v in x:
            fv = float(v)
            if math.isfinite(fv):
                self._buffer.append(fv)
                self._seen += 1
```

Add `import math` at the top of `primitive_impls.py` if absent (grep first).

- [ ] **Step 4: Extract `_apply_control` and add the disarm hook to `set_control`**

Replace the body of `set_control` (`eval_.py:1346-1370`, Python branch after the `_rust` guard) so the forward logic lives in `_apply_control`:

```python
    def set_control(self, name: str, value: float) -> None:
        """... (existing docstring) ..."""
        if self._rust is not None:
            self._rust.set_control(name, float(value))
            return
        # A host write to a seeded control that has not fired yet is a deliberate
        # clinical judgement — disarm the seed and run normally (§2.6). Disarm,
        # NOT fail: the clinician just took responsibility for the value.
        latch = self._seed_latches.get(f"control/{name}")
        if latch is not None and latch.armed and not latch.fired:
            latch.armed = False
            latch.status = "disarmed_by_host"
        self._apply_control(name, value)

    def _apply_control(self, name: str, value: float) -> None:
        """Forward a control value to its dependent impls WITHOUT the disarm
        hook. Used by both `set_control` and the seed latch's fire path (the
        seed must never disarm itself)."""
        target = f"control/{name}"
        if target not in self._controls:
            raise KeyError(f"no control named {name!r}")
        self._controls[target] = float(value)
        for impl in self._control_deps.get(target, []):
            updater = getattr(impl, "update_control", None)
            if updater is not None:
                updater(target, float(value))
```

- [ ] **Step 5: Add `_SeedLatch` and build latches**

Add the latch class at module scope in `eval_.py`:

```python
class _SeedLatch:
    """Per-seeded-control polled latch (§2.5). Ingests the `from` derive during
    warmup; fires once at the warmup→run edge."""

    def __init__(self, *, control_name, control_target, seed, buffer):
        self.control_name = control_name        # bare, for _apply_control + report key
        self.control_target = control_target     # "control/<name>"
        self.from_entity = seed.from_entity       # "derive/<name>"
        self.target_pct = seed.target_pct          # IRExpr (control_ref or number)
        self.window_samples = seed.window_samples
        self.buffer = buffer                        # impls.PercentileImpl (reused storage)
        self.armed = True
        self.fired = False
        self.status = "pending"
        self.value = None
        self.n_samples = 0
        self.at_time_s = None
```

Add a builder and call it from `__init__` **after** `_build_pipeline()` populates `_control_deps` and `self._controls` exists:

```python
    def _build_seed_latches(self) -> None:
        self._seed_latches: dict[str, _SeedLatch] = {}
        self._seed_failed_mute = False
        for name, ctrl in self.ir.controls.items():
            if ctrl.seed is None:
                continue
            init_pct = self._seed_target_pct_value(ctrl.seed.target_pct)
            # window_ms round-trips to the same window_samples the resolver baked
            # (window_samples = round(window_ms/1000*rate)), so the buffer cap
            # matches exactly.
            window_ms = ctrl.seed.window_samples * 1000.0 / self.sample_rate_hz
            buf = impls.PercentileImpl(
                target_pct=init_pct, window_ms=window_ms, sample_rate_hz=self.sample_rate_hz)
            self._seed_latches[ctrl.canonical_name] = _SeedLatch(
                control_name=name, control_target=ctrl.canonical_name, seed=ctrl.seed, buffer=buf)

    def _seed_target_pct_value(self, target_pct) -> float:
        if isinstance(target_pct, IRControlRef):
            return float(self._controls.get(target_pct.target, 0.0))
        if isinstance(target_pct, IRNumberLit):
            return float(target_pct.value)
        raise TypeError("seed.target_pct must be a control_ref or number")
```

Confirm `IRControlRef`/`IRNumberLit` are imported in `eval_.py` (grep; add to the `from .ir import` block if missing). Add `self._build_seed_latches()` to `__init__` after the pipeline build — grep `def __init__` in `eval_.py`, find where `_build_pipeline()`/`_control_deps` are set up, and call it there (guard the Rust-backed branch: only build latches on the Python path, since the Rust evaluator owns its own latches).

- [ ] **Step 6: Run tests**

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py -v && PYTHONPATH=src pytest tests/test_eval_control_refs.py -v`
Expected: PASS — `ingest` test passes; the existing control-ref/`set_control` tests still pass (the refactor is behaviour-preserving).

- [ ] **Step 7: Commit**

```bash
git add src/refrain/primitive_impls.py src/refrain/eval_.py tests/test_eval_seed.py
git commit -m "feat(eval): seed-latch scaffolding — ingest, _apply_control, latch build"
```

---

## Task 10: Seed step in `_process_chunk` — warmup ingest, fire at the run edge (Python)

**Files:**
- Modify: `src/refrain/eval_.py:898-931` (`_process_chunk` — insert the seed step after derives, before thresholds); add `_step_seeds`
- Test: `tests/test_eval_seed.py` (extend)

**Interfaces:**
- Consumes: `stream_values[from_entity]` (derives computed at `eval_.py:917-920`); `self._state`; `self._apply_control` (Task 9); `PercentileImpl.export_state()`.
- Produces: `self._step_seeds(stream_values, t0_s)`; sets `latch.fired/value/status/n_samples/at_time_s` and `self._seed_failed_mute`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_seed.py`. Construct via the **real** API — `Evaluator.live(resolve(parse(src)), …, backend="python")` (there is no `from_ir_json`; the constructor takes a resolved `IRProtocol`, not the JSON dict). `backend="python"` pins the reference engine regardless of whether the Rust wheel is built.

```python
import numpy as np
from refrain.parser import parse
from refrain.resolver import resolve
from refrain.eval_ import Evaluator
from tests._seed_fixtures import SEED_PROTO   # verified block-syntax fixture (Task 4)


def _build(src=SEED_PROTO, *, bindings=None):
    ir = resolve(parse(src), bindings=bindings) if bindings else resolve(parse(src))
    return Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz",), backend="python")


def _run(ev, value, n_chunks, chunk=256):
    for _ in range(n_chunks):
        ev.step_chunk(np.full((chunk, 1), value, dtype=np.float64))


def test_seed_writes_control_at_run_edge_and_holds():
    ev = _build()
    ev.start(skip_warmup=False)
    # 3 s warmup at 256 Hz = 768 samples = 3 chunks (ingest); the 4th chunk is the
    # first `run` chunk, where the seed fires before any threshold steps.
    _run(ev, value=5.0, n_chunks=4)
    report = ev.seed_report()
    assert report["thr_uv"]["status"] == "seeded"
    # The seed writes percentile(env); for a constant input env is constant, so the
    # written value equals env's last tap — montage arithmetic is irrelevant here.
    assert abs(report["thr_uv"]["value"] - ev.last_taps()["derive/env"]) < 1e-9
```

`seed_report()` arrives in Task 12; if executing strictly in order, assert against `ev._seed_latches["control/thr_uv"].value` here and switch to `seed_report()` after Task 12. Confirm the env tap key is `derive/env` (it is — derives tap as `derive/<name>`).

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py -k run_edge -v`
Expected: FAIL — nothing ingests or fires; the latch value stays `None`.

- [ ] **Step 3: Insert the seed step in `_process_chunk`**

In `_process_chunk`, immediately after the derives loop (`eval_.py:920`) and before the `freeze_ingest` computation (`:927`):

```python
        # Baseline seeding (§2.5): ingest the `from` derive during warmup; at the
        # first `run` chunk compute the percentile and write the control BEFORE
        # any threshold steps, so the seeded value is live with no one-chunk lag.
        if self._seed_latches:
            self._step_seeds(stream_values, t0_s)
            suppress_output = suppress_output or self._seed_failed_mute
```

Add the method:

```python
    def _step_seeds(self, stream_values: dict, t0_s: float) -> None:
        for latch in self._seed_latches.values():
            if not latch.armed:
                continue
            src = stream_values.get(latch.from_entity)
            if self._state == "warmup":
                if src is not None:
                    latch.buffer.ingest(src)        # append-only, skips non-finite
                continue
            # state == "run", armed, not yet fired -> fire exactly once.
            if latch.fired:
                continue
            latch.fired = True
            pct = self._seed_target_pct_value(latch.target_pct)  # tracks a live reward_pct
            latch.buffer.update_control(latch.control_target, pct)
            st = latch.buffer.export_state()
            n_eff = int(st["n_eff"])
            latch.n_samples = n_eff
            latch.at_time_s = t0_s
            if n_eff < latch.window_samples:
                # The measurement did not complete (short/skipped warmup, early
                # advance) -> fail closed for the rest of the session (§2.6).
                latch.status = "insufficient_samples"
                self._seed_failed_mute = True
                continue
            value = float(st["value"])
            latch.value = value
            latch.status = "seeded"
            self._apply_control(latch.control_name, value)   # NOT set_control -> no self-disarm
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py -k "run_edge or ingest" -v`
Expected: PASS — the control is written to 5.0 at the run edge and holds (`absolute(thr_uv)` now emits against the seeded threshold).

- [ ] **Step 5: Add a "seed once across staged blocks" test**

Append a test using a staged protocol (two run phases with different blocks over one warmup) asserting the latch fires exactly once (`fired` true after the first run chunk; `value` unchanged across the phase boundary). Reuse an existing staged fixture shape from `tests/conftest_staged.py` if available.

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/eval_.py tests/test_eval_seed.py
git commit -m "feat(eval): seed latch ingests during warmup and fires at the run edge"
```

---

## Task 11: Fail-closed matrix — disarm, skip_warmup, NaN (Python)

Task 10 already wired `insufficient_samples → fail closed` and (via Task 9) `disarm_by_host`. This task proves the whole §2.6 matrix and confirms NaN-skip parity.

**Files:**
- Test: `tests/test_eval_seed.py` (extend); no new production code expected — if a test surfaces a gap, fix it here.

**Interfaces:**
- Consumes: `Evaluator.start(skip_warmup=...)`, `set_control`, `seed_report()`/latch fields.

- [ ] **Step 1: Write the matrix tests**

Append to `tests/test_eval_seed.py`:

```python
def test_skip_warmup_fails_closed():
    ev = _build()
    ev.start(skip_warmup=True)          # warmup skipped -> measurement never happens
    events = ev.step_chunk(np.full((256, 1), 5.0))
    latch = ev._seed_latches["control/thr_uv"]
    assert latch.status == "insufficient_samples"
    assert ev._seed_failed_mute is True
    assert events == []                  # output suppressed for the session


def test_host_write_during_warmup_disarms_not_fails():
    ev = _build()
    ev.start(skip_warmup=False)
    ev.step_chunk(np.full((256, 1), 5.0))   # one warmup chunk
    ev.set_control("thr_uv", 1.5)            # clinician takes over
    _run(ev, value=5.0, n_chunks=4)          # cross into run
    latch = ev._seed_latches["control/thr_uv"]
    assert latch.status == "disarmed_by_host"
    assert latch.fired is False
    assert ev._seed_failed_mute is False     # disarmed != failed -> runs normally


def test_nonfinite_samples_are_skipped_not_counted():
    ev = _build()
    ev.start(skip_warmup=False)
    good = np.full((256, 1), 5.0); good[:10] = np.nan   # NaNs must not poison/crash
    ev.step_chunk(good)
    _run(ev, value=5.0, n_chunks=4)
    assert ev._seed_latches["control/thr_uv"].status == "seeded"
```

- [ ] **Step 2: Run and fix any gaps**

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py -v`
Expected: PASS. If `test_skip_warmup_fails_closed` sees a non-empty `events`, verify the `suppress_output = suppress_output or self._seed_failed_mute` line (Task 10) sits before the output-emission early-return (`eval_.py:1081`, `:1130`); the fire runs at the top of the same chunk, so the mute applies from that chunk on.

- [ ] **Step 3: Commit**

```bash
git add tests/test_eval_seed.py src/refrain/eval_.py
git commit -m "test(eval): cover the §2.6 fail-closed / disarm / NaN matrix"
```

---

## Task 12: `seed_report()` accessor (Python)

**Files:**
- Modify: `src/refrain/eval_.py` (add `seed_report`)
- Test: `tests/test_eval_seed.py` (extend)

**Interfaces:**
- Produces: `Evaluator.seed_report() -> dict[str, dict]` keyed by bare control name, values `{status, value, source, target_pct, n_samples, window_s, at_time_s}` (§2.7). Delegates to `self._rust.seed_report()` on the Rust path.

- [ ] **Step 1: Write the failing test**

```python
def test_seed_report_shape():
    ev = _build()
    ev.start(skip_warmup=False)
    _run(ev, value=5.0, n_chunks=4)
    r = ev.seed_report()["thr_uv"]
    assert r["status"] == "seeded"
    assert r["source"] == "derive/env"
    assert r["target_pct"] == 70.0
    assert r["window_s"] == 2.0
    assert r["n_samples"] >= 512          # 2 s at 256 Hz
    assert r["at_time_s"] is not None


def test_seed_report_empty_for_non_seeding_protocol():
    from tests._seed_fixtures import NON_SEEDING   # verified fixture (Task 4)
    ev = _build(NON_SEEDING)
    ev.start(skip_warmup=True)
    assert ev.seed_report() == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py -k seed_report -v`
Expected: FAIL — `AttributeError: 'Evaluator' object has no attribute 'seed_report'`.

- [ ] **Step 3: Implement `seed_report`**

```python
    def seed_report(self) -> dict:
        """Per-control baseline-seed outcome (§2.7), keyed by bare control name.
        Empty for a protocol with no seeds. Deliberately NOT a tap — keeps the
        strict tap key-set parity test untouched (mirrors export_state, v0.8.0)."""
        if self._rust is not None:
            return self._rust.seed_report()
        out: dict[str, dict] = {}
        for latch in self._seed_latches.values():
            out[latch.control_name] = {
                "status": latch.status,
                "value": latch.value,
                "source": latch.from_entity,
                "target_pct": self._seed_target_pct_value(latch.target_pct),
                "n_samples": latch.n_samples,
                "window_s": latch.window_samples / self.sample_rate_hz,
                "at_time_s": latch.at_time_s,
            }
        return out
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_eval_seed.py -v`
Expected: PASS. Update the Task 10/11 tests that read `ev._seed_latches[...]` to use `seed_report()` where cleaner.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/eval_.py tests/test_eval_seed.py
git commit -m "feat(eval): seed_report() accessor (not a tap)"
```

---

## Task 13: Rust IR `ControlSeed` + `Percentile::ingest`/`value_at`/`n_eff` + `apply_control_value`

Mirror the Python data + storage additions on the Rust side, plus the non-disarming write path.

**Files:**
- Modify: `refrain-core/src/ir.rs:52-55` (`ControlDecl`); add `ControlSeed`
- Modify: `refrain-core/src/dsp.rs:241-308` (`Percentile`); add `ingest`, `value_at`, `n_eff`
- Modify: `refrain-core/src/eval.rs:1051-1062` (`set_control` — extract `apply_control_value`)
- Test: `refrain-core/tests/ir_deser.rs`, `refrain-core/tests/set_control.rs`

**Interfaces:**
- Consumes: `Expr` (`ir.rs:209`); `VecDeque` buffer + `percentile_linear` (`dsp.rs:231`, `:527`).
- Produces: `ControlSeed { statistic, from, window_samples, target_pct: Expr }`; `ControlDecl.seed: Option<ControlSeed>`; `Percentile::ingest(&[f64])`, `::value_at(f64) -> f64`, `::n_eff() -> u64`; `Evaluator::apply_control_value(&str, f64) -> Result<(), String>`.

- [ ] **Step 1: Write the failing tests**

In `refrain-core/tests/ir_deser.rs`:

```rust
#[test]
fn deserializes_a_control_seed() {
    let json = r#"{
      "sample_rate_hz":256.0,"channels":["Cz"],"inputs":{},"derives":{},
      "controls":{"thr_uv":{"canonical_name":"control/thr_uv","seed":{
        "statistic":"percentile","from":"derive/env","window_samples":15360,
        "target_pct":{"node":"number","value":70.0}}}}
    }"#;
    let p: refrain_core::ir::Protocol = serde_json::from_str(json).unwrap();
    let seed = p.controls["thr_uv"].seed.as_ref().unwrap();
    assert_eq!(seed.window_samples, 15360);
    assert_eq!(seed.from, "derive/env");
}

#[test]
fn control_without_seed_still_deserializes() {
    let json = r#"{"sample_rate_hz":256.0,"channels":["Cz"],"inputs":{},"derives":{},
      "controls":{"reward_pct":{"canonical_name":"control/reward_pct"}}}"#;
    let p: refrain_core::ir::Protocol = serde_json::from_str(json).unwrap();
    assert!(p.controls["reward_pct"].seed.is_none());
}
```

Add a `dsp.rs` unit test (or in a `#[cfg(test)]` module there):

```rust
#[test]
fn ingest_skips_nonfinite_and_value_at_matches() {
    let mut p = Percentile::new(70.0, 10);
    p.ingest(&[1.0, 2.0, f64::NAN, 3.0, f64::INFINITY, 4.0]);
    assert_eq!(p.n_eff(), 4);
    // linear percentile of [1,2,3,4] at 70 == 3.1 (NumPy 'linear')
    assert!((p.value_at(70.0) - 3.1).abs() < 1e-9);
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd refrain-core && cargo test --test ir_deser deserializes_a_control_seed && cargo test ingest_skips_nonfinite`
Expected: FAIL — `ControlDecl` has no `seed`; `Percentile` has no `ingest`/`value_at`/`n_eff`.

- [ ] **Step 3: Add `ControlSeed` and the field**

In `refrain-core/src/ir.rs`, extend `ControlDecl` (line 52) and add the struct:

```rust
#[derive(Debug, Deserialize)]
pub struct ControlDecl {
    pub canonical_name: String,
    #[serde(default)]
    pub seed: Option<ControlSeed>,
}

/// A control's baseline-seed rule (`_emit_seed`). `target_pct` reuses the closed
/// `Expr` union (a `number` or a `control_ref`) — no new node kind.
#[derive(Debug, Deserialize, Clone)]
pub struct ControlSeed {
    pub statistic: String,
    pub from: String,
    pub window_samples: usize,
    pub target_pct: Expr,
}
```

- [ ] **Step 4: Add `ingest`/`value_at`/`n_eff` to `Percentile`**

In `refrain-core/src/dsp.rs`, in `impl Percentile` (after `seed`, `dsp.rs:279`):

```rust
    /// Append finite samples to the window WITHOUT computing a percentile
    /// (seed-latch warmup ingest). Non-finite samples are skipped — never
    /// pushed, never counted — so Rust matches Python's NaN-skip instead of
    /// panicking in `percentile_linear` (§2.6).
    pub fn ingest(&mut self, x: &[f64]) {
        if !self.ingesting {
            return;
        }
        for &v in x {
            if v.is_finite() {
                if self.buf.len() == self.cap {
                    self.buf.pop_front();
                }
                self.buf.push_back(v);
                self.seen += 1;
            }
        }
    }

    /// Percentile of the current window at an explicit target (seed fire path,
    /// where the target tracks a live control read at the warmup→run edge).
    pub fn value_at(&self, pct: f64) -> f64 {
        if self.buf.is_empty() { 0.0 } else { percentile_linear(&self.buf, pct) }
    }

    /// Effective sample count (capped at the window), for the full-window check.
    pub fn n_eff(&self) -> u64 {
        self.seen.min(self.cap as u64)
    }
```

- [ ] **Step 5: Extract `apply_control_value` and add the disarm hook**

In `refrain-core/src/eval.rs`, replace `set_control` (`:1051`):

```rust
    pub fn set_control(&mut self, name: &str, value: f64) -> Result<(), String> {
        let target = format!("control/{name}");
        // A host write to a not-yet-fired seeded control disarms the seed (§2.6).
        if let Some(latch) = self.seed_latches.iter_mut().find(|l| l.control_target == target) {
            if latch.armed && !latch.fired {
                latch.armed = false;
                latch.status = SeedStatus::DisarmedByHost;
            }
        }
        self.apply_control_value(name, value)
    }

    /// Forward a control value to its bound stages WITHOUT the disarm hook. Used
    /// by both `set_control` and the seed fire path.
    fn apply_control_value(&mut self, name: &str, value: f64) -> Result<(), String> {
        let target = format!("control/{name}");
        if !self.declared_controls.contains(&target) {
            return Err(format!("no control named {name:?}"));
        }
        if let Some(bindings) = self.controls.get(&target) {
            for ctrl in bindings {
                ctrl.apply(value);
            }
        }
        Ok(())
    }
```

`self.seed_latches` and `SeedStatus` land in Task 14; if compiling this task standalone, add the empty `seed_latches: Vec<SeedLatch>` field + the `SeedStatus` enum stub now (Task 14 fills them in) so `eval.rs` builds. Simpler: implement Tasks 13 and 14 in one branch and split only the commits.

- [ ] **Step 6: Run tests**

Run: `cd refrain-core && cargo test --test ir_deser && cargo test ingest_skips_nonfinite`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add refrain-core/src/ir.rs refrain-core/src/dsp.rs refrain-core/src/eval.rs \
        refrain-core/tests/ir_deser.rs
git commit -m "feat(core): ControlSeed deser + Percentile ingest/value_at + apply_control_value"
```

---

## Task 14: Rust seed latch — build + step in `eval_chunk` (ingest/fire, NaN skip)

Mirror the Python latch: build one per seeded control in `Evaluator::new`; run it at the top of `eval_chunk` (after derives, before thresholds); fire at the first run chunk; fail closed on a short window.

**Files:**
- Modify: `refrain-core/src/eval.rs` — `Evaluator` struct + `new` (`:736-`, `:822`), `eval_chunk` (`:1200-1254`), `step_chunk_events` (`:1600`); add `SeedLatch`, `SeedStatus`, `step_seeds`, `eval_target_pct`
- Test: `refrain-core/tests/seed.rs` (create)

**Interfaces:**
- Consumes: `BuildCtx`, `build_node` (`eval.rs:1894`), `Percentile::{new,ingest,value_at,n_eff}`, `CNode::eval(&env, n)`, `Val::F`, `self.state`, `self.samples_pushed`, `apply_control_value` (Task 13).
- Produces: `Evaluator.seed_latches: Vec<SeedLatch>`, `Evaluator.seed_failed: bool`; `SeedLatch`; `enum SeedStatus`; `fn step_seeds(&mut self, env: &HashMap<String, Val>)`.

- [ ] **Step 1: Write the failing test**

Create `refrain-core/tests/seed.rs`:

```rust
use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

fn proto() -> Protocol {
    // Same shape as the Python SEED_PROTO: 3 s muted warmup, 2 s window, const 5.0.
    let s = std::fs::read_to_string("tests/fixtures/seed_smr_baseline.ir.json").unwrap();
    serde_json::from_str(&s).unwrap()
}

#[test]
fn fires_once_and_writes_the_measured_percentile() {
    let p = proto();
    let mut ev = Evaluator::new(&p, 256.0, &["Cz".into()]);
    ev.start(false);
    for _ in 0..4 {
        ev.step_chunk_events(&vec![vec![5.0_f64]; 256]);
    }
    let r = ev.seed_report();   // arrives in Task 15; assert on a latch getter until then
    let e = &r["thr_uv"];
    assert_eq!(e.status, "seeded");
    assert!((e.value.unwrap() - 5.0).abs() < 1e-9);
}
```

(This test depends on the fixture from Task 16 and `seed_report` from Task 15; if executing strictly in order, first assert via a temporary `pub fn seed_latch_value(&self, name: &str) -> Option<f64>` test hook, then delete it once `seed_report` lands.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd refrain-core && cargo test --test seed`
Expected: FAIL — no latch machinery / fixture yet.

- [ ] **Step 3: Add `SeedStatus`, `SeedLatch`, and the struct fields**

At module scope in `eval.rs`:

```rust
#[derive(Debug, Clone, Copy, PartialEq)]
enum SeedStatus { Pending, Seeded, InsufficientSamples, DisarmedByHost }

impl SeedStatus {
    fn as_str(self) -> &'static str {
        match self {
            SeedStatus::Pending => "pending",
            SeedStatus::Seeded => "seeded",
            SeedStatus::InsufficientSamples => "insufficient_samples",
            SeedStatus::DisarmedByHost => "disarmed_by_host",
        }
    }
}

/// Per-seeded-control polled latch (§2.5), mirror of Python `_SeedLatch`.
struct SeedLatch {
    control_name: String,     // bare, for apply_control_value + report key
    control_target: String,    // "control/<name>"
    from_entity: String,        // "derive/<name>"
    target_pct: CNode,           // number/control_ref value node (built via build_node)
    window_samples: u64,
    buffer: Percentile,           // reused window storage
    armed: bool,
    fired: bool,
    status: SeedStatus,
    value: Option<f64>,
    n_samples: u64,
    at_time_s: Option<f64>,
}
```

Add to `struct Evaluator` (near `:736`): `seed_latches: Vec<SeedLatch>,` and `seed_failed: bool,`. Initialise both in `Evaluator::new` (`:822`) — build the latches with the same `BuildCtx` used for the pipeline, so a `control_ref` `target_pct` registers a `Control` binding and tracks a live `reward_pct`:

```rust
        // (inside Evaluator::new, after the pipeline/controls are built)
        let mut seed_latches = Vec::new();
        for (bare, decl) in p.controls.iter() {
            if let Some(seed) = &decl.seed {
                let target_pct = build_node(&seed.target_pct, &mut ctx);
                // initial pct: eval the node against an empty env (control_ref ->
                // ConstCell reads its cell; number -> const).
                let init_pct = eval_target_pct(&target_pct, &HashMap::new());
                seed_latches.push(SeedLatch {
                    control_name: bare.clone(),
                    control_target: decl.canonical_name.clone(),
                    from_entity: seed.from.clone(),
                    target_pct,
                    window_samples: seed.window_samples as u64,
                    buffer: Percentile::new(init_pct, seed.window_samples),
                    armed: true,
                    fired: false,
                    status: SeedStatus::Pending,
                    value: None,
                    n_samples: 0,
                    at_time_s: None,
                });
            }
        }
        // ... set `seed_latches` and `seed_failed: false` on the returned Evaluator.
```

Add the free helper:

```rust
fn eval_target_pct(node: &CNode, env: &HashMap<String, Val>) -> f64 {
    node.eval(env, 1).into_f().first().copied().unwrap_or(0.0)
}
```

- [ ] **Step 4: Add `step_seeds` and call it in `eval_chunk`**

In `eval_chunk`, after the derives loop (`eval.rs:1247`) and before the thresholds loop (`:1248`):

```rust
        // Baseline seeding (§2.5): ingest `from` during warmup; fire at the first
        // run chunk BEFORE thresholds step so the seeded value is live.
        if !self.seed_latches.is_empty() {
            self.step_seeds(&env);
        }
        let mutes_output = mutes_output || self.seed_failed;   // fail-closed mute
```

Change `let mutes_output` at `eval.rs:1221` to `let mut mutes_output` (it is now reassigned). Add the method:

```rust
    fn step_seeds(&mut self, env: &HashMap<String, Val>) {
        let t0_s = self.samples_pushed as f64 / self.sample_rate_hz;
        let warmup = self.state == State::Warmup;
        let mut writes: Vec<(String, f64)> = Vec::new();
        let mut any_failed = false;
        for latch in self.seed_latches.iter_mut() {
            if !latch.armed {
                continue;
            }
            let src: &[f64] = match env.get(&latch.from_entity) {
                Some(Val::F(v)) => v.as_slice(),
                _ => &[],
            };
            if warmup {
                latch.buffer.ingest(src);      // append-only, skips non-finite
                continue;
            }
            if latch.fired {
                continue;
            }
            latch.fired = true;
            let pct = eval_target_pct(&latch.target_pct, env);   // tracks live reward_pct
            let n = latch.buffer.n_eff();
            latch.n_samples = n;
            latch.at_time_s = Some(t0_s);
            if n < latch.window_samples {
                latch.status = SeedStatus::InsufficientSamples;
                any_failed = true;             // set self.seed_failed after the loop
                continue;
            }
            let value = latch.buffer.value_at(pct);
            latch.value = Some(value);
            latch.status = SeedStatus::Seeded;
            writes.push((latch.control_name.clone(), value));
        }
        if any_failed {
            self.seed_failed = true;
        }
        for (name, value) in writes {
            let _ = self.apply_control_value(&name, value);   // NOT set_control -> no self-disarm
        }
    }
```

In `step_chunk_events` (`eval.rs:1600`), make the suppression pick up `seed_failed` after `eval_chunk`:

```rust
        let mut suppress_output = self.phase_mutes_output();
        let (env, muted, outs) = self.eval_chunk(chunk);
        suppress_output |= self.seed_failed;
```

- [ ] **Step 5: Run the parity-adjacent unit test + suite**

Run: `cd refrain-core && cargo test` (the `seed` test still needs Task 16's fixture; the rest of the suite must stay green after the struct/`eval_chunk` changes).
Expected: PASS for the existing suite; `seed` test passes after Task 16.

- [ ] **Step 6: Commit**

```bash
git add refrain-core/src/eval.rs refrain-core/tests/seed.rs
git commit -m "feat(core): seed latch — warmup ingest, fire at run edge, fail-closed mute"
```

---

## Task 15: Rust `seed_report()` + PyO3/uniffi plumbing + regenerated bindings

**Files:**
- Modify: `refrain-core/src/eval.rs` (add `seed_report` + `SeedReportEntry`), `refrain-core/src/python.rs:150-` (expose), `refrain-core/src/mobile.rs` (uniffi record + method)
- Modify: regenerated Swift/Kotlin binding files (whatever `.github/workflows/mobile.yml` drift-gates)
- Test: `refrain-core/tests/seed.rs` (extend); a Python round-trip via the Rust backend

**Interfaces:**
- Produces: `Evaluator::seed_report() -> BTreeMap<String, SeedReportEntry>`; `SeedReportEntry { status, value, source, target_pct, n_samples, window_s, at_time_s }`; `RustEvaluator.seed_report()` (PyDict, same shape as the Python `seed_report()`); a uniffi `seed_report()` returning a map of records.

- [ ] **Step 1: Write the failing test**

Extend `refrain-core/tests/seed.rs`:

```rust
#[test]
fn seed_report_is_keyed_by_control_name() {
    let mut ev = Evaluator::new(&proto(), 256.0, &["Cz".into()]);
    ev.start(false);
    for _ in 0..4 { ev.step_chunk_events(&vec![vec![5.0_f64]; 256]); }
    let r = ev.seed_report();
    assert_eq!(r["thr_uv"].source, "derive/env");
    assert_eq!(r["thr_uv"].target_pct, 70.0);
    assert!((r["thr_uv"].window_s - 2.0).abs() < 1e-9);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd refrain-core && cargo test --test seed seed_report`
Expected: FAIL — `Evaluator` has no `seed_report`.

- [ ] **Step 3: Implement `seed_report` on `Evaluator`**

```rust
pub struct SeedReportEntry {
    pub status: String,
    pub value: Option<f64>,
    pub source: String,
    pub target_pct: f64,
    pub n_samples: u64,
    pub window_s: f64,
    pub at_time_s: Option<f64>,
}

impl Evaluator {
    pub fn seed_report(&self) -> BTreeMap<String, SeedReportEntry> {
        let mut out = BTreeMap::new();
        for latch in &self.seed_latches {
            out.insert(latch.control_name.clone(), SeedReportEntry {
                status: latch.status.as_str().to_string(),
                value: latch.value,
                source: latch.from_entity.clone(),
                // control_ref -> ConstCell reads its live cell; number -> const.
                target_pct: eval_target_pct(&latch.target_pct, &HashMap::new()),
                n_samples: latch.n_samples,
                window_s: latch.window_samples as f64 / self.sample_rate_hz,
                at_time_s: latch.at_time_s,
            });
        }
        out
    }
}
```

- [ ] **Step 4: Expose on PyO3 (`python.rs`)**

Add to `#[pymethods] impl RustEvaluator` (after `set_control`, `python.rs:157`):

```rust
    /// `eval_.Evaluator.seed_report`: per-control baseline-seed outcome (§2.7),
    /// keyed by bare control name. Empty for a non-seeding protocol.
    fn seed_report<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let out = PyDict::new(py);
        for (name, e) in self.inner.seed_report() {
            let d = PyDict::new(py);
            d.set_item("status", e.status)?;
            d.set_item("value", e.value)?;             // None -> Python None
            d.set_item("source", e.source)?;
            d.set_item("target_pct", e.target_pct)?;
            d.set_item("n_samples", e.n_samples)?;
            d.set_item("window_s", e.window_s)?;
            d.set_item("at_time_s", e.at_time_s)?;
            out.set_item(name, d)?;
        }
        Ok(out)
    }
```

- [ ] **Step 5: Expose on uniffi (`mobile.rs`)**

Add a uniffi record and method mirroring the export-state pattern already in `mobile.rs`:

```rust
#[derive(uniffi::Record)]
pub struct SeedReport {
    pub status: String,
    pub value: Option<f64>,
    pub source: String,
    pub target_pct: f64,
    pub n_samples: u64,
    pub window_s: f64,
    pub at_time_s: Option<f64>,
}

// in the exported evaluator impl:
    pub fn seed_report(&self) -> HashMap<String, SeedReport> {
        self.inner.lock().unwrap().seed_report().into_iter().map(|(k, e)| {
            (k, SeedReport {
                status: e.status, value: e.value, source: e.source,
                target_pct: e.target_pct, n_samples: e.n_samples,
                window_s: e.window_s, at_time_s: e.at_time_s,
            })
        }).collect()
    }
```

Match the exact wrapper/lock idiom used by the neighbouring `mobile.rs` methods (grep `impl` / `self.inner` in `mobile.rs`).

- [ ] **Step 6: Regenerate the Swift/Kotlin bindings and build the wheel**

Run the binding-generation + wheel build the CI drift gate runs (`.github/workflows/mobile.yml`; per project memory, cargo lives in `~/.cargo/bin` and `maturin` is auto-provisioned by PEP517). Commit the regenerated Swift/Kotlin files so the drift gate is green. Then verify the Python↔Rust round-trip:

```bash
cd refrain-core && maturin develop        # build the wheel into the venv
cd .. && PYTHONPATH=src pytest tests/test_eval_seed.py tests/test_eval_rust_backend.py -v
```
Expected: PASS — `seed_report()` returns the same shape on the Rust backend as on the Python path.

- [ ] **Step 7: Commit**

```bash
git add refrain-core/src/eval.rs refrain-core/src/python.rs refrain-core/src/mobile.rs \
        refrain-core/tests/seed.rs <regenerated-binding-files>
git commit -m "feat(core): seed_report() on Evaluator + PyO3/uniffi + regenerated bindings"
```

---

## Task 16: `skip_warmup=False` conformance fixture + parity test @ 1e-9

The one genuinely novel piece of engineering (§5, §8 risk 1). Every golden fixture today is generated with `start(skip_warmup=True)` (`gen_fixtures.py:56`, `:114`; `docs/CONFORMANCE.md` §3). This feature exists *only* during warmup, so it needs a `skip_warmup=False` bundle — new corpus territory.

**Files:**
- Create: `bench/protocols/seed_smr_baseline.refrain`
- Modify: `refrain-core/tools/gen_fixtures.py` (a `skip_warmup=False` generation path + register the stem)
- Create: `refrain-core/tests/seed_parity.rs`
- Modify: `docs/CONFORMANCE.md` (document the seeding-bundle exception)

**Interfaces:**
- Consumes: the fixture bundle format (`docs/CONFORMANCE.md` §2); the `equivalence.rs` compare harness (`refrain-core/tests/equivalence.rs:32-`).
- Produces: `seed_smr_baseline.{ir,io}.json` generated with warmup; a Rust test asserting the seeded stream is bit-exact across backends.

- [ ] **Step 1: Write the fixture protocol**

Create `bench/protocols/seed_smr_baseline.refrain` — verified block syntax (same shape as `SEED_PROTO`). A `passthrough()` montage + `magnitude()` gives a constant `env` equal to the constant input, so the seeded percentile is exact by construction (per §5). `thr_uv` default 9.9 uV so the seed visibly moves it:

```refrain
protocol "seed_smr_baseline" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "SMR baseline seed" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "env" { from = "raw"; pipeline = [ magnitude() ] }
  threshold "thr" { signal = "env"; type = absolute(value: thr_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }
    thr_uv = voltage {
      default = 9.9 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 2 s; target_pct = reward_pct }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 3 s; output_muted = true },
    phase { name = "run";    duration = 5 s },
  ] }
}
```

- [ ] **Step 2: Add a `skip_warmup=False` generation path**

In `refrain-core/tools/gen_fixtures.py`, thread a `skip_warmup` flag through the reference-run helper (the functions calling `ev.start(skip_warmup=True)` at `:56`/`:114`) and through `generate(...)` (`:168`). Register `seed_smr_baseline` in the generation list (`:235-249`) with `skip_warmup=False`, and add it to the input-signal setup so the `io.json` carries a signal long enough to cross warmup (≥ 8 s at 256 Hz). Keep the input constant-shaped (a fixed level) so the seeded percentile is exact.

Write the failing test first:

- [ ] **Step 3: Write the failing parity test**

Create `refrain-core/tests/seed_parity.rs`:

```rust
//! Baseline-seeding parity: run the seeding fixture WITHOUT skip_warmup on the
//! Rust core and compare its output stream to the Python reference bundle. The
//! seeded value is bit-exact by construction (constant-fill percentile), so pin
//! at 1e-9 — the existing `# constant fill is exact` precedent.
mod common;   // reuse equivalence.rs helpers if factored out; else inline load_ir/load_io

#[test]
fn seed_stream_is_bit_exact_across_backends() {
    let ir = load_ir("seed_smr_baseline");
    let io = load_io("seed_smr_baseline");
    let mut ev = Evaluator::new(&ir, ir.sample_rate_hz, &channels(&ir));
    ev.start(false);   // NOT skip_warmup — the seed must run
    // drive the recorded input; collect the `output/fb` stream
    let got = run_streams(&mut ev, &io);
    let want = &io.reference_streams["output/fb"];
    for (g, w) in got.iter().zip(want) {
        assert!((g - w).abs() < 1e-9, "seed parity broke: {g} vs {w}");
    }
}
```

Model `load_ir`/`load_io`/`run_streams` on `equivalence.rs:23-35`; if that harness hardcodes `skip_warmup`/a warmup-skip in its compare, add a seeding-aware path rather than editing the shared `check`.

- [ ] **Step 4: Generate the bundle and run parity**

Run:
```bash
PYTHONPATH=src python refrain-core/tools/gen_fixtures.py
cd refrain-core && cargo test --test seed_parity && cargo test --test seed
```
Expected: PASS — `output/fb` is identical to 1e-9 across backends; the Task 14/15 `seed.rs` tests (which read this fixture) now pass.

- [ ] **Step 5: Document the exception**

In `docs/CONFORMANCE.md` §2/§3, add a short note: seeding fixtures are generated with `skip_warmup=False` (the seed runs only during warmup); a runtime self-validating a seeding bundle must call `start(skip_warmup=False)`, unlike the rest of the corpus.

- [ ] **Step 6: Commit**

```bash
git add bench/protocols/seed_smr_baseline.refrain refrain-core/tools/gen_fixtures.py \
        refrain-core/tests/seed_parity.rs refrain-core/tests/fixtures/seed_smr_baseline.* \
        docs/CONFORMANCE.md
git commit -m "test(conformance): skip_warmup=False seeding fixture + 1e-9 parity"
```

---

## Task 17: Echo `meta.seeds` in the compile response

Mirror `meta.bindings` (`compile_json.py:174-185`, `:232-238`) so the portal gets a positive compile-time signal rather than inferring from the version tag (§2.8).

**Files:**
- Modify: `src/refrain/compile_json.py:178-186` (base_meta), `:232-239` (success meta)
- Test: `tests/test_compile_json.py` (extend)

**Interfaces:**
- Consumes: the resolved `ir.controls` (each `IRControl.canonical_name`, `.seed`).
- Produces: `meta["seeds"]` — a sorted list of bare control names that carry a surviving seed; `[]` otherwise.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_compile_json.py`:

```python
def test_meta_echoes_seeded_control_names():
    from tests._seed_fixtures import SEEDING, NON_SEEDING   # verified fixtures (Task 4)
    assert compile_to_ir_json(SEEDING).meta["seeds"] == ["thr_uv"]
    assert compile_to_ir_json(NON_SEEDING).meta["seeds"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src pytest tests/test_compile_json.py -k seeds -v`
Expected: FAIL — `KeyError: 'seeds'`.

- [ ] **Step 3: Populate `meta.seeds`**

In `compile_to_ir_json`, add `"seeds": []` to `base_meta` (`compile_json.py:185`, alongside `"bindings"`). After `obj = ir_to_json_obj(...)` (`:230`), compute and add to the success `meta` (`:232`):

```python
    seeds = sorted(
        c.canonical_name.removeprefix("control/")
        for c in ir.controls.values()
        if c.seed is not None
    )
    meta = {
        "refrain_version": __version__,
        "ir_version": obj["refrain_ir_version"],
        "sample_rate_hz": obj["sample_rate_hz"],
        "content_hash": _content_hash(canonical),
        "extends": file_ast.protocol.extends,
        "bindings": applied_bindings,
        "seeds": seeds,
    }
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src pytest tests/test_compile_json.py -v`
Expected: PASS — `seeds == ["thr_uv"]` for the seeding protocol, `[]` otherwise.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/compile_json.py tests/test_compile_json.py
git commit -m "feat(compile): echo meta.seeds in the compile response"
```

---

## Task 18: Docs, free-finding corrections, and the v0.15.0 lockstep bump

**Files:**
- Modify: `docs/SPEC.md` (§9.3 gate now built; document the control `seed` surface + §4.9/§4.10 the free-finding gaps for `mode`/`block` if quick), `docs/IR-JSON.md` (v0.3 `seed`), `docs/PRIMITIVES.md:359` + `docs/DESIGN-NOTES.md:489` (P² correction), `docs/EMBEDDING.md` (mention `seed_report`)
- Modify: `pyproject.toml:7`, `refrain-core/pyproject.toml`, `refrain-core/Cargo.toml`, `CHANGELOG.md`, `refrain-core/CHANGELOG.md`
- Test: full suites (no new test code; this is docs + version)

**Interfaces:** none (documentation + release metadata).

- [ ] **Step 1: Document the `seed` surface**

In `docs/SPEC.md`, add a subsection under the controls chapter describing `seed = percentile { from, window, target_pct }`: the statistic-is-the-block-kind rule, `window` semantics (trailing N seconds), `target_pct` binding a `percent` control or number, and the resolve-time guarantees (real derive, warmup-fits, dead-seed elimination). Note the SPEC §9.3 gate is now implemented (a `0.3`-tagged protocol is refused by older runtimes at load).

In `docs/IR-JSON.md`, document `refrain_ir_version: "0.3"` and the `controls.<name>.seed` object (`statistic`/`from`/`window_samples`/`target_pct`), emitted only when present.

- [ ] **Step 2: Correct the P² claim (free finding §6)**

In `docs/PRIMITIVES.md:359`, replace the incorrect "P² online algorithm with constant memory" claim: both implementations keep the full window and call `np.percentile`/`percentile_linear` per sample (`docs/DESIGN-NOTES.md:489` is correct). Add a one-line note: constant-prefill seeding relies on the full-buffer representation; if P² ever lands, seeding needs rework (P² state is 5 markers, not a buffer).

In `docs/EMBEDDING.md`, add `seed_report()` alongside `export_state`/`seed_state`.

- [ ] **Step 3: Bump versions (lockstep) and changelogs**

Set `version = "0.15.0"` in `pyproject.toml:7`, `refrain-core/pyproject.toml`, and `refrain-core/Cargo.toml`. Add a `## 0.15.0` entry to `CHANGELOG.md` and `refrain-core/CHANGELOG.md` describing: first-class baseline seeding (`seed = percentile`), the SPEC §9.3 version gate, the expression-position control-ref fix, and `seed_report()`.

Do **not** tag here — tagging happens on the merge commit after the release PR merges (Global Constraints).

- [ ] **Step 4: Run the full suites and the fuzz gate**

Run:
```bash
PYTHONPATH=src pytest -q
cd refrain-core && cargo test
```
Expected: PASS across Python and Rust. Also run the `refrain-protocols` fuzz gate if wired locally (per project memory the fuzz CI gate must stay green as features ship); at minimum confirm no seeding-related regressions.

- [ ] **Step 5: Commit**

```bash
git add docs/ pyproject.toml refrain-core/pyproject.toml refrain-core/Cargo.toml \
        CHANGELOG.md refrain-core/CHANGELOG.md
git commit -m "release: v0.15.0 — first-class baseline seeding + version gate"
```

---

## Self-Review

**Spec coverage** (§ → task):
- §2.1 surface (no grammar change) → Tasks 5, 6 (parse via existing block shape).
- §2.2 control-not-phase/threshold, dropped `at`/`min_window` → Tasks 3, 6 (seed lives on the control; no extra fields).
- §2.3 IR (`IRControlSeed`, omit-when-unused, per-protocol v0.3, closed union) → Tasks 3, 4.
- §2.4 resolve (validate `from`, bake window, warmup-fits, dead-seed) → Tasks 6, 7, 8.
- §2.5 runtime polled latch (state-keyed, fire at top before thresholds) → Tasks 9, 10 (Python), 14 (Rust).
- §2.6 errors (fail-closed, disarm, NaN skip, skip_warmup) → Tasks 11 (Python), 14 (Rust fail-closed), 13 (NaN skip both).
- §2.7 `seed_report` (not a tap) → Tasks 12 (Python), 15 (Rust + PyO3/uniffi).
- §2.8 version skew (SPEC §9.3 gate, `meta.seeds`) → Tasks 2, 17.
- §3 prerequisites (control-ref fix, version gate) → Tasks 1, 2.
- §5 testing (skip_warmup=False bundle, 1e-9 parity, resolve cases) → Tasks 6/7/8 (resolve), 16 (parity).
- §6 free findings (P²/PRIMITIVES) → Task 18. Portal/`live_tunable`/`BandpassImpl` findings are host-repo or explicitly out of scope.
- §4 cross-repo host work → out of scope (documented under Scope).

**Placeholder scan:** every code step carries real code; every run step carries a command + expected output. Points that require an in-task grep (not a placeholder — the target is named): the `Dimensions` import location (Task 3), the `_validate` version→schema mapping (Task 4), the resolver `from .ir import` additions (Tasks 5, 6, 8), the mode-conditional house syntax (Task 8), the `Evaluator` constructor entrypoint and `IRControlRef`/`IRNumberLit` imports in `eval_.py` (Task 9), and the `mobile.rs` lock/wrapper idiom (Task 15).

**Type consistency:** `IRControlSeed` fields (`statistic`, `from_entity`, `window_samples`, `target_pct`) are identical across ir.py (Task 3), the emitter `seed` object (`statistic`/`from`/`window_samples`/`target_pct`, Task 4), and Rust `ControlSeed` (Task 13). The latch write path is `_apply_control` (Python) / `apply_control_value` (Rust) in every fire site; `set_control` (both) carries the disarm hook only. `seed_report()` keys and value fields (`status`/`value`/`source`/`target_pct`/`n_samples`/`window_s`/`at_time_s`) match across Python (Task 12), Rust `SeedReportEntry` (Task 15), and PyO3/uniffi (Task 15). Status strings (`pending`/`seeded`/`insufficient_samples`/`disarmed_by_host`) match Python literals and Rust `SeedStatus::as_str`.
