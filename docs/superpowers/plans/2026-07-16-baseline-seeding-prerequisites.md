# Baseline Seeding Prerequisites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two latent `refrain-core` defects that sit directly on the baseline-seeding feature's path, before that feature is built on top of them.

**Architecture:** Two independent Rust bug fixes, each with a regression test that fails first. Task 1 makes expression-position control refs live, matching Python. Task 2 makes the core refuse IR whose schema version it does not understand, implementing SPEC §9.3. Neither depends on the other; neither depends on baseline seeding. Both ship before v0.15.0.

**Tech Stack:** Rust (`refrain-core`), serde_json, Python 3 (`refrain` reference evaluator, fixture generation), pytest, cargo.

**Spec:** `docs/superpowers/specs/2026-07-16-baseline-seeding-design.md` §3.

## Global Constraints

- **Toolchain PATH:** `cargo` and `maturin` live in `~/.cargo/bin`, which is **not** on the default PATH. Prefix every cargo/maturin command with `export PATH="$HOME/.cargo/bin:$PATH"`.
- **Python venv:** this worktree has `.venv/`. Use `.venv/bin/python` and `.venv/bin/pytest`, never bare `python`/`pytest`.
- **Never hand-edit fixtures.** `refrain-core/tests/fixtures/*` are generated. Regenerate with `.venv/bin/python refrain-core/tools/gen_fixtures.py` from the worktree root (`docs/CONFORMANCE.md` §1).
- **Parity tolerance:** continuous streams `atol=1e-6, rtol=1e-4`; boolean streams exact (`docs/CONFORMANCE.md` §4).
- **Do not add an `Expr` node discriminator.** `Expr` is an internally-tagged serde enum (`refrain-core/src/ir.rs:209`); an unknown variant fails the entire document load. New *fields* are free; new *node kinds* are fatal.
- **Baseline before you start:** `819 passed, 27 skipped`. Several skips read `refrain_core wheel not installed`; Task 1's Step 9 needs the wheel.

### API facts you will need (verified — do not re-derive)

```rust
// refrain-core/src/eval.rs
impl Evaluator {
    pub fn new(p: &Protocol, sample_rate_hz: f64, channels: &[String]) -> Self;  // :822 — NOT a Result
    pub fn start(&mut self, skip_warmup: bool);                                   // :1067
    pub fn set_control(&mut self, name: &str, value: f64) -> Result<(), String>;  // :1051
    pub fn step_chunk_events(&mut self, chunk: &[Vec<f64>]) -> Vec<Event>;        // :1586 — row slices, not flat
    pub fn last_taps(&self) -> BTreeMap<String, f64>;                             // :1493
}
```

`Evaluator::new` takes an **already-deserialized** `&Protocol`. Deserialization happens in the two callers — `refrain-core/src/mobile.rs:113` and `refrain-core/src/python.rs:85` — which is where Task 2's gate must go. `RefrainError` (with `InvalidIr { message: String }`) is declared in `mobile.rs:22`, not in `eval.rs`.

---

### Task 1: Make expression-position control refs live in Rust

**The defect.** `refrain-core/src/eval.rs:1902` compiles a `control_ref` in a plain value position (an output binding, a derive formula) to `CNode::Const(*default)` — frozen at its default forever. `set_control` on such a control returns **success and does nothing**; `eval.rs:1048` documents this as *"A known control with no bound stages … is a no-op success"*. Python evaluates the same node live (`eval_.py:1399`, from a `control_chunks` cache rebuilt every chunk).

The corpus cannot catch it: all four `control_ref` nodes across the parity fixtures sit in *recognised parameter slots* (`Control::Weight`, `Control::Percentile`), which are wired correctly. None sits in an expression position. `Evaluator.live(backend="auto")` prefers Rust whenever the wheel is importable, so this is a "passes tests, silently does nothing in production" bug.

**The fix.** `Control::Const { value: ControlCell }` (`eval.rs:107`) and `CNode::ConstCell(cell)` (`eval.rs:294`, evaluated at `:316`) already exist and already do the right thing — `absolute(value: <control>)` thresholds use them at `eval.rs:2128`. `build_node` simply never registers the binding. Register it.

**Files:**
- Create: `bench/protocols/micro_11_control_expr.refrain`
- Modify: `refrain-core/tools/gen_fixtures.py:245` (stem list)
- Modify: `refrain-core/src/eval.rs:1894-1902` (`build_node`)
- Modify: `refrain-core/tests/equivalence.rs` (append a test fn)
- Test: `refrain-core/tests/set_control.rs` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: no new API. Behaviour change only — `set_control` on an expression-position control now moves the Rust output, matching Python.

