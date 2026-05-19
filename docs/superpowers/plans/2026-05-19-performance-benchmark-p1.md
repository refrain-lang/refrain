# Performance Benchmark Suite — Phase P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundation of Refrain's performance benchmark suite — a chunked runner, a numerical-equivalence checker, an IR→numpy transpiler, idiomatic numpy baselines, and a microbench protocol corpus. End state: a `python -m bench equivalence` command that confirms Refrain's outputs match both transpiled and idiomatic baselines on every P1 protocol.

**Architecture:** All bench code lives under `bench/` at the repo root, siblings to `src/`, `tests/`, `examples/`, `docs/`. Bench-specific tests live under `tests/bench/`. The bench depends on Refrain as a normal in-repo import. One small Refrain-side change (Task 2) adds a `record_streams=True` mode to `Evaluator` so the harness can capture per-chunk stream arrays for equivalence comparison. The IR transpiler walks `IRProtocol` and emits an instance of `TranspiledProtocol` that calls the same `primitive_impls.py` classes Refrain uses — bypassing only the `Evaluator` framing (`_eval_expr` dispatch, tap capture, warmup gating, event emission).

**Tech Stack:** Python 3.10+, numpy, scipy, lark (via existing Refrain deps). pytest for tests. No new third-party dependencies.

**Scope cut from this plan (deferred to P2 plan):** timing measurements and tax curves, hardware tiers, CI integration, complexity ceiling sweep, transpiler support for inhibits / bandpower / coherence, realistic Othmer and Alpha-Theta protocols (both depend on inhibits + bandpower).

**P1 protocol corpus:**
- `micro_01_passthrough.refrain` — input only
- `micro_02_bandpass.refrain` — bandpass alone
- `micro_03_envelope.refrain` — bandpass → hilbert → magnitude → smooth
- `micro_04_threshold.refrain` — envelope + percentile threshold
- `micro_05_reward.refrain` — envelope + threshold + dwell + sigmoid
- `realistic_smr.refrain` — copy of `examples/smr_cz.refrain` (uses only P1-supported primitives)

---

## File Structure

**Created in this plan:**

```
bench/
  __init__.py                                    # empty
  __main__.py                                    # `python -m bench` entry
  README.md                                      # one paragraph: what bench/ is
  cli.py                                         # argparse, dispatches subcommands
  harness/
    __init__.py
    equivalence.py                               # assert_equivalent + EquivalenceReport
    env_capture.py                               # capture_env() -> dict
    runner.py                                    # ChunkedRunner: run protocol, return per-chunk outputs and latencies
    transpile.py                                 # transpile(ir) -> TranspiledProtocol
  baselines/
    __init__.py
    micro_01_passthrough_idiomatic.py
    micro_02_bandpass_idiomatic.py
    micro_03_envelope_idiomatic.py
    micro_04_threshold_idiomatic.py
    micro_05_reward_idiomatic.py
    realistic_smr_idiomatic.py
  protocols/
    micro_01_passthrough.refrain
    micro_02_bandpass.refrain
    micro_03_envelope.refrain
    micro_04_threshold.refrain
    micro_05_reward.refrain
    realistic_smr.refrain
tests/bench/
  __init__.py
  test_equivalence.py
  test_env_capture.py
  test_runner.py
  test_transpile_input.py
  test_transpile_pipeline.py
  test_transpile_threshold.py
  test_transpile_reward.py
  test_baselines_idiomatic.py                    # equivalence: refrain ≡ (a) for all protocols
  test_baselines_transpiled.py                   # equivalence: refrain ≡ (c) for all protocols
  test_cli.py                                    # smoke test for `python -m bench equivalence`
```

**Modified in this plan:**

```
src/refrain/eval_.py                             # add record_streams=True mode (Task 2)
pyproject.toml                                   # extend ruff + pytest testpaths (Task 15)
```

---

## Task 1: Bench package skeleton

**Files:**
- Create: `bench/__init__.py`, `bench/harness/__init__.py`, `bench/baselines/__init__.py`, `bench/protocols/.gitkeep`, `bench/README.md`
- Create: `tests/bench/__init__.py`
- Test: `tests/bench/test_skeleton.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_skeleton.py
"""Smoke test for bench/ package layout. All other bench tests depend on these imports working."""

import importlib


def test_bench_package_imports():
    importlib.import_module("bench")
    importlib.import_module("bench.harness")
    importlib.import_module("bench.baselines")


def test_bench_protocols_dir_exists():
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent.parent
    assert (repo / "bench" / "protocols").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_skeleton.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench'`

- [ ] **Step 3: Create the package skeleton**

```python
# bench/__init__.py
"""Performance benchmark suite for Refrain.

See `docs/superpowers/specs/2026-05-19-performance-benchmark-design.md`
for the methodology this implements.
"""
```

```python
# bench/harness/__init__.py
"""Bench harness: runner, equivalence checker, env capture, transpiler."""
```

```python
# bench/baselines/__init__.py
"""Idiomatic numpy/scipy baselines, one per protocol. Each module exposes
a `Baseline` class with `step(raw_chunk) -> dict[str, np.ndarray]` matching
the corresponding Refrain protocol's stream outputs."""
```

```
# bench/protocols/.gitkeep
```

```markdown
# bench/README.md
# Refrain benchmark suite

This directory holds the performance benchmark suite for the Refrain
reference evaluator. See
[`docs/superpowers/specs/2026-05-19-performance-benchmark-design.md`](../docs/superpowers/specs/2026-05-19-performance-benchmark-design.md)
for design, metrics, and methodology.

Phase P1 (this commit set) ships harness + equivalence; timing measurements
land in P2.

Run the equivalence audit:

    python -m bench equivalence
```

```python
# tests/bench/__init__.py
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/bench/test_skeleton.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/ tests/bench/
git commit -m "bench: package skeleton (P1)"
```

---

## Task 2: Refrain-side change — `Evaluator.record_streams` mode

The bench needs per-chunk stream arrays for equivalence comparison. `last_taps()` only exposes last-sample scalars. Add a constructor flag that, when set, captures the `stream_values` dict per chunk into `self._last_streams`.

**Files:**
- Modify: `src/refrain/eval_.py` (constructor signature, `live()` factory, `_process_chunk` epilogue, new `last_streams()` accessor)
- Test: `tests/test_eval_record_streams.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_record_streams.py
"""record_streams=True captures per-chunk stream_values for the bench harness."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"


def _smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"),
                   load_amp_profile(AMP_Q21))


def test_record_streams_default_off():
    ev = Evaluator.live(_smr_ir(), sample_rate_hz=256, channel_names=("Cz",))
    ev.start(skip_warmup=True)
    ev.step_chunk(np.zeros((32, 1), dtype=np.float64))
    assert ev.last_streams() == {}, "default mode must not record"


def test_record_streams_captures_chunk():
    ev = Evaluator.live(
        _smr_ir(), sample_rate_hz=256, channel_names=("Cz",),
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    chunk = np.random.default_rng(0).standard_normal((32, 1))
    ev.step_chunk(chunk)
    streams = ev.last_streams()
    assert "raw" in streams
    assert streams["raw"].shape == (32,)
    assert "smr_envelope" in streams
    assert streams["smr_envelope"].shape == (32,)


def test_record_streams_overwrites_each_chunk():
    ev = Evaluator.live(
        _smr_ir(), sample_rate_hz=256, channel_names=("Cz",),
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    rng = np.random.default_rng(1)
    ev.step_chunk(rng.standard_normal((32, 1)))
    first = ev.last_streams()["raw"].copy()
    ev.step_chunk(rng.standard_normal((32, 1)))
    second = ev.last_streams()["raw"]
    assert not np.array_equal(first, second), "stream snapshot must refresh per chunk"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_record_streams.py -v`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'record_streams'`

- [ ] **Step 3: Modify `Evaluator.__init__` to accept the flag**

In `src/refrain/eval_.py`, extend the constructor signature and store the flag plus an empty stream dict:

```python
def __init__(
    self,
    ir: IRProtocol,
    source: Source | None = None,
    *,
    sample_rate_hz: float | None = None,
    channel_names: tuple[str, ...] | None = None,
    record_streams: bool = False,
):
    # ... existing body unchanged ...
    self._record_streams = bool(record_streams)
    self._last_streams: dict[str, np.ndarray] = {}
```

- [ ] **Step 4: Forward the flag through the `live()` factory**

```python
@classmethod
def live(
    cls,
    ir: IRProtocol,
    *,
    sample_rate_hz: float,
    channel_names: tuple[str, ...],
    record_streams: bool = False,
) -> "Evaluator":
    return cls(
        ir,
        sample_rate_hz=sample_rate_hz,
        channel_names=channel_names,
        record_streams=record_streams,
    )
```

- [ ] **Step 5: Capture `stream_values` at the end of `_process_chunk`**

In `_process_chunk`, immediately after the existing `self._capture_taps(...)` call, add:

```python
if self._record_streams:
    self._last_streams = {k: np.asarray(v).copy() for k, v in stream_values.items()}
```

- [ ] **Step 6: Add `last_streams()` accessor**

Below the existing `last_taps()` method, add:

```python
def last_streams(self) -> dict[str, np.ndarray]:
    """Per-chunk snapshot of derive/input/threshold stream arrays.

    Empty unless `record_streams=True` was passed at construction. The bench
    harness uses this to compare per-sample stream outputs against
    independently computed baselines (`bench/baselines/`, `bench/harness/transpile.py`).
    Returns a fresh dict each call; callers may mutate it freely.
    """
    return dict(self._last_streams)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_eval_record_streams.py -v`
Expected: PASS (3 tests)

Also run the existing eval test suite to confirm no regression:

Run: `pytest tests/test_eval_lifecycle.py tests/test_eval_taps.py -v`
Expected: all existing tests still PASS.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/eval_.py tests/test_eval_record_streams.py
git commit -m "eval: add record_streams=True mode for benchmark harness use"
```

---

## Task 3: Equivalence checker

`assert_equivalent` is the gate the whole bench rests on. It takes two stream dicts and asserts they match within tolerance after a configurable warmup skip.

