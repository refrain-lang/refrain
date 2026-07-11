# Fuzzer Metamorphic Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the noise-dominated majority of the protocol corpus on direction-aware, same-noise-realization sweeps of **time-in-reward**, replacing the direction-blind event-count monotonicity check.

**Architecture:** The fuzzer fixes the noise seed and varies only tone amplitude, so noise is bit-identical across a sweep — a controlled A/B on one noise realization. Each reward-feeding derive gets a *sweep group*: a baseline (no drive) plus a 4-rung amplitude ladder anchored at that leaf's **decision level**, with the other leaves held favourable (kind-aware). The metric is the fraction of the spike window the engine's `reward.event.holds` stream is true. A group asserts non-decreasing (`above` leaves) or non-increasing (`below` leaves) metric, plus a **contrast** requirement that fails loud on a flat sweep. Protocols with any percentile leaf route to this tier and have their sample-exact scenarios suppressed.

**Tech Stack:** Python 3.12, numpy, pytest, ruff. Engine access via `Evaluator(record_streams=True)`.

## Global Constraints

- Working dir: `/Users/jcroall/git/refrain/refrain`. Branch off latest `origin/main` (PR #60 is merged at `534e121`).
- Python: `.venv/bin/python`. Tests: `.venv/bin/python -m pytest tests/fuzz/ -q`.
- Lint: `.venv/bin/ruff check src/refrain/fuzz/` must be clean; CI gate `.venv/bin/ruff check src/refrain --select F,E9` must be clean.
- **No tolerance-fudge knob.** Robustness comes from the metric, the direction-awareness, and the anchor. Never add a slack term (`n[i] >= n[i-1] - k`) to the comparison. Tuning the *ladder* (a probe) is legitimate; adding slack to the *assertion* is not.
- **Never a silent pass.** A sweep that cannot assert is *reported* as unassertable. A protocol with zero crisp assertions anywhere raises `VacuityError`.
- Reuse, do not reinvent: `oracle.bandpass_gain_at`, `oracle.settle_time_s`, `synthetic.render_scenario`, `synthetic.channels_for_synthetic`, `sources.SyntheticSource`, `check.VacuityError`, `check.ActualEvent`.
- Design constants (validated in the spec addendum, do not change without re-running Task 8):
  - `_LADDER = (0.5, 1.0, 2.0, 4.0)` — rungs in anchor units (envelope µV).
  - `_HI = 4.0` — favourable-background drive, in anchor units.
  - `_CONTRAST_FRACTION = 0.5` — top rung must close half the gap baseline→saturation.
  - `_SPIKE_FRACTION_SAFETY = 2.5` — spike ≤ headroom/2.5 of the percentile buffer.
  - `_HOLD_FRACTIONS = (0.5, 0.9, 1.5, 2.5, 5.0)` — hold sweep, in dwell units.

**Reference:** `docs/superpowers/specs/2026-07-08-fuzzer-metamorphic-tier-design.md` (read the Addendum — R1–R5 are load-bearing).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/refrain/fuzz/engine.py` | **New.** Run a Scenario against the real evaluator; return events + concatenated per-sample streams. Measure the per-derive noise floor. Compute time-in-reward. *Instrumentation, never an oracle.* |
| `src/refrain/fuzz/sweep.py` | **New.** Pure sweep planning: direction classification, anchors, fill/spike geometry, kind-aware segment construction, `plan_sweeps`. No engine, no DSP. |
| `src/refrain/fuzz/metamorphic.py` | **New.** Pure checking: monotonicity + contrast over measured metrics. |
| `src/refrain/fuzz/surface.py` | Add `tier`, `reward_leaves()`, `derive_for()`, `threshold_for()`. Stop skipping percentile single-leaf. |
| `src/refrain/fuzz/generate.py` | Delete `generate_rank_sweep` / `generate_hold_duration_sweep` (moved to `sweep.py`). Reuse `surface.reward_leaves`. |
| `src/refrain/fuzz/check.py` | Delete `check_metamorphic_monotonic`, `MetamorphicViolation`, `_series_sort_key`. Oracle checking only. |
| `src/refrain/fuzz/runner.py` | Tier routing, seed threading, sweep execution, vacuity gate. `_apply_phase_override` moves to `engine.py`. |
| `src/refrain/fuzz/report.py` | Render the metamorphic section (per group: direction, baseline, series, verdict). |
| `src/refrain/cli.py` | `--seed` flag. |
| `tools/fuzz_corpus_gate.py` | **New.** The Task-0 gate harness: corpus × N seeds → violations, generator-bugs, wall-clock. |

`sweep.py` and `metamorphic.py` are pure functions of their inputs, so every assertion in this design is unit-testable without running the engine. `engine.py` is the only new module that touches the evaluator.

---

## Task 1: Gate harness + RED baseline

Record today's failure before changing behaviour. This is the evidence that the bug is real and the number Task 8 must drive to zero.

**Files:**
- Create: `tools/fuzz_corpus_gate.py`
- Create: `docs/superpowers/ci/metamorphic-tier-gate-baseline.md`

**Interfaces:**
- Consumes: `refrain.fuzz.runner.run_batch`, `batch_exit_code`, `FUZZED`, `ERRORED`, `SKIPPED`.
- Produces: `tools/fuzz_corpus_gate.py` CLI: `--corpus DIR --seeds 41,42,43,44,45`, exit 1 on any violation or generator-bug.

- [ ] **Step 1: Write the gate harness**

`tools/fuzz_corpus_gate.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Task-0 gate: run the whole protocol corpus under the metamorphic tier across
several fixed seeds on a known-good engine, and require ZERO metamorphic
violations and ZERO hollow passes.

A hollow pass is a protocol that reports FUZZED while asserting nothing; the
runner raises VacuityError for that, which `run_batch` classifies as an ERRORED
outcome whose reason starts with "generator-bug:". This harness counts those
separately from parse/resolve/eval errors, which are pre-existing corpus gaps
(coherence, bandpower, montage) and are NOT gate failures.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from refrain.fuzz.runner import (  # noqa: E402
    ERRORED,
    FUZZED,
    SKIPPED,
    run_batch,
)
from refrain.parser import ParseError, parse_file  # noqa: E402
from refrain.resolver import ResolveError, resolve  # noqa: E402
from refrain.compose import default_library_dirs, filesystem_loader  # noqa: E402


def _resolver(library_dirs):
    loader = filesystem_loader(library_dirs) if library_dirs else None

    def resolve_fn(path):
        try:
            return resolve(parse_file(Path(path)), None, parent_loader=loader)
        except (ParseError, ResolveError) as exc:
            return (str(exc).splitlines() or ["error"])[0][:80]

    return resolve_fn


def main() -> int:
    ap = argparse.ArgumentParser(description="Metamorphic-tier corpus gate")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--seeds", default="41,42,43,44,45")
    ap.add_argument("--chunk-size", type=int, default=64)
    ap.add_argument("--library", action="append", default=[])
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",")]
    library_dirs = [Path(d) for d in args.library] + default_library_dirs()
    resolve_fn = _resolver(library_dirs)

    total_violations = 0
    total_generator_bugs = 0
    for seed in seeds:
        t0 = time.perf_counter()
        outcomes = run_batch(
            [args.corpus], max_scenarios=0, chunk_size=args.chunk_size,
            resolve_fn=resolve_fn, seed=seed,
        )
        dt = time.perf_counter() - t0
        violations = [o for o in outcomes if o.status == FUZZED and o.passed is False]
        gen_bugs = [o for o in outcomes if o.status == ERRORED
                    and (o.reason or "").startswith("generator-bug:")]
        other_err = [o for o in outcomes if o.status == ERRORED and o not in gen_bugs]
        fuzzed = [o for o in outcomes if o.status == FUZZED]
        skipped = [o for o in outcomes if o.status == SKIPPED]
        total_violations += len(violations)
        total_generator_bugs += len(gen_bugs)

        print(f"\n=== seed {seed}  ({dt:.1f}s) ===")
        print(f"  fuzzed {len(fuzzed)} / skipped {len(skipped)} / errored {len(outcomes) - len(fuzzed) - len(skipped)}")
        print(f"  VIOLATIONS:     {len(violations)}")
        print(f"  generator-bugs: {len(gen_bugs)}   (hollow passes — must be 0)")
        print(f"  other errors:   {len(other_err)}  (pre-existing corpus gaps, not a gate failure)")
        for o in violations:
            print(f"    [VIOLATION] {o.path}")
        for o in gen_bugs:
            print(f"    [HOLLOW]    {o.path}: {o.reason}")

    print(f"\n=== GATE ===\n  violations across {len(seeds)} seeds: {total_violations}"
          f"\n  hollow passes: {total_generator_bugs}")
    ok = total_violations == 0 and total_generator_bugs == 0
    print("  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Check the imports resolve against the real modules**

The import paths above are asserted, not verified. Confirm each exists and fix the harness if not:

Run: `.venv/bin/python -c "from refrain.compose import default_library_dirs, filesystem_loader; print('ok')"`

Verified: both live in `refrain.compose` (not `refrain.resolver_loader`). `load_amp_profile` is unused — do not import it.

- [ ] **Step 3: Run the RED baseline (current code)**

The harness is written against the *target* API (`run_batch(..., seed=)`), which does not exist yet — running it now raises `TypeError`. That is expected; it starts working at Task 6. To capture today's red, drive the existing CLI:

Run: `.venv/bin/python -m refrain.cli fuzz /Users/jcroall/git/refrain-protocols/protocols --library /Users/jcroall/git/refrain-protocols/lib 2>&1 | tail -25`

Expected: a nonzero `fail` count, driven by metamorphic monotonicity violations on the `below`/percentile protocols (e.g. `smr_classic_cz_brainbit`).

- [ ] **Step 4: Record the baseline**

Write `docs/superpowers/ci/metamorphic-tier-gate-baseline.md` containing the verbatim tail of the Step-3 output, plus this framing:

```markdown
# Metamorphic-tier gate — RED baseline (2026-07-09)

Captured on the merged (pre-metamorphic-tier) fuzzer, before any behaviour change.
This is the red the new tier must eliminate. Two independent causes:

- **FM1 (deterministic):** `check_metamorphic_monotonic` asserts non-DECREASING
  firing for every swept threshold, which is sign-wrong for `below`/inhibit
  leaves. Reproduced: `smr_classic_cz_brainbit`'s `theta_t` sweep = [57, 56, 57, 56].
- **FM2 (systematic):** the metric is event count, which counts dwell re-triggers.
  Every noise dip that recovers adds an event, so event count runs BACKWARDS in
  drive. Measured on `micro_single_pct`: [12, 16, 9, 9]. Non-monotone on 5/5 seeds.

<paste the batch output here>
```

- [ ] **Step 5: Commit**

```bash
git add tools/fuzz_corpus_gate.py docs/superpowers/ci/metamorphic-tier-gate-baseline.md docs/superpowers/specs/2026-07-08-fuzzer-metamorphic-tier-design.md
git commit -m "fuzz: add metamorphic-tier gate harness + record RED baseline"
```

---

## Task 2: `engine.py` — run a scenario, get per-sample streams

**Files:**
- Create: `src/refrain/fuzz/engine.py`
- Create: `tests/fuzz/test_engine.py`
- Modify: `src/refrain/fuzz/runner.py` (remove `_apply_phase_override`, import from `engine`)

**Interfaces:**
- Consumes: `refrain.eval_.Evaluator`, `refrain.sources.SyntheticSource`, `refrain.synthetic.render_scenario`, `refrain.fuzz.check.ActualEvent`.
- Produces:
  - `REWARD_HOLDS: str = "reward.event.holds"`
  - `RunResult(events: tuple[ActualEvent, ...], streams: dict[str, np.ndarray])`
  - `apply_phase_override(ir, phase_override) -> ir`
  - `run_scenario(scenario, *, ir, channels, chunk_size) -> RunResult`
  - `time_in_reward(streams, *, window_s: tuple[float, float], fs: int) -> float`
  - `measure_noise_floor(*, ir, surface, channels, chunk_size, fill_s: float, seed: int) -> dict[str, float]`

- [ ] **Step 1: Write the failing tests**

`tests/fuzz/test_engine.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""engine.py — scenario execution, per-sample streams, noise-floor probe.

The load-bearing test here is `test_noise_is_bit_identical_across_amplitudes`:
the entire metamorphic tier rests on the sweep being a controlled A/B on ONE
noise realization. If that ever breaks, every sweep assertion silently becomes
a comparison across independent noisy runs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.fuzz.engine import (
    REWARD_HOLDS,
    measure_noise_floor,
    run_scenario,
    time_in_reward,
)
from refrain.fuzz.scenario import BandSegment, PhaseOverride, Scenario, Tone
from refrain.fuzz.surface import build_surface
from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.synthetic import channels_for_synthetic, render_scenario
from refrain.sources import SyntheticSource

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ir(rel: str):
    return resolve(parse_file(REPO_ROOT / rel), None)


def _scenario(amp: float, *, total_s: float = 10.0) -> Scenario:
    segs = (
        (BandSegment(band=(12.0, 15.0), channel="Cz", start_s=2.0, end_s=8.0,
                     content=Tone(amplitude_uv=amp)),)
        if amp > 0 else ()
    )
    return Scenario(
        label=f"amp_{amp:g}", duration_s=total_s, sample_rate_hz=256,
        segments=segs, controls={}, coverage_tags=frozenset(),
        phase_override=PhaseOverride(1.0, total_s - 1.5, 0.5), seed=42,
    )


def _render(scenario, channels) -> np.ndarray:
    src = SyntheticSource(render_scenario(scenario, channels=channels),
                          duration_s=scenario.duration_s)
    return np.concatenate([c.copy() for c in src.iter_chunks(64)], axis=0)


def test_noise_is_bit_identical_across_amplitudes():
    """Same seed + same segments-except-tone-amplitude => the noise realization
    is byte-identical, and the difference is exactly the injected tone."""
    ir = _ir("bench/protocols/micro_single_pct.refrain")
    channels = channels_for_synthetic(ir)
    quiet = _render(_scenario(0.0), channels)
    driven = _render(_scenario(20.0), channels)

    # Outside the tone segment [2 s, 8 s): bit-identical, not merely close.
    assert np.array_equal(quiet[: 2 * 256], driven[: 2 * 256])
    assert np.array_equal(quiet[8 * 256 :], driven[8 * 256 :])

    # Inside: a pure 20 uV sinusoid on Cz only (channel 0), zero elsewhere.
    diff = driven[2 * 256 : 8 * 256] - quiet[2 * 256 : 8 * 256]
    assert np.abs(diff[:, 0]).max() == pytest.approx(20.0, rel=0.02)
    assert np.abs(diff[:, 1:]).max() == 0.0


def test_run_scenario_returns_per_sample_reward_holds():
    ir = _ir("bench/protocols/micro_single_pct.refrain")
    channels = channels_for_synthetic(ir)
    res = run_scenario(_scenario(20.0), ir=ir, channels=channels, chunk_size=64)

    holds = res.streams[REWARD_HOLDS]
    assert holds.shape == (10 * 256,)
    assert holds.dtype == np.bool_
    # The derive's envelope stream is exposed under its bare name.
    assert res.streams["up_env"].shape == (10 * 256,)
    assert holds.any(), "a 20 uV tone must drive the reward at some point"


def test_time_in_reward_is_the_fraction_of_the_window_holding():
    streams = {REWARD_HOLDS: np.array([0, 0, 1, 1, 1, 1, 0, 0], dtype=bool)}
    # window [2/8 s, 6/8 s) at fs=8 -> samples 2..6 -> all four are True.
    assert time_in_reward(streams, window_s=(0.25, 0.75), fs=8) == 1.0
    assert time_in_reward(streams, window_s=(0.0, 1.0), fs=8) == 0.5


def test_time_in_reward_rejects_an_empty_window():
    streams = {REWARD_HOLDS: np.zeros(8, dtype=bool)}
    with pytest.raises(ValueError, match="empty"):
        time_in_reward(streams, window_s=(0.5, 0.5), fs=8)


def test_measure_noise_floor_is_positive_and_seed_stable():
    ir = _ir("bench/protocols/realistic_smr.refrain")
    surface = build_surface(ir)
    channels = channels_for_synthetic(ir)
    a = measure_noise_floor(ir=ir, surface=surface, channels=channels,
                            chunk_size=64, fill_s=20.0, seed=42)
    b = measure_noise_floor(ir=ir, surface=surface, channels=channels,
                            chunk_size=64, fill_s=20.0, seed=43)
    assert set(a) == {d.name for d in surface.derives}
    for name, floor in a.items():
        assert floor > 0.0
        # A median over ~16 s of quiet noise is stable across realizations.
        assert abs(floor - b[name]) / floor < 0.25, (name, floor, b[name])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'refrain.fuzz.engine'`

- [ ] **Step 3: Implement `engine.py`**

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Run a fuzz Scenario against the real evaluator and expose its per-sample streams.

The metamorphic tier measures TIME-IN-REWARD: the fraction of a window during
which the engine's reward output is actively holding. That primitive lives in
`Evaluator.last_streams()["reward.event.holds"]` — validated bit-exact by the
calibrated-oracle gate finding. `last_streams()` is a PER-CHUNK snapshot, so we
concatenate it across the run.

This module is INSTRUMENTATION, not an oracle. Its streams place the amplitude
ladder and measure a metric; they never predict what the engine ought to do.
Metamorphic assertions are engine-vs-property, not engine-vs-oracle, so reading
the engine's own envelope here does not weaken them. (DSP correctness is covered
by the Rust<->Python golden vectors and the band-characterization probe — see
docs/superpowers/specs/2026-07-07-fuzzer-target-tiered-gate-design.md.)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from ..eval_ import Evaluator
from ..ir import IRPhase
from ..sources import SyntheticSource
from ..synthetic import render_scenario
from .check import ActualEvent
from .scenario import PhaseOverride, Scenario

