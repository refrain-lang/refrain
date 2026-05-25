# refrain-core

A portable Rust implementation of Refrain's **real-time evaluator** — the
signal-to-feedback transformation. It is the *one canonical* runtime, callable
from Python (desktop/tooling), Swift, and Kotlin (mobile), reproducing the
Python reference evaluator (`src/refrain/eval_.py`) to floating-point tolerance.

> Status: production-track PoC on branch `worktree-rust-core-poc` / PR #13.
> M3c is done — `Evaluator.live(..., backend="rust")` delegates to this core,
> the behavioral suite is green under `REFRAIN_EVAL_BACKEND=rust` (49 passed /
> 17 skipped), and the dual-backend drift gate is live in CI. Remaining: M5
> (schema/conformance docs). See
> `docs/superpowers/plans/2026-05-24-rust-core-production-roadmap.md`.

## What it is (and isn't)

**Is:** a chunk-driven evaluator. You give it a compiled protocol
(**IR-JSON**, emitted by the Python front-end `refrain.ir_json`) plus the
host's runtime sample rate and channel layout; it consumes `(n_samples ×
n_channels)` chunks and returns **feedback events** (and, for validation,
streams + observation taps).

**Isn't:** it does not parse `.refrain` files, design filters, acquire EEG, or
render anything. The Python front-end (parser → resolver) is the single source
of truth for *authoring*; acquisition + rendering belong to the host. Filter
**coefficients are baked into the IR-JSON** by Python, so this core only *runs*
deterministic recurrences/convolutions — it never reimplements SciPy.

## Architecture

```
.refrain ──(Python: parse → resolve)──► IR  ──(refrain.ir_json, coeffs baked)──► IR-JSON
                                                                                    │
                                                              ┌─────────────────────┘
                                                              ▼
   host chunks (f64) ─────────────► refrain-core (this crate): deserialize → evaluate
                                                              │
                                              ┌───────────────┼───────────────┐
                                              ▼               ▼               ▼
                                           events          taps           streams
                                       (feedback out)  (clinician obs)  (validation)
```

Two facts the host must honor (enforced by the wire format, see the host brief):
1. **Sample rate is baked.** Coefficients are designed for the rate the runtime
   will use; ship the IR-JSON variant matching your amp's rate and pass that
   same rate in. (The protocol only declares a *minimum*.)
2. **`channels` you pass is the physical acquisition layout** (incl. reference
   electrodes like A1/A2), not the protocol's required channels.

## Module map (`src/`)

| File | Responsibility |
|---|---|
| `ir.rs` | serde structs mirroring IR-JSON (`Protocol`, the `Expr` tagged enum, `Coeffs`, `Session`/`Phase`, `Threshold`, `Inhibit`, `Reward`, `ControlDecl`) |
| `dsp.rs` | streaming stages/trackers: `Biquad` (DF2T SOS cascade), `HilbertFir`, `Magnitude`, `Smooth`, `Percentile`, `AutoRange`, `Differentiate`, `Bandpower`, `Dwell`, `InhibitGate`; `ControlCell` for live retuning |
| `coherence.rs` | `WelchMsc` — band-averaged magnitude-squared coherence (the only place `realfft` is used) |
| `eval.rs` | the evaluator: `Val`, `CNode` compiled tree, `Montage`, `eval_chunk`, `step_chunk` (streams), `step_chunk_events` (feedback), `last_taps`, `set_control`, lifecycle (`start`/`stop`/warmup), and the `build_*` compilers |
| `python.rs` | PyO3 binding (`RustEvaluator` + `Event`), feature `python` |
| `mobile.rs` | uniffi binding (`RefrainCore` + `Event`), feature `uniffi` |

## Primitive coverage (matches Refrain v0.1 standard library)

Montage: `referential`, `bipolar`. Spectral: `bandpass`, `hilbert`, `bandpower`,
`coherence`. Time-series: `magnitude`, `rectify`, `smooth`, `differentiate`.
Statistics: `percentile` (+ threshold), `auto_range`. Thresholds: `absolute`,
`percentile`. Conditions: `above`, `below`, `inside`, `all_of`, `any_of`.
Events: `dwell`. Mappings: `sigmoid`, `linear`. Expression: `/` (and `+`,`-`,`*`)
binop, conditional `? :`. Inhibits: `mute`, `freeze`, `flag` + output muting.
Live controls: `set_control` (percentile target_pct, smooth tau, sigmoid midpoint),
in-place, state-preserving. Observation: `last_taps`.

## Validation (golden-vector parity)

The core's behavior is pinned to the Python evaluator by **golden vectors**:
`tools/gen_fixtures.py` runs each protocol through the Python evaluator and
records `(input, IR-JSON, output streams, events, taps)`; the Rust tests replay
and assert equivalence within `atol=1e-6, rtol=1e-4` (the bench harness
tolerance). Observed worst case across the corpus: **~1e-13** (machine
precision). Boolean event/condition streams match exactly.

Corpus (`tests/fixtures/`): `micro_01`..`micro_09` (passthrough, bandpass,
envelope, threshold, reward, coherence, ilf/bipolar, bandpower, inhibit) +
`realistic_smr` (full clinical: 3 bands, dwell+all_of, sigmoid, conditional
outputs, control-ref thresholds) + a `set_control` scenario.

## Build, test, regenerate

```bash
# from refrain-core/  (needs:  . "$HOME/.cargo/env")
cargo test                       # all golden-vector tests (equivalence, events, taps, set_control, ir_deser)
cargo build --all-targets        # 0 warnings expected

