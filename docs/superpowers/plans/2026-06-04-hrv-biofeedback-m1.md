# HRV Biofeedback M1 (Asks 1, 2, 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the non-breaking M1 bundle of the Coherence Recorder HRV request — a low-latency envelope option (Ask 1), cross-session seed/export of adaptive tracker state (Ask 2), and a first-class `passthrough()` montage (Ask 4) — in both the Python evaluator and the Rust core, with Python↔Rust parity preserved.

**Architecture:** All DSP coefficients are baked once in Python (`ir_json._extract_coeffs`/`_bake_coeffs`) and *consumed* by Rust (`dsp.rs` `Biquad`); the new IIR Hilbert is built as two all-pass second-order-section branches so Rust reuses `Biquad` verbatim. Adaptive state (Ask 2) is **runtime** state, not IR — seeded via a new `Evaluator` kwarg and read back via a new `export_state()` accessor, so the IR-JSON schema is frozen. `passthrough()` resolves to an identity montage. Every change is additive; the existing parity suite (`equivalence/events/taps.rs`, `check_equivalence.py`) is the regression guardrail and existing fixtures stay byte-identical.

**Tech Stack:** Python 3.10+ (numpy, scipy, lark), Rust (refrain-core, PyO3/maturin, uniffi), pytest, cargo test.

**Spec:** `docs/superpowers/specs/2026-06-03-hrv-biofeedback-support-design.md`

**Ordering rationale:** Ask 4 first (smallest; exercises the full Python→bake→Rust→parity loop end-to-end as a warm-up). Then Ask 2 (medium, self-contained, highest longitudinal value). Then Ask 1 (hardest; the recorder-unblocking `rectify+smooth` validation lands first and is certain, the uncertain `iir_allpass` primitive second behind a hard gate).

**Baseline (already confirmed green in this worktree):**
- `MNE_USE_NUMBA=false QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg .venv/bin/python -m pytest -q` → pass
- `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py` → PASS (all five steps)

**Conventions for every command below** (the worktree root is the cwd):
- Python: prefix with `MNE_USE_NUMBA=false QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg .venv/bin/python -m pytest ...`
- Rust/gate: prefix with `PATH="$HOME/.cargo/bin:$PATH"` and use `.venv/bin/python` / `.venv/bin/maturin`.

---

## File Structure

**Python (`src/refrain/`)**
- `primitive_impls.py` — add `PassthroughImpl`; add `seed()`/`export_state()` to `AutoRangeImpl` & `PercentileImpl`; replace `HilbertIirAllpassImpl` stub with the SOS-pair design; extend `make_filter_impl` for `passthrough` and the IIR Hilbert ctor args.
- `resolver.py` — accept `passthrough` as a montage callee (single-channel stream type).
- `eval_.py` — `Evaluator(..., seed_state=...)`; `Evaluator.export_state()`; `_apply_seed_state()` and `_collect_stateful_impls()` helpers.
- `ir_json.py` — `_extract_coeffs` learns the two-SOS shape of the IIR Hilbert.
- `grammar.lark` — only if `passthrough()` does not already parse as a generic montage call (verify first; likely no change).

**Rust (`refrain-core/src/`)**
- `dsp.rs` — `HilbertIir` (two `Biquad` branches + 1-sample delay); seed/export on the `AutoRange`/`Percentile` runtime structs.
- `eval.rs` — wire `iir_allpass` Hilbert node; apply seed / read export; identity passthrough montage.
- `python.rs` — expose `seed_state` ctor arg + `export_state()` to PyO3.
- `mobile.rs` — expose the same over uniffi.

**Tests**
- `tests/test_passthrough_montage.py`, `tests/test_seed_export_state.py`, `tests/test_hilbert_iir.py`, `tests/test_low_fs_envelope.py` (new).
- `refrain-core/tools/gen_fixtures.py` — register new fixtures (`micro_passthrough_identity`, `seed_export_*`, `hilbert_iir_*`).
- `refrain-core/tests/*.rs` — extend `equivalence.rs`/`taps.rs` only as needed via new fixtures (no edits to existing fixture assertions).

---

## PHASE A — Ask 4: `passthrough()` montage

### Task A1: Confirm `passthrough()` parses; pin resolver behavior with a failing test

**Files:**
- Test: `tests/test_passthrough_montage.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_passthrough_montage.py
"""Ask 4 — first-class identity montage `passthrough()`."""
import numpy as np
import pytest
from refrain.parser import parse
from refrain.resolver import resolve

PROTO = '''
amplifier { name = "test"; channels = [ { name = "ch0"; kind = "other" } ]
            sample_rates = [ 4 ]; reference = "none" }
input "tach" { montage = passthrough() }
derive "x" { from = "tach"; pipeline = [ rectify() ] }
output { audio_gain = x }
'''

def test_passthrough_resolves_to_single_channel():
    ir = resolve(parse(PROTO))
    inp = ir.inputs["tach"]
    assert inp.montage.callee == "passthrough"
    # identity montage carries the single source channel through unchanged
    assert inp.stream_type.n_channels == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MNE_USE_NUMBA=false QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg .venv/bin/python -m pytest tests/test_passthrough_montage.py -q`