REWARD_HOLDS = "reward.event.holds"

# Seconds of a quiet probe discarded before taking the noise median, so the
# bandpass/smooth start-up transient cannot bias it.
_SETTLE_SKIP_S = 4.0


@dataclass(frozen=True, slots=True)
class RunResult:
    events: tuple[ActualEvent, ...]
    streams: dict[str, np.ndarray]


def apply_phase_override(ir, phase_override: PhaseOverride | None):
    """Rebuild `ir.session.phases` from a fuzz `PhaseOverride` so the evaluator's
    warmup window matches what the scenario assumed.

    The override carries durations in seconds; `IRPhase.duration_ms` is in
    milliseconds. Returns `ir` unchanged when there is no override. Zero-length
    phases are dropped; the evaluator tolerates an empty phases tuple."""
    if phase_override is None:
        return ir
    po = phase_override
    spec = [
        ("warmup", po.warmup_s, True),
        ("training", po.training_s, False),
        ("cooldown", po.cooldown_s, True),
    ]
    phases = tuple(
        IRPhase(name=name, duration_ms=dur_s * 1000.0, output_muted=muted)
        for name, dur_s, muted in spec
        if dur_s > 0
    )
    return dataclasses.replace(ir, session=dataclasses.replace(ir.session, phases=phases))


def run_scenario(scenario: Scenario, *, ir, channels, chunk_size: int) -> RunResult:
    """Render + run one scenario, returning its events and full per-sample streams."""
    scenario_ir = apply_phase_override(ir, scenario.phase_override)
    source = SyntheticSource(render_scenario(scenario, channels=channels),
                             duration_s=scenario.duration_s)
    ev = Evaluator(scenario_ir, source, record_streams=True)
    fs = scenario.sample_rate_hz
    events: list[ActualEvent] = []
    parts: dict[str, list[np.ndarray]] = {}
    for chunk in source.iter_chunks(chunk_size):
        for e in ev.step_chunk(chunk):
            if e.kind == "event":
                events.append(ActualEvent(sample=int(round(e.timestamp_s * fs)),
                                          kind=e.kind, channel=e.channel))
        for key, arr in ev.last_streams().items():
            parts.setdefault(key, []).append(np.asarray(arr).copy())
    streams = {k: np.concatenate(v) for k, v in parts.items()}
    return RunResult(events=tuple(events), streams=streams)


def time_in_reward(streams: dict[str, np.ndarray], *,
                   window_s: tuple[float, float], fs: int) -> float:
    """Fraction of `window_s` during which the reward is actively holding.

    This is the metamorphic metric. It is NOT event count: an event is a dwell
    RE-TRIGGER, so during a noisy spike every momentary dip that recovers emits
    another event. Event count is therefore a noise artifact and runs backwards
    in drive; time-in-reward does not count flicker."""
    holds = streams.get(REWARD_HOLDS)
    if holds is None:
        raise KeyError(
            f"{REWARD_HOLDS!r} missing from streams; the protocol has no dwell reward"
        )
    start = int(round(window_s[0] * fs))
    end = int(round(window_s[1] * fs))
    seg = np.asarray(holds)[start:end]
    if seg.size == 0:
        raise ValueError(f"time_in_reward: empty window {window_s} at fs={fs}")
    return float(seg.astype(bool).mean())


def measure_noise_floor(*, ir, surface, channels, chunk_size: int,
                        fill_s: float, seed: int) -> dict[str, float]:
    """Median in-band envelope of every derive during a quiet run.

    This is the DECISION LEVEL for percentile leaves: a percentile threshold over
    a mostly-quiet rolling window sits at the noise level, so the ladder must
    straddle the noise floor. (Absolute leaves anchor on their own threshold
    instead — see `sweep.leaf_anchor_uv`.)"""
    total_s = max(fill_s, _SETTLE_SKIP_S + 4.0) + 2.0
    probe = Scenario(
        label="noise_floor_probe", duration_s=total_s,
        sample_rate_hz=surface.sample_rate_hz, segments=(), controls={},
        coverage_tags=frozenset({"probe:noise_floor"}),
        phase_override=PhaseOverride(1.0, total_s - 1.5, 0.5), seed=seed,
    )
    res = run_scenario(probe, ir=ir, channels=channels, chunk_size=chunk_size)
    skip = int(round(_SETTLE_SKIP_S * surface.sample_rate_hz))
    floors: dict[str, float] = {}
    for d in surface.derives:
        arr = res.streams.get(d.name)
        if arr is None or arr[skip:].size == 0:
            continue
        floors[d.name] = float(np.median(arr[skip:]))
    return floors


