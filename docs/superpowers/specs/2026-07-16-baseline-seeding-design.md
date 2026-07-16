# Baseline seeding — design

**Date:** 2026-07-16
**Status:** design approved, plan pending
**Re:** hosts' "Request for scoping: first-class baseline seeding in Refrain"
**Lands in:** v0.15.0 (proposed)

A protocol can declare that a control derives its value from a percentile of a
named window of its own signal, measured during warmup and held for the run.
The engine executes it, so every host gets identical behaviour without
implementing anything.

---

## 1. Why

### 1.1 The ask

Baseline-variant protocols set a threshold from the patient's own resting
signal rather than adapting continuously. Something must watch the signal during
warmup, take a percentile, write it into the control, and stop. Today that
"something" is `coherence-recorder/recorder/backend/nf/baseline_seed.py` plus a
name-keyed dict at `manifest.py:184`. Anything outside the IR must be
reimplemented by every host.

### 1.2 The premise that expired

`docs/superpowers/specs/2026-05-29-staged-protocols-design.md` §"R5: out of
scope" explicitly designed this out of the language:

> The "one shared baseline" is **not** a Refrain ask. The recorder buffers warmup
> telemetry, takes a percentile of the settled tail per signal, and calls
> `set_control(name, value)` on each block's control... This rides entirely on
> substrate already shipped in v0.6.3.

That reasoning was correct **given its premise**: that every host can
orchestrate. Coherence Companion runs pre-baked IR-JSON through refrain-core
with no Python and no compiler. It structurally cannot. R5 is re-opened because
its premise no longer holds, not because it was wrong.

A second argument, which the hosts did not make: the baseline is currently
invisible to `protocol_hash`. Two sessions with identical hashes can have run at
different thresholds. For a project with CRED-nf reproducibility ambitions that
is a gap the engine should close regardless of host count.

### 1.3 The hosts' diagnosis is wrong in three ways

Verified against `refrain-protocols`, `coherence-recorder`,
`coherence-companion`, `coherence-portal`.

1. **There was no rename.** The claim is that a Phase-2 collapse renamed
   `smr_threshold_uv` → `thr_uv`, breaking the table. `thr_uv` was the generic
   family's control name from the initial seed commit; `git log -S"smr_threshold_uv"`
   over the protocol files hits only BrainBit. **The bug predates the collapse**
   — pre-collapse baseline files hit the same fallback.

2. **"The intended 40th" is wrong for all 16.** By the table's own stated
   principle (match the adaptive default), the target is each protocol's own
   `reward_pct`: **70 for the 10 up-train, 30 for the 6 down-train**. Adding
   `thr_uv: 40.0` would leave all sixteen wrong and invert the six down-train.

3. **A name-keyed table structurally cannot fix this.** Sixteen protocols share
   one control name and need two different percentiles. This is not a stale-table
   bug; it is a category error. **This is the strongest argument for the feature
   and the hosts did not make it.** They came one sentence away: *"The table only
   exists to defeat a file split you already fixed... Once `mode` puts both
   controls in one file, that value is already in scope."*

### 1.4 The incident's actual cause

Not the table. **Coherence Companion has no seeding executor at all.**
`apps/cc-mobile/src/nf/live/buildManifest.ts:87` ports the dict verbatim, writes
`baseline_seed` into the manifest, and nothing on-device ever reads it. The only
`setControl` caller is the relay downlink (`applyDownlink.ts:22`). The table-miss
story applies to the 16 generic protocols on **desktop**.

Consequence: the hosts' proposed stopgap — a throwaway mobile port — would port
an unfixable table into a third repo. It must not be built.

### 1.5 Nothing is released

All products are in private development. No deployed engines, no longitudinal
patient data. This removes the deployment-ordering risk, the migration
validation gate, and the need for any interim. It also makes the version gate
free exactly once — now.

---

## 2. Design

### 2.1 Surface

```refrain
controls {
  reward_pct = percent {
    default      = 70
    range        = (50, 90)
    live_tunable = true
  }

  thr_uv = voltage {
    default      = 2.0 uV
    range        = (0.5 uV, 10 uV)
    live_tunable = true
    seed = percentile {
      from       = "env"        // a derive name
      window     = 60 s         // the trim
      target_pct = reward_pct   // binds to the control
    }
  }
}
```

Read aloud: *`thr_uv` gets its value from the last 60 s of the `env` signal
during warmup, at the percentile given by `reward_pct`.*