Expected: FAIL — resolver raises `ResolveError` (unknown montage callee `passthrough`) or `stream_type` lookup fails.

- [ ] **Step 3: Read the existing montage resolution path**

Read `src/refrain/resolver.py` `_resolve_call` and how `referential`/`bipolar` produce a `stream_type` (n_channels). Mirror the single-channel (referential-like) case: `passthrough` consumes one source channel and emits one channel unchanged. Confirm the grammar parses `passthrough()` (it should — montage is a generic `A.Call`; `resolver.py:459` only requires `isinstance(montage_ast, A.Call)`). If parsing fails, add `passthrough` to the montage/callee rule in `grammar.lark` mirroring `referential`.

- [ ] **Step 4: Implement resolver support**

In `resolver.py` `_resolve_call`, add a `passthrough` branch alongside `referential`: it takes no required args, binds to the input's single declared source channel, and produces a 1-channel `StreamType` (copy the referential single-channel `stream_type` construction). Validate it is used only in montage position (same guard as `referential`).

- [ ] **Step 5: Run test to verify it passes**

Run: `... .venv/bin/python -m pytest tests/test_passthrough_montage.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_passthrough_montage.py src/refrain/resolver.py
git commit -m "feat(montage): resolve passthrough() to a single-channel identity"
```

### Task A2: `PassthroughImpl` — identity step, with parity-shaped behavior test

**Files:**
- Modify: `src/refrain/primitive_impls.py` (add `PassthroughImpl`; extend `make_filter_impl`)
- Test: `tests/test_passthrough_montage.py`

- [ ] **Step 1: Add the failing behavior test**

```python
def test_passthrough_equals_raw_channel():
    from refrain.eval_ import Evaluator
    ir = resolve(parse(PROTO))
    ev = Evaluator(ir, sample_rate_hz=4.0, channel_names=("ch0",), backend="python")
    ev.start(skip_warmup=True)
    chunk = np.array([[0.5], [-0.5], [2.0]], dtype=np.float64)  # (n_samples, 1)
    ev.step_chunk(chunk)
    # passthrough then rectify -> abs of the raw channel
    assert ev.last_taps()["derive/x"] == pytest.approx(2.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `... -m pytest tests/test_passthrough_montage.py::test_passthrough_equals_raw_channel -q`
Expected: FAIL — `make_filter_impl` raises for unknown callee `passthrough`.

- [ ] **Step 3: Implement `PassthroughImpl` and factory branch**

```python
# primitive_impls.py — near BipolarImpl/ReferentialImpl
class PassthroughImpl(PrimitiveImpl):
    """`passthrough()` — first-class identity montage. Emits the single
    source channel unchanged (the sanctioned replacement for the
    `referential(reference="device")` workaround)."""

    def __init__(self, *, channel_names: tuple[str, ...] = ()):
        if len(channel_names) != 1:
            # montage resolution guarantees a single source channel
            pass
        self._channel_names = channel_names

    def step(self, x: np.ndarray) -> np.ndarray:
        # x is (n_samples, n_channels==1) for a montage; emit column 0.
        return x[:, 0] if x.ndim == 2 else x
```

```python
# primitive_impls.py — in make_filter_impl, alongside `referential`
    if callee == "passthrough":
        return PassthroughImpl(channel_names=channel_names)
```

Verify against `ReferentialImpl.step`'s exact return shape (match it so downstream stages are identical). If `ReferentialImpl` returns `x[:, idx]`, mirror that contract.

- [ ] **Step 4: Run to verify it passes**

Run: `... -m pytest tests/test_passthrough_montage.py -q`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/refrain/primitive_impls.py tests/test_passthrough_montage.py
git commit -m "feat(montage): PassthroughImpl identity step + factory wiring"
```

### Task A3: Rust identity passthrough + fixture + parity

**Files:**
- Modify: `refrain-core/src/eval.rs` (montage match: `passthrough` → identity)
- Modify: `refrain-core/tools/gen_fixtures.py` (register `micro_passthrough_identity`)
- Test: parity via fixture + `check_equivalence.py`

- [ ] **Step 1: Read the Rust montage dispatch**

Read `refrain-core/src/eval.rs` where montage callees (`referential`, `bipolar`) are matched and how a referential single-channel passthrough is handled. The `reference == "device"` path already returns the channel unchanged — `passthrough` is the same identity, keyed on `callee == "passthrough"`.

- [ ] **Step 2: Add a failing fixture-backed parity expectation**

