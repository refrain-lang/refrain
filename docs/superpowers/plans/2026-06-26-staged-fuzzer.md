# Staged / Segmented Protocol Fuzzing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `refrain fuzz` to introspect, drive, predict, and check **staged/segmented** protocols (v0.7.0) — per-block reward selection, per-phase muting, stage transitions, warm-signal boundaries, and the host control API (`advance_phase`/`hold`/`set_clock_frozen`).

**Architecture:** Generalise the existing v1 fuzz pipeline so a non-staged protocol is the degenerate "one implicit block, one timed phase, no host actions" case (existing 56 tests stay green). A new pure `phases.py` module resolves the per-sample active-phase/active-block/muted timeline from the surface's phases + a scenario's host-action schedule; the oracle selects the active block's reward bundle per sample; a new `drive.py` module runs the real evaluator step-by-step via `Evaluator.live(...).step_chunk(...)`, issuing host actions and collecting channel-tagged events. The oracle stays independent (predicts from baked coefficients + phase model, never runs the evaluator).

**Tech Stack:** Python 3.10+, numpy, scipy.signal, the existing `refrain.fuzz` package, and v0.7.0's staged runtime (`Evaluator.live`, `IRBlock`, `IRProtocol.blocks`/`reward_bundles`, `IRPhase.mode`/`block`).

**Source-of-truth spec:** `docs/superpowers/specs/2026-06-26-staged-fuzzer-design.md`.

---

## Prerequisite: this plan runs on a `main`-reconciled branch

The staged runtime (`Evaluator.live`, `IRBlock`, etc.) only exists on `main` (v0.7.0). The fuzzer branch (`worktree-protocol-fuzzer`) was cut before it. **Task 0 merges `main` in.** Every later task assumes the staged runtime is importable.

## Verified API facts (from v0.7.0 `main` — do not re-guess these)