**Files:**
- Create: `bench/harness/equivalence.py`
- Test: `tests/bench/test_equivalence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_equivalence.py
"""Equivalence checker: pass/fail for matching/mismatching stream dicts."""

from __future__ import annotations

import numpy as np
import pytest

from bench.harness.equivalence import EquivalenceFailure, assert_equivalent


def test_identical_streams_pass():
    a = {"x": np.arange(100, dtype=np.float64)}
    b = {"x": np.arange(100, dtype=np.float64)}
    report = assert_equivalent(a, b, warmup_samples=10)
    assert report.passed
    assert report.streams_checked == ("x",)


def test_streams_within_tolerance_pass():
    a = {"x": np.arange(100, dtype=np.float64)}
    b = {"x": np.arange(100, dtype=np.float64) + 1e-10}
    report = assert_equivalent(a, b, warmup_samples=10, atol=1e-9, rtol=1e-6)
    assert report.passed


def test_streams_outside_tolerance_fail():
    a = {"x": np.arange(100, dtype=np.float64)}
    b = {"x": np.arange(100, dtype=np.float64) + 1.0}
    with pytest.raises(EquivalenceFailure) as excinfo:
        assert_equivalent(a, b, warmup_samples=10, atol=1e-9, rtol=1e-6)
    assert "x" in str(excinfo.value)


def test_warmup_samples_skipped():
    a = {"x": np.zeros(100, dtype=np.float64)}
    b = {"x": np.zeros(100, dtype=np.float64)}
    b["x"][:5] = 999.0  # warmup region differs
    report = assert_equivalent(a, b, warmup_samples=10)
    assert report.passed


def test_missing_stream_in_b_fails():
    a = {"x": np.zeros(10), "y": np.zeros(10)}
    b = {"x": np.zeros(10)}
    with pytest.raises(EquivalenceFailure) as excinfo:
        assert_equivalent(a, b, warmup_samples=0)
    assert "y" in str(excinfo.value)


def test_extra_stream_in_b_ignored():
    """Extra streams in (b) are permitted — refrain may expose more streams
    than the baseline computes. The contract is: every refrain stream named
    in `streams_to_check` exists in (b) and matches."""
    a = {"x": np.zeros(10)}
    b = {"x": np.zeros(10), "y_unrequested": np.zeros(10)}
    report = assert_equivalent(a, b, warmup_samples=0)
    assert report.passed
    assert report.streams_checked == ("x",)


def test_shape_mismatch_fails():
    a = {"x": np.zeros(100)}
    b = {"x": np.zeros(50)}
    with pytest.raises(EquivalenceFailure) as excinfo:
        assert_equivalent(a, b, warmup_samples=0)
    assert "shape" in str(excinfo.value).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_equivalence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bench.harness.equivalence'`

- [ ] **Step 3: Implement the equivalence checker**

```python
# bench/harness/equivalence.py
"""Numerical equivalence checker for the benchmark suite.

The whole DSL-tax measurement rests on the assertion that Refrain and the
baselines compute the same thing. If they don't, the timing comparison is
meaningless. This module is the gate: every (refrain_output, baseline_output)
pair must pass `assert_equivalent` before any timing is reported.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class EquivalenceFailure(AssertionError):
    """Raised when refrain and baseline streams disagree beyond tolerance."""


@dataclass(frozen=True)
class EquivalenceReport:
    passed: bool
    streams_checked: tuple[str, ...]
    warmup_samples: int
    atol: float
    rtol: float


def assert_equivalent(
    refrain_streams: dict[str, np.ndarray],
    baseline_streams: dict[str, np.ndarray],
    *,
    warmup_samples: int,
    atol: float = 1e-9,
    rtol: float = 1e-6,
) -> EquivalenceReport:
    """Assert that every stream named in `refrain_streams` also exists in
    `baseline_streams` and matches within tolerance after the warmup window.

    Extra streams in `baseline_streams` are ignored.

    Raises `EquivalenceFailure` on any disagreement; returns an
    `EquivalenceReport` on success.
    """
    names = tuple(sorted(refrain_streams.keys()))
    for name in names:
        if name not in baseline_streams:
            raise EquivalenceFailure(
                f"stream {name!r} missing from baseline output"
            )
        a = np.asarray(refrain_streams[name])
        b = np.asarray(baseline_streams[name])
        if a.shape != b.shape:
            raise EquivalenceFailure(
                f"stream {name!r} shape mismatch: refrain={a.shape}, baseline={b.shape}"
            )
        if a.ndim == 0 or a.shape[0] <= warmup_samples:
            continue
        a_steady = a[warmup_samples:]
        b_steady = b[warmup_samples:]
        if not np.allclose(a_steady, b_steady, atol=atol, rtol=rtol, equal_nan=False):
            max_abs = float(np.max(np.abs(a_steady - b_steady)))
            first_diff = int(np.argmax(np.abs(a_steady - b_steady)))
            raise EquivalenceFailure(
                f"stream {name!r}: max |diff| = {max_abs:.3e} "
                f"(atol={atol}, rtol={rtol}); first divergence at "
                f"steady-state sample {first_diff} "
                f"(refrain={a_steady[first_diff]!r}, baseline={b_steady[first_diff]!r})"
            )
    return EquivalenceReport(
        passed=True,
        streams_checked=names,
        warmup_samples=warmup_samples,
        atol=atol,
        rtol=rtol,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_equivalence.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/harness/equivalence.py tests/bench/test_equivalence.py
git commit -m "bench: equivalence checker with warmup skip and tolerance"
```

---

## Task 4: Environment capture

`capture_env()` snapshots everything that could affect timing or numerical results. Used in every run record.

**Files:**
- Create: `bench/harness/env_capture.py`
- Test: `tests/bench/test_env_capture.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_env_capture.py
"""Env capture: required keys present, no crashes on missing optional info."""

from __future__ import annotations

from bench.harness.env_capture import capture_env


REQUIRED_KEYS = (
    "python_version",
    "platform",
    "cpu",
    "numpy_version",
    "scipy_version",
    "refrain_version",
    "git_sha",
)


def test_capture_env_returns_required_keys():
    env = capture_env()
    for key in REQUIRED_KEYS:
        assert key in env, f"missing required key: {key}"


def test_capture_env_values_are_strings_or_none():
    env = capture_env()
    for key, val in env.items():
        assert val is None or isinstance(val, str), f"{key} is {type(val).__name__}"


def test_capture_env_records_git_sha_format():
    env = capture_env()
    sha = env["git_sha"]
    # In a git repo, sha is 40 hex chars; outside one, it's None.
    if sha is not None:
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha.lower())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_env_capture.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement env capture**

```python
# bench/harness/env_capture.py
"""Capture the host environment for a bench run.

Returned dict is committed to `bench/results/<run>/env.json`. Every field is
either a string or None; never raises. Designed to never block a run on a
missing tool — if `git` isn't on PATH, `git_sha` is None and the run still
goes through.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


def _safe_run(*cmd: str) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return None


def _module_version(name: str) -> str | None:
    try:
        mod = __import__(name)
        return getattr(mod, "__version__", None)
    except ImportError:
        return None


def _cpu_model() -> str | None:
    if sys.platform == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            return None
    if sys.platform == "darwin":
        return _safe_run("sysctl", "-n", "machdep.cpu.brand_string")
    return platform.processor() or None


def _cpu_governor() -> str | None:
    if sys.platform == "linux":
        try:
            return Path(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
            ).read_text().strip()
        except OSError:
            return None
    return None


def _cpu_freq_khz() -> str | None:
    if sys.platform == "linux":
        try:
            return Path(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"
            ).read_text().strip()
        except OSError:
            return None
    return None


def capture_env() -> dict[str, str | None]:
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu": _cpu_model(),
        "cpu_governor": _cpu_governor(),
        "cpu_max_freq_khz": _cpu_freq_khz(),
        "cpu_count_logical": str(platform.machine() and (__import__("os").cpu_count() or 0)),
        "numpy_version": _module_version("numpy"),
        "scipy_version": _module_version("scipy"),
        "refrain_version": _module_version("refrain"),
        "git_sha": _safe_run("git", "rev-parse", "HEAD"),
        "git_dirty": "true" if _safe_run("git", "status", "--porcelain") else "false",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_env_capture.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/harness/env_capture.py tests/bench/test_env_capture.py
git commit -m "bench: env capture (python/numpy/scipy versions, cpu, git sha)"
```

---

## Task 5: Chunked runner

`ChunkedRunner.run(callable, n_samples)` drives any object with a `step(raw_chunk)` method, returns a `RunResult` with concatenated stream outputs and per-chunk latencies. Generic over the callable so it works for Refrain (via a thin adapter, see Task 9), the transpiled baseline, and the idiomatic baselines.

**Files:**
- Create: `bench/harness/runner.py`
- Test: `tests/bench/test_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_runner.py
"""Chunked runner: drives a step() callable, collects outputs and latencies."""

from __future__ import annotations

import numpy as np

from bench.harness.runner import ChunkedRunner, RunResult


class _DoubleIt:
    """Trivial step() callable: doubles input, emits as 'x' stream."""

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        return {"x": raw_chunk[:, 0] * 2.0}


def test_runner_returns_concatenated_streams():
    rng = np.random.default_rng(0)
    input_signal = rng.standard_normal((1024, 1))
    runner = ChunkedRunner(chunk_size=32)
    result = runner.run(_DoubleIt(), input_signal)
    assert isinstance(result, RunResult)
    assert result.streams["x"].shape == (1024,)
    np.testing.assert_array_equal(result.streams["x"], input_signal[:, 0] * 2.0)


def test_runner_records_one_latency_per_chunk():
    runner = ChunkedRunner(chunk_size=32)
    input_signal = np.zeros((256, 1))
    result = runner.run(_DoubleIt(), input_signal)
    expected_chunks = 256 // 32
    assert len(result.per_chunk_ns) == expected_chunks
    assert all(t > 0 for t in result.per_chunk_ns)


def test_runner_rejects_non_divisible_length():
    runner = ChunkedRunner(chunk_size=32)
    input_signal = np.zeros((100, 1))
    try:
        runner.run(_DoubleIt(), input_signal)
    except ValueError as exc:
        assert "chunk_size" in str(exc)
    else:
        raise AssertionError("expected ValueError on non-divisible length")


def test_runner_passes_correct_chunk_shape():
    seen_shapes: list[tuple[int, int]] = []

    class _Spy:
        def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
            seen_shapes.append(raw_chunk.shape)
            return {"x": raw_chunk[:, 0]}

    runner = ChunkedRunner(chunk_size=64)
    runner.run(_Spy(), np.zeros((256, 2)))
    assert seen_shapes == [(64, 2), (64, 2), (64, 2), (64, 2)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement the runner**

```python
# bench/harness/runner.py
"""Chunked runner: drives a step() callable, captures stream outputs and
per-chunk timing.

The callable contract is `step(raw_chunk: np.ndarray) -> dict[str, np.ndarray]`.
The runner concatenates per-chunk outputs into full-length streams (one
1-D array per stream name) and records `time.perf_counter_ns` deltas per
chunk. No statistical aggregation happens here — every chunk's latency is
preserved so downstream code can compute P50/P95/P99/P99.9 honestly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np


class StepCallable(Protocol):
    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        ...


@dataclass(frozen=True)
class RunResult:
    streams: dict[str, np.ndarray]      # name -> full-length 1-D array
    per_chunk_ns: tuple[int, ...]       # one wall-clock measurement per chunk