Add to `refrain-core/tools/gen_fixtures.py` a `micro_passthrough_identity` protocol identical to PROTO above (4 Hz, one `other` channel, `passthrough()` → `rectify()`), so it emits `*.ir.json` + `*.io.json` from the Python evaluator. Regenerate:

Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/gen_fixtures.py`
Expected: new `refrain-core/tests/fixtures/micro_passthrough_identity.*` written.

- [ ] **Step 3: Run cargo equivalence to verify Rust currently fails**

Run: `PATH="$HOME/.cargo/bin:$PATH" cargo test --manifest-path refrain-core/Cargo.toml --test equivalence`
Expected: FAIL — Rust eval errors / mismatches on unknown `passthrough` montage callee.

- [ ] **Step 4: Implement the Rust identity montage**

In `eval.rs`, add `"passthrough" => /* identity: take the single source channel column unchanged */` to the montage match, mirroring the `reference == "device"` identity branch.

- [ ] **Step 5: Run the full gate**

Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py`
Expected: RESULT: PASS (all five steps).

- [ ] **Step 6: Commit**

```bash
git add refrain-core/src/eval.rs refrain-core/tools/gen_fixtures.py refrain-core/tests/fixtures/micro_passthrough_identity.*
git commit -m "feat(montage): passthrough identity in Rust core + parity fixture"
```

---

## PHASE B — Ask 2: seed + export of adaptive state (compact summary)

### Task B1: `AutoRangeImpl.export_state()` / `.seed()` — anchors + deterministic pre-fill

**Files:**
- Modify: `src/refrain/primitive_impls.py` (`AutoRangeImpl`)
- Test: `tests/test_seed_export_state.py` (create)

- [ ] **Step 1: Write the failing unit test**

```python
# tests/test_seed_export_state.py
"""Ask 2 — compact-summary seed/export for stateful trackers."""
import numpy as np
from refrain.primitive_impls import AutoRangeImpl, PercentileImpl

def test_auto_range_export_after_run():
    impl = AutoRangeImpl(window_ms=5*60*1000, low_pct=1, high_pct=99, sample_rate_hz=4.0)
    rng = np.random.default_rng(0)
    impl.step(rng.uniform(0.01, 0.05, size=400))
    st = impl.export_state()
    assert set(st) == {"low", "high", "n_eff"}
    assert st["low"] < st["high"]
    assert st["n_eff"] == 400  # samples seen, capped at window

def test_auto_range_seed_reproduces_anchors():
    impl = AutoRangeImpl(window_ms=5*60*1000, low_pct=1, high_pct=99, sample_rate_hz=4.0)
    impl.seed({"low": 0.012, "high": 0.048, "n_eff": 1200})
    st = impl.export_state()
    # deterministic synthetic fill reproduces the seeded anchors within tolerance
    assert st["low"] == pytest.approx(0.012, abs=2e-3)
    assert st["high"] == pytest.approx(0.048, abs=2e-3)

def test_unseeded_auto_range_is_cold_start():
    a = AutoRangeImpl(window_ms=1000, low_pct=5, high_pct=95, sample_rate_hz=4.0)
    b = AutoRangeImpl(window_ms=1000, low_pct=5, high_pct=95, sample_rate_hz=4.0)
    x = np.linspace(0, 1, 10)
    assert np.array_equal(a.step(x), b.step(x))  # no seed => identical to today
```

Add `import pytest` at top.

- [ ] **Step 2: Run to verify it fails**

Run: `... -m pytest tests/test_seed_export_state.py -q`
Expected: FAIL — `AutoRangeImpl` has no `export_state`/`seed`.

- [ ] **Step 3: Implement `export_state` + `seed` on `AutoRangeImpl`**

```python
# AutoRangeImpl — add fields + methods. Track samples seen for n_eff.
    # in __init__, after self._buffer = ...:
    #     self._seen = 0
    # in step(), inside the per-sample loop after append:
    #     self._seen += 1   (cap reporting at window_samples)

    def export_state(self) -> dict:
        if len(self._buffer):
            arr = np.fromiter(self._buffer, dtype=np.float64)
            low, high = np.percentile(arr, [self.low_pct, self.high_pct])
        else:
            low = high = 0.0
        return {"low": float(low), "high": float(high),
                "n_eff": int(min(self._seen, self.window_samples))}

    def seed(self, state: dict) -> None:
        """Pre-fill the rolling window with a deterministic synthetic ramp
        whose `low_pct`/`high_pct` percentiles reproduce the seeded anchors.
        For a linear ramp a..b, percentile(p) = a + (p/100)(b-a); solve so
        percentile(low_pct)=low and percentile(high_pct)=high."""
        low, high = float(state["low"]), float(state["high"])
        n = int(min(state.get("n_eff", self.window_samples), self.window_samples))
        n = max(n, 1)
        span_pct = max(self.high_pct - self.low_pct, 1e-9)
        b_minus_a = (high - low) * 100.0 / span_pct
        a = low - (self.low_pct / 100.0) * b_minus_a
        ramp = np.linspace(a, a + b_minus_a, n)
        self._buffer.clear()
        self._buffer.extend(float(v) for v in ramp)
        self._seen = n
```