**Grammar: no changes required.** Controls are already written
`<name> = <kind> { ... }` (`env_center = frequency { ... }`,
`threshold_style = mode { ... }`), which is `assignment: NAME "=" expression`
over `block_expr: NAME? block`. `seed = percentile { ... }` is the identical
shape one level down, so the parser needs no new rule and no new keyword.

Two consequences of following the house pattern:

- **The statistic is the block kind**, mirroring how a control's type is its
  block kind. A future `seed = median { ... }` or `seed = mean { ... }` needs no
  syntax change — only a resolver case.
- **`window = 60 s`, not `last 60 s`.** This reuses the existing
  `percentile(window: 2 min)` convention, where a window is already understood as
  the trailing N seconds. The trim is structural: the buffer is a
  `deque(maxlen=window_samples)`, so "the last 60 s" is simply what it holds. The
  word `last` would be redundant.

**`target_pct: reward_pct` is the load-bearing line.** `reward_pct` already
exists in these protocols and is already correct per protocol (70 up / 30 down).
Binding to it dissolves the 70/30 problem: no second copy of the value exists, so
none can drift. Because `reward_pct` is `live_tunable`, the seed tracks a
clinician who retunes it — something no static table can do.

### 2.2 Why the control, not the threshold or the phase

**Control (chosen).** The thing computed is a control's value; the rule lives
with the control. Requirement 7 ("control-seeding, not threshold-seeding") is
satisfied natively, and `smr_graded_baseline_cz`'s `smr_anchor_uv` /
`theta_anchor_uv` — sigmoid anchors, not thresholds — need no new vocabulary
because anchors are just controls.

**Phase — rejected.** Phases have no `warmup` kind: `IRPhase` is
`name/duration_ms/output_muted/mode/block` (`ir.py:289`), and "warmup" is purely
positional (index 0 + `output_muted`). Keying clinical semantics on a naming
convention breaks silently if phase 0 is renamed or preceded. It also makes
"seed once" a validation rule rather than a structural fact, and relocates each
control's policy away from the control — a milder version of the complaint that
started this.

