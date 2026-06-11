# `autocorr` Primitive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-input streaming primitive `autocorr(lag, window)` that computes the rolling lag-k Pearson autocorrelation of a stream — the "critical slowing down" indicator for the flutter-cue protocol — with full Python↔Rust parity.

**Architecture:** `autocorr` is a single-input pipeline stage (like `bandpower`/`auto_range`): the input is threaded implicitly, `window` and `lag` are baked to sample counts at the target rate, and the rolling lag-k Pearson autocorrelation is computed O(window)-per-sample (mirroring `bandpower`'s rolling mean). Python `AutocorrImpl` is the reference; the Rust `Autocorr` `Stage` mirrors it byte-for-byte; a golden fixture gates parity.

**Tech Stack:** Python 3.10+ (NumPy, SciPy), Rust (the `refrain-core` crate), JSON Schema, pytest, cargo test.

**Plan note — deliberate spec trim:** the design spec lists a `detrend: bool = true` param. v1 implements the minimal `autocorr(lag, window)` (mean-centered windowed Pearson — the standard estimator); detrending of slow trends is handled upstream in the protocol (subtract a long `smooth`). A `detrend` param is tracked for a follow-up. This keeps the primitive minimal and exactly parity-able.

**This is one of three sequenced plans** from `docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md`: (1) this `autocorr` primitive, (2) band fan-out, (3) the `flutter_cue.refrain` protocol + gap RFC.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `src/refrain/primitives.py` | `_AUTOCORR` PrimitiveSpec + registry entry | Modify |
| `src/refrain/primitive_impls.py` | `AutocorrImpl` streaming class + `make_filter_impl` branch | Modify |
| `src/refrain/ir_json.py` | `_extract_coeffs` autocorr branch (bake `window_samples`, `lag_samples`) | Modify |
| `src/refrain/cost.py` | autocorr cost driver | Modify |
| `src/refrain/eval_.py` | static-arg massage (`window`→`window_ms`, `lag`→`lag_ms`) | Modify |
| `refrain-core/src/dsp.rs` | `Autocorr` struct + `Stage` impl | Modify |
| `refrain-core/src/eval.rs` | `build_stage` `"autocorr"` arm + pipeline-callee allowlist | Modify |
| `refrain-core/schema/ir-json-v0.1.schema.json`, `…v0.2…` | add `lag_samples` to `Coeffs` | Modify |
| `bench/protocols/micro_07_autocorr.refrain` | parity corpus protocol | Create |
| `refrain-core/tests/equivalence.rs` | Rust equivalence test entry | Modify |
| `tests/test_primitive_impls.py` | Python unit tests | Modify |
| `docs/PRIMITIVES.md`, `docs/SPEC.md` | docs | Modify |

---

## Reference: the estimator (both backends compute this identically)

Over the trailing window buffer `b = [x_0 … x_{n-1}]` (n = current buffer length, capacity = `window_samples`), with lag `L = lag_samples`:

```
warm-up:  if n < L + 2  ->  0.0
mean   =  (Σ_i b_i) / n
num    =  Σ_{i=L}^{n-1} (b_i - mean) * (b_{i-L} - mean)
den    =  Σ_{i=0}^{n-1} (b_i - mean)^2
result =  0.0 if den == 0 else clamp(num / den, -1.0, 1.0)
```

One value emitted per input sample. O(window) per sample (same complexity class as `bandpower`'s rolling mean).

---

### Task 1: Python `AutocorrImpl` + registration

**Files:**
- Modify: `src/refrain/primitives.py` (after the `_COHERENCE` spec, ~line 396; registry ~line 699)
- Modify: `src/refrain/primitive_impls.py` (new class near `CoherenceImpl` ~line 819; factory branch ~line 1028)
- Modify: `src/refrain/eval_.py` (static-arg massage ~line 224)
- Test: `tests/test_primitive_impls.py`

- [ ] **Step 1: Write the failing unit tests**

Add to `tests/test_primitive_impls.py` (mirror the `coherence` test block ~line 423). `SR` is the module's sample-rate constant (256.0); follow the file's existing import of `AutocorrImpl`.

```python
def test_autocorr_warmup_returns_zero():
    """Before lag+2 samples accumulate, autocorr returns 0.0."""
    impl = AutocorrImpl(window_ms=1000.0, lag_ms=None, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(np.array([1.0, 2.0]))  # only 2 samples, lag 1 -> n<lag+2 false at n=2? n=2,lag=1 -> need n>=3
    assert out[0] == 0.0 and out[1] == 0.0

def test_autocorr_white_noise_near_zero():
    """Lag-1 autocorrelation of white noise is ~0."""
    rng = np.random.default_rng(0)
    x = rng.standard_normal(4000)
    impl = AutocorrImpl(window_ms=1000.0, lag_ms=None, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(x)
    assert abs(out[-1]) < 0.15  # last (warm) sample

def test_autocorr_smooth_signal_near_one():
    """A slowly-varying (highly autocorrelated) signal -> lag-1 ac near 1."""
    t = np.arange(4000) / SR
    x = np.sin(2 * np.pi * 0.5 * t)  # 0.5 Hz: adjacent samples nearly identical
    impl = AutocorrImpl(window_ms=1000.0, lag_ms=None, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(x)
    assert out[-1] > 0.9

def test_autocorr_bounded_and_constant_input_is_zero():
    """Output in [-1,1]; constant input (den=0) -> 0.0 not NaN."""
    impl = AutocorrImpl(window_ms=1000.0, lag_ms=None, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(np.full(1000, 3.0))
    assert np.all(out == 0.0)

def test_autocorr_streaming_matches_single_shot():
    """Chunked stepping == one-shot over the same samples (persistent buffer)."""
    rng = np.random.default_rng(1)
    x = rng.standard_normal(2000)
    a = AutocorrImpl(window_ms=500.0, lag_ms=None, lag_samples=2, sample_rate_hz=SR)
    b = AutocorrImpl(window_ms=500.0, lag_ms=None, lag_samples=2, sample_rate_hz=SR)
    one = a.step(x)
    chunked = np.concatenate([b.step(x[i:i+64]) for i in range(0, len(x), 64)])
    np.testing.assert_allclose(one, chunked, atol=1e-12)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/jcroall/git/refrain/refrain && python -m pytest tests/test_primitive_impls.py -k autocorr -q`
Expected: FAIL — `ImportError` / `cannot import name 'AutocorrImpl'`.

- [ ] **Step 3: Implement `AutocorrImpl`**

In `src/refrain/primitive_impls.py`, near `CoherenceImpl`, add (use the file's existing `deque`, `np`, and `PrimitiveImpl` imports; copy the Apache header style of the file):

```python
class AutocorrImpl(PrimitiveImpl):
    """`autocorr(lag, window)` — rolling lag-k Pearson autocorrelation.

    Single-input pipeline stage. Maintains a trailing window of `window_samples`
    and emits, per input sample, the lag-`k` autocorrelation of the buffer
    ending at that sample. Returns 0.0 until `lag + 2` samples accumulate
    (warm-up) and 0.0 for a constant window (zero variance). Output in [-1, 1].
    This is the "critical slowing down" early-warning indicator.
    """

    def __init__(self, *, window_ms, lag_ms, lag_samples, sample_rate_hz):
        ws = max(1, int(round(window_ms / 1000.0 * sample_rate_hz)))
        if lag_ms is not None:
            ls = max(1, int(round(lag_ms / 1000.0 * sample_rate_hz)))
        else:
            ls = max(1, int(lag_samples))
        if ws < ls + 2:
            raise ValueError(
                f"autocorr window ({ws} samples) must be >= lag+2 ({ls + 2})"
            )
        self.window_samples = ws
        self.lag = ls
        self._buf: deque[float] = deque(maxlen=ws)

    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(len(x), dtype=float)
        buf = self._buf
        lag = self.lag
        for i, v in enumerate(x):
            buf.append(float(v))
            n = len(buf)
            if n < lag + 2:
                out[i] = 0.0
                continue
            arr = np.fromiter(buf, dtype=float, count=n)
            mean = arr.mean()
            d = arr - mean
            den = float(np.dot(d, d))
            if den == 0.0:
                out[i] = 0.0
                continue
            num = float(np.dot(d[lag:], d[:-lag]))
            out[i] = max(-1.0, min(1.0, num / den))
        return out
```

- [ ] **Step 4: Add the factory branch in `make_filter_impl`**

In `src/refrain/primitive_impls.py`, in `make_filter_impl` (the `if callee == "coherence":` block ~line 1028), add a sibling:

```python
    if callee == "autocorr":
        return AutocorrImpl(
            window_ms=float(static_args.get("window_ms", 1000.0)),
            lag_ms=static_args.get("lag_ms"),
            lag_samples=int(static_args.get("lag", 1)),
            sample_rate_hz=sample_rate_hz,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_primitive_impls.py -k autocorr -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Register `_AUTOCORR` spec**

In `src/refrain/primitives.py`, after `_COHERENCE` (~line 396):

```python
def _autocorr_output(args: dict[str, ResolvedArg]) -> StreamType:
    # Pearson autocorrelation is dimensionless in [-1, 1].
    return scalar_stream(DIMENSIONLESS)

_AUTOCORR = PrimitiveSpec(
    name="autocorr",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("__input__", "stream_ref"),
                ParamSpec("lag", "duration_or_int"),
                ParamSpec("window", "duration"),
            ),
            output=_autocorr_output,
        ),
    ),
    budget=ResourceBudget(state_kb=8, worst_case_us=400),
    doc="Rolling lag-k Pearson autocorrelation of a stream over a sliding "
        "window. Returns scalar in [-1, 1]; 0.0 during warm-up. The "
        "critical-slowing-down early-warning indicator.",
)
```

Then add `_AUTOCORR` to the `_REGISTRY` dict (~line 699), keyed `"autocorr"`.

> **Note on `lag` param type:** if a `"duration_or_int"` param kind does not already exist in `primitives.py`, reuse the existing `"duration"` kind and require `lag` as a duration (the protocol passes `lag: <ms>`); drop the int path from `AutocorrImpl` (`lag_ms` only). Check `ParamSpec` kinds in `primitives.py` and pick the existing kind that admits a duration; do NOT invent a new kind in this task.

- [ ] **Step 7: Static-arg massage in `eval_.py`**

In `src/refrain/eval_.py`, beside the coherence massage (~line 224 `if callee == "coherence" and "window" in out:`), add:

```python
    if callee == "autocorr":
        if "window" in out:
            out["window_ms"] = out.pop("window")
        if "lag" in out:
            # duration lag -> lag_ms; integer lag stays as `lag`
            lag_v = out["lag"]
            if isinstance(lag_v, (int, float)) and not isinstance(lag_v, bool):
                pass  # plain int sample-lag: leave as `lag`
            else:
                out["lag_ms"] = out.pop("lag")