__all__ = [
    "REWARD_HOLDS", "RunResult", "apply_phase_override", "measure_noise_floor",
    "run_scenario", "time_in_reward",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_engine.py -q`
Expected: 5 passed.

- [ ] **Step 5: Point `runner.py` at the shared helper**

In `src/refrain/fuzz/runner.py`, delete the `_apply_phase_override` definition (currently `runner.py:128-150`) and import the shared one:

```python
from .engine import apply_phase_override
```

Rewrite `_run_one_scenario` to reuse `run_scenario` rather than re-implementing render + eval + event collection:

```python
def _run_one_scenario(scenario, *, ir, surface, channels, collar_samples, chunk_size):
    """Run + oracle-predict + check a single scenario. Returns a
    PerScenarioResult; may raise VacuityError (a generator bug)."""
    fs = surface.sample_rate_hz
    res = run_scenario(scenario, ir=ir, channels=channels, chunk_size=chunk_size)
    expected = predict(scenario, surface)
    return check_scenario(
        scenario_label=scenario.label, expected=expected, actual=list(res.events), fs=fs,
        collar_samples=collar_samples, coverage_tags=scenario.coverage_tags,
        total_samples=int(round(scenario.duration_s * fs)),
    )
```

Add `run_scenario` to the `.engine` import. Remove `from ..ir import IRPhase`, `from ..eval_ import eval_protocol`, `from ..sources import SyntheticSource`, and `render_scenario` from the `..synthetic` import (keep `channels_for_synthetic`) — nothing else uses them. **Keep `import dataclasses`**: Task 6 uses it to thread the seed. Verify with `grep -n "dataclasses\|IRPhase\|eval_protocol\|SyntheticSource\|render_scenario" src/refrain/fuzz/runner.py`.

- [ ] **Step 6: Verify no regression + lint**

Run: `.venv/bin/python -m pytest tests/fuzz/ -q && .venv/bin/ruff check src/refrain/fuzz/`
Expected: all pass, no lint findings.

- [ ] **Step 7: Commit**

```bash
git add src/refrain/fuzz/engine.py tests/fuzz/test_engine.py src/refrain/fuzz/runner.py
git commit -m "fuzz: add engine.py — per-sample streams, time-in-reward, noise-floor probe"
```

---

## Task 3: `surface.py` — tier routing and leaf helpers

> **Additive only.** This task must NOT change which protocols skip. Unskipping
> percentile single-leaf happens in Task 6, together with the tier routing and
> the sweeps that gate it — so behaviour flips exactly once, when the machinery
> to handle it exists. Unskipping here would push `micro_single_pct` through the
> *old* direction-blind event-count check and red the batch for three tasks.

**Files:**
- Modify: `src/refrain/fuzz/surface.py`
- Modify: `src/refrain/fuzz/generate.py` (reuse `reward_leaves`)
- Modify: `tests/fuzz/test_surface.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `SAMPLE_EXACT = "sample_exact"`, `METAMORPHIC = "metamorphic"`
  - `LogicalSurface.tier: str` (new field, last, no default — set by `build_surface`)
  - `reward_leaves(surface_or_node) -> tuple[ConditionLeaf, ...]`
  - `derive_for(surface, name) -> DeriveSurface`
  - `threshold_for(surface, name) -> ThresholdSurface`

- [ ] **Step 1: Write the failing tests**

Append to `tests/fuzz/test_surface.py`:

```python
from refrain.fuzz.surface import (
    METAMORPHIC,
    SAMPLE_EXACT,
    derive_for,
    reward_leaves,
    threshold_for,
)


def test_absolute_only_reward_is_sample_exact_tier():
    surface = build_surface(_ir("bench/protocols/micro_single_above.refrain"))
    assert surface.tier == SAMPLE_EXACT


def test_any_percentile_leaf_routes_to_the_metamorphic_tier():
    # A percentile threshold makes the reward noise-dominated: the oracle can
    # only mark those regions DON'T-CARE, which is exactly the hollow pass.
    surface = build_surface(_ir("bench/protocols/realistic_smr.refrain"))
    assert surface.tier == METAMORPHIC


def test_reward_leaves_flattens_the_condition_tree_in_order():
    surface = build_surface(_ir("bench/protocols/realistic_smr.refrain"))
    assert [(leaf.op, leaf.signal) for leaf in reward_leaves(surface)] == [
        ("above", "smr_envelope"),
        ("below", "theta_envelope"),
        ("below", "high_beta_envelope"),
    ]


def test_derive_for_and_threshold_for_look_up_by_name():
    surface = build_surface(_ir("bench/protocols/realistic_smr.refrain"))
    assert derive_for(surface, "smr_envelope").band == (12.0, 15.0)
    assert threshold_for(surface, "hbeta_t").absolute_uv == 8.0
    with pytest.raises(KeyError):
        derive_for(surface, "nope")
```

Add `import pytest` and a `_ir` helper to that file if not present (mirror `tests/fuzz/test_runner.py:19-20`).

Do **not** touch `tests/fuzz/test_unsupported.py` or `tests/fuzz/test_batch.py` in this task — the percentile skip is still in force until Task 6.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_surface.py -q`
Expected: FAIL — `ImportError: cannot import name 'METAMORPHIC'`

- [ ] **Step 3: Implement**

In `src/refrain/fuzz/surface.py`:

(a) Add the tier constants near the top, after `_DEFAULT_HILBERT_TAPS`:

```python
# Semantic tiers (see docs/superpowers/specs/2026-07-07-fuzzer-target-tiered-gate-design.md).
# A protocol is metamorphic-tier iff any reward leaf uses a percentile threshold:
# such a threshold tracks its own signal, so firing is decided by the noise
# realization and sample-exact prediction is impossible.
SAMPLE_EXACT = "sample_exact"
METAMORPHIC = "metamorphic"
```

(b) Add `tier: str` as the last field of `LogicalSurface`.

(c) Add the lookup + flatten helpers after the `LogicalSurface` definition:

```python
def reward_leaves(surface_or_node) -> tuple[ConditionLeaf, ...]:
    """Flatten a reward condition tree to its leaves, left to right."""
    node = getattr(surface_or_node, "reward_condition", surface_or_node)

    def walk(n):
        if isinstance(n, ConditionLeaf):
            yield n
            return
        for child in n.children:
            yield from walk(child)

    return tuple(walk(node))


def derive_for(surface: LogicalSurface, name: str) -> DeriveSurface:
    for d in surface.derives:
        if d.name == name:
            return d
    raise KeyError(f"surface: no derive named {name!r}")


def threshold_for(surface: LogicalSurface, name: str) -> ThresholdSurface:
    for t in surface.thresholds:
        if t.name == name:
            return t
    raise KeyError(f"surface: no threshold named {name!r}")
```

(d) Leave `_classify_single_leaf` **unchanged**. The percentile rejection stays until Task 6.

(e) In `build_surface`, compute the tier after `reward_condition` and pass it:

```python
    leaves = reward_leaves(reward_condition)
    by_name = {t.name: t for t in thresholds}
    tier = (
        METAMORPHIC
        if any(by_name[leaf.threshold].kind == "percentile"
               for leaf in leaves if leaf.threshold in by_name)
        else SAMPLE_EXACT
    )
```

and add `tier=tier,` to the `LogicalSurface(...)` construction.

(f) Add `"METAMORPHIC"`, `"SAMPLE_EXACT"`, `"derive_for"`, `"reward_leaves"`, `"threshold_for"` to `__all__`.

(g) In `src/refrain/fuzz/generate.py`, delete the private `_all_leaves` generator and import the shared one:

```python
from .surface import ConditionLeaf, DeriveSurface, LogicalSurface, ThresholdSurface, reward_leaves
```

Replace every `_all_leaves(surface.reward_condition)` call with `reward_leaves(surface)` and `list(_all_leaves(...))` with `list(reward_leaves(surface))`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/ -q`
Expected: all pass. The whole fuzz suite must stay green — this task is additive, so `test_unsupported.py` and `test_batch.py` must be untouched **and still passing**. If either fails, you changed skip behaviour; revert that part.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/surface.py src/refrain/fuzz/generate.py tests/fuzz/test_surface.py
git commit -m "fuzz(surface): add tier classification + leaf helpers (additive)"
```

---

## Task 4: `sweep.py` — direction, anchors, geometry, segments

This is the heart of the design. Everything here is a pure function of the surface + noise floor, so it is fully unit-testable without running the engine.

**Files:**
- Create: `src/refrain/fuzz/sweep.py`
- Create: `tests/fuzz/test_sweep.py`

**Interfaces:**
- Consumes: `surface.reward_leaves/derive_for/threshold_for`, `oracle.bandpass_gain_at`, `scenario.BandSegment/Scenario/Tone/PhaseOverride`.
- Produces:
  - `UP = "up"`, `DOWN = "down"`, `NONE = "none"`
  - `SweepMember(scenario: Scenario, index: int)` — `index == -1` is the baseline; `0..n-1` are rungs in ascending-drive order.
  - `SweepGroup(tag: str, direction: str, reason: str | None, members: tuple[SweepMember, ...], metric_window_s: tuple[float, float])`
  - `sweep_direction(surface, derive_name) -> tuple[str, str | None]`
  - `leaf_anchor_uv(leaf, surface, noise_floor) -> float | None`
  - `sweep_geometry(surface, *, collar_s) -> SweepGeometry(fill_s, spike_s, total_s, metric_window_s)`
  - `plan_sweeps(surface, *, noise_floor, collar_s, seed) -> tuple[SweepGroup, ...]`

- [ ] **Step 1: Write the failing tests**

`tests/fuzz/test_sweep.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""sweep.py — direction classification, anchors, geometry, segment construction.

All pure: no engine, no DSP beyond the baked bandpass gain.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from refrain.fuzz.scenario import Tone
from refrain.fuzz.surface import build_surface, reward_leaves, threshold_for
from refrain.fuzz.sweep import (
    DOWN,
    NONE,
    UP,
    leaf_anchor_uv,
    plan_sweeps,
    sweep_direction,
    sweep_geometry,
)
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]

# Measured quiet-run medians (tests/fuzz/test_engine.py asserts these are stable).
SMR_FLOORS = {"smr_envelope": 1.1, "theta_envelope": 2.8, "high_beta_envelope": 1.0}


def _surface(rel: str):
    return build_surface(resolve(parse_file(REPO_ROOT / rel), None))


def test_above_leaf_sweeps_non_decreasing():
    s = _surface("bench/protocols/realistic_smr.refrain")
    assert sweep_direction(s, "smr_envelope") == (UP, None)


def test_below_leaf_sweeps_non_increasing():
    """The pre-existing bug: check_metamorphic_monotonic asserted non-DECREASING
    for every swept threshold, false-failing every inhibit/below leaf."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    assert sweep_direction(s, "theta_envelope") == (DOWN, None)
    assert sweep_direction(s, "high_beta_envelope") == (DOWN, None)


def test_derive_not_feeding_the_reward_asserts_nothing():
    s = _surface("bench/protocols/realistic_smr.refrain")
    direction, reason = sweep_direction(s, "nonexistent_envelope")
    assert direction == NONE
    assert "does not feed" in reason


def test_anchor_is_the_absolute_threshold_for_an_absolute_leaf():
    s = _surface("bench/protocols/realistic_smr.refrain")
    leaf = next(x for x in reward_leaves(s) if x.signal == "high_beta_envelope")
    assert threshold_for(s, leaf.threshold).kind == "absolute"
    assert leaf_anchor_uv(leaf, s, SMR_FLOORS) == 8.0


def test_anchor_is_the_measured_noise_floor_for_a_percentile_leaf():
    s = _surface("bench/protocols/realistic_smr.refrain")
    leaf = next(x for x in reward_leaves(s) if x.signal == "smr_envelope")
    assert leaf_anchor_uv(leaf, s, SMR_FLOORS) == pytest.approx(1.1)


def test_geometry_keeps_the_spike_a_small_fraction_of_the_percentile_buffer():
    """A spike that fills too much of the rolling window shifts the percentile
    onto itself and the sweep flattens. Constraint: spike/(fill+spike) <= p/2.5
    where p = min(target_pct, 100-target_pct)/100 (= 0.30 for p70/p30)."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    geom = sweep_geometry(s, collar_s=1.0)
    assert geom.spike_s / (geom.fill_s + geom.spike_s) <= 0.30 / 2.5 + 1e-9
    # And the fill never waits out the declared 2-minute window.
    assert geom.fill_s < 60.0
    # A usable metric window survives the settle + dwell collar.
    lo, hi = geom.metric_window_s
    assert hi - lo >= 0.5


