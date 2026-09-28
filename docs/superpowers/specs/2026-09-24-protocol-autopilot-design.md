# Protocol-declared autopilot — design

**Date:** 2026-09-24
**Status:** design approved in conversation; written spec pending review
**Re:** recorder request "protocol-declared closed-loop guidance/autopilot contract"
(`coherence-recorder` commit `967159f`, "docs: defer session guidance to Refrain")
**Lands in:** v0.22.0 (proposed)

A protocol can declare how each of its clinician-tunable controls may be
adjusted during a session: what reward check the control addresses, its step,
direction, bounds, evidence requirements and cadence, and whether a change may
be applied automatically or only suggested. Refrain validates the policy at
compile time and runs a fixed, deterministic decision procedure in both engines
(Python reference and Rust core), emitting one structured piece of advice after
every chunk. The host (Coherence Recorder, later Companion and Portal) owns the
per-session enable switch, the display, the act of applying a change, and the
persisted audit trail.

Protocols with no policy still receive **observations** and, where the protocol
structure makes it unambiguous, **direction hints**, but never a proposed value
and never an automatic change.

---

## 1. Why

### 1.1 What exists today

Two host-side rulebooks, both of which guess at protocol structure:

- **`coherence-recorder/recorder/backend/nf/guidance.py`** — alpha/theta only.
  Finds the "theta" and "crossover" reward components by substring search on
  their names; hard-codes a 15% guard ceiling, a 70% theta-gate floor, a
  10%/35% reward band, a ±0.05 step and a 0.5–1.0 clamp; knows exactly one
  control (`crossover_target`).
- **`@coherence/nf-coach`** (`training_efficacy.ts`) — generic. Judges
  time-in-criteria against a 50–75% sweet spot and tells the clinician to raise
  or lower "the reward control". Picks that control by name pattern
  (`display_as_reward_rate`, `*_inhibit_rate`) and infers direction from a
  seed-percentile heuristic (> 50 ⇒ down-train). Several of its tests are
  regressions for wrong guesses (coaching a "reward rate" control that does not
  exist; reward-rate wording on a baseline protocol).

Both encode clinical policy outside the protocol, so it is not in the
reproducibility hash, not reviewable alongside the protocol, not portable to
Companion, and wrong whenever a protocol's naming departs from the pattern.

### 1.2 What the protocol structure already knows

The resolved IR knows every reward check, every guard (inhibit), every control,
and — by tracing references — which control feeds which check or guard, and
whether raising it makes the check easier or harder. Everything nf-coach
approximates by name pattern is derivable exactly.

### 1.3 Worked cases that shaped the design

Six production protocols in `refrain-protocols` were walked through:

| Protocol | What it taught |
|---|---|
| `alpha_theta` | Two checks with knobs on different scales (ratio ±0.05; percent ±5). Guards (delta = sleep, EMG = muscle) must produce messages, never changes. Reward target is 10–35%, not the usual 50–75%. Needs reversal after a bad tightening. |
| `smr` | Three checks. A failing high-beta check means client tension: the right output is a message with no control change. |
| `high_beta_down` | Raising `hbeta_inhibit_rate` makes reward *easier* — the opposite of SMR's `smr_reward_pct` though both are `percent`. Direction must be per control. Baseline-mode µV thresholds span 2–30 µV across clients: needs proportional steps and re-baselining. |
| `hrv_resonance` | 5 s dwell, 5 min percentile window: evidence windows and cadence must be per protocol. |
| `critical_fluctuation` | The guard *is* the protocol; its knob must never be autopiloted. Gets no policy. |
| `faa_f3f4` | One adaptive check. |

**Adaptive thresholds already self-adjust.** `percentile(target_pct: k)` passes
roughly `100 − k`% of the time by construction. A protocol with a single
adaptive check therefore has nothing for autopilot to observe; autopilot is
useful for multi-check protocols (whose joint pass rate varies), baseline-mode
absolute thresholds, and plain targets such as `crossover_target`.

