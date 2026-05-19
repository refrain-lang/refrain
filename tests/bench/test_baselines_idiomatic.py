"""Idiomatic baselines: equivalence against Refrain.

This is the equivalence gate that makes the baselines load-bearing. If any
baseline disagrees with Refrain, the eventual DSL-tax measurement built on it
is worthless. Later tasks extend ALL_CASES with more protocols.
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
CHANNELS = ("Cz", "A1", "A2")
WARMUP_SAMPLES = int(60 * SAMPLE_RATE_HZ)
N_SAMPLES = WARMUP_SAMPLES + 2048

# (protocol_stem, baseline_module). Extended by later tasks.
ALL_CASES = [
    ("micro_01_passthrough", "bench.baselines.micro_01_passthrough_idiomatic"),
    ("micro_02_bandpass", "bench.baselines.micro_02_bandpass_idiomatic"),
]


def _run_refrain(ir) -> tuple[dict, np.ndarray]:
    rng = np.random.default_rng(0)
    n = (N_SAMPLES // CHUNK_SIZE) * CHUNK_SIZE
    signal = rng.standard_normal((n, len(CHANNELS))) * 10.0

    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS,
        record_streams=True,
    )
    ev.start(skip_warmup=True)

    class _Adapter:
        def step(self, raw_chunk):
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    refrain_out = ChunkedRunner(chunk_size=CHUNK_SIZE).run(_Adapter(), signal).streams
    return refrain_out, signal


def _check_equivalence(protocol_stem: str, baseline_module: str) -> None:
    ir = resolve(parse_file(PROTOCOLS / f"{protocol_stem}.refrain"),
                 load_amp_profile(AMP_Q21))
    refrain_out, signal = _run_refrain(ir)

    print(f"[{protocol_stem}] refrain keys: {sorted(refrain_out)}")

    baseline_cls = importlib.import_module(baseline_module).Baseline
    baseline = baseline_cls(sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS)
    baseline_out = ChunkedRunner(chunk_size=CHUNK_SIZE).run(baseline, signal).streams

    assert_equivalent(
        refrain_out, baseline_out,
        warmup_samples=WARMUP_SAMPLES,
        atol=1e-6, rtol=1e-4,
    )


@pytest.mark.parametrize(("protocol_stem", "baseline_module"), ALL_CASES)
def test_idiomatic_baseline_equivalent_to_refrain(protocol_stem, baseline_module):
    _check_equivalence(protocol_stem, baseline_module)