```

> If Step 6 chose duration-only `lag`, simplify this to always `out["lag_ms"] = out.pop("lag")`.

- [ ] **Step 8: Run the full primitive + eval suites**

Run: `python -m pytest tests/test_primitive_impls.py -q`
Expected: PASS (existing + 5 new).

- [ ] **Step 9: Commit**

```bash
git add src/refrain/primitives.py src/refrain/primitive_impls.py src/refrain/eval_.py tests/test_primitive_impls.py
git commit -m "feat(primitives): add autocorr (rolling lag-k autocorrelation) — Python ref impl"
```

---

### Task 2: IR-JSON coeff baking + schema

**Files:**
- Modify: `src/refrain/ir_json.py` (`_extract_coeffs` ~lines 103–131)
- Modify: `src/refrain/cost.py` (driver list ~line 159)
- Modify: `refrain-core/schema/ir-json-v0.1.schema.json`, `refrain-core/schema/ir-json-v0.2.schema.json` (`Coeffs` props)
- Test: `tests/` (new emission test) — use the existing IR-JSON emission test file pattern

- [ ] **Step 1: Write the failing emission test**

Find the existing IR-JSON test (grep `test` dir for `ir_json` / `_bake_coeffs`); add a test asserting an `autocorr` call emits baked coeffs. Pattern:

```python
def test_autocorr_emits_baked_coeffs():
    src = '''
    protocol "ac" {
      meta { version="0.1.0" evidence="demo" description="x" }
      requires { sample_rate=">= 256 Hz" channels=["Cz"] }
      input "raw" { montage = referential(active:"Cz", reference:"device") }
      derive "env" { from="raw" pipeline=[ bandpass(band:(8 Hz,12 Hz)), hilbert(), magnitude() ] }
      derive "ac1" { from="env" pipeline=[ autocorr(lag: 4, window: 1 s) ] }
      output { audio_gain = 0 }
    }'''
    ir = resolve_source(src)                      # use the test module's existing resolve helper
    j = emit_ir_json(ir, sample_rate_hz=256.0)    # use the existing emit entrypoint
    node = find_call(j, "autocorr")               # helper or inline walk
    assert node["coeffs"]["window_samples"] == 256
    assert node["coeffs"]["lag_samples"] == 4
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/ -k autocorr_emits -q`
Expected: FAIL — `coeffs` is `None` (no autocorr branch in `_extract_coeffs`).

- [ ] **Step 3: Add the `_extract_coeffs` branch**

In `src/refrain/ir_json.py`, in `_extract_coeffs` (the function that special-cases `CoherenceImpl` ~line 103), add:

```python
    if isinstance(impl, AutocorrImpl):
        return {
            "window_samples": impl.window_samples,
            "lag_samples": impl.lag,
        }