- [ ] **Step 1: Write the protocol that exercises the defect**

The load-bearing line is `audio_gain = gain * reward.continuous`: a `control_ref` (`gain`) in an output binding — a plain expression position, not a recognised parameter slot.

Create `bench/protocols/micro_11_control_expr.refrain`:

```refrain
// Exercises a control_ref in a plain EXPRESSION position (an output binding),
// as opposed to a recognised tunable parameter slot (percentile target_pct,
// smooth tau, sigmoid midpoint, reward weight). Regression fixture for the
// Rust build_node divergence: such refs must be live on BOTH backends.
protocol "micro_11_control_expr" {
  meta { description = "control_ref in expression position" }

  requires { sample_rate_hz = 256 Hz; channels = ["Cz"] }

  input "raw" { montage = referential(active: "Cz", reference: "device") }

  derive "env" {
    from = "raw"
    pipeline = [
      bandpass(band: (12 Hz, 15 Hz), order: 4),
      rectify(),
      smooth(tau: 0.5 s),
    ]
  }

  threshold "env_t" { signal = "env"; type = absolute(value: 1.0 uV) }

  reward { continuous = sigmoid(env - "env_t") }

  controls {
    gain = percent {
      default      = 50
      range        = (0, 100)
      label        = "Output gain"
      live_tunable = true
    }
  }

  output { audio_gain = gain * reward.continuous }

  session { phases = [ phase { name = "run"; duration = 16 s } ] }
}
```

- [ ] **Step 2: Register the stem and confirm the fixture actually contains the ref**

In `refrain-core/tools/gen_fixtures.py`, add `"micro_11_control_expr",` to the stem list next to `"micro_10_autocorr",` (line 245).

```bash
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research
.venv/bin/python refrain-core/tools/gen_fixtures.py
.venv/bin/python -c "
import json
ir = json.load(open('refrain-core/tests/fixtures/micro_11_control_expr.ir.json'))
print(json.dumps(ir['output'], indent=2))
"
```

Expected: `audio_gain` is a `binop` whose `left` is
`{"node": "control_ref", "target": "control/gain", "default": 50.0}`.

**If it is a plain `number`, stop.** The control was folded to a literal and the test would prove nothing.

- [ ] **Step 3: Write the failing test**

Append to `refrain-core/tests/set_control.rs`. It reuses the file's existing `Io` struct and `refrain_core::{eval::Evaluator, ir::Protocol}` imports.

```rust
/// A `control_ref` in a plain expression position (an output binding) must be
/// LIVE, matching the Python evaluator, which rebuilds its control cache every
/// chunk (`eval_.py:1399`). Regression: `build_node` compiled these to
/// `CNode::Const`, freezing them at the default and making `set_control` a
/// silent no-op success.
#[test]
fn control_ref_in_expression_position_is_live() {
    const S: &str = "micro_11_control_expr";
    let ir = std::fs::read_to_string(format!("tests/fixtures/{S}.ir.json"))
        .expect("fixture missing — run tools/gen_fixtures.py");
    let p: Protocol = serde_json::from_str(&ir).expect("parse ir");
    let io: Io = serde_json::from_str(
        &std::fs::read_to_string(format!("tests/fixtures/{S}.io.json")).unwrap(),
    )
    .expect("parse io");

    // Feed the SAME four chunks twice: once at the default gain (50), once
    // after set_control(gain, 100). Identical input, so a live control must
    // exactly double `output/audio_gain`; a frozen one leaves it unchanged.
    let run = |gain: Option<f64>| -> f64 {
        let mut ev = Evaluator::new(&p, io.sample_rate_hz, &io.channels);
        ev.start(true);
        if let Some(g) = gain {
            ev.set_control("gain", g).expect("gain is a declared control");
        }
        let mut last = 0.0;
        for c in 0..4 {
            let chunk = &io.input[c * io.chunk_size..(c + 1) * io.chunk_size];
            ev.step_chunk_events(chunk);
            last = *ev
                .last_taps()
                .get("output/audio_gain")
                .expect("output/audio_gain tap");
        }
        last
    };

    let at_default = run(None);
    let at_double = run(Some(100.0));

    assert!(
        at_default.abs() > 1e-9,
        "degenerate fixture: output is 0 at the default gain, so this test \
         cannot distinguish live from frozen"
    );
    let want = 2.0 * at_default;
    assert!(
        (at_double - want).abs() <= 1e-6 + 1e-4 * want.abs(),
        "expression-position control_ref is frozen: default={at_default}, \
         after set_control(gain, 100)={at_double}, expected {want}"
    );
}
```