**Every case reduces to the same procedure** — wait for clean evidence; find the
failing check; see whether a knob owns it; nudge one step toward the target
band — with only numbers and choices differing. Hence: a fixed procedure in the
engine, declared parameters in the protocol.

---

## 2. Scope

**In scope (this repo):** grammar, resolver validation, IR and IR-JSON v0.4,
the advisor (Python + Rust, parity-gated), Evaluator host API (Python, PyO3,
uniffi), direction tracing, alpha/theta worked example, docs, a response note
for the recorder team.

**Out of scope:** recorder UI, per-session enable switch, persistence of the
audit trail, retirement of `guidance.py` and nf-coach (recorder / nf-coach
repos); editing `refrain-protocols` (follow-up change after release); the
protocol fuzzer (autopilot does not change patient-facing output); weighted
composite (v0.2 `reward "x" { signal; weight }`) protocols — see §3.6.

---

## 3. Protocol surface

### 3.1 Three levels of participation

| In the protocol | Clinician receives |
|---|---|
| Nothing (all protocols today) | **Observations** + **direction hints**, judged against the built-in default target (§3.5). No proposed value, nothing applied. |
| `autopilot { reward_target = … }` and other protocol-wide settings only | Same, against the protocol's own target, timing and guard messages. |
| Protocol-wide block **and** per-control `autopilot = <strategy> { … }` | Adds concrete proposed values, and automatic application where permitted. |

### 3.2 Named reward checks

A direct element of the reward dwell's `all_of([...])` / `any_of([...])` list
(or the single condition of a one-condition dwell) may carry a name:

```refrain
reward {
  event = dwell(
    condition: all_of([
      above("theta_envelope", "theta_t")           as "theta",
      above("theta_alpha_ratio", crossover_target) as "crossover",
    ]),
    duration: 1000 ms
  )
}
```

- Grammar: array elements become `expression ("as" string_lit)?`. `as` is
  already a grammar token (`import … as NAME`).
- `as` is legal only on the elements of a reward dwell condition list (or on a
  single dwell condition). Anywhere else is a `ResolveError`.
- Names are unique within one reward bundle. Staged protocols' named reward
  bundles (`reward "beta_reward" { … }`) name checks independently.
- Names are front-end metadata: the IR records them as a list aligned with the
  existing condition indices. No new expression node kind. Tap keys are
  **unchanged** (`reward/condition[i]` stays; the strict tap key-set parity test
  is untouched, the same decision `seed_report()` made).
- Unnamed checks are referred to in advice as `check 0`, `check 1`, ….

### 3.3 The `autopilot` block (protocol-wide)

New section keyword `autopilot` (added to `SECTION_KW`).

```refrain
autopilot {
  // provenance — required whenever the block exists
  evidence  = "expert_opinion"
  citation  = "Peak Mind clinical team (2026). Adapted from Peniston & Kulkosky 1989 practice."
  rationale = "Crossover is rare by nature; a 50-75% target would drive the ratio target to its floor."
  reviewed  = "J. Croall, 2026-09-24"          // optional

  // when and how advice runs — all optional, defaults in §3.5
  reward_target     = (10%, 35%)
  phases            = ["deep1", "deep2"]
  watch             = 2 min
  between_moves     = 3 min
  equipment_settle  = 60 s
  tighten_first     = ["crossover", "theta"]

  // guards: hold and explain when an inhibit is active too often
  delta = guard { max = 15%; say = "Client may be drifting toward sleep; check alertness." }
  emg   = guard { max = 15%; say = "Muscle artifact; check jaw/neck tension or the electrode." }

  // a reward check with no knob, or whose knob must not be touched
  // (SMR example): hold with this message when it is the limiter
  // high_beta = limiter { say = "Likely client tension; coach relaxation rather than loosening." }
}
```

