# M3c — Python `Evaluator` delegates to the Rust core (handoff prompt)

> **For the next session.** This is a ready-to-execute prompt. Use
> `superpowers:subagent-driven-development` (subagent-per-task + controller
> diff-audit) and the project's hard rule: **DO NOT reinvent or duplicate
> what exists — reuse it, and check every diff for duplication.** Re-verify
> every commit (cargo test, pytest on *both* backends, both drift gates, 0
> warnings).

**Goal:** make the Python `Evaluator` *delegate its live/push runtime to the
Rust core* (`refrain-core`), so there is literally ONE implementation of the
signal-to-feedback transformation — then validate by running the evaluator test
suite through the Rust backend. This is the "reproducibility by construction"
finale (CRED-nf).

## Where things stand (read first)

- Branch `worktree-rust-core-poc` / PR #13. Read `refrain-core/README.md` and
  `docs/superpowers/plans/2026-05-24-rust-core-production-roadmap.md` (M1a–M4 +
  M3a/M3b are DONE).
- The Rust core is **API-complete and behavior-equivalent** to the Python
  evaluator's live path: `live`/`start`/`step_chunk`→events/`set_control`/`stop`/
  `last_taps` all match Python to ≤1e-13 across the corpus (streams, events,
  taps, set_control golden vectors). 19 Rust tests green; 401 Python tests green.
- The PyO3 binding (`refrain_core.RustEvaluator`, feature `python`, built via
  `maturin develop`) already exposes: `RustEvaluator(ir_json, sample_rate_hz,
  channel_names)`, `start(skip_warmup)`, `stop()`, `step_chunk(chunk)->dict`
  (streams), `step_chunk_events(chunk)->[Event]`, `set_control(name, value)`
  (raises `KeyError` on unknown), `last_taps()`. `Event` has
  `timestamp_s/channel/kind/value`.
- IR-JSON is emitted by `refrain.ir_json.ir_to_json(ir, sample_rate_hz=...)`.

## Approach

Add an opt-in `backend` to the push-mode evaluator; when `"rust"`, forward to
`refrain_core` instead of running the Python primitive impls. Keep `"python"`
as the default and the reference oracle. Then parametrize the *behavioral*
evaluator tests on both backends and reconcile.

**Scope boundary (important):** only the **live/push runtime** delegates.
The parser, resolver, composer, source readers (FIF/EDF/XDF), `ir_print`/CRED-nf
export, and resolver *validation* stay Python — they are front-end/offline and
are NOT part of the Rust core. Pull-mode `Evaluator(ir, source).run()` (offline
file replay) stays Python too.

## Tasks

### Task 1 — `backend="rust"` delegation path in `Evaluator.live`
**Files:** `src/refrain/eval_.py` (modify), `tests/test_eval_rust_backend.py` (new).

- [ ] **Write the failing test:** a test that builds a resolved IR (e.g. from
  `examples/smr_cz.refrain` + `q21.json`), constructs
  `Evaluator.live(ir, sample_rate_hz=256, channel_names=("Cz","A1","A2"),
  backend="rust")`, `start(skip_warmup=True)`, feeds a seeded chunk, and asserts
  the returned events are `Event` instances matching the `backend="python"`
  run's events within `atol=1e-6` (mirror the bench tolerance). Run it; it fails
  (`backend` is an unexpected kwarg).
