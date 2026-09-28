# Response — protocol-declared autopilot

**To:** Coherence Recorder, Coherence Companion, Coherence Portal
**From:** Refrain
**Re:** "Request for scoping: protocol-declared closed-loop guidance/autopilot
contract" (`coherence-recorder` commit `967159f`, "docs: defer session
guidance to Refrain")
**Verdict:** Shipping in **v0.22.0**. Design:
`docs/superpowers/specs/2026-09-24-protocol-autopilot-design.md`. Authoring
guide: `docs/AUTOPILOT-AUTHORING.md`. Host API: `docs/EMBEDDING.md`
"Autopilot advice".

You asked us to take the coaching logic that currently lives in two host
repos — `recorder/backend/nf/guidance.py` and `@coherence/nf-coach`'s
`training_efficacy.ts` — and move it into the engine, where it can see the
protocol structure directly instead of guessing at it from control names.
We're doing that. This note is what shipped, what it replaces, and one thing
in your own numbers we need you to help us re-confirm before it drives a
real session.

---

## 1. What shipped

Every session now carries a second, built-in decision procedure — the
*advisor* — running alongside the reward/output pipeline, identical in both
engines (Python reference, Rust core). After every chunk it produces one
structured result: hold steady, keep collecting evidence, hint at a
direction, or propose a concrete change to one control, with the numbers
behind the decision attached. This runs for **every** protocol, including
every one you have today — a protocol that does nothing new still gets
observations and, where the protocol structure makes it unambiguous,
direction hints. Naming the reward checks and adding a policy block are
both opt-in, additive changes a protocol author makes when they're worth it;
nothing about your 100+ existing compiled protocols changes until someone
does that.

**Refrain never changes a control on its own.** It proposes; your app
decides whether the practitioner has autopilot turned on for this session and
whether this particular proposal is one the protocol allows to be applied
without a human in the loop. `apply_advice(id, by="autopilot")` is the one
enforcement point — it refuses anything the protocol declared suggest-only,
so a host bug can't auto-apply something the protocol author reserved for a
practitioner's judgement.

## 2. What replaces your two rulebooks

**`recorder/backend/nf/guidance.py`** is alpha/theta-specific: it finds
"theta" and "crossover" by substring search on component names, and
hard-codes a 15% guard ceiling, a 70% theta-gate floor, a 10–35% reward band,
a ±0.05 crossover step clamped to (0.5, 1.0), and a 2-minute window with a
3-minute suggestion cadence. **`@coherence/nf-coach`'s `training_efficacy.ts`**
is generic but guesses: it picks a "coaching control" by flag
(`display_as_reward_rate`), by the presence of `baseline_seed`, or by a
`*_inhibit_rate` name suffix, infers up-train vs. down-train from the seed
percentile, and judges a single 50–75% time-in-criteria sweet spot regardless
of protocol.

