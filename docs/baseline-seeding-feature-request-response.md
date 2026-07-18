# Response — first-class baseline seeding

**To:** Coherence Recorder, Coherence Companion, Coherence Portal
**From:** Refrain
**Re:** "Request for scoping: first-class baseline seeding in Refrain" (evaluated against v0.14.0)
**Verdict:** **Medium.** Scoped for **v0.15.0**. Design: `docs/superpowers/specs/2026-07-16-baseline-seeding-design.md`.

**Do not ship the interim port.** Not "wait for us" — *build nothing*. Your
stopgap would reproduce the bug it is meant to paper over, for reasons in §2.
Your own instinct — *"we'd rather wait than write the third copy of logic that
belongs in the engine"* — was right, and the corrections below make it more
right, not less.

Thanks for a request precise enough to verify line by line. We checked every
claim against `refrain-protocols`, `coherence-recorder`, `coherence-companion`
and `coherence-portal` before answering. Three of your claims did not survive,
and one of the corrections is the best argument in the document — you came a
sentence away from making it yourselves.

---

## 1. You are right about the important thing

**The semantic belongs in the engine, and we are taking it.**

We closed this once. The staged-protocols design (2026-05-29) called it *"R5:
one-shot baseline measure-then-freeze"* and pushed it recorder-side, on the
grounds that it *"rides entirely on substrate already shipped in v0.6.3."*

That reasoning was sound **given its premise**: that every host can orchestrate —
buffer telemetry, take a percentile, call `set_control`. Coherence Companion runs
pre-baked IR through refrain-core with no Python and no compiler. It structurally
cannot. **The premise expired, so the decision is re-opened.** That is a
legitimate reason and you should not have had to argue for it as hard as you did.

One argument you didn't make, which we're acting on anyway: **the baseline is
currently invisible to `protocol_hash`.** Two sessions with identical hashes can
have run at different thresholds. For a project with CRED-nf reproducibility
ambitions, that alone justifies the engine owning this.

## 2. Three corrections — the third is your best argument

**(a) There was no rename.** You attribute the breakage to a Phase-2 collapse
renaming `smr_threshold_uv` → `thr_uv`. `thr_uv` was the generic family's control
name from the initial seed commit; `git log -S"smr_threshold_uv"` over the
protocol files hits only BrainBit. **The bug predates the collapse** — the
pre-collapse `*_baseline.refrain` files hit the same fallback. The collapse
changed which *mode* it manifests in, nothing more.

**(b) "Instead of the intended 40th" — 40 is not intended for any of the 16.** By
your table's own stated principle (*"Matches the corresponding adaptive
protocol's… `*_reward_pct` default"*), the target is each protocol's own
`reward_pct`: **70 for the ten up-train, 30 for the six down-train.** Adding
`thr_uv: 40.0` would leave all sixteen wrong and invert the six down-train ones.

**(c) A name-keyed table cannot express this — at all.** Sixteen protocols share
the single control name `thr_uv` and need **two different percentiles**. One dict
entry cannot say both. This is not a stale-table bug; it is a category error, and
no amount of maintenance fixes it.

**(c) is the strongest case for the feature and you nearly made it:** *"The table
only exists to defeat a file split you already fixed… Once `mode` puts both
controls in one file, that value is already in scope."* Exactly. That is the
design (§3).

Also worth noting: `reward_pct` is `live_tunable`. Even a *correct* static entry
desyncs the moment a clinician touches the slider.

## 3. What we're building

The rule hangs on the **control**, in the protocol, carried in the IR:

```refrain
controls {
  reward_pct = percent { default = 70; range = (50, 90); live_tunable = true }

  thr_uv = voltage {
    default      = 2.0 uV
    range        = (0.5 uV, 10 uV)
    live_tunable = true
    seed = percentile {
      from       = "env"        // a derive name
      window     = 60 s         // the trim — your requirement 3
      target_pct = reward_pct   // the control, not a literal
    }
  }
}
```