- [ ] **Step 4: Run to verify it passes**

Run: `... -m pytest tests/test_seed_export_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/primitive_impls.py tests/test_seed_export_state.py
git commit -m "feat(state): AutoRange export_state/seed via synthetic-ramp pre-fill"
```

### Task B2: `PercentileImpl.export_state()` / `.seed()`

**Files:**
- Modify: `src/refrain/primitive_impls.py` (`PercentileImpl`)
- Test: `tests/test_seed_export_state.py`

- [ ] **Step 1: Add failing tests**

```python
def test_percentile_export_and_seed_roundtrip():
    impl = PercentileImpl(target_pct=70, window_ms=5*60*1000, sample_rate_hz=4.0)
    impl.seed({"value": 0.04, "target_pct": 70, "n_eff": 1200})
    st = impl.export_state()
    assert st["value"] == pytest.approx(0.04, abs=1e-9)  # constant fill is exact
    assert st["target_pct"] == 70
    assert st["n_eff"] == 1200
```

- [ ] **Step 2: Run to verify it fails**

Run: `... -m pytest tests/test_seed_export_state.py::test_percentile_export_and_seed_roundtrip -q`
Expected: FAIL — no `export_state`/`seed` on `PercentileImpl`.

- [ ] **Step 3: Implement on `PercentileImpl`**

```python
    # in __init__: self._seen = 0 ; in step() loop after append: self._seen += 1
    def export_state(self) -> dict:
        if len(self._buffer):
            val = float(np.percentile(
                np.fromiter(self._buffer, dtype=np.float64), self.target_pct))
        else:
            val = 0.0
        return {"value": val, "target_pct": float(self.target_pct),
                "n_eff": int(min(self._seen, self.window_samples))}

    def seed(self, state: dict) -> None:
        """Fill the window with `value` repeated, so percentile(target_pct)
        == value exactly at session start; real samples then displace it."""
        value = float(state["value"])
        n = int(min(state.get("n_eff", self.window_samples), self.window_samples))
        n = max(n, 1)
        self._buffer.clear()
        self._buffer.extend([value] * n)
        self._seen = n
```

`PercentileThresholdImpl` subclasses `PercentileImpl`, so it inherits both. Confirm no override needed.

- [ ] **Step 4: Run to verify it passes**

Run: `... -m pytest tests/test_seed_export_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/primitive_impls.py tests/test_seed_export_state.py
git commit -m "feat(state): Percentile export_state/seed via constant-fill"
```

### Task B3: `Evaluator.export_state()` + `seed_state=` wiring (canonical keys)

**Files:**
- Modify: `src/refrain/eval_.py`
- Test: `tests/test_seed_export_state.py`

- [ ] **Step 1: Write the failing integration test**

```python
def test_evaluator_export_and_seed_continuity():
    from refrain.parser import parse
    from refrain.resolver import resolve
    from refrain.eval_ import Evaluator
    proto = '''
    amplifier { name="t"; channels=[{name="ch0";kind="other"}]; sample_rates=[4]; reference="none" }
    input "tach" { montage = passthrough() }
    derive "env" { from="tach"; pipeline=[ rectify(), smooth(tau: 4 s) ] }
    derive "rs"  { from="env";  pipeline=[ auto_range(window: 5 min, percentile: (1, 99)) ] }
    threshold "rs_t" { signal="rs"; type = percentile(target_pct: 70, window: 5 min) }
    reward { event = dwell(condition: above("rs","rs_t"), duration: 5 s) }
    output { audio_gain = rs; audio_chime = reward.event }
    '''
    ir = resolve(parse(proto))
    ev = Evaluator(ir, sample_rate_hz=4.0, channel_names=("ch0",), backend="python")
    ev.start(skip_warmup=True)
    rng = np.random.default_rng(1)
    for _ in range(50):
        ev.step_chunk(rng.uniform(0.0, 0.1, size=(8, 1)))
    state = ev.export_state()
    assert "rs.auto_range" in state and "rs_t.percentile" in state
    assert set(state["rs.auto_range"]) == {"low", "high", "n_eff"}

    # seed a fresh run from the prior state — export is stable across the seed
    ev2 = Evaluator(ir, sample_rate_hz=4.0, channel_names=("ch0",),
                    backend="python", seed_state=state)
    ev2.start(skip_warmup=True)
    seeded = ev2.export_state()
    assert seeded["rs.auto_range"]["low"] == pytest.approx(
        state["rs.auto_range"]["low"], abs=2e-3)
```

- [ ] **Step 2: Run to verify it fails**

Run: `... -m pytest tests/test_seed_export_state.py::test_evaluator_export_and_seed_continuity -q`
Expected: FAIL — `Evaluator.__init__` has no `seed_state`; no `export_state`.

