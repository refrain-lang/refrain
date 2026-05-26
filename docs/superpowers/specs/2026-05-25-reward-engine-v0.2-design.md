# Reward engine v0.2 — weighted multi-component composite — design

> Status: approved design (brainstorm complete), ready for an implementation plan.
> Builds on `main` (tag v0.5.0). This is the **first IR-JSON wire bump (v0.1 → v0.2)**
> and the first change to the Rust core's reward model.
> Unifies two previously-separate threads: independent per-site feedback (was "B2")
> and the vector aggregate (was "Mode 2b") — both fall out of one weighted-composite
> model.

## Goal

Let a protocol's feedback be driven by a **weighted combination of several reward
and inhibit components**, each with a clinician-tunable weight — the standard
multi-metric / summary / z-score model in clinical NF (BrainMaster, Cygnet,
BioExplorer). Today Refrain has a single reward computed once per chunk; this
generalizes it to a composite over named components, while keeping every existing
single-reward protocol working unchanged.

## Why this is one feature, not two

A weighted aggregate across a replicated set ("mean/weighted SMR across C3,Cz,C4")
and a weighted aggregate across distinct bands ("SMR up, theta down, hibeta down")
are the **same operation** — a weighted reduction over component signals. So
"independent per-site feedback", "vector aggregate across a set", and "weighted
multi-band training" are all expressed through one `reward.combine` family plus
named components. The set-replication fan-out (shipped in v0.4.0) just produces
more components; the combine step is identical.

## Decisions locked in brainstorming

1. **Named reward blocks.** Multiple `reward "<name>" { … }` components are
   allowed; the bare `reward { … }` remains the default/aggregator (full
   back-compat). Outputs may reference `reward.<name>.*` and the aggregate
   `reward.composite` / `reward.event`.
2. **Weighted composite is a weighted average of per-component success in [0,1].**
   `composite = Σ wⱼ·sⱼ / Σ wⱼ`, where a reward component contributes
   `sⱼ = signalⱼ` and a suppress-inhibit component contributes `sⱼ = 1 − signalⱼ`.
   Reads as "weighted % of targets met."
3. **Weights are ordinary numeric controls** — author `default`, `resolve(bindings=)`
   override, and `set_control(...)` live tuning all come for free from the existing
   controls system. No new weight mechanism.
4. **Two inhibit behaviors.** A *weighted suppress band* (`−` contribution to the
   composite, graded) and the existing *hard gate* (`mute`/`freeze`/`flag`, for
   artifact rejection) — an author picks per inhibit. Hard gates gate the whole
   composite and are independent of the weighting (you must not leak graded reward
   during an EMG burst).
5. **`combine` family** over the components: `all`/`any` (boolean condition join,
   shipped), **`weighted`** (the composite above — new), `independent` (emit N
   separate per-component/per-site feedbacks rather than aggregating — new).

## Design

### 1. Authoring syntax

```refrain
controls {
  w_smr   = 1.0 within [0, 4]      // weights are plain numeric controls →
  w_theta = 0.6 within [0, 4]      //   default + deploy-override + live-tune
}

reward  "smr"   { signal = sigmoid("smr_env",   midpoint: 6 uV, steepness: 1); weight = w_smr }
inhibit "theta" { signal = sigmoid("theta_env", midpoint: 8 uV, steepness: 1); weight = w_theta }  // suppress band
inhibit "emg" {                                      // hard artifact gate — no weight, existing v0.1 form
  metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
  threshold = percentile(target_pct: 95, window: 2 min)
  action    = mute(release: 200 ms)
}

reward {
  combine = "weighted"                                 // form the composite
  event   = dwell(condition: above(reward.composite, 0.7), duration: 250 ms)
}
output {
  audio_gain  = reward.composite                       // weighted score, [0,1]
  audio_chime = reward.event
}
```

- A **reward/inhibit component** is a named block with a `signal` (an expression
  producing a `[0,1]` success metric — typically `sigmoid(...)`) and an optional
  `weight` (a numeric control ref; default 1.0). An `inhibit` with the existing
  `metric` + `threshold` + `action = mute/freeze/flag(...)` form is a *hard gate*
  (v0.1 semantics) and takes no weight; the resolver disambiguates by field
  presence (a `signal` field ⇒ suppress band, an `action` field ⇒ hard gate) and
  rejects a block that mixes the two forms.
- The top-level `reward { }` is the **aggregator**: `combine` selects the strategy
  and `event`/`continuous` define the patient-facing aggregate, referencing
  `reward.composite`.
- **Back-compat:** a lone `reward { continuous = …; event = … }` with no named
  components and no `combine` is exactly today's single-reward protocol — the
  composite collapses to that one expression.

### 2. The composite (`combine = "weighted"`)

`reward.composite` (a `[0,1]` stream) is computed each chunk as the weighted
average of per-component success:

```
composite = ( Σ_r w_r · signal_r  +  Σ_i w_i · (1 − signal_i) ) / ( Σ_r w_r + Σ_i w_i )
            reward components r                 suppress-inhibit components i
```

- All component `signal`s are `[0,1]` (the resolver type-checks this; a non-[0,1]
  signal is a resolve error — wrap it in `sigmoid`/`linear`).
- If all weights are 0 → composite is undefined → resolve error (at least one
  positive weight required, validated against control ranges where statically
  knowable; otherwise a runtime guard).
