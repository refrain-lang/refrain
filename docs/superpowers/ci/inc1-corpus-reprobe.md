# Increment 1 — corpus re-probe

The roadmap requires re-running the corpus probe after each increment to confirm the
empirical unlock before starting the next. Increment 1 added single-condition
(above/below, absolute) + center/bandwidth bandpass support.

## refrain repo (what CI gates)

`refrain fuzz bench/protocols examples --library examples --max-scenarios 2`:

```
fuzzed 7 (pass 7 / fail 0) / skipped 19 / errored 0
coverage: fuzzed 7 / total 26 (27%)
```

- **fuzzed 4 → 7**: the three new absolute single-leaf fixtures
  (`micro_single_above`, `micro_single_below`, `micro_center_bandwidth`) now fuzz.
- The old generic `single-condition reward` skip is split into specific,
  feature-mapped reasons (`single percentile-leaf reward (needs calibrated oracle)`,
  `composite-signal reward condition`, `non-bandpass (coherence) reward signal`,
  `reward condition without a resolvable threshold`).
- Exit 0. The refrain-repo CI gate stays green.

## refrain-protocols (the real target, not yet CI-wired)

Per-protocol probe over `protocols/` + `drafts/` (59 files, `--library <repo>`),
catching evaluator crashes:

```
 6  fuzzed   (pass 0 / violation 6)
51  skipped
 2  eval-crash
```

Skip reasons:
```
20  unclassified (unsupported operand type(s) for *: 'NoneType' and 'float')
18  single percentile-leaf reward (needs calibrated oracle)
 4  non-bandpass (coherence) reward signal
 4  composite-signal reward condition
 2  reward condition without a resolvable threshold
 2  unclassified (reward.event has no all_of/any_of condition)
 1  unclassified (band must be (low<high); got (0.0, 0.0))
```
Eval crashes: `bipolar: plus channel 'C3' not in source`, `referential: active
channel 'C3' not in source`.

## Reading the result

Increment 1 correctly implements the two features and the taxonomy — the refrain-repo
corpus and the synthetic fixtures fuzz cleanly. But the **real** refrain-protocols
corpus does NOT yet unlock cleanly: the single-condition + center/bandwidth shape is
now recognised, yet three *further*, out-of-scope blockers stand between recognition
and a clean fuzz. These are the next work items (in priority order):

1. **Oracle noise-floor fidelity (the 6 violations).** The 6 protocols that now fuzz
   all report violations. At `max_scenarios=1` this is the `negative_control_quiet`
   scenario firing SPURIOUS: their absolute thresholds sit near the synthetic
   noise floor, so quiet EEG noise crosses the threshold and the engine fires while
   the analytic oracle predicts silence. This is the **same analytic-vs-calibrated
   gap** as percentile single-leaf — the synthetic fixtures avoid it only by using
   thresholds comfortably above the noise floor. The **calibrated oracle** (already
   the roadmap's next big lever for the 18 percentile protocols) covers this for
   near-noise-floor absolute thresholds too. Until then, these are false positives on
   real protocols — do NOT wire refrain-protocols CI to gate on them.

2. **`NoneType * float` in introspection (20 protocols, 34%).** A control-default (or
   similar) resolves to `None` and is multiplied by a float during `build_surface`,
   caught by the backstop as `unclassified`. This is a `surface.py` robustness gap,
   not a language feature — it should be fixed (or classified) so these 20 protocols
   surface their real feature gap instead of a generic TypeError. Likely the single
   largest lever after the calibrated oracle.

3. **Synthetic channels for bipolar/referential montages (2 eval-crashes).**
   `_channels_for_synthetic` supplies the `requires` channels + ear channels, but a
   `bipolar(plus: C3, ...)` / `referential(active: C3, ...)` montage needs channels
   not in `requires`. The synthetic source lacks them → the evaluator raises at
   pipeline build → the batch **crashes** (the error is outside the guarded backstop).
   Two fixes: (a) derive synthetic channels from the montage refs, and (b) make the
   batch runner classify an evaluator-setup channel error as ERRORED rather than
   crashing the whole run.

## Next increment

The **calibrated oracle** is confirmed as the highest-value next increment: it unlocks
the 18 percentile single-leaf protocols AND the near-noise-floor absolute ones (the 6
current violations) — the largest coherent slice of the corpus. The `NoneType * float`
introspection fix and the montage-channel synthetic support are the next two levers and
are prerequisites for wiring refrain-protocols CI.