- [ ] **Step 3: Implement helpers + API on `Evaluator`**

```python
# eval_.py — add a stateful-impl collector keyed by "<short>.<callee>".
# Reuse the call-walking shape from _instantiate_expr.
_STATEFUL_CALLEES = ("auto_range", "percentile")

def _walk_calls(expr):
    from .ir import IRCall, IRBinaryOp, IRConditional, IRArray
    if isinstance(expr, IRCall):
        for a in expr.args:
            yield from _walk_calls(a.value)
        yield expr
    elif isinstance(expr, IRBinaryOp):
        yield from _walk_calls(expr.left); yield from _walk_calls(expr.right)
    elif isinstance(expr, IRConditional):
        yield from _walk_calls(expr.cond)
        yield from _walk_calls(expr.then_branch)
        yield from _walk_calls(expr.else_branch)
    elif isinstance(expr, IRArray):
        for e in expr.elements:
            yield from _walk_calls(e)
```

```python
# In Evaluator: build {key: impl} for stateful trackers. short = canonical_name
# after the "/" ; key = f"{short}.{callee}" with a numeric suffix on collision.
    def _collect_stateful_impls(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        def add(short: str, call):
            if call.callee not in _STATEFUL_CALLEES:
                return
            impl = self._impls.get(id(call))
            if impl is None:
                return
            base = f"{short}.{call.callee}"
            key, i = base, 1
            while key in out:
                i += 1; key = f"{base}#{i}"
            out[key] = impl
        for d in self.ir.derives.values():
            short = d.canonical_name.split("/", 1)[-1]
            for c in _walk_calls(d.expression):
                add(short, c)
        for t in self.ir.thresholds.values():
            short = t.canonical_name.split("/", 1)[-1]
            for c in _walk_calls(t.threshold_call):
                add(short, c)
        return out

    def export_state(self) -> dict[str, dict]:
        return {k: impl.export_state() for k, impl in self._collect_stateful_impls().items()}

    def _apply_seed_state(self, seed_state: dict) -> None:
        impls_by_key = self._collect_stateful_impls()
        for key, st in seed_state.items():
            impl = impls_by_key.get(key)
            if impl is not None:
                impl.seed(st)
```

Add `seed_state: dict | None = None` to `__init__` (keyword-only, after `record_streams`), and call `self._apply_seed_state(seed_state)` at the end of `__init__` **after** `self._build_pipeline()`. Add the same `seed_state` passthrough to `Evaluator.live(...)` (forward to `__init__`).

- [ ] **Step 4: Run to verify it passes**

Run: `... -m pytest tests/test_seed_export_state.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Run the full Python suite (no regression)**

Run: `... -m pytest -q`
Expected: pass (same skips as baseline).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/eval_.py tests/test_seed_export_state.py
git commit -m "feat(state): Evaluator.export_state() + seed_state= (canonical keys)"
```

### Task B4: Rust seed/export + parity

**Files:**
- Modify: `refrain-core/src/{dsp,eval,python,mobile}.rs`
- Modify: `refrain-core/tools/gen_fixtures.py`
- Test: parity via `check_equivalence.py` + a Rust unit test

- [ ] **Step 1: Read the Rust AutoRange/Percentile runtime structs**

Read `dsp.rs` for the structs backing `auto_range` and `percentile` (rolling buffers). Identify their per-sample ingest and how `eval.rs` constructs them.

- [ ] **Step 2: Add a Rust unit test (seed reproduces anchors)**

In `refrain-core/tests/` add `seed_export.rs` asserting: an `AutoRange` seeded with `{low,high,n_eff}` via the same synthetic-ramp formula exports `low`/`high` within `2e-3`; a `Percentile` seeded with constant fill exports `value` exactly. Use the identical ramp/constant math as Python (Task B1/B2) so parity is structural.

Run: `PATH="$HOME/.cargo/bin:$PATH" cargo test --manifest-path refrain-core/Cargo.toml --test seed_export`
Expected: FAIL — methods don't exist.

- [ ] **Step 3: Implement seed/export on the Rust structs + boundary**

- `dsp.rs`: `fn seed(&mut self, ...)` (pre-fill `VecDeque` with the ramp/constant), `fn export_state(&self) -> (f64,f64,u64)` / `(f64,f64,u64)`.
- `eval.rs`: after building impls, apply seed by canonical key (mirror `_collect_stateful_impls` key scheme exactly); add an `export_state()` returning a map keyed identically.
- `python.rs`: add optional `seed_state` ctor arg (dict) and `export_state()` returning a dict; mirror in `mobile.rs` over uniffi (additive, optional).

- [ ] **Step 4: Add a dual-backend parity fixture/test**

Extend `gen_fixtures.py` with `seed_export_smr` (a tiny protocol with one `auto_range` + one `percentile`) that records a seeded run's export. The dual-backend pytest step already runs the eval suite under `REFRAIN_EVAL_BACKEND=rust`; add a parity test in `tests/test_seed_export_state.py` parametrized on the `backend` fixture asserting Python and Rust `export_state()` agree to `1e-6`.