def test_geometry_needs_no_fill_without_percentile_thresholds():
    s = _surface("bench/protocols/micro_single_above.refrain")
    assert sweep_geometry(s, collar_s=1.0).fill_s == 0.0


def test_plan_sweeps_emits_a_baseline_plus_an_ascending_ladder_per_derive():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    rank = {g.tag: g for g in groups if g.tag.startswith("rank_sweep:")}
    assert set(rank) == {
        "rank_sweep:smr_envelope",
        "rank_sweep:theta_envelope",
        "rank_sweep:high_beta_envelope",
    }
    g = rank["rank_sweep:smr_envelope"]
    assert g.direction == UP
    assert [m.index for m in g.members] == [-1, 0, 1, 2, 3]
    # All members share one duration => one noise realization, byte-identical.
    assert len({m.scenario.duration_s for m in g.members}) == 1
    assert len({m.scenario.seed for m in g.members}) == 1
    # The baseline drives nothing on the swept derive.
    base = g.members[0].scenario
    assert not any(seg.band == (12.0, 15.0) and seg.start_s >= g.metric_window_s[0] - 5
                   for seg in base.segments)


def test_ladder_amplitudes_ascend_and_are_gain_compensated():
    s = _surface("bench/protocols/micro_single_above.refrain")
    floors = {d.name: 2.0 for d in s.derives}
    g = next(g for g in plan_sweeps(s, noise_floor=floors, collar_s=1.0, seed=42)
             if g.tag.startswith("rank_sweep:"))
    amps = []
    for m in g.members[1:]:
        tone = next(seg.content for seg in m.scenario.segments
                    if isinstance(seg.content, Tone))
        amps.append(tone.amplitude_uv)
    assert amps == sorted(amps)
    assert amps[-1] / amps[0] == pytest.approx(8.0, rel=1e-6)  # 4.0x / 0.5x


def test_percentile_below_leaves_are_primed_high_during_the_fill():
    """Quiet is NOT favourable for below(x, percentile(x)): the threshold tracks
    its own signal, so it holds ~p% of the time however quiet x is. Priming it
    high during the fill raises its rolling percentile so the quiet spike sits
    clearly below it."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:smr_envelope")
    geom = sweep_geometry(s, collar_s=1.0)
    theta = next(d for d in s.derives if d.name == "theta_envelope")
    for m in g.members:
        primes = [seg for seg in m.scenario.segments
                  if seg.band == theta.band and seg.start_s == 0.0]
        assert len(primes) == 1, "theta (below+percentile) must be primed in the fill"
        assert primes[0].end_s == pytest.approx(geom.fill_s)


def test_the_swept_derive_is_never_primed():
    """Priming the swept derive would raise its own percentile and flatten the sweep."""
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:theta_envelope")
    theta = next(d for d in s.derives if d.name == "theta_envelope")
    for m in g.members:
        assert not [seg for seg in m.scenario.segments
                    if seg.band == theta.band and seg.start_s == 0.0]


def test_absolute_below_leaves_are_left_quiet():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "rank_sweep:smr_envelope")
    hbeta = next(d for d in s.derives if d.name == "high_beta_envelope")
    for m in g.members:
        assert not [seg for seg in m.scenario.segments if seg.band == hbeta.band]


def test_hold_sweep_group_is_planned_with_ascending_holds():
    s = _surface("bench/protocols/realistic_smr.refrain")
    groups = plan_sweeps(s, noise_floor=SMR_FLOORS, collar_s=1.0, seed=42)
    g = next(g for g in groups if g.tag == "hold_duration_sweep")
    assert g.direction == UP
    assert [m.index for m in g.members] == [-1, 0, 1, 2, 3, 4]
    assert len({m.scenario.duration_s for m in g.members}) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_sweep.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'refrain.fuzz.sweep'`

- [ ] **Step 3: Implement `sweep.py`**

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Plan the metamorphic sweeps: direction, anchors, geometry, segments.

The fuzzer fixes the noise seed and varies only tone amplitude, so every member
of a sweep sees a BYTE-IDENTICAL noise realization (guarded by
tests/fuzz/test_engine.py::test_noise_is_bit_identical_across_amplitudes). A
sweep is therefore a controlled A/B on one realization, and "more in-band drive
pushes the leaf harder" is a real, assertable ordering.

Pure module: a function of the surface + a measured noise floor. No engine.
"""
from __future__ import annotations

from dataclasses import dataclass

from .oracle import bandpass_gain_at
from .scenario import BandSegment, PhaseOverride, Scenario, Tone
from .surface import (
    ConditionLeaf,
    DeriveSurface,
    LogicalSurface,
    derive_for,
    reward_leaves,
    threshold_for,
)

UP = "up"
DOWN = "down"
NONE = "none"

# Ladder rungs, in anchor units (the anchor is the leaf's decision level).
_LADDER = (0.5, 1.0, 2.0, 4.0)
# Drive applied to non-swept leaves to hold them favourable, in anchor units.
_HI = 4.0
# Hold-sweep rungs, in dwell units.
_HOLD_FRACTIONS = (0.5, 0.9, 1.5, 2.5, 5.0)

# The spike must stay a small fraction of the percentile's rolling buffer, or it
# shifts the percentile onto itself and the sweep flattens. `headroom` is the
# distance from the target percentile to the nearer end of the distribution.
_SPIKE_FRACTION_SAFETY = 2.5
_MIN_FILL_S = 8.0
_MIN_SPIKE_S = 4.0
_MIN_METRIC_WINDOW_S = 0.5
_TAIL_PAD_S = 2.0
_WARMUP_S = 2.0
_COOLDOWN_S = 0.5


@dataclass(frozen=True, slots=True)
class SweepGeometry:
    fill_s: float
    spike_s: float
    total_s: float
    metric_window_s: tuple[float, float]


@dataclass(frozen=True, slots=True)
class SweepMember:
    scenario: Scenario
    index: int          # -1 = baseline (no drive); 0..n-1 = rungs, ascending drive


@dataclass(frozen=True, slots=True)
class SweepGroup:
    tag: str
    direction: str                       # UP | DOWN | NONE
    reason: str | None                   # why NONE
    members: tuple[SweepMember, ...]
    metric_window_s: tuple[float, float]


def sweep_direction(surface: LogicalSurface, derive_name: str) -> tuple[str, str | None]:
    """Which way the reward metric must move as this derive's drive increases.

    `above()` and `below()` are monotone in their signal, and `all_of`/`any_of`
    are monotone in each child's truth (there is no negation), so a derive that
    feeds only `above` leaves can only push the reward up, and one that feeds
    only `below` leaves can only push it down. Anything else asserts nothing.
    """
    leaves = [x for x in reward_leaves(surface) if x.signal == derive_name]
    if not leaves:
        return NONE, "derive does not feed the reward condition"
    ops = {x.op for x in leaves}
    if len(ops) > 1:
        return NONE, "derive feeds both above() and below() leaves"
    if len({x.threshold for x in leaves}) > 1:
        return NONE, "derive feeds several leaves with different thresholds"
    return (UP if ops == {"above"} else DOWN), None


def leaf_anchor_uv(leaf: ConditionLeaf, surface: LogicalSurface,
                   noise_floor: dict[str, float]) -> float | None:
    """The leaf's decision level in envelope microvolts — where its truth flips.

    absolute: the threshold itself. percentile: the measured noise floor, because
    a percentile over a mostly-quiet rolling window sits at the noise level.
    A ladder anchored anywhere else does not straddle the boundary."""
    thr = threshold_for(surface, leaf.threshold)
    if thr.kind == "absolute":
        return thr.absolute_uv
    if thr.kind == "percentile":
        return noise_floor.get(leaf.signal)
    return None


def _percentile_headroom(surface: LogicalSurface) -> float | None:
    """min(target, 100-target)/100 over the percentile thresholds feeding the
    reward, or None when there are none."""
    names = {x.threshold for x in reward_leaves(surface)}
    pcts = [t for t in surface.thresholds
            if t.name in names and t.kind == "percentile"
            and t.percentile_target is not None]
    if not pcts:
        return None
    return max(
        min(min(t.percentile_target, 100.0 - t.percentile_target) / 100.0 for t in pcts),
        0.02,
    )


def _longest_reward_percentile_window_s(surface: LogicalSurface) -> float | None:
    """Longest declared rolling window among the reward's percentile thresholds,
    or None when none of them declares one (then there is nothing to cap against)."""
    names = {x.threshold for x in reward_leaves(surface)}
    windows = [
        (t.percentile_window_ms or 0.0) / 1000.0
        for t in surface.thresholds
        if t.name in names and t.kind == "percentile" and t.percentile_window_ms
    ]
    return max(windows) if windows else None


def sweep_geometry(surface: LogicalSurface, *, collar_s: float) -> SweepGeometry:
    """Fill / spike / metric-window layout.

    `PercentileImpl.step` computes over its CURRENT buffer ("warm-up: short
    buffer is OK") at O(buffer) per sample, so cost grows as fill^2 and filling a
    declared 2-minute window is ~12x more expensive for no assertion power. We
    fill only long enough that (a) the percentile estimate is stable and (b) the
    spike stays a small fraction of the buffer. The protocol's declared window is
    never overridden — we simply stop waiting for it to fill."""
    dwell_s = surface.dwell_ms / 1000.0
    spike_s = max(_MIN_SPIKE_S, 2.0 * (collar_s + dwell_s))
    headroom = _percentile_headroom(surface)
    if headroom is None:
        fill_s = 0.0
    else:
        frac = headroom / _SPIKE_FRACTION_SAFETY
        fill_s = max(_MIN_FILL_S, spike_s * (1.0 / frac - 1.0))
        declared = _longest_reward_percentile_window_s(surface)
        if declared is not None:
            fill_s = min(fill_s, declared + 2.0)
        # If a short declared window capped the fill, shrink the spike to keep
        # the fraction invariant rather than silently polluting the percentile.
        spike_s = min(spike_s, frac / (1.0 - frac) * fill_s)
    # The metric window opens only after the settle collar and the dwell latency,
    # so the filter transient and the dwell ramp cannot bias time-in-reward.
    metric = (fill_s + collar_s + dwell_s, fill_s + spike_s)
    return SweepGeometry(fill_s=fill_s, spike_s=spike_s,
                         total_s=fill_s + spike_s + _TAIL_PAD_S,
                         metric_window_s=metric)


def _tone_amplitude_uv(derive: DeriveSurface, env_uv: float, fs: int) -> float:
    """Tone amplitude that yields `env_uv` of in-band envelope, via the baked
    bandpass gain at the derive's band center."""
    center = 0.5 * (derive.band[0] + derive.band[1])
    gain = bandpass_gain_at(derive.sos, freq_hz=center, fs=fs)
    return env_uv / max(gain, 1e-3)


def _seg(derive: DeriveSurface, env_uv: float, start_s: float, end_s: float,
         fs: int) -> BandSegment | None:
    if env_uv <= 0.0 or end_s <= start_s or derive.sos is None:
        return None
    return BandSegment(band=derive.band, channel=derive.channel,
                       start_s=start_s, end_s=end_s,
                       content=Tone(amplitude_uv=_tone_amplitude_uv(derive, env_uv, fs)))


