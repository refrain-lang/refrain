# Protocol fuzzer — Increment 1: single-condition reward — design

> Status: approved design (brainstorm complete), ready for the TDD plan.
> Parent: [[2026-06-28-fuzzer-parity-roadmap-design]] (Increment 1).
> Builds on Increment 0 (`fuzz/runner.py`, `UnsupportedProtocol`, batch + CI),
> merged to main as #51 (`ee5867d`).

## Goal

Turn a **sole** `dwell(above/below(...))` reward — a single `ConditionLeaf` with
no `all_of`/`any_of` wrapper — from *skipped* into *fuzzed*. This is the dominant
~80% pattern across the real `refrain-protocols` corpus, so it is the largest
single coverage jump in the roadmap.

Increment 0 makes such protocols skip as `single-condition reward`. This increment
makes the clean case actually fuzz, and splits the *still*-unsupported single-leaf
shapes into specific, feature-mapped skip reasons that point at later increments.

## Decisions locked in brainstorming

- **Supported shape = sole leaf vs threshold.** Support exactly a sole
  `above/below(derive_signal, threshold)` where the signal resolves to a
  band-envelope derive (has a baked SOS) and the threshold resolves (absolute or
  percentile). Derive-vs-derive, literal-RHS, composite/coherence signals stay
  skipped (their own increments).
- **Skip taxonomy = specific feature-mapped reasons.** The entangled single-leaf
  shapes skip with reasons that map to later increments (see below), not generic
  `unclassified`.
- **Test fixture = add a new repo fixture.** The refrain repo has no clean
  single-condition example; add a committed one. Bumps the visible CI coverage
  from `fuzzed 4 / total 22` to `fuzzed 5 / total 23`.

## Corpus reality (why this isn't a one-line change)

The oracle (`_walk_condition`) and generator (`_all_leaves`) already recurse into a
`ConditionLeaf` — so the work is **not** "teach them about leaves." Probing the
actual corpus surfaced the real gaps:

1. **No clean in-repo target.** The refrain repo's 3 "single-condition" protocols
   are each entangled with a *later*-increment feature, so none is a clean Inc 1
   target (this is why a new fixture is required):

   | protocol | leaf | why not Inc 1 |
   |---|---|---|
   | `composite_smr_theta` | `above('', '')` | signal is `reward.composite` (a reward-field) → weighted composite, Inc 4 |
   | `dyadic_alpha_coherence_pz` | `above(dyad_coh, coh_t)` | `dyad_coh` is a coherence derive (`sos=None`, `band=(0,0)`) → Inc 6 |
   | `alpha_theta` | `above(alpha_envelope, '')` | no resolvable threshold (derive-vs-derive / literal RHS) |

2. **The single-leaf path crashes/vacuums today.** With the leaf returned instead
   of raised, a clean single-percentile-leaf protocol fails in two ways:
   - `generate.py:_pivotal_scenarios_for_leaf` does
     `next(d for d in surface.derives if d.name == leaf.signal)` → **StopIteration**
     when the signal is not a derive (composite/empty).
   - The generator emits band-characterization probes and rank sweeps for **all**
     declared derives/thresholds, not just those referenced by the (now single-leaf)
     reward. Off-band probes (`probe:tone_6.0hz`) and orphan-threshold sweeps
     (`rank_sweep:theta_t:*`) then carry **zero crisp assertions** → `VacuityError`
     ("fail loud on vacuity"). For an `all_of` reward every leaf's threshold is
     used and an absolute leaf anchors the probes, so this never surfaced before.

   Per-scenario probe (clean single `above(smr_envelope, smr_t)` fixture): the
   **smr_t-referenced** scenarios are crisp (`leaf:…:true`,
   `percentile_warmup_then_spike`, `rank_sweep:smr_t:*`) — the per-leaf oracle math
   already works — while the unreferenced-band/threshold scenarios are vacuous.

So Increment 1 is: a **supportability detector** in `surface.py`, plus **scoping
the generator to the reward condition's own leaves/thresholds**, plus a clean
fixture.

## Supportability criterion (surface.py)

`_reward_condition_from_ir` returns `ConditionNode | ConditionLeaf`. When the sole
reward condition is a `ConditionLeaf`, classify it:

- **Supported** → return the leaf, iff:
  - `leaf.signal` names a derive in `surface.derives` whose `sos is not None`
    (a band-envelope the oracle can model), **and**
  - `leaf.threshold` names a threshold in `surface.thresholds` (absolute or
    percentile).
- **Skip with a specific reason** otherwise (raise `UnsupportedProtocol`):
  - signal is empty / not a derive → `composite-signal reward condition` (Inc 4).
  - signal is a derive with `sos is None` (coherence / non-bandpass) →
    `non-bandpass (coherence) reward signal` (Inc 6).
  - threshold does not resolve → `reward condition without a resolvable threshold`.

`reward_condition`'s type annotation becomes `ConditionNode | ConditionLeaf`. The
`all_of`/`any_of` path is unchanged. The non-dwell / no-condition fallthrough stays
a plain `ValueError` (→ backstop `unclassified`).

These reasons join the Increment-0 vocabulary so the batch by-reason breakdown
keeps mapping to roadmap increments. After this increment the three entangled
protocols re-classify out of `single-condition reward` into their specific buckets.