- [ ] **Implement:** add `backend: str = "python"` to `Evaluator.live(...)`.
  When `"rust"`: lazily `import refrain_core` (raise a clear error if the wheel
  isn't built — `maturin develop` first); emit IR-JSON via
  `ir_to_json(ir, sample_rate_hz=sample_rate_hz)`; hold a `RustEvaluator`.
  Route `start`/`stop`/`step_chunk`/`set_control`/`last_taps` to it, wrapping
  Rust `Event` objects back into the Python `Event` dataclass and preserving the
  exact return types/semantics. For `record_streams=True`, back `last_streams()`
  with the Rust `step_chunk(chunk)` streams dict (keys already match the Python
  `last_streams` convention — verify). **REUSE** `ir_to_json` and the existing
  `RustEvaluator`; do NOT duplicate evaluation logic in Python.
- [ ] Verify the test passes; `pytest -q` still green; commit.

### Task 2 — error/lifecycle-semantics parity
**Files:** `src/refrain/eval_.py` and/or `refrain-core/src/python.rs` (as needed), tests.
Reconcile the observable error/lifecycle behavior so the Rust path matches the
Python path's contracts that tests assert:
- [ ] `step_chunk` after `stop()` → same error type as Python (`RuntimeError`).
- [ ] channel-count / channel-name mismatch on a chunk → same error as Python.
- [ ] `set_control` unknown name → `KeyError` (already mapped; add a test).
- [ ] `state` / `warmup_remaining_s` read-only properties behave consistently
  (decide: compute host-side in the wrapper, or expose from Rust). 
For each: write the failing parity test (both backends raise the same way),
implement the minimal fix (prefer fixing in the Python wrapper; only touch
`python.rs` if the Rust side must surface something), verify, commit. Keep
changes minimal and reuse existing error types.

### Task 3 — parametrize the behavioral evaluator suite on both backends
**Files:** `tests/conftest.py` (new or modified), the `test_eval_*.py` that are
*behavioral* (events/taps/controls/lifecycle/coherence-integration/record_streams).
- [ ] Add a pytest mechanism (a `backend` fixture + marker, or an env flag
  `REFRAIN_EVAL_BACKEND`) so the behavioral evaluator tests can run under
  `backend="rust"`. Apply it to the tests that exercise the *runtime* through
  `Evaluator.live`. Do **not** parametrize: parser/resolver/sources/ir_print
  tests, resolver-validation tests, pull-mode `run()` tests, or tests that poke
  Python-only internals (`_impls`, `_warmup_samples`, etc.) — list each excluded
  test with a one-line reason in a module docstring or a skip reason.
- [ ] Run `REFRAIN_EVAL_BACKEND=rust pytest tests/test_eval_*.py` (after
  `maturin develop`). Triage failures:
  - genuine parity bugs → fix the Rust core (golden-vector TDD: capture the
    Python output, make Rust match) and re-run;
  - Python-only-internal assertions → exclude with a reason.
- [ ] Goal: a green run of the behavioral evaluator suite under
  `backend="rust"`, with every exclusion explicitly justified. Commit when green.

### Task 4 — wire the drift gate to cover both backends + CI
**Files:** `refrain-core/tools/check_equivalence.py` (extend) or a new gate;
`.github/workflows/test.yml` (the existing `rust-equivalence` job).
- [ ] Extend the gate (or add a step) to run the behavioral evaluator suite
  under `REFRAIN_EVAL_BACKEND=rust` (after building the wheel), so dual-backend
  parity is enforced in CI. Reuse the existing gate structure. Validate locally.
- [ ] Commit.

## Gotchas / reuse pointers

- **Build the wheel first:** `cd refrain-core && VIRTUAL_ENV=../.venv
  PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 ../.venv/bin/maturin develop --release`.
- **Sample rate & channels:** emit IR-JSON at the *runtime* `sample_rate_hz`
  (the emitter override), and pass the host channel layout — not the protocol's
  required channels. (See README "two facts".)
- **Streams key convention:** Rust `step_chunk` returns bare-name keys matching
  Python `last_streams`; `last_taps` uses canonical-prefixed keys. Don't
  re-map blindly — match what each Python method returns.
- **Don't reinvent:** `ir_to_json`, `RustEvaluator`, the golden-vector
  generator (`gen_fixtures.py`) and harnesses already exist. The wrapper is thin
  forwarding + type-wrapping. If you find yourself porting evaluation logic into
  Python, stop — it belongs in (and already is in) Rust.
- **Custom primitives / unimplemented features:** any test protocol using a
  primitive the Rust core doesn't implement (e.g. SPEC §4.11 custom Python
  primitives, or `hilbert(kind="iir_allpass")`) must stay Python-only — exclude
  with a reason; don't stub it in Rust.

## Verification (definition of done)

- `REFRAIN_EVAL_BACKEND=rust` behavioral evaluator suite: green (exclusions
  justified). Default `pytest` (python backend): still green (401+).
- `cargo test`: green; `cargo build --all-targets`: 0 warnings.
- Both drift gates (`check_equivalence.py`, `check_bindings.py`): PASS.
- A short note in the roadmap marking M3c done + the dual-backend gate live.

## After M3c

- **M5** — `docs/IR-JSON.md` (versioned wire schema), publish the golden-vector
  conformance suite, and the CRED-nf reproducibility doc ("one Rust core
  compiled everywhere, validated by the conformance suite").
- Long-term: consider flipping the `Evaluator.live` default to `backend="rust"`
  once the dual-backend gate has been green in CI for a while, making the Python
  primitive impls the spec oracle used only by the gate.