class ChunkedRunner:
    def __init__(self, *, chunk_size: int):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size

    def run(self, callable_: StepCallable, input_signal: np.ndarray) -> RunResult:
        if input_signal.ndim != 2:
            raise ValueError(
                f"input_signal must be 2D (n_samples, n_channels); got shape {input_signal.shape}"
            )
        total_samples = input_signal.shape[0]
        if total_samples % self.chunk_size != 0:
            raise ValueError(
                f"input_signal length {total_samples} is not divisible by chunk_size {self.chunk_size}"
            )
        n_chunks = total_samples // self.chunk_size

        accumulated: dict[str, list[np.ndarray]] = {}
        per_chunk_ns: list[int] = []

        for i in range(n_chunks):
            start = i * self.chunk_size
            end = start + self.chunk_size
            chunk = input_signal[start:end]
            t0 = time.perf_counter_ns()
            out = callable_.step(chunk)
            t1 = time.perf_counter_ns()
            per_chunk_ns.append(t1 - t0)
            for name, arr in out.items():
                accumulated.setdefault(name, []).append(np.asarray(arr))

        streams = {name: np.concatenate(parts) for name, parts in accumulated.items()}
        return RunResult(streams=streams, per_chunk_ns=tuple(per_chunk_ns))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_runner.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bench/harness/runner.py tests/bench/test_runner.py
git commit -m "bench: chunked runner with per-chunk timing"
```

---

## Task 6: Microbench protocol fixtures

Five `.refrain` files that isolate primitive groups. Each must parse and resolve cleanly. Coherence and bandpower deliberately omitted — they land in P2.

**Files:**
- Create: `bench/protocols/micro_01_passthrough.refrain`
- Create: `bench/protocols/micro_02_bandpass.refrain`
- Create: `bench/protocols/micro_03_envelope.refrain`
- Create: `bench/protocols/micro_04_threshold.refrain`
- Create: `bench/protocols/micro_05_reward.refrain`
- Test: `tests/bench/test_protocols_parse.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_protocols_parse.py
"""Every bench protocol must parse and resolve cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest

from refrain.amp_profile import load_amp_profile
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

MICRO_PROTOCOLS = [
    "micro_01_passthrough.refrain",
    "micro_02_bandpass.refrain",
    "micro_03_envelope.refrain",
    "micro_04_threshold.refrain",
    "micro_05_reward.refrain",
]


@pytest.mark.parametrize("filename", MICRO_PROTOCOLS)
def test_microbench_protocol_parses_and_resolves(filename: str):
    path = PROTOCOLS / filename
    assert path.exists(), f"missing protocol file: {path}"
    ir = resolve(parse_file(path), load_amp_profile(AMP_Q21))
    assert ir is not None
    assert ir.name is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_protocols_parse.py -v`
Expected: FAIL — files don't exist (5 failures, one per filename).

- [ ] **Step 3: Create `micro_01_passthrough.refrain`**

```
// micro_01_passthrough.refrain
// Bench micro: input stage only. Establishes the lower bound of overhead —
// no DSP, just the IR walk and dispatch.

protocol "micro_01_passthrough" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "Bench: passthrough — input stage only, no derives"
  }

  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }

  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }

  output {
    audio_gain = 0
  }
}
```

- [ ] **Step 4: Create `micro_02_bandpass.refrain`**

```
// micro_02_bandpass.refrain
// Bench micro: one bandpass on the input. Isolates filter cost.

protocol "micro_02_bandpass" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "Bench: single bandpass, no envelope"
  }

  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }

  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }

  derive "smr_bp" {
    from = "raw"
    pipeline = [
      bandpass(band: (12 Hz, 15 Hz), order: 4),
    ]
  }

  output {
    audio_gain = 0
  }
}
```

- [ ] **Step 5: Create `micro_03_envelope.refrain`**

```
// micro_03_envelope.refrain
// Bench micro: full SMR envelope pipeline. The dominant per-sample work
// in a realistic SMR protocol per band.

protocol "micro_03_envelope" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "Bench: bandpass -> hilbert -> magnitude -> smooth"
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

  output {
    audio_gain = 0
  }
}
```

- [ ] **Step 6: Create `micro_04_threshold.refrain`**

```
// micro_04_threshold.refrain
// Bench micro: envelope + percentile threshold. Exercises the rolling-window
// percentile data structure, the heaviest non-FFT primitive in a typical NF
// pipeline.

protocol "micro_04_threshold" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "Bench: envelope + percentile threshold over 2 min window"
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

  threshold "smr_t" {
    signal = "smr_envelope"
    type   = percentile(target_pct: 70, window: 2 min)
  }

  output {
    audio_gain = 0
  }
}
```

- [ ] **Step 7: Create `micro_05_reward.refrain`**

```
// micro_05_reward.refrain
// Bench micro: full reward stage — sigmoid continuous + dwell event with
// above/below/all_of. Covers everything below the inhibit gate.

protocol "micro_05_reward" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "Bench: envelope + threshold + dwell event + sigmoid reward"
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

  threshold "smr_t" {
    signal = "smr_envelope"
    type   = percentile(target_pct: 70, window: 2 min)
  }

  threshold "theta_t" {
    signal = "theta_envelope"
    type   = percentile(target_pct: 30, window: 2 min)
  }

  reward {
    event = dwell(
      condition: all_of([
        above("smr_envelope", "smr_t"),
        below("theta_envelope", "theta_t"),
      ]),
      duration: 250 ms
    )
    continuous = sigmoid("smr_envelope" / "smr_t",
                         midpoint: 1.0, steepness: 3)
  }

  output {
    audio_chime = reward.event
    audio_gain  = reward.continuous
  }
}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/bench/test_protocols_parse.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Commit**

```bash
git add bench/protocols/micro_*.refrain tests/bench/test_protocols_parse.py
git commit -m "bench: microbench protocol corpus (5 protocols)"
```

---

## Task 7: Realistic SMR protocol fixture

Copy `examples/smr_cz.refrain` under `bench/protocols/realistic_smr.refrain` with a header marking it as a bench-synced copy. Use a plain copy (not a symlink) so the bench can pin its protocol shape independently of upstream `examples/` evolution.

**Files:**
- Create: `bench/protocols/realistic_smr.refrain`
- Test: `tests/bench/test_protocols_parse.py` (extend)

- [ ] **Step 1: Extend the parse test**

In `tests/bench/test_protocols_parse.py`, add to the list:

```python
REALISTIC_PROTOCOLS = [
    "realistic_smr.refrain",
]


@pytest.mark.parametrize("filename", REALISTIC_PROTOCOLS)
def test_realistic_protocol_parses_and_resolves(filename: str):
    path = PROTOCOLS / filename
    assert path.exists(), f"missing protocol file: {path}"
    ir = resolve(parse_file(path), load_amp_profile(AMP_Q21))
    assert ir is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_protocols_parse.py::test_realistic_protocol_parses_and_resolves -v`
Expected: FAIL — file missing.

- [ ] **Step 3: Create the realistic protocol fixture**

`bench/protocols/realistic_smr.refrain` is the body of `examples/smr_cz.refrain` prefixed with a sync header. Concretely, prepend the following lines to a verbatim copy of `examples/smr_cz.refrain`:

```
// realistic_smr.refrain
// DO NOT EDIT — synced from examples/smr_cz.refrain at P1 ship time.
// Re-syncing requires re-running the equivalence audit; see
// docs/superpowers/specs/2026-05-19-performance-benchmark-design.md §8.
//
```

The remainder of the file is byte-identical to `examples/smr_cz.refrain` (starting at the `// SMR / theta-beta operant training at Cz.` line and continuing through the closing `}` of the protocol block).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_protocols_parse.py -v`
Expected: PASS (6 tests total: 5 micro + 1 realistic).

- [ ] **Step 5: Commit**

```bash
git add bench/protocols/realistic_smr.refrain tests/bench/test_protocols_parse.py
git commit -m "bench: realistic_smr fixture (synced from examples/smr_cz.refrain)"
```

---

## Task 8: IR transpiler — input stage

The transpiler walks an `IRProtocol` and returns a `TranspiledProtocol` instance whose `.step(raw_chunk)` calls the same primitive implementations as `Evaluator` but with no expression-tree dispatch. This task implements the input stage only; later tasks add derives, thresholds, and reward.

**Files:**
- Create: `bench/harness/transpile.py`
- Test: `tests/bench/test_transpile_input.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_transpile_input.py
"""Transpiler: input stage only (referential and bipolar montages)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from refrain.amp_profile import load_amp_profile
from refrain.parser import parse_file
from refrain.resolver import resolve

from bench.harness.transpile import transpile

REPO = Path(__file__).resolve().parent.parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"


def _ir(filename: str):
    return resolve(parse_file(PROTOCOLS / filename), load_amp_profile(AMP_Q21))