- `IRBlock(name: str, thresholds: tuple[str,...], reward: str | None, outputs: tuple[str,...], inhibits: tuple[str,...], loc=None)` — note plural `thresholds`/`outputs`/`inhibits`, singular `reward` (one bundle name).
- `IRProtocol.blocks: dict[str, IRBlock]` and `IRProtocol.reward_bundles: dict[str, IRReward]` — both default `{}` (empty ⇒ non-staged).
- `IRPhase(name, duration_ms: float, output_muted: bool, mode: str = "timed", block: str | None = None, loc=None)`; `mode ∈ {"timed","open","timed_with_floor"}`.
- A reward bundle is an `IRReward(continuous, event, combine="all", components=(), loc=None)`; its `event` is the `dwell(...)` `IRCall`. The existing `surface.py` helpers `_reward_condition_from_ir`/`_dwell_ms_from_ir` read a *protocol's* `ir.reward`; this plan refactors them to read any `IRReward`.
- `IRProtocol.output: dict[str, IRExpr]` (channel name → expr). An **event channel** is one whose expr is `IRRewardField(field_path == "event")` (the runtime's `_is_event_channel` heuristic). For `staged_beta_alpha`: `audio_chime` is the event channel, `audio_gain` is analog.
- Evaluator push API: `Evaluator.live(ir, *, sample_rate_hz, channel_names, record_streams=False, backend="auto", seed_state=None)`; `.start(*, skip_warmup=False)`; `.step_chunk(raw_chunk: np.ndarray) -> list[Event]` (chunk shape `(n_samples, n_channels)`); `.advance_phase() -> bool`; `.hold(held=True) -> bool`; `.set_clock_frozen(frozen) -> None`; `.current_phase() -> dict{index,name,mode,output_muted,block,remaining_s,clock_frozen,held}` (snapshot of the *last processed* chunk's phase); `.stop()`. Use `backend="python"` for deterministic fuzzing.
- `Event(timestamp_s: float, channel: str, kind: str, value: float | None)` — `kind=="event"` for discrete chime (value None), `kind=="value"` for analog (value float). `channel` is the output dict key.
- `staged_beta_alpha.refrain`: channels `["Cz","A1","A2"]`; derives `beta_envelope` (15–20 Hz), `alpha_envelope` (8–12 Hz); thresholds `beta_t`/`alpha_t` = `absolute(value: <control>)` (controls `beta_uv`/`alpha_uv`, default 5 µV); bundles `beta_reward`/`alpha_reward`, each `event = dwell(condition: above(<env>,<thr>), duration: 500 ms)`; outputs `audio_gain=reward.continuous`, `audio_chime=reward.event`; blocks `beta_up`{threshold=beta_t,reward=beta_reward,output=[audio_gain,audio_chime]}, `alpha_up`{...alpha...}; phases: `warmup`(120 s,timed,muted) → `block1`(5 min,timed_with_floor,beta_up) → `rest1`(open,muted) → `block2`(5 min,timed_with_floor,alpha_up) → `rest2`(2 min,timed,muted) → `cooldown`(30 s,timed,muted).

---

## File Structure

| File | Job | Change |
|---|---|---|
| `src/refrain/fuzz/scenario.py` | Shared contract | Add `HostAction` union (`AdvancePhase`/`Hold`/`SetClockFrozen`) + `Scenario.actions`. |
| `src/refrain/fuzz/surface.py` | IR introspection | Add `BlockSurface`, `RewardBundleSurface`; extend `PhaseSurface` (`mode`,`block`) + `LogicalSurface` (`blocks`,`reward_bundles`,`event_channels`); degenerate one-block synthesis; refactor reward/dwell helpers to take an `IRReward`. |
| `src/refrain/fuzz/phases.py` | **NEW** pure phase-timeline resolver | `resolve_phase_timeline(surface, scenario, n_samples) -> PhaseTimeline` (active phase/block + muted mask per sample, honouring host actions). |
| `src/refrain/fuzz/oracle.py` | Independent predictor | `predict` consumes `phases.resolve_phase_timeline`; per-sample active-block reward selection; per-phase muting; no warm-boundary collar. |
| `src/refrain/fuzz/generate.py` | Scenario generator | Staged families: per-block pivots, block-isolation, per-phase muting, stage transition, warm-signal, host-action, (percentile-freeze in B). |
| `src/refrain/fuzz/check.py` | Checker | Channel-aware event attribution (active block's event channel). |
| `src/refrain/fuzz/report.py` | Report | Per-block behavioural summary. |
| `src/refrain/fuzz/drive.py` | **NEW** step-wise driver | `drive_scenario(ir, scenario, surface, *, chunk_size) -> list[ActualEvent]` via `Evaluator.live` + `step_chunk` + host actions. |
| `src/refrain/cli.py` | CLI | `_fuzz_one_scenario` uses `drive.drive_scenario` for staged (and degenerate) protocols; remove the v1 "unsupported" crash. |

**New tests:** `tests/fuzz/_staged.py` (fixture) + one module per changed unit + a staged end-to-end/mutation module.

## Conventions

- Every step is 2–5 min; each task ends with a commit. Strict TDD: failing test → run (see it fail) → minimal impl → run (see it pass) → commit.
- Run tests with `.venv/bin/python -m pytest tests/fuzz/ -x -q`. Keep the fuzz package ruff-clean: `.venv/bin/ruff check src/refrain/fuzz/`.
- Commit style: `feat(fuzz): ...` / `test(fuzz): ...` / `refactor(fuzz): ...`. End commit messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Reuse v1 machinery (3-valued leaf truth, condition tree, dwell, collar, `DontCareReason`, `ActualEvent`, `check_scenario`) — only *selection* of the active bundle is new.

---

## Task 0: Reconcile the branch with `main` (v0.7.0)

**Files:** none created; merges `main`.

- [ ] **Step 1: Confirm clean tree + current head**

Run: `git status --short` (expect empty) and `git log -1 --oneline` (expect `c199f1d ...` or later).

- [ ] **Step 2: Merge `main`**

```bash
git merge main --no-edit
```
Expected: clean merge, no conflicts (verified during planning). If conflicts appear, STOP and report.

- [ ] **Step 3: Reinstall + baseline tests**

```bash
.venv/bin/pip install -q -e ".[dev]"
.venv/bin/python -m pytest tests/fuzz/ -q
.venv/bin/python -m pytest tests/ -q -k "staged"
```
Expected: fuzz suite green (56 passed); staged runtime tests green. This confirms the staged runtime is importable and the v1 fuzzer still works on the merged tree.

- [ ] **Step 4: Confirm the staged crash is real (characterisation)**

Run: `.venv/bin/refrain fuzz examples/staged_beta_alpha.refrain --max-scenarios 1 2>&1 | tail -3`
Expected: a traceback ending `ValueError: surface: reward.event has no all_of/any_of condition`. (This is the gap this plan closes; do NOT fix it yet.)

- [ ] **Step 5: Commit the merge** (the merge commit already exists from Step 2; nothing to add). Proceed.

---

# Increment A — Core staged support (blocks, phases, muting; nominal-duration marching)

## Task A1: `Scenario` host-action contract

**Files:**
- Modify: `src/refrain/fuzz/scenario.py`
- Test: `tests/fuzz/test_scenario.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/fuzz/test_scenario.py`:

```python
from refrain.fuzz.scenario import AdvancePhase, Hold, SetClockFrozen


def test_host_actions_are_frozen_and_carry_timing():
    a = AdvancePhase(at_s=12.0)
    h = Hold(at_s=5.0)
    f = SetClockFrozen(at_s=3.0, frozen=True)
    assert a.at_s == 12.0
    assert h.at_s == 5.0 and h.held is True            # default held=True
    assert f.at_s == 3.0 and f.frozen is True
    with pytest.raises(Exception):
        a.at_s = 99.0                                   # frozen


def test_scenario_actions_default_empty_and_accepts_schedule():
    s = Scenario(
        label="staged", duration_s=10.0, sample_rate_hz=256,
        segments=(), controls={}, coverage_tags=frozenset(),
    )
    assert s.actions == ()
    s2 = Scenario(
        label="staged2", duration_s=10.0, sample_rate_hz=256,
        segments=(), controls={}, coverage_tags=frozenset(),
        actions=(AdvancePhase(at_s=4.0), Hold(at_s=2.0), SetClockFrozen(at_s=6.0, frozen=False)),
    )
    assert len(s2.actions) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_scenario.py -q`
Expected: `ImportError: cannot import name 'AdvancePhase'`.

- [ ] **Step 3: Implement** — in `src/refrain/fuzz/scenario.py`, add the three frozen dataclasses and a union (place them above `Scenario`), and add the field to `Scenario`:

```python
@dataclass(frozen=True, slots=True)
class AdvancePhase:
    """End the current phase now (host `advance_phase()`), at scenario time at_s."""
    at_s: float


@dataclass(frozen=True, slots=True)
class Hold:
    """Host `hold(held)` at at_s — extend a timed_with_floor phase past its floor
    (held=True) or re-arm auto-advance (held=False)."""
    at_s: float
    held: bool = True


@dataclass(frozen=True, slots=True)
class SetClockFrozen:
    """Host `set_clock_frozen(frozen)` at at_s — pause/resume the phase countdown."""
    at_s: float
    frozen: bool


HostAction = AdvancePhase | Hold | SetClockFrozen
```

Add to the `Scenario` dataclass (after `phase_override`, before `seed`):

```python
    actions: tuple[HostAction, ...] = ()
```

Update `__all__` to include `"AdvancePhase"`, `"Hold"`, `"SetClockFrozen"`, `"HostAction"`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_scenario.py -q` — expect all green. Then `.venv/bin/ruff check src/refrain/fuzz/scenario.py` — clean.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/scenario.py tests/fuzz/test_scenario.py
git commit -m "feat(fuzz): host-action schedule on Scenario (advance/hold/freeze)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A2: Surface — reward bundles, blocks, phase mode/block, degenerate synthesis

**Files:**
- Modify: `src/refrain/fuzz/surface.py`
- Create: `tests/fuzz/_staged.py`
- Test: `tests/fuzz/test_surface_staged.py`

- [ ] **Step 1: Shared staged fixture** — create `tests/fuzz/_staged.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Shared fixture: parse + resolve the staged_beta_alpha example."""
from __future__ import annotations

from pathlib import Path

from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGED_PROTOCOL = REPO_ROOT / "examples" / "staged_beta_alpha.refrain"


def resolved_staged_ir():
    return resolve(parse_file(STAGED_PROTOCOL), amp=None, parent_loader=None)
```

- [ ] **Step 2: Write the failing test** — create `tests/fuzz/test_surface_staged.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Surface extraction for staged protocols + degenerate (non-staged) synthesis."""
from __future__ import annotations

import pytest

from refrain.fuzz.surface import build_surface
from tests.fuzz._smr import resolved_smr_ir
from tests.fuzz._staged import resolved_staged_ir


@pytest.fixture(scope="module")
def staged():
    return build_surface(resolved_staged_ir())


def test_extracts_reward_bundles_with_conditions(staged):
    assert set(staged.reward_bundles) == {"beta_reward", "alpha_reward"}
    beta = staged.reward_bundles["beta_reward"]
    # bundle condition is a single `above(beta_envelope, beta_t)` leaf
    assert beta.dwell_ms == pytest.approx(500.0)
    leaves = _leaves(beta.condition)
    assert ("above", "beta_envelope", "beta_t") in leaves


def test_extracts_blocks_with_reward_and_outputs(staged):
    assert set(staged.blocks) == {"beta_up", "alpha_up"}
    b = staged.blocks["beta_up"]
    assert b.reward == "beta_reward"
    assert "beta_t" in b.thresholds
    assert "audio_chime" in b.outputs and "audio_gain" in b.outputs


def test_phases_carry_mode_and_block(staged):
    by_name = {p.name: p for p in staged.phases}
    assert by_name["block1"].mode == "timed_with_floor"
    assert by_name["block1"].block == "beta_up"
    assert by_name["rest1"].mode == "open"
    assert by_name["rest1"].block is None
    assert by_name["warmup"].output_muted is True


def test_event_channels_detected(staged):
    assert staged.event_channels == ("audio_chime",)


def test_non_staged_synthesises_one_implicit_block():
    smr = build_surface(resolved_smr_ir())
    assert len(smr.blocks) == 1
    (name, block), = smr.blocks.items()
    # implicit block wraps the top-level reward; its bundle is the default
    assert smr.reward_bundles[block.reward].dwell_ms == pytest.approx(250.0)
    # every phase points at the implicit block
    assert all(p.block == name for p in smr.phases)


def _leaves(node):
    from refrain.fuzz.surface import ConditionLeaf
    if isinstance(node, ConditionLeaf):
        return {(node.op, node.signal, node.threshold)}
    out = set()
    for c in node.children:
        out |= _leaves(c)
    return out
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_surface_staged.py -q`
Expected: `AttributeError`/`TypeError` (no `reward_bundles`/`blocks`/`event_channels` on the surface; `PhaseSurface` has no `mode`/`block`).

- [ ] **Step 4: Implement** — edit `src/refrain/fuzz/surface.py`:

(a) Add dataclasses (near the other `*Surface` dataclasses):

```python
@dataclass(frozen=True, slots=True)
class RewardBundleSurface:
    name: str
    condition: "ConditionNode | ConditionLeaf"
    dwell_ms: float


@dataclass(frozen=True, slots=True)
class BlockSurface:
    name: str
    thresholds: tuple[str, ...]
    reward: str                      # reward-bundle name (resolved; never None here)
    outputs: tuple[str, ...]
    inhibits: tuple[str, ...]
```

(b) Extend `PhaseSurface` to add two fields (keep existing ones):

```python
    mode: str = "timed"
    block: str | None = None
```

(c) Extend `LogicalSurface` to add three fields:

```python
    blocks: dict[str, BlockSurface]
    reward_bundles: dict[str, RewardBundleSurface]
    event_channels: tuple[str, ...]
```

(d) Refactor the existing reward/dwell readers to operate on an `IRReward`. Replace the body of `_reward_condition_from_ir(ir)` so a thin wrapper extracts `ir.reward` and a new `_reward_condition_from_reward(reward)` does the work on an `IRReward`; same for dwell. Concretely add:

```python
def _bundle_surface(name: str, reward) -> RewardBundleSurface:
    return RewardBundleSurface(
        name=name,
        condition=_reward_condition_from_reward(reward),
        dwell_ms=_dwell_ms_from_reward(reward),
    )
```

where `_reward_condition_from_reward(reward)` / `_dwell_ms_from_reward(reward)` are the current bodies of `_reward_condition_from_ir` / `_dwell_ms_from_ir` but reading `reward.event` instead of `ir.reward.event`. Keep `_reward_condition_from_ir(ir)` as `return _reward_condition_from_reward(ir.reward)` so the non-staged path is unchanged.

(e) In `build_surface`, after the existing extraction, build the staged fields:

```python
    # Reward bundles: explicit (staged) or the single default (non-staged).
    if ir.reward_bundles:
        reward_bundles = {n: _bundle_surface(n, rb) for n, rb in ir.reward_bundles.items()}
    else:
        reward_bundles = {"__default__": _bundle_surface("__default__", ir.reward)}

    # Event channels: output bindings that carry reward.event.
    event_channels = tuple(
        name for name, expr in ir.output.items() if _is_event_channel(expr)
    )

    # Blocks: explicit (staged) or one implicit block over the default reward.
    if ir.blocks:
        blocks = {
            n: BlockSurface(name=n, thresholds=tuple(b.thresholds), reward=b.reward,
                            outputs=tuple(b.outputs), inhibits=tuple(b.inhibits))
            for n, b in ir.blocks.items()
        }
        phases = tuple(
            PhaseSurface(name=p.name, duration_s=p.duration_ms / 1000.0,
                         output_muted=p.output_muted, mode=p.mode, block=p.block)
            for p in ir.session.phases
        )
    else:
        impl = "__default__"
        # `thresholds` is the ThresholdSurface tuple build_surface already built
        # (v1: `thresholds = tuple(_threshold_surface(t, ir) for t in ir.thresholds)`).
        blocks = {impl: BlockSurface(name=impl, thresholds=tuple(t.name for t in thresholds),
                                     reward=impl, outputs=event_channels, inhibits=())}
        phases = tuple(
            PhaseSurface(name=p.name, duration_s=p.duration_ms / 1000.0,
                         output_muted=p.output_muted, mode=getattr(p, "mode", "timed"),
                         block=impl)
            for p in ir.session.phases
        )
```

(`thresholds` is the local variable holding the already-built `ThresholdSurface` tuple; reuse it — do not re-enumerate `ir.thresholds`.) Add `_is_event_channel(expr)` mirroring the runtime:

```python
def _is_event_channel(expr) -> bool:
    from ..ir import IRRewardField
    return isinstance(expr, IRRewardField) and getattr(expr, "field_path", None) == "event"
```

Pass `blocks`, `reward_bundles`, `event_channels` into the `LogicalSurface(...)` constructor. Add the new names (`RewardBundleSurface`, `BlockSurface`) to `__all__`.

> **Note on phases:** the existing surface already builds a `phases` tuple — replace that construction with the block-aware one above (it now also sets `mode`/`block`). Keep `duration_s = duration_ms / 1000.0`.

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_surface_staged.py tests/fuzz/test_surface.py -q` — both the new staged tests and the original surface tests (non-staged) pass. Then `.venv/bin/ruff check src/refrain/fuzz/surface.py`.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/surface.py tests/fuzz/_staged.py tests/fuzz/test_surface_staged.py
git commit -m "feat(fuzz): surface extracts blocks/reward-bundles/phase-mode (+ degenerate one-block)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A3: `phases.py` — per-sample phase/block/muted timeline (nominal durations)

**Files:**
- Create: `src/refrain/fuzz/phases.py`
- Test: `tests/fuzz/test_phases.py`

This module is pure and host-action-aware in shape, but Increment A only implements the **nominal-duration** marching (no host actions yet — `actions=()`); Task B1 adds host-action handling. It computes, per sample: the active phase index, the active block name (or None), whether output is muted, and whether percentile windows are frozen.

- [ ] **Step 1: Write the failing test** — create `tests/fuzz/test_phases.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Pure phase-timeline resolution from surface phases + scenario host actions."""
from __future__ import annotations

import pytest

from refrain.fuzz.phases import resolve_phase_timeline
from refrain.fuzz.scenario import Scenario
from refrain.fuzz.surface import build_surface
from tests.fuzz._staged import resolved_staged_ir


@pytest.fixture(scope="module")
def staged():
    return build_surface(resolved_staged_ir())


def _scn(duration_s, fs, actions=()):
    return Scenario(label="t", duration_s=duration_s, sample_rate_hz=fs,
                    segments=(), controls={}, coverage_tags=frozenset(), actions=actions)


def test_nominal_marching_assigns_blocks_per_phase(staged):
    fs = staged.sample_rate_hz
    # warmup 120 + block1 300 + rest1(open, synthetic dwell) ...
    duration = 130.0
    tl = resolve_phase_timeline(staged, _scn(duration, fs), n_samples=int(duration * fs))
    # at t=10 s (warmup) -> no block, muted
    i = int(10 * fs)
    assert tl.active_block[i] is None
    assert tl.muted[i] is True
    # at t=125 s (into block1) -> beta_up, not muted
    j = int(125 * fs)
    assert tl.active_block[j] == "beta_up"
    assert tl.muted[j] is False


def test_open_phase_gets_synthetic_dwell_then_advances(staged):
    fs = staged.sample_rate_hz
    # warmup(120) + block1(300) + rest1(open) — with no host action, rest1 uses a
    # synthetic dwell and then block2 becomes active.
    duration = 700.0
    tl = resolve_phase_timeline(staged, _scn(duration, fs), n_samples=int(duration * fs))
    # somewhere after warmup+block1+synthetic-rest we should reach block2 (alpha_up)
    assert "alpha_up" in {b for b in tl.active_block if b is not None}


def test_phase_index_is_monotonic_nondecreasing(staged):
    fs = staged.sample_rate_hz
    duration = 200.0
    tl = resolve_phase_timeline(staged, _scn(duration, fs), n_samples=int(duration * fs))
    idxs = tl.phase_index
    assert all(idxs[k] <= idxs[k + 1] for k in range(len(idxs) - 1))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_phases.py -q`
Expected: `ModuleNotFoundError: No module named 'refrain.fuzz.phases'`.

- [ ] **Step 3: Implement** — create `src/refrain/fuzz/phases.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Pure resolution of a staged session's per-sample phase/block/muted timeline.

Consumes the LogicalSurface's phases (name/duration/mode/block/output_muted) and a
Scenario's host-action schedule, and produces per-sample arrays the oracle and
report read. Never runs the evaluator. Increment A implements nominal-duration
marching (actions=()); Increment B folds in advance/hold/freeze.
"""
from __future__ import annotations

from dataclasses import dataclass

# Synthetic dwell (seconds) for an `open` phase when no host AdvancePhase ends it.
# Long enough for a dwell/condition to resolve, short enough to keep runs fast.
_OPEN_SYNTHETIC_DWELL_S = 8.0


@dataclass(frozen=True, slots=True)
class PhaseTimeline:
    phase_index: list[int]        # active phase index per sample
    active_block: list[str | None]
    muted: list[bool]             # output suppressed at this sample
    frozen_ingest: list[bool]     # percentile windows frozen at this sample (Increment B)
    phase_starts: list[int]       # sample index where each phase becomes active (len = n_phases marched)


def resolve_phase_timeline(surface, scenario, n_samples: int) -> PhaseTimeline:
    fs = surface.sample_rate_hz
    phases = surface.phases
    phase_index = [0] * n_samples
    active_block = [None] * n_samples
    muted = [False] * n_samples
    frozen = [False] * n_samples
    phase_starts: list[int] = []

    i = 0          # sample cursor
    pi = 0         # phase cursor
    seen_first_muted_done = False
    while i < n_samples and pi < len(phases):
        ph = phases[pi]
        phase_starts.append(i)
        dur_s = ph.duration_s if ph.duration_s and ph.duration_s > 0 else _OPEN_SYNTHETIC_DWELL_S
        if ph.mode == "open" and (not ph.duration_s or ph.duration_s <= 0):
            dur_s = _OPEN_SYNTHETIC_DWELL_S
        end = min(n_samples, i + int(round(dur_s * fs)))
        # percentile freeze: ingest during the first phase + non-muted phases;
        # freeze during muted phases AFTER the first phase.
        is_frozen = ph.output_muted and seen_first_muted_done
        for k in range(i, end):
            phase_index[k] = pi
            active_block[k] = ph.block
            muted[k] = ph.output_muted
            frozen[k] = is_frozen
        if ph.output_muted:
            seen_first_muted_done = True
        i = end
        pi += 1
    # tail beyond the last phase (if any): clamp to last phase
    if i < n_samples and phases:
        last = len(phases) - 1
        for k in range(i, n_samples):
            phase_index[k] = last
            active_block[k] = phases[last].block
            muted[k] = phases[last].output_muted
    return PhaseTimeline(phase_index, active_block, muted, frozen, phase_starts)


__all__ = ["PhaseTimeline", "resolve_phase_timeline"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_phases.py -q` — green. `.venv/bin/ruff check src/refrain/fuzz/phases.py`.

> **Note:** the `seen_first_muted_done` flag intentionally fires AFTER the first muted phase completes, so the warmup phase itself ingests (freeze only applies to muted rests *after* warmup), matching the runtime's "one baseline up front" rule.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/phases.py tests/fuzz/test_phases.py
git commit -m "feat(fuzz): pure phase-timeline resolver (nominal-duration marching)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A4: Oracle — active-block reward selection + per-phase muting

**Files:**
- Modify: `src/refrain/fuzz/oracle.py`
- Test: `tests/fuzz/test_oracle_staged.py`

Generalise `predict` to select the active block's reward bundle per sample (via `phases.resolve_phase_timeline`) and to mute per active phase. The 3-valued envelope/leaf/dwell machinery is reused; only bundle selection + muting source change. The `staged_beta_alpha` thresholds are absolute, so Increment A predicts it fully.

- [ ] **Step 1: Write the failing test** — create `tests/fuzz/test_oracle_staged.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Oracle prediction for staged protocols (active-block selection + muting)."""
from __future__ import annotations

import pytest

from refrain.fuzz.oracle import predict
from refrain.fuzz.scenario import BandSegment, Scenario, Tone
from refrain.fuzz.surface import build_surface
from tests.fuzz._staged import resolved_staged_ir


@pytest.fixture(scope="module")
def staged():
    return build_surface(resolved_staged_ir())


def _beta_tone(start_s, end_s, amp=20.0):
    return BandSegment(band=(15.0, 20.0), channel="Cz", start_s=start_s, end_s=end_s,
                       content=Tone(amplitude_uv=amp))


def test_beta_signal_during_block1_predicts_fire(staged):
    fs = staged.sample_rate_hz
    duration = 200.0
    # warmup 120 s; block1 (beta_up) starts at 120 s. Drive beta up well inside block1.
    scn = Scenario(label="beta-in-block1", duration_s=duration, sample_rate_hz=fs,
                   segments=(_beta_tone(125.0, 160.0),), controls={},
                   coverage_tags=frozenset())
    tl = predict(scn, staged)
    a, b = int(127 * fs), int(160 * fs)
    assert any(a <= s <= b for s in tl.should_fire_event_samples)


def test_beta_signal_during_block2_does_not_fire(staged):
    fs = staged.sample_rate_hz
    duration = 520.0
    # block2 (alpha_up) is active ~ after warmup(120)+block1(300)+rest(open synth 8) ≈ 428 s.
    # A beta tone there must NOT fire: alpha_reward is the selected bundle.
    scn = Scenario(label="beta-in-block2", duration_s=duration, sample_rate_hz=fs,
                   segments=(_beta_tone(440.0, 470.0),), controls={},
                   coverage_tags=frozenset())
    tl = predict(scn, staged)
    a, b = int(440 * fs), int(470 * fs)
    assert not any(a <= s <= b for s in tl.should_fire_event_samples)


def test_beta_signal_during_muted_warmup_is_not_asserted(staged):
    fs = staged.sample_rate_hz
    duration = 130.0
    scn = Scenario(label="beta-in-warmup", duration_s=duration, sample_rate_hz=fs,
                   segments=(_beta_tone(10.0, 100.0),), controls={},
                   coverage_tags=frozenset())
    tl = predict(scn, staged)
    # warmup is muted -> no crisp SHOULD-FIRE in [0,120s)
    assert not any(s < int(120 * fs) for s in tl.should_fire_event_samples)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_oracle_staged.py -q`
Expected: failures — current `predict` uses the single `surface.reward_condition`/`dwell_ms` and the v1 muting model, so block2 selection and per-phase muting are wrong (and `predict` may error on staged surfaces that no longer expose `reward_condition`).

- [ ] **Step 3: Implement** — edit `src/refrain/fuzz/oracle.py`:

(a) Import the resolver: `from .phases import resolve_phase_timeline`.

(b) In `predict`, replace the single-condition path with per-sample active-bundle selection. After computing per-derive envelope timelines and per-(signal,threshold) leaf truths (unchanged), resolve the phase timeline and build `truth_per_sample` by, at each sample, looking up the active block → its reward bundle → that bundle's condition tree, and evaluating it from the precomputed leaf truths:

```python
    tl = resolve_phase_timeline(surface, scenario, n_samples)

    # Per-sample condition truth using the ACTIVE block's reward bundle.
    bundle_truth: dict[str, list[bool | None]] = {
        name: _walk_condition(rb.condition, leaf_truth, n_samples)
        for name, rb in surface.reward_bundles.items()
    }
    truth_per_sample = [None] * n_samples
    for i in range(n_samples):
        blk = tl.active_block[i]
        if blk is None:
            truth_per_sample[i] = False        # no active reward → SHOULD-NOT-FIRE
            continue
        bundle = surface.blocks[blk].reward
        truth_per_sample[i] = bundle_truth[bundle][i]
```

(c) Dwell: the dwell samples differ per bundle. For v1 there was one `surface.dwell_ms`; now use the active block's bundle dwell. Simplest correct approach for A: since all bundles in the target share 500 ms dwell and apply_dwell takes a single `dwell_samples`, compute `dwell_samples` from the *active* bundle. Because dwell can vary per block, generalise `apply_dwell` to accept a per-sample dwell OR run it per contiguous active-block run. Implement the per-run approach in a small wrapper `_apply_dwell_staged(truth_per_sample, tl, surface, fs, collar_s, muted_mask)` that splits the timeline into maximal runs of constant active block, runs the existing `apply_dwell` on each run with that block's `dwell_samples`, and concatenates the resulting `should_fire_event_samples` (offset by the run start) and dont-care intervals. Reset the dwell streak at each run boundary (a block boundary is a fresh activation).

```python
def _bundle_dwell_samples(surface, block_name, fs):
    rb = surface.reward_bundles[surface.blocks[block_name].reward]
    return int(round(rb.dwell_ms / 1000.0 * fs))
```

(d) Muted mask now comes from `tl.muted` (not the v1 `_muted_mask`). Pass `muted_mask=tl.muted` into the dwell/collar machinery.

(e) No warm-boundary collar: only add the settle collar around genuine condition transitions and at session start, NOT at phase-index changes. (The existing collar is keyed on condition transitions already; just ensure block-boundary phase changes do not themselves inject a collar.)

(f) Keep `_add_pre_fill_dont_care` for percentile thresholds (none in the staged target, but harmless).

Keep the non-staged degenerate path working: for `smr_cz`, `surface.reward_bundles == {"__default__": ...}`, `blocks == {"__default__": ...}`, every sample's active block is `__default__`, dwell is 250 ms — identical to v1 behaviour. The existing `tests/fuzz/test_oracle_scenario.py` must still pass.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_oracle_staged.py tests/fuzz/test_oracle_scenario.py tests/fuzz/test_oracle_logic.py -q` — staged predictions pass AND the v1 oracle tests still pass. `.venv/bin/ruff check src/refrain/fuzz/oracle.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/oracle.py tests/fuzz/test_oracle_staged.py
git commit -m "feat(fuzz): oracle selects active-block reward bundle + per-phase muting

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A5: `drive.py` — step-wise evaluator driver (nominal marching)

**Files:**
- Create: `src/refrain/fuzz/drive.py`
- Test: `tests/fuzz/test_drive.py`

Runs a scenario through the REAL evaluator via `Evaluator.live(...).step_chunk(...)`, marching phases at nominal durations and collecting channel-tagged discrete events as `ActualEvent`s. Increment A does NOT yet issue host actions (those are tier-2 / Increment B), but it MUST advance `open` phases (else the run hangs) — it does so by calling `advance_phase()` when it detects an `open` phase has run its synthetic dwell. This mirrors `phases.py`'s nominal policy so oracle and driver agree.

- [ ] **Step 1: Write the failing test** — create `tests/fuzz/test_drive.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Step-wise driver: runs a scenario through the real evaluator, collects events."""
from __future__ import annotations

import pytest

from refrain.fuzz.check import ActualEvent
from refrain.fuzz.drive import drive_scenario
from refrain.fuzz.scenario import BandSegment, Scenario, Tone
from refrain.fuzz.surface import build_surface
from tests.fuzz._staged import resolved_staged_ir


@pytest.fixture(scope="module")
def staged_ir():
    return resolved_staged_ir()


def test_driver_completes_open_phase_without_hanging(staged_ir):
    surface = build_surface(staged_ir)
    fs = surface.sample_rate_hz
    # Long enough to traverse warmup+block1+open-rest+into block2 — must terminate.
    scn = Scenario(label="quiet", duration_s=460.0, sample_rate_hz=fs,
                   segments=(), controls={}, coverage_tags=frozenset())
    events = drive_scenario(staged_ir, scn, surface, chunk_size=64)
    assert isinstance(events, list)
    assert all(isinstance(e, ActualEvent) for e in events)


def test_driver_emits_beta_chime_during_block1(staged_ir):
    surface = build_surface(staged_ir)
    fs = surface.sample_rate_hz
    scn = Scenario(label="beta", duration_s=200.0, sample_rate_hz=fs,
                   segments=(BandSegment(band=(15.0, 20.0), channel="Cz",
                                         start_s=125.0, end_s=160.0,
                                         content=Tone(amplitude_uv=25.0)),),
                   controls={}, coverage_tags=frozenset())
    events = drive_scenario(staged_ir, scn, surface, chunk_size=64)
    chimes = [e for e in events if e.kind == "event" and e.channel == "audio_chime"]
    # at least one chime lands inside the beta tone window within block1
    assert any(int(126 * fs) <= e.sample <= int(161 * fs) for e in chimes)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_drive.py -q`
Expected: `ModuleNotFoundError: No module named 'refrain.fuzz.drive'`.

- [ ] **Step 3: Implement** — create `src/refrain/fuzz/drive.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Drive a Scenario through the real evaluator, collecting channel-tagged events.

Uses the push-mode Evaluator (`Evaluator.live(...).step_chunk(...)`) so host
actions can be interleaved between chunks (Increment B). Increment A marches at
nominal durations and only issues advance_phase() to end `open` phases (which never
auto-advance), mirroring phases.py so the oracle and driver agree on timing.
"""
from __future__ import annotations

import numpy as np

from ..eval_ import Evaluator
from .check import ActualEvent
from .phases import _OPEN_SYNTHETIC_DWELL_S
from .synthetic import render_scenario


def _channels_for(ir) -> tuple[str, ...]:
    # The protocol's required channels (active + references), matching how
    # `refrain run --synthetic` builds them. Reuse the surface's channels.
    return tuple(ir.requires.channels)


def drive_scenario(ir, scenario, surface, *, chunk_size: int = 64) -> list[ActualEvent]:
    fs = surface.sample_rate_hz
    channels = _channels_for(ir)
    gen = render_scenario(scenario, channels=channels)
    n_samples = int(round(scenario.duration_s * fs))

    ev = Evaluator.live(ir, sample_rate_hz=float(fs), channel_names=channels,
                        backend="python")
    ev.start()

    events: list[ActualEvent] = []
    pushed = 0
    open_phase_elapsed = 0  # samples spent in the current open phase
    while pushed < n_samples:
        n = min(chunk_size, n_samples - pushed)
        chunk = gen.next_chunk(n)                      # (n, n_channels) float64
        for e in ev.step_chunk(np.asarray(chunk, dtype=np.float64)):
            events.append(ActualEvent(sample=int(round(e.timestamp_s * fs)),
                                      kind=e.kind, channel=e.channel))
        pushed += n
        # End an `open` phase after the synthetic dwell so the run never hangs.
        ph = ev.current_phase()
        if ph.get("mode") == "open":
            open_phase_elapsed += n
            if open_phase_elapsed >= int(round(_OPEN_SYNTHETIC_DWELL_S * fs)):
                ev.advance_phase()
                open_phase_elapsed = 0
        else:
            open_phase_elapsed = 0
    ev.stop()
    return events


__all__ = ["drive_scenario"]
```

> **Note on channel resolution:** `render_scenario` needs the same channel tuple the evaluator expects. `ir.requires.channels` is the protocol's declared channel list (`("Cz","A1","A2")` for the staged target). If `cli.py` already has a `_channels_for_synthetic(ir)` helper (it does), `drive.py` may import and use that instead — pick one and keep it DRY.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_drive.py -q` — green (the second test may take a few seconds; it runs ~200 s of synthetic EEG). `.venv/bin/ruff check src/refrain/fuzz/drive.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/drive.py tests/fuzz/test_drive.py
git commit -m "feat(fuzz): step-wise evaluator driver (nominal marching, open-phase advance)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A6: Checker — channel-aware event attribution

**Files:**
- Modify: `src/refrain/fuzz/check.py`
- Test: `tests/fuzz/test_check.py` (append)

`ActualEvent` already carries `channel`. Add an optional `event_channels` filter to `check_scenario` so only events on the surface's event channels are classified (analog `value` events are ignored), and a non-event-channel chime is reported as spurious. This keeps v1 calls working (default `None` = accept all event-kind).

- [ ] **Step 1: Write the failing test** — append to `tests/fuzz/test_check.py`:

```python
def test_check_filters_to_event_channels():
    fs = 256
    expected = ExpectedTimeline(should_fire_event_samples=[500])
    actual = [
        ActualEvent(sample=510, kind="event", channel="audio_chime"),
        ActualEvent(sample=510, kind="value", channel="audio_gain"),   # analog, ignored
    ]
    res = check_scenario(
        scenario_label="t", expected=expected, actual=actual,
        fs=fs, collar_samples=64, coverage_tags=frozenset({"x"}),
        total_samples=1024, event_channels=("audio_chime",),
    )
    assert res.verdict is Verdict.PASS
    assert res.n_events == 1            # only the chime counts


def test_check_event_on_unknown_channel_is_spurious():
    fs = 256
    expected = ExpectedTimeline(should_fire_event_samples=[])
    actual = [ActualEvent(sample=500, kind="event", channel="audio_chime")]
    res = check_scenario(
        scenario_label="t", expected=expected, actual=actual,
        fs=fs, collar_samples=64, coverage_tags=frozenset({"dwell:missed"}),
        total_samples=1024, event_channels=("audio_chime",),
    )
    assert res.verdict is Verdict.SPURIOUS
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_check.py -q`
Expected: `TypeError: check_scenario() got an unexpected keyword argument 'event_channels'`.

- [ ] **Step 3: Implement** — edit `check_scenario` in `src/refrain/fuzz/check.py`:

Add keyword-only param `event_channels: tuple[str, ...] | None = None`. At the top of the event-classification loop, filter the actuals:

```python
    if event_channels is not None:
        actual = [e for e in actual if e.kind == "event" and e.channel in event_channels]
    else:
        actual = [e for e in actual if e.kind == "event"]
```

(The v1 callers passed only event-kind already, so default behaviour is unchanged; `n_events = len(actual)` now counts only the filtered events.) Document the param in the docstring.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_check.py -q` — all green (new + existing). `.venv/bin/ruff check src/refrain/fuzz/check.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/check.py tests/fuzz/test_check.py
git commit -m "feat(fuzz): channel-aware event filtering in check_scenario

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A7: Generator — staged scenario families (per-block, isolation, muting, transition)

**Files:**
- Modify: `src/refrain/fuzz/generate.py`
- Test: `tests/fuzz/test_generate_staged.py`

Add `generate_staged_scenarios(surface)` yielding the staged families. It is only invoked when the surface is staged (`len(surface.blocks) > 1` or any phase has a non-None block other than the implicit one); the non-staged generators are untouched.

- [ ] **Step 1: Write the failing test** — create `tests/fuzz/test_generate_staged.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Directed staged scenario generation."""
from __future__ import annotations

import pytest

from refrain.fuzz.generate import generate_staged_scenarios
from refrain.fuzz.surface import build_surface
from tests.fuzz._staged import resolved_staged_ir


@pytest.fixture(scope="module")
def scenarios():
    surface = build_surface(resolved_staged_ir())
    return list(generate_staged_scenarios(surface))


def test_has_per_block_reward_pivots(scenarios):
    tags = {t for s in scenarios for t in s.coverage_tags}
    for blk in ("beta_up", "alpha_up"):
        assert f"block:{blk}:reward:true" in tags
        assert f"block:{blk}:reward:false" in tags


def test_has_block_isolation_scenario(scenarios):
    tags = {t for s in scenarios for t in s.coverage_tags}
    # drive beta during alpha_up's phase -> must not fire (selection proof)
    assert "block_isolation:beta_up_signal_in_alpha_up" in tags


def test_has_muting_and_transition_scenarios(scenarios):
    tags = {t for s in scenarios for t in s.coverage_tags}
    assert any(t.startswith("muting:") for t in tags)
    assert any(t.startswith("transition:") for t in tags)


def test_staged_scenarios_have_no_phase_override(scenarios):
    # staged scenarios drive the real protocol phases; they must NOT override phases
    assert all(s.phase_override is None for s in scenarios)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_generate_staged.py -q`
Expected: `ImportError: cannot import name 'generate_staged_scenarios'`.

- [ ] **Step 3: Implement** — append to `src/refrain/fuzz/generate.py`. Use the phase timeline to place tones in the right phase window. Helper to find a block's active window:

```python
def _block_window_s(surface, block_name):
    """(start_s, end_s) of the first phase whose block == block_name, using nominal
    durations (open phases use the synthetic dwell)."""
    from .phases import _OPEN_SYNTHETIC_DWELL_S
    t = 0.0
    for p in surface.phases:
        dur = p.duration_s if (p.duration_s and p.duration_s > 0) else _OPEN_SYNTHETIC_DWELL_S
        if p.block == block_name:
            return (t, t + dur)
        t += dur
    raise ValueError(f"no phase for block {block_name!r}")


def _derive_for_block(surface, block_name):
    """The derive a block's reward condition keys on (first leaf's signal)."""
    rb = surface.reward_bundles[surface.blocks[block_name].reward]
    leaf = _first_leaf(rb.condition)
    return next(d for d in surface.derives if d.name == leaf.signal)


def _first_leaf(node):
    from .surface import ConditionLeaf
    if isinstance(node, ConditionLeaf):
        return node
    return _first_leaf(node.children[0])


def generate_staged_scenarios(surface):
    fs = surface.sample_rate_hz
    block_names = [b for b in surface.blocks if b != "__default__"]
    last_end = max(_block_window_s(surface, b)[1] for b in block_names)
    duration = last_end + 30.0

    # Per-block reward pivots: drive each block's derive up/down inside its window.
    for blk in block_names:
        derive = _derive_for_block(surface, blk)
        w0, w1 = _block_window_s(surface, blk)
        spike0, spike1 = w0 + 3.0, min(w1 - 1.0, w0 + 30.0)
        for side in ("true", "false"):
            amp = 25.0 if side == "true" else 0.0
            seg = ((BandSegment(band=derive.band, channel=derive.channel,
                                start_s=spike0, end_s=spike1,
                                content=Tone(amplitude_uv=amp)),) if amp > 0 else ())
            yield Scenario(label=f"block_{blk}_reward_{side}", duration_s=duration,
                           sample_rate_hz=fs, segments=seg, controls={},
                           coverage_tags=frozenset({f"block:{blk}:reward:{side}"}))

    # Block isolation: drive block A's signal during block B's window -> no fire.
    if len(block_names) >= 2:
        a, b = block_names[0], block_names[1]
        derive_a = _derive_for_block(surface, a)
        w0, w1 = _block_window_s(surface, b)
        yield Scenario(
            label=f"isolation_{a}_signal_in_{b}", duration_s=duration, sample_rate_hz=fs,
            segments=(BandSegment(band=derive_a.band, channel=derive_a.channel,
                                  start_s=w0 + 3.0, end_s=min(w1 - 1.0, w0 + 30.0),
                                  content=Tone(amplitude_uv=25.0)),),
            controls={}, coverage_tags=frozenset({f"block_isolation:{a}_signal_in_{b}"}))

    # Muting: drive a block's signal during a muted rest phase -> no emission.
    muted_rest = next((p for p in surface.phases if p.output_muted and p.block is None
                       and p.name != surface.phases[0].name), None)
    if muted_rest is not None:
        rw0, rw1 = _phase_window_s(surface, muted_rest.name)
        derive = _derive_for_block(surface, block_names[0])
        yield Scenario(
            label=f"muting_{muted_rest.name}", duration_s=duration, sample_rate_hz=fs,
            segments=(BandSegment(band=derive.band, channel=derive.channel,
                                  start_s=rw0 + 1.0, end_s=rw1 - 0.5,
                                  content=Tone(amplitude_uv=25.0)),),
            controls={}, coverage_tags=frozenset({f"muting:{muted_rest.name}"}))

    # Transition: hold a block's signal across its whole window -> fires in-block.
    blk = block_names[0]
    derive = _derive_for_block(surface, blk)
    w0, w1 = _block_window_s(surface, blk)
    yield Scenario(
        label=f"transition_into_{blk}", duration_s=duration, sample_rate_hz=fs,
        segments=(BandSegment(band=derive.band, channel=derive.channel,
                              start_s=w0 - 5.0 if w0 > 5.0 else 0.0, end_s=w1 - 1.0,
                              content=Tone(amplitude_uv=25.0)),),
        controls={}, coverage_tags=frozenset({f"transition:into_{blk}"}))
```

Also add this `_phase_window_s` helper (matches by phase name instead of block):

```python
def _phase_window_s(surface, phase_name):
    """(start_s, end_s) of the phase named phase_name, using nominal durations."""
    from .phases import _OPEN_SYNTHETIC_DWELL_S
    t = 0.0
    for p in surface.phases:
        dur = p.duration_s if (p.duration_s and p.duration_s > 0) else _OPEN_SYNTHETIC_DWELL_S
        if p.name == phase_name:
            return (t, t + dur)
        t += dur
    raise ValueError(f"no phase named {phase_name!r}")
```

Add `generate_staged_scenarios` to `__all__`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_generate_staged.py tests/fuzz/test_generate.py -q` — staged + non-staged generators pass. `.venv/bin/ruff check src/refrain/fuzz/generate.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/generate.py tests/fuzz/test_generate_staged.py
git commit -m "feat(fuzz): staged scenario families (per-block, isolation, muting, transition)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A8: Report — per-block behavioural summary

**Files:**
- Modify: `src/refrain/fuzz/report.py`
- Test: `tests/fuzz/test_report.py` (append)

Add a per-block line to "What your protocol does" when staged scenarios are present (coverage tags starting `block:`). Pass an optional `blocks` argument (tuple of block names) to `render_report`; default `()` keeps v1 output unchanged.

- [ ] **Step 1: Write the failing test** — append to `tests/fuzz/test_report.py`:

```python
def test_report_includes_per_block_summary():
    results = [
        _r("block_beta_up_reward_true", Verdict.PASS, {"block:beta_up:reward:true"}, n_events=2),
        _r("block_alpha_up_reward_true", Verdict.PASS, {"block:alpha_up:reward:true"}, n_events=2),
    ]
    text = render_report(
        protocol_name="staged_beta_alpha", results=results,
        metamorphic_violations=[],
        all_coverage_tags={"block:beta_up:reward:true", "block:alpha_up:reward:true"},
        blocks=("beta_up", "alpha_up"),
    )
    assert "beta_up" in text and "alpha_up" in text
    assert "Blocks" in text or "block" in text.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_report.py -q`
Expected: `TypeError: render_report() got an unexpected keyword argument 'blocks'`.

- [ ] **Step 3: Implement** — edit `render_report` in `src/refrain/fuzz/report.py`:

Add keyword-only `blocks: tuple[str, ...] = ()`. In Section A (after the behavioural summary), when `blocks`, append a per-block block:

```python
    if blocks:
        out.append("\n  Blocks:\n")
        for blk in blocks:
            fired = [r for r in rs if r.n_events > 0
                     and any(t == f"block:{blk}:reward:true" for t in r.coverage_tags)]
            verb = "rewards its condition" if fired else "no reward observed yet"
            out.append(f"    • {blk}: {verb}\n")
```

(`rs = list(results)` already exists at the top of `render_report`.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_report.py -q` — green. `.venv/bin/ruff check src/refrain/fuzz/report.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/report.py tests/fuzz/test_report.py
git commit -m "feat(fuzz): per-block behavioural summary in report

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A9: CLI — wire the staged path through the driver

**Files:**
- Modify: `src/refrain/cli.py`
- Test: `tests/fuzz/test_cli_fuzz_staged.py`

Route staged protocols through `drive.drive_scenario` + the staged generator, and pass `event_channels` / `blocks` into the checker/report. The v1 "unsupported staged" `ValueError` no longer fires because `build_surface` now handles staged IRs. Keep the non-staged path (single-condition protocols) exactly as today.

- [ ] **Step 1: Write the failing test** — create `tests/fuzz/test_cli_fuzz_staged.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""`refrain fuzz` on a staged protocol renders a report (no crash)."""
from __future__ import annotations

from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGED = str(REPO_ROOT / "examples" / "staged_beta_alpha.refrain")


def test_refrain_fuzz_staged_emits_report(capsys):
    rc = main(["fuzz", STAGED, "--max-scenarios", "3"])
    combined = "".join(capsys.readouterr())
    assert "What your protocol does" in combined
    assert "Engine check" in combined
    assert "GENERATOR BUG" not in combined
    assert rc in (0, 1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_cli_fuzz_staged.py -q`
Expected: the current `_cmd_fuzz` raises the staged `ValueError` from `build_surface` (now fixed) OR fails because it uses the v1 single-condition path / `eval_protocol` driver that can't traverse open phases. The test fails until the staged path is wired.

- [ ] **Step 3: Implement** — edit `src/refrain/cli.py`:

(a) In `_cmd_fuzz`, after `surface = build_surface(ir)`, detect staged:

```python
    staged = len(surface.blocks) > 1 or any(
        p.block is not None and p.block != "__default__" for p in surface.phases
    )
```

(b) Build the corpus accordingly:

```python
    if staged:
        from .fuzz.generate import generate_staged_scenarios
        scenarios = list(generate_staged_scenarios(surface))
    else:
        scenarios = _fuzz_corpus(surface)        # existing non-staged corpus
    if args.max_scenarios:
        scenarios = scenarios[: args.max_scenarios]
```

(c) In `_fuzz_one_scenario`, branch on staged: use the step-wise driver and pass `event_channels` to the checker:

```python
    if staged:
        from .fuzz.drive import drive_scenario
        actual = drive_scenario(ir, scenario, surface, chunk_size=chunk_size)
        expected = predict(scenario, surface)
        return check_scenario(
            scenario_label=scenario.label, expected=expected, actual=actual,
            fs=surface.sample_rate_hz, collar_samples=collar_samples,
            coverage_tags=scenario.coverage_tags,
            total_samples=int(round(scenario.duration_s * surface.sample_rate_hz)),
            event_channels=surface.event_channels,
        )
    # ... existing non-staged body unchanged ...
```

Thread `staged` into `_fuzz_one_scenario`'s signature (and the call site). For staged scenarios there is no phase override (the driver uses the protocol's real phases).

(d) Pass `blocks` to the report for staged runs:

```python
    text = render_report(
        protocol_name=ir.name, results=results,
        metamorphic_violations=metamorphic_violations,
        all_coverage_tags=all_coverage_tags,
        blocks=tuple(b for b in surface.blocks if b != "__default__") if staged else (),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_cli_fuzz_staged.py tests/fuzz/test_cli_fuzz.py -q` — staged + non-staged CLI tests pass. (The staged test runs a few hundred seconds of synthetic EEG across 3 scenarios; it may take ~1–2 min.) `.venv/bin/ruff check src/refrain/fuzz/`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/cli.py tests/fuzz/test_cli_fuzz_staged.py
git commit -m "feat(cli): route staged protocols through the step-wise driver

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task A10: Increment A acceptance — staged core engine check

**Files:**
- Create: `tests/fuzz/test_staged_end_to_end.py`

- [ ] **Step 1: Write the test** — create `tests/fuzz/test_staged_end_to_end.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Staged acceptance: staged_beta_alpha passes the core engine check."""
from __future__ import annotations

from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGED = str(REPO_ROOT / "examples" / "staged_beta_alpha.refrain")


def test_staged_core_engine_check(capsys):
    rc = main(["fuzz", STAGED, "--max-scenarios", "6"])
    out = "".join(capsys.readouterr())
    assert "Engine check" in out
    assert "GENERATOR BUG" not in out
    assert rc in (0, 1)
```

- [ ] **Step 2: Run + full fuzz regression**

Run: `.venv/bin/python -m pytest tests/fuzz/test_staged_end_to_end.py -q` then `.venv/bin/python -m pytest tests/fuzz/ -q`.
Expected: staged acceptance passes; the whole fuzz suite (v1 + staged) is green. If a directed staged scenario is MISSED/SPURIOUS, debug the generator/oracle/driver agreement (timing of phase windows, the open-phase synthetic dwell) — do NOT weaken assertions.

- [ ] **Step 3: Commit**

```bash
git add tests/fuzz/test_staged_end_to_end.py
git commit -m "test(fuzz): staged core engine-check acceptance (Increment A)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# Increment B — Host control API + adaptivity

## Task B1: `phases.py` honours host actions (advance / hold / freeze)

**Files:**
- Modify: `src/refrain/fuzz/phases.py`
- Test: `tests/fuzz/test_phases.py` (append)

Extend `resolve_phase_timeline` so the per-sample timeline reflects the scenario's `actions`: an `AdvancePhase(at_s)` ends the active phase at that sample; `Hold(at_s, held=True)` on a `timed_with_floor` phase suppresses its nominal auto-advance until a later `AdvancePhase`; `SetClockFrozen` intervals pause `phase_elapsed` accumulation (countdown freezes).

- [ ] **Step 1: Write the failing test** — append to `tests/fuzz/test_phases.py`:

```python
from refrain.fuzz.scenario import AdvancePhase, Hold, SetClockFrozen


def test_advance_phase_ends_phase_early(staged):
    fs = staged.sample_rate_hz
    duration = 200.0
    # Advance out of warmup at 30 s (instead of nominal 120 s) -> block1 active by 40 s.
    scn = _scn(duration, fs, actions=(AdvancePhase(at_s=30.0),))
    tl = resolve_phase_timeline(staged, scn, n_samples=int(duration * fs))
    assert tl.active_block[int(40 * fs)] == "beta_up"


def test_hold_extends_timed_with_floor(staged):
    fs = staged.sample_rate_hz
    duration = 700.0
    # block1 floor is 300 s (nominal). Hold during block1, advance only at 600 s.
    scn = _scn(duration, fs, actions=(Hold(at_s=130.0), AdvancePhase(at_s=600.0)))
    tl = resolve_phase_timeline(staged, scn, n_samples=int(duration * fs))
    # at 500 s (past nominal block1 end) the held block1 is still active
    assert tl.active_block[int(500 * fs)] == "beta_up"


def test_clock_freeze_delays_auto_advance(staged):
    fs = staged.sample_rate_hz
    duration = 300.0
    # Freeze the clock during warmup for 60 s -> warmup ends ~60 s later than nominal.
    scn = _scn(duration, fs, actions=(SetClockFrozen(at_s=20.0, frozen=True),
                                      SetClockFrozen(at_s=80.0, frozen=False)))
    tl = resolve_phase_timeline(staged, scn, n_samples=int(duration * fs))
    # at 150 s we are still in warmup (nominal 120 + 60 frozen = 180 s)
    assert tl.active_block[int(150 * fs)] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_phases.py -q`
Expected: the three new tests fail (actions are ignored by the A3 nominal marcher).

- [ ] **Step 3: Implement** — rewrite the marching loop in `resolve_phase_timeline` to be sample-stepped and action-aware. Replace the phase-block loop with a per-sample state machine:

```python
def resolve_phase_timeline(surface, scenario, n_samples: int) -> PhaseTimeline:
    fs = surface.sample_rate_hz
    phases = surface.phases
    actions = sorted(scenario.actions, key=lambda a: a.at_s)

    phase_index = [0] * n_samples
    active_block = [None] * n_samples
    muted = [False] * n_samples
    frozen = [False] * n_samples
    phase_starts: list[int] = [0]

    pi = 0
    phase_elapsed = 0            # samples accumulated in current phase (clock)
    clock_frozen = False
    held = False
    open_elapsed = 0
    seen_first_muted_done = False
    ai = 0                       # next action index

    def _dur_samples(ph):
        if ph.mode == "open" or not ph.duration_s or ph.duration_s <= 0:
            return None          # no nominal auto-advance
        return int(round(ph.duration_s * fs))

    for i in range(n_samples):
        # apply any host actions scheduled at/just before this sample
        while ai < len(actions) and actions[ai].at_s * fs <= i:
            act = actions[ai]; ai += 1
            from .scenario import AdvancePhase, Hold, SetClockFrozen
            if isinstance(act, AdvancePhase):
                pi = min(pi + 1, len(phases))
                phase_elapsed = 0; held = False; open_elapsed = 0
                if pi < len(phases):
                    phase_starts.append(i)
            elif isinstance(act, Hold):
                held = act.held
            elif isinstance(act, SetClockFrozen):
                clock_frozen = act.frozen
        if pi >= len(phases):
            pi_eff = len(phases) - 1
            ph = phases[pi_eff]
            phase_index[i] = pi_eff; active_block[i] = ph.block
            muted[i] = ph.output_muted; frozen[i] = False
            continue
        ph = phases[pi]
        phase_index[i] = pi
        active_block[i] = ph.block
        muted[i] = ph.output_muted
        frozen[i] = ph.output_muted and seen_first_muted_done
        # clock + nominal auto-advance
        if not clock_frozen:
            phase_elapsed += 1
        dur = _dur_samples(ph)
        auto = (ph.mode == "timed") or (ph.mode == "timed_with_floor" and not held)
        if ph.mode == "open":
            open_elapsed += 1
        advance_now = False
        if auto and dur is not None and not clock_frozen and phase_elapsed >= dur:
            advance_now = True
        elif ph.mode == "open" and open_elapsed >= int(round(_OPEN_SYNTHETIC_DWELL_S * fs)):
            advance_now = True       # synthetic dwell ends an un-advanced open phase
        if advance_now:
            if ph.output_muted:
                seen_first_muted_done = True
            pi += 1; phase_elapsed = 0; held = False; open_elapsed = 0
            if pi < len(phases):
                phase_starts.append(i + 1)
    return PhaseTimeline(phase_index, active_block, muted, frozen, phase_starts)
```

This subsumes the A3 nominal marcher (with `actions=()` it behaves identically). Keep `_OPEN_SYNTHETIC_DWELL_S` and the `PhaseTimeline` dataclass.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_phases.py -q` — all (A3 + B1) green. `.venv/bin/ruff check src/refrain/fuzz/phases.py`.

> The oracle already consumes `resolve_phase_timeline`, so it now predicts host-action-shifted timing automatically — no oracle change required for B1.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/phases.py tests/fuzz/test_phases.py
git commit -m "feat(fuzz): phase timeline honours advance/hold/clock-freeze host actions

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B2: Driver issues host actions during stepping

**Files:**
- Modify: `src/refrain/fuzz/drive.py`
- Test: `tests/fuzz/test_drive.py` (append)

The driver must call `advance_phase()` / `hold()` / `set_clock_frozen()` at the scenario's scheduled samples so the real evaluator's phase cursor matches the oracle's predicted timeline.

- [ ] **Step 1: Write the failing test** — append to `tests/fuzz/test_drive.py`:

```python
from refrain.fuzz.scenario import AdvancePhase, BandSegment, Tone


def test_early_advance_moves_beta_window_earlier(staged_ir):
    surface = build_surface(staged_ir)
    fs = surface.sample_rate_hz
    # Advance out of warmup at 20 s; drive beta at 25–45 s (now inside block1).
    scn = Scenario(
        label="early-beta", duration_s=120.0, sample_rate_hz=fs,
        segments=(BandSegment(band=(15.0, 20.0), channel="Cz", start_s=25.0, end_s=45.0,
                              content=Tone(amplitude_uv=25.0)),),
        controls={}, coverage_tags=frozenset(),
        actions=(AdvancePhase(at_s=20.0),),
    )
    events = drive_scenario(staged_ir, scn, surface, chunk_size=64)
    chimes = [e for e in events if e.kind == "event" and e.channel == "audio_chime"]
    # without the early advance these would be muted in warmup; with it they fire
    assert any(int(26 * fs) <= e.sample <= int(46 * fs) for e in chimes)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_drive.py::test_early_advance_moves_beta_window_earlier -q`
Expected: fails — A5's driver ignores `scenario.actions` (the beta window stays muted in warmup).

- [ ] **Step 3: Implement** — edit `drive_scenario` to issue scheduled actions at the right sample boundary. Track `pushed` (samples processed) and, before stepping each chunk, fire any actions whose `at_s * fs` falls within the samples already processed. Replace the loop body:

```python
    from .scenario import AdvancePhase, Hold, SetClockFrozen
    actions = sorted(scenario.actions, key=lambda a: a.at_s)
    ai = 0
    while pushed < n_samples:
        # fire host actions scheduled up to the current sample boundary
        while ai < len(actions) and actions[ai].at_s * fs <= pushed:
            act = actions[ai]; ai += 1
            if isinstance(act, AdvancePhase):
                ev.advance_phase()
            elif isinstance(act, Hold):
                ev.hold(act.held)
            elif isinstance(act, SetClockFrozen):
                ev.set_clock_frozen(act.frozen)
        n = min(chunk_size, n_samples - pushed)
        chunk = gen.next_chunk(n)
        for e in ev.step_chunk(np.asarray(chunk, dtype=np.float64)):
            events.append(ActualEvent(sample=int(round(e.timestamp_s * fs)),
                                      kind=e.kind, channel=e.channel))
        pushed += n
        ph = ev.current_phase()
        if ph.get("mode") == "open":
            open_phase_elapsed += n
            if open_phase_elapsed >= int(round(_OPEN_SYNTHETIC_DWELL_S * fs)):
                ev.advance_phase(); open_phase_elapsed = 0
        else:
            open_phase_elapsed = 0
```

(Keep the open-phase auto-advance for `open` phases the scenario does NOT explicitly advance.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_drive.py -q` — green. `.venv/bin/ruff check src/refrain/fuzz/drive.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/drive.py tests/fuzz/test_drive.py
git commit -m "feat(fuzz): driver issues advance/hold/freeze at scheduled samples

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B3: Generator — host-action + warm-signal families

**Files:**
- Modify: `src/refrain/fuzz/generate.py`
- Test: `tests/fuzz/test_generate_staged.py` (append)

Add to `generate_staged_scenarios`: a `hold`-extends-block scenario (signal held past the nominal floor with a `Hold` + late `AdvancePhase` → fires past nominal end), an early-`advance_phase` scenario (block ended early → reward stops), and a warm-signal-at-boundary scenario (signal already TRUE when the block goes active → fires promptly).

- [ ] **Step 1: Write the failing test** — append to `tests/fuzz/test_generate_staged.py`:

```python
def test_has_host_action_and_warm_signal_scenarios():
    from refrain.fuzz.surface import build_surface
    from tests.fuzz._staged import resolved_staged_ir
    scns = list(generate_staged_scenarios(build_surface(resolved_staged_ir())))
    tags = {t for s in scns for t in s.coverage_tags}
    assert any(t.startswith("host:hold_extends:") for t in tags)
    assert any(t.startswith("host:early_advance:") for t in tags)
    assert any(t.startswith("warm_signal:") for t in tags)
    # the host-action scenarios actually carry actions
    assert any(s.actions for s in scns)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_generate_staged.py::test_has_host_action_and_warm_signal_scenarios -q`
Expected: fails — no `host:`/`warm_signal:` tags yet.

- [ ] **Step 3: Implement** — append these yields inside `generate_staged_scenarios` (after the transition scenario), for the first `timed_with_floor` block:

```python
    tw_block = next((p.block for p in surface.phases
                     if p.mode == "timed_with_floor" and p.block), None)
    if tw_block is not None:
        from .scenario import AdvancePhase, Hold
        derive = _derive_for_block(surface, tw_block)
        w0, w1 = _block_window_s(surface, tw_block)
        # Hold extends the block: signal held past nominal end; advance well after.
        yield Scenario(
            label=f"hold_extends_{tw_block}", duration_s=duration, sample_rate_hz=fs,
            segments=(BandSegment(band=derive.band, channel=derive.channel,
                                  start_s=w0 + 3.0, end_s=w1 + 60.0,
                                  content=Tone(amplitude_uv=25.0)),),
            controls={},
            coverage_tags=frozenset({f"host:hold_extends:{tw_block}"}),
            actions=(Hold(at_s=w0 + 2.0), AdvancePhase(at_s=w1 + 90.0)))
        # Early advance: end the block at its midpoint; signal after that must not fire.
        mid = (w0 + w1) / 2.0
        yield Scenario(
            label=f"early_advance_{tw_block}", duration_s=duration, sample_rate_hz=fs,
            segments=(BandSegment(band=derive.band, channel=derive.channel,
                                  start_s=mid + 3.0, end_s=w1 - 1.0,
                                  content=Tone(amplitude_uv=25.0)),),
            controls={},
            coverage_tags=frozenset({f"host:early_advance:{tw_block}"}),
            actions=(AdvancePhase(at_s=mid),))
        # Warm signal: tone already on as the block goes active (spans the boundary).
        yield Scenario(
            label=f"warm_signal_{tw_block}", duration_s=duration, sample_rate_hz=fs,
            segments=(BandSegment(band=derive.band, channel=derive.channel,
                                  start_s=max(0.0, w0 - 10.0), end_s=w0 + 20.0,
                                  content=Tone(amplitude_uv=25.0)),),
            controls={},
            coverage_tags=frozenset({f"warm_signal:{tw_block}"}))
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_generate_staged.py -q` — green. `.venv/bin/ruff check src/refrain/fuzz/generate.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/generate.py tests/fuzz/test_generate_staged.py
git commit -m "feat(fuzz): host-action + warm-signal staged scenario families

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B4: Oracle — percentile-window freeze

**Files:**
- Modify: `src/refrain/fuzz/oracle.py`
- Test: `tests/fuzz/test_oracle_logic.py` (append)

The runtime freezes adaptive (percentile) windows during muted phases after the first. The oracle's `_ordinal_percentile_truth` must stop ingesting samples that fall in a frozen interval (the rank uses the window as it stood when freezing began). `phases.py` already exposes a per-sample `frozen_ingest` mask; thread it into the percentile leaf computation. (The `staged_beta_alpha` target uses absolute thresholds, so this is exercised by a focused unit test on the percentile function.)

- [ ] **Step 1: Write the failing test** — append to `tests/fuzz/test_oracle_logic.py`:

```python
def test_ordinal_percentile_respects_freeze_mask():
    from refrain.fuzz.oracle import _ordinal_percentile_truth

    class _Thr:
        percentile_target = 70.0
        percentile_window_ms = 1000.0
    fs = 256
    # env: low for 2 s (ingested), then a frozen 1 s where a spike appears.
    window_samples = fs  # 1 s
    n = 3 * fs
    env = [1.0] * (2 * fs) + [50.0] * fs
    frozen = [False] * (2 * fs) + [True] * fs
    out = _ordinal_percentile_truth(env, _Thr(), window_samples, frozen_mask=frozen)
    # In the frozen region the spike is NOT ingested into the window, so the rank
    # of the spike is computed against the (still-low) frozen window snapshot →
    # the spike still ranks high (True), but later frozen samples do not pollute
    # the window. The key assertion: frozen samples are not added to the window,
    # so a sample right after the spike (still frozen) ranks against pre-freeze data.
    assert out[2 * fs + 5] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_oracle_logic.py::test_ordinal_percentile_respects_freeze_mask -q`
Expected: `TypeError: _ordinal_percentile_truth() got an unexpected keyword argument 'frozen_mask'`.

- [ ] **Step 3: Implement** — edit `_ordinal_percentile_truth` in `src/refrain/fuzz/oracle.py` to accept `frozen_mask: list[bool] | None = None`. The v1 body keeps a `bisect`-maintained sorted trailing window; the only change is that **frozen samples are still ranked but never ingested** (the window is paused). The rank computation is unchanged; only the window-maintenance is gated:

```python
def _ordinal_percentile_truth(env, thr, window_samples, frozen_mask=None):
    import bisect
    target = thr.percentile_target
    out: list[bool | None] = [None] * len(env)
    window: list[float] = []          # sorted trailing window (v1 used the same)
    for i, x in enumerate(env):
        if len(window) >= window_samples:           # rank is meaningful
            below = bisect.bisect_left(window, x)
            rank = 100.0 * below / len(window)
            out[i] = True if rank > target else (False if rank < target else None)
        if frozen_mask is None or not frozen_mask[i]:
            bisect.insort(window, x)                 # ingest only when not frozen
            if len(window) > window_samples:
                window.pop(bisect.bisect_left(window, env[i - window_samples]))
    return out
```

(Match the exact rank thresholds/margins the v1 body already uses — keep its `target ± margin` logic; the only added line is the `frozen_mask` guard around ingestion. Do not change the rank math, just whether the sample enters the window.) Thread the mask from `predict`: for percentile thresholds call `_leaf_truth_timeline(..., frozen_mask=tl.frozen_ingest)`, where `tl = resolve_phase_timeline(...)` (already computed in `predict`). Absolute thresholds ignore the mask.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/fuzz/test_oracle_logic.py tests/fuzz/test_oracle_staged.py tests/fuzz/test_oracle_scenario.py -q` — green (freeze unit test + staged + v1 oracle). `.venv/bin/ruff check src/refrain/fuzz/oracle.py`.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/oracle.py tests/fuzz/test_oracle_logic.py
git commit -m "feat(fuzz): oracle freezes percentile windows during muted rests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task B5: Increment B acceptance + mutation test

**Files:**
- Modify: `tests/fuzz/test_staged_end_to_end.py` (append)

- [ ] **Step 1: Write the tests** — append to `tests/fuzz/test_staged_end_to_end.py`:

```python
def test_staged_full_corpus_runs(capsys):
    # Larger cap exercises host-action + warm-signal families end to end.
    rc = main(["fuzz", STAGED, "--max-scenarios", "12"])
    out = "".join(capsys.readouterr())
    assert "Engine check" in out
    assert "GENERATOR BUG" not in out
    assert "Blocks" in out                      # per-block summary rendered
    assert rc in (0, 1)


def test_mutation_swaps_block_reward_bundle(tmp_path, capsys):
    """Point block1 at the WRONG reward bundle (alpha instead of beta) and assert
    the fuzzer reacts (behavioural shift or engine FAIL)."""
    src = Path(STAGED).read_text()
    mutated = src.replace(
        'block "beta_up"  { threshold = "beta_t";  reward = "beta_reward";',
        'block "beta_up"  { threshold = "beta_t";  reward = "alpha_reward";',
    )
    assert mutated != src, "expected to replace block1's reward bundle"
    p = tmp_path / "staged_mutant.refrain"
    p.write_text(mutated)
    rc = main(["fuzz", str(p), "--max-scenarios", "8"])
    out = "".join(capsys.readouterr())
    assert "What your protocol does" in out
    assert "GENERATOR BUG" not in out
    # beta signal in block1 now drives alpha_reward (wrong band) → block1's
    # beta pivot no longer fires → engine FAIL or a visible behavioural shift.
    assert rc == 1 or "block:beta_up:reward:true" in out
```

> **Note:** verify the exact `block "beta_up"` line text against `examples/staged_beta_alpha.refrain` and adjust the `replace()` strings to match the real file (spacing/quoting), exactly as the v1 mutation test does. The replacement must change ONLY block1's reward bundle.

- [ ] **Step 2: Run + full regression**

Run: `.venv/bin/python -m pytest tests/fuzz/test_staged_end_to_end.py -q` then `.venv/bin/python -m pytest tests/fuzz/ -q` then `.venv/bin/python -m pytest -q` (whole repo).
Expected: staged acceptance + mutation pass; full fuzz suite green; whole repo green. The staged e2e is slow (minutes) — acceptable.

- [ ] **Step 3: Commit**

```bash
git add tests/fuzz/test_staged_end_to_end.py
git commit -m "test(fuzz): staged host-API acceptance + reward-bundle mutation (Increment B)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-review (verify before handoff)

After all tasks land:

1. **Spec coverage** — trace each spec section to a task:
   - Scenario host-action contract (§1) → A1 ✓
   - Surface blocks/bundles/phase-model + degenerate (§2) → A2 ✓
   - Oracle phase-aware prediction (§3): phase-schedule resolution → A3/B1; active-block selection + muting → A4; percentile freeze → B4; no warm-boundary collar → A4 ✓
   - Generator families (§4): per-block/isolation/muting/transition → A7; host-action/warm-signal → B3; percentile-freeze modelled in oracle (B4) ✓
   - Checker/report channel/block aware (§5) → A6/A8 ✓
   - Step-wise driver (§6) → A5/B2 ✓
   - Test target + mutation → A10/B5 ✓
2. **Run the full suite:** `.venv/bin/python -m pytest tests/fuzz/ -q` — every test passing; `.venv/bin/ruff check src/refrain/fuzz/` clean.
3. **Run on the staged target:** `.venv/bin/refrain fuzz examples/staged_beta_alpha.refrain --max-scenarios 12` — report renders both sections + the Blocks summary.
4. **Non-staged regression:** `.venv/bin/refrain fuzz bench/protocols/realistic_smr.refrain --max-scenarios 8` — still works (degenerate one-block path).

## Deferred to a later layer (post this plan)

- Fuzzing weighted-composite reward, `inhibit` masking, coherence, multi-site placement.
- Randomized host-action interleavings with shrinking; Rust-backend parity fuzzing.
- Generalising the smr-specific derive lookups in `generate.py` (the `TODO(v2)` notes) to arbitrary protocols.
