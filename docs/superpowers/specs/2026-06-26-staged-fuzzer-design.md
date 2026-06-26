# Staged / segmented protocol fuzzing — design

> Status: approved design (brainstorm complete), ready for an implementation plan.
> Builds on: the protocol fuzzer v1 (`src/refrain/fuzz/`, this branch) and the
> staged/segmented protocol runtime shipped in v0.7.0 (PRs #32/#33; design at
> `docs/superpowers/specs/2026-05-29-staged-protocols-design.md` on `main`).
> Scope: extend `refrain fuzz` to introspect, drive, predict, and check **staged**
> protocols — including the host control API (`advance_phase` / `hold` /
> `set_clock_frozen`).

## Goal

`refrain fuzz <staged>.refrain` should auto-synthesise scenarios for a staged
session, predict the expected per-phase / per-block behaviour analytically
(independent of the evaluator), drive the real evaluator step-by-step through its
phases (issuing host actions where required), assert match, and report — exactly
the value the v1 fuzzer delivers for single-condition protocols, generalised to:

- **Per-block reward correctness** — each block's reward bundle fires for *its own*
  condition while that block is active, on *its* output channel; other blocks'
  rewards stay silent (selection is correct).
- **Per-phase muting** — output is suppressed exactly where `current_phase.output_muted`,
  so mid-session muted rests actually mute and `warmup → run` happens once.
- **Stage transitions & warm signals** — reward behaves correctly across
  `block → rest → block` boundaries, with no re-settle dead time (derives are global).
- **Host control API** — `hold()` extends a `timed_with_floor` block past its floor,
  `set_clock_frozen()` pauses the countdown, early `advance_phase()` ends a block, and
  `open` phases advance only on host command.
- **Percentile freeze** — adaptive windows ingest during the first phase and non-muted
  phases and freeze during muted phases after the first ("one baseline, up front").

