# Refrain Rust Core — Production Implementation Plan (Phase B→D)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan milestone-by-milestone. This is a **roadmap of sub-plans** — each milestone (M1…M5) is an independently executable plan that produces working, testable software. Steps use checkbox (`- [ ]`) syntax. The first risk-retiring milestone (M1a) is written in full bite-sized TDD detail as the template; detail each later milestone the same way when you reach it.

**Goal:** Turn the validated PoC (branch `worktree-rust-core-poc`, PR #13) into a production-grade portable Rust core that is the *one canonical* implementation of Refrain's signal-to-feedback transformation, callable from Python (desktop, first), Swift, and Kotlin (mobile).

**Architecture:** Python stays the authoring front-end (parse → resolve → emit **IR-JSON** with baked filter coefficients). The Rust crate `refrain-core/` deserializes IR-JSON and evaluates chunk-by-chunk. Bindings: **PyO3 + rust-numpy** (desktop/tooling, ships first), then **uniffi** (Swift + Kotlin) for the mobile app. The desktop milestone makes the Python `Evaluator` *delegate to the Rust core*, so there is literally one implementation (reproducibility-by-construction, the CRED-nf goal).

**Tech Stack:** Rust (serde, rustfft/realfft, pyo3, rust-numpy, uniffi), maturin, cargo-ndk, xcframework tooling; Python 3.10+ (lark, numpy, scipy) front-end; the existing `bench/` harness as the equivalence + latency gate.

**Sequencing decisions (locked):** App targets **iOS/Swift + Android/Kotlin**; **ship Python/desktop first**. Lead with the **highest risks**, not the easy primitives.

---

## Where we are (M0 — done, PR #13)

Validated on `worktree-rust-core-poc`: IR-JSON emitter (`src/refrain/ir_json.py`); Rust crate `refrain-core/` reproducing `micro_01..05` vs the Python evaluator to ~1e-13 (machine precision), ~10–13× faster per chunk; PyO3 binding through the bench `ChunkedRunner`; staticlib cross-compiles for iOS+Android. Primitives done: referential montage, biquad cascade (DF2T), FIR Hilbert, magnitude, smooth, rolling-percentile (introselect), absolute threshold, above/below/all_of, dwell, sigmoid, `/` binop, output clamp.

**Open from M0:** `realistic_smr` (control-ref `target_pct`), `coherence`, the remaining primitives, real bindings/packaging, the IR-JSON schema spec.

## Risk-led ordering (why this sequence)

| Risk (from feasibility report) | Retired by |
|---|---|
| #1 `coherence` Welch MSC parity (no Rust crate) | **M1a** — do it *first*, before banking the timeline |
| #3 two implementations drift (defeats reproducibility) | **M1b** equivalence CI gate, then **M3** Python-delegates-to-Rust |
| #2 coefficient-baking rejected | already validated in M0; **M2** extends it to control-refs |
| Per-sample percentile parity / perf | already handled in M0 (introselect); regression-guarded by M1b |

---

## M1a — Coherence (Welch MSC) parity spike  ✅ DONE (commit d7474ac)

**Result:** `refrain-core/src/coherence.rs` (realfft Welch MSC) matches `scipy.signal.coherence` to **max|diff| = 9.7e-17** (machine epsilon) on the `micro_06_coherence` golden-vector test — the #1 estimate risk is retired. Reused the fixture generator, equivalence harness, Coeffs struct, and the integration-test protocol; the only new code is the Welch math. All 6 Rust equivalence tests + Python suite (398) green, 0 warnings. Original step-by-step plan retained below for reference.

---

**Why first:** `coherence` is the one HARD primitive with no Rust crate; SciPy's exact Welch semantics (window, detrend, overlap, scaling, one-sided scaling, segment averaging) are fiddly. Proving bit-tolerance parity now converts the #1 estimate risk into a known quantity.

**Files:**
- Create: `refrain-core/src/coherence.rs`
- Modify: `refrain-core/src/dsp.rs` (add a `Coherence` stateful tracker), `refrain-core/Cargo.toml` (add `realfft`)
- Create fixture: `refrain-core/tests/fixtures/coherence_pair.io.json` (+ `.ir.json`)
- Modify: `refrain-core/tools/gen_fixtures.py` (add a 2-input coherence protocol), `refrain-core/tests/equivalence.rs`
- Reference (read, don't modify): `src/refrain/primitive_impls.py:709-803` (`CoherenceImpl`), and `scipy.signal.coherence` / `scipy.signal.csd` (Welch).

**Algorithm to match (from `CoherenceImpl`):** two `deque(maxlen=window_samples)` buffers; once `>= nperseg + (nperseg-noverlap)` samples, call `scipy.signal.coherence(a, b, fs, nperseg, noverlap, window="hann")`; band-average MSC over `[band_lo, band_hi]` via a frequency mask; `nan_to_num`; emit a constant scalar per chunk; `nperseg = max(8, fs//4)`, `noverlap = nperseg//2`.

- [ ] **Step 1: Write a failing Rust unit test for Hann-windowed periodogram parity**

Generate, in Python (one-off, committed as a tiny fixture `coherence_welch_vectors.json`), reference outputs of `scipy.signal.csd`/`coherence` for a known 2-channel signal at the protocol's nperseg/noverlap. Then:

```rust
// refrain-core/tests/coherence_welch.rs
use refrain_core::coherence::welch_msc_band;
#[test]
fn welch_msc_matches_scipy_on_fixture() {
    let v = load_welch_vectors(); // a, b, fs, nperseg, noverlap, band, expected_msc
    let got = welch_msc_band(&v.a, &v.b, v.fs, v.nperseg, v.noverlap, v.band);
    assert!((got - v.expected_msc).abs() < 1e-9,
        "MSC mismatch: got {got}, want {}", v.expected_msc);
}
```

- [ ] **Step 2: Run it, watch it fail** — `cargo test --test coherence_welch` → FAIL (`welch_msc_band` undefined).

- [ ] **Step 3: Implement `welch_msc_band`** in `src/coherence.rs` using `realfft`:
  - Split the buffer into `nperseg`-length segments stepping by `nperseg - noverlap`.
  - Apply a Hann window (`0.5 - 0.5*cos(2πn/(nperseg-1))`, matching scipy's `sym=False`/`periodic` — **verify which scipy uses**; scipy.signal.get_window("hann", nperseg) is periodic).
  - Per segment: rFFT → `Pxx += |A|²`, `Pyy += |B|²`, `Pxy += A * conj(B)`, with scipy's scaling `1/(fs * sum(win²))` (density scaling; cancels in MSC but keep it for csd parity).
  - Average over segments; `MSC(f) = |Pxy|² / (Pxx * Pyy)`.
  - Band-average MSC over bins with `band_lo <= f <= band_hi` (`f = k*fs/nperseg`); `nan_to_num`.

- [ ] **Step 4: Run it, watch it pass** — `cargo test --test coherence_welch` → PASS.

- [ ] **Step 5: Add the streaming `Coherence` stage** in `dsp.rs` (two `VecDeque`s, warmup returns 0.0 until `>= min_samples`, then `welch_msc_band` over the current buffers, emitted as a constant-per-chunk vec). Bake `nperseg`/`noverlap`/`window_samples`/`band` into IR-JSON (extend `_extract_coeffs` — `CoherenceImpl` already exposes them).

- [ ] **Step 6: End-to-end golden-vector test.** Add a 2-input coherence protocol to `gen_fixtures.py`, generate `coherence_pair.{ir,io}.json`, add `coherence_pair_equivalent()` to `equivalence.rs`. Run `cargo test --test equivalence` → PASS within `atol=1e-6, rtol=1e-4`.

- [ ] **Step 7: Commit** — `git commit -m "feat(rust-core): coherence (Welch MSC) with scipy parity"`.

**Exit criteria:** Rust `coherence` matches `scipy.signal.coherence` within 1e-6 on a real EEG-like fixture. If parity proves stubborn after ~2 days, fall back: keep coherence Python-only for mobile v1 (most consumer NF protocols are single-band envelope/percentile) and document the gap.

## M1b — Equivalence as a CI gate (kill the drift risk)  ✅ DONE (commit c5b690d)

**Result:** `refrain-core/tools/check_equivalence.py` regenerates fixtures from the current Python evaluator (reusing `gen_fixtures.py`) then runs the Rust equivalence tests — one command, validated locally (PASS). A `rust-equivalence` job was added to `.github/workflows/test.yml` (flagged: needs a real CI run to confirm toolchain wiring). Original plan below.

**Files:**
- Modify: `.github/workflows/test.yml` (add a `rust-equivalence` job)
- Create: `refrain-core/tools/check_equivalence.py` (regenerates fixtures from the *current* Python evaluator, runs the Rust core, asserts equivalence — fails CI on drift)

- [ ] **Step 1:** Add a CI job that installs the Rust toolchain, runs `gen_fixtures.py` against the current Python, builds the PyO3 module (`maturin develop --release`), runs `cargo test`, and runs the three-way `bench` equivalence (`python -m bench equivalence`).
- [ ] **Step 2:** Make the job **required**. Now any change to a Python primitive that isn't mirrored in Rust (or vice versa) fails CI — drift is impossible to merge.
- [ ] **Step 3: Commit.**

**Exit criteria:** a deliberately-introduced 1-sample discrepancy in either implementation turns CI red.

## M2 — Complete core parity (→ remaining primitives)

**M2a DONE (commit 35cb87b + 1535301):** control-ref resolution landed — `ir_json.py` resolves control defaults (reused `control_defaults` extracted from `_resolve_controls`, + `_substitute_controls`) so `realistic_smr` (3 bands, percentile+absolute thresholds, dwell+all_of, sigmoid, conditional outputs) reproduces at machine precision (worst 1.35e-13) with **no Rust change**. Corpus now 7/7 (micro_01–06 + realistic_smr). Emitter made deterministic (sorted `upstream`).

**M2b DONE (commit becabcd):** ported bipolar montage, auto_range, differentiate, inside, and **fixed bandpower** (was mis-aliased to plain bandpass). Also fixed a latent derive-ordering bug (derives now evaluate in `topological_order`, not alphabetical). rectify/any_of/linear were already covered. New test protocols micro_07/08; all machine-precision.

**M2c DONE (commit dec6561):** inhibit actions (mute/freeze hangover gate, flag no-op) + output muting (`muted` OR-fold, event `&~muted` / value `where(muted,0,clamped)`). micro_09 inhibit protocol trips genuinely (50.7% samples muted) and matches to ~5e-15.

**→ SIGNAL-PATH PARITY COMPLETE:** the Rust core reproduces the full evaluator (montage → derives → thresholds → inhibits → reward → outputs → events) at machine precision across the corpus. 14 Rust tests, 401 Python tests, drift gates green.

**M3b/c REMAINING:** `set_control` + `last_taps` over the bindings; then `Evaluator` delegation + full Python suite.

**Files:** `src/refrain/ir_json.py` (control-default resolution), `refrain-core/src/eval.rs` + `dsp.rs` (remaining primitives), `tools/gen_fixtures.py`, `tests/equivalence.rs`.

- [ ] **Control-ref resolution (unblocks `realistic_smr`).** In `ir_json.py`, reuse `Evaluator._resolve_controls`-equivalent logic (extract it to a module-level `control_defaults(ir)` in `eval_.py` and call from both) to substitute control defaults before `_classify_call`, so percentile `target_pct` becomes a literal and `window_samples` bakes. Emit `control_ref` nodes *with* their resolved default so live retuning stays expressible later. TDD: a test asserting `realistic_smr`'s baked percentile window + resolved target_pct.
- [ ] **Remaining primitives in Rust** (each: golden-vector TDD against the Python evaluator): `auto_range`, `differentiate`, `rectify`, `inside`, `any_of`, `linear`, `bandpower` (biquad + rolling mean), and the **inhibit actions** `mute`/`freeze`/`flag` (hangover state machines, `primitive_impls.py:592-656`) with the output-gating/mute combination logic from `eval_.py`.
- [ ] **Lifecycle parity:** warmup state + output suppression, `set_control` routing to live-tunable impls (percentile target_pct, smooth tau, sigmoid midpoint), `last_taps()` parity.
- [ ] **Full corpus:** generate fixtures for all examples + bench protocols incl. `realistic_smr`; `cargo test` green across the corpus.
- [ ] **Commit per primitive.**

**Exit criteria:** every primitive in `docs/PRIMITIVES.md` that the Python evaluator implements is reproduced in Rust within 1e-6 across the full corpus (coherence from M1a included).

## M3 — PyO3 desktop-first: Python delegates to the Rust core (SHIP #1)

**M3a DONE (commit d0592b6):** the streams→`list[Event]` stage + warmup lifecycle are ported. Rust `step_chunk_events` reproduces the Python evaluator's feedback events exactly — micro_05 128/128 (max value diff 8.8e-15), realistic_smr 256/256 (exact), timestamps exact. PyO3 exposes `start`/`stop`/`step_chunk_events` + an `Event` pyclass; the streams `step_chunk` API is unchanged (shared private `eval_chunk()`, no duplicate interpreter). 10/10 Rust tests, 0 warnings, drift gate PASS. **The Rust core now has the full feedback pipeline (streams + events).** Remaining M3b/c below.

**M3b/c REMAINING:** `set_control` + `last_taps` parity over PyO3; then `Evaluator.live(..., backend="rust")` delegation + run the full Python suite through it.

**Why:** ship the lowest-risk target first *and* collapse to one implementation. After this, `refrain.Evaluator` is a thin wrapper over `refrain-core`; the pure-Python evaluator becomes the reference/spec oracle used only by the equivalence gate.

**Files:** `refrain-core/` (PyO3 surface: `RustEvaluator` gains `set_control`, `last_taps`, lifecycle), `src/refrain/eval_.py` (optional `backend="rust"` path), `pyproject.toml` (maturin build), CI wheel-build job.

- [ ] Expand the PyO3 API to the full embedding surface (`live`/`start`/`step_chunk`/`set_control`/`stop`/`last_taps`) returning the same types the Python `Evaluator` does.
- [ ] Add `Evaluator.live(..., backend="rust")` that constructs the Rust core from emitted IR-JSON and forwards calls; keep `backend="python"` as the oracle. Default stays Python until the gate is green for the whole corpus, then flip the default.
- [ ] Build/publish wheels (`maturin`) for macOS/Windows/Linux in CI.
- [ ] **Exit criteria:** `backend="rust"` passes the entire existing Python test suite (350+ tests) and the bench equivalence; desktop wheels build in CI.

## M4 — Mobile bindings (the app: iOS/Swift + Android/Kotlin)

**M4 in-sandbox + CI wiring DONE (commits 563fc44, de43a63):** uniffi interface (`src/mobile.rs`, feature-gated) wraps the evaluator's event API; Swift + Kotlin bindings generated and committed under `refrain-core/bindings/`; staticlibs cross-compile for `aarch64-apple-ios` + `aarch64-linux-android` with `--features uniffi`; a feature-gated test proves the wrapper emits byte-identical events to the evaluator. Packaging home (owner's choice): **Mac-only job in THIS repo** — `.github/workflows/mobile.yml` has `bindings-verify` (Linux, Mac-free drift gate via `tools/check_bindings.py`, CI-validatable), `ios-xcframework` (`self-hosted, macOS` = the farm Mac mini, **unvalidated**), `android-aar` (Linux + NDK, **unvalidated** — NDK provisioning + Gradle AAR module still needed). **Remaining for the farm:** confirm macOS runner labels, provision Android NDK + cargo-ndk, add the Gradle AAR module; then enable the two flagged jobs.

**Files:** `refrain-core/src/udl` or `#[uniffi::export]` interface, `refrain-core/Cargo.toml` (uniffi), `build.rs`, packaging scripts.

- [ ] Define a uniffi interface over the evaluator: `RefrainCore::new(ir_json: String, sample_rate_hz: f64, channel_names: Vec<String>)`, `step_chunk(chunk: Vec<f64>, n_channels: u32) -> Vec<Event>` (or a `HashMap<String, Vec<f64>>` of streams), `set_control(name, value)`, `last_taps() -> HashMap<String, f64>`. Flat `Vec<f64>` + `n_channels` (uniffi has no 2-D type).
- [ ] Generate Swift bindings; build an **xcframework** wrapping the `aarch64-apple-ios` (+ simulator `aarch64-apple-ios-sim`) staticlib. Smoke test in a tiny SwiftPM target: load an IR-JSON asset, push synthetic chunks, assert events.
- [ ] Generate Kotlin bindings; build the `aarch64-linux-android` (+ `armeabi-v7a`, `x86_64`) `.so` via **cargo-ndk**, package an **AAR**. Smoke test in a minimal Android unit test.
- [ ] CI cross-build matrix produces the xcframework + AAR as artifacts.
- [ ] **Exit criteria:** Swift and Kotlin smoke tests run an IR-JSON protocol end-to-end on-target; artifacts published from CI.

## M5 — Validation, schema & reproducibility docs

**Files:** `docs/IR-JSON.md` (new), `docs/spec/` conformance vectors (new), `docs/SPEC.md`/`DESIGN-NOTES.md` (IR-coefficient-baking note), `docs/RUST-CORE-HOST-BRIEF.md` (already drafted).

- [ ] **IR-JSON schema spec** (`docs/IR-JSON.md`): versioned wire contract — node discriminators, baked-coefficient fields, the sample-rate and channel-layout-are-runtime-inputs rules. This is the new spec artifact; flag in CHANGELOG.
- [ ] **Conformance suite:** publish the committed golden vectors (input + IR-JSON + reference streams) as the conformance set any future runtime must pass; document the tolerance methodology.
- [ ] **CRED-nf reproducibility doc:** state that the signal-to-feedback transformation is one Rust core compiled everywhere, validated by the conformance suite — reproducibility by construction.
- [ ] **Spec updates in the same change:** record the coefficient-baking IR enhancement in DESIGN-NOTES (behavior-neutral).
- [ ] **Exit criteria:** a third party can take the IR-JSON spec + conformance vectors and validate any runtime independently.

---

## Engine path (Unity/Unreal) — deferred, not dropped

App is mobile, so the C-ABI (`cbindgen`) + P/Invoke (C#) / FFI (C++) path is **out of scope for v1** but the design holds: the uniffi interface from M4 sits over a C-compatible layer, and a thin `extern "C"` shim (create/step/free with out-params) is a 1–2 day add when an engine target appears. Note in `docs/IR-JSON.md`.

## Top risks (carried) + current status

1. **Coherence Welch parity** — *being retired in M1a (first).* Fallback: Python-only coherence for mobile v1.
2. **Drift between implementations** — *retired by M1b (CI gate) + M3 (single impl).*
3. **Coefficient-baking** — *validated in M0; extended to control-refs in M2.*
4. **f64 throughout / NaN-inf semantics** — guard in tests (the `/`-finite rule and strict f64 are already in the Rust core).

## New dependencies to flag (per repo policy)

Rust crates: `realfft` (M1a), `uniffi` (M4). Already in PoC: `serde`, `serde_json`, `pyo3`, `rust-numpy`. Tooling: `maturin`, `cargo-ndk`, xcframework tooling. **No** `ndarray-linalg`/BLAS/LAPACK (no eigendecomposition needed).