- [ ] **Step 5: Run the full gate**

Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py`
Expected: RESULT: PASS.

- [ ] **Step 6: Commit**

```bash
git add refrain-core/src/dsp.rs refrain-core/src/eval.rs refrain-core/src/python.rs refrain-core/src/mobile.rs refrain-core/tests/seed_export.rs refrain-core/tools/gen_fixtures.py tests/test_seed_export_state.py
git commit -m "feat(state): seed/export parity across Rust core + uniffi boundary"
```

---

## PHASE C — Ask 1: low-latency envelope

### Task C1: Validate + document `rectify() + smooth()` as the sanctioned low-Fs envelope (recorder-unblocking, certain)

**Files:**
- Test: `tests/test_low_fs_envelope.py` (create)
- Docs: `docs/PRIMITIVES.md` (add a "low-Fs envelope" note)

- [ ] **Step 1: Write the acceptance test (uses existing primitives — no new impl)**

```python
# tests/test_low_fs_envelope.py
"""Ask 1 fallback: rectify()+smooth() as the low-Fs (4 Hz HRV) envelope.
This is the recorder's sanctioned substitute for hilbert->magnitude when
the sample rate is too low for a low-latency analytic signal."""
import numpy as np
from refrain.primitive_impls import RectifyImpl, SmoothImpl

def test_rectify_smooth_tracks_lf_envelope_under_1s_latency():
    fs = 4.0
    t = np.arange(int(300*fs)) / fs
    carrier = np.sin(2*np.pi*0.1*t)              # 0.1 Hz LF rhythm (in band)
    env = 1.0 + 0.8*np.sin(2*np.pi*0.01*t)       # slow AM envelope
    x = env * carrier
    rect = RectifyImpl()
    sm = SmoothImpl(tau_ms=1000.0, sample_rate_hz=fs)
    y = sm.step(rect.step(x))
    # envelope is recovered (correlation with the true |env| after settling)
    settle = int(30*fs)
    r = np.corrcoef(y[settle:], np.abs(env[settle:]))[0, 1]
    assert r > 0.9
```

- [ ] **Step 2: Run to verify it passes (primitives already exist)**

Run: `... -m pytest tests/test_low_fs_envelope.py -q`
Expected: PASS. (If the correlation threshold is marginal, tune `tau_ms`/threshold and record the chosen value — this pins the recommended low-Fs envelope settings.)

- [ ] **Step 3: Document in `docs/PRIMITIVES.md`**

Add a short subsection: at low sample rates (e.g., 4 Hz tachogram) the FIR Hilbert's group delay is prohibitive; `rectify() + smooth(tau)` is the sanctioned low-latency envelope. State the validated `tau` and the latency (≈ `tau`, well under the FIR's 8 s).

- [ ] **Step 4: Commit**

```bash
git add tests/test_low_fs_envelope.py docs/PRIMITIVES.md
git commit -m "docs+test: rectify+smooth sanctioned as low-Fs envelope (Ask 1 fallback)"
```

### Task C2: IIR-allpass Hilbert — acceptance harness first (the real spec of correctness)

**Files:**
- Test: `tests/test_hilbert_iir.py` (create)

- [ ] **Step 1: Write the acceptance test (validated harness from the design spike)**

```python
# tests/test_hilbert_iir.py
"""Ask 1 — hilbert(kind="iir_allpass"). Acceptance is envelope flatness
(analytic magnitude of a pure tone is ~constant) validated against
scipy.signal.hilbert as ground truth, plus an in-band group-delay budget."""
import numpy as np
import pytest
from scipy import signal

def _ripple(analytic_fn, fs, f, secs=80, warm=40):
    n = np.arange(int(secs*fs)); x = np.sin(2*np.pi*f*n/fs)
    z = analytic_fn(x)[int(warm*fs):]; m = np.abs(z)
    return (m.max() - m.min()) / m.mean() * 100.0

def test_harness_sanity_scipy_hilbert_is_flat():
    assert _ripple(signal.hilbert, 256.0, 14.0) < 1.0  # oracle ~0%

@pytest.mark.parametrize("f", [8.0, 12.0, 16.0, 20.0])
def test_iir_hilbert_envelope_flat_at_eeg_rate(f):
    from refrain.primitive_impls import HilbertIirAllpassImpl
    impl = HilbertIirAllpassImpl(sample_rate_hz=256.0)
    def analytic(x):
        impl_local = HilbertIirAllpassImpl(sample_rate_hz=256.0)
        return impl_local.step(x)
    assert _ripple(analytic, 256.0, f) < 5.0   # GATE: <5% envelope ripple in band