| Field | Type | Meaning |
|---|---|---|
| `evidence` | string, **required** | `published` \| `clinical_consensus` \| `expert_opinion` \| `experimental`. Closed set (unlike free-text `meta.evidence`). |
| `citation` | string or array of strings, **required** | Source(s) for the numbers. |
| `rationale` | string, **required** | One or two sentences on why these numbers. |
| `reviewed` | string, optional | Who reviewed it and when. |
| `reward_target` | tuple of two percents | Target share of clean training time during which the reward condition is met (§4.3). `low < high`, both in (0%, 100%). |
| `phases` | array of phase names | Phases in which advice may run. Each must name a declared session phase whose output is not muted. |
| `watch` | duration | Clean time required before any judgement. |
| `between_moves` | duration | Minimum time between two applied changes (to any knob). |
| `equipment_settle` | duration | Hold after the host reports an equipment change. |
| `tighten_first` | array of check names | Order in which checks are tightened when reward is above target (§4.4). |
| `<inhibit> = guard { max; say }` | entry | Guard ceiling (percent) and message. The entry's name must name a declared inhibit. |
| `<check> = limiter { say }` | entry | Message used when this check is the limiter and no eligible knob fixes it. Name must name a declared check. |

Entries (`guard`, `limiter`) are distinguished from settings by their block
kind. An entry whose name collides with a setting name is a `ResolveError`.

### 3.4 Per-control policy

A control field, following the `seed` precedent: `autopilot = <strategy> { … }`,
where the strategy is the block kind.

```refrain
controls {
  crossover_target = number {
    default = 0.60; range = (0.5, 1.0); live_tunable = true
    autopilot = fixed_step {
      fixes     = "crossover"
      step      = 0.05
      higher_is = "harder"
      limits    = (0.5, 1.0)
      apply     = "auto"
      round_to  = 0.01
      say       = "Crossover target"
    }
  }

  theta_reward_pct = percent {
    default = 15; range = (15, 70); live_tunable = true
    autopilot = fixed_step {
      fixes     = "theta"
      step      = 5
      higher_is = "harder"
      limits    = (15, 40)
      apply     = "suggest"
      only_when = threshold_style == "adaptive"
    }
  }

  theta_threshold_uv = voltage {
    default = 8.0 uV; range = (2.0 uV, 30.0 uV); live_tunable = true
    seed = percentile { from = "theta_envelope"; window = 60 s; target_pct = theta_reward_pct }
    autopilot = proportional_step {
      fixes     = "theta"
      step      = 10%
      higher_is = "harder"
      apply     = "suggest"
      round_to  = 0.1 uV
      only_when = threshold_style == "baseline"
    }
  }
}
```

**Strategies:**

| Strategy | Proposed value | Fields specific to it |
|---|---|---|
| `fixed_step` | `current ± step` | `step`: in the knob's own units (bare number for `number`/`percent`; `uV`, `Hz`, `s`/`ms` for `voltage`/`frequency`/`duration`). |
| `proportional_step` | `current × (1 ± step)` | `step`: a percent, `0% < step < 100%`. |
| `rebaseline` | the `percentile` of the last `window` of **clean** samples of derive `from` | `from` (derive name), `window` (duration), `percentile` (number 1–99). Always suggest-only. |

**Common fields:**

| Field | Required | Meaning |
|---|---|---|
| `fixes` | yes | The named check this knob addresses. |
| `higher_is` | yes | `"harder"` or `"easier"`. |
| `apply` | yes | `"auto"` or `"suggest"`. |
| `limits` | no | Autopilot bounds, within the knob's `range`. Defaults to `range`; required if the knob has no `range`. |
| `round_to` | no | Snap proposed values to this precision, in the knob's units. |
| `say` | no | Display name used in advice; defaults to the control's `label`, then its name. |
| `watch`, `between_moves` | no | Per-knob overrides of the protocol-wide values. |
| `only_when` | no | A comparison of a **mode control** to a string literal. Mode controls are bound at resolve time (`resolver._fold_mode_conditionals`), so `only_when` is evaluated at compile time: the policy is kept when it holds and dropped when it does not. It never reaches the IR. |
| `citation` | no | Per-knob source, when this knob's numbers come from elsewhere. |