- `reward.event`/`reward.continuous` and `output` bindings may reference
  `reward.composite`; `reward.<name>.signal` exposes an individual component (for
  taps / per-component output).

### 3. `combine` family (applied uniformly by the fan-out + aggregator)

| `combine` | result |
|---|---|
| `all` / `any` | one reward; `event` dwell condition = `all_of`/`any_of` over per-component/per-site conditions (shipped, unchanged) |
| `weighted` | one reward; `reward.composite` = the weighted average above |
| `independent` | **N** rewards: each component/site becomes a named reward `reward.<name>` (or `reward.<channel>@<site>`) with its own `event`/`continuous`, and `output` bindings fan out to `<channel>@<name>` |

For a **replicated set** (v0.4.0 Mode 2a), the fan-out produces one component per
site; `combine` then aggregates them — `weighted` gives the per-site weighted mean
(the old "Mode 2b"), `independent` gives per-site feedback (the old "B2"),
`all`/`any` give the combined boolean reward (already shipped). Per-site weights
are controls too (e.g. `w@<site>`, defaulted, deploy/live-overridable).

### 4. Hard gates (artifact rejection) — unchanged role, clarified scope

`inhibit "<x>" { metric = …; threshold = …; action = mute|freeze|flag(...) }`
keeps the v0.1 semantics and gates the **whole composite / all outputs**. It is orthogonal to weighting: a hard
gate produces a boolean that suppresses output; it never contributes a graded term.
This keeps the composite interpretable ("low score = brain, not biceps").

### 5. Weights as controls

A component's `weight` references a numeric control (or is a literal, treated as a
fixed control with that default). Because it's a control:
- author sets the `default` (and `range`),
- deploy overrides via `resolve(ast, amp, bindings={"w_theta": 0.4})`,
- the clinician retunes live via `set_control("w_theta", 0.4)` — and the composite
  recomputes with the new weight (warm-restart, like the percentile/sigmoid
  live-tune already supported).

## IR / wire / Rust impact — the v0.2 bump

This is the first change to the reward model in the IR, the wire format, and the
Rust core. **`IR_JSON_VERSION` → `"0.2"`**; a new `refrain-core/schema/ir-json-v0.2.schema.json`.

- **IR:** `IRProtocol.reward: IRReward` → a structure carrying *named components*
  (reward + suppress-inhibit, each with a `weight` control ref + `[0,1]` signal),
  the `combine` mode, and the aggregator `event`/`continuous`. `IRRewardField`
  gains an optional component/aggregate name (`reward.<name>` / `reward.composite`).
  Hard-gate inhibits stay in the existing inhibit structure.
- **Evaluator (Python):** compute each component signal per chunk, then the
  weighted-average composite (or `all_of`/`any_of`, or N independent rewards);
  apply hard gates as today. Live `set_control` on a weight updates the running
  composite.
- **Rust core:** mirror the above — a weighted-reduction over component signals.
  Parity-gated against Python as usual.
- **Back-compat:** a v0.1 single-reward protocol serializes to a v0.2 IR with one
  reward component, weight 1.0, `combine` absent → byte-equivalent runtime
  behavior. The Rust core reads both schema versions (v0.1 fixtures stay green).
- **Drift gate:** `check_equivalence.py` extends to validate v0.2 fixtures + the
  new schema, alongside the retained v0.1 ones.

## Out of scope (deferred)

- Per-metric weighting *within* a component (a component is one signal).
- Per-reward (rather than global) hard-gate targeting.
- Nonlinear composites (only the weighted average in v0.2).
- A migration that rewrites existing assets to v0.2 (they stay v0.1; new/edited
  protocols using components emit v0.2).

## Open questions for review

1. **Composite normalization** — weighted *average* of per-component `[0,1]`
   success (proposed; keeps `[0,1]`, reads as "% of targets met"). Alternative:
   signed weighted sum with clamping. Confirm the average.
2. **`event` on the composite** — `dwell(above(reward.composite, 0.7), …)` reuses
   the existing condition/dwell machinery on the new `reward.composite` stream.
   Confirm vs. a dedicated composite-threshold form.
3. **`independent` + weights** — when `independent`, weights are presumably ignored
   (each feedback is standalone). Confirm, or define a meaning (e.g. per-feedback
   gain).
4. **`reward.composite` naming** — `reward.composite` vs reusing `reward.continuous`
   for the aggregate. Proposed `composite`; `continuous` kept for the single-reward
   back-compat case.

## Verification / exit criteria

- A weighted protocol (≥1 reward + ≥1 suppress inhibit, distinct weights) computes
  `reward.composite` as the weighted-average success; Python ≡ Rust at machine
  precision; live `set_control` on a weight moves the composite.
- A hard-gate inhibit still mutes the composite/output exactly as in v0.1.
- A v0.1 single-reward protocol round-trips byte-identically (back-compat fixture
  in the drift gate); all existing v0.1 golden vectors stay green.
- A replicated set with `combine="weighted"` yields the per-site weighted mean;
  with `independent`, N per-site feedbacks; with `all`/`any`, the shipped boolean
  reward.
- `IR_JSON_VERSION == "0.2"`; v0.2 schema published; drift gate validates both
  schema versions. Full `pytest` + `cargo test` green. Version → `0.6.0`.