def test_iir_hilbert_group_delay_budget_eeg():
    """In-band group delay must beat the FIR's 32-sample delay materially."""
    from refrain.primitive_impls import HilbertIirAllpassImpl
    impl = HilbertIirAllpassImpl(sample_rate_hz=256.0)
    # measure via the baked SOS branches' group delay (transfer-function eval)
    gd = impl.max_group_delay_samples(band_hz=(8.0, 20.0))
    assert gd < 32  # strictly better than FIR taps=65 group delay
```

- [ ] **Step 2: Run to verify it fails**

Run: `... -m pytest tests/test_hilbert_iir.py -q`
Expected: FAIL — `HilbertIirAllpassImpl` raises `NotImplementedError` / lacks `step`/`max_group_delay_samples`.

- [ ] **Step 3: Commit the failing acceptance harness (spec-as-test)**

```bash
git add tests/test_hilbert_iir.py
git commit -m "test(hilbert): IIR-allpass acceptance harness (flatness + group-delay gate)"
```

### Task C3: Design the two-branch all-pass Hilbert until the EEG-rate gate passes

> **Design note (from the brainstorming spike, recorded so the implementer does not repeat dead ends):** half-remembered coefficient tables and a naive elliptic-`ellip(order,…,0.5)` pole split BOTH fail — they yield valid all-pass branches whose phase difference *sweeps* through 90° instead of holding it (≈150% envelope ripple). A correct design needs a genuine **power-symmetric half-band** parallel-all-pass decomposition (poles on the imaginary z-axis, `a_k = β_k²`, sections `(a_k+z⁻²)/(1+a_k z⁻²)` = biquad `[a,0,1,1,0,a]`), real branch = `A0(z²)`, imag branch = `z⁻¹·A1(z²)`. Validate with `sosfreqz` that `angle(HA / (z⁻¹·HB))` is flat ≈90° across the band BEFORE the time-domain test.

**Files:**
- Modify: `src/refrain/primitive_impls.py` (`HilbertIirAllpassImpl`)
- Modify: `src/refrain/primitive_impls.py` `make_filter_impl` (pass `sample_rate_hz` to the IIR ctor)
- Modify: `src/refrain/ir_json.py` (`_extract_coeffs` for the two-SOS shape)

- [ ] **Step 1: Implement `HilbertIirAllpassImpl`**

Construct two all-pass SOS branches via a power-symmetric half-band design (recommended: Butterworth power-symmetric half-band closed form, or a verified parallel-all-pass extraction). Expose `self.sos_re`, `self.sos_im` for baking, `self._delay` state for the real-branch `z⁻¹`, and:

```python
    def step(self, x):
        re, self._zi_re = scisig.sosfilt(self.sos_re, x, zi=self._zi_re)
        xd, self._dly = _pure_delay(x, self._dly)   # z^-1 on imag branch path
        im, self._zi_im = scisig.sosfilt(self.sos_im, xd, zi=self._zi_im)
        return re + 1j*im

    def max_group_delay_samples(self, band_hz):
        w, gd = scisig.group_delay((np.r_[...], np.r_[...]))  # per-branch, in band
        return float(max gd over band)
```

`make_filter_impl`: `if kind == "iir_allpass": return HilbertIirAllpassImpl(sample_rate_hz=sample_rate_hz, order=int(static_args.get("order", <chosen>)))`.

- [ ] **Step 2: Iterate order/design until the gate passes**

Run: `... -m pytest tests/test_hilbert_iir.py -q`
Expected: PASS — `<5%` ripple at the EEG band and group delay `<32` samples. Use the `sosfreqz` phase-difference check (design note) to converge quickly; raise order only as needed. **If no sane order passes at the EEG rate, STOP and escalate to the user** (the primitive may not be deliverable as `iir_allpass`; the recorder still ships on Task C1). Do not lower the gate to force a pass.

- [ ] **Step 3: Bake coefficients (`ir_json`)**

In `ir_json.py` `_extract_coeffs`, detect the IIR Hilbert impl (e.g., `hasattr(impl, "sos_re")`) and serialize `{"sos_re": ..., "sos_im": ...}`. Confirm round-trip: the baked dict reconstructs the same branches.

- [ ] **Step 4: Run the Python suite + commit**

Run: `... -m pytest -q`
Expected: pass (baseline skips only).

```bash
git add src/refrain/primitive_impls.py src/refrain/ir_json.py
git commit -m "feat(hilbert): IIR-allpass analytic signal via two all-pass SOS branches"
```

### Task C4: Rust `HilbertIir` (Biquad reuse) + parity; 4 Hz latency gate documented

**Files:**
- Modify: `refrain-core/src/dsp.rs` (`HilbertIir`), `refrain-core/src/eval.rs` (wire `iir_allpass`)
- Modify: `refrain-core/tools/gen_fixtures.py` (`hilbert_iir_eeg` fixture)
- Test: `tests/test_hilbert_iir.py` (4 Hz characterization), parity gate

- [ ] **Step 1: Implement `HilbertIir` in Rust reusing `Biquad`**

```rust
// dsp.rs
pub struct HilbertIir { re: Biquad, im: Biquad, dly: f64 }  // dly = 1-sample z^-1 on imag path
impl HilbertIir {
    pub fn new(sos_re: &[Vec<f64>], sos_im: &[Vec<f64>]) -> Self { /* Biquad::new each */ }
    pub fn step(&mut self, x: &[f64]) -> Vec<Complex> { /* re=self.re.step; delay x; im=self.im.step */ }
}
```

`eval.rs`: in the hilbert node match, `"iir_allpass" => HilbertIir::new(baked.sos_re, baked.sos_im)`, producing a complex stream (mirror the FIR complex output type).

- [ ] **Step 2: Add the EEG parity fixture and run cargo equivalence (expect fail first)**

Add `hilbert_iir_eeg` to `gen_fixtures.py` (256 Hz, one channel, `bandpass → hilbert(kind:"iir_allpass") → magnitude`). Regenerate, then:

Run: `PATH="$HOME/.cargo/bin:$PATH" cargo test --manifest-path refrain-core/Cargo.toml --test equivalence`
Expected: FAIL before the Rust impl, PASS after.

- [ ] **Step 3: Characterize (not gate) the 4 Hz / near-DC case and record it**

Add a `tests/test_hilbert_iir.py` test that measures envelope ripple at the recorder's 4 Hz band (0.04–0.15 Hz) and **records** it (assert it runs; print the ripple), with a comment citing the spec's honest gate. If ripple is poor (expected near DC), the recorder uses Task C1's `rectify+smooth`; this test documents *why*.

- [ ] **Step 4: Run the full gate + commit**

Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py`
Expected: RESULT: PASS.

