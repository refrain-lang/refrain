# Follow-up: a seeded control's range clamp compares face values, so a time-typed control with a range in seconds clamps 1000× low

**Status:** open, not fixed in 0.25.0. A units change inside the clamp at release time is
the wrong trade for a hole nothing in the repository can reach (`final-review.md` M3).

## The reproduction

```refrain
controls {
  hold_ms = duration { default = 250 ms; range = (0.1 s, 2 s); live_tunable = true
    seed = percentile { from = "level"; window = 2 s; target_pct = 50; scale = 1.5 } }
}
```

resolves, with no warning, to:

```
type_kind: duration | default: 250.0 ms
range_low:  0.1 s -> _literal_bound 0.1
range_high: 2.0 s -> _literal_bound 2.0
```

`_literal_bound` (`src/refrain/eval_.py:326-337`) returns `float(expr.value)` — the
authored face number, with no unit conversion — and `literal_bound`
(`refrain-core/src/eval.rs:938`) reads the same `number` node off the wire, so both
engines clamp against 0.1 and 2.0. The control's own working number is 250 for the same
duration. A seed that measured anything over 2 would be clamped to 2, three orders of
magnitude below the range the author wrote, and `docs/SPEC.md` §4.9.4's promise — "a
scale cannot push a limit outside what the protocol declared as sane" — is the sentence
that is weaker than it reads.

The clamp only exists on the `scale` path, so `scale = 1.0` (or no `scale`) never meets
it.

## Reach, precisely

Triggering it needs all three of:

1. a **time-typed** seeded control (`duration`), because time is the only dimension with
   more than one surface unit — `src/refrain/grammar.lark:130` is
   `"uV2" | "Hz" | "ms" | "min" | "s" | "uV" | "C" | "%"`, and `uV`, `uV2`, `Hz`, `%`
   and `C` each have exactly one, so a voltage, frequency or percent control's face
   number and working number are the same number;
2. a `range` written in `s` or `min` rather than `ms`;
3. `scale != 1.0`, which is what brings the clamp.

**What is in this repository:** 6 seeded controls across the 40 `.refrain` files —
`autopilot_staged.refrain` (`t_uv`), `reseed_staged.refrain` (`smr_threshold_uv`),
`seed_exprpos.refrain` (`thr_uv`), `seed_scale_emg.refrain` (`emg_limit_uv`),
`seed_smr_baseline.refrain` (`thr_uv`) and `examples/alpha_theta_autopilot.refrain`
(`crossover_target`). Five are `voltage` with both bounds in `uV`; one is a unitless
`number`. None is time-typed, and the only one with `scale != 1.0`
(`seed_scale_emg.refrain`, `scale = 3.0`) declares no `range` at all, so no clamp
applies to it. The worked per-person muscle-limit case in the design is therefore
unaffected, as is every golden vector.

## The more general hole underneath it

The resolver accepts `hold_ms = duration { ... seed = percentile { from = "level" ... } }`
where `"level"` is a **voltage** derive, with no warning: a duration control seeded from
a voltage statistic. That is dimensionally meaningless whatever the clamp does, and it
is pre-existing — the clamp bug is only the visible corner of it. A fix that converted
units inside the clamp without also refusing a dimension mismatch on the `seed` rule
would make the clamp right about a number that should not exist.

## Options, if taken up

- **Refuse it at compile time.** `_resolve_control_seed` checks the seeded control's
  dimension against the `from` entity's and raises when they differ. This closes the
  general hole, makes the clamp question moot for `duration` controls, and refuses
  nothing anybody has written. Preferred.
- **Convert in the clamp.** Make `_literal_bound` (and `literal_bound`) carry the unit
  and normalise a duration bound to the control's working units. Touches both engines
  and the wire's meaning of `range_low`/`range_high`, which several v0.6 vectors already
  carry — so it needs a fixture pass and a version question answered, which is exactly
  why it was not done at release time.
- **Document the face-value rule.** Weakest: say in §4.9.4 that a seeded control's
  `range` is compared in the control's own units and that a `duration` range should be
  written in `ms`. Cheap, and worth doing in any case alongside either fix above.