# Python (PyO3) binding — desktop/tooling:
VIRTUAL_ENV=../.venv PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 ../.venv/bin/maturin develop --release

# Mobile bindings (Swift/Kotlin) — see bindings/README.md:
cargo build --features uniffi
#   then uniffi-bindgen + cross-compile (aarch64-apple-ios / aarch64-linux-android)

# Three-way latency (Rust vs Python-evaluator vs idiomatic NumPy):
PYTHONPATH=.. ../.venv/bin/python tools/latency.py   # run from refrain-core/.. with PYTHONPATH=worktree root
```

### Drift gates (run from the worktree root, with cargo on PATH)

```bash
PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py
PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_bindings.py
```

- `check_equivalence.py` — four-step gate: (1) regenerates fixtures from the
  *current* Python evaluator, (2) runs the full Rust suite, (3) builds the
  `refrain_core` wheel from current Rust source, (4) runs the behavioral
  evaluator suite (`tests/test_eval_*.py`) under `REFRAIN_EVAL_BACKEND=rust`
  (dual-backend parity). Fails on any Python↔Rust drift at the golden-vector
  or behavioral level (this is what keeps "one canonical transformation" honest).
- `check_bindings.py` — cross-compiles the iOS/Android staticlibs and
  regenerates the Swift/Kotlin bindings, failing if the committed bindings are
  stale.

CI: `.github/workflows/mobile.yml` runs `bindings-verify` (Linux), and the
`ios-xcframework` (self-hosted macOS) + `android-aar` (Linux+NDK) packaging jobs
(the latter two are flagged UNVALIDATED until the farm runners are provisioned).

## Consuming it

- **Python:** `import refrain_core; e = refrain_core.RustEvaluator(ir_json, sr, channels); e.start(False); e.step_chunk_events(chunk)` (and `set_control`, `stop`, `last_taps`). Or drop it into the bench `ChunkedRunner` via `step_chunk`.
- **Swift / Kotlin (mobile app):** see `bindings/` and the integration brief
  `docs/RUST-CORE-HOST-BRIEF.md` — hand that to the app's agent.

## Performance

Per 32-sample chunk (P50), Rust vs the Python evaluator: envelope **2.4µs vs
32µs**, percentile-threshold **282µs vs 3022µs**, reward **580µs vs 6231µs** —
~10–13× faster (the percentile path uses introselect, not a full sort, to match
NumPy's complexity). Latency reproducible via `tools/latency.py`.

## Dependencies

`serde`, `serde_json`, `realfft` (coherence FFT), `indexmap` (preserves output
declaration order). Optional: `pyo3` + `numpy` (feature `python`), `uniffi`
(feature `uniffi`). No BLAS/LAPACK/ndarray-linalg — no eigendecomposition is
needed. f64 throughout.