```bash
git add refrain-core/src/dsp.rs refrain-core/src/eval.rs refrain-core/tools/gen_fixtures.py refrain-core/tests/fixtures/hilbert_iir_eeg.* tests/test_hilbert_iir.py
git commit -m "feat(hilbert): Rust HilbertIir via Biquad reuse + EEG parity; 4Hz characterized"
```

---

## PHASE D — Finalize

### Task D1: Docs, CHANGELOG, version bump

**Files:**
- Modify: `docs/PRIMITIVES.md` (passthrough montage; iir_allpass kind; low-Fs envelope — some added earlier), `docs/IR-JSON.md` (two-SOS Hilbert baked shape), `CHANGELOG.md`, `pyproject.toml` (version → `0.8.0`), `refrain-core/Cargo.toml` (version bump if it tracks).

- [ ] **Step 1: Update CHANGELOG + version**

Add a `v0.8.0` section: additive — `passthrough()` montage; `Evaluator.export_state()`/`seed_state=`; `hilbert(kind="iir_allpass")` (EEG-validated; low-Fs envelope guidance for HRV). Note the additive uniffi surface growth (mobile consumers regenerate bindings). Bump `pyproject.toml` to `0.8.0`.

- [ ] **Step 2: Run the whole gate one final time**

Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py`
Expected: RESULT: PASS.

- [ ] **Step 3: Commit**

```bash
git add docs/ CHANGELOG.md pyproject.toml refrain-core/Cargo.toml
git commit -m "release: v0.8.0 — HRV M1 (passthrough, seed/export, iir_allpass)"
```

### Task D2: Finish the branch

- [ ] Use the `superpowers:finishing-a-development-branch` skill to choose merge/PR. The PR body should summarize the three asks, link the spec, and flag: (a) Ask 1 `iir_allpass` is EEG-validated with the 4 Hz/near-DC case served by `rectify+smooth`; (b) uniffi surface grew additively. Reply to the recorder team referencing `v0.8.0`.

---

## Self-Review

**Spec coverage:** Ask 1 → C1 (fallback, certain) + C2–C4 (iir_allpass, gated); Ask 2 → B1–B4 (export/seed Python + Rust + parity, compact-summary as designed); Ask 4 → A1–A3 (resolver + impl + Rust identity + parity). Shared guarantees: every phase ends on `check_equivalence.py`; existing fixtures untouched; IR-JSON frozen for Ask 2 (runtime state); SemVer bump in D1. §8 risks: C2/C3 encode the latency gate + escalation; C4 step 3 records the near-DC characterization.

**Placeholder scan:** DSP coefficient *values* for C3 are intentionally not pinned — that task is a bounded numerical design whose **acceptance test (C2) is fully concrete**, with an explicit escalation path if it can't pass. This is the honest representation of a numerical-design task, not a hand-wave; the design note records the verified dead ends so the implementer starts ahead.

**Type/name consistency:** `export_state()`/`seed()` signatures match between B1/B2 (impl) and B3 (Evaluator collector) and B4 (Rust); key scheme `"<short>.<callee>"` defined once in B3 and reused in B4; `sos_re`/`sos_im` naming consistent across C3 (Python bake) and C4 (Rust consume).