`target_pct: reward_pct` is the whole answer to §2(c): each protocol names its own
percentile, no second copy exists to drift, and because `reward_pct` is
`live_tunable` the seed tracks a clinician who retunes it. Sixteen protocols,
one control name, sixteen right answers.

Against your requirements:

| # | Requirement | How |
|---|---|---|
| 1 | Expressible in protocol, carried in IR | `seed = percentile { }` on the control declaration |
| 2 | Policy is the protocol's | Every field is protocol-authored; **no host table** |
| 3 | Settle/trim protocol-expressible | `window = 60 s`; the compiler enforces it fits (§4) |
| 4 | Runtime, not resolve-time; don't perturb IR bytes | IR carries the *rule*, never the value. Emitted only when present, so **every non-seeding protocol keeps a byte-identical `content_hash`** |
| 5 | Live override survives | The engine writes the control **once**, ever. There is no second write to overwrite you. A write *during* warmup disarms the seed permanently — explicit clinical action beats a measurement |
| 6 | Seed once, hold | Keys on the `warmup → run` state edge, which fires exactly once per session (a tested invariant). One baseline anchors all four staged blocks with no special-casing |
| 7 | Control-seeding, not threshold-seeding | It hangs on the control. `smr_anchor_uv` / `theta_anchor_uv` need **no** anchor-vs-threshold vocabulary — anchors are just controls |
| 8 | Composes with `mode` | A seed whose control has no surviving reference is dropped at resolve. Adaptive artifacts carry zero seed rules |
| 9 | Observable | `seed_report()` — see §5 |

Two things you'll like: **no grammar changes** (`= kind { }` is the existing house
pattern), and **no new percentile implementation** — the seed reuses
`PercentileImpl`, which makes the seeded value **bit-exact across Python and
Rust by construction**.

## 4. Your open questions, answered

**"Warmup too short / not enough samples — what should happen?"**
**It should not compile.** Phase durations must be numeric literals, so the
compiler can compare `window` against warmup's duration statically. A 60 s window
under a 40 s warmup is a `ResolveError`, not a degraded session. This also
retires your symptom #1: the 90 s warmup stops being hand-tuned against
`_SETTLED_WINDOW_S` in a repo the protocol can't see, and becomes a constraint the
compiler enforces.

**"Seeding fails or can't run. We'd rather it fail loudly."**
**Fail closed: the engine mutes output for the rest of the session.** Not a flag.
Your portal *already* renders an amber "baseline not seeded — using the protocol
default" badge, and a session still ran thirty minutes at 100% reward. A warning
hosts are free to ignore is not a safety mechanism. A protocol that promised to
measure the patient and couldn't will not emit reward.

Note the distinction: **disarmed ≠ failed.** A clinician who set the control
during warmup gets a normal session, unmuted. They decided; the engine defers.

**"Engine version skew — is there an equivalent to the `bindings` echo?"**
**No, and it's worse than you think — so we're building one.** refrain-core
ignores unknown fields *and* never reads `refrain_ir_version` (it doesn't even
declare the field). An old core handed a seeding protocol would run it at the
placeholder default, silently. **That is your incident, bit for bit.** SPEC §9.3
already specifies the fix and was never implemented; it ships as a prerequisite,
so an old core refuses a seeding protocol **at load**, loudly. Per-protocol
version tagging keeps the blast radius exact — everything not seeding stays at
0.1/0.2 and keeps loading. We're also echoing the seeded control names in compile
metadata, mirroring `meta.bindings`.

**"Relationship to `auto_range` / `export_state` / `seed_state`?"**
**A sibling, not a special case.** It differs on both axes that matter.
*Timing:* `seed_state` is applied in the constructor — there is no mid-session
re-seed on any backend — while this fires at warmup→run, mid-session.
*Substance:* `seed_state` is deliberately **runtime state, never IR** (v0.8.0's
central decision); this is an authored **rule** that must ship with the protocol.
Same motivation, opposite side of both lines. Your cross-session intuition is
still good, but it's a separate feature.