def test_transpile_passthrough_produces_input_stream():
    tp = transpile(
        _ir("micro_01_passthrough.refrain"),
        sample_rate_hz=256.0,
        channel_names=("Cz", "linked_ears"),
    )
    rng = np.random.default_rng(0)
    chunk = rng.standard_normal((32, 2))
    out = tp.step(chunk)
    assert "raw" in out
    assert out["raw"].shape == (32,)
    # Referential montage: active - reference
    np.testing.assert_allclose(out["raw"], chunk[:, 0] - chunk[:, 1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_transpile_input.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement transpiler skeleton + input stage**

```python
# bench/harness/transpile.py
"""IR -> flat numpy transpiler. Produces a `TranspiledProtocol` that calls
the same `refrain.primitive_impls` classes the evaluator uses, but with no
expression-tree dispatch, no tap capture, no warmup gating, and no event
emission. The output stream dict matches the keys captured by
`Evaluator(record_streams=True)` so `assert_equivalent` can compare them
directly.

P1 coverage: input (referential, bipolar), derive pipelines composed of
bandpass + hilbert + magnitude + smooth, percentile / absolute thresholds,
reward with above/below/all_of/dwell/sigmoid, output bindings.

NOT supported in P1: inhibits, bandpower, coherence, control substitution
(controls are evaluated to their static defaults at transpile time).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from refrain import primitive_impls as impls
from refrain.ir import IRProtocol


@dataclass
class TranspiledProtocol:
    """Output of `transpile(ir)`. Drives the same `step()` contract the
    runner expects: `step(raw_chunk: np.ndarray) -> dict[str, np.ndarray]`.
    """
    stages: list[Callable[[dict[str, np.ndarray], np.ndarray], None]] = field(default_factory=list)

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        streams: dict[str, np.ndarray] = {}
        for stage in self.stages:
            stage(streams, raw_chunk)
        return streams


def transpile(
    ir: IRProtocol,
    *,
    sample_rate_hz: float,
    channel_names: tuple[str, ...],
) -> TranspiledProtocol:
    tp = TranspiledProtocol()
    _emit_inputs(ir, tp, channel_names=channel_names)
    # Derives, thresholds, reward, output added by later tasks.
    return tp


def _emit_inputs(
    ir: IRProtocol,
    tp: TranspiledProtocol,
    *,
    channel_names: tuple[str, ...],
) -> None:
    for inp in ir.inputs.values():
        impl = _instantiate_montage(inp.montage, channel_names=channel_names)
        name = inp.canonical_name

        def _stage(streams: dict[str, np.ndarray], raw_chunk: np.ndarray, impl=impl, name=name) -> None:
            streams[name] = impl.step(raw_chunk)

        tp.stages.append(_stage)


def _instantiate_montage(montage: Any, *, channel_names: tuple[str, ...]) -> impls.PrimitiveImpl:
    callee = montage.callee
    static = {kw.name: _to_py(kw.value) for kw in montage.kwargs}
    if callee == "referential":
        return impls.ReferentialImpl(
            active=static["active"], reference=static["reference"],
            channel_names=channel_names,
        )
    if callee == "bipolar":
        return impls.BipolarImpl(
            plus=static["plus"], minus=static["minus"],
            channel_names=channel_names,
        )
    raise NotImplementedError(f"montage type {callee!r} not supported in P1 transpiler")


def _to_py(expr: Any) -> Any:
    """Convert an IR literal expr to a plain Python value.

    Mirrors `refrain.eval_._to_python_value` semantics for the subset of
    literal kinds the P1 transpiler accepts (string, int, float, duration,
    voltage, frequency, percent, tuple, list).
    """
    from refrain.eval_ import _to_python_value  # type: ignore[attr-defined]
    return _to_python_value(expr)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_transpile_input.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add bench/harness/transpile.py tests/bench/test_transpile_input.py
git commit -m "bench: IR transpiler — input stage (referential, bipolar)"
```

---

## Task 9: IR transpiler — derive pipelines

Extend the transpiler to handle `derive` blocks whose pipelines consist of `bandpass`, `hilbert`, `magnitude`, `rectify`, `smooth`, `differentiate`. The implementation reads the IR's pipeline list and chains the corresponding `PrimitiveImpl` instances. Equivalence to Refrain is checked against `micro_02_bandpass` and `micro_03_envelope`.

**Files:**
- Modify: `bench/harness/transpile.py`
- Test: `tests/bench/test_transpile_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_transpile_pipeline.py
"""Transpiler: derive pipelines. Compares per-chunk stream output against
Refrain's Evaluator(record_streams=True) on the same input."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

from bench.harness.equivalence import assert_equivalent
from bench.harness.runner import ChunkedRunner
from bench.harness.transpile import transpile

REPO = Path(__file__).resolve().parent.parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

SAMPLE_RATE_HZ = 256.0
CHUNK_SIZE = 32
N_SAMPLES = 4096                                   # 16 seconds at 256 Hz
WARMUP_SAMPLES = 1024                              # 4 seconds, covers filter settling


def _ir(filename: str):
    return resolve(parse_file(PROTOCOLS / filename), load_amp_profile(AMP_Q21))


def _run_refrain(ir, input_signal, channel_names) -> dict[str, np.ndarray]:
    ev = Evaluator.live(
        ir,
        sample_rate_hz=SAMPLE_RATE_HZ,
        channel_names=channel_names,
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    runner = ChunkedRunner(chunk_size=CHUNK_SIZE)

    class _Adapter:
        def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    return runner.run(_Adapter(), input_signal).streams


def _run_transpiled(ir, input_signal, channel_names) -> dict[str, np.ndarray]:
    tp = transpile(ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=channel_names)
    return ChunkedRunner(chunk_size=CHUNK_SIZE).run(tp, input_signal).streams


@pytest.mark.parametrize("filename", [
    "micro_02_bandpass.refrain",
    "micro_03_envelope.refrain",
])
def test_transpile_pipeline_matches_refrain(filename: str):
    ir = _ir(filename)
    channel_names = ("Cz", "linked_ears")
    rng = np.random.default_rng(0)
    input_signal = rng.standard_normal((N_SAMPLES, 2)) * 10.0

    refrain_out = _run_refrain(ir, input_signal, channel_names)
    transpiled_out = _run_transpiled(ir, input_signal, channel_names)

    assert_equivalent(
        refrain_out, transpiled_out,
        warmup_samples=WARMUP_SAMPLES,
        atol=1e-9, rtol=1e-6,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_transpile_pipeline.py -v`
Expected: FAIL — `_emit_derives` not called; `smr_bp` / `smr_envelope` missing from transpiled output.

- [ ] **Step 3: Add derive-stage emission**

In `bench/harness/transpile.py`, add a `_emit_derives` function and call it from `transpile()`. The implementation reads each `IRDerive`'s `expression` and, for derives whose top-level expression is a pipeline call chain, constructs the chained primitive impls.

```python
def transpile(
    ir: IRProtocol,
    *,
    sample_rate_hz: float,
    channel_names: tuple[str, ...],
) -> TranspiledProtocol:
    tp = TranspiledProtocol()
    _emit_inputs(ir, tp, channel_names=channel_names)
    _emit_derives(ir, tp, sample_rate_hz=sample_rate_hz)
    return tp


def _emit_derives(ir: IRProtocol, tp: TranspiledProtocol, *, sample_rate_hz: float) -> None:
    from refrain.eval_ import _classify_call  # type: ignore[attr-defined]

    for d in ir.derives.values():
        name = d.canonical_name
        chain = _build_chain(d.expression, sample_rate_hz=sample_rate_hz)
        source_name = _derive_source_name(d.expression)

        def _stage(streams, raw_chunk, name=name, chain=chain, source_name=source_name):
            x = streams[source_name]
            for impl in chain:
                x = impl.step(x)
            streams[name] = x

        tp.stages.append(_stage)


def _derive_source_name(expr: Any) -> str:
    """Walk to the leaf of a pipeline expression chain and return the source
    stream name (e.g., 'raw'). Pipeline expressions wrap an inner IRRef in
    successive IRCalls.
    """
    from refrain.ir import IRCall, IRRef
    node = expr
    while isinstance(node, IRCall):
        if not node.positional:
            raise NotImplementedError(
                "P1 transpiler only supports linear pipelines (single positional input)"
            )
        node = node.positional[0]
    if isinstance(node, IRRef):
        return node.name
    raise NotImplementedError(f"unsupported derive expression: {type(node).__name__}")


def _build_chain(expr: Any, *, sample_rate_hz: float) -> list[impls.PrimitiveImpl]:
    """Walk a pipeline expression outermost-first, return primitive impls in
    apply order (innermost first)."""
    from refrain.ir import IRCall

    calls: list[Any] = []
    node = expr
    while isinstance(node, IRCall):
        calls.append(node)
        node = node.positional[0]
    calls.reverse()
    return [_instantiate_primitive(c, sample_rate_hz=sample_rate_hz) for c in calls]


def _instantiate_primitive(call: Any, *, sample_rate_hz: float) -> impls.PrimitiveImpl:
    callee = call.callee
    static = {kw.name: _to_py(kw.value) for kw in call.kwargs}
    if callee == "bandpass":
        band = static["band"]
        order = static.get("order", 4)
        kind = static.get("kind", "butterworth")
        return impls.make_filter_impl(
            kind=kind, band=band, order=order, sample_rate_hz=sample_rate_hz,
        )
    if callee == "hilbert":
        method = static.get("method", "fir")
        n_taps = static.get("n_taps", 65)
        if method != "fir":
            raise NotImplementedError(f"hilbert method {method!r} not in P1")
        return impls.HilbertFirImpl(n_taps=n_taps)
    if callee == "magnitude":
        return impls.MagnitudeImpl()
    if callee == "rectify":
        return impls.RectifyImpl()
    if callee == "smooth":
        tau_s = static["tau"] / 1000.0 if "tau_unit" in static and static["tau_unit"] == "ms" else static["tau"]
        return impls.SmoothImpl(tau_s=tau_s, sample_rate_hz=sample_rate_hz)
    if callee == "differentiate":
        return impls.DifferentiateImpl(sample_rate_hz=sample_rate_hz)
    raise NotImplementedError(f"primitive {callee!r} not supported in P1 transpiler")
```

Note: `make_filter_impl` is the existing factory in `primitive_impls.py:810`. The `smooth` tau handling above is a stub — verify how `_to_py` normalises duration literals by reading `_to_python_value` and `_scale_to_ms_if_duration` in `refrain.eval_` before finalising. If those helpers return seconds directly, simplify to `tau_s=static["tau"]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_transpile_pipeline.py -v`
Expected: PASS (2 tests).

If equivalence fails, the first failure message names the diverging stream and the first divergent sample. Common cause of failure at this stage: tau unit handling in `smooth` — fix by matching how `Evaluator._instantiate_call` constructs `SmoothImpl` (check `eval_.py:448`).

- [ ] **Step 5: Commit**

```bash
git add bench/harness/transpile.py tests/bench/test_transpile_pipeline.py
git commit -m "bench: transpiler — derive pipelines (bandpass/hilbert/magnitude/smooth)"
```

---

## Task 10: IR transpiler — thresholds

Add `threshold` stage handling: `percentile(target_pct, window)` and `absolute(value)` types. The implementation mirrors the threshold loop inside `Evaluator._process_chunk` (`eval_.py:591-597`): per-threshold impl, fed the signal it tracks. Equivalence checked against `micro_04_threshold`.

**Files:**
- Modify: `bench/harness/transpile.py`
- Test: `tests/bench/test_transpile_threshold.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_transpile_threshold.py
"""Transpiler: percentile and absolute thresholds."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

from bench.harness.equivalence import assert_equivalent
from bench.harness.runner import ChunkedRunner
from bench.harness.transpile import transpile

REPO = Path(__file__).resolve().parent.parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

SAMPLE_RATE_HZ = 256.0
CHUNK_SIZE = 32
N_SAMPLES = 4096
# Threshold uses a 2-min percentile window. We can't simulate two minutes
# of warmup in a unit test, but the percentile state stabilises rapidly
# under deterministic input. Empirically 60-second warmup is sufficient.
WARMUP_SAMPLES = int(60 * SAMPLE_RATE_HZ)


def test_transpile_threshold_matches_refrain():
    ir = resolve(parse_file(PROTOCOLS / "micro_04_threshold.refrain"),
                 load_amp_profile(AMP_Q21))
    channel_names = ("Cz", "linked_ears")

    # Generate enough signal for the percentile window to populate.
    rng = np.random.default_rng(0)
    input_signal = rng.standard_normal((N_SAMPLES + WARMUP_SAMPLES, 2)) * 10.0
    # Round length to chunk-divisible.
    total = (input_signal.shape[0] // CHUNK_SIZE) * CHUNK_SIZE
    input_signal = input_signal[:total]

    ev = Evaluator.live(
        ir,
        sample_rate_hz=SAMPLE_RATE_HZ,
        channel_names=channel_names,
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    runner = ChunkedRunner(chunk_size=CHUNK_SIZE)

    class _RefrainAdapter:
        def step(self, raw_chunk):
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    refrain_out = runner.run(_RefrainAdapter(), input_signal).streams
    tp = transpile(ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=channel_names)
    transpiled_out = ChunkedRunner(chunk_size=CHUNK_SIZE).run(tp, input_signal).streams

    assert "smr_t" in refrain_out, "refrain must expose threshold as a stream"
    assert "smr_t" in transpiled_out, "transpiler must emit threshold stream"

    assert_equivalent(
        refrain_out, transpiled_out,
        warmup_samples=WARMUP_SAMPLES,
        atol=1e-9, rtol=1e-6,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_transpile_threshold.py -v`
Expected: FAIL — `smr_t` missing from transpiled output.

- [ ] **Step 3: Add threshold emission**

In `bench/harness/transpile.py`:

```python
def transpile(
    ir: IRProtocol,
    *,
    sample_rate_hz: float,
    channel_names: tuple[str, ...],
) -> TranspiledProtocol:
    tp = TranspiledProtocol()
    _emit_inputs(ir, tp, channel_names=channel_names)
    _emit_derives(ir, tp, sample_rate_hz=sample_rate_hz)
    _emit_thresholds(ir, tp, sample_rate_hz=sample_rate_hz)
    return tp


def _emit_thresholds(ir: IRProtocol, tp: TranspiledProtocol, *, sample_rate_hz: float) -> None:
    for t in ir.thresholds.values():
        impl = _instantiate_threshold(t.threshold_call, sample_rate_hz=sample_rate_hz)
        name = t.canonical_name
        signal_name = t.signal

        if isinstance(impl, impls.AbsoluteThresholdImpl):
            def _stage(streams, raw_chunk, name=name, impl=impl):
                # AbsoluteThreshold ignores its input shape but expects a 1-D
                # array of the chunk length, per Evaluator's contract.
                n = raw_chunk.shape[0]
                streams[name] = impl.step(np.zeros(n))
        else:
            def _stage(streams, raw_chunk, name=name, signal_name=signal_name, impl=impl):
                streams[name] = impl.step(streams[signal_name])

        tp.stages.append(_stage)


def _instantiate_threshold(call: Any, *, sample_rate_hz: float) -> impls.PrimitiveImpl:
    callee = call.callee
    static = {kw.name: _to_py(kw.value) for kw in call.kwargs}
    if callee == "percentile":
        target_pct = static["target_pct"]
        window_s = static["window"]                          # already in seconds via _to_py
        return impls.PercentileThresholdImpl(
            target_pct=target_pct, window_s=window_s, sample_rate_hz=sample_rate_hz,
        )
    if callee == "absolute":
        value = static["value"] if "value" in static else next(iter(static.values()))
        return impls.AbsoluteThresholdImpl(value=value)
    raise NotImplementedError(f"threshold type {callee!r} not supported in P1 transpiler")
```

Note: the exact constructor signatures of `PercentileThresholdImpl` and `AbsoluteThresholdImpl` are at `primitive_impls.py:447` and `:437`. Read them before finalising the kwargs. The `absolute(8 uV)` syntax in `.refrain` files passes a positional voltage literal; `_to_py` should return it as a float in microvolts — verify by inspecting `_to_python_value`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_transpile_threshold.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add bench/harness/transpile.py tests/bench/test_transpile_threshold.py
git commit -m "bench: transpiler — thresholds (percentile, absolute)"
```

---

## Task 11: IR transpiler — reward and output

Add reward and output stages. Covers `sigmoid` for continuous reward; `dwell(condition: all_of([above/below(...)]), duration: ms)` for event reward; output bindings with muting/clamping that mirror `Evaluator._process_chunk:635-651`.

**Files:**
- Modify: `bench/harness/transpile.py`
- Test: `tests/bench/test_transpile_reward.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_transpile_reward.py
"""Transpiler: reward stage (sigmoid continuous, dwell event) and outputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

from bench.harness.equivalence import assert_equivalent
from bench.harness.runner import ChunkedRunner
from bench.harness.transpile import transpile

REPO = Path(__file__).resolve().parent.parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

SAMPLE_RATE_HZ = 256.0
CHUNK_SIZE = 32
WARMUP_SAMPLES = int(60 * SAMPLE_RATE_HZ)
N_SAMPLES = WARMUP_SAMPLES + 2048


def _equiv_check(filename: str, channel_names: tuple[str, ...]):
    ir = resolve(parse_file(PROTOCOLS / filename), load_amp_profile(AMP_Q21))
    rng = np.random.default_rng(0)
    input_signal = rng.standard_normal((N_SAMPLES, len(channel_names))) * 10.0
    total = (input_signal.shape[0] // CHUNK_SIZE) * CHUNK_SIZE
    input_signal = input_signal[:total]

    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=channel_names,
        record_streams=True,
    )
    ev.start(skip_warmup=True)

    class _Adapter:
        def step(self, raw_chunk):
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    refrain_out = ChunkedRunner(chunk_size=CHUNK_SIZE).run(_Adapter(), input_signal).streams
    tp = transpile(ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=channel_names)
    transpiled_out = ChunkedRunner(chunk_size=CHUNK_SIZE).run(tp, input_signal).streams

    assert_equivalent(
        refrain_out, transpiled_out,
        warmup_samples=WARMUP_SAMPLES,
        atol=1e-9, rtol=1e-6,
    )


def test_transpile_micro_05_reward():
    _equiv_check("micro_05_reward.refrain", ("Cz", "linked_ears"))


def test_transpile_realistic_smr():
    _equiv_check("realistic_smr.refrain", ("Cz", "linked_ears"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_transpile_reward.py -v`
Expected: FAIL — reward / output streams missing or non-equivalent.

- [ ] **Step 3: Add reward and output emission**

In `bench/harness/transpile.py`, expand `transpile()` and add helpers. The reward and output stages reproduce `Evaluator._process_chunk`'s logic from line 612 through line 651, minus the warmup gate and event emission (which we don't need — we only need the stream values).

```python
def transpile(
    ir: IRProtocol,
    *,
    sample_rate_hz: float,
    channel_names: tuple[str, ...],
) -> TranspiledProtocol:
    tp = TranspiledProtocol()
    _emit_inputs(ir, tp, channel_names=channel_names)
    _emit_derives(ir, tp, sample_rate_hz=sample_rate_hz)
    _emit_thresholds(ir, tp, sample_rate_hz=sample_rate_hz)
    _emit_reward_and_output(ir, tp, sample_rate_hz=sample_rate_hz)
    return tp


def _emit_reward_and_output(ir: IRProtocol, tp: TranspiledProtocol, *, sample_rate_hz: float) -> None:
    """Emit reward.continuous, reward.event, and output bindings as additional
    stream entries. Stream names match Evaluator's tap conventions so they
    compare cleanly:
      - "reward.continuous" — sigmoid output, shape (n,)
      - "reward.event"      — dwell boolean stream, shape (n,)
      - "output/<channel>"  — per-output binding stream, shape (n,)
    """
    reward = ir.reward
    output_bindings = ir.output

    # Pre-instantiate dwell impls (stateful).
    dwell_impl: impls.DwellImpl | None = None
    if reward.event is not None:
        dwell_impl = _instantiate_dwell(reward.event, sample_rate_hz=sample_rate_hz)

    def _stage(streams, raw_chunk, dwell_impl=dwell_impl):
        n = raw_chunk.shape[0]
        if reward.continuous is not None:
            streams["reward.continuous"] = _eval_simple_expr(
                reward.continuous, streams, n,
            )
        if reward.event is not None and dwell_impl is not None:
            condition_stream = _eval_dwell_condition(reward.event, streams, n)
            streams["reward.event"] = dwell_impl.step(condition_stream).fired
        for channel, expr in output_bindings.items():
            val = _eval_simple_expr(expr, streams, n,
                                    reward_continuous=streams.get("reward.continuous"),
                                    reward_event=streams.get("reward.event"))
            if _is_event_output(expr):
                streams[f"output/{channel}"] = val.astype(bool)
            else:
                streams[f"output/{channel}"] = np.clip(val, 0.0, 1.0)

    tp.stages.append(_stage)


def _instantiate_dwell(event_call: Any, *, sample_rate_hz: float) -> impls.DwellImpl:
    static = {kw.name: _to_py(kw.value) for kw in event_call.kwargs}
    duration_ms = static["duration"]              # _to_py normalises to ms for duration literals
    return impls.DwellImpl(duration_s=duration_ms / 1000.0, sample_rate_hz=sample_rate_hz)


def _eval_dwell_condition(event_call: Any, streams: dict[str, np.ndarray], n: int) -> np.ndarray:
    """Evaluate the `condition:` argument of a dwell event. P1 supports:
       above(signal, threshold), below(signal, threshold),
       all_of([...]), any_of([...]) nested.
    """
    cond = next(kw.value for kw in event_call.kwargs if kw.name == "condition")
    return _eval_bool_expr(cond, streams, n)


def _eval_bool_expr(expr: Any, streams: dict[str, np.ndarray], n: int) -> np.ndarray:
    from refrain.ir import IRCall
    if not isinstance(expr, IRCall):
        raise NotImplementedError(f"P1 reward condition: unsupported expr type {type(expr).__name__}")
    callee = expr.callee
    if callee == "above":
        a = _resolve_signal(expr.positional[0], streams)
        b = _resolve_signal(expr.positional[1], streams)
        return (a > b).astype(bool)
    if callee == "below":
        a = _resolve_signal(expr.positional[0], streams)
        b = _resolve_signal(expr.positional[1], streams)
        return (a < b).astype(bool)
    if callee == "all_of":
        items = expr.positional[0]                # IRListLit
        result = np.ones(n, dtype=bool)
        for item in items.elements:
            result &= _eval_bool_expr(item, streams, n)
        return result
    if callee == "any_of":
        items = expr.positional[0]
        result = np.zeros(n, dtype=bool)
        for item in items.elements:
            result |= _eval_bool_expr(item, streams, n)
        return result
    raise NotImplementedError(f"P1 transpiler: boolean op {callee!r} unsupported")


def _resolve_signal(expr: Any, streams: dict[str, np.ndarray]) -> np.ndarray:
    """A signal reference inside a reward condition can be a string literal
    naming a stream, or an IRRef. Both resolve to the named stream array."""
    from refrain.ir import IRRef, IRStringLit
    if isinstance(expr, IRStringLit):
        return streams[expr.value]
    if isinstance(expr, IRRef):
        return streams[expr.name]
    raise NotImplementedError(f"P1 transpiler: signal ref type {type(expr).__name__}")


def _eval_simple_expr(
    expr: Any,
    streams: dict[str, np.ndarray],
    n: int,
    *,
    reward_continuous: np.ndarray | None = None,
    reward_event: np.ndarray | None = None,
) -> np.ndarray:
    """Evaluate a reward.continuous or output-binding expression. P1 supports:
       - sigmoid(signal_or_ratio, midpoint=, steepness=)
       - linear(signal, ...)                     (not used in P1 protocols but cheap)
       - reward.continuous / reward.event references
       - division of two signal refs ("a" / "b")
       - bare numeric literals (e.g., `output { audio_gain = 0 }`)
    """
    from refrain.ir import IRBinOp, IRCall, IRMemberAccess, IRNumberLit, IRRef, IRStringLit

    if isinstance(expr, IRNumberLit):
        return np.full(n, float(expr.value), dtype=np.float64)
    if isinstance(expr, IRMemberAccess):
        path = ".".join(_member_path(expr))
        if path == "reward.continuous" and reward_continuous is not None:
            return reward_continuous.astype(np.float64)
        if path == "reward.event" and reward_event is not None:
            return reward_event.astype(np.float64)
        raise NotImplementedError(f"P1 transpiler: member access {path!r}")
    if isinstance(expr, IRBinOp):
        left = _eval_simple_expr(expr.left, streams, n,
                                 reward_continuous=reward_continuous, reward_event=reward_event)
        right = _eval_simple_expr(expr.right, streams, n,
                                  reward_continuous=reward_continuous, reward_event=reward_event)
        if expr.op == "/":
            return left / right
        if expr.op == "*":
            return left * right
        if expr.op == "+":
            return left + right
        if expr.op == "-":
            return left - right
        raise NotImplementedError(f"P1 transpiler: binop {expr.op!r}")
    if isinstance(expr, IRCall) and expr.callee == "sigmoid":
        x = _eval_simple_expr(expr.positional[0], streams, n,
                              reward_continuous=reward_continuous, reward_event=reward_event)
        static = {kw.name: _to_py(kw.value) for kw in expr.kwargs}
        midpoint = float(static["midpoint"])
        steepness = float(static["steepness"])
        return 1.0 / (1.0 + np.exp(-steepness * (x - midpoint)))
    if isinstance(expr, IRStringLit):
        return streams[expr.value]
    if isinstance(expr, IRRef):
        return streams[expr.name]
    raise NotImplementedError(f"P1 transpiler: expression type {type(expr).__name__}")


def _member_path(expr: Any) -> list[str]:
    from refrain.ir import IRMemberAccess, IRRef
    parts: list[str] = []
    node = expr
    while isinstance(node, IRMemberAccess):
        parts.append(node.member)
        node = node.target
    if isinstance(node, IRRef):
        parts.append(node.name)
    parts.reverse()
    return parts


def _is_event_output(expr: Any) -> bool:
    """True iff this output binding ultimately resolves to `reward.event`."""
    from refrain.ir import IRMemberAccess
    if isinstance(expr, IRMemberAccess):
        return ".".join(_member_path(expr)) == "reward.event"
    return False
```

Caveats to verify before finalising this task (read the noted lines, adjust accordingly):

- `_classify_call`, `_to_python_value`, `_scale_to_ms_if_duration` live in `refrain/eval_.py` at lines 115, 174, 1095. The exact unit normalisation for durations may differ from what's assumed above.
- `DwellImpl`'s constructor: `primitive_impls.py:511`. Its `.step()` returns a `DwellResult` dataclass (`:497`) — confirm `.fired` is the right field name.
- The IR node class names (`IRBinOp`, `IRMemberAccess`, `IRListLit`, `IRStringLit`, `IRNumberLit`, `IRRef`, `IRCall`) must match the real definitions in `refrain/ir.py`. If a name differs (e.g., `IRBinaryOp`), update the import and the `isinstance` checks throughout.

These are the load-bearing details. Don't paper over a name mismatch — fix it where it appears, then re-run the test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_transpile_reward.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run all transpiler tests together to catch regressions**

Run: `pytest tests/bench/test_transpile_input.py tests/bench/test_transpile_pipeline.py tests/bench/test_transpile_threshold.py tests/bench/test_transpile_reward.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 6: Commit**

```bash
git add bench/harness/transpile.py tests/bench/test_transpile_reward.py
git commit -m "bench: transpiler — reward and output (sigmoid, dwell, bindings)"
```

---

## Task 12: Idiomatic baselines — microbench ladder

Hand-written numpy/scipy baselines, one per microbench. Each baseline lives in its own module exposing a `Baseline` class with `step(raw_chunk) -> dict[str, np.ndarray]` whose keys match the protocol's stream names. Each baseline must pass equivalence against Refrain for the same input — this is what makes the eventual DSL-tax number defensible.

**Files:**
- Create: `bench/baselines/micro_01_passthrough_idiomatic.py`
- Create: `bench/baselines/micro_02_bandpass_idiomatic.py`
- Create: `bench/baselines/micro_03_envelope_idiomatic.py`
- Create: `bench/baselines/micro_04_threshold_idiomatic.py`
- Create: `bench/baselines/micro_05_reward_idiomatic.py`
- Test: `tests/bench/test_baselines_idiomatic.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_baselines_idiomatic.py
"""Idiomatic baselines: equivalence against Refrain.

This is the equivalence gate that makes (a) baselines load-bearing. If any
baseline disagrees with Refrain, the DSL-tax measurement built on it is
worthless. CI must run this on every PR.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

from bench.harness.equivalence import assert_equivalent
from bench.harness.runner import ChunkedRunner

REPO = Path(__file__).resolve().parent.parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

SAMPLE_RATE_HZ = 256.0
CHUNK_SIZE = 32
WARMUP_SAMPLES = int(60 * SAMPLE_RATE_HZ)
N_SAMPLES = WARMUP_SAMPLES + 2048

MICRO_CASES = [
    ("micro_01_passthrough", "bench.baselines.micro_01_passthrough_idiomatic"),
    ("micro_02_bandpass",    "bench.baselines.micro_02_bandpass_idiomatic"),
    ("micro_03_envelope",    "bench.baselines.micro_03_envelope_idiomatic"),
    ("micro_04_threshold",   "bench.baselines.micro_04_threshold_idiomatic"),
    ("micro_05_reward",      "bench.baselines.micro_05_reward_idiomatic"),
]
# Task 13 extends ALL_CASES with realistic protocols.
ALL_CASES = MICRO_CASES


def _check_equivalence(protocol_stem: str, baseline_module: str) -> None:
    ir = resolve(parse_file(PROTOCOLS / f"{protocol_stem}.refrain"),
                 load_amp_profile(AMP_Q21))
    channel_names = ("Cz", "linked_ears")
    rng = np.random.default_rng(0)
    input_signal = rng.standard_normal((N_SAMPLES, len(channel_names))) * 10.0
    total = (input_signal.shape[0] // CHUNK_SIZE) * CHUNK_SIZE
    input_signal = input_signal[:total]

    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=channel_names,
        record_streams=True,
    )
    ev.start(skip_warmup=True)

    class _Adapter:
        def step(self, raw_chunk):
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    refrain_out = ChunkedRunner(chunk_size=CHUNK_SIZE).run(_Adapter(), input_signal).streams

    baseline_cls = importlib.import_module(baseline_module).Baseline
    baseline = baseline_cls(sample_rate_hz=SAMPLE_RATE_HZ, channel_names=channel_names)
    baseline_out = ChunkedRunner(chunk_size=CHUNK_SIZE).run(baseline, input_signal).streams

    # Idiomatic baselines use scipy.signal.sosfilt internally, which is
    # numerically close to but not bit-identical to refrain's internal filter
    # (different SOS factorisation / state init). Loosen the absolute
    # tolerance accordingly; the rtol gate still catches genuine drift.
    assert_equivalent(
        refrain_out, baseline_out,
        warmup_samples=WARMUP_SAMPLES,
        atol=1e-6, rtol=1e-4,
    )


@pytest.mark.parametrize(("protocol_stem", "baseline_module"), ALL_CASES)
def test_idiomatic_baseline_equivalent_to_refrain(
    protocol_stem: str, baseline_module: str,
):
    _check_equivalence(protocol_stem, baseline_module)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_baselines_idiomatic.py -v`
Expected: FAIL — baseline modules don't exist.

- [ ] **Step 3: Implement `micro_01_passthrough_idiomatic.py`**

```python
# bench/baselines/micro_01_passthrough_idiomatic.py
"""Idiomatic baseline for micro_01_passthrough.

Pipeline: referential montage (active - reference) → emit as 'raw'.
"""

from __future__ import annotations

import numpy as np


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.active_idx = channel_names.index("Cz")
        self.reference_idx = channel_names.index("linked_ears")

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.active_idx] - raw_chunk[:, self.reference_idx]
        return {"raw": raw}
```

- [ ] **Step 4: Implement `micro_02_bandpass_idiomatic.py`**

```python
# bench/baselines/micro_02_bandpass_idiomatic.py
"""Idiomatic baseline for micro_02_bandpass.

Pipeline: referential montage → butter SOS bandpass (12–15 Hz, order 4).
"""

from __future__ import annotations

import numpy as np
from scipy import signal as scisig


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.active_idx = channel_names.index("Cz")
        self.reference_idx = channel_names.index("linked_ears")
        nyq = sample_rate_hz / 2.0
        self.sos = scisig.butter(
            N=4, Wn=[12.0 / nyq, 15.0 / nyq], btype="bandpass", output="sos",
        )
        self.zi = scisig.sosfilt_zi(self.sos) * 0.0   # zero initial state

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.active_idx] - raw_chunk[:, self.reference_idx]
        smr_bp, self.zi = scisig.sosfilt(self.sos, raw, zi=self.zi)
        return {"raw": raw, "smr_bp": smr_bp}