def _prime_segments(surface, noise_floor, *, geom, exclude: str | None) -> list[BandSegment]:
    """Drive every percentile-`below` leaf HIGH across the fill.

    Quiet is not favourable for `below(x, percentile(x))`: the threshold tracks
    its own signal, so the leaf holds only ~p% of the time no matter how quiet x
    is — capping the whole condition and flattening any sweep of a DIFFERENT
    leaf. Raising x during the fill lifts its rolling percentile, so the quiet
    spike then sits clearly below it. The swept derive is never primed (that
    would flatten its own sweep)."""
    if geom.fill_s <= 0.0:
        return []
    out = []
    for leaf in reward_leaves(surface):
        if leaf.signal == exclude or leaf.op != "below":
            continue
        if threshold_for(surface, leaf.threshold).kind != "percentile":
            continue
        anchor = leaf_anchor_uv(leaf, surface, noise_floor)
        if not anchor:
            continue
        seg = _seg(derive_for(surface, leaf.signal), _HI * anchor, 0.0, geom.fill_s,
                   surface.sample_rate_hz)
        if seg is not None:
            out.append(seg)
    return out


def _favourable_segments(surface, noise_floor, *, exclude: str | None,
                         window: tuple[float, float]) -> list[BandSegment]:
    """Hold every non-swept reward leaf TRUE over `window`.

    above: drive high. below+percentile: nothing here (primed in the fill).
    below+absolute: nothing (quiet already sits below the threshold)."""
    out = []
    for leaf in reward_leaves(surface):
        if leaf.signal == exclude or leaf.op != "above":
            continue
        anchor = leaf_anchor_uv(leaf, surface, noise_floor)
        if not anchor:
            continue
        seg = _seg(derive_for(surface, leaf.signal), _HI * anchor, window[0], window[1],
                   surface.sample_rate_hz)
        if seg is not None:
            out.append(seg)
    return out


def _unfavourable_segments(surface, noise_floor, *,
                           window: tuple[float, float]) -> list[BandSegment]:
    """Force every reward leaf FALSE over `window` (used after a hold ends).

    below leaves: drive them above their threshold. above leaves: silence
    already does it. Doing both keeps `any_of` rewards false too."""
    out = []
    for leaf in reward_leaves(surface):
        if leaf.op != "below":
            continue
        anchor = leaf_anchor_uv(leaf, surface, noise_floor)
        if not anchor:
            continue
        seg = _seg(derive_for(surface, leaf.signal), _HI * anchor, window[0], window[1],
                   surface.sample_rate_hz)
        if seg is not None:
            out.append(seg)
    return out


def _phase(total_s: float) -> PhaseOverride:
    return PhaseOverride(_WARMUP_S, total_s - _WARMUP_S - _COOLDOWN_S, _COOLDOWN_S)


def _scenario(label, segments, *, total_s, fs, tag, seed) -> Scenario:
    return Scenario(label=label, duration_s=total_s, sample_rate_hz=fs,
                    segments=tuple(segments), controls={},
                    coverage_tags=frozenset({f"metamorphic:{tag}", label}),
                    phase_override=_phase(total_s), seed=seed)


def _rank_group(surface, noise_floor, *, geom, derive_name, seed) -> SweepGroup:
    fs = surface.sample_rate_hz
    tag = f"rank_sweep:{derive_name}"
    direction, reason = sweep_direction(surface, derive_name)
    leaves = [x for x in reward_leaves(surface) if x.signal == derive_name]
    anchor = leaf_anchor_uv(leaves[0], surface, noise_floor) if leaves else None
    if direction != NONE and not anchor:
        direction, reason = NONE, "no resolvable decision level for the swept leaf"
    lo, hi = geom.metric_window_s
    if hi - lo < _MIN_METRIC_WINDOW_S:
        direction, reason = NONE, "metric window shorter than the settle+dwell collar"

    spike = (geom.fill_s, geom.fill_s + geom.spike_s)
    background = (_prime_segments(surface, noise_floor, geom=geom, exclude=derive_name)
                  + _favourable_segments(surface, noise_floor, exclude=derive_name,
                                         window=spike))
    derive = derive_for(surface, derive_name)
    members = [SweepMember(
        scenario=_scenario(f"{tag}:baseline", background, total_s=geom.total_s,
                           fs=fs, tag=tag, seed=seed),
        index=-1,
    )]
    for i, rung in enumerate(_LADDER):
        seg = _seg(derive, rung * (anchor or 0.0), spike[0], spike[1], fs)
        members.append(SweepMember(
            scenario=_scenario(f"{tag}:rung_{i}", background + ([seg] if seg else []),
                               total_s=geom.total_s, fs=fs, tag=tag, seed=seed),
            index=i,
        ))
    return SweepGroup(tag=tag, direction=direction, reason=reason,
                      members=tuple(members), metric_window_s=geom.metric_window_s)


def _hold_group(surface, noise_floor, *, geom, collar_s, seed) -> SweepGroup:
    """Sweep the hold duration with every leaf favourable, then unfavourable.

    Longer hold past dwell => more time in reward, on the same realization.

    Every hold is offset by the settle collar (`hold = collar + f*dwell`) and the
    metric window opens after it. Without that offset the collar eats the whole
    reward: at f=5 and dwell=250 ms the tone runs 1.25 s, a ~1 s filter collar
    leaves ~0 s of reward, every rung measures 0.0, and the flat sweep fails
    `no_contrast` on a perfectly good engine."""
    fs = surface.sample_rate_hz
    tag = "hold_duration_sweep"
    dwell_s = surface.dwell_ms / 1000.0
    max_reward_s = max(_HOLD_FRACTIONS) * dwell_s      # metric window length
    max_hold_s = collar_s + max_reward_s               # longest driven tone

    # Absolute-only protocols have no fill; still let the filters settle first.
    start_s = geom.fill_s if geom.fill_s > 0.0 else _WARMUP_S + 0.5

    headroom = _percentile_headroom(surface)
    if headroom is not None:
        frac = headroom / _SPIKE_FRACTION_SAFETY
        start_s = max(start_s, max_hold_s * (1.0 / frac - 1.0))
        declared = _longest_reward_percentile_window_s(surface)
        if declared is not None:
            start_s = min(start_s, declared + 2.0)
    total_s = start_s + max_hold_s + _TAIL_PAD_S
    metric = (start_s + collar_s, start_s + collar_s + max_reward_s)

    direction, reason = UP, None
    if metric[1] - metric[0] < _MIN_METRIC_WINDOW_S or max_reward_s <= dwell_s:
        direction, reason = NONE, "hold window shorter than the settle+dwell collar"

    hold_geom = SweepGeometry(fill_s=start_s, spike_s=max_hold_s, total_s=total_s,
                              metric_window_s=metric)

    def member(hold_s: float, index: int, label: str) -> SweepMember:
        segs = _prime_segments(surface, noise_floor, geom=hold_geom, exclude=None)
        if hold_s > 0.0:
            segs += _favourable_segments(surface, noise_floor, exclude=None,
                                         window=(start_s, start_s + hold_s))
        segs += _unfavourable_segments(surface, noise_floor,
                                       window=(start_s + hold_s, total_s))
        return SweepMember(
            scenario=_scenario(label, segs, total_s=total_s, fs=fs, tag=tag, seed=seed),
            index=index,
        )

    members = [member(0.0, -1, f"{tag}:baseline")]
    for i, f in enumerate(_HOLD_FRACTIONS):
        members.append(member(collar_s + f * dwell_s, i, f"{tag}:rung_{i}"))
    return SweepGroup(tag=tag, direction=direction, reason=reason,
                      members=tuple(members), metric_window_s=metric)


def plan_sweeps(surface: LogicalSurface, *, noise_floor: dict[str, float],
                collar_s: float, seed: int) -> tuple[SweepGroup, ...]:
    """One rank-sweep group per reward-feeding derive, plus one hold sweep."""
    geom = sweep_geometry(surface, collar_s=collar_s)
    seen: list[str] = []
    for leaf in reward_leaves(surface):
        if leaf.signal not in seen:
            seen.append(leaf.signal)
    groups = [_rank_group(surface, noise_floor, geom=geom, derive_name=name, seed=seed)
              for name in seen]
    groups.append(_hold_group(surface, noise_floor, geom=geom, collar_s=collar_s, seed=seed))
    return tuple(groups)