The proposed value is always clamped to `limits` and snapped to `round_to`. If
the result equals the current value, the knob is "at limit".

### 3.5 Built-in defaults

Used when the protocol omits a setting (including protocols with no `autopilot`
block at all). Defined once per engine as `ADVISOR_DEFAULTS`, versioned by
`ADVISOR_VERSION = "1"`.

| Setting | Default | Source |
|---|---|---|
| `reward_target` | (50%, 75%) | nf-coach `SWEET_SPOT_LO_PCT`/`HI_PCT`; documented as Peak Mind clinical heuristic |
| `phases` | every session phase whose output is not muted; if no session block, the whole run | — |
| `watch` | 2 min | recorder `guidance.py` `_WINDOW_S` |
| `between_moves` | 3 min | recorder `_SUGGESTION_CADENCE_S` |
| `equipment_settle` | 60 s | recorder `_EQUIPMENT_FREEZE_S` |
| guard `max` (every inhibit, when no `guard` entry) | 15% | recorder guard ceiling |
| guard `say` | "`<inhibit>` guard active `<n>`% of training time." | — |
| `tighten_first` | none: tighten the check that passes most often | — |

Defaults are not part of the protocol, so they are not in the protocol hash.
Every advice result and audit event carries `advisor_version` and the effective
settings (§5.2), so the session record shows exactly which rules applied.

### 3.6 Protocols the advisor does not judge

- **No reward condition** (continuous-only reward: ILF, `critical_fluctuation`):
  observations report phase, collecting and guard state only; `reward_rate` is
  absent; no hints. An `autopilot` block with `reward_target` or knob policies
  is a `ResolveError`.
- **Weighted composite rewards** (v0.2 `combine = "weighted"`): same as above in
  v1. Extending to composites is future work.

### 3.7 Compile-time validation

Every rule is a `ResolveError` naming the control/entry and the problem in
plain words. Checks run on the final, merged protocol (after `extends`,
`amend`, mode folding).

| # | Rejected | Why |
|---|---|---|
| V1 | `fixes` names a check that does not exist | typo would silently disable the policy |
| V2 | The knob does not feed the check it `fixes` (traced through thresholds, derives and control refs, after mode folding) | a policy may not claim a knob fixes what it cannot affect; also forces `only_when` on mode-dependent knobs |
| V3 | Declared `higher_is` contradicts the traced direction, when the trace is unambiguous (§4.6) | catches reversed direction |
| V4 | `apply = "auto"` on a knob that feeds any inhibit, in **any** mode branch (checked before folding) | guards are never loosened automatically |
| V5 | A policy on a knob that feeds no reward check (volume-like, band edges) | such knobs are never autopiloted |
| V6 | `rebaseline` with `apply = "auto"` | large jumps are always the clinician's call |
| V7 | Two surviving policies `fixes` the same check | one knob per check; mode-exclusive pairs are resolved by `only_when` before this check |
| V8 | Knob is not `live_tunable`, or its kind is `mode`, `boolean`, `enum` or `placement` | cannot be changed mid-session |
| V9 | `limits` outside `range`; no `limits` and no `range`; `low ≥ high` | scale errors |
| V10 | `step ≤ 0`; `fixed_step` step in the wrong units; `proportional_step` step not a percent or ≥ 100% | scale errors |
| V11 | `rebaseline.from` is not a declared derive; its unit is incompatible with the knob's; `percentile` outside 1–99 | |
| V12 | `guard` entry names no inhibit; `limiter` entry or `tighten_first` names no check; `phases` names an unknown or output-muted phase | typos cannot silently disable a safety check |
| V13 | Knob policies present with no `autopilot` block; `autopilot` block missing `evidence`, `citation` or `rationale`; `evidence` outside the closed set | no unsourced policy |
| V14 | `reward_target` malformed or outside (0%, 100%); durations ≤ 0 | |
| V15 | `as "<name>"` outside a reward dwell condition list; duplicate names within a bundle | |
| V16 | `autopilot` targets or knob policies on a protocol with no reward condition or a weighted composite (§3.6) | |
| V17 | `only_when` that is not `<mode control> == "<choice>"` / `!=`, or names a choice the mode does not declare | |