```

Ensure `AutocorrImpl` is imported at the top of `ir_json.py` alongside `CoherenceImpl`.

- [ ] **Step 4: Add `lag_samples` to the schema `Coeffs`**

In BOTH `refrain-core/schema/ir-json-v0.1.schema.json` and `…-v0.2.schema.json`, in `$defs.Coeffs.properties`, add beside `window_samples`:

```json
        "lag_samples": { "type": "integer" },
```

- [ ] **Step 5: Add the cost driver**

In `src/refrain/cost.py`, after the coherence block (~line 169), add (mirror it):

```python
    # --- autocorr (uncalibrated) ---------------------------------------------
    ac_calls = [c for e in _all_stream_exprs(ir) for c in _iter_calls(e)
                if c.callee == "autocorr"]
    if ac_calls:
        win_samples = sum(_window_ms(c, 1000.0) / 1000.0 * sr for c in ac_calls)
        drivers.append(CostDriver(
            name=f"autocorr ({len(ac_calls)})",
            detail=f"{win_samples:,.0f} window-samples (rolling lag-k Pearson)",
            us_per_sample=K_COHERENCE_US_PER_WINDOW_SAMPLE * win_samples,
            calibrated=False,
        ))
```

> If `K_COHERENCE_US_PER_WINDOW_SAMPLE` is the only window-stat constant, reuse it; otherwise define `K_AUTOCORR_US_PER_WINDOW_SAMPLE` near the other `K_*` constants with the same value and use it here.

- [ ] **Step 6: Run emission test + schema validation**

Run: `python -m pytest tests/ -k "autocorr_emits or ir_json or schema" -q`
Expected: PASS. (If a schema-validation test iterates golden `.ir.json`, it still passes — `lag_samples` is additive.)

- [ ] **Step 7: Commit**

```bash
git add src/refrain/ir_json.py src/refrain/cost.py refrain-core/schema/ir-json-v0.1.schema.json refrain-core/schema/ir-json-v0.2.schema.json tests/
git commit -m "feat(ir-json): bake autocorr coeffs (window_samples, lag_samples) + schema + cost"
```

---

### Task 3: Rust `Autocorr` `Stage` + dispatch

**Files:**
- Modify: `refrain-core/src/dsp.rs` (new `Autocorr` struct near `Bandpower` ~line 439)
- Modify: `refrain-core/src/eval.rs` (`build_stage` arm ~line 1872; pipeline-callee allowlist ~line 1754; import line 16)
- Test: `refrain-core/src/dsp.rs` `#[cfg(test)]` module (mirror existing dsp tests)