__all__ = [
    "DOWN", "NONE", "UP", "SweepGeometry", "SweepGroup", "SweepMember",
    "leaf_anchor_uv", "plan_sweeps", "sweep_direction", "sweep_geometry",
]
```

Note: `math` is imported but may be unused — remove the import if ruff flags it.

**Deliberately NOT implemented:** the spec's "Escalation" (assert monotonicity of
the median over 3–5 seeds, or a Spearman rank-correlation). Single-seed
time-in-reward was measured monotone on 5/5 seeds for both leaf directions, so
the escalation is unnecessary and would multiply corpus wall-clock. Revisit only
if Task 8 goes red for marginality — never to make a red gate pass.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_sweep.py -q && .venv/bin/ruff check src/refrain/fuzz/`
Expected: 13 passed, no lint findings.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/sweep.py tests/fuzz/test_sweep.py
git commit -m "fuzz: add sweep.py — direction-aware, anchored, floor-straddling sweep planner"
```

---

## Task 5: `metamorphic.py` — monotonicity + contrast

**Files:**
- Create: `src/refrain/fuzz/metamorphic.py`
- Create: `tests/fuzz/test_metamorphic.py`

**Interfaces:**
- Consumes: `sweep.SweepGroup`, `sweep.UP/DOWN/NONE`.
- Produces:
  - `SweepOutcome(tag, direction, baseline: float | None, series: tuple[tuple[str, float], ...], assertable: bool, reason: str | None)`
  - `MetamorphicViolation(tag, kind: str, direction, baseline, series, detail)` — `kind` ∈ `{"monotonicity", "no_contrast"}`
  - `check_metamorphic(groups, metrics: dict[str, float]) -> tuple[list[MetamorphicViolation], list[SweepOutcome]]`

- [ ] **Step 1: Write the failing tests**

`tests/fuzz/test_metamorphic.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""metamorphic.py — direction-aware monotonicity + contrast, no tolerance knob."""
from __future__ import annotations

from refrain.fuzz.metamorphic import check_metamorphic
from refrain.fuzz.scenario import PhaseOverride, Scenario
from refrain.fuzz.sweep import DOWN, NONE, UP, SweepGroup, SweepMember


def _group(tag, direction, n_rungs=4, reason=None) -> SweepGroup:
    def sc(label):
        return Scenario(label=label, duration_s=10.0, sample_rate_hz=256, segments=(),
                        controls={}, coverage_tags=frozenset(),
                        phase_override=PhaseOverride(1.0, 8.5, 0.5), seed=42)

    members = [SweepMember(scenario=sc(f"{tag}:baseline"), index=-1)]
    members += [SweepMember(scenario=sc(f"{tag}:rung_{i}"), index=i)
                for i in range(n_rungs)]
    return SweepGroup(tag=tag, direction=direction, reason=reason,
                      members=tuple(members), metric_window_s=(2.0, 8.0))


def _metrics(tag, baseline, series) -> dict[str, float]:
    m = {f"{tag}:baseline": baseline}
    m.update({f"{tag}:rung_{i}": v for i, v in enumerate(series)})
    return m


def test_above_leaf_non_decreasing_series_passes():
    # Real measured series (micro_single_pct, seed 42): baseline 0.10.
    g = _group("rank_sweep:up_env", UP)
    v, out = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.1022,
                                             [0.2987, 0.4609, 1.0, 1.0]))
    assert v == []
    assert out[0].assertable is True


def test_below_leaf_non_increasing_series_is_not_a_violation():
    """THE pre-existing bug. The merged check asserted non-DECREASING for every
    sweep, so this real micro_single_below series 'violated' on every seed."""
    g = _group("rank_sweep:down_env", DOWN)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:down_env", 1.0,
                                           [1.0, 0.2764, 0.0, 0.0]))
    assert v == []


def test_below_leaf_increasing_series_is_a_violation():
    g = _group("rank_sweep:down_env", DOWN)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:down_env", 1.0,
                                           [1.0, 0.2, 0.9, 0.0]))
    assert [x.kind for x in v] == ["monotonicity"]


def test_above_leaf_decreasing_series_is_a_violation():
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.1,
                                           [0.3, 0.2, 0.8, 1.0]))
    assert [x.kind for x in v] == ["monotonicity"]


def test_a_flat_sweep_fails_loud_as_vacuous():
    """[0,0,0,0] and [k,k,k,k] prove nothing. They must FAIL, not pass."""
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.0,
                                           [0.0, 0.0, 0.0, 0.0]))
    assert [x.kind for x in v] == ["no_contrast"]

    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.4,
                                           [0.4, 0.4, 0.4, 0.4]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_insufficient_contrast_fails_even_when_monotone():
    # Monotone, but the top rung closes < half the gap from baseline to 1.0.
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 0.0,
                                           [0.0, 0.1, 0.2, 0.25]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_a_baseline_already_saturated_is_no_contrast_not_a_pass():
    """base=1.0 (up) would satisfy `last - base >= 0.5*(1-base)` as 0 >= 0.
    That is a reward firing on pure noise, not a passing sweep."""
    g = _group("rank_sweep:up_env", UP)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:up_env", 1.0,
                                           [1.0, 1.0, 1.0, 1.0]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_a_baseline_already_silent_is_no_contrast_for_a_down_sweep():
    g = _group("rank_sweep:down_env", DOWN)
    v, _ = check_metamorphic([g], _metrics("rank_sweep:down_env", 0.0,
                                           [0.0, 0.0, 0.0, 0.0]))
    assert [x.kind for x in v] == ["no_contrast"]


def test_a_mixed_sweep_asserts_nothing_and_is_reported_not_passed():
    g = _group("rank_sweep:both", NONE, reason="derive feeds both above() and below() leaves")
    v, out = check_metamorphic([g], _metrics("rank_sweep:both", 0.5, [0.9, 0.1, 0.7, 0.2]))
    assert v == []
    assert out[0].assertable is False
    assert "both above()" in out[0].reason


def test_a_missing_metric_is_an_error_not_a_silent_skip():
    g = _group("rank_sweep:up_env", UP)
    try:
        check_metamorphic([g], {"rank_sweep:up_env:baseline": 0.1})
    except KeyError as exc:
        assert "rung_0" in str(exc)
    else:
        raise AssertionError("a missing sweep metric must raise, never pass silently")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_metamorphic.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'refrain.fuzz.metamorphic'`

- [ ] **Step 3: Implement `metamorphic.py`**

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Check the metamorphic properties of a measured sweep.

Two assertions per assertable group, and NO tolerance knob:

1. MONOTONICITY, direction-aware. `above` leaves push the reward up, `below`
   leaves push it down. The merged implementation asserted non-decreasing firing
   for every swept threshold, which is sign-wrong for inhibit leaves and
   false-failed every near-floor `below` protocol.

2. CONTRAST. The top rung must close at least half the gap from the measured
   baseline to saturation. A flat sweep proves nothing and FAILS LOUD rather
   than passing vacuously — the calibrated-oracle gate finding was exactly a
   family of hollow passes.

A slack term (`m[i] >= m[i-1] - k`) is deliberately absent: any k large enough to
absorb an inhibit inversion also hides a real regression. Robustness comes from
the metric (time-in-reward on a fixed noise realization) and the direction, not
from loosening the comparison.
"""
from __future__ import annotations

from dataclasses import dataclass

from .sweep import DOWN, NONE, UP, SweepGroup

# The top rung must close at least this fraction of the baseline->saturation gap.
_CONTRAST_FRACTION = 0.5
# Metrics are means of a boolean array; only float noise needs absorbing.
_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    tag: str
    direction: str
    baseline: float | None
    series: tuple[tuple[str, float], ...]
    assertable: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class MetamorphicViolation:
    tag: str
    kind: str                              # "monotonicity" | "no_contrast"
    direction: str
    baseline: float
    series: tuple[tuple[str, float], ...]
    detail: str


def _is_monotone(direction: str, values: list[float]) -> bool:
    if direction == UP:
        return all(values[i] >= values[i - 1] - _EPS for i in range(1, len(values)))
    return all(values[i] <= values[i - 1] + _EPS for i in range(1, len(values)))


def _contrast(direction: str, baseline: float, last: float) -> tuple[bool, str]:
    """Did the top rung move the metric at least half way to saturation?

    Saturation is 1.0 for an `up` sweep and 0.0 for a `down` one. The degenerate
    baselines are guarded explicitly: without that, `base == 1.0` on an `up`
    sweep satisfies `0 >= 0` — a reward that already holds on pure noise would
    pass. That is the hollow pass this tier exists to catch."""
    if direction == UP:
        if baseline >= 1.0 - _EPS:
            return False, ("baseline is already saturated (reward holds on noise "
                           "alone) — the sweep cannot demonstrate contrast")
        need = _CONTRAST_FRACTION * (1.0 - baseline)
        got = last - baseline
    else:
        if baseline <= _EPS:
            return False, ("baseline is already silent — the sweep cannot "
                           "demonstrate contrast")
        need = _CONTRAST_FRACTION * baseline
        got = baseline - last
    ok = got >= need - _EPS
    return ok, f"top rung moved {got:.4f}; needs >= {need:.4f} from baseline {baseline:.4f}"


def check_metamorphic(
    groups: list[SweepGroup], metrics: dict[str, float],
) -> tuple[list[MetamorphicViolation], list[SweepOutcome]]:
    """Evaluate every sweep group against its measured metrics.

    `metrics` maps scenario label -> time-in-reward. A missing metric raises
    KeyError: a sweep member that did not run must never be silently dropped."""
    violations: list[MetamorphicViolation] = []
    outcomes: list[SweepOutcome] = []
    for g in groups:
        rungs = sorted((m for m in g.members if m.index >= 0), key=lambda m: m.index)
        base_member = next((m for m in g.members if m.index < 0), None)
        series = tuple((m.scenario.label, metrics[m.scenario.label]) for m in rungs)
        baseline = metrics[base_member.scenario.label] if base_member else None

        if g.direction == NONE or baseline is None or len(series) < 2:
            outcomes.append(SweepOutcome(
                tag=g.tag, direction=g.direction, baseline=baseline, series=series,
                assertable=False,
                reason=g.reason or "sweep has no baseline or too few rungs",
            ))
            continue

        values = [v for _, v in series]
        if not _is_monotone(g.direction, values):
            expected = "non-decreasing" if g.direction == UP else "non-increasing"
            violations.append(MetamorphicViolation(
                tag=g.tag, kind="monotonicity", direction=g.direction,
                baseline=baseline, series=series,
                detail=f"time-in-reward must be {expected} in drive",
            ))
        ok, detail = _contrast(g.direction, baseline, values[-1])
        if not ok:
            violations.append(MetamorphicViolation(
                tag=g.tag, kind="no_contrast", direction=g.direction,
                baseline=baseline, series=series, detail=detail,
            ))
        outcomes.append(SweepOutcome(
            tag=g.tag, direction=g.direction, baseline=baseline, series=series,
            assertable=True, reason=None,
        ))
    return violations, outcomes


