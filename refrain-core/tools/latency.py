"""Three-way latency + in-process equivalence for the Rust core.

Drives Rust, the Python evaluator, and the idiomatic NumPy baseline through
the *same* bench `ChunkedRunner`, on the same seeded signal, and reports
per-chunk P50/P95/P99. Also re-asserts Rust == Python-evaluator in-process.

Run from the worktree root:
    PYTHONPATH=. ./.venv/bin/python refrain-core/tools/latency.py
"""

from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
import refrain_core

from bench.harness.equivalence import assert_equivalent
from bench.harness.runner import ChunkedRunner
from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.ir_json import ir_to_json
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parents[2]
AMP = load_amp_profile(REPO / "src" / "refrain" / "amp_profiles" / "q21.json")

SAMPLE_RATE_HZ = 256.0
CHANNELS = ("Cz", "A1", "A2")
CHUNK_SIZE = 32
N_SAMPLES = 16384
WARMUP_SAMPLES = 2048
SEED = 0


def _pcts(ns) -> tuple[float, float, float]:
    a = np.asarray(ns, dtype=np.float64)
    p50, p95, p99 = np.percentile(a, [50, 95, 99])
    return p50, p95, p99


def _run(stem: str, baseline_module: str) -> None:
    ir = resolve(parse_file(REPO / "bench" / "protocols" / f"{stem}.refrain"), AMP)
    ir_json = ir_to_json(ir, sample_rate_hz=SAMPLE_RATE_HZ)

    rng = np.random.default_rng(SEED)
    signal = rng.standard_normal((N_SAMPLES, len(CHANNELS))) * 10.0

    # Python evaluator (reference).
    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS, record_streams=True
    )
    ev.start(skip_warmup=True)

    class PyAdapter:
        def step(self, chunk):
            ev.step_chunk(chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    py = ChunkedRunner(chunk_size=CHUNK_SIZE).run(PyAdapter(), signal)

    # Rust core via PyO3.
    rce = refrain_core.RustEvaluator(ir_json, SAMPLE_RATE_HZ, list(CHANNELS))

    class RustAdapter:
        def step(self, chunk):
            return rce.step_chunk(np.ascontiguousarray(chunk, dtype=np.float64))

    rs = ChunkedRunner(chunk_size=CHUNK_SIZE).run(RustAdapter(), signal)

    # Idiomatic NumPy baseline.
    baseline = importlib.import_module(baseline_module).Baseline(
        sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS
    )
    bl = ChunkedRunner(chunk_size=CHUNK_SIZE).run(baseline, signal)

    # In-process equivalence: Rust must match the Python evaluator.
    assert_equivalent(
        rs.streams, py.streams, warmup_samples=WARMUP_SAMPLES, atol=1e-6, rtol=1e-4
    )

    print(f"{stem}  (chunk={CHUNK_SIZE} samples, {len(py.per_chunk_ns)} chunks)")
    print("  impl          P50        P95        P99")
    for name, res in (("rust", rs), ("py-refrain", py), ("numpy", bl)):
        p50, p95, p99 = _pcts(res.per_chunk_ns)
        print(f"  {name:<11} {p50/1000:7.2f}us {p95/1000:7.2f}us {p99/1000:7.2f}us")
    print("  equivalence: Rust == Python-evaluator  (atol=1e-6, rtol=1e-4)  PASS")
    print()


if __name__ == "__main__":
    _run("micro_03_envelope", "bench.baselines.micro_03_envelope_idiomatic")
    _run("micro_04_threshold", "bench.baselines.micro_04_threshold_idiomatic")
    _run("micro_05_reward", "bench.baselines.micro_05_reward_idiomatic")
