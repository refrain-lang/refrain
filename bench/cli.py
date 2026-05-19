"""Bench CLI: subcommands for the benchmark suite. Phase P1 ships only the
equivalence audit (refrain vs idiomatic baseline). Timing lands in P2.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Iterable

import numpy as np

from bench.harness.env_capture import capture_env
from bench.harness.equivalence import EquivalenceFailure, assert_equivalent
from bench.harness.runner import ChunkedRunner
from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

SAMPLE_RATE_HZ = 256.0
CHUNK_SIZE = 32
CHANNELS = ("Cz", "A1", "A2")
WARMUP_SAMPLES = int(60 * SAMPLE_RATE_HZ)
N_SAMPLES = (WARMUP_SAMPLES + 2048) // CHUNK_SIZE * CHUNK_SIZE

# (protocol_stem, baseline_module)
CORPUS: list[tuple[str, str]] = [
    ("micro_01_passthrough", "bench.baselines.micro_01_passthrough_idiomatic"),
    ("micro_02_bandpass", "bench.baselines.micro_02_bandpass_idiomatic"),
    ("micro_03_envelope", "bench.baselines.micro_03_envelope_idiomatic"),
    ("micro_04_threshold", "bench.baselines.micro_04_threshold_idiomatic"),
    ("micro_05_reward", "bench.baselines.micro_05_reward_idiomatic"),
    ("realistic_smr", "bench.baselines.realistic_smr_idiomatic"),
]


def _run_refrain(ir, signal) -> dict[str, np.ndarray]:
    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS,
        record_streams=True,
    )
    ev.start(skip_warmup=True)

    class _Adapter:
        def step(self, raw_chunk):
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    return ChunkedRunner(chunk_size=CHUNK_SIZE).run(_Adapter(), signal).streams


def _run_baseline(module_name, signal) -> dict[str, np.ndarray]:
    cls = importlib.import_module(module_name).Baseline
    baseline = cls(sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS)
    return ChunkedRunner(chunk_size=CHUNK_SIZE).run(baseline, signal).streams


def _equivalence_run(only: Iterable[str] | None) -> int:
    corpus = CORPUS if not only else [c for c in CORPUS if c[0] in set(only)]
    if not corpus:
        print(f"no matching protocols (available: {[c[0] for c in CORPUS]})", file=sys.stderr)
        return 2

    env = capture_env()
    sha = env["git_sha"][:12] if env["git_sha"] else "?"
    print(f"# Refrain bench equivalence audit (git {sha})")
    print(
        f"# python={env['python_version']}"
        f"  numpy={env['numpy_version']}"
        f"  scipy={env['scipy_version']}"
    )
    print()

    rng = np.random.default_rng(0)
    failures = 0
    for stem, baseline_module in corpus:
        ir = resolve(parse_file(PROTOCOLS / f"{stem}.refrain"), load_amp_profile(AMP_Q21))
        signal = rng.standard_normal((N_SAMPLES, len(CHANNELS))) * 10.0
        refrain_out = _run_refrain(ir, signal)
        try:
            baseline_out = _run_baseline(baseline_module, signal)
            assert_equivalent(refrain_out, baseline_out,
                              warmup_samples=WARMUP_SAMPLES, atol=1e-6, rtol=1e-4)
            status = "PASS"
        except (EquivalenceFailure, ModuleNotFoundError) as exc:
            status = f"FAIL  ({type(exc).__name__}: {exc})"
            failures += 1
        print(f"  {stem:<28}  idiomatic: {status}")

    print()
    print(f"# {failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench",
        description="Refrain benchmark suite — phase P1 (equivalence only).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_eq = sub.add_parser("equivalence",
                          help="Audit refrain == idiomatic baseline on all protocols.")
    p_eq.add_argument("--only", nargs="*",
                      help="Restrict to named protocol stems (e.g. micro_01_passthrough).")
    args = parser.parse_args(argv)
    if args.cmd == "equivalence":
        return _equivalence_run(args.only)
    return 2


if __name__ == "__main__":
    sys.exit(main())