- [ ] **Step 1: Write the failing Rust unit test**

In `refrain-core/src/dsp.rs`, in the `#[cfg(test)] mod tests` block, add (mirror existing `Bandpower`/`AutoRange` tests):

```rust
#[test]
fn autocorr_constant_is_zero_then_smooth_is_high() {
    // Constant input -> 0.0 (zero variance, not NaN).
    let mut ac = Autocorr::new(256, 1);
    let out = ac.step(&vec![3.0_f64; 300]);
    assert!(out.iter().all(|&v| v == 0.0));

    // Slowly-varying input -> lag-1 autocorrelation near 1.
    let mut ac2 = Autocorr::new(256, 1);
    let x: Vec<f64> = (0..2000).map(|i| (2.0 * std::f64::consts::PI * 0.5 * i as f64 / 256.0).sin()).collect();
    let out2 = ac2.step(&x);
    assert!(*out2.last().unwrap() > 0.9);
}

#[test]
fn autocorr_warmup_zero() {
    let mut ac = Autocorr::new(256, 1);
    let out = ac.step(&[1.0, 2.0]); // n<lag+2 for first samples
    assert_eq!(out[0], 0.0);
}
```

- [ ] **Step 2: Run it to verify it fails**

Run (cargo lives in `~/.cargo/bin`, NOT on default PATH):
`cd /Users/jcroall/git/refrain/refrain/refrain-core && PATH="$HOME/.cargo/bin:$PATH" cargo test autocorr 2>&1 | tail -20`
Expected: FAIL — `cannot find type Autocorr`.