- [ ] **Step 4: Run the test to verify it fails**

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research/refrain-core
cargo test --test set_control control_ref_in_expression_position_is_live -- --nocapture
```

Expected: **FAIL** — `expression-position control_ref is frozen: default=<x>, after set_control(gain, 100)=<same x>, expected <2x>`. Both runs return the identical value because `CNode::Const(50.0)` ignores the write.

- [ ] **Step 5: Apply the fix**

In `refrain-core/src/eval.rs`, replace the `Expr::ControlRef` arm of `build_node` — line 1902 **and** its now-false comment at lines 1897-1901:

```rust
        // A `control_ref` in a plain value position (an output binding, a derive
        // formula) is LIVE: it compiles to a shared cell registered as a
        // `Control::Const` binding, so `set_control` moves it mid-session. This
        // mirrors the Python evaluator, which rebuilds `control_chunks` from
        // `self._controls` every chunk (`eval_.py:1399`). Recognised parameter
        // slots (percentile `target_pct`, smooth `tau`, sigmoid `midpoint`,
        // reward `weight`) register their own richer bindings elsewhere.
        Expr::ControlRef { target, default } => {
            let cell: ControlCell = Arc::new(Mutex::new(*default));
            ctx.register(target, Control::Const { value: cell.clone() });
            CNode::ConstCell(cell)
        }
```

`ctx: &mut BuildCtx` is already `build_node`'s second parameter, and `BuildCtx::register(&mut self, target: &str, ctrl: Control)` is at `eval.rs:156`. `Expr::ControlRef` declares `target: String` (`ir.rs:223`) plus the baked `default`.

- [ ] **Step 6: Run the test to verify it passes**

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research/refrain-core
cargo test --test set_control control_ref_in_expression_position_is_live
```

Expected: **PASS**.

- [ ] **Step 7: Pin the static streams for cross-backend parity**

`equivalence.rs` is one test function per stem — not a list. Append, matching the `micro_10_autocorr_equivalent` shape at line 129:

```rust
#[test]
fn micro_11_control_expr_equivalent() {
    // A control_ref in an output binding: the static streams must match Python
    // at the default control value. The LIVE behaviour (set_control moves it)
    // is pinned by set_control.rs::control_ref_in_expression_position_is_live.
    run_protocol("micro_11_control_expr");
}
```

- [ ] **Step 8: Run both full suites**

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research/refrain-core
cargo test
cd ..
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: cargo all green; pytest ≥`819 passed`, **0 failed**. A previously-passing test breaking here would mean a fixture depended on the frozen behaviour — investigate, do not paper over.

- [ ] **Step 9: Run the equivalence gate**

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research
.venv/bin/python refrain-core/tools/check_equivalence.py
```

Expected: green. This regenerates fixtures, runs `cargo test`, builds the `refrain_core` wheel, and re-runs the Python behavioural suite under `REFRAIN_EVAL_BACKEND=rust`.

- [ ] **Step 10: Commit**

```bash
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research
git add bench/protocols/micro_11_control_expr.refrain \
        refrain-core/tools/gen_fixtures.py \
        refrain-core/src/eval.rs \
        refrain-core/tests/set_control.rs \
        refrain-core/tests/equivalence.rs \
        refrain-core/tests/fixtures/micro_11_control_expr.*
git commit -m "fix(core): expression-position control_ref is live, not frozen

build_node compiled a control_ref in a plain value position to
CNode::Const(default), freezing it forever and making set_control a silent
no-op success. Python evaluates the same node live. Register a Control::Const
binding over a shared cell instead — the machinery absolute(value: <control>)
thresholds already use.

The corpus could not catch this: every control_ref across the parity fixtures
sat in a recognised parameter slot. Adds micro_11_control_expr, which places
one in an output binding.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Refuse IR newer than the runtime understands (SPEC §9.3)

