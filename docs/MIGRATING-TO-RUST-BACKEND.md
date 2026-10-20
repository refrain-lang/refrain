# Migrating a Python host to the Rust backend

For a Python application already embedding the pure-Python `refrain.Evaluator`
(live/push mode) that wants the Rust core's speed and "one canonical
implementation" guarantee. The Rust path is a **drop-in**: identical Python API,
validated to match the Python reference at machine precision.

## TL;DR

```python
# before — pure-Python evaluator
ev = Evaluator.live(ir, sample_rate_hz=sr, channel_names=chans)

# after — delegates the whole push runtime to the Rust core (refrain_core)
ev = Evaluator.live(ir, sample_rate_hz=sr, channel_names=chans, backend="rust")
```

Everything else stays the same. `start()`, `stop()`, `step_chunk()` (same
`Event` objects), `set_control()`, `last_taps()`, `last_streams()`, and the
`state` / `warmup_remaining_s` properties all behave identically. The two
backends are gated against each other in CI (`check_equivalence.py`), so outputs
match to ~1e-13; boolean event/condition streams match exactly.

> **Parameterized placement is orthogonal to the backend.** If your protocols
> use `placement` controls (added since v0.3.0) to deploy at clinician-chosen
> sites, that binding is a **resolve-time** step — `resolve(ast, amp,
> bindings={...})` — that runs in the Python front-end and is unaffected by
> `backend=`. The resolved IR is identical either way. See the deploy-binding
> recipe in `docs/EMBEDDING.md`.

## 1. Install the `refrain_core` wheel

`backend="rust"` lazily imports `refrain_core`; if it isn't installed you get a
clear `ImportError` telling you to build it. Options, simplest first:

```bash
# Dev (editable, in your virtualenv) — needs a Rust toolchain:
cd refrain-core && maturin develop --release

# Or build a wheel and install it (one abi3 wheel covers Python 3.10+):
cd refrain-core && maturin build --release
pip install target/wheels/refrain_core-*.whl
```

For deployment beyond a dev box, distribute the abi3 wheels via your package
index (see the project's distribution notes) and `pip install refrain-core`
with a pinned version + hash. No Rust toolchain is needed to *install* a built
wheel.

## 2. What does NOT migrate (stays Python)

- **Pull-mode / offline replay.** `Evaluator(ir, source).run()` (file replay via
  a `Source`) is Python-only; `backend` applies only to `Evaluator.live(...)` +
  `step_chunk(...)`. Live acquisition → use rust; offline replay → python (or
  run both — they match).
- **Unimplemented / custom primitives.** The full v0.1 standard library is
  covered, but anything outside it — custom Python primitives (SPEC §4.11) or a
  non-default `hilbert(kind=...)` — is not in the Rust core; those protocols must
  stay `backend="python"`. See `docs/CONFORMANCE.md` for the covered set.
- **Three positions a covered primitive can appear in, but the Rust core does
  not accept.** Each one is a stdlib primitive the Rust core implements —
  somewhere else:
  - `absolute(value: "<a derive>")`, a threshold whose level tracks another
    stream instead of sitting at a constant. The Rust core's `absolute` takes
    a number or a `control_ref`.
  - `percentile(target_pct:, window:)` as a **derive pipeline stage**. The Rust
    core has `percentile` as a threshold constructor and as a baseline-seed
    buffer, not as a stage in a `pipeline = [...]`.
  - `passthrough(input: "<a derive>")`, the way an `inhibit` names one derive
    as its metric. The Rust core has `passthrough` only as the identity
    *montage* over a single-channel source.

  All three resolve and run under `backend="python"`. Under `backend="rust"`
  they are refused at evaluator construction with a `RuntimeError` naming the
  fallback — catchable with `except Exception`, so a host can retry on Python.
  Pinned by `tests/test_eval_rust_backend.py::test_python_only_shapes_fall_back_cleanly_on_rust`.

## 3. Verify parity before you flip

Keep `backend="python"` as a fallback/oracle and confirm equivalence on your own
protocols + signals before switching production over:

```python
import numpy as np

def assert_parity(ir, sr, chans, chunks, *, atol=1e-6):
    py = Evaluator.live(ir, sample_rate_hz=sr, channel_names=chans, backend="python")
    rs = Evaluator.live(ir, sample_rate_hz=sr, channel_names=chans, backend="rust")
    py.start(skip_warmup=True); rs.start(skip_warmup=True)
    for chunk in chunks:
        pe, re = py.step_chunk(chunk), rs.step_chunk(chunk)
        assert len(pe) == len(re)
        for a, b in zip(pe, re):
            assert a.channel == b.channel and a.kind == b.kind
            assert abs(a.timestamp_s - b.timestamp_s) < atol
            if a.kind == "value":
                assert abs(a.value - b.value) < atol
```

(The Refrain repo runs this parity at the suite level via
`REFRAIN_EVAL_BACKEND=rust pytest tests/test_eval_*.py`; a host wants the
equivalent check against its own protocols.)

## 4. Pin for reproducibility

- Pin a specific Refrain commit/version and the **IR-JSON schema version**
  (currently `0.1`, `src/refrain/schema/ir-json-v0.1.schema.json`) your
  protocol assets target. See `docs/IR-JSON.md`.
- When CR distributes wheels, pin exact `refrain-core` version **+ hash** and
  prefer an index-scoped install (avoid `--extra-index-url` dependency
  confusion — `refrain` is an unrelated package on public PyPI).

## 5. Error/lifecycle contract (identical across backends)

| Situation | Behavior (both backends) |
|---|---|
| `step_chunk()` after `stop()` | `RuntimeError` |
| chunk with wrong channel count | `ValueError` ("configured for N") |
| `set_control()` with unknown name | `KeyError` |
| `start()` called twice | `RuntimeError` |
| `backend=` not `"python"`/`"rust"` | `ValueError` |
| `backend="rust"` but wheel not installed | `ImportError` (with build instructions) |

## See also

- `docs/EMBEDDING.md` — the host division-of-labour, the five-method embedding model, and the deploy-time placement-binding recipe.
- `docs/IR-JSON.md` — the wire format + the sample-rate-baked / channels-are-runtime rules.
- `docs/CONFORMANCE.md` — golden-vector conformance suite + what the Rust core covers.
- `docs/REPRODUCIBILITY.md` — why one core + the conformance suite gives reproducibility by construction.