| Old (host-guessed) | New (protocol-declared) |
|---|---|
| `guidance.py`'s substring search for "theta"/"crossover" in component names | The protocol names its own checks: `above(...) as "theta"`, `above(...) as "crossover"`. No guessing, no name pattern to keep in sync. |
| `guidance.py`'s hard-coded 10–35% band, one protocol only | `autopilot.reward_target` — any protocol declares its own band, or takes the (50–75%) default. |
| `guidance.py`'s hard-coded 15% guard ceiling | `autopilot`'s `guard { max; say }` entries, or the 15% built-in default for any inhibit with no entry — same number, now a declared default instead of an implicit one. |
| `guidance.py`'s hard-coded 70% theta-gate floor, checked independently of which check is actually failing worst | Gone as a special case. The advisor always addresses whichever named check has the **worst** pass rate when reward is too strict (`docs/SPEC.md` §7.10.4) — see the correction below; this is a real behavior change, not a relabeling. |
| `guidance.py`'s ±0.05 `crossover_target` step, clamped to (0.5, 1.0), rounded to 2 decimals | `crossover_target`'s `autopilot = fixed_step { step = 0.05; limits = (0.5, 1.0); round_to = 0.01 }` — the exact same numbers, now declared in the protocol and inside `content_hash` instead of hard-coded in your repo. |
| `guidance.py`'s 120 s window / 180 s retention / 180 s cadence / 60 s equipment freeze | `autopilot.watch` / (built-in `2 × watch` retention, not separately configurable) / `between_moves` / `equipment_settle` — same defaults (120 s / 180 s / 60 s) when a protocol doesn't override them. |
| `guidance.py.observe(frame)`, `.record_gong(t_s)`, `.mark_equipment_adjustment(t_s)`, `.set_current_target(value)`, `.recommendation()`, `.resolve(id, outcome)` | `step_chunk` (feeds the advisor automatically — nothing to call), `advice()`, `mark_equipment_change()`, `set_control()` (unchanged signature, now also notifies the advisor), `apply_advice()`/`dismiss_advice()`. |
| nf-coach's `findCoachingControl` (flag / `baseline_seed` / name-suffix guessing) | The protocol's own `fixes = "<check>"` on each knob's policy — declared, not inferred. Where no policy exists at all, the resolver's direction tracer (§7.10.5) finds the single knob that sets a check's threshold from the check's own expression — still no name pattern. |
| nf-coach's `deriveControlMode` (up-train vs. down-train inferred from `baseline_seed.percentile <= 50`) | `higher_is = "harder" \| "easier"`, declared per knob and cross-checked against the traced direction at compile time (rule V3) — where the compiler can trace the knob, a wrong declaration is a compile error, not a silent wrong-direction suggestion. Where it can't (the check doesn't compare its signal directly against the knob), the direction can't be verified, so the compiler only lets that knob suggest (`apply = "suggest"`, rule V18): a practitioner sees every move before it happens. |
| nf-coach's `assessEfficacy` verdict (`settling`/`on_track`/`too_strict`/`too_easy`, a text suggestion, one 50–75% sweet spot for every protocol) | `advice()`'s `state`/`reason`/`message`, with an actual proposed value (not just a phrase) when a policy exists, judged against the protocol's own `reward_target`. The reason codes `too_strict`/`too_easy`/`on_track` carry over by name. |
| nf-coach's `bandNotes`/`bandDirections` (per-band % change vs. warmup baseline, for the post-session debrief) | **Out of scope, unchanged.** This is a display feature over data the recorder already has (baseline deltas), not a coaching decision — nothing here needs to move into the engine. |
| nf-coach's `REWARD_RATE_TARGET_LO`/`HI` (already dead — "legacy export; no longer drives the verdict") | `evidence.chimes_per_min` — reported for information only, never drives a decision, matching what your own code already concluded these numbers should be. |

**A correction, not a relabeling: the 70% theta-gate floor doesn't survive
as a hard-coded rule, because it was never really a rule about 70% — it was
a stand-in for "check what's actually failing worst."** `guidance.py` checks
the theta gate against a fixed 70% floor *independently* of whether the
crossover check is failing far worse — so a session at 65% theta / 3%
crossover gets a "keep the crossover target steady, the theta gate is soft"
message even though crossover is the real problem by an order of magnitude.
The new advisor has no per-check hard-coded floor at all: it always tightens
or loosens whichever named check has the worst pass rate for the direction
that's needed (§7.10.4), so it would address crossover in that scenario, not
theta. If your team wants the old "theta gate specifically shouldn't drop
below X%" behavior *in addition to* the general procedure, that's expressible
today as a second named check with its own policy, or a `limiter` message on
the theta check — it's a protocol-authoring decision now, not something we
can silently preserve as a hidden default.

## 3. The host API

Six calls on `Evaluator` (same names in PyO3 and uniffi;
`docs/EMBEDDING.md` "Autopilot advice" has the full shape, reason codes, and
audit-event kinds):

```python
evaluator.advice() -> dict
evaluator.apply_advice(advice_id, by="practitioner") -> dict
evaluator.dismiss_advice(advice_id) -> None
evaluator.mark_equipment_change() -> None
evaluator.drain_advice_events() -> list[dict]
evaluator.autopilot_policy() -> dict
```

`advice()` is a read — it's computed as a side effect of `step_chunk`, so
there's no separate `observe()` call to wire into your chunk loop. The
recorder-shaped loop:

```python
for chunk in amp_chunks():
    events = ev.step_chunk(chunk)
    a = ev.advice()
    ui.show_advice(a)                              # every chunk: collecting / hold / hint / adjust
    if a["state"] == "adjust" and session.autopilot_on and a["control"]["auto_allowed"]:
        ev.apply_advice(a["id"], by="autopilot")
    audit.extend(ev.drain_advice_events())          # persist with the session record
```

With `backend="rust"` every call delegates to the Rust core the same way
`seed_report()` does; results and events are identical once parsed as JSON
values (numbers compared as `f64` — Python and Rust print floats
differently, so parity is checked on parsed values, not raw bytes). The
uniffi surface gained one error variant,
`RefrainError.Advice { message }`, mirroring `apply_advice`/`dismiss_advice`
raising `AdviceError` on the Python/PyO3 side — mobile consumers need to
regenerate bindings to pick it up.

## 4. What you own

**The per-session enable switch and the audit trail are yours, same as
before.** `session.autopilot_on` in the loop above is your own flag —
Refrain has no opinion on whether a given session, practitioner, or person training
should have autopilot on; it only ever tells you what it would do and lets
`apply_advice` refuse what the protocol forbids. `drain_advice_events()`
hands you every `suggested`/`superseded`/`blocked`/`applied`/`dismissed`/
`changed_manually`/`equipment_change` event since the last drain, in order —
persist those with the session record exactly as you'd persist any other
event stream; Refrain keeps nothing across sessions.

**Delete, don't port.** `guidance.py` and nf-coach's `findCoachingControl` /
`deriveControlMode` / `assessEfficacy` name-guessing become dead code the
moment a protocol adopts named checks and a policy, and their numbers (the
120 s window, the 15% guard, the 10–35% band, the ±0.05 step) are the ones
that shipped as the built-in defaults and the alpha/theta example's declared
policy — there's no daylight to port into an interim version. nf-coach's
`bandNotes`/`bandDirections` debrief code is unaffected and stays exactly
where it is.

## 5. One thing to re-confirm: the alpha/theta band

The 10–35% reward-target band in `examples/alpha_theta_autopilot.refrain`
(and inherited from `guidance.py`) was measured by your recorder against the
**dwell-held** reward state — the one-second-sustained flag. The new
advisor's `reward_rate` is measured against the **instantaneous** condition
(§7.10.2) — matching nf-coach's own "time-in-criteria" definition, not
`guidance.py`'s. Held-vs-instantaneous can shift a percentage measurably
depending on how often the condition flickers around the dwell boundary. We
shipped the number as-is with a header comment saying so, but we can't
re-confirm it ourselves — that needs real recorded sessions run under the
instantaneous measure. Please treat the band as provisional
(`evidence = "exploratory"` already says as much) until someone checks it
against your session logs.

## 6. Follow-up in `refrain-protocols`

Out of scope for this release, tracked as the next piece of work once
v0.22.0 ships:

- The ratio derive and `crossover_target` control have already landed in
  production `protocols/eeg/alpha_theta.refrain`, now at v1.2.0
  (refrain-protocols PR #23), so the crossover check has a live setting to
  move. What's still open: naming the two reward checks and adding the
  policy shown in `examples/alpha_theta_autopilot.refrain` to that same
  production file — this is where your alpha/theta sessions actually start
  getting live advice.
- Name the checks in SMR and its siblings (`smr`, `high_beta_down`,
  `hrv_resonance`, `critical_fluctuation`, `faa_f3f4`), and add `limiter`
  messages or policies where they're worth it — `critical_fluctuation` in
  particular should get **names only**, never a policy: its guard *is* the
  protocol, and its knob must never be autopiloted.
- These are ordinary protocol changes (a `meta.version` bump each, since
  provenance is inside the hash) — no engine work is needed to do them.

## 7. TL;DR

| Your ask | Answer |
|---|---|
| Move the coaching logic into the engine | Done — `docs/SPEC.md` §7.10, both engines, parity-tested |
| Keep `guidance.py`? | No — delete it once your protocol adopts names + policy |
| Keep nf-coach's control-guessing? | No — `fixes`/`higher_is` are declared, or traced exactly (no guessing) when no policy exists |
| Keep the 70% theta-gate floor? | No, and this is a real behavior change — see §2's correction |
| New host calls | Six, listed in §3; `advice()` is a read, nothing new to wire into the chunk loop |
| Per-session switch / audit persistence | Yours, unchanged in ownership |
| Anything to double-check before relying on it | Yes — the 10–35% band under the instantaneous measure (§5) |
| `refrain-protocols` changes | Follow-up, not this release (§6) |

Thanks for filing a request precise enough that we could carry your own
numbers (the window, the cadence, the guard ceiling, the crossover step)
forward as the built-in defaults instead of re-deriving them from scratch.