**The defect.** SPEC §9.3 states: *"a protocol whose schema is newer than the runtime supports is refused at load with a clear diagnostic."* The core never implemented it. `refrain_ir_version` appears **nowhere** in `refrain-core/src/` — `Protocol` (`ir.rs:12`) does not declare the field. With no `deny_unknown_fields` anywhere in the crate, an old core handed a newer protocol silently ignores what it cannot honour and runs anyway.

That is the exact shape of the incident baseline seeding exists to prevent, so the feature must not ship without this. Nothing is released, so the gate becomes the floor at zero cost.

**Files:**
- Modify: `refrain-core/src/ir.rs` (field + constant + check fn)
- Modify: `refrain-core/src/mobile.rs:113` (call the gate)
- Modify: `refrain-core/src/python.rs:85` (call the gate)
- Modify: `docs/IR-JSON.md` (versioning section)
- Test: `refrain-core/tests/ir_deser.rs` (append)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `refrain_core::ir::SUPPORTED_IR_VERSIONS: &[&str]` — versions this core accepts. **The feature plan appends `"0.3"`.**
  - `refrain_core::ir::check_ir_version(p: &Protocol) -> Result<(), String>` — `Ok` if supported; `Err(diagnostic)` otherwise.

- [ ] **Step 1: Write the failing tests**

Append to `refrain-core/tests/ir_deser.rs`. It already has `use refrain_core::ir::Protocol;` and a `load(stem) -> Protocol` helper at line 6.

```rust
/// SPEC 9.3: a protocol whose schema is newer than this runtime supports must
/// be refused at load with a clear diagnostic — never silently ignored. An
/// unknown field is invisible to serde, so without this gate a newer protocol
/// runs with its new semantics dropped and nobody is told.
#[test]
fn refuses_ir_version_newer_than_supported() {
    let mut v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/fixtures/micro_03_envelope.ir.json").unwrap(),
    )
    .unwrap();
    v["refrain_ir_version"] = serde_json::Value::String("99.0".into());

    let p: Protocol = serde_json::from_str(&v.to_string()).expect("still deserializes");
    let err = refrain_core::ir::check_ir_version(&p)
        .expect_err("must refuse IR newer than supported");
    assert!(
        err.contains("99.0") && err.contains("refrain_ir_version"),
        "diagnostic must name the offending version and the field, got: {err}"
    );
}

/// The versions this core claims to support must pass the gate. Guards against
/// it being over-eager.
#[test]
fn accepts_supported_ir_versions() {
    for stem in ["micro_03_envelope", "realistic_smr"] {
        let p = load(stem);
        let tag = p.refrain_ir_version.clone().unwrap_or_else(|| "0.1".into());
        assert!(
            refrain_core::ir::SUPPORTED_IR_VERSIONS.contains(&tag.as_str()),
            "{stem} is tagged {tag}, which this core does not claim to support"
        );
        refrain_core::ir::check_ir_version(&p)
            .unwrap_or_else(|e| panic!("{stem} ({tag}) must pass the gate: {e}"));
    }
}

/// A document with no version tag predates versioning; treat it as 0.1 and
/// accept it.
#[test]
fn accepts_ir_with_no_version_tag() {
    let mut v: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string("tests/fixtures/micro_03_envelope.ir.json").unwrap(),
    )
    .unwrap();
    v.as_object_mut().unwrap().remove("refrain_ir_version");

    let p: Protocol = serde_json::from_str(&v.to_string()).expect("untagged must deserialize");
    assert!(p.refrain_ir_version.is_none());
    refrain_core::ir::check_ir_version(&p).expect("untagged must pass the gate");
}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research/refrain-core
cargo test --test ir_deser
```

Expected: **FAIL to compile** — no `SUPPORTED_IR_VERSIONS`, no `check_ir_version`, no `Protocol::refrain_ir_version`. That is the correct first failure.

- [ ] **Step 3: Add the field, the constant, and the check**

In `refrain-core/src/ir.rs`, add to `Protocol` (the struct at line 12). `#[serde(default)]` keeps untagged documents deserializing:

```rust
    /// The IR-JSON schema version this document was emitted against
    /// (`ir_json.py:56` `_protocol_ir_version` tags it per protocol). Absent on
    /// pre-versioning documents, which are treated as "0.1".
    #[serde(default)]
    pub refrain_ir_version: Option<String>,
```

Then at module level in `ir.rs`:

