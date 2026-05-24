"""Generate golden-vector fixtures for the Rust core PoC.

For each protocol we emit:
  - `<stem>.ir.json` — the IR-JSON wire format (input to the Rust core)
  - `<stem>.io.json` — a seeded input signal plus the *reference* output
    streams from the Python evaluator (the ground truth the Rust core must
    reproduce within tolerance)

This is the Phase-4 golden-vector strategy: capture (input, IR, output) from
the canonical Python implementation; the Rust core reproduces it. Run from
the worktree venv:  ./.venv/bin/python refrain-core/tools/gen_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bench.harness.runner import ChunkedRunner
from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
AMP = load_amp_profile(REPO / "src" / "refrain" / "amp_profiles" / "q21.json")

SAMPLE_RATE_HZ = 256.0
CHANNELS = ("Cz", "A1", "A2")
CHUNK_SIZE = 32
N_SAMPLES = 4096
WARMUP_SAMPLES = 512
SEED = 0


def _reference_streams(ir, signal: np.ndarray) -> dict[str, np.ndarray]:
    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS, record_streams=True
    )
    ev.start(skip_warmup=True)

    class _Adapter:
        def step(self, raw_chunk):
            ev.step_chunk(raw_chunk)
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    return ChunkedRunner(chunk_size=CHUNK_SIZE).run(_Adapter(), signal).streams


def generate(stem: str) -> None:
    ir = resolve(parse_file(REPO / "bench" / "protocols" / f"{stem}.refrain"), AMP)
    # Bake at the rate the runtime actually uses (a host choice >= the
    # protocol minimum), which can differ from the resolver's default.
    (FIX / f"{stem}.ir.json").write_text(
        json.dumps(ir_to_json_obj(ir, sample_rate_hz=SAMPLE_RATE_HZ), indent=2, sort_keys=True)
    )

    rng = np.random.default_rng(SEED)
    signal = rng.standard_normal((N_SAMPLES, len(CHANNELS))) * 10.0
    streams = _reference_streams(ir, signal)

    io = {
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "channels": list(CHANNELS),
        "chunk_size": CHUNK_SIZE,
        "warmup_samples": WARMUP_SAMPLES,
        "n_samples": N_SAMPLES,
        "seed": SEED,
        "input": signal.tolist(),
        # Cast every stream to float64 so boolean event/holds streams compare
        # numerically (0.0/1.0) against the Rust core's f64 output.
        "streams": {
            k: np.asarray(v, dtype=np.float64).tolist() for k, v in streams.items()
        },
    }
    (FIX / f"{stem}.io.json").write_text(json.dumps(io))
    print(f"{stem}: ir+io written; reference streams = {sorted(streams)}")


if __name__ == "__main__":
    FIX.mkdir(parents=True, exist_ok=True)
    # realistic_smr deferred: its percentile thresholds use control-ref
    # target_pct, which needs control-default resolution in the emitter
    # (a known Phase-B item). micro_03/04/05 use literal args and cover
    # percentile, threshold, dwell, above/below/all_of, sigmoid, and binop.
    for stem in (
        "micro_01_passthrough",
        "micro_02_bandpass",
        "micro_03_envelope",
        "micro_04_threshold",
        "micro_05_reward",
    ):
        generate(stem)
