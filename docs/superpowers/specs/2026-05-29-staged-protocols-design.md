# Staged / segmented protocols — N-phase runtime, named blocks — design

> Status: approved design (brainstorm complete), ready for an implementation plan.
> Builds on `main` (v0.6.3). Requested by the Coherence Recorder team (2026-05-29).
> Scope: **R1–R4 + R6** of the request. R5 (one-shot baseline measure-then-freeze)
> was reclassified by the requester as recorder-side responsibility and is **out of
> scope** here — see "R5: out of scope" below.

## Goal

Let one `.refrain` protocol describe a **staged clinical session**: N short
training blocks with rests between them, taken against **one baseline up front**,
where blocks may train **different things** in one sitting (block 1 beta-up,
block 2 alpha-up). Today the evaluator's lifecycle is one-shot
`ready → warmup → run → stopped` (`eval_.py:399-457`, `eval.rs:676-1094`): only
the first session phase is acted on (its `output_muted` flag defines the warmup
window), and every component is global for the whole run. A muted rest placed
mid-session does **not** mute today. This generalizes the lifecycle into a real
N-phase runtime with host-driven advance/hold, and adds named blocks that gate
*which* components emit per phase — while keeping every existing protocol working
unchanged.

## The guiding invariant

**Activation gates *emission / selection*, never *computation*.**

Every derive, threshold impl, inhibit, and reward bundle computes **every chunk in
every phase**, exactly as today. A block only changes:

- which named **threshold / output / inhibit** may emit/gate, and
- which **reward bundle** the `reward.continuous` / `reward.event` references
  resolve to.

Consequences this invariant buys us, all called out as requirements by the
requester:

- **Warm signals across block boundaries** — `beta_envelope` and `alpha_envelope`
  both run the whole session, so a block's signal is already settled when it goes
  active (no filter re-settling, no dead time at the boundary). (R4)
- **`set_control` seeding lands regardless of active phase** — the recorder seeds
  every block's control-backed `absolute()` threshold at the single warmup→run via
  `set_control`; because threshold impls are global and always-stepped, the update
  takes effect even though that block's phase isn't active yet. Confirmed against
  `eval_.py:667-673` (every threshold steps each chunk), `eval_.py:1009-1033` +
  `primitive_impls.py:446-449` (`set_control` → `AbsoluteThresholdImpl.update_control`,
  phase-agnostic), and the Rust mirror `Control::Const { ControlCell }` /
  `set_control` (`eval.rs:660-671`). (R5 confirmation)

A Python↔Rust parity test asserts this invariant directly (see §Testing).

## R5: out of scope (recorder-side)

The "one shared baseline" is **not** a Refrain ask. The recorder buffers warmup
telemetry, takes a percentile of the settled tail per signal, and calls
`set_control(name, value)` on each block's control. The protocol threshold is
`absolute(value: <control>)`, so setting the control freezes it at the measured
value. This rides entirely on substrate already shipped in v0.6.3 (control-backed
`absolute()` thresholds + `set_control` reaching `AbsoluteThresholdImpl`). For
staged sessions the recorder seeds every block's threshold-control once at
warmup→run, which works because all derives run during warmup (the guiding
invariant), so the warmup telemetry already contains every block's signal. The
engine's only obligation here is the invariant above, which the parity test
guards.

## DSL surface

```
derive beta_envelope  { ... }        // global, always computed (unchanged)
derive alpha_envelope { ... }

threshold "beta_t"  { signal = "beta_envelope";  type = absolute(value: beta_uv) }
threshold "alpha_t" { signal = "alpha_envelope"; type = absolute(value: alpha_uv) }

// Named reward BUNDLES. Declared on the existing `reward "<name>"` form,
// disambiguated from weighted components BY FIELDS:
//   continuous / event  ⇒ a block-selectable reward bundle (new)
//   signal / weight      ⇒ a weighted composite component (v0.2, unchanged)
reward "beta_reward"  { continuous = sigmoid(beta_envelope  - "beta_t"); event = dwell(...) }
reward "alpha_reward" { continuous = sigmoid(alpha_envelope - "alpha_t"); event = dwell(...) }

output {                              // channels declared globally, as today
  audio_gain  = reward.continuous     // resolves to the ACTIVE block's bundle
  audio_chime = reward.event
}

block "beta_up"  { threshold = "beta_t";  reward = "beta_reward";  output = ["audio_gain","audio_chime"] }
block "alpha_up" { threshold = "alpha_t"; reward = "alpha_reward"; output = ["audio_gain","audio_chime"] }

session { phases = [
  phase { name="warmup";   duration=120 s; output_muted=true },              // timed (default)
  phase { name="block1";   duration=5 min; block="beta_up";  mode=timed_with_floor },
  phase { name="rest1";    output_muted=true; mode=open },                   // open: host advances
  phase { name="block2";   duration=5 min; block="alpha_up"; mode=timed_with_floor },
  phase { name="rest2";    duration=2 min;  output_muted=true },             // timed rest
  phase { name="cooldown"; duration=30 s;   output_muted=true },
]}
```

