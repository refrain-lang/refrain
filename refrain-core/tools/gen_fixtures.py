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


def _reference(
    ir, signal: np.ndarray
) -> tuple[dict[str, np.ndarray], list[dict], list[dict]]:
    """Drive the canonical Python evaluator once over `signal`, capturing the
    reference output *streams* (ground truth for `equivalence.rs`), the
    `list[Event]` returned per chunk (ground truth for `events.rs`), and the
    per-chunk `last_taps()` snapshot (ground truth for `taps.rs`). One run,
    same seeded signal/evaluator — no duplicated signal generation or setup."""
    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS, record_streams=True
    )
    ev.start(skip_warmup=True)

    events: list[dict] = []
    taps: list[dict] = []

    class _Adapter:
        def step(self, raw_chunk):
            for e in ev.step_chunk(raw_chunk):
                events.append(
                    {
                        "timestamp_s": e.timestamp_s,
                        "channel": e.channel,
                        "kind": e.kind,
                        "value": e.value,
                    }
                )
            # Tap snapshot for this chunk, cast to float so bool → 0.0/1.0 —
            # the single-map form the Rust `last_taps()` golden-vector uses.
            taps.append({k: float(v) for k, v in ev.last_taps().items()})
            return {k: np.asarray(v).copy() for k, v in ev.last_streams().items()}

    streams = ChunkedRunner(chunk_size=CHUNK_SIZE).run(_Adapter(), signal).streams
    return streams, events, taps


# --- set_control (live retuning) scenario ----------------------------------
#
# `realistic_smr`'s SMR/theta thresholds are `percentile(target_pct: <control>)`,
# the canonical live-tunable clinician knob. The scenario runs the same seeded
# signal through the Python evaluator but calls `set_control` partway through,
# proving (a) the retune matches Python and (b) the percentile buffer is NOT
# reset — it keeps filling across the change. The Rust `set_control.rs` test
# replays the SAME schedule at the same chunk and asserts the event/tap streams
# match.
SETCONTROL_STEM = "realistic_smr"
# Apply the retune at this chunk index (0-based): halfway, well after warm-up,
# so the percentile buffer is partially filled when the change lands.
SETCONTROL_AT_CHUNK = 64
# Knobs to retune and their new values. Moving SMR's target percentile up and
# theta's down shifts both adaptive thresholds, so events visibly diverge from
# the static run — making the parity test meaningful.
SETCONTROL_CHANGES = (
    ("smr_target_pct", 55.0),
    ("theta_target_pct", 45.0),
)