**Threshold constructor — rejected.** Would get mode-composition free (the
untaken branch is deleted at AST level), but: it violates requirement 7; the
sigmoid-anchor case needs a parallel mechanism; and fatally, the produced value
would have **no control name a host could address**, so `set_control` could not
reach it. That breaks requirement 5 (the clinician's slider is the safety valve)
and requirement 9 (the seed report is keyed by control name).

**Dropped as YAGNI:** an `at =` field. Requirement 6 fixes the moment at
warmup→run; the engine's `state` makes that transition exactly once
(`test_state_never_returns_to_warmup`). There is no second moment to name.

Also dropped: `min_window` / `min_samples`. Resolve-time validation (§2.4) covers
the only case an author controls. The residue — host advanced early — does not
warrant an author-declared tolerance.

### 2.3 IR

`IRControl` gains one optional field:

```python
@dataclass(frozen=True, slots=True)
class IRControlSeed:
    statistic: str        # "percentile" (v1: the only one) — from the block kind
    from_entity: str      # canonical "derive/env"
    window_samples: int   # baked at compile rate
    target_pct: IRExpr    # existing control_ref or number node
    loc: Loc | None = None
```

`IRControl` (`ir.py:264`) gains `seed: IRControlSeed | None = None`. It is a
frozen slots dataclass whose optional fields are all defaulted, so appending one
more is additive for every existing construction site.

**`target_pct` reuses existing `Expr` variants deliberately.** `Expr` is an
internally-tagged serde enum (`refrain-core/src/ir.rs:209`); an unknown *variant*
fails the entire document load, not just that node. The closed union is defended
on purpose (`tests/test_ir_json_schema.py:95`). New *fields* are free; new *node
kinds* are fatal. **Never add a node discriminator.**

On the wire, `"seed"` is emitted **only when present**, following the emitter's
omit-when-unused idiom (`ir_json.py:229`, `:313`). Consequence: **every protocol
that does not seed keeps a byte-identical `content_hash`.**
`_protocol_ir_version` (`ir_json.py:56`) returns `"0.3"` only for seeding
protocols; a new `ir-json-v0.3.schema.json` ships alongside.

### 2.4 Resolve

Four additions:

1. Validate `from` names a real derive; `value` is `percentile(target_pct:)` over
   a number or a `percent` control ref.
2. Bake `window_samples` at the compile rate.
3. **Warmup-fits check.** Phase durations must be numeric literals
   (`resolver.py:1758`), so the compiler always knows warmup's length. A `window`
   longer than phase 0's duration is a `ResolveError`. This deletes the largest
   failure class at compile time and answers the hosts' "warmup too short"
   question: *it should not compile*. It also fixes their symptom #1 — the 90 s
   warmup stops being hand-tuned against `_SETTLED_WINDOW_S` in another repo and
   becomes a compiler-enforced constraint.
4. **Dead-seed elimination (mandatory).** Drop any seed whose control has no
   surviving `control_ref` in the resolved IR. Control declarations survive mode
   folding even when unreferenced (`ir_json.py:414`), so without this an
   *adaptive* artifact carries a live seed rule for the orphaned `thr_uv` — and
   under fail-closed (§2.6) a noisy warmup would mute an adaptive session that
   never wanted a baseline. This is what makes requirement 8 exact.

### 2.5 Runtime — a polled latch

Per seeded control: a `PercentileImpl` buffer, plus `armed` and `fired` flags.
Evaluated at the **top of `_process_chunk`**, matching the per-chunk-read-the-cursor
grain of every other phase-dependent behaviour in both engines. There is no
phase-transition hook in either engine and this feature does not add one.

- `state == "warmup"` and `armed` → ingest the `from` chunk, skipping non-finite.
- `state == "run"`, `armed`, not `fired` → compute, write the control via the
  existing `_control_deps` → `AbsoluteThresholdImpl.update_control` path,
  `fired = true`, drop the buffer.
- host `set_control` on a seeded control while `armed and not fired` →
  `armed = false`, status `disarmed_by_host`.

**Key on `state`, not `phase_index`.** `state` goes `warmup → run` exactly once
(tested invariant), which is requirement 6 for free: one baseline anchors all
four staged blocks, because every derive runs during warmup and every threshold
steps every chunk regardless of active block. Staged rests are `output_muted`
with `state == "run"`, so they cannot re-arm.

**Fire at the top of the chunk.** `_advance_if_due` runs *after* `_process_chunk`
(`eval_.py:894`), so the flip to `run` lands between chunks. Firing at the top of
the first run chunk means the seeded value is live before any threshold steps —
no one-chunk lag, and no dependence on the deliberately-skewed `current_phase()`
contract.

Because the engine writes the control only at that single moment, a clinician who
moves the slider afterward can never be overwritten. No rule is needed; there is
no second write.

### 2.6 Errors

| Case | When caught | Behaviour |
|---|---|---|
| `window` > warmup duration (timed phase) | **resolve** | `ResolveError` — will not compile |
| Host advanced out of warmup early | runtime | **fail closed** — mute output for the session |
| Open-ended warmup cut short | runtime | **fail closed** |
| `start(skip_warmup=True)` on a seeding protocol | runtime | **fail closed** (see below) |
| Non-finite samples | runtime | skipped on ingest; not counted |
| Clinician wrote the control during warmup | runtime | **disarm** — seed steps aside, session runs normally |

**The seed requires a full window.** Having dropped `min_window` (§2.2), the rule
is unambiguous: fewer than `window_samples` finite samples ingested at the
warmup→run edge ⇒ `insufficient_samples` ⇒ fail closed. This is not brittle in
practice, because §2.4's resolve check guarantees a timed warmup is longer than
the window, so the buffer is full well before the edge. The only shortfalls are
the rows above, all of which are "the measurement did not happen".

**`skip_warmup=True` fails closed, deliberately.** A host that skips warmup has
skipped the measurement, so a protocol that declared a seed cannot honour it.
Note the consequence for the conformance corpus: every existing fixture is
generated with `skip_warmup=True` (`docs/CONFORMANCE.md` §3), so a seeding
fixture **must** use `skip_warmup=False` — this is not merely preferable, it is
the only way the feature is exercised at all. See §5.

**Fail closed** means the engine suppresses reward output for the rest of the
session via the existing per-chunk `suppress_output` path, and reports the
status. Rationale: the gentler alternative has already been tried and failed. The
portal *already* renders an amber "baseline not seeded — using the protocol
default" badge (`widgets.tsx:409`), and a session still ran 30 minutes at 100%
reward. A warning hosts are free to ignore is not a safety mechanism.

**`disarmed` is not `failed`.** Failed = the protocol promised a measurement and
the engine could not deliver; nobody decided anything; mute. Disarmed = a human
made a deliberate clinical judgement; run normally. Conflating them would mute
sessions a clinician had just taken responsibility for.

**NaN skipping is not optional.** Rust *panics* on a NaN in a percentile buffer
(`dsp.rs:533`, `.expect("NaN in percentile buffer")`) while Python propagates it.
Same input, crash versus poison, untested today.

### 2.7 Observability

A `seed_report()` accessor, keyed by control name:

```jsonc
{
  "thr_uv": {
    "status": "seeded",          // seeded | insufficient_samples | disarmed_by_host
    "value": 2.03,
    "source": "derive/env",
    "target_pct": 70.0,
    "n_samples": 14847,
    "window_s": 60.0,
    "at_time_s": 90.0
  }
}
```

**Deliberately not a tap.** `refrain-core/tests/taps.rs:70` asserts exact tap
key-set equality against Python fixtures, so a new key means regenerating every
`*.taps.json` fixture. v0.8.0 made precisely this call for `export_state()`
("separate from `last_taps()` so the strict tap key-set parity test is
untouched"); the same reasoning applies.

This populates the portal's existing `baseline_seeds` field with something true,
replacing the placeholder "default" — which is indistinguishable from "we
measured this patient and 2.0 is genuinely their number."

Four-site plumbing: `eval_.py`, `eval.rs`, `python.rs`, `mobile.rs`, plus
regenerated Swift/Kotlin bindings (CI drift gate, `.github/workflows/mobile.yml`).

### 2.8 Version skew

The hosts asked for an equivalent to the `bindings` fail-closed echo. **None
exists on this hop, and without one the feature would recreate the bug it fixes.**

The Rust core ignores unknown fields (no `deny_unknown_fields` anywhere; every
post-v0.1 field is `#[serde(default)]`) **and never reads `refrain_ir_version`** —
it does not even declare the field. An old engine handed a seeding protocol would
run it at the placeholder default, silently. That is the incident, bit for bit.

**Fix: implement SPEC §9.3**, which already specifies the behaviour and was never
built: *"a protocol whose schema is newer than the runtime supports is refused at
load with a clear diagnostic."* Combined with per-protocol version tagging
(§2.3), the blast radius is exact — non-seeding protocols stay at 0.1/0.2 and
keep loading everywhere; seeding protocols are refused **loudly** by old engines,
at load, before a patient is connected.

**Ship it now.** Nothing is released, so the gate becomes the floor at zero cost.
This is the cheapest it will ever be, and the window closes on its own.

Additionally: echo the seeded control names in compile response metadata
(`meta.seeds`), mirroring `meta.bindings`, so the portal gets a positive
compile-time signal rather than inferring from the version tag.

---

## 3. Prerequisites

Both worth doing on their own merits; both sit on this feature's path.

1. **Fix the Rust expression-position control-ref divergence.**
   `refrain-core/src/eval.rs:1902` compiles a `control_ref` in a plain value
   position to `CNode::Const(*default)` — frozen forever, with `set_control`
   returning a **no-op success**. Python evaluates it live (`eval_.py:1399`). All
   four control refs in the parity fixtures sit in recognised parameter slots, so
   the corpus **structurally cannot catch this**. `Evaluator.live(backend="auto")`
   prefers Rust whenever the wheel is importable → passes tests, does nothing in
   production. Fix, and add a fixture with an expression-position control ref.

2. **Implement the SPEC §9.3 version gate** (§2.8).

---

## 4. Cross-repo plan

| Repo | Work |
|---|---|
| **refrain** | The feature (§2), both prerequisites (§3), docs |
| **refrain-core** | Runtime mirror, version gate, `seed_report` on PyO3 + uniffi, regenerated Swift/Kotlin bindings |
| **refrain-protocols** | Add `seed { }` to 21 baseline protocols (5 BrainBit + 16 generic); the 16 generic get their first *correct* percentile via `reward_pct` |
| **coherence-recorder** | **Delete** `baseline_seed.py` and `_BASELINE_SEED_PERCENTILES`. One clean cut — no dual path, nothing released |
| **coherence-companion** | Delete the dead `BASELINE_SEED_PERCENTILES` port in `buildManifest.ts`. Read `seed_report()` for display. **Build no stopgap.** |
| **coherence-portal** | Populate `baseline_seeds` from `seed_report()`. Fix the `ready`-state widget divergence (§6) |

**Ordering.** The prerequisites (§3) land first and independently — both are
bug fixes that stand on their own and neither depends on the feature. Then
refrain + refrain-core ship together as v0.15.0 (they are lockstep since v0.14.0:
bump both `pyproject.toml` files and both CHANGELOGs in one `release:` PR, then
tag the merge commit). Only then can refrain-protocols add `seed { }`, because
the protocols will not compile until the grammar exists. The three host repos
are last and are mostly deletions; none of them blocks the others.

Note that refrain-protocols is where the user-visible bug actually gets fixed:
the 16 generic protocols receive their first correct percentile. That is a
protocol change, not an engine change — the engine work is what makes it
expressible.

---

## 5. Testing

- **New fixture shape.** Every golden fixture is generated with
  `start(skip_warmup=True)` (`docs/CONFORMANCE.md` §3), deliberately, to avoid
  startup-transient divergence. This feature exists *only* during warmup, so it
  needs a `skip_warmup=False` bundle — genuinely new corpus territory, not
  another row. **This is the likeliest place an estimate is wrong.**
- **Parity.** The seeded value is bit-exact across backends by construction: a
  constant-shaped result satisfies `np.percentile([v]*n, p) == v` exactly, and
  Rust's `percentile_linear` reduces to `v + (v-v)*frac == v`. Pin at `1e-9`,
  matching the existing `# constant fill is exact` precedent.
- **Resolve.** Dead-seed elimination (an adaptive artifact contains zero seed
  rules); warmup-fits `ResolveError`; byte-identical `content_hash` for every
  non-seeding protocol.
- **Runtime.** Seed once across staged blocks; disarm-by-host; fail-closed mute;
  NaN skip on both backends.

---

## 6. Free findings (unrelated to whether this ships)

- **NaN asymmetry** — Rust panics, Python poisons. Untested. (§2.6)
- **`docs/PRIMITIVES.md:359`** claims the percentile estimator uses the P² online
  algorithm with constant memory. Both implementations keep the full window and
  call `np.percentile`/`percentile_linear` per sample. `DESIGN-NOTES.md:489` is
  correct; PRIMITIVES is wrong. Note: if P² ever lands, constant-prefill seeding
  needs rework (P² state is 5 markers, not a buffer).
- **`docs/SPEC.md` §4.10** documents neither `mode` nor `block` despite both
  shipping. §4.9 never lists `mode` as a control type.
- **`docs/EMBEDDING.md`** never mentions `export_state`/`seed_state`, while
  `PRIMITIVES.md:368` points readers there for exactly that.
- **`docs/IR-JSON.md`** documents only v0.1 — no v0.2, `blocks`, or
  `reward_bundles`.
- **Portal UI** — `ready` renders as `'default'` in `EnvelopeCard` while
  `ControlSlider` treats `ready` as pending (`widgets.tsx:619`), so one screen can
  say "baseline pending…" and "default" at once. Plausibly the origin of "it
  always shows default".
- **`live_tunable` is decorative** — read by neither evaluator
  (`grep -c live_tunable src/refrain/eval_.py` → 0). `set_control` will write a
  control declared `live_tunable = false`. Not required by this design, but the
  hosts believe it means something.
- **`BandpassImpl` has no `update_control`**, so ORF retuning silently does not
  work despite SPEC §7.7. `EMBEDDING.md:237` is honest about it.

---

## 7. Cost

**Medium. One focused release (v0.15.0).** ~1,400–1,600 lines, ~20 files, both
engines.

Calibration from this repo's history:

| Feature | Kind | Size | Backends |
|---|---|---|---|
| `mode` (v0.12.0) | resolve-time | 484 lines, 11 files | Python only — folds away |
| seed/export (v0.8.0) | runtime API, no syntax | 646 lines, 6 files | both |
| `autocorr` (#40) | full construct | 3,305 lines, 31 files (~440 production) | both |

This sits between, closer to `autocorr` in shape. `mode` is not a valid
comparable: resolve-time features never reach the engine.

**Verdict for the hosts: it's medium — and you should still wait and build
nothing.** Not because the wait is short, but because the stopgap is worse than
the gap: it ports a table that cannot express what these protocols need, into a
third repo, to fix five protocols nobody is running. Their instinct — *"we'd
rather wait than write the third copy of logic that belongs in the engine"* — was
right, and the correction to their diagnosis makes it more right.

---

## 8. Risks

1. **Conformance fixture shape** (§5) — the one genuinely novel piece of
   engineering.
2. **Migration shift, now benign.** The recorder percentiles over ~60 decimated
   telemetry points (`last_taps()` is one sample per chunk; `_MIN_SAMPLES = 30` is
   documented as "30s at the 1Hz telemetry rate"). In-engine, the same window is
   ~15,000 full-rate samples. Same distribution in expectation, better estimator,
   **different number for a given recording**. With nothing released and no
   longitudinal data, this needs no validation gate — but it would have, and it is
   why the five working BrainBit protocols cannot be assumed to seed identically.
3. **Ring-buffer duplication.** Neither backend has a shared "last N seconds"
   abstraction — the deque idiom is open-coded 6× per backend, and the
   ms→samples expression 8× in `primitive_impls.py`. Reusing `PercentileImpl`
   avoids adding a seventh; do not hand-roll new storage.