Spelling notes:

- A block's `threshold`, `output`, and `inhibit` fields are **lists**; a bare
  string is sugar for a one-element list. `reward` references exactly one bundle.
- The homogeneous "4×5 min" case is the **same block** named by multiple phases.
  The heterogeneous case names different blocks.
- Reward bundles reuse the `reward "<name>"` declaration; the resolver already
  branches on field shape (`resolver.py:664-700` errors on unexpected fields in a
  weighted component), so `{continuous, event}` vs `{signal, weight}` disambiguate
  cleanly with no new keyword and no collision.

## Decisions locked in brainstorming

1. **Approach C (hybrid).** Masking (warm-compute, gate-emit) for
   thresholds/outputs/inhibits; named, block-selectable bundles for **reward**.
   Pure masking (A) was rejected because reward is a singleton today
   (`reward.event` is one global dwell), so two blocks could not have genuinely
   different chimes — exactly the heterogeneous case being motivated. A full reward
   registry (B) was rejected as paying the registry cost for thresholds/outputs
   that only need a mask.
2. **Reward bundles compute even when inactive.** All bundles evaluate every chunk
   so event/dwell timers stay warm; only the active bundle is selected for emission.
   (Preserves the warm-signal invariant for event reward, not just continuous.)
3. **Percentile freeze rule keys on phase position, not a new knob.** Adaptive
   windows ingest during the first phase (warmup populates) and during non-muted
   phases; they freeze during `output_muted` phases after the first. Directly
   encodes the requester's "one baseline, up front." (YAGNI: no per-phase
   `freeze_windows` field in v1.)
4. **`state` stays coarse for back-compat** (`ready | warmup | run | stopped`); the
   real phase model is exposed via a new `current_phase()` introspection method.

## Phase types & runtime state machine (R1, R2)

`IRPhase` gains two fields: `mode ∈ {timed, open, timed_with_floor}` (default
`timed`) and `block: str | None` (default `None`). The evaluator replaces the
single `_warmup_samples` boundary with a **phase cursor**: `phase_index`,
`phase_elapsed_samples`, and a per-phase `held: bool`.

Per phase type:

- **timed** — auto-advances when `phase_elapsed ≥ duration`. Firm: `hold()` is a
  no-op. (Today's behavior; used for `rest2`, `cooldown`.)
- **open** — no `duration`; never auto-advances; ends only on `advance_phase()`.
  (Open-ended rests the clinician ends when the patient is ready.)
- **timed_with_floor** — auto-advances at `duration` *unless* `hold()` was called
  (then extends until `advance_phase()`); `advance_phase()` may also end it early.
  (Clinician-controllable training blocks.)

`state` (coarse, back-compat) maps as: `ready` before `start()`; `warmup` while in
the **first** phase iff it is `output_muted` (today's exact behavior); `run` for any
later active phase; `stopped` after the last phase advances.

**Invariant the recorder's baseline seeding depends on (request seam #3):** `state`
transitions `warmup → run` **exactly once** — when the initial `output_muted` phase
completes — and **never returns to `warmup`**. Every mid-session muted rest reads as
`run`; its muting comes from `current_phase.output_muted`, never from `state`. So a
host that filters warmup frames by `state == "warmup"` and seeds controls on the
single `warmup → run` edge stays correct in a staged session. (If the first phase is
not `output_muted`, there is no warmup phase and `state` goes `ready → run` with no
`warmup` interval — but staged protocols carry a warmup phase.) A test asserts
`state` never re-enters `warmup` across a full staged run.

**Output suppression generalizes** from `state == "warmup"` to
`current_phase.output_muted`. This is the fix for the core limitation: mid-session
muted rests now actually mute. The per-chunk suppression flag in
`_process_chunk` (`eval_.py:651`) and Rust `step_chunk_events` (`eval.rs:1010`)
changes from "is warmup" to "is current phase muted."

Phase advance happens at the end of each `step_chunk` (mirroring today's
`advance()` cursor logic in both backends): accumulate `phase_elapsed_samples`;
if the phase is `timed`, or `timed_with_floor` and not held, and elapsed ≥ duration,
advance to the next phase (resetting `phase_elapsed_samples`, recomputing the active
block). Advancing past the last phase → `stopped`.

## Advance / hold / introspection API (R3)

New push-mode methods, implemented in the Python `Evaluator`, the Rust
`RustEvaluator`, the pyo3 bindings (`python.rs`), and the uniffi mobile wrapper
(`mobile.rs`):

- `advance_phase() -> bool` — end the current phase now and enter the next.
  Advancing past the last phase transitions to `stopped`. No-op returning `False`
  if already `stopped` (robust at the final phase, per acceptance criteria). Works
  while the clock is frozen (a clinician may "Next" during a pause).
- `hold(held: bool = True) -> bool` — suppress auto-advance for the current phase if
  it is `timed_with_floor` ("extend this training block past its floor until I
  advance"); returns `True` if it took effect, else `False` (open phases hold
  implicitly; `timed` is firm). **Releasable:** `hold(False)` re-arms auto-advance
  without advancing, so a clinician Hold toggle works; `hold` is also cleared on the
  next `advance_phase()`. This is the *extend-past-floor* action — distinct from the
  *pause-the-clock* action below.
- `set_clock_frozen(frozen: bool) -> None` — suspend (or resume) accumulation of
  `phase_elapsed` for the **current phase, on every phase type**. While frozen, no
  auto-advance can fire; on resume the countdown continues from where it stopped.
  **Orthogonal to output** — this is purely the protocol-clock half; the recorder's
  pause keeps its own independent output gate (see Transport integration). Idempotent.
- `current_phase() -> {index, name, mode, output_muted, block, remaining_s,
  clock_frozen, held}` — queryable each step. `remaining_s` is `duration − elapsed`
  for timed / timed_with_floor-not-held phases (a static value while
  `clock_frozen`), and `None` for `open` or held phases (no live clock). Before
  `start()` / after `stopped`, returns a terminal/empty form (index `-1`).

**Auto-advance predicate** (evaluated at the end of each `step_chunk`):
`fires ⇔ (mode == timed OR (mode == timed_with_floor AND not held)) AND not
clock_frozen AND phase_elapsed ≥ duration`. `phase_elapsed` accumulates only while
`not clock_frozen`.

**Tap/phase alignment (no boundary off-by-one).** The phase cursor advances at the
*end* of `step_chunk`, but taps are captured mid-chunk. `current_phase()` therefore
returns a **snapshot of the phase active during the chunk just processed** (captured
at the same point as `last_taps` / `last_streams`), not the post-advance cursor — so
a host polling `current_phase()` and `last_taps()` after a `step_chunk` always sees a
coherent pair, even on the chunk that triggers an advance. In addition, `last_taps`
gains a numeric **`phase/index`** key (and `phase/output_muted` as 0.0/1.0) so the
tap frame itself carries its phase; the host maps index → name/block from the
protocol it already holds. (Phase *name*/*block* stay out of `last_taps`, which is
numeric-only.)

`warmup_remaining_s` is retained and re-expressed in terms of the phase cursor
(remaining of the first phase while it is the active warmup phase, else `0.0`). This
generalizes the offline-only `skip_warmup` (`runner.py:130-147`) into the supported
live control the requester asked for.

### Transport integration (pause × phase clock)

The recorder's three transport controls map cleanly onto this surface:

| Transport control | Engine call(s) | Behavior |
|-------------------|----------------|----------|
| **Next →** | `advance_phase()` | End current phase, enter next; ends the session past the last phase. |
| **Pause / Resume** | `set_clock_frozen(true/false)` **+** the recorder's existing output gate | Freezes the protocol clock so a phase can't drift/auto-advance during an unplanned break; output muting stays the recorder's independent gate. Works on any phase type. |
| **Hold / release** (extend a training block) | `hold()` / `hold(False)` | `timed_with_floor` only; extend past the floor, then resume the countdown or `advance_phase()`. |

`state` is unaffected by `set_clock_frozen` / `hold` — both only influence *when* the
phase cursor advances, never the coarse lifecycle state (see Back-compat).

## Activation masking & reward selection (R4)

Each chunk, after computing all streams, the evaluator determines the **active
block** = `current_phase.block` (or, if the protocol declares no blocks, an
implicit global bundle). Then:

- **Outputs.** Channels not in the active block's `output` set are muted via the
  same gating path as `output_muted`. If no block is declared, all channels are
  live (back-compat).
- **Reward.** All reward bundles compute every chunk (decision 2). The
  `reward.continuous` / `reward.event` / `reward.event.holds` references resolve to
  the **active** block's bundle. If no bundles are declared, the single top-level
  `reward { … }` is the implicit always-active bundle (back-compat).
- **Inhibits.** Keep stepping every chunk (warm). A block's `inhibit` list selects
  which inhibits gate during that block; non-member inhibits do not contribute to
  the mute gate. The list is **optional and defaults to all declared inhibits** —
  so an always-on EMG guard needs no per-block restatement; a block narrows the set
  only when it wants to.
- **Thresholds.** Keep stepping every chunk (warm). A block's `threshold` list is
  **declarative**: it records which thresholds belong to the block (for validation
  and the recorder's per-block telemetry) and matches the request's sketch. Emission
  selection flows transitively through the block's reward bundle and its referenced
  output expressions (e.g. `beta_reward = sigmoid(beta_envelope - "beta_t")` pulls
  in `beta_t` by selecting the bundle), so the field does not itself add a runtime
  gate. The resolver validates that listed thresholds exist and warns if a block's
  reward bundle references a threshold the block did not list.

"Two identical blocks across phases" gives the homogeneous case; "different blocks"
gives beta-up-then-alpha-up. Both are pure activation over already-computed, named
components — no derive chains are torn down or rebuilt.

## Adaptive `percentile()` windows across rests (R6) — freeze

Mechanism: **freeze ingestion**. During a frozen sample the rolling buffer is simply
**not appended** (the `deque` in `PercentileImpl`, `primitive_impls.py:355-399`; the
`VecDeque` in Rust `Percentile`, `dsp.rs:208-241`); the current (frozen) percentile
value is still emitted so taps and any active output keep a sensible value. Freeze
is cleaner than "exclude" in both backends because neither buffer carries
provenance metadata — skipping the push needs no new state.

Rule, grounded in "one baseline up front":

> Adaptive windows ingest during the **initial phase** (warmup populates) and during
> any **non-muted** phase; they **freeze** during `output_muted` phases **after the
> first**.

Equivalently: `freeze_ingestion ⇔ current_phase.output_muted AND phase_index > 0`.
So `block2`'s window = `block1`'s tail + `block2`, with `rest1` excluded — no
rest-period artifact (water break, movement) pollutes a later block's window.
`absolute()` / baseline-fixed thresholds are inherently immune and unaffected.

Note (request seam #5): the freeze keys on **muted-ness**, not block-active. In a
heterogeneous *adaptive* session a threshold's window still ingests during non-muted
phases where its block is not active (e.g. `beta_t`'s `percentile()` window ingests
during the alpha block, since the alpha block is non-muted). This is harmless for
the requester, who defaults staged protocols to baseline-fixed `absolute()`
thresholds (immune to the window entirely), and a per-block freeze knob is not
warranted (YAGNI). Flagged here so no one is surprised by an adaptive staged
protocol later.

The freeze decision is computed once per chunk by the evaluator and threaded into
the threshold-step path (a per-chunk `ingest: bool` argument to the percentile
step, or equivalent), so both backends gate ingestion identically. Non-adaptive
threshold impls ignore the flag.

## IR, IR-JSON, and schema changes

- **`ir.py`:** `IRPhase` gains `mode: str` and `block: str | None`. New `IRBlock`
  dataclass (`name`, `thresholds: tuple[str,...]`, `reward: str | None`,
  `outputs: tuple[str,...]`, `inhibits: tuple[str,...]`). `IRProtocol` gains
  `blocks: dict[str, IRBlock]` and `reward_bundles: dict[str, IRReward]` (the
  existing single `reward` remains as the implicit/default bundle). `ir_print.py`
  renders the new fields.
- **`ir_json.py`:** emit `phase.mode`, `phase.block`, a `blocks` map, and named
  reward bundles. IR-JSON stays at **v0.2** — all additions are new optional
  properties (the v0.2 schema already uses `additionalProperties: true` on
  `Session`/`Phase`), so existing fixtures remain valid. (No wire-version bump.)
- **`ir-json-v0.2.schema.json`:** add `mode` (enum) and `block` to `Phase`; add a
  `Block` `$def` and a `blocks` map; document reward-bundle shape. Keep
  `duration_ms` optional-when-`mode=open` (a `mode=open` phase has no duration).
- **`refrain-core/src/ir.rs`:** `Phase` gains `mode: String` (serde default
  `"timed"`) and `block: Option<String>`; new `Block` struct; `Protocol` gains
  `blocks` and named reward bundles. All additive with serde defaults so older
  IR-JSON deserializes unchanged.

## Back-compat & validation

- **No `session` / no phases** → one implicit run phase, everything active (today,
  untouched).
- **Phases but no blocks** → phase sequencing + generalized muting; the single
  global reward and all outputs are always active. Existing protocols produce
  byte-identical output (only previously-ignored mid-session muted phases now
  actually mute — which is the requested fix, and is covered by a dedicated test).
- **Blocks declared** → the resolver requires every **non-muted** phase to name a
  `block`; muted rests and the warmup phase need none. References to unknown
  threshold / reward-bundle / output / inhibit / block names are resolve errors
  with source locations. A `mode=open` phase must omit `duration`; a `timed` /
  `timed_with_floor` phase must provide one.

## Dual-backend touch points

| Layer | File(s) |
|-------|---------|
| Grammar | `src/refrain/grammar.lark` (add `block` to `DECL_KW`; `mode`/`block` phase fields parse via existing block-expr) |
| Resolver | `src/refrain/resolver.py` (`_resolve_session` phase fields; new `_resolve_blocks`; reward-bundle branch; validation) |
| IR | `src/refrain/ir.py`, `src/refrain/ir_print.py` |
| IR-JSON | `src/refrain/ir_json.py`, `refrain-core/schema/ir-json-v0.2.schema.json` |
| Python evaluator | `src/refrain/eval_.py` (phase cursor, `advance_phase`/`hold`/`set_clock_frozen`/`current_phase`, `phase/index` tap, generalized suppression, active-block masking, reward selection, percentile freeze) |
| Python impls | `src/refrain/primitive_impls.py` (percentile ingest flag) |
| Rust core | `refrain-core/src/eval.rs`, `ir.rs`, `dsp.rs` |
| Rust bindings | `refrain-core/src/python.rs`, `refrain-core/src/mobile.rs` (expose `advance_phase`/`hold`/`set_clock_frozen`/`current_phase`) |

## Testing

Python-first unit tests per requirement, then a **Python↔Rust parity suite**
(extending the `tests/test_eval_rust_backend.py` pattern) asserting identical
events, taps, and phase introspection on a staged protocol. Specific cases:

1. **N-phase sequencing (R1/R2):** a 6-phase protocol runs phases in order and
   reaches `stopped` after the last; `timed` auto-advances on its clock.
2. **open / timed_with_floor (R2/R3):** an `open` phase never auto-advances and
   ends on `advance_phase()`; a `timed_with_floor` phase auto-advances at duration,
   ends early on `advance_phase()`, and extends past duration after `hold()`;
   `hold(False)` re-arms the countdown without advancing (seam #4).
3. **Robust final phase (R3):** `advance_phase()` past the last phase → `stopped`;
   a further `advance_phase()` is a no-op.
4. **Introspection (R3):** `current_phase()` reports correct
   index/name/mode/block/`clock_frozen`/`held` and `remaining_s` each step (and
   `None` remaining for open/held).
4b. **Clock freeze / pause (seam #1):** with `set_clock_frozen(true)` mid-phase,
   feeding many chunks does not advance the phase; `set_clock_frozen(false)` resumes
   the countdown from where it stopped (a 4:30-of-5:00 block finishes at 5:00, not
   immediately); `advance_phase()` still works while frozen. `state` is unchanged by
   freezing.
4c. **Tap/phase alignment (seam #2):** on the chunk that triggers an auto-advance,
   `current_phase()` and `last_taps()` (incl. the `phase/index` key) describe the
   **same** chunk — no off-by-one — verified by stepping exactly across a boundary.
4d. **State never re-enters warmup (seam #3):** across a full staged run with
   mid-session muted rests, `state` is `warmup` only during the initial muted phase
   and is `run` for every later phase including the rests; it never returns to
   `warmup`.
5. **Mid-session mute (R1/§Goal):** output is emitted during active phases and
   muted during a mid-session `output_muted` rest — not just the first phase.
6. **Activation (R4):** with two heterogeneous blocks, reward/output reflect
   `beta_reward` during block1 and `alpha_reward` during block2; non-member
   channels are muted.
7. **Warm-signal + seeding invariant (§invariant / R5):** `set_control` on an
   inactive block's control-backed `absolute()` threshold during warmup takes
   effect; both blocks' derives advance every chunk regardless of active block.
8. **Percentile freeze (R6):** a `percentile()` window's value after a muted rest
   equals what it would be had the rest samples never been ingested.
9. **Back-compat:** every existing example/test protocol produces byte-identical
   events under the new evaluator (no blocks, no new phase fields).
10. **Parity:** cases 1–8 (and 4b–4d) run on both backends with identical results,
    including `current_phase()` fields and the `advance_phase`/`hold`/
    `set_clock_frozen` control surface.

The Rust wheel is built (`cd refrain-core && maturin develop --release`) so parity
tests run rather than skip.

## Acceptance criteria (from the request)

- ☐ Run phases in order, each ending by its `mode` rule; reach `stopped` after the
  last phase. (Tests 1–3)
- ☐ Report current phase (name/index/type) and remaining time for timed phases,
  queryable each step. (Test 4)
- ☐ Honor `advance_phase()` during any active phase; honor `hold()` / open-phase
  semantics; robust at the final phase. (Tests 2, 3)
- ☐ Honor `set_clock_frozen()` (transport pause) on any phase type without
  drift/auto-advance, releasable; `hold(False)` release. (Tests 4b, 2) — request
  seams #1, #4.
- ☐ `current_phase()` aligns with the `last_taps` frame (no boundary off-by-one);
  `phase/index` tap present. (Test 4c) — request seam #2.
- ☐ `state` flips `warmup → run` once and never returns to `warmup`. (Test 4d) —
  request seam #3.
- ☐ Emit reward/output only for the active block; mute during `output_muted` phases
  including mid-session rests. (Tests 5, 6)
- ☐ Keep all derives computing across all phases regardless of active block.
  (Test 7)
- ☐ Not ingest muted-rest samples into adaptive `percentile()` windows. (Test 8)
- ☐ Behave identically on Python and Rust backends. (Test 10)
- ☑ One baseline at warmup seeding all blocks' thresholds — **recorder-side (R5
  out of scope)**; engine obligation is the warm-signal/seeding invariant (Test 7).

## Integration seams (raised in spec review — resolved in this design)

1. **Pause × phase clock (#1, #4).** Resolved by `set_clock_frozen(bool)` (clock-only
   pause, any phase type, releasable, orthogonal to the recorder's output gate) plus
   releasable `hold(False)`. See Advance/hold API + Transport integration.
2. **Tap/phase alignment (#2).** Resolved: `current_phase()` snapshots the phase
   active during the chunk `last_taps` describes (no boundary off-by-one), and
   `last_taps` gains a numeric `phase/index` key. See Advance/hold API.
3. **`state` stays `run` through mid-session rests (#3).** Resolved + asserted by a
   test. See state-machine invariant.
4. **Adaptive freeze keys on muted-ness, not block-active (#5).** Acknowledged in the
   R6 section; no per-block knob (YAGNI).

## Open questions deferred to the plan

- Exact `current_phase()` return type at the Python↔Rust boundary (dict vs a small
  dataclass / pyo3 struct) — capability and field set are fixed above; only the
  wire representation is open.
- Whether the percentile `ingest` gate is a new `step` parameter or a small impl
  method (`set_ingesting(bool)`); pick whichever keeps the two backends closest.