```

- [ ] **Step 5: Implement `micro_03_envelope_idiomatic.py`**

```python
# bench/baselines/micro_03_envelope_idiomatic.py
"""Idiomatic baseline for micro_03_envelope.

Pipeline: referential → bandpass (12–15 Hz) → FIR Hilbert → magnitude →
EWMA smooth (tau = 250 ms). The Hilbert FIR length must match Refrain's
default `HilbertFirImpl(n_taps=65)`; see refrain/primitive_impls.py:211.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from scipy import signal as scisig


def _design_hilbert_fir(n_taps: int) -> np.ndarray:
    """Hilbert transformer impulse response: h[n] = 2/(π·n) for odd n, 0 for
    even n, with a Hamming window. Matches `HilbertFirImpl` in refrain."""
    if n_taps % 2 == 0:
        n_taps += 1
    half = (n_taps - 1) // 2
    h = np.zeros(n_taps, dtype=np.float64)
    for i, n in enumerate(range(-half, half + 1)):
        if n != 0 and n % 2 != 0:
            h[i] = 2.0 / (np.pi * n)
    h *= np.hamming(n_taps)
    return h


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.active_idx = channel_names.index("Cz")
        self.reference_idx = channel_names.index("linked_ears")
        nyq = sample_rate_hz / 2.0
        self.bp_sos = scisig.butter(
            4, [12.0 / nyq, 15.0 / nyq], btype="bandpass", output="sos",
        )
        self.bp_zi = scisig.sosfilt_zi(self.bp_sos) * 0.0
        self.h_fir = _design_hilbert_fir(65)
        # FIR streaming: keep last (n_taps - 1) samples as overlap buffer.
        self.fir_state = np.zeros(len(self.h_fir) - 1, dtype=np.float64)
        # Pure delay to keep the real part time-aligned with the imaginary
        # (Hilbert) part, matching refrain's HilbertFirImpl behaviour.
        self.delay = deque([0.0] * ((len(self.h_fir) - 1) // 2), maxlen=(len(self.h_fir) - 1) // 2)
        # EWMA smooth: y[n] = (1 - alpha) y[n-1] + alpha x[n].
        tau_s = 0.250
        self.alpha = 1.0 - np.exp(-1.0 / (tau_s * sample_rate_hz))
        self.smooth_state = 0.0

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.active_idx] - raw_chunk[:, self.reference_idx]
        # Bandpass
        bp, self.bp_zi = scisig.sosfilt(self.bp_sos, raw, zi=self.bp_zi)
        # Hilbert FIR (imaginary part)
        padded = np.concatenate([self.fir_state, bp])
        imag = scisig.lfilter(self.h_fir, [1.0], padded)[-len(bp):]
        self.fir_state = padded[-len(self.fir_state):] if len(self.fir_state) else self.fir_state
        # Pure delay on the real part to compensate FIR group delay.
        real = np.empty_like(bp)
        for i, s in enumerate(bp):
            self.delay.append(float(s))
            real[i] = self.delay[0]
        # Magnitude
        mag = np.sqrt(real * real + imag * imag)
        # Smooth (EWMA)
        env = np.empty_like(mag)
        s = self.smooth_state
        for i, x in enumerate(mag):
            s = (1.0 - self.alpha) * s + self.alpha * float(x)
            env[i] = s
        self.smooth_state = s
        return {"raw": raw, "smr_envelope": env}
```

Note: the FIR state buffer and delay queue handling are subtle. If the equivalence test fails at this step, the most likely cause is a half-sample delay mismatch between the baseline and `HilbertFirImpl`. Open `refrain/primitive_impls.py:211` and compare the streaming convention. The baseline must match Refrain's exact framing, not "a streaming Hilbert" generally.

- [ ] **Step 6: Implement `micro_04_threshold_idiomatic.py`**

```python
# bench/baselines/micro_04_threshold_idiomatic.py
"""Idiomatic baseline for micro_04_threshold.

