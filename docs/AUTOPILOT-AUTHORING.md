# Writing an autopilot policy

**Audience:** protocol authors adding autopilot to a neurofeedback protocol,
and reviewers checking a policy's clinical numbers before it ships.

**Companion docs:** [`SPEC.md`](./SPEC.md) §4.7, §4.9.5, §4.12, §6.7, §7.10 for
the normative grammar and decision procedure. [`EMBEDDING.md`](./EMBEDDING.md)
"Autopilot advice" for the host-side API. [`IR-JSON.md`](./IR-JSON.md) §3.2 for
the wire format.

This document is the how-to: when to add a policy, how to write one step by
step, what the compiler refuses and why, and a full worked example.

---

## 1. What autopilot is

Refrain watches a training session and, every chunk, decides whether to hold
steady or nudge one clinician-tunable setting — with the numbers behind the
decision shown alongside it. The host (Coherence Recorder today; Companion and
Portal later) displays that decision and applies it only when the clinician
has turned autopilot on for the session **and** the protocol allows that
particular change to be applied automatically. Refrain itself never changes a
setting; it only ever proposes.

A protocol participates at one of three levels:

| In the protocol | Clinician receives |
|---|---|
| Nothing (every protocol today) | **Observations** and, where the protocol's structure makes it unambiguous, **direction hints** — judged against a built-in default target. No proposed value, nothing applied. |
| An `autopilot { }` block with `reward_target` and the other protocol-wide settings, but no per-control policy | The same observations and hints, now judged against the protocol's own target, timing and guard messages instead of the defaults. |
| The block **and** one or more `autopilot = <strategy> { }` fields on controls | Concrete proposed values, and automatic application for the controls whose policy allows it. |

Moving from one level to the next is additive: a protocol can add named
checks and a target band today and add knob policies in a later revision,
without disturbing anything already running.

## 2. Before you write a policy: does this protocol need one?

**A single adaptive check does not need autopilot.** `percentile(target_pct:
k)` passes roughly `100 − k`% of the time by construction — the threshold
chases the signal every window, so there is nothing left for autopilot to
observe. A protocol with exactly one adaptive-percentile check and no other
reward condition gets no useful advice from a policy; skip it.

**It is worth adding for:**
- Protocols with **several reward checks** whose *joint* pass rate is what
  actually varies session to session (alpha/theta's theta-gate-and-crossover
  pair is the canonical case).
- **Baseline-mode (µV) thresholds**, which are fixed values seeded once from
  the patient's own signal and never self-adjust again — these are exactly
  the thresholds that drift out of range across clients and sessions.
- **Plain, non-adaptive targets** like a crossover ratio (`crossover_target`)
  that the clinician currently has to eyeball and retune by hand.

**Guard (inhibit) knobs can be suggested but never auto-applied.** A knob that
feeds an inhibit can carry a policy, but the compiler refuses
`apply = "auto"` on it (rule V4, §6). Loosening a guard automatically would
mean the engine deciding, on its own, to let more artifact or drowsiness
through — that decision stays with the clinician.

## 3. Step 1 — name the reward checks

A policy addresses a reward check by name, so the checks need names first.
Name a check by adding `as "<name>"` to an element of the reward dwell's
`all_of([...])` / `any_of([...])` list:

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

- `as "<name>"` is legal only on an element of a reward dwell condition list.
  Anywhere else is a compile error (rule V15).
- A dwell with a single (non-list) condition can still be named — wrap it in a
  one-element `all_of`: `all_of([<condition> as "x"])`.
- Names must be unique within the reward bundle they are declared in.
  Top-level `reward { }` and each named, block-selectable `reward "<name>" {
  }` bundle (staged protocols) are separate bundles with their own naming.
  Because `fixes`, `tighten_first` and `limiter` entries address a check by
  bare name with no bundle qualifier, reusing a name across two different
  bundles in the same protocol is confusing even though the compiler does not
  currently flag it across bundles — give every check in a protocol a
  distinct name as a matter of style.
