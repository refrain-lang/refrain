# Protocol fuzzer — Increment 1 (re-scoped): single-condition + center/bandwidth — design

> Status: approved design (brainstorm complete), ready for the TDD plan.
> Parent: [[2026-06-28-fuzzer-parity-roadmap-design]].
> **Supersedes** [[2026-06-28-fuzzer-inc1-single-condition-design]] — corpus
> evidence (below) shows single-condition alone unlocks ~0 real protocols; the two
> features are a per-protocol conjunction, so this increment does both.
> Builds on Increment 0 (`fuzz/runner.py`, `UnsupportedProtocol`, batch + CI, #51).

## Goal

Make the first increment that actually moves the `refrain-protocols` corpus by
supporting the two entangled features that gate most of it together:

1. **Single-condition reward** — a sole `dwell(above/below(...))` leaf (no
   `all_of`/`any_of`).
2. **`center:`/`bandwidth:` bandpass** — the alternative bandpass declaration form.

Together these fuzz the **absolute** single-leaf protocols (above & below) and the
multi-leaf protocols that use center/bandwidth. Percentile single-leaf protocols
are deferred to a calibrated-oracle increment (see below).

## Corpus evidence (why combine — measured on the 59-file library)

Reward shape × threshold kind, read directly from the IR:

| shape | absolute | percentile | no-threshold |
|---|--:|--:|--:|
| single `above` | 10 | 13 | 6 |
| single `below` | 6 | 9 | — |
| multi-leaf (`all_of`/`any_of`) | 13 combined | | |
| non-dwell reward | 2 | | |

With single-leaf support **assumed**, what still blocks `build_surface`:

```
35  center/bandwidth bandpass     ← dominant gate (59% of corpus)
13  multi-leaf (already builds)
 7  single-leaf entangled (composite/coherence/no-threshold)
 2  clean single-leaf — both PERCENTILE (oracle-blocked)
 2  AttributeError (a separate long-tail bug)
 0  clean ABSOLUTE single-leaf
```

Every absolute single-leaf protocol is **also** blocked by `center/bandwidth
bandpass`. So single-condition alone unlocks nothing; center/bandwidth is the
choke point. Doing both is the minimal combination that fuzzes real protocols.

## Decisions locked in brainstorming

- **Combined increment**: single-condition (above & below) + center/bandwidth,
  both oracle-independent.
- **Percentile single-leaf is deferred** to a calibrated-oracle increment. A bare
  percentile leaf produces systematic oracle↔engine disagreement: the engine fires
  on the rolling percentile of the *actual noisy* envelope (quiet EEG crosses its
  own 70th percentile; a sustained spike raises its own percentile), while the
  analytic oracle models idealized rank intent. Multi-leaf masks this; single-leaf
  exposes it. It skips with a specific reason.
- **Fixtures**: add clean absolute single-leaf fixtures (above & below) — the refrain
  repo has none.

## Part A — single-condition support

### Supportability detector (`surface.py`)

`_reward_condition_from_ir` returns `ConditionNode | ConditionLeaf`. For a sole
`ConditionLeaf` (post derive/threshold build, so the derives+thresholds are
available), classify:

- **Supported** → return the leaf, iff the signal names a derive with a baked SOS
  **and** the threshold resolves **and** the threshold is `absolute`.
- **Skip** (`UnsupportedProtocol`) otherwise:
  - signal not a derive (empty/reward-field) → `composite-signal reward condition` (Inc for weighted composite).
  - signal is a derive with `sos is None` → `non-bandpass (coherence) reward signal`.
  - threshold does not resolve → `reward condition without a resolvable threshold`.
  - threshold is `percentile` → `single percentile-leaf reward (needs calibrated oracle)`.

`reward_condition`'s type annotation becomes `ConditionNode | ConditionLeaf`. The
`all_of`/`any_of` path is unchanged; the non-dwell fallthrough stays `ValueError`.

### Generator: single-leaf-safe (`generate.py`)

The v1 generator is smr_cz-shaped; three fixes make it correct for a sole leaf,
each a **provable no-op** for the 4 currently-fuzzed `all_of` protocols:

1. **Driven derive.** `_dwell_scenarios`, `_percentile_warmup_scenarios`, and
   `generate_hold_duration_sweep` hardcode a derive named `smr_envelope`. Replace
   with the reward condition's **driven derive** = the first `above`-leaf's derive
   (single leaf → its own derive). Returns `smr_envelope` for all 4 → no-op.
2. **Op-aware drive direction (above + below).** The "hold the reward-positive
   state" scenarios assume above-semantics (spike up → reward). For a sole `below`
   leaf, reward-positive is the band *low*, so the dwell/hold scenarios must
   **pre-roll a spike (FALSE) then go quiet for the hold window (TRUE)** — the
   inverted figure-ground. The per-leaf *pivotal* scenarios are already op-aware.
3. **Scope + gate the smr_cz-specific extras.**
   - `_percentile_warmup_scenarios`: emit only when the reward references a
     percentile threshold (absolute-only single-leaf ⇒ skip it; else it MISSES).
   - `generate_characterization_probe`: for a **single-leaf** reward, skip it (the
     per-leaf pivotal scenarios already drive the reward band with gain-compensated
     amplitude and assert true/false; the dedicated band probe uses a fixed,
     non-gain-compensated amplitude that mismatches a lone absolute leaf). For
     `all_of` it is unchanged. Dedicated single-leaf band-characterization is a
     later refinement.
   - `generate_rank_sweep`: percentile-only, so absolute single-leaf emits none;
     for percentile protocols scope it to reward-referenced thresholds.

Verified: with these, an absolute single-`above` fixture fuzzes **10/10 PASS**
(core scenarios: pivotal ±, dwell met/missed, hold sweep). Below uses the inverted
driver.

## Part B — center/bandwidth bandpass support (`surface.py`)

`_band_from_call` currently raises `UnsupportedProtocol("center/bandwidth
bandpass")` when a bandpass call has no `band=(lo,hi)` arg. The `center:`/`bandwidth:`
form is `bandpass(center: <val>, bandwidth: ratio(<n>), order: 4)`. The **baked SOS
is always present in IR-JSON** regardless of declaration form (verified), and the
oracle's gain math already reads the SOS — so this is purely a band-*reading*
change in `surface.py`:

- When there is no `band` arg, derive the band edges from the **baked SOS passband**
  (the source of truth that exists in every form) — compute the passband center and
  −3 dB edges from the SOS via the existing `bandpass_gain_at` / `scipy.signal`
  path. (Alternatively compute from `center`/`bandwidth` args, but the SOS is the
  robust, always-present source and matches what the engine actually runs.)
- `DeriveSurface.band` is used only for the scenario tone center `0.5*(lo+hi)` and
  the `BandSegment` band; the −3 dB window is sufficient for both.
- For edge-frequency (`band=(lo,hi)`) protocols this path is untouched — no-op.

## Skip taxonomy (surfaced in the batch by-reason breakdown)

New / refined reasons: `single percentile-leaf reward (needs calibrated oracle)`,
`composite-signal reward condition`, `non-bandpass (coherence) reward signal`,
`reward condition without a resolvable threshold`. The Increment-0
`center/bandwidth bandpass` reason disappears (now supported).

## New fixtures

The refrain repo has no clean single-leaf example; add two minimal, absolute-threshold
ones (model on `examples/smr_cz.refrain`, single leaf, one derive/threshold, no
orphans, derive **not** named `smr_envelope` so the generalization is exercised):

- `bench/protocols/micro_single_above.refrain` — sole `above(env, absolute)`.
- `bench/protocols/micro_single_below.refrain` — sole `below(env, absolute)` (inverted driver).

Both fuzz clean. Coverage on the refrain corpus rises `fuzzed 4 / total 22` →
`fuzzed 6 / total 24` (existing `othmer_ilf_t3t4` center/bandwidth protocol may also
flip once Part B lands — confirm on re-probe).

## Components touched

- `src/refrain/fuzz/surface.py` — single-leaf detector; `reward_condition` type;
  center/bandwidth band reading in `_band_from_call`.
- `src/refrain/fuzz/generate.py` — driven derive; op-aware drive; gate the
  smr_cz-specific extras.
- `bench/protocols/micro_single_above.refrain`, `…/micro_single_below.refrain`.
- Tests under `tests/fuzz/`. `oracle.py` needs **no change** (SOS-based gain math
  already works for both features).

## Testing (TDD)

- **Detector**: both new fixtures build (no raise); a percentile single-leaf →
  `single percentile-leaf reward (needs calibrated oracle)`; composite/coherence/
  no-threshold → their reasons; `all_of` still builds.
- **center/bandwidth**: a center/bandwidth protocol (`othmer_ilf_t3t4`, or a
  refrain-protocols fixture copied in) builds a surface with a sensible band derived
  from the SOS; an edge-frequency protocol's band is unchanged (no-op).
- **End-to-end fuzz**: `fuzz_protocol` on each new fixture (above & below) → `FUZZED`,
  `passed is True`, non-vacuous.
- **Generator no-op guard**: for an `all_of` surface (`realistic_smr`) the generated
  corpus is byte-identical (labels + coverage tags) before/after.
- **Batch coverage**: real-corpus aggregate rises to `fuzzed 6 / total 24` (refrain
  repo) with the entangled protocols under their new reasons; exit 0.

All via `.venv/bin/python -m pytest tests/fuzz/ -q`; `src/refrain/fuzz/` ruff-clean.

## Success metric (re-run the corpus probe)

After merge, re-run the `refrain-protocols` probe. Expected: the ~16 absolute
single-leaf protocols that were double-blocked (single-leaf + center/bandwidth) now
fuzz (minus any with a *third* blocker); the 22 percentile single-leaf skip under
the calibrated-oracle reason. This confirms the empirical unlock before the next
increment (which should be the **calibrated oracle** — now the largest remaining
lever at 37% of the corpus).

## Out of scope

- **Calibrated oracle / percentile single-leaf** — the next increment; the largest
  remaining slice (37%). This increment only *labels* it precisely.
- Weighted-composite reward, coherence, inhibit, bandpower, staged (later increments).
- Derive-vs-derive / literal-RHS single leaves (`no-threshold`).
- The 2 `AttributeError` long-tail protocols.
- Raising the CI `--max-scenarios` cap.

## Risks / open questions for the plan

- **Below inverted driver.** Reproduce a sole `below` fixture per-scenario; confirm
  the pre-roll-then-quiet dwell/hold scenarios are non-vacuous and oracle-agreeing.
- **center/bandwidth band from SOS.** Confirm the SOS→band-edge computation gives a
  center frequency that drives the leaf correctly (the tone must land in the
  passband). Validate on `othmer_ilf_t3t4` and a refrain-protocols center/bandwidth
  protocol.
- **`all_of` byte-identical guard.** The driven-derive + extra-gating changes must
  be provable no-ops for the 4 current protocols; the regression test compares full
  corpora.
- **Third blockers.** Some of the 16 absolute single-leaf protocols may have a
  *further* unsupported feature; the success metric counts actual flips, not the
  theoretical 16.