Today, pointing `refrain fuzz` at a staged protocol raises an unhandled
`ValueError` in `surface.py` (`_reward_condition_from_ir`: "reward.event has no
all_of/any_of condition"), because v1 assumes a single global reward. This design
removes that limitation by making the whole pipeline phase- and block-aware.

## Guiding principles

1. **Generalise, don't fork.** A non-staged protocol is the degenerate case of a
   staged one: *one implicit block, one timed phase, no host actions*. All
   pipeline stages are written once against the block/phase model; `smr_cz` and the
   existing 56 fuzz tests remain green unchanged. No separate staged code path, and
   the v1 "unsupported staged protocol" guard becomes unnecessary.
2. **Preserve oracle independence.** The oracle predicts phase-by-phase from the
   baked filter coefficients + the protocol's phase/block model + the scenario's
   host-action schedule, and **never runs the evaluator**. A cascade or
   phase-runtime bug must not be able to influence the prediction.
3. **Mirror the runtime's guiding invariant.** Per the staged runtime,
   *activation gates emission/selection, never computation*: every derive,
   threshold, inhibit, and reward bundle computes every chunk; a block only changes
   *which* threshold/output/inhibit may emit and *which* reward bundle the
   `reward.*` references resolve to. The oracle encodes this directly (warm signals
   across boundaries; `set_control` seeding lands regardless of active phase).

## Component changes

### 1. `Scenario` contract — add a host-action schedule

`Scenario` gains one optional, ordered field:

```python
HostAction = AdvancePhase(at_s: float)
           | Hold(at_s: float, held: bool = True)
           | SetClockFrozen(at_s: float, frozen: bool)

actions: tuple[HostAction, ...] = ()   # default: no explicit host actions
```

- Band-content `segments` are unchanged (EEG over time).
- The **renderer ignores `actions`** (signal only); the **driver** issues the
  corresponding evaluator calls at the matching sample; the **oracle** consumes
  `actions` to resolve when each phase actually starts/ends. Same Scenario,
  consumed independently by renderer / oracle / driver.
- **Tier 1 (default schedule):** most scenarios carry `actions=()` and rely on the
  driver's default policy — advance each `timed` / `timed_with_floor` phase at its
  nominal duration, and advance each `open` phase after a fixed synthetic dwell
  (enough to march any session to completion without hanging).
- **Tier 2 (host-action family):** a dedicated, smaller family populates `actions`
  with non-trivial schedules (hold / freeze / early or open advance).

### 2. `LogicalSurface` — blocks, reward bundles, phase model

`build_surface` extracts:

- **`reward_bundles: dict[str, RewardBundleSurface]`** — each named bundle carries
  its own condition tree + dwell, extracted exactly as the single reward is in v1
  (reusing `_reward_condition_from_ir` / `_dwell_ms_from_ir` per bundle).
- **`blocks: tuple[BlockSurface, ...]`** — each records its `threshold`(s) (list),
  its one `reward` bundle name, and its `output` / `inhibit` channel lists.
- **`phases`** — each `PhaseSurface` gains `mode ∈ {timed, open, timed_with_floor}`
  and `block: str | None` (it already carries `duration` / `output_muted`).
- **Output channel roles** — for each output binding, which reward field it carries
  (`reward.event` discrete vs `reward.continuous`), so the checker can match a
  discrete chime to the active block's event channel.

**Degenerate (non-staged) synthesis:** when the IR has no blocks
(`ir.blocks` / `ir.reward_bundles` empty), the surface synthesises a single implicit
block named e.g. `__default__` wrapping the top-level reward, with every phase
pointing at it. Detection keys on `ir.blocks` / `ir.reward_bundles` being non-empty
(both are empty for `smr_cz`).

### 3. Oracle — phase-aware prediction

`predict(scenario, surface)` gains a front-end step and generalises the rest:

1. **Phase-schedule resolution** — produce the per-sample active-phase timeline by
   walking the protocol's phases under the scenario's host actions:
   - `timed` advances when `phase_elapsed ≥ duration`;
   - `open` advances only at a scheduled `AdvancePhase` (else runs to scenario end);
   - `timed_with_floor` advances at `duration` unless a `Hold(held=True)` is active,
     then extends until its `AdvancePhase`;
   - `SetClockFrozen` intervals pause `phase_elapsed` accumulation (the countdown
     freezes; auto-advance cannot fire while frozen).
   Output: the active phase index — and thus the active block — at every sample.
2. **Active-block reward selection** — at each sample, the SHOULD-FIRE prediction
   uses the *active block's* reward bundle (its condition tree + dwell) and is
   attributed to the *active block's* event output channel. Samples in a block-less
   phase (warmup / rest) have no active reward → SHOULD-NOT-FIRE.
3. **Per-phase muting** — output suppressed wherever `current_phase.output_muted`
   (generalises v1's warmup-only muting). A muted mid-session rest → suppressed /
   DON'T-CARE there.
4. **Percentile freeze** — the ordinal-percentile model ingests during the first
   phase and non-muted phases and freezes its rolling window during `output_muted`
   phases after the first.
5. **No settle-collar at warm boundaries** — because derives run globally, the
   signal is already settled when a block goes active; the oracle keeps its
   steady-state envelope across block/phase boundaries and inserts a settle collar
   only at genuine signal/condition transitions and session start, *not* at phase
   transitions.

The existing 3-valued machinery (leaf truth, condition tree, dwell, collar,
DON'T-CARE reasons) is reused per active bundle; only the *selection* of which
bundle/condition is live per sample is new.

### 4. Generator — staged scenario families

Built on the shared phase/block model; each tagged for coverage reporting:

- **Per-block reward pivots** — for each block, drive its condition TRUE / FALSE
  during that block's active phase → its chime fires / stays silent on its channel.
- **Block isolation (selection proof)** — drive block B's condition TRUE while block
  A is active → must NOT fire (proves correct bundle selection).
- **Per-phase muting** — drive a condition TRUE during a muted rest → no emission;
  plus the `warmup → run`-once invariant.
- **Stage transition** — a condition held TRUE across `block → rest → block` → fires
  in the blocks, silent in the rest.
- **Warm-signal-at-boundary** — condition already TRUE the instant a block goes
  active → fires promptly (asserts no re-settle dead time).
- **Host-action family (tier 2)** — `hold()` extends a block past its floor (reward
  keeps firing past nominal end); `set_clock_frozen` pauses the countdown; early
  `advance_phase()` ends a block (reward stops); `open`-phase advance.
- **Percentile-freeze** — a rank that *would* shift if the window kept ingesting
  through a rest must stay put while frozen.

### 5. Checker + report — channel/block aware

- `check_scenario` becomes **channel-aware**: each `ActualEvent` carries its output
  channel; it is matched to the *active block's* expected event channel + window. A
  chime attributed to a non-active block → SPURIOUS. The vacuity guard, collar
  matching, and metamorphic monotonicity are otherwise unchanged.
- Coverage tags gain per-block / per-phase-type / per-host-action dimensions.
- The report's "What your protocol does" section gains a per-block behavioural
  summary (e.g. "`beta_up` rewards beta-up at Cz; `alpha_up` rewards alpha-up"),
  alongside the muting / transition / host-action verdicts in "Engine check".

### 6. Driver / CLI — step-wise marching

The one notable evaluator-interface change. The v1 driver iterates
`eval_protocol(ir, source)` as a black box; a staged run must instead drive the
evaluator **step-by-step**: step a chunk, consult `current_phase()`, and issue
`advance_phase()` / `hold()` / `set_clock_frozen()` at the scheduled samples (an
`open` phase requires this or the run hangs). The driver moves to the step-wise
evaluator API and collects channel-tagged events. The exact step API surface
(`step_chunk` / `advance_phase` / `hold` / `set_clock_frozen` / `current_phase`) on
the Python `Evaluator` will be confirmed against `eval_.py` during planning; the
staged runtime design specifies these methods exist. The non-staged path may keep
using `eval_protocol` (degenerate: one timed phase, no actions) or share the
step-wise path — to be decided in the plan based on the real API.

## Test target & acceptance

- **Target protocol:** `examples/staged_beta_alpha.refrain` (the staged analogue of
  `smr_cz`): two heterogeneous blocks (beta-up, alpha-up), one baseline, the three
  phase types, and muted rests.
- **Headline acceptance:** the target passes the full staged fuzz (report renders,
  no vacuity/generator abort), **and** at least one mutation is caught — e.g. point
  `block1` at the wrong reward bundle (alpha instead of beta), or un-mute a rest, and
  assert the report's behavioural summary / engine check reflects the change.
- **Regression:** `smr_cz` and all existing fuzz tests stay green (degenerate path).

## Implementation sequencing (one plan, A → B)

- **Increment A (core):** Scenario host-action field (contract only); surface
  block/phase/bundle extraction + degenerate synthesis; oracle phase-schedule
  resolution + active-block selection + per-phase muting; step-wise driver marching
  at nominal durations (tier-1 default schedule, no host actions yet); channel-aware
  checker/report; per-block + muting + transition scenario families; staged target
  passes core engine check; non-staged regression green.
- **Increment B (host API + adaptivity):** host-action family (hold / freeze /
  early-advance / open) wired through driver + oracle; percentile-freeze modelling
  and scenarios; warm-signal-at-boundary scenarios; mutation acceptance test.

Each increment ends green and reviewable; B builds on A's phase model.

## Out of scope (YAGNI for this design)

- Fuzzing weighted-composite reward, `inhibit`-primitive masking, coherence, or
  multi-site placement (orthogonal v1 fuzzer limitations, unchanged here).
- Recorder-side baseline seeding (R5) — recorder responsibility per the staged
  runtime design; the fuzzer assumes default control values as v1 does.
- Rust-backend parity fuzzing — the fuzzer drives the Python evaluator; Rust parity
  is covered by the staged runtime's own parity tests.
- Exhaustive host-action interleavings / randomized host scripts with shrinking —
  the directed host-action family is the v1 target; broader exploration is a later
  layer.

## Risks / open questions for planning

- **Step-wise evaluator API shape.** The driver depends on `step_chunk` +
  `advance_phase` / `hold` / `set_clock_frozen` / `current_phase` being callable
  push-mode on the Python `Evaluator`. Confirm exact signatures and the
  tap/phase-alignment snapshot semantics in `eval_.py` before building the driver.
- **Event channel attribution.** Confirm how `eval_protocol` / the step API surfaces
  the output channel and reward-field of each emitted event, so the checker can
  attribute a chime to the active block's channel.
- **Percentile-freeze fidelity.** The oracle must replicate the runtime's exact
  ingest/freeze rule (first phase + non-muted ingest; muted-after-first freeze) for
  rank predictions to match; this is the subtlest oracle addition and warrants a
  dedicated directed test against the runtime's behaviour.
- **Open-phase synthetic dwell.** Tier-1's fixed synthetic dwell for `open` phases
  must be long enough for the relevant condition/dwell to resolve but short enough
  to keep runs fast; pick per-protocol from the bundle dwell + settle, as v1 sizes
  its spikes.