- Names are front-end metadata only: they change no IR expression node and no
  tap key. `reward/condition[i]` still means the same thing it always did.
- **Unnamed checks are not an error.** Advice simply refers to them as
  `check 0`, `check 1`, … — 0-based, matching the condition index the engine
  already exposes as `reward/condition[i]`.
- Named checks cannot yet be combined with band fan-out (`bands { }`) or
  per-site fan-out (a `placement { kind = "set" }` montage) — either would
  have to duplicate or rename the checks. The compiler refuses this at
  resolve time; add the checks after removing fan-out, or leave them unnamed
  for now.

## 4. Step 2 — the `autopilot { }` block

A new top-level section, alongside `meta`, `reward`, `controls`, and so on.
Every field is optional except the three provenance fields, which are
required the moment the block exists at all:

```refrain
autopilot {
  evidence  = "expert_opinion"
  citation  = "Peak Mind clinical team (2026). Adapted from Peniston & Kulkosky 1989 practice."
  rationale = "Crossover is rare by nature; a 50-75% target would drive the ratio target to its floor."
  reviewed  = "J. Croall, 2026-09-24"          // optional

  reward_target     = (10%, 35%)
  phases            = ["deep1", "deep2"]
  watch             = 2 min
  between_moves     = 3 min
  equipment_settle  = 60 s
  tighten_first     = ["crossover", "theta"]

  delta = guard { max = 15%; say = "Client may be drifting toward sleep; check alertness." }
  emg   = guard { max = 15%; say = "Muscle artifact; check jaw/neck tension or the electrode." }
}
```

| Field | Type | Meaning |
|---|---|---|
| `evidence` | string, **required** | One of `published`, `clinical_consensus`, `expert_opinion`, `experimental` — a closed set (§7 below). |
| `citation` | string or list of strings, **required** | Source(s) for the numbers below. |
| `rationale` | string, **required** | One or two sentences on why these numbers were chosen. |
| `reviewed` | string, optional | Who reviewed the policy and when. |
| `reward_target` | pair of percents | Target share of clean training time the reward condition should be met. `low < high`, both strictly between 0% and 100%. |
| `phases` | list of phase names | Session phases in which advice may run. Each name must be a declared, non-output-muted phase. |
| `watch` | duration | Clean time required before any judgement is made. |
| `between_moves` | duration | Minimum time between two applied changes, to any knob. |
| `equipment_settle` | duration | Hold period after the host reports an equipment adjustment. |
| `tighten_first` | list of check names | Order to tighten checks in when reward is above target (§5). |
| `<inhibit> = guard { max; say }` | entry | Ceiling (percent) on that inhibit's active share of training time, and the message shown when it's exceeded. The name must be a declared inhibit. |
| `<check> = limiter { say }` | entry | Message shown when this check is the limiter and no policy addresses it. The name must be a named reward check. |

`tighten_first` and the two entry kinds (`guard`, `limiter`) are the reason
checks need names at all — every other field works even on an unnamed
protocol.

**`tighten_first` in one sentence:** loosening always follows the evidence —
the worst-passing check is always the one eased — but *tightening* is a
clinical choice, because several checks can be passing comfortably at once
and the protocol author decides which one to make stricter first.

Every setting has a built-in default (used by every protocol, including ones
with no `autopilot { }` block at all):

| Setting | Default |
|---|---|
| `reward_target` | (50%, 75%) |
| `phases` | Every session phase whose output is not muted; the whole run if the protocol has no `session` block. |
| `watch` | 2 min |
| `between_moves` | 3 min |
| `equipment_settle` | 60 s |
| guard `max` (per inhibit, when no `guard` entry names it) | 15% |
| guard `say` (when the entry — or its default — has none) | "`<inhibit>` guard active `<n>`% of training time." |
| `tighten_first` | none: tighten whichever check currently passes most often |

Defaults are not part of the protocol and are not in the protocol hash — they
live in the engine, versioned as `advisor_version` (currently `"1"`). Every
advice result and every audit event carries `advisor_version`, so a session
record always shows exactly which rule set produced it.