def _setcontrol_reference(
    ir, signal: np.ndarray
) -> tuple[list[dict], list[dict]]:
    """Drive the Python evaluator over `signal`, calling `set_control` at
    `SETCONTROL_AT_CHUNK`, and capture per-chunk events + taps across the whole
    run. REUSES the same `Evaluator.live` / `step_chunk` / `last_taps` path as
    `_reference`; the only difference is the mid-run control change."""
    ev = Evaluator.live(
        ir, sample_rate_hz=SAMPLE_RATE_HZ, channel_names=CHANNELS, record_streams=True
    )
    ev.start(skip_warmup=True)

    events: list[dict] = []
    taps: list[dict] = []
    n_chunks = signal.shape[0] // CHUNK_SIZE
    for i in range(n_chunks):
        if i == SETCONTROL_AT_CHUNK:
            for name, value in SETCONTROL_CHANGES:
                ev.set_control(name, value)
        chunk = signal[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
        for e in ev.step_chunk(chunk):
            events.append(
                {
                    "timestamp_s": e.timestamp_s,
                    "channel": e.channel,
                    "kind": e.kind,
                    "value": e.value,
                }
            )
        taps.append({k: float(v) for k, v in ev.last_taps().items()})
    return events, taps


def _generate_setcontrol(ir) -> None:
    """Emit `<stem>_setcontrol.events.json` / `.taps.json` plus the schedule
    metadata the Rust test replays."""
    rng = np.random.default_rng(SEED)
    signal = rng.standard_normal((N_SAMPLES, len(CHANNELS))) * 10.0
    events, taps = _setcontrol_reference(ir, signal)

    schedule = {
        "at_chunk": SETCONTROL_AT_CHUNK,
        "changes": [{"name": n, "value": v} for n, v in SETCONTROL_CHANGES],
    }
    (FIX / f"{SETCONTROL_STEM}_setcontrol.events.json").write_text(json.dumps(events))
    (FIX / f"{SETCONTROL_STEM}_setcontrol.taps.json").write_text(json.dumps(taps))
    (FIX / f"{SETCONTROL_STEM}_setcontrol.schedule.json").write_text(json.dumps(schedule))
    print(
        f"{SETCONTROL_STEM}_setcontrol: events+taps+schedule written; "
        f"events = {len(events)}; set_control @ chunk {SETCONTROL_AT_CHUNK} "
        f"-> {list(SETCONTROL_CHANGES)}"
    )


# Protocols whose output channels actually emit events (the others are
# pure-DSP micro corpora with no `output` bindings to compare).
EVENT_BEARING = frozenset({"micro_05_reward", "realistic_smr", "micro_09_inhibit"})

# Tap-rich protocols for the `taps.rs` golden-vector compare. `realistic_smr`
# exercises input/derive/threshold/reward/condition/output taps;
# `micro_09_inhibit` adds inhibit/<name> and the combined `muted` gate.
TAP_BEARING = frozenset({"realistic_smr", "micro_09_inhibit"})


def generate(stem: str) -> None:
    ir = resolve(parse_file(REPO / "bench" / "protocols" / f"{stem}.refrain"), AMP)
    # Bake at the rate the runtime actually uses (a host choice >= the
    # protocol minimum), which can differ from the resolver's default.
    # Match the production wire format emitted by `ir_to_json` (sort_keys is
    # intentionally False there so the `output` section keeps declaration
    # order — see ir_json.py). Generating golden vectors with a *different*
    # serialization would let the Rust golden suite drift from the canonical
    # transformation it is meant to pin. `indent` only affects readability.
    (FIX / f"{stem}.ir.json").write_text(
        json.dumps(ir_to_json_obj(ir, sample_rate_hz=SAMPLE_RATE_HZ), indent=2)
    )

    rng = np.random.default_rng(SEED)
    signal = rng.standard_normal((N_SAMPLES, len(CHANNELS))) * 10.0
    streams, events, taps = _reference(ir, signal)

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

    # Event-bearing protocols also capture the feedback Event list the Rust
    # core must reproduce (events.rs).
    if stem in EVENT_BEARING:
        (FIX / f"{stem}.events.json").write_text(json.dumps(events))
        print(
            f"{stem}: ir+io+events written; "
            f"streams = {sorted(streams)}; events = {len(events)}"
        )
    else:
        print(f"{stem}: ir+io written; reference streams = {sorted(streams)}")

    # Tap-rich protocols also capture the per-chunk `last_taps()` snapshots the
    # Rust core must reproduce (taps.rs): a list of {key: float} per chunk.
    if stem in TAP_BEARING:
        (FIX / f"{stem}.taps.json").write_text(json.dumps(taps))
        keyset = sorted({k for chunk in taps for k in chunk})
        print(f"{stem}: taps written; {len(taps)} chunks; tap keys = {keyset}")

    # The live-retuning parity scenario (set_control.rs): same IR / seeded
    # signal, but a mid-run `set_control` on this protocol's percentile knobs.
    # REUSES the just-resolved `ir` and the same chunk size / seed.
    if stem == SETCONTROL_STEM:
        _generate_setcontrol(ir)


if __name__ == "__main__":
    FIX.mkdir(parents=True, exist_ok=True)
    # realistic_smr now covered: its percentile thresholds use control-ref
    # target_pct, which the emitter resolves to the control default (and
    # bakes the percentile window). micro_03/04/05 use literal args and cover
    # percentile, threshold, dwell, above/below/all_of, sigmoid, and binop.
    for stem in (
        "micro_01_passthrough",
        "micro_02_bandpass",
        "micro_03_envelope",
        "micro_04_threshold",
        "micro_05_reward",
        "micro_06_coherence",
        "micro_07_ilf",
        "micro_08_bandpower",
        "micro_09_inhibit",
        "realistic_smr",
    ):
        generate(stem)