```rust
/// IR-JSON schema versions this core can honour. SPEC 9.3: a document tagged
/// newer than any of these is refused at load rather than silently
/// misinterpreted. This crate has no `deny_unknown_fields`, so an unknown field
/// is invisible to serde — without this gate, a newer protocol would run with
/// its new semantics dropped and no signal to anyone.
pub const SUPPORTED_IR_VERSIONS: &[&str] = &["0.1", "0.2"];

/// Refuse a document tagged with an unsupported schema version. Untagged
/// documents predate versioning and are treated as "0.1".
pub fn check_ir_version(p: &Protocol) -> Result<(), String> {
    let tag = p.refrain_ir_version.as_deref().unwrap_or("0.1");
    if SUPPORTED_IR_VERSIONS.contains(&tag) {
        return Ok(());
    }
    Err(format!(
        "unsupported refrain_ir_version {tag:?}: this runtime supports \
         {SUPPORTED_IR_VERSIONS:?}. Update the runtime, or recompile the \
         protocol against a supported version."
    ))
}
```

- [ ] **Step 4: Call the gate at both load sites**

`Evaluator::new` takes an already-parsed `&Protocol` and returns `Self`, so the gate belongs in the two deserializing callers.

In `refrain-core/src/mobile.rs`, inside `new` (line 113), between the `from_str` and `Evaluator::new`:

```rust
        let p: Protocol = serde_json::from_str(&ir_json)
            .map_err(|e| RefrainError::InvalidIr { message: e.to_string() })?;
        crate::ir::check_ir_version(&p)
            .map_err(|message| RefrainError::InvalidIr { message })?;
```

In `refrain-core/src/python.rs`, inside `new` (line 85), likewise:

```rust
        let p: Protocol = serde_json::from_str(ir_json)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("IR-JSON: {e}")))?;
        crate::ir::check_ir_version(&p)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
```

**Do not** add a bypass flag. The gate must not be skippable.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research/refrain-core
cargo test --test ir_deser
```

Expected: **PASS**, all three.

- [ ] **Step 6: Run both full suites**

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research/refrain-core
cargo test
cd ..
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: all green. Every shipped fixture is tagged `0.1` or `0.2`, so none is refused. If one *is* refused, reconcile its tag against `ir_json.py:56` `_protocol_ir_version` — **do not widen `SUPPORTED_IR_VERSIONS` to make a red test green.**

- [ ] **Step 7: Make the docs true**

`docs/IR-JSON.md` currently documents only v0.1 and does not describe an enforced gate. In its versioning section, state the behaviour that now exists:

```markdown
A runtime refuses at load any document whose `refrain_ir_version` is not in the
set it supports (`SUPPORTED_IR_VERSIONS`, `refrain-core/src/ir.rs`), with a
diagnostic naming the offending version. A document with no tag is treated as
`0.1`. This is what makes adding a new IR field safe: an old runtime cannot
silently ignore semantics it does not implement, because it will not load the
document at all.
```

- [ ] **Step 8: Commit**

```bash
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/baseline-seeding-research
git add refrain-core/src/ir.rs refrain-core/src/mobile.rs refrain-core/src/python.rs \
        refrain-core/tests/ir_deser.rs docs/IR-JSON.md
git commit -m "fix(core): refuse IR newer than the runtime supports (SPEC 9.3)

SPEC 9.3 specifies that a protocol whose schema is newer than the runtime
supports is refused at load with a clear diagnostic. The core never
implemented it: refrain_ir_version appeared nowhere in refrain-core/src, and
Protocol did not declare the field. With no deny_unknown_fields anywhere, an
old core handed a newer protocol silently ignored what it could not honour.

Adds SUPPORTED_IR_VERSIONS + check_ir_version, called at both deserializing
call sites (mobile.rs, python.rs). Untagged documents are treated as 0.1 and
still load; every shipped fixture is unaffected.

Prerequisite for baseline seeding: without it, a seeding protocol on an old
core would run at its placeholder defaults, silently — the exact failure the
feature exists to prevent.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Done when

- `cargo test` green; `.venv/bin/python -m pytest tests/ -q` green (≥819 passed, 0 failed).
- `.venv/bin/python refrain-core/tools/check_equivalence.py` green.
- `set_control` on an expression-position control ref moves the Rust output — proven by a test that failed before the fix.
- A protocol tagged `99.0` is refused with a diagnostic naming the version; untagged and `0.1`/`0.2` documents still load.

**Next:** `docs/superpowers/plans/2026-07-16-baseline-seeding.md` (the feature, v0.15.0), which appends `"0.3"` to `SUPPORTED_IR_VERSIONS`.