A protocol that fails any rule does not compile. There is no "compile but run
without autopilot" path; the author sees the error.

### 3.8 Composition

- `autopilot` is a singleton section, merged like `reward`: a child's block
  replaces the parent's; `amend autopilot { … }` overrides individual settings
  and entries; `final` locks it; `remove` is not applicable to sections.
- A control's `autopilot` field merges with the rest of the control at field
  level, exactly as `seed` does.
- Validation (§3.7) runs after merging.

---

## 4. Decision procedure

### 4.1 Where it runs

`advisor.py` / `advisor.rs`: a self-contained module whose only job is to turn
per-chunk facts into advice. The Evaluator owns one advisor per session and
feeds it at the end of every `step_chunk`, the same way the seed latch runs
inside the Evaluator. Hosts wire nothing; a knob change via `set_control` cannot
bypass it.

Time is session sample time (samples stepped ÷ sample rate). No wall clock, no
randomness. Window edges fall on chunk boundaries; identical chunk sequences
produce identical advice in both engines.

### 4.2 Per-chunk facts

For each chunk the Evaluator hands the advisor aggregated counts, not raw
samples:

- `n` samples in the chunk; current phase name; whether output is phase-muted;
  whether the session is held / clock-frozen
- per sample: whether any muting inhibit is active (→ `muted`), each inhibit's
  state, each reward check's state, and the reward condition's state
  (the combined `all_of`/`any_of`, before dwell)