- [ ] **Step 3: Implement the `Autocorr` `Stage`**

In `refrain-core/src/dsp.rs`, near `Bandpower` (~line 439), add (match the file's `Stage`/`Signal`/`VecDeque` patterns and Apache header style):

```rust
/// `autocorr(lag, window)` — rolling lag-`k` Pearson autocorrelation over a
/// sliding window of `window_samples`. Emits one value per input sample;
/// 0.0 until `lag + 2` samples accumulate (warm-up) and 0.0 for a constant
/// window (zero variance). Mirrors `AutocorrImpl`. Output in [-1, 1].
pub struct Autocorr {
    cap: usize,
    lag: usize,
    buf: VecDeque<f64>,
}

impl Autocorr {
    pub fn new(window_samples: usize, lag_samples: usize) -> Self {
        let cap = window_samples.max(1);
        Autocorr { cap, lag: lag_samples.max(1), buf: VecDeque::with_capacity(cap) }
    }

    fn lag_autocorr(&self) -> f64 {
        let n = self.buf.len();
        if n < self.lag + 2 {
            return 0.0;
        }
        let mean = self.buf.iter().sum::<f64>() / n as f64;
        let d: Vec<f64> = self.buf.iter().map(|&v| v - mean).collect();
        let den: f64 = d.iter().map(|&v| v * v).sum();
        if den == 0.0 {
            return 0.0;
        }
        let mut num = 0.0;
        for i in self.lag..n {
            num += d[i] * d[i - self.lag];
        }
        (num / den).clamp(-1.0, 1.0)
    }
}

impl Stage for Autocorr {
    fn process(&mut self, input: Signal) -> Signal {
        let x = input.into_real();
        let mut out = Vec::with_capacity(x.len());
        for &v in &x {
            if self.buf.len() == self.cap {
                self.buf.pop_front();
            }
            self.buf.push_back(v);
            out.push(self.lag_autocorr());
        }
        Signal::Real(out)
    }
}
```

- [ ] **Step 4: Wire `build_stage` + the pipeline-callee allowlist**

In `refrain-core/src/eval.rs`:

(a) import `Autocorr` — extend the `use crate::dsp::{… AutoRange, Bandpower, …}` line (~line 16) to include `Autocorr`.

(b) in `build_stage` (~line 1872, beside `"bandpower"`), add:

```rust
        "autocorr" => {
            let c = need();
            Box::new(Autocorr::new(
                c.window_samples.expect("autocorr window_samples"),
                c.lag_samples.expect("autocorr lag_samples"),
            ))
        }
```

(c) in the pipeline-stage callee allowlist (~line 1754, the `| "smooth" | "auto_range" | "bandpower"` chain), add `| "autocorr"`.

(d) Confirm the Rust `Coeffs` struct (in `refrain-core/src/ir.rs`) has a `lag_samples: Option<usize>` field; if not, add it beside `window_samples` (with `#[serde(default)]`).

- [ ] **Step 5: Run the Rust tests**

Run: `cd /Users/jcroall/git/refrain/refrain/refrain-core && PATH="$HOME/.cargo/bin:$PATH" cargo test 2>&1 | tail -20`
Expected: PASS (existing + 2 new autocorr tests).

- [ ] **Step 6: Commit**

```bash
git add refrain-core/src/dsp.rs refrain-core/src/eval.rs refrain-core/src/ir.rs
git commit -m "feat(rust-core): Autocorr Stage (rolling lag-k autocorrelation) + dispatch"
```

---

### Task 4: Parity fixture + drift gate

**Files:**
- Create: `bench/protocols/micro_07_autocorr.refrain`
- Modify: `refrain-core/tools/gen_fixtures.py` (corpus list ~line 241)
- Modify: `refrain-core/tests/equivalence.rs` (new test ~line 105)

- [ ] **Step 1: Create the corpus protocol**

`bench/protocols/micro_07_autocorr.refrain` (mirror `micro_06_coherence.refrain`):

```refrain
protocol "micro_07_autocorr" {
  meta {
    version = "0.1.0"
    evidence = "demo"
    description = "Bench: rolling lag-k autocorrelation of an alpha envelope"
  }
  requires {
    sample_rate = ">= 256 Hz"
    channels = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "device")
  }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (8 Hz, 12 Hz), order: 4), hilbert(), magnitude() ]
  }
  derive "ac1" {
    from = "env"
    pipeline = [ autocorr(lag: 4, window: 1 s) ]
  }
  output {
    audio_gain = 0
  }
}
```

- [ ] **Step 2: Add it to the fixture corpus + regenerate goldens**

In `refrain-core/tools/gen_fixtures.py`, add `"micro_07_autocorr"` to the corpus list (~line 241, beside `"micro_06_coherence"`).

Run: `cd /Users/jcroall/git/refrain/refrain && python refrain-core/tools/gen_fixtures.py`
Expected: writes `refrain-core/tests/fixtures/micro_07_autocorr.ir.json` and `.io.json`.

Verify the IR-JSON has baked coeffs:
Run: `python -c "import json; d=json.load(open('refrain-core/tests/fixtures/micro_07_autocorr.ir.json')); print('lag_samples' in json.dumps(d) and 'OK')"`
Expected: `OK`.

- [ ] **Step 3: Add the Rust equivalence test**

In `refrain-core/tests/equivalence.rs` (beside `coherence_pair_equivalent` ~line 105):

```rust
#[test]
fn autocorr_equivalent() {
    run_protocol("micro_07_autocorr");
}
```

- [ ] **Step 4: Run the equivalence test (the parity gate)**

Run: `cd /Users/jcroall/git/refrain/refrain/refrain-core && PATH="$HOME/.cargo/bin:$PATH" cargo test autocorr_equivalent 2>&1 | tail -20`
Expected: PASS — Rust output matches the Python-emitted golden within tolerance.

- [ ] **Step 5: Run the full dual-backend drift gate**

Run: `cd /Users/jcroall/git/refrain/refrain && PATH="$HOME/.cargo/bin:$PATH" python refrain-core/tools/check_equivalence.py 2>&1 | tail -25`
Expected: PASS (builds the wheel, runs the parity corpus incl. `micro_07_autocorr`, and the backend-parametrized eval suite).

- [ ] **Step 6: Commit**

```bash
git add bench/protocols/micro_07_autocorr.refrain refrain-core/tools/gen_fixtures.py refrain-core/tests/equivalence.rs refrain-core/tests/fixtures/micro_07_autocorr.ir.json refrain-core/tests/fixtures/micro_07_autocorr.io.json
git commit -m "test(parity): autocorr Python<->Rust equivalence fixture + drift gate"
```

---

### Task 5: Documentation

**Files:**
- Modify: `docs/PRIMITIVES.md` (Statistics section, after `percentile`)
- Modify: `docs/SPEC.md` (primitive list, if it enumerates primitives)

- [ ] **Step 1: Add the PRIMITIVES.md entry**

In `docs/PRIMITIVES.md`, under "Statistics" (after `percentile`):

````markdown
### `autocorr`

```
autocorr(lag: duration | int, window: duration) -> stream<scalar in [-1, 1]>
```

Rolling lag-`k` Pearson autocorrelation of a stream over a sliding `window`,
emitted per sample. Returns `0.0` during warm-up (until `lag + 2` samples
accumulate) and `0.0` for a constant window (zero variance). This is the
**critical-slowing-down** early-warning indicator: as a system nears a
phase-state transition it recovers more slowly from perturbations, so its
lag-1 autocorrelation rises (Scheffer et al., *Nature* 2009; validated in EEG
by Maturana et al., *Nat. Commun.* 2020).

```refrain
derive "ac1" {
  from = "alpha_envelope"
  pipeline = [ autocorr(lag: 4, window: 1 s) ]
}
```

**Avoid the oversampling footgun.** At 256 Hz, lag-1-*sample* autocorrelation is
≈1 always (adjacent samples are nearly identical). Compute `autocorr` on a slow
signal (a band *envelope*) and/or set `lag` to a meaningful interval. Detrend
slow drifts upstream (subtract a long `smooth`) — `autocorr` mean-centers within
its window but does not remove a linear trend.
````

- [ ] **Step 2: Update SPEC.md if it enumerates primitives**

Grep `docs/SPEC.md` for the primitive enumeration (e.g. where `coherence` is listed) and add `autocorr` to it. If SPEC.md does not enumerate primitives, skip.

Run: `grep -n "coherence" docs/SPEC.md`

- [ ] **Step 3: Commit**

```bash
git add docs/PRIMITIVES.md docs/SPEC.md
git commit -m "docs: autocorr primitive reference"
```

---

## Self-review (completed by plan author)

- **Spec coverage:** Implements the spec's "New primitive — `autocorr`" section (signature, semantics, warm-up, streaming/cost, plug-in points, parity). The spec's `detrend` param is deliberately trimmed from v1 (documented in the header + PRIMITIVES note). Bands/fan-out/protocol/RFC are the other two plans — out of scope here.
- **Placeholder scan:** No TBD/TODO. Every code step shows code; every run step shows the command + expected result. Two steps flag a real conditional (the `lag` param-kind choice in Task 1 Step 6, and the `lag_samples` Rust IR field in Task 3 Step 4(d)) with the exact fallback — these are verify-then-do, not placeholders.
- **Type consistency:** `AutocorrImpl(window_ms, lag_ms, lag_samples, sample_rate_hz)` and attributes `window_samples`/`lag` are used identically across Tasks 1–4. Baked coeff keys `window_samples`/`lag_samples` match across `_extract_coeffs` (Py), the schema, and `Autocorr::new` (Rust). `Autocorr::new(window_samples, lag_samples)` signature is consistent in dsp.rs and the eval.rs dispatch.
```