__all__ = ["MetamorphicViolation", "SweepOutcome", "check_metamorphic"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_metamorphic.py -q`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/metamorphic.py tests/fuzz/test_metamorphic.py
git commit -m "fuzz: add metamorphic.py — direction-aware monotonicity + fail-loud contrast"
```

---

## Task 6: Wire the tier into `runner.py` + `report.py`; delete the old check

**Files:**
- Modify: `src/refrain/fuzz/runner.py`
- Modify: `src/refrain/fuzz/check.py`
- Modify: `src/refrain/fuzz/generate.py`
- Modify: `src/refrain/fuzz/report.py`
- Modify: `tests/fuzz/test_runner.py`, `tests/fuzz/test_check.py`, `tests/fuzz/test_generate.py`, `tests/fuzz/test_report.py`

**Interfaces:**
- Consumes: `engine.run_scenario/time_in_reward/measure_noise_floor`, `sweep.plan_sweeps`, `metamorphic.check_metamorphic`, `surface.METAMORPHIC`.
- Produces:
  - `fuzz_protocol(ir, *, path, max_scenarios, chunk_size, seed: int = 42) -> ProtocolOutcome`
  - `run_batch(paths, *, max_scenarios, chunk_size, resolve_fn, seed: int = 42)`
  - `render_report(*, protocol_name, tier, results, sweep_outcomes, metamorphic_violations, all_coverage_tags)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/fuzz/test_runner.py`:

```python
from refrain.fuzz.runner import ERRORED


def test_percentile_single_leaf_now_fuzzes_clean_under_the_metamorphic_tier():
    out = _run("bench/protocols/micro_single_pct.refrain", max_scenarios=0)
    assert out.status == FUZZED
    assert out.passed is True
    assert "Metamorphic" in out.report


def test_realistic_smr_moves_to_the_metamorphic_tier_and_passes():
    # It fires 7 events on the quiet negative control; its old sample-exact pass
    # was hollow (the oracle marked the percentile regions DON'T-CARE).
    out = _run("bench/protocols/realistic_smr.refrain", max_scenarios=0)
    assert out.status == FUZZED
    assert out.passed is True
    assert "tier: metamorphic" in out.report


def test_absolute_only_protocols_keep_the_sample_exact_tier():
    for rel in ("bench/protocols/micro_single_above.refrain",
                "bench/protocols/micro_single_below.refrain"):
        out = _run(rel, max_scenarios=0)
        assert out.status == FUZZED, rel
        assert out.passed is True, rel
        assert "tier: sample_exact" in out.report, rel


def test_max_scenarios_never_truncates_a_sweep_group():
    """Truncating a sweep silently drops rungs and breaks the monotonicity
    comparison. The cap applies to oracle scenarios only."""
    out = _run("bench/protocols/micro_single_pct.refrain", max_scenarios=1)
    assert out.status == FUZZED
    assert out.passed is True


def test_seed_is_threaded_into_every_scenario():
    a = _run_seeded("bench/protocols/micro_single_pct.refrain", seed=42)
    b = _run_seeded("bench/protocols/micro_single_pct.refrain", seed=43)
    assert a.passed is True and b.passed is True
    assert a.report != b.report  # different realization => different metrics
```

Add the `_run_seeded` helper to that file:

```python
def _run_seeded(rel: str, *, seed: int):
    return fuzz_protocol(_ir(rel), path=rel, max_scenarios=0, chunk_size=64, seed=seed)
```

and update `_run` to pass `max_scenarios=kw.get("max_scenarios", 2)` through unchanged.

In `tests/fuzz/test_check.py`, **delete** every test of `check_metamorphic_monotonic` and `_series_sort_key`. Find them with `grep -n "metamorphic\|series_sort" tests/fuzz/test_check.py`.

In `tests/fuzz/test_generate.py`, delete tests of `generate_rank_sweep` / `generate_hold_duration_sweep` (`grep -n "rank_sweep\|hold_duration" tests/fuzz/test_generate.py`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_runner.py -q`
Expected: FAIL — `TypeError: fuzz_protocol() got an unexpected keyword argument 'seed'` (and the percentile protocol still SKIPs).

- [ ] **Step 3: Unskip percentile single-leaf (relocated from Task 3)**

This is the single point where skip behaviour flips — the sweeps now exist to gate it.

In `src/refrain/fuzz/surface.py`, in `_classify_single_leaf`, **delete** the percentile rejection:

```python
    if thr.kind == "percentile":
        raise UnsupportedProtocol("single percentile-leaf reward (needs calibrated oracle)")
```

and change the following guard so percentile passes through while `dynamic` still skips:

```python
    if thr.kind not in ("absolute", "percentile"):
        raise UnsupportedProtocol(f"single {thr.kind}-threshold reward (unsupported)")
    if thr.kind == "absolute" and thr.absolute_uv is None:
        # e.g. `absolute(value: <control>)` — the surface only extracts numeric
        # literals, so the value is unresolved. Skip cleanly rather than crash
        # downstream in scenario generation (`thr.absolute_uv * ...` on None).
        raise UnsupportedProtocol("absolute threshold value did not resolve to a literal")
    return leaf
```

Two existing tests assert that skip. Update **both** (`grep -rn "percentile-leaf" tests/ src/` to confirm you found them all):

- `tests/fuzz/test_unsupported.py:97` — asserts `e.value.reason == "single percentile-leaf reward (needs calibrated oracle)"`. Delete that test.
- `tests/fuzz/test_batch.py:56` — asserts that reason appears in the batch by-reason breakdown, and also pins `"coverage: fuzzed 7 / total 26"` and `rc == 0`. Percentile single-leaf no longer skips, so: drop the percentile-leaf assertion, and **re-derive** the coverage line by running the batch (`.venv/bin/python -m pytest tests/fuzz/test_batch.py -q` will show the mismatch; then run the CLI on the same paths to read the real number). Keep `assert "composite-signal reward condition" in out` and keep `assert rc == 0` — if `rc != 0`, a protocol is genuinely violating and you must report that, not relax the assertion.

Add to `tests/fuzz/test_surface.py`:

```python
def test_single_percentile_leaf_is_no_longer_skipped():
    surface = build_surface(_ir("bench/protocols/micro_single_pct.refrain"))
    assert surface.tier == METAMORPHIC
    assert [leaf.op for leaf in reward_leaves(surface)] == ["above"]
```

- [ ] **Step 4: Delete the superseded code**

In `src/refrain/fuzz/check.py`, delete `_series_sort_key`, `MetamorphicViolation`, and `check_metamorphic_monotonic`; remove `import re`; trim `__all__` to `["ActualEvent", "PerScenarioResult", "VacuityError", "check_scenario"]`.

In `src/refrain/fuzz/generate.py`, delete `generate_rank_sweep` and `generate_hold_duration_sweep` and drop them from `__all__`. `_reward_positive_segments` is now used only by `_dwell_scenarios`; keep it. The sample-exact tier is absolute-only, so tighten `_amplitude_for_truth` — replace its percentile branch with:

```python
    if thr.kind != "absolute":
        raise ValueError(
            f"generate: sample-exact scenarios need an absolute threshold, got {thr.kind!r}"
        )
```

(and delete the `else: # percentile — pick amplitudes by rank intent` block).

- [ ] **Step 5: Rewrite `runner.py`'s pipeline**

Replace `fuzz_protocol` and `_build_corpus`, and add `_run_sweeps`:

```python
def fuzz_protocol(ir, *, path: str, max_scenarios: int, chunk_size: int,
                  seed: int = 42) -> ProtocolOutcome:
    """Fuzz one resolved protocol. Raises VacuityError on a generator bug."""
    try:
        surface = build_surface(ir)
        collar_samples = _collar_samples(surface, chunk_size)
        collar_s = collar_samples / surface.sample_rate_hz
        channels = channels_for_synthetic(ir)
        oracle_scenarios = _oracle_corpus(surface, max_scenarios, seed)
    except UnsupportedProtocol as exc:
        return ProtocolOutcome(path=path, status=SKIPPED, reason=exc.reason)
    except _BACKSTOP_ERRORS as exc:
        return ProtocolOutcome(
            path=path, status=SKIPPED, reason=f"unclassified ({_short_reason(exc)})"
        )

    # --- evaluate -> oracle -> check: OUTSIDE the backstop ---
    results = []
    all_tags: set[str] = set()
    for scenario in oracle_scenarios:
        all_tags |= set(scenario.coverage_tags)
        results.append(_run_one_scenario(
            scenario, ir=ir, surface=surface, channels=channels,
            collar_samples=collar_samples, chunk_size=chunk_size,
        ))

    # The noise floor is the decision level for percentile leaves; measuring it
    # needs one quiet engine run. Absolute-only protocols need no probe.
    geom = sweep_geometry(surface, collar_s=collar_s)
    noise_floor = (
        measure_noise_floor(ir=ir, surface=surface, channels=channels,
                            chunk_size=chunk_size, fill_s=geom.fill_s, seed=seed)
        if geom.fill_s > 0.0 else
        {d.name: 0.0 for d in surface.derives}
    )
    groups = plan_sweeps(surface, noise_floor=noise_floor, collar_s=collar_s, seed=seed)
    metrics = _run_sweeps(groups, ir=ir, surface=surface, channels=channels,
                          chunk_size=chunk_size)
    for g in groups:
        all_tags |= {f"metamorphic:{g.tag}"}
    violations, outcomes = check_metamorphic(list(groups), metrics)

    n_assertions = sum(r.n_crisp_assertions for r in results) + sum(
        1 for o in outcomes if o.assertable
    )
    if n_assertions == 0:
        raise VacuityError(
            f"protocol {surface.protocol_name!r}: zero crisp assertions anywhere "
            f"(no sample-exact assertion and no assertable sweep). This is a "
            f"generator bug, not a pass."
        )

    report = render_report(
        protocol_name=surface.protocol_name, tier=surface.tier, results=results,
        sweep_outcomes=outcomes, metamorphic_violations=violations,
        all_coverage_tags=all_tags,
    )
    has_violation = bool(violations) or any(
        r.verdict in (Verdict.MISSED, Verdict.SPURIOUS) for r in results
    )
    return ProtocolOutcome(
        path=path, status=FUZZED, passed=not has_violation, report=report
    )


def _oracle_corpus(surface, max_scenarios: int, seed: int):
    """Sample-exact scenarios. Empty for the metamorphic tier: where a percentile
    threshold decides firing, the oracle can only emit DON'T-CARE, and a
    DON'T-CARE that absorbs real noise-firing is a hollow pass, not coverage."""
    if surface.tier == METAMORPHIC:
        return []
    corpus = (list(generate_directed_scenarios(surface))
              + list(generate_characterization_probe(surface)))
    if max_scenarios > 0:
        corpus = corpus[:max_scenarios]
    return [dataclasses.replace(s, seed=seed) for s in corpus]


def _run_sweeps(groups, *, ir, surface, channels, chunk_size) -> dict[str, float]:
    """Measure time-in-reward for every member of every sweep group.

    Unassertable groups are still RUN (their series is reported), so a reader can
    see why nothing was asserted rather than seeing silence."""
    fs = surface.sample_rate_hz
    metrics: dict[str, float] = {}
    for g in groups:
        for m in g.members:
            res = run_scenario(m.scenario, ir=ir, channels=channels, chunk_size=chunk_size)
            metrics[m.scenario.label] = time_in_reward(
                res.streams, window_s=g.metric_window_s, fs=fs)
    return metrics
```

Update `runner.py`'s imports:

```python
import dataclasses

from .check import ActualEvent, VacuityError, check_scenario
from .engine import apply_phase_override, measure_noise_floor, run_scenario, time_in_reward
from .errors import UnsupportedProtocol
from .generate import generate_characterization_probe, generate_directed_scenarios
from .metamorphic import check_metamorphic
from .oracle import predict, settle_time_s
from .report import render_report
from .scenario import Verdict
from .surface import METAMORPHIC, build_surface
from .sweep import plan_sweeps, sweep_geometry
```

Delete the old `_build_corpus`. Thread `seed` through `run_batch`:

```python
def run_batch(paths, *, max_scenarios, chunk_size, resolve_fn, seed: int = 42) -> list[ProtocolOutcome]:
```

and pass `seed=seed` into the `fuzz_protocol(...)` call inside it.

- [ ] **Step 6: Add the metamorphic section to `report.py`**

Change `render_report`'s signature to accept `tier: str` and `sweep_outcomes`, import `MetamorphicViolation`/`SweepOutcome` from `.metamorphic` instead of `.check`, print the tier under the header:

```python
    out.append(f"\n{_BAR}\nrefrain fuzz: {protocol_name}\n  tier: {tier}\n{_BAR}\n")
```

and replace the old metamorphic-violations block with:

```python
    if sweep_outcomes:
        out.append("\n## Metamorphic sweeps\n")
        for o in sweep_outcomes:
            series = " -> ".join(f"{v:.3f}" for _, v in o.series)
            if not o.assertable:
                out.append(f"  [NO ASSERTION] {o.tag}: {o.reason}\n")
                out.append(f"       series (recorded, not asserted): {series}\n")
                continue
            out.append(f"  [{o.direction.upper():4}] {o.tag}: "
                       f"baseline {o.baseline:.3f} | {series}\n")
    if metamorphic_violations:
        out.append("\n  METAMORPHIC violations:\n")
        for v in metamorphic_violations:
            series = " -> ".join(f"{val:.3f}" for _, val in v.series)
            out.append(f"    [VIOLATION:{v.kind.upper()}] {v.tag} ({v.direction}): "
                       f"baseline {v.baseline:.3f} | {series}\n      {v.detail}\n")
```

Update `overall` to account for sweeps:

```python
    n_asserted = sum(1 for o in sweep_outcomes if o.assertable)
    out.append(f"  sweeps asserted: {n_asserted} / {len(sweep_outcomes)}\n")
    overall = "PASS" if (pass_count == len(rs) and not metamorphic_violations) else "FAIL"
```

Update `tests/fuzz/test_report.py` call sites to pass `tier="sample_exact"` and `sweep_outcomes=[]`.

- [ ] **Step 7: Run the full fuzz suite**

Run: `.venv/bin/python -m pytest tests/fuzz/ -q && .venv/bin/ruff check src/refrain/fuzz/ && .venv/bin/ruff check src/refrain --select F,E9`
Expected: all pass, no lint findings.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/fuzz/ tests/fuzz/
git commit -m "fuzz: route noise-dominated protocols to the metamorphic tier"
```

---

## Task 7: `--seed` CLI flag

**Files:**
- Modify: `src/refrain/cli.py`
- Modify: `tests/fuzz/test_cli_fuzz.py`

**Interfaces:**
- Consumes: `runner.fuzz_protocol(..., seed=)`, `runner.run_batch(..., seed=)`.
- Produces: `refrain fuzz PATH... [--seed N]`, default 42.

- [ ] **Step 1: Write the failing test**

Append to `tests/fuzz/test_cli_fuzz.py`:

```python
def test_fuzz_accepts_a_seed_flag(capsys):
    rc = main(["fuzz", "bench/protocols/micro_single_above.refrain", "--seed", "43"])
    assert rc == 0


def test_fuzz_seed_defaults_to_42(capsys):
    rc = main(["fuzz", "bench/protocols/micro_single_above.refrain"])
    assert rc == 0
```

(Mirror the existing invocation style in that file — check `grep -n "def test_" tests/fuzz/test_cli_fuzz.py` and reuse its runner/`main` import and any `monkeypatch.chdir` fixture.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_cli_fuzz.py -q`
Expected: FAIL — `unrecognized arguments: --seed`

- [ ] **Step 3: Implement**

In `src/refrain/cli.py`, after the `--chunk-size` argument of `fuzz_cmd` (around line 355):

```python
    fuzz_cmd.add_argument(
        "--seed", type=int, default=42, metavar="N",
        help="Noise-realization seed (default: 42). Every scenario in a sweep "
             "shares it, so the sweep is a controlled A/B on one realization. "
             "Vary it to re-check a violation against another realization.",
    )
```

In `_fuzz_batch`, pass `seed=args.seed` to `run_batch(...)`. In `_fuzz_single`, pass `seed=args.seed` to `fuzz_protocol(...)`.

Update the module docstring at `cli.py:15`:

```python
#   - `refrain fuzz PATH... [--max-scenarios N] [--seed N]`  — auto-synthesise scenarios
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_cli_fuzz.py -q && .venv/bin/ruff check src/refrain --select F,E9`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/cli.py tests/fuzz/test_cli_fuzz.py
git commit -m "fuzz(cli): add --seed to select the noise realization"
```

---

## Task 8: THE GATE — corpus × 5 seeds must be clean

> **This task is the point of the increment.** It has veto power. If the corpus
> cannot go clean on a known-good engine, the design is not shippable: **STOP,
> report, and re-brainstorm.** Do not add slack, do not widen the contrast rule,
> do not special-case a protocol. Tuning the *ladder* (a probe) is legitimate;
> loosening the *assertion* is what killed the calibrated oracle.

**Files:**
- Modify: `tools/fuzz_corpus_gate.py` (only if Task 1's API guesses were wrong)
- Create: `docs/superpowers/ci/metamorphic-tier-gate-result.md`

- [ ] **Step 1: Run the gate on the refrain-protocols corpus, 5 fixed seeds**

Run:
```bash
.venv/bin/python tools/fuzz_corpus_gate.py \
  --corpus /Users/jcroall/git/refrain-protocols/protocols \
  --library /Users/jcroall/git/refrain-protocols/lib \
  --seeds 41,42,43,44,45 2>&1 | tee /tmp/gate.txt; tail -20 /tmp/gate.txt
```

Expected: `RESULT: PASS` — `violations across 5 seeds: 0`, `hollow passes: 0`.

Budget ~30–75 min (measured: ~0.9 s per `realistic_smr`-class engine run, ~20 runs per metamorphic protocol). Run it in the background and poll.

- [ ] **Step 2: Interpret the result honestly**

- **`violations: 0` and `hollow passes: 0`** → the gate is green. Proceed.
- **`other errors: N`** → these are pre-existing corpus gaps (coherence, bandpower, composite, montage `C3 not in source`). They are *not* gate failures and are out of scope; record the count.
- **Any violation** → diagnose before touching thresholds. In order:
  1. Is the sweep's `direction` right for that leaf? (a `sweep_direction` bug)
  2. Is the anchor the real decision level? (`leaf_anchor_uv` — percentile leaves must use the measured floor)
  3. Is a percentile-`below` leaf unprimed, capping the condition? (R4)
  4. Is the spike too large a fraction of the percentile buffer? (`sweep_geometry`)
  Fixing any of these is fixing the *probe*. If none of them explains it, the
  violation is either a real engine bug (investigate with
  `superpowers:systematic-debugging`) or the design does not hold — **STOP.**
- **Any hollow pass** (`generator-bug:`) → a protocol asserted nothing. Report it; do not paper over it.

- [ ] **Step 3: Re-probe coverage and record the unlock**

Run: `.venv/bin/python -m refrain.cli fuzz /Users/jcroall/git/refrain-protocols/protocols --library /Users/jcroall/git/refrain-protocols/lib 2>&1 | tail -20`

Capture the `coverage: fuzzed N / total M` line and the by-reason skip breakdown.

- [ ] **Step 4: Write the result document**

`docs/superpowers/ci/metamorphic-tier-gate-result.md`, containing: the per-seed table (violations / hollow passes / wall-clock), the before→after coverage numbers, the surviving skip reasons mapped to remaining work, and — if any check had to be adjusted — exactly what was adjusted and why it tuned the probe rather than the assertion.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/ci/metamorphic-tier-gate-result.md tools/fuzz_corpus_gate.py
git commit -m "fuzz: record the metamorphic-tier corpus gate result (5 seeds, zero violations)"
```

---

## Task 9: Open the refrain PR

- [ ] **Step 1: Verify everything, with evidence**

Run: `.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5 && .venv/bin/ruff check src/refrain/fuzz/ && .venv/bin/ruff check src/refrain --select F,E9`
Expected: full suite green, no lint findings. Paste the actual output into the PR body — do not claim green without it.

- [ ] **Step 2: Update `CHANGELOG.md`**

Under `## Unreleased`:

```markdown
### Added
- Fuzzer **metamorphic tier** (Tier 2): noise-dominated protocols (any percentile
  reward leaf) are gated on direction-aware, same-noise-realization sweeps of
  time-in-reward, with a fail-loud contrast requirement. Percentile single-leaf
  protocols are no longer skipped.
- `refrain fuzz --seed N` selects the noise realization.

### Fixed
- `check_metamorphic_monotonic` asserted non-decreasing firing for *every* swept
  threshold, which is sign-wrong for `below`/inhibit leaves and false-failed
  every near-floor protocol. The check is now direction-aware.
- The metamorphic metric was event count, i.e. dwell re-triggers — a noise
  artifact that runs backwards in drive (measured non-monotone on 10/10 seeds).
  It is now time-in-reward, read from the engine's `reward.event.holds` stream.
```

- [ ] **Step 3: Branch, push, open the PR**

```bash
git checkout -b fuzzer-metamorphic-tier   # if not already on it
git push -u origin fuzzer-metamorphic-tier
gh pr create --title "Fuzzer: metamorphic tier (noise-dominated-protocol gate)" --body "$(cat <<'EOF'
Implements `docs/superpowers/specs/2026-07-08-fuzzer-metamorphic-tier-design.md`
(Tier 2 of the tiered-gate charter), replacing the failed calibrated-oracle slot.

## What
The fuzzer fixes the noise seed and varies only tone amplitude, so noise is
byte-identical across a sweep — a controlled A/B on one realization. On that
footing, noise-dominated protocols are gated on:

- **direction-aware sweeps** (above-leaf → non-decreasing, below-leaf →
  non-increasing, mixed → assert nothing, reported not passed);
- **metric = time-in-reward**, not event count;
- an **anchored, floor-straddling ladder** with a **fail-loud contrast**
  requirement (a flat sweep FAILS as vacuous);
- **no tolerance-fudge knob.**

## Bugs this fixes
- `check_metamorphic_monotonic` was direction-blind: it asserted non-*decreasing*
  firing for every swept threshold, false-failing every `below`/inhibit leaf.
- The metric was event count = dwell re-triggers, a pure noise artifact. Measured
  on `micro_single_pct`: `[12, 16, 9, 9]` — it runs *backwards* in drive, and is
  non-monotone on 5/5 seeds. Time-in-reward is monotone on 5/5.

## Gate (Task 0)
Whole refrain-protocols corpus, metamorphic tier on, 5 fixed seeds, known-good
engine: **zero violations, zero hollow passes.** See
`docs/superpowers/ci/metamorphic-tier-gate-result.md` (and
`...-baseline.md` for the red it eliminates).

## Design refinements found while validating
Five, all recorded in the spec addendum with measurements. The load-bearing one:
**quiet is not favourable for a percentile-`below` leaf** — the threshold tracks
its own signal, so it holds ~p% of the time however quiet the signal is, capping
the condition and flattening any sweep of a different leaf. Such leaves must be
primed high during the fill.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Task 10: refrain release (gated on Task 9 merging)

> Blocked until the refrain PR merges. Per the release procedure: bump both
> `pyproject.toml` files + `CHANGELOG.md` in a `release: vX.Y.Z` PR, then tag the
> **merge commit**. Never tag before the bump PR merges — that publishes
> mislabeled wheels.

- [ ] **Step 1: Confirm the fuzzer PR is merged**

Run: `gh pr view <N> --json state,mergeCommit`
Expected: `"state":"MERGED"`.

- [ ] **Step 2: Bump the version to 0.13.0**

New minor: the fuzzer gains a tier and `refrain fuzz` gains a flag; no engine
behaviour changes. Edit both `pyproject.toml` files (find them:
`git ls-files '*pyproject.toml'`) and move the `## Unreleased` CHANGELOG entries
under `## v0.13.0 — 2026-07-09`.

- [ ] **Step 3: Open the release PR, merge it, then tag the merge commit**

```bash
git checkout -b release-v0.13.0 && git commit -am "release: v0.13.0" && git push -u origin release-v0.13.0
gh pr create --title "release: v0.13.0" --body "Fuzzer metamorphic tier + \`refrain fuzz --seed\`."
# after it merges:
git checkout main && git pull
git tag v0.13.0 && git push origin v0.13.0
```

---

## Task 11: Flip the refrain-protocols CI gate (gated on Task 10)

**Files:**
- Modify: `/Users/jcroall/git/refrain-protocols/.github/workflows/ci.yml`

- [ ] **Step 1: Pin the new refrain and add a fuzz job**

In `ci.yml`, bump the pin `refrain @ git+https://github.com/refrain-lang/refrain.git@v0.12.0` → `@v0.13.0`, and add a job after `validate`:

```yaml
  fuzz:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install refrain
        run: |
          python -m pip install --upgrade pip
          pip install "refrain @ git+https://github.com/refrain-lang/refrain.git@v0.13.0"
      - name: Fuzz the corpus (structural + sample-exact + metamorphic tiers)
        run: refrain fuzz protocols/ --library lib --seed 42
```

- [ ] **Step 2: Verify the job command locally before pushing**

Run: `cd /Users/jcroall/git/refrain-protocols && /Users/jcroall/git/refrain/refrain/.venv/bin/python -m refrain.cli fuzz protocols/ --library lib --seed 42; echo "exit=$?"`
Expected: `exit=0`. If nonzero, read the batch report — a nonzero exit here means a real violation or an ERRORED protocol, and CI would be red on merge. Pre-existing ERRORED protocols (coherence/bandpower/montage) make the batch exit 1: if so, the fuzz job must scope to the protocols the fuzzer supports until those gaps close. Record the decision in the PR body rather than silencing the exit code.

- [ ] **Step 3: Open the PR**

```bash
cd /Users/jcroall/git/refrain-protocols
git checkout -b ci-fuzz-gate && git commit -am "ci: gate the corpus on refrain fuzz (structural + metamorphic tiers)" && git push -u origin ci-fuzz-gate
gh pr create --title "ci: gate the corpus on \`refrain fuzz\`" --body "Turns on the tiered fuzz gate now that refrain v0.13.0 ships the metamorphic tier. Pins refrain to v0.13.0.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Notes for the implementer

**What is being tested, honestly.** The metamorphic tier does not verify that any
specific event is "correct" — where behaviour is noise-arbitrary, that question
has no answer. It verifies *trends on a fixed noise realization*: more in-band
drive cannot make an `above`-gated reward hold less. That is the strongest claim
available in this regime, and it is stated rather than hidden behind DON'T-CARE.

**Why reading the engine's own streams is not circular.** `engine.py` reads
`reward.event.holds` and the derive envelopes. Those place the ladder and measure
the metric; they never predict what the engine *should* do. The assertion is
engine-vs-property. A broken DSP layer would misplace the ladder, but DSP is
covered by the Rust↔Python golden vectors and the band-characterization probe —
see the charter's "what the fuzzer does NOT verify".

**Known coverage gap introduced.** Metamorphic-tier protocols do not run the
characterization probe (its scenarios are oracle-checked, and the oracle is
exactly what we suppress for them). Note it in the Task-8 result doc as remaining
work; do not silently drop it.