Pipeline: micro_03_envelope + percentile threshold (target_pct=70, window=2 min).
"""

from __future__ import annotations

from collections import deque

import numpy as np

from bench.baselines.micro_03_envelope_idiomatic import Baseline as EnvelopeBaseline


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.env = EnvelopeBaseline(
            sample_rate_hz=sample_rate_hz, channel_names=channel_names,
        )
        window_s = 120.0
        window_n = int(window_s * sample_rate_hz)
        self.target_pct = 70.0
        self.window: deque[float] = deque(maxlen=window_n)

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        out = self.env.step(raw_chunk)
        envelope = out["smr_envelope"]
        threshold_out = np.empty_like(envelope)
        for i, x in enumerate(envelope):
            self.window.append(float(x))
            if len(self.window) >= 2:
                threshold_out[i] = float(np.percentile(self.window, self.target_pct))
            else:
                threshold_out[i] = float(x)
        out["smr_t"] = threshold_out
        return out
```

Note: this naive implementation calls `np.percentile` per sample over a 30,720-sample deque at 256 Hz. That is slow but correct, which is what we need for equivalence. Performance work is P2, not here.

- [ ] **Step 7: Implement `micro_05_reward_idiomatic.py`**

```python
# bench/baselines/micro_05_reward_idiomatic.py
"""Idiomatic baseline for micro_05_reward.