## Generator: scope to the reward condition

The generator must derive its probe/sweep targets from the **reward condition's own
leaves** (`_all_leaves(surface.reward_condition)`), not from all declared
derives/thresholds:

- **Rank sweeps** (`generate_rank_sweep`): sweep only thresholds referenced by a
  reward-condition leaf. Eliminates orphan-threshold vacuity
  (`rank_sweep:theta_t:*` when `theta_t` isn't in the reward).
- **Band-characterization probes** (`generate_characterization_probe`): emit probes
  only for derives referenced by the reward condition. Eliminates off-band-probe
  vacuity.
- For an `all_of`/`any_of` reward this is a **no-op** (every leaf's derive/threshold
  is already referenced), so existing fuzzable protocols are unaffected — a
  regression guard asserts identical corpora for the 4 currently-fuzzed protocols.

After scoping, the only remaining single-leaf scenarios are the reward-referenced
ones, which the per-scenario probe already shows are crisp (the
`percentile_warmup_then_spike` scenario establishes the percentile window). If any
reward-referenced single-leaf scenario is still vacuous after scoping, the plan
extends the percentile-warmup seeding to that generator (the oracle already does
the rank reasoning; the generator must feed it a warmed window) — the
fail-loud-on-vacuity invariant stays intact (no vacuity exemptions).

## New fixture

Add `bench/protocols/micro_single_condition.refrain`: a minimal clean
single-condition protocol — one band-envelope derive, one percentile threshold, a
sole `above(derive, threshold)` dwell reward, no orphan derives/thresholds. (Model
on `examples/smr_cz.refrain` with the `all_of([...])` replaced by a bare `above`,
and the unused theta/high-beta derives + thresholds removed.) This is the TDD
target and bumps refrain CI coverage to `fuzzed 5 / total 23`.

## Components touched

- `src/refrain/fuzz/surface.py` — supportability detector + `reward_condition` type.
- `src/refrain/fuzz/generate.py` — scope rank sweeps + characterization probes to
  the reward condition's leaves; (if needed) percentile-warmup seeding for the
  single-leaf case.
- `bench/protocols/micro_single_condition.refrain` — new fixture.
- Tests under `tests/fuzz/` (see below). `oracle.py` should need **no change** (it
  already walks a leaf); if a single-leaf prediction gap appears, it is in scope.

## Testing (TDD)

- **Detector** (`test_surface.py` / `test_unsupported.py`): the new fixture's sole
  leaf builds a surface (no raise); `composite_smr_theta` →
  `UnsupportedProtocol("composite-signal reward condition")`; `dyadic_…` →
  `"non-bandpass (coherence) reward signal"`; `alpha_theta` → `"reward condition
  without a resolvable threshold"`; an `all_of` protocol still builds.
- **End-to-end fuzz** (`test_runner.py` / `test_end_to_end.py`): `fuzz_protocol` on
  the new fixture → `FUZZED`, `passed is True`, non-vacuous (no `VacuityError`).
- **Generator scoping** (`test_generate.py`): for a single-leaf surface, the rank
  sweep covers only the leaf's threshold and the characterization probe only the
  leaf's derive; for an `all_of` surface (e.g. `realistic_smr`) the generated
  corpus is **unchanged** (regression guard).
- **Single-file CLI** (`test_cli_fuzz.py`): the new fixture → exit 0 with a report;
  `composite_smr_theta` → exit 0 with `SKIPPED (unsupported: composite-signal
  reward condition)`.
- **Batch coverage** (`test_batch.py`): the real-corpus aggregate now shows
  `fuzzed 5 / total 23` with the three entangled protocols under their new
  reasons; exit 0.

All via `.venv/bin/python -m pytest tests/fuzz/ -q`; `src/refrain/fuzz/` stays
ruff-clean.

## Out of scope (Increment 1)

- Weighted-composite reward signals (`composite_smr_theta`) → Increment 4.
- Coherence signals (`dyadic_alpha_coherence_pz`) → Increment 6.
- Derive-vs-derive / literal-RHS comparisons (`alpha_theta`).
- `center:`/`bandwidth:` bandpass (Increment 2), inhibit (3), bandpower (5).
- Raising the CI `--max-scenarios` cap / metamorphic-sweep depth.

## Risks / open questions for the plan

- **Residual single-leaf vacuity after scoping.** Confirm that scoping the
  generator to reward-referenced leaves fully removes vacuity for the new fixture
  (percentile threshold). If the in-band characterization probe is still vacuous
  due to percentile warmup, pin the warmup-seeding fix and a test that asserts the
  probe carries a crisp assertion. Reproduce per-scenario (the brainstorm probe
  script is the starting point).
- **`all_of` no-op guarantee.** The generator-scoping change must be a provable
  no-op for multi-leaf rewards. The regression test compares the full generated
  corpus (labels + coverage tags) for `realistic_smr`/`smr_cz` before/after.
- **Supportability discriminator robustness.** `sos is not None` is the proposed
  band-envelope test; confirm no supported band-envelope derive legitimately has
  `sos is None`, and that coherence/bandpower derives reliably have `sos is None`.
- **Fixture realism.** Keep the new fixture minimal but representative of the real
  ~80% pattern (single percentile `above` over a band envelope); avoid accidentally
  re-introducing an orphan threshold.