## 5. Step 3 — a policy per knob

A control field, following the same `<field> = <kind> { }` shape `seed` uses:

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
}
```

**Three strategies — the block kind names which one:**

| Strategy | Proposed value | Use it for |
|---|---|---|
| `fixed_step` | `current ± step`, in the knob's own units | Fixed scales: a ratio target, a reward-rate percentage. |
| `proportional_step` | `current × (1 ± step)`, `step` a percent | Voltage (µV) thresholds that vary widely client to client — a fixed-µV step is either too small for a high-baseline client or too large for a low-baseline one; a percentage step scales with the current value. |
| `rebaseline` | The `percentile` of the last `window` of clean samples of derive `from` | "Today's threshold is clearly wrong for this client" — a bigger jump than a single step. **Always suggest-only** (the compiler refuses `apply = "auto"` on it, rule V6): a rebaseline is a large enough move that a clinician should look at it first. |

**Fields common to all three:**

| Field | Required | Meaning |
|---|---|---|
| `fixes` | yes | The named check this knob addresses. |
| `higher_is` | yes | `"harder"` or `"easier"` — what raising this knob's value does to the check. |
| `apply` | yes | `"auto"` (autopilot may apply it, protocol permitting) or `"suggest"` (clinician applies it by hand). |
| `limits` | no | Autopilot's own bounds, which may be **narrower** than the control's `range` (e.g. a 50–90% clinical comfort zone inside a 15–99% technical range). Defaults to `range`; required if the control has no `range`. |
| `round_to` | no | Snap every proposed value to this precision, in the knob's units. |
| `say` | no | Display name used in advice text; defaults to the control's `label`, then its bare name. |
| `watch`, `between_moves` | no | Per-knob overrides of the protocol-wide values. |
| `only_when` | no | Restrict the policy to one mode branch — see below. |
| `citation` | no | A source for this knob's numbers specifically, when it differs from the block-level `citation`. |

The proposed value is always clamped to `limits` and snapped to `round_to`
before it is offered. If clamping and snapping produce the current value
unchanged, the knob is reported "at limit" instead of proposing a no-op move.

**`only_when`.** Some controls only matter in one mode — `theta_threshold_uv`
only feeds the reward check when `threshold_style == "baseline"`; the
percentile-driven `theta_reward_pct` only feeds it under `"adaptive"`. Write
`only_when = threshold_style == "baseline"` on the policy, comparing a `mode`
control to one of its own string choices. Because mode controls are bound at
compile time (the same folding that resolves any other mode-dependent
expression), `only_when` is evaluated once, at compile time: the policy is
kept when the comparison holds for the binding being compiled and silently
dropped — not emitted, not an error — when it doesn't. It never reaches the
IR. **Forgetting `only_when` on a mode-dependent knob is the single most
common mistake** — see the table in §6.

## 6. What the compiler refuses and why

Every rule below names the offending control or entry and says what's wrong
in plain words; none of these degrade to "compiles but autopilot is
disabled" — a protocol that fails any rule does not compile at all.

| # | You wrote | Why it's refused | Fix |
|---|---|---|---|
| V1 | `fixes = "crossover"`, but no check in the protocol is named `"crossover"` | A typo in `fixes` would silently disable the policy — the advisor could never find the check it names | Name the check with `as "crossover"`, or fix the spelling |
| V2 | `fixes = "theta"` on a knob that doesn't appear anywhere in that check's expression, after mode folding | A knob can't fix a check it has no effect on | Point `fixes` at a check the knob actually feeds, or add `only_when` if it only feeds that check in one mode |
| V3 | `higher_is = "easier"` on a knob the compiler traces as making its check harder when raised (or vice versa) | The declared direction disagrees with what the protocol's own expressions say | Fix `higher_is` to match the traced direction |
| V4 | `apply = "auto"` on a knob that feeds a guard (inhibit), in any mode branch | Guards are never loosened automatically — that stays a clinical decision | Use `apply = "suggest"` |
| V5 | A policy on a knob that feeds no reward check at all (a volume-like setting, a band edge) | Whichever check you name in `fixes`, this knob doesn't feed it — V2 catches this the same way it catches a `fixes` naming the wrong check | Remove the policy — this knob isn't a candidate for autopilot |
| V6 | `rebaseline { … apply = "auto" }` | A re-baseline is a bigger jump than a single step; it always needs a clinician to look at it first | Use `apply = "suggest"` |
| V7 | Two different controls both declare `fixes = "crossover"` | One knob per check — the advisor wouldn't know which one to move | Split them with `only_when` if they apply to different modes, or remove one policy |
| V8 | A policy on a `mode`, `boolean`, `enum`, or `placement` control, or on a control that isn't `live_tunable` | Only `number`, `percent`, `voltage`, and `frequency` controls that can change mid-session are eligible | Only add policies to live-tunable numeric/percent/voltage/frequency controls |
| V9 | `limits` outside the control's `range`; no `limits` and no `range` either; or `limits`' low end at or above its high end | Scale error — autopilot's bounds must make sense against the control | Fix the pair, or add a `range` to the control |
| V10 | `step ≤ 0`; a `fixed_step` step in the wrong units (e.g. `%` on a `number` control); a `proportional_step` step that isn't a percent, or is `≥ 100%` | Scale error | Use the control's own units for `fixed_step`; a percent under 100% for `proportional_step` |
| V11 | `rebaseline.from` names something that isn't a declared derive, or one whose unit doesn't match the knob's; `percentile` outside 1–99 | `from` has to be a real, unit-compatible signal to measure a percentile from | Name a real derive with matching units; pick a percentile from 1 to 99 |
| V12 | `guard`/`limiter` entry names, or a `tighten_first` name, that doesn't match a declared inhibit or check | A typo here would silently disable a safety message | Fix the name, or declare the check/inhibit it should have referred to |
| V13 | A control policy with no top-level `autopilot { }` block at all; or a block missing `evidence`, `citation`, or `rationale`; or `evidence` outside the closed set | No unsourced policy — every automated suggestion must be traceable to a reason | Add the block with all three provenance fields, using one of the four `evidence` levels |
| V14 | `reward_target` malformed, or outside (0%, 100%); any duration ≤ 0 | Scale/range error | Fix the pair or the duration |
| V15 | `as "<name>"` anywhere other than an element of a reward dwell's `all_of`/`any_of` list; the same name used twice in one bundle | `as` only means "name this reward check" in that one position | Move the label, or rename the duplicate |
| V16 | `reward_target` or a knob policy on a protocol with no `event = dwell(...)` reward condition, or one using a weighted composite reward | There's no per-sample condition for autopilot to judge the pass rate of | Remove the policy, or add a dwell-based reward condition |
| V17 | `only_when` that isn't `<mode control> == "<choice>"` (or `!=`), or that names a choice the mode doesn't declare | `only_when` only understands a direct comparison against one of the mode's own choices | Rewrite as `<mode> == "<choice>"`/`!=`, using a choice the mode actually declares |

The two most common mistakes in practice: **forgetting `only_when` on a
mode-dependent knob** (it fails V2, since the knob only feeds its check in
one branch) and **writing `apply = "auto"` on a guard knob** (V4) out of habit
from an ordinary reward knob.

## 7. Provenance and citations

Every `autopilot { }` block requires `evidence`, `citation`, and `rationale`
— there is no unsourced autopilot policy.

**The `evidence` scale:**

| Level | What qualifies |
|---|---|
| `published` | The numbers come from a peer-reviewed source, cited in `citation`. |
| `clinical_consensus` | A written team protocol or guideline, not necessarily published. |
| `expert_opinion` | An experienced clinician's judgement, not formally written up elsewhere. |
| `experimental` | Untested numbers. Legal to ship — hosts should visibly label advice from an `experimental` policy so a clinician can weigh it accordingly. |

**`citation`** is one string or a list of strings. Cite the protocol's clinical
origin *and* the source of the autopilot numbers *separately* — they are
often not the same source:

- A paper: `"Author A, Author B (Year). Title. Journal, vol(issue), pages. doi:…"`
- A team's own written protocol: `"<Team> clinical protocol <id>, <date>"`
- Numbers adapted from an existing tool, not yet re-validated on this signal
  path: `"Adapted from <source>; re-confirm on recorded sessions"`

**`rationale`** is one or two sentences on *why* these particular numbers —
not a restatement of what they are.

**`reviewed = "Name, YYYY-MM-DD"`** is optional, for when someone other than
the author checked the policy before it shipped.

**A per-knob `citation`** is available on any control's `autopilot = ... { }`
block, for the case where one knob's step size or bounds come from a
different source than the rest of the policy.

**Provenance is inside the protocol hash.** Changing a citation, a rationale,
or any autopilot number is a protocol change like any other — bump
`meta.version` when you do.

## 8. Worked example

`examples/alpha_theta_autopilot.refrain` is the reference: a copy of the
production `alpha_theta` protocol with two named checks and a full policy
added. Its `autopilot { }` block:

```
autopilot {
  evidence      = "expert_opinion"
  citation      = [
    "Peniston & Kulkosky 1989, 1991 (protocol)",
    "Peak Mind clinical team 2026: step sizes and target band adapted from Coherence Recorder guidance v1",
  ]
  rationale     = "Crossover is rare by nature; a 50-75% target would drive the ratio target to its floor. Tighten crossover first, one 0.05 step at a time."
  reward_target = (10%, 35%)
  phases        = ["deep1", "deep2"]
  watch         = 2 min
  between_moves = 3 min
  tighten_first = ["crossover", "theta"]
  delta = guard { max = 15%; say = "Client may be drifting toward sleep; check alertness." }
  emg   = guard { max = 15%; say = "Muscle artifact; check jaw/neck tension or the electrode." }
}
```

Line by line: `expert_opinion` because the target band (10–35%) was carried
over and adapted from Coherence Recorder's own guidance engine rather than
published outright; two citations, because the *protocol* traces to Peniston
& Kulkosky but the *autopilot numbers* trace to the recorder team. Advice
only runs in the two eyes-closed training phases (`deep1`, `deep2`) — never
during `settle`, `rest1`, or `cooldown`, which are all output-muted anyway.
`tighten_first = ["crossover", "theta"]` means: when reward is comfortably
above target, tighten the crossover ratio before touching the theta gate.
Both guards use the built-in 15% ceiling implicitly (no explicit `max` needed
beyond what's shown) with protocol-specific messages instead of the generic
default text.

Three knob policies, one per relevant control:

```
crossover_target = number {
  ...
  autopilot = fixed_step { fixes = "crossover"; step = 0.05; higher_is = "harder";
                            limits = (0.5, 1.0); apply = "auto"; round_to = 0.01;
                            say = "Crossover target" }
}
theta_reward_pct = percent {
  ...
  autopilot = fixed_step { fixes = "theta"; step = 5; higher_is = "harder";
                            limits = (15, 40); apply = "suggest";
                            only_when = threshold_style == "adaptive";
                            say = "Theta reward rate" }
}
theta_threshold_uv = voltage {
  ...
  autopilot = proportional_step { fixes = "theta"; step = 10%; higher_is = "harder";
                                   apply = "suggest"; round_to = 0.1 uV;
                                   only_when = threshold_style == "baseline";
                                   say = "Theta threshold" }
}
```

`crossover_target` is a plain ratio target, so `fixed_step` with a 0.05 step
is enough, and it's `apply = "auto"` because a small, bounded ratio nudge
carries low risk. The theta gate is split by `threshold_style`:
`theta_reward_pct` (an adaptive percentile target) under `"adaptive"` mode,
`theta_threshold_uv` (a fixed µV threshold, seeded from the patient's own
signal) under `"baseline"` mode — `proportional_step` there, because a
10%-of-current step scales correctly whether this client's baseline theta
sits at 4 µV or 14 µV. Both theta-side policies are suggest-only: the theta
gate change is more clinically visible than the crossover ratio, so it goes
through the clinician.

**Two advice results**, generated by feeding this exact compiled protocol
through the advisor with scripted evidence (the same technique
`tests/test_advisor.py` uses), so the numbers below are real output, not
hand-typed examples.

*Reward is too strict* (theta passes 100% of clean time, crossover only 5%
— tighten_first doesn't apply here since reward is below target, so the
worst-passing check, crossover, is eased):

```json
{
  "advisor_version": "1",
  "id": "adv-0001",
  "state": "adjust",
  "level": "policy",
  "reason": "too_strict",
  "message": "Reward met 5% of clean time (target 10-35%). The limiter is crossover. Lower Crossover target 0.60 -> 0.55.",
  "limiter": { "check": "crossover", "pass_rate": 0.05 },
  "control": {
    "name": "crossover_target", "label": "Crossover target", "units": "", "round_to": 0.01,
    "current": 0.6, "proposed": 0.55, "direction": "easier", "strategy": "fixed_step",
    "auto_allowed": true
  },
  "evidence": {
    "reward_rate": 0.05, "target": [0.1, 0.35],
    "checks": { "theta": 1.0, "crossover": 0.05 },
    "guards": { "delta": 0.0, "emg": 0.0 }
  }
}
```

*Reward is too easy* (both checks pass 100% of clean time — `tighten_first`
picks `crossover` first):

```json
{
  "advisor_version": "1",
  "id": "adv-0001",
  "state": "adjust",
  "level": "policy",
  "reason": "too_easy",
  "message": "Reward met 100% of clean time (target 10-35%). The limiter is crossover. Raise Crossover target 0.60 -> 0.65.",
  "limiter": { "check": "crossover", "pass_rate": 1.0 },
  "control": {
    "name": "crossover_target", "label": "Crossover target", "units": "", "round_to": 0.01,
    "current": 0.6, "proposed": 0.65, "direction": "harder", "strategy": "fixed_step",
    "auto_allowed": true
  },
  "evidence": {
    "reward_rate": 1.0, "target": [0.1, 0.35],
    "checks": { "theta": 1.0, "crossover": 1.0 },
    "guards": { "delta": 0.0, "emg": 0.0 }
  }
}
```

(Both trimmed to the fields relevant here; the live `advice()` result always
carries every key described in `EMBEDDING.md`, with unused ones `null`.) A
guard example, EMG active 20% of a 120 s window against a 15% ceiling:

```json
{ "state": "hold", "reason": "guard",
  "message": "Muscle artifact; check jaw/neck tension or the electrode. (emg guard active 20% of training time)." }
```

## 9. Existing protocols

**No change is required.** Every protocol in this repo and in
`refrain-protocols` compiles unchanged — identical IR-JSON, identical
`content_hash` — and stays fully manual: a clinician sees exactly what they
see today. Once a host calls `advice()`, these protocols additionally get
**observations** (the current reward rate, checks, and guard activity against
the built-in default target) and, for protocols whose limiting check has an
unambiguous single knob, a **direction hint** — never a proposed value, never
an automatic change.

Opting an existing protocol in is incremental and can stop at any step:

1. **Add names** to the reward checks (`as "<name>"`). This alone upgrades
   observations from "check 0, check 1" to real names, and may be enough to
   produce direction hints where none existed before you named the limiting
   check.
2. **Add `reward_target`** (and any other protocol-wide settings) in an
   `autopilot { }` block, still with no knob policies. Observations and hints
   now judge against the protocol's own numbers instead of the 50–75%
   default.
3. **Add knob policies** where they're worth it (§2), one control at a time.
   Each addition is a normal protocol change — bump `meta.version`, since
   provenance is inside the hash.

Each step is independently useful and none of them require the others.