Pipeline: two envelopes (SMR 12–15 Hz, theta 4–8 Hz), two percentile
thresholds, dwell event over (smr above smr_t) AND (theta below theta_t)
for 250 ms, sigmoid continuous reward on smr/smr_t.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from scipy import signal as scisig

from bench.baselines.micro_03_envelope_idiomatic import _design_hilbert_fir


class _Envelope:
    """Reusable band envelope: bandpass → Hilbert FIR → magnitude → EWMA."""

    def __init__(self, *, band: tuple[float, float], tau_s: float, sample_rate_hz: float):
        nyq = sample_rate_hz / 2.0
        self.bp_sos = scisig.butter(
            4, [band[0] / nyq, band[1] / nyq], btype="bandpass", output="sos",
        )
        self.bp_zi = scisig.sosfilt_zi(self.bp_sos) * 0.0
        self.h_fir = _design_hilbert_fir(65)
        self.fir_state = np.zeros(len(self.h_fir) - 1, dtype=np.float64)
        self.delay = deque([0.0] * ((len(self.h_fir) - 1) // 2),
                           maxlen=(len(self.h_fir) - 1) // 2)
        self.alpha = 1.0 - np.exp(-1.0 / (tau_s * sample_rate_hz))
        self.smooth_state = 0.0

    def step(self, signal: np.ndarray) -> np.ndarray:
        bp, self.bp_zi = scisig.sosfilt(self.bp_sos, signal, zi=self.bp_zi)
        padded = np.concatenate([self.fir_state, bp])
        imag = scisig.lfilter(self.h_fir, [1.0], padded)[-len(bp):]
        self.fir_state = padded[-len(self.fir_state):] if len(self.fir_state) else self.fir_state
        real = np.empty_like(bp)
        for i, s in enumerate(bp):
            self.delay.append(float(s))
            real[i] = self.delay[0]
        mag = np.sqrt(real * real + imag * imag)
        env = np.empty_like(mag)
        s = self.smooth_state
        for i, x in enumerate(mag):
            s = (1.0 - self.alpha) * s + self.alpha * float(x)
            env[i] = s
        self.smooth_state = s
        return env


class _PercentileThreshold:
    def __init__(self, *, target_pct: float, window_s: float, sample_rate_hz: float):
        self.target_pct = target_pct
        self.window: deque[float] = deque(maxlen=int(window_s * sample_rate_hz))

    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        for i, xi in enumerate(x):
            self.window.append(float(xi))
            out[i] = (float(np.percentile(self.window, self.target_pct))
                      if len(self.window) >= 2 else float(xi))
        return out


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.active_idx = channel_names.index("Cz")
        self.reference_idx = channel_names.index("linked_ears")
        self.smr_env = _Envelope(band=(12.0, 15.0), tau_s=0.250, sample_rate_hz=sample_rate_hz)
        self.theta_env = _Envelope(band=(4.0, 8.0), tau_s=0.250, sample_rate_hz=sample_rate_hz)
        self.smr_t = _PercentileThreshold(target_pct=70.0, window_s=120.0, sample_rate_hz=sample_rate_hz)
        self.theta_t = _PercentileThreshold(target_pct=30.0, window_s=120.0, sample_rate_hz=sample_rate_hz)
        self.dwell_target = int(0.250 * sample_rate_hz)
        self.dwell_counter = 0

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.active_idx] - raw_chunk[:, self.reference_idx]
        smr_e = self.smr_env.step(raw)
        theta_e = self.theta_env.step(raw)
        smr_t = self.smr_t.step(smr_e)
        theta_t = self.theta_t.step(theta_e)
        condition = (smr_e > smr_t) & (theta_e < theta_t)
        # Dwell: edge when condition has held for `dwell_target` consecutive samples.
        event = np.zeros_like(condition, dtype=bool)
        for i, c in enumerate(condition):
            if c:
                self.dwell_counter += 1
                if self.dwell_counter == self.dwell_target:
                    event[i] = True
            else:
                self.dwell_counter = 0
        ratio = smr_e / smr_t
        continuous = 1.0 / (1.0 + np.exp(-3.0 * (ratio - 1.0)))
        return {
            "raw": raw,
            "smr_envelope": smr_e,
            "theta_envelope": theta_e,
            "smr_t": smr_t,
            "theta_t": theta_t,
            "reward.continuous": continuous,
            "reward.event": event,
            "output/audio_chime": event.astype(bool),
            "output/audio_gain": np.clip(continuous, 0.0, 1.0),
        }
```

Note: the dwell edge semantics (event fires ONLY on the sample where the counter reaches the target, not every sample while it's met) must match `DwellImpl` in `refrain/primitive_impls.py:511`. If equivalence fails on `reward.event`, read `DwellImpl.step()` first and align the baseline.

- [ ] **Step 8: Run all baseline equivalence tests**

Run: `pytest tests/bench/test_baselines_idiomatic.py -v`
Expected: PASS (5 parametrized tests).

- [ ] **Step 9: Commit**

```bash
git add bench/baselines/micro_*_idiomatic.py tests/bench/test_baselines_idiomatic.py
git commit -m "bench: idiomatic baselines for microbench ladder"
```

---

## Task 13: Idiomatic baseline — realistic SMR

The realistic SMR protocol is three bands (SMR / theta / high-beta) + three thresholds + dwell + sigmoid + output gating. The baseline composes the `_Envelope` and `_PercentileThreshold` helpers from Task 12, adds an absolute threshold for high-beta, and reproduces the `output { ... }` gating from `examples/smr_cz.refrain:98-102`.

**Files:**
- Create: `bench/baselines/realistic_smr_idiomatic.py`
- Test: `tests/bench/test_baselines_idiomatic.py` (extend)

- [ ] **Step 1: Extend the parametrize list to include the realistic case**

In `tests/bench/test_baselines_idiomatic.py`, after the `MICRO_CASES` list, replace the `ALL_CASES = MICRO_CASES` line with:

```python
REALISTIC_CASES = [
    ("realistic_smr", "bench.baselines.realistic_smr_idiomatic"),
]
ALL_CASES = MICRO_CASES + REALISTIC_CASES
```

No other change to the test file — the existing `test_idiomatic_baseline_equivalent_to_refrain` already parametrizes over `ALL_CASES` and dispatches through `_check_equivalence`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_baselines_idiomatic.py::test_realistic_idiomatic_baseline_equivalent_to_refrain -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `realistic_smr_idiomatic.py`**

```python
# bench/baselines/realistic_smr_idiomatic.py
"""Idiomatic baseline for realistic_smr (= examples/smr_cz.refrain).

Three band envelopes (SMR 12–15, theta 4–8, high-beta 22–30), each with a
250 ms EWMA smoother. Adaptive percentile thresholds for SMR (target 70 %)
and theta (target 30 %). Absolute threshold for high-beta at 8 µV. Dwell
fires on all three conditions held for 250 ms. Continuous reward is
sigmoid(smr / smr_t, midpoint=1, steepness=3). Output bindings:
  audio_chime = reward.event
  audio_gain  = reward.event.holds ? reward.continuous : 0
  game_speed  = reward.event.holds ? reward.continuous : 0

For P1, `reward.event.holds` is approximated as `reward.event` (the dwell
boolean stream). If Refrain distinguishes "fires" vs "holds" elsewhere,
this is the seam to revisit before claiming equivalence.
"""

from __future__ import annotations

import numpy as np

from bench.baselines.micro_05_reward_idiomatic import _Envelope, _PercentileThreshold


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.active_idx = channel_names.index("Cz")
        self.reference_idx = channel_names.index("linked_ears")
        self.smr_env = _Envelope(band=(12.0, 15.0), tau_s=0.250, sample_rate_hz=sample_rate_hz)
        self.theta_env = _Envelope(band=(4.0, 8.0), tau_s=0.250, sample_rate_hz=sample_rate_hz)
        self.hbeta_env = _Envelope(band=(22.0, 30.0), tau_s=0.250, sample_rate_hz=sample_rate_hz)
        self.smr_t = _PercentileThreshold(target_pct=70.0, window_s=120.0, sample_rate_hz=sample_rate_hz)
        self.theta_t = _PercentileThreshold(target_pct=30.0, window_s=120.0, sample_rate_hz=sample_rate_hz)
        self.hbeta_t_value = 8.0
        self.dwell_target = int(0.250 * sample_rate_hz)
        self.dwell_counter = 0

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = raw_chunk[:, self.active_idx] - raw_chunk[:, self.reference_idx]
        smr_e = self.smr_env.step(raw)
        theta_e = self.theta_env.step(raw)
        hbeta_e = self.hbeta_env.step(raw)
        smr_t = self.smr_t.step(smr_e)
        theta_t = self.theta_t.step(theta_e)
        hbeta_t = np.full_like(hbeta_e, self.hbeta_t_value)
        condition = (smr_e > smr_t) & (theta_e < theta_t) & (hbeta_e < hbeta_t)
        event = np.zeros_like(condition, dtype=bool)
        holds = np.zeros_like(condition, dtype=bool)
        for i, c in enumerate(condition):
            if c:
                self.dwell_counter += 1
                if self.dwell_counter >= self.dwell_target:
                    holds[i] = True
                    if self.dwell_counter == self.dwell_target:
                        event[i] = True
            else:
                self.dwell_counter = 0
        ratio = smr_e / smr_t
        continuous = 1.0 / (1.0 + np.exp(-3.0 * (ratio - 1.0)))
        audio_gain = np.where(holds, np.clip(continuous, 0.0, 1.0), 0.0)
        game_speed = audio_gain                                   # same expression
        return {
            "raw": raw,
            "smr_envelope": smr_e,
            "theta_envelope": theta_e,
            "high_beta_envelope": hbeta_e,
            "smr_t": smr_t,
            "theta_t": theta_t,
            "hbeta_t": hbeta_t,
            "reward.continuous": continuous,
            "reward.event": event,
            "output/audio_chime": event,
            "output/audio_gain": audio_gain,
            "output/game_speed": game_speed,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/bench/test_baselines_idiomatic.py -v`
Expected: PASS (6 parametrized tests total).

- [ ] **Step 5: Commit**

```bash
git add bench/baselines/realistic_smr_idiomatic.py tests/bench/test_baselines_idiomatic.py
git commit -m "bench: idiomatic baseline for realistic SMR"
```

---

## Task 14: Bench CLI — `python -m bench equivalence`

A small `argparse` entry point that iterates the protocol corpus, runs Refrain + transpiler + idiomatic baseline on each, calls `assert_equivalent`, and prints a pass/fail table. This is the P1 user-facing artifact: one command that confirms the harness works on every protocol.

**Files:**
- Create: `bench/cli.py`
- Create: `bench/__main__.py`
- Test: `tests/bench/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_cli.py
"""Smoke test for the `python -m bench equivalence` CLI."""

from __future__ import annotations

import subprocess
import sys


def test_equivalence_cli_smoke():
    """Runs the CLI on a single microbench; expects exit 0 and a PASS line."""
    proc = subprocess.run(
        [sys.executable, "-m", "bench", "equivalence", "--only", "micro_01_passthrough"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"CLI failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "PASS" in proc.stdout
    assert "micro_01_passthrough" in proc.stdout


def test_equivalence_cli_help():
    proc = subprocess.run(
        [sys.executable, "-m", "bench", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert "equivalence" in proc.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/bench/test_cli.py -v`
Expected: FAIL — `python -m bench` exits non-zero (no `__main__.py`).

- [ ] **Step 3: Implement `bench/cli.py`**

```python
# bench/cli.py
"""Bench CLI: subcommands for equivalence audit and (future) timing runs."""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Iterable

import numpy as np

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

from bench.harness.equivalence import EquivalenceFailure, assert_equivalent
from bench.harness.env_capture import capture_env
from bench.harness.runner import ChunkedRunner
from bench.harness.transpile import transpile


REPO = Path(__file__).resolve().parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

# (protocol_stem, baseline_module_name, channel_names)
CORPUS: list[tuple[str, str, tuple[str, ...]]] = [
    ("micro_01_passthrough", "bench.baselines.micro_01_passthrough_idiomatic",
     ("Cz", "linked_ears")),
    ("micro_02_bandpass",    "bench.baselines.micro_02_bandpass_idiomatic",
     ("Cz", "linked_ears")),
    ("micro_03_envelope",    "bench.baselines.micro_03_envelope_idiomatic",
     ("Cz", "linked_ears")),
    ("micro_04_threshold",   "bench.baselines.micro_04_threshold_idiomatic",
     ("Cz", "linked_ears")),
    ("micro_05_reward",      "bench.baselines.micro_05_reward_idiomatic",
     ("Cz", "linked_ears")),
    ("realistic_smr",        "bench.baselines.realistic_smr_idiomatic",
     ("Cz", "linked_ears")),
]


def _run_refrain(ir, input_signal, channel_names) -> dict[str, np.ndarray]:
    ev = Evaluator.live(
        ir, sample_rate_hz=256.0, channel_names=channel_names,
        record_streams=True,
    )
    ev.start(skip_warmup=True)

    class _Adapter:
        def step(self, raw_chunk):
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    return ChunkedRunner(chunk_size=32).run(_Adapter(), input_signal).streams


def _run_transpiled(ir, input_signal, channel_names) -> dict[str, np.ndarray]:
    tp = transpile(ir, sample_rate_hz=256.0, channel_names=channel_names)
    return ChunkedRunner(chunk_size=32).run(tp, input_signal).streams


def _run_baseline(module_name, input_signal, channel_names) -> dict[str, np.ndarray]:
    cls = importlib.import_module(module_name).Baseline
    baseline = cls(sample_rate_hz=256.0, channel_names=channel_names)
    return ChunkedRunner(chunk_size=32).run(baseline, input_signal).streams


def _equivalence_run(only: Iterable[str] | None) -> int:
    corpus = CORPUS if not only else [c for c in CORPUS if c[0] in set(only)]
    if not corpus:
        print(f"no matching protocols (available: {[c[0] for c in CORPUS]})", file=sys.stderr)
        return 2

    env = capture_env()
    print(f"# Refrain bench equivalence audit (git {env['git_sha'][:12] if env['git_sha'] else '?'})")
    print(f"# python={env['python_version']}  numpy={env['numpy_version']}  scipy={env['scipy_version']}")
    print()

    rng = np.random.default_rng(0)
    warmup = int(60 * 256.0)
    n_samples = warmup + 2048
    n_samples = (n_samples // 32) * 32                            # divisible by chunk_size

    failures = 0
    for stem, baseline_module, channels in corpus:
        ir = resolve(parse_file(PROTOCOLS / f"{stem}.refrain"),
                     load_amp_profile(AMP_Q21))
        signal = rng.standard_normal((n_samples, len(channels))) * 10.0

        refrain_out = _run_refrain(ir, signal, channels)

        try:
            transpiled_out = _run_transpiled(ir, signal, channels)
            assert_equivalent(refrain_out, transpiled_out,
                              warmup_samples=warmup, atol=1e-9, rtol=1e-6)
            t_status = "PASS"
        except EquivalenceFailure as exc:
            t_status = f"FAIL  ({exc})"
            failures += 1
        except NotImplementedError as exc:
            t_status = f"SKIP  ({exc})"

        try:
            baseline_out = _run_baseline(baseline_module, signal, channels)
            assert_equivalent(refrain_out, baseline_out,
                              warmup_samples=warmup, atol=1e-6, rtol=1e-4)
            i_status = "PASS"
        except (EquivalenceFailure, ModuleNotFoundError) as exc:
            i_status = f"FAIL  ({type(exc).__name__}: {exc})"
            failures += 1

        print(f"  {stem:<32}  transpiled: {t_status:<8}  idiomatic: {i_status}")

    print()
    print(f"# {failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench",
        description="Refrain benchmark suite — phase P1 (equivalence only).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_eq = sub.add_parser("equivalence", help="Audit refrain ≡ transpiled ≡ idiomatic on all protocols.")
    p_eq.add_argument("--only", nargs="*", help="Restrict to named protocol stems (e.g. micro_01_passthrough).")
    args = parser.parse_args(argv)
    if args.cmd == "equivalence":
        return _equivalence_run(args.only)
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Implement `bench/__main__.py`**

```python
# bench/__main__.py
"""`python -m bench` entry point."""
from bench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/bench/test_cli.py -v`
Expected: PASS (2 tests).

Also run the CLI manually to confirm the human-facing output is reasonable:

Run: `python -m bench equivalence`
Expected: a 6-line PASS table and `# 0 failure(s)`.

- [ ] **Step 6: Commit**

```bash
git add bench/cli.py bench/__main__.py tests/bench/test_cli.py
git commit -m "bench: CLI — `python -m bench equivalence`"
```

---

## Task 15: Tooling integration

Extend `pyproject.toml` so pytest discovers `tests/bench/`, ruff lints `bench/`, and a dev-mode install picks up the bench package.

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/bench/test_tooling.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bench/test_tooling.py
"""Tooling: pytest discovers bench tests, ruff lints bench/."""

from __future__ import annotations

import subprocess
import sys


def test_pytest_discovers_bench_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/bench/"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"collect failed:\n{proc.stdout}\n{proc.stderr}"
    assert "test_equivalence" in proc.stdout
    assert "test_runner" in proc.stdout


def test_ruff_lints_bench_clean():
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "bench/"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"ruff:\n{proc.stdout}\n{proc.stderr}"
```

- [ ] **Step 2: Run test to verify it fails (or check it already passes)**

Run: `pytest tests/bench/test_tooling.py -v`
Expected: the first test likely PASSES already (pytest auto-discovers `tests/`); the ruff test may FAIL if any bench module triggers lints — that's the signal to fix the source rather than configure away the rule.

- [ ] **Step 3: If ruff complains, fix the offending modules**

The most likely complaints are unused imports (F401) inside `bench/harness/transpile.py` (local `from refrain.ir import ...` inside helper functions). Either move them to module-level imports or add explicit `__all__` exports. Do not silence rules with `# noqa` unless the rule is genuinely wrong.

- [ ] **Step 4: Confirm pyproject.toml requires no change**

The existing `[tool.pytest.ini_options]` block has `testpaths = ["tests"]`, which already covers `tests/bench/`. Ruff has no path filter and lints the whole repo. If a future `[tool.ruff]` section adds path filters, append `"bench"` then. Otherwise no edit is needed.

If a change IS needed (e.g., a new `[tool.ruff]` `extend-include` list appears), apply the minimum-required edit and re-run the test.

- [ ] **Step 5: Run all bench tests as a final integration check**

Run: `pytest tests/bench/ -v`
Expected: PASS for every test added in this plan (rough count: 26 tests).

- [ ] **Step 6: Commit**

```bash
git add tests/bench/test_tooling.py pyproject.toml
git commit -m "bench: tooling — pytest discovery and ruff cleanliness"
```

If `pyproject.toml` was not modified, omit it from the `git add`:

```bash
git add tests/bench/test_tooling.py
git commit -m "bench: tooling — pytest discovery and ruff cleanliness"
```

---

## Done criteria for P1

After Task 15, the following must all be true:

- `pytest tests/bench/ -v` passes (all ~26 tests).
- `pytest tests/` (full suite, including the new `tests/test_eval_record_streams.py`) passes with no regressions in any pre-existing test.
- `ruff check bench/ tests/bench/` passes clean.
- `python -m bench equivalence` exits 0 and prints a 6-line PASS table.
- `bench/results/` directory exists but is empty — populated in P2.

At that point: open a PR for P1. The follow-on P2 plan can then build on the harness to add timing, the synthetic-throttle floor (Tier A), and the publication-side artifacts.
