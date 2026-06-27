"""Back-compat guard: the staged-protocol machinery (phase cursor, masking,
reward selection, percentile freeze) must be transparent to existing blockless
protocols. A protocol that declares no blocks / no reward bundles must run
through the new evaluator exactly as before — a leading warmup phase mutes,
then `run` emits, no spurious masking, finite outputs.

(Byte-level IR-JSON back-compat is covered by tests/test_ir_json_schema.py;
per-primitive numerical equivalence by the bench suite; and every pre-existing
tests/test_eval_*.py suite runs blockless protocols through this same path.
This guard adds an explicit lifecycle check.)
"""

import math

import numpy as np

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve
from tests.conftest_staged import BASE

SR = 256

# A blockless protocol with a short leading warmup, a run phase, and a cooldown
# — the classic pre-staging shape. No `block`/`reward "name"` declarations.
BLOCKLESS = BASE % '''session { phases = [
  phase { name = "warmup";   duration = 1 s; output_muted = true },
  phase { name = "training"; duration = 2 s },
  phase { name = "cooldown"; duration = 1 s; output_muted = true },
] }'''
WARMUP_CHUNKS = SR // 64   # 256 / 64 = 4 chunks span the 1 s warmup


def test_smr_cz_blockless_has_no_blocks_or_bundles():
    # A real published example resolves with empty staging maps (structural
    # back-compat: existing protocols gain no blocks/bundles).
    ir = resolve(parse(open("examples/smr_cz.refrain").read()))
    assert ir.blocks == {}
    assert ir.reward_bundles == {}


def test_blockless_warmup_then_run_emits_finite():
    ir = resolve(parse(BLOCKLESS))
    assert ir.blocks == {}
    ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("Cz",), backend="python")
    ev.start()
    assert ev.state == "warmup"

    # Warmup phase: output suppressed (no events), exactly as pre-staging.
    assert ev.step_chunk(np.full((64, 1), 1.0)) == []
    assert ev.current_phase()["output_muted"] is True

    # Feed the rest of the 1 s warmup; the boundary chunk flips warmup -> run.
    for _ in range(WARMUP_CHUNKS - 1):
        ev.step_chunk(np.full((64, 1), 1.0))
    assert ev.state == "run"          # warmup -> run, exactly once

    # Process training chunks (well inside the 2 s training phase). current_phase
    # reflects the chunk just run (aligned with last_taps), and the value
    # channel emits finite output — the blockless run path is unchanged.
    events = []
    for _ in range(WARMUP_CHUNKS):
        events.extend(ev.step_chunk(np.full((64, 1), 1.0)))
    assert ev.current_phase()["name"] == "training"
    assert events, "blockless training phase should emit output"
    for e in events:
        assert e.value is None or math.isfinite(e.value)