- reward event count in the chunk
- for each `rebaseline` policy's `from` derive: the chunk's clean samples
  (buffered up to that policy's `window`)

A sample is **in training** when the current phase is in `phases`, output is
not phase-muted, and the session is not held. A sample is **clean** when it is
in training and not `muted`.

### 4.3 Evidence window

- The window is the most recent `watch` of **clean** samples.
- Clean samples older than `2 × watch` of session time drop out, so evidence
  cannot be assembled from scraps across a long, guard-interrupted stretch.
- Guard activity is measured over **in-training** samples in the same span.
- **Reward rate** = clean samples with the reward condition met ÷ clean samples.
  (Instantaneous condition, not the dwell-held state: matches nf-coach's
  time-in-criteria.)
- **Check pass rate** = clean samples with that check true ÷ clean samples.
- **Chimes per minute** = reward events in the window ÷ window minutes. Reported
  for information only; never drives a decision.

**The window restarts when:**
- any control that feeds a reward check or inhibit changes (manual
  `set_control`, `apply_advice`, or seed firing);
- the host calls `mark_equipment_change()`;
- a new phase in `phases` begins.

### 4.4 Steps

Evaluated after every chunk. The first step that applies decides the result.

| # | Condition | Result `state` / `reason` |
|---|---|---|
| 1 | Current sample not in training | `hold` / `not_training_phase` |
| 2 | Within `equipment_settle` of the last equipment change | `hold` / `equipment_settling` (with remaining time) |
| 3 | Some inhibit's active share of in-training samples in the window exceeds its guard `max` (worst one reported) | `hold` / `guard` (with that guard's `say`) |
| 4 | Clean time in window < `watch` | `collecting` / `collecting` (with progress) |
| 5 | The last change made through `apply_advice` went toward **harder** and reward rate is now below `low`, or toward **easier** and reward rate is now above `high`; evaluated once, on the first full window after that change | `adjust` / `reversal` — propose the pre-change value; bypasses `between_moves` |
| 6 | `low ≤ reward rate ≤ high` | `hold` / `on_track` |
| 7 | Select the check (§4.5) | — |
| 8 | No surviving policy fixes that check | `hold` / `no_knob` (with the `limiter` entry's `say`, if any) — or `hint` (§4.6) when no policy exists and the trace is unambiguous |
| 9 | The knob is at its limit in the needed direction | `hold` / `at_limit` |
| 10 | Less than `between_moves` since the last applied change, or the same knob+direction was dismissed within `between_moves` | `hold` / `cooldown` (with `eligible_at_s`) |
| 11 | Otherwise | `adjust` / `too_strict` or `too_easy`, with the proposed value |

Level 1–2 protocols (no knob policies) run the same steps; step 5 never fires
(no `apply_advice` changes), and step 8 yields `hint` or `no_knob`.

### 4.5 Which check

- **Too strict** (reward rate < `low`): the check with the **lowest** pass rate.
  Ties broken by declaration order. Autopilot never loosens a different check
  to compensate: if the limiter has no knob, it holds and says why.
- **Too easy** (reward rate > `high`): the first check in `tighten_first` that
  has a surviving policy not already at its harder limit; if `tighten_first` is
  absent or exhausted, the check with the **highest** pass rate.

### 4.6 Direction hints (no policy)

The resolver traces, for each reward check, the single knob that determines its
threshold and the knob's direction:

| Check shape | Knob position | Higher knob value makes the check… |
|---|---|---|
| `above(S, k)` | `k` a control ref | harder |
| `below(S, k)` | `k` a control ref | easier |
| `above(S, "t")`, `t = absolute(value: k)` | | harder |
| `below(S, "t")`, `t = absolute(value: k)` | | easier |
| `above(S, "t")`, `t = percentile(target_pct: k, …)` | | harder |
| `below(S, "t")`, `t = percentile(target_pct: k, …)` | | easier |
| anything else (knob inside a derive formula, arithmetic on the threshold, several knobs) | | ambiguous → no hint |

A hint is emitted only when exactly one live-tunable knob of an eligible kind
(V8) determines the limiter's threshold, the trace is unambiguous, and the knob
feeds no inhibit. The hint names the knob, its label and the direction
("lower is easier") — never a value. The same trace backs validation rule V3.

The tracer is a small table-driven function implemented in **both** engines and
run at load time over the existing IR. It needs no new wire field, so hints work
for every existing protocol without changing its compiled JSON or hash. A parity
test runs both tracers over every example protocol.

### 4.7 Recommendation lifecycle

Each `adjust` (and `hint`) result has an id: `adv-NNNN`, a per-session counter
(deterministic; identical across engines). The id is kept while knob, direction
and proposed value are unchanged, so advice does not flicker.

| Event `kind` | Emitted by | When |
|---|---|---|
| `suggested` | advisor | a new id is first emitted |
| `superseded` | advisor | a standing id is replaced by a different result at steps 4–11 (or by a manual change) |
| `blocked` | advisor | a standing id is replaced by a hold at steps 1–3 (carries the hold reason) |
| `applied` | `apply_advice` | with `by: "clinician" \| "autopilot"`, from/to values |
| `dismissed` | `dismiss_advice` | |
| `changed_manually` | `set_control` | knob, from, to — for knobs that feed a check or inhibit |
| `equipment_change` | `mark_equipment_change` | |

Routine `collecting` / `on_track` results are not events; only transitions are.

---

## 5. Host API

### 5.1 Calls (Python `Evaluator`; same names in PyO3 and uniffi)

| Call | Behaviour |
|---|---|
| `advice() -> dict` | Current result (§5.2). Always present once started. Computed during `step_chunk`; this is a read. |
| `apply_advice(id, by="clinician") -> dict` | Sets the knob to the proposed value (through the normal `set_control` path), logs `applied`, restarts the window, arms the reversal check. Raises if `id` is not the current `adjust` id, or if `by="autopilot"` and `auto_allowed` is false. Returns the `applied` event. |
| `dismiss_advice(id) -> None` | Logs `dismissed`; suppresses that knob+direction for `between_moves`. Raises on an unknown id. |
| `mark_equipment_change() -> None` | Logs the event, restarts the window, starts `equipment_settle`. |
| `drain_advice_events() -> list[dict]` | Audit events since the last drain, in order. |
| `autopilot_policy() -> dict` | Effective policy as data: advisor version, settings with defaults filled in, guards, limiters, knob policies (units, limits, step, strategy, direction, apply, labels), provenance. For the host setup screen and session header. |

`set_control` is unchanged in signature; it now also restarts the window and
logs `changed_manually` (§4.7).

With `backend="rust"`, every call delegates to the Rust core, as `seed_report()`
does. Across uniffi, results and events are JSON strings, byte-identical to
Python's `json.dumps(…, sort_keys=True, separators=(",", ":"))`.

**The host decides whether to auto-apply.** When the clinician has enabled
autopilot for the session and `advice()["control"]["auto_allowed"]` is true,
the host calls `apply_advice(id, by="autopilot")`. Refrain never changes a knob
on its own, and refuses auto-application the protocol forbids.

### 5.2 `advice()` shape

```json
{
  "advisor_version": "1",
  "id": "adv-0007",
  "state": "adjust",
  "level": "policy",
  "reason": "too_strict",
  "message": "Reward met 6% of clean time (target 10–35%). The crossover check is the limiter.",
  "t_s": 1312.5,
  "limiter": { "check": "crossover", "pass_rate": 0.08 },
  "control": {
    "name": "crossover_target", "label": "Crossover target",
    "units": "", "round_to": 0.01,
    "current": 0.75, "proposed": 0.70,
    "direction": "easier", "strategy": "fixed_step",
    "auto_allowed": true
  },
  "evidence": {
    "window_start_s": 1180.0, "window_end_s": 1312.5,
    "clean_s": 120.0, "required_s": 120.0,
    "reward_rate": 0.06, "target": [0.10, 0.35],
    "checks": { "theta": 0.84, "crossover": 0.08 },
    "guards": { "delta": 0.03, "emg": 0.07 },
    "chimes_per_min": 0.5
  },
  "eligible_at_s": 1312.5
}
```

- `state`: `collecting` | `hold` | `hint` | `adjust`.
- `level`: `observation` | `hint` | `policy`.
- `reason`: `not_training_phase`, `equipment_settling`, `guard`, `collecting`,
  `on_track`, `no_knob`, `at_limit`, `cooldown`, `too_strict`, `too_easy`,
  `reversal`. Hosts style or translate by code; `message` is the English
  fallback.
- `id` present for `hint` and `adjust`; `control` present for `hint` (without
  `proposed`, `auto_allowed`) and `adjust`; `limiter` present from step 7 on;
  `evidence` fields present once computable; `reward_rate`, `target`, `checks`
  absent for protocols in §3.6.
- Numbers are rounded to 6 decimal places before serialisation so the two
  engines match byte for byte.

---

## 6. Wire format and versioning

### 6.1 IR

- `IRProtocol.autopilot: IRAutopilot | None` — settings as given (durations in
  ms), provenance, guard and limiter entries, `tighten_first`.
- `IRControl.autopilot: IRControlAutopilot | None` — strategy, `fixes`,
  `higher_is`, `apply`, `limits`, `round_to`, `say`, overrides, strategy
  fields, citation. `only_when` is consumed at resolve time and absent.
- `IRReward.check_names: tuple[str | None, ...]` aligned with condition
  indices. Named reward bundles carry their own.

All new fields are optional and defaulted; no existing construction site
changes.

### 6.2 IR-JSON

- New keys are emitted **only when present** (the emitter's omit-when-unused
  idiom): top-level `autopilot`, `controls.<name>.autopilot`,
  `reward.check_names`. Durations are baked to samples at
  the emitted `sample_rate_hz` (`watch_samples`, …), as seed windows are.
- No new `Expr` variants (the Rust loader rejects unknown variants, failing the
  whole document).
- `_protocol_ir_version` returns `"0.4"` when any of these keys is present;
  new `ir-json-v0.4.schema.json`; Rust `SUPPORTED_IR_VERSIONS` gains `"0.4"`.
- An engine without this feature refuses a 0.4 protocol at load (SPEC §9.3)
  rather than running it without its policy.

### 6.3 Hash

The policy is inside `content_hash`: two sessions with the same hash ran the
same rules. Protocols without the new keys keep a byte-identical hash.

---

## 7. Migration

| Where | Effect |
|---|---|
| Every protocol in this repo and `refrain-protocols` | Compiles unchanged, same hash, manual only. Observations available once a host calls `advice()`. |
| Protocols with unnamed checks | Advice says `check 0`, `check 1` until names are added (a minor protocol version bump; the hash changes). |
| New `examples/alpha_theta_autopilot.refrain` | Copy of production `refrain-protocols/protocols/eeg/alpha_theta.refrain` v1.3.0 with named checks and the full policy, plus one further deviation: the montage reference is the literal `"linked_ears"` instead of `amp.reference`, so the example resolves without an amp profile like every other example. Test fixture and worked example. Header notes the 10–35% band came from the recorder's held-reward measure and must be re-confirmed on real sessions under the instantaneous measure. |
| `refrain-protocols` | Follow-up change after v0.22.0 is released: alpha/theta gains names + policy; SMR and siblings gain names (and optionally `limiter` messages). |
| Recorder | Replaces `nf/guidance.py` with `advice()`; nf-coach's name-guessing retires once the recorder and portal consume `advice()`. Old session files keep their old guidance events readable. Recorder repo work. |
| Companion | Needs a Rust core ≥ v0.22.0 to load any 0.4 protocol. |

---

## 8. Testing

All deterministic: scripted inputs, sample-counted time.

| Area | Tests |
|---|---|
| Validation | One failing protocol per rule V1–V17, asserting the message names the entity and problem. |
| Decision steps | Scripted chunk-fact sequences into the advisor alone, one per step in §4.4 plus: tighten-first order and fallback; tie-breaking; stale-window drop-out; window restart on each trigger; reversal fires once and bypasses cadence; dismissal suppression; id stability; superseded vs blocked. |
| Strategies | fixed, proportional and rebaseline values; clamping to `limits`; `round_to`; at-limit detection. |
| Host API | `apply_advice` refuses stale ids and forbidden auto; `set_control` restarts window and logs; `mark_equipment_change`; drain ordering; `autopilot_policy()` fills defaults. |
| Direction tracing | Each row of §4.6; SMR / high-beta-down / FAA shapes; mode-folded thresholds; ambiguous shapes produce no hint; V3 contradiction. |
| Parity | Same scripted sequences through Python and Rust advisors; full synthetic-signal session of `alpha_theta_autopilot.refrain` through both backends; `advice()` JSON and event JSON byte-identical. Added to the existing equivalence check. |
| Stability | Every existing example: identical IR-JSON and hash. v0.4 schema validates the new example. Unparser round-trips `autopilot` blocks, knob policies and `as` names. |

---

## 9. Documentation

| Doc | Content |
|---|---|
| **New** `docs/AUTOPILOT-AUTHORING.md` | Writing a policy, walked through on alpha/theta; choosing strategy, step and direction; when not to add one (single adaptive check, guard knobs); **provenance**: the `evidence` scale and what qualifies for each level, citing papers vs team consensus vs "adapted from recorder guidance v1, re-confirm", optional `reviewed` and per-knob `citation`; `experimental` policies are legal and hosts should label them. |
| `docs/SPEC.md` | §4.12 `autopilot`, per-control `autopilot` field, named checks; §7.10 advisor semantics. |
| `docs/EMBEDDING.md` | Host calls and a recorder-shaped loop. |
| `docs/IR-JSON.md` + schema | v0.4 fields. |
| **New** response note for the recorder team | What replaces `guidance.py` and nf-coach, output shape, host-side migration. |
| `CHANGELOG.md` | v0.22.0, additive. |

---

## 10. Open items

### 10.1 Alpha/theta target band

10–35% was measured by the recorder on the dwell-held reward. Under the
instantaneous measure (§4.3) the equivalent band may differ. The example ships
with 10–35% and a note; re-confirm on recorded sessions before production use.