**"Does it belong on the control, the threshold, or the phase?"**
**The control.** The phase loses because "warmup" isn't a real category — it's
just whichever phase is first and silent, so the semantics shift if you rename or
precede it. The threshold loses on your own requirement 7, and fatally: a value
living inside a threshold has **no control name a host can address**, so
`set_control` couldn't reach it and your safety valve would be gone.

## 5. Reading it back

```jsonc
{ "thr_uv": { "status": "seeded",        // seeded | insufficient_samples | disarmed_by_host
              "value": 2.03, "source": "derive/env", "target_pct": 70.0,
              "n_samples": 14847, "window_s": 60.0, "at_time_s": 90.0 } }
```

A dedicated `seed_report()` accessor, keyed by control name — deliberately **not**
a tap (the tap key-set is pinned by an exact-equality parity test; v0.8.0 made the
same call for `export_state()`). It lands on all backends **including uniffi**,
so mobile gets it natively.

This is what finally lets your `baseline_seeds` field say something true instead
of "default" — which today is indistinguishable from *"we measured this patient
and 2.0 is genuinely their number."*

## 6. Two things about your incident report

**The cause isn't the one you gave.** The five BrainBit baseline protocols are
the *correctly* keyed ones (40/80/50 — we resolved each protocol and ran your own
detector against the IR). They break on mobile because **Coherence Companion has
no seeding executor at all.** `buildManifest.ts:87` ports the dict faithfully,
writes `baseline_seed` into the manifest, and nothing on-device ever reads it —
the only `setControl` caller is the relay downlink. It is dead code. The
table-miss story is real, but it's about the **16 generic protocols on desktop**.

**A portal bug that may be the whole origin story.** `ready` renders as `'default'`
in `EnvelopeCard` while `ControlSlider` treats `ready` as pending
(`widgets.tsx:619`) — so one screen can say "baseline pending…" and "default"
simultaneously. That's plausibly what someone saw and generalised into "it always
shows default." Worth a look independent of any of this.

## 7. What each of you does

| Repo | Action |
|---|---|
| **Coherence Companion** | **Nothing.** Delete the dead `BASELINE_SEED_PERCENTILES` port when convenient. Read `seed_report()` when v0.15.0 lands. **Do not build the interim.** |
| **Coherence Recorder** | Nothing yet. On v0.15.0: **delete** `baseline_seed.py` and `_BASELINE_SEED_PERCENTILES`. One clean cut — nothing is released, so no dual path |
| **Coherence Portal** | Fix the `ready`-state widget divergence (§6) — that one's real today. On v0.15.0: populate `baseline_seeds` from `seed_report()` |
| **refrain-protocols** | On v0.15.0: add `seed = percentile { }` to the 21 baseline protocols. **This is where the user-visible bug actually gets fixed** — the engine work is what makes the fix expressible |

## 8. TL;DR

| Your ask | Answer |
|---|---|
| Scope it | **Medium** — ~1,400–1,600 lines, ~20 files, both engines, one release (v0.15.0) |
| Small → we build nothing | **Build nothing anyway.** Your stopgap ports a table that *cannot* be correct |
| Keep your `baseline_seed` descriptor? | No — delete it, as you hoped |
| New percentile impl? | No. Reuses `PercentileImpl` → bit-exact across backends for free |
| Warmup too short | Won't compile |
| Seeding fails | Fails closed (muted), never silently "successful" |
| Version skew | No equivalent existed; SPEC §9.3 ships as a prerequisite |
| Special case of `seed_state`? | Sibling. Different timing, different side of the IR line |
| Control, threshold, or phase? | **Control** |

**Nothing is released, which is why this is the right moment.** The version gate
is free exactly once — before there's a fleet to be backward-compatible with —
and that window closes on its own.
