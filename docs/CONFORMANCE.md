# Conformance suite

`refrain-core/tests/fixtures/` is the canonical conformance corpus. It
contains **golden vectors**: the exact `(seeded input, IR-JSON, reference
output)` triples that any IR-JSON runtime must reproduce within tolerance.
No duplication and no separate tooling are needed — the corpus is generated
from the canonical Python evaluator and tested by the existing Rust suite.

---

## 1. What it is

Each fixture bundle is a record of one complete run of the Python reference
evaluator (`src/refrain/eval_.py`) over a seeded pseudo-random signal. A
conforming runtime is one that, given the same IR-JSON and the same seeded
input, produces outputs that agree with the references within the stated
tolerance.

**Supported wire versions.** A conforming runtime refuses at load any document
whose `refrain_ir_version` is not one it supports, rather than running it with
the unknown semantics silently dropped. As of 0.25.0 the supported set is:

| `refrain_ir_version` | What first requires it |
|---|---|
| `"0.1"` (also: the tag absent) | The baseline shape this document describes |
| `"0.2"` | Composite reward, staged sessions |
| `"0.3"` | A control baseline-`seed` rule |
| `"0.4"` | Autopilot, named reward checks |
| `"0.5"` | Typed feedback, a dwell/rearm-bound output, baked complex FIR taps |
| `"0.6"` (new in 0.25.0) | A hold `tolerance` that bakes to one or more samples, an inhibit `kind = "artifact"`, a seed `scale` other than `1.0`, or a non-empty block `reseed` |

Each version's schema is a superset of the previous one's fields, so a runtime
that supports `"0.6"` reads every earlier document. The normative list is
`SUPPORTED_IR_VERSIONS` in `refrain-core/src/ir.rs`; the per-version field
documentation and the exact version-bump predicate are in `docs/IR-JSON.md`
§1 and §3.x. Note that the predicate is behavioural, not syntactic: a
`tolerance` that rounds to zero samples at the document's rate, an inhibit
labelled `kind = "trained"`, and an explicit `scale = 1.0` each emit nothing
and do not raise the version.

**Tooling (source only — do not modify or regenerate fixtures manually):**

- `refrain-core/tools/gen_fixtures.py` — generates all fixture files from the
  canonical Python evaluator. Run via `.venv/bin/python
  refrain-core/tools/gen_fixtures.py` from the worktree root.
- `refrain-core/tools/check_equivalence.py` — the full drift gate: (1)
  regenerates fixtures, (2) runs `cargo test`, (3) builds the `refrain_core`
  wheel, (4) runs the behavioral evaluator suite under
  `REFRAIN_EVAL_BACKEND=rust`. Gated in CI as `rust-equivalence`.

---

## 2. Bundle format

Each bundle is identified by a `<stem>` (e.g. `micro_02_bandpass`,
`realistic_smr`). The files making up a bundle:

### `<stem>.ir.json`

The IR-JSON wire input: the protocol fully resolved and type-checked, with
filter coefficients baked at the fixture's runtime sample rate (256 Hz for all
current fixtures). Consumed by the runtime's deserializer; see `docs/IR-JSON.md`
for the full schema.

Top-level shape (from `realistic_smr.ir.json`):

```json
{
  "refrain_ir_version": "0.1",
  "name": "smr_cz_v1",
  "extends": null,
  "sample_rate_hz": 256.0,
  "channels": ["Cz"],
  "requires": { ... },
  "meta": { ... },
  "inputs": { ... },
  "derives": { ... },
  "thresholds": { ... },
  "inhibits": { ... },
  "reward": { ... },
  "output": { ... },
  "controls": { ... },
  "session": { ... },
  "topological_order": [...]
}
```

### `<stem>.io.json`

Seeded input signal, runtime parameters, and reference output streams. Shape
(from `realistic_smr.io.json`):

```json
{
  "sample_rate_hz": 256.0,
  "channels": ["Cz", "A1", "A2"],
  "chunk_size": 32,
  "warmup_samples": 512,
  "n_samples": 4096,
  "seed": 0,
  "input": [[...], ...],
  "streams": {
    "raw": [...],
    "smr_envelope": [...],
    ...
  }
}
```

- `input` — `n_samples × n_channels` row-major array (seeded
  `rng.standard_normal * 10`, seed 0).
- `streams` — reference output streams from the Python evaluator, keyed by
  user name (not canonical prefixed name). Boolean streams are cast to `float64`
  (0.0/1.0) for numeric comparison.
- `sample_rate_hz` and `channels` — the runtime parameters to pass to the
  evaluator constructor.

### `<stem>.events.json`

Reference feedback `Event` list (present for event-bearing protocols only:
`micro_05_reward`, `micro_09_inhibit`, `realistic_smr`, and
`realistic_smr_setcontrol`). A flat JSON array:

```json
[
  { "timestamp_s": 0.0, "channel": "audio_gain", "kind": "value", "value": 0.0 },
  { "timestamp_s": 0.0, "channel": "game_speed", "kind": "value", "value": 0.0 },
  ...
]
```

Each object has `timestamp_s` (float), `channel` (string), `kind`
(`"value"` or `"event"`), and `value` (float).

### `<stem>.taps.json`

Per-chunk `last_taps()` snapshots (present for tap-bearing protocols:
`micro_09_inhibit`, `realistic_smr`, and `realistic_smr_setcontrol`). A JSON
array of length `n_samples / chunk_size`, one map per chunk:

```json
[
  {
    "input/raw": -13.76,
    "derive/smr_envelope": 1.086e-05,
    "threshold/smr_t": 2.73e-06,
    "muted": 0.0,
    "reward/continuous": 0.9999,
    "reward/event": 0.0,
    "reward/event.holds": 0.0,
    "reward/condition[0]": 1.0,
    ...
  },
  ...
]
```

All values are floats (boolean taps cast to 0.0/1.0).

### `realistic_smr_setcontrol.{events,taps,schedule}.json`

A live-retuning scenario layered on top of `realistic_smr`. The same seeded
signal is run with a mid-session `set_control` call applied at a specific
chunk. The schedule file describes when and what changes:

```json
{
  "at_chunk": 64,
  "changes": [
    { "name": "smr_target_pct", "value": 55.0 },
    { "name": "theta_target_pct", "value": 45.0 }
  ]
}
```

The corresponding `.events.json` and `.taps.json` are the reference outputs
from the Python evaluator with that retuning applied.

---

## 3. How a runtime self-validates

1. Deserialize `<stem>.ir.json` into the runtime's protocol representation.
2. Construct the evaluator using `sample_rate_hz` and `channels` from
   `<stem>.io.json`.
3. Call `start(skip_warmup=True)` (the fixtures were generated with warmup
   skipped to avoid startup-transient divergence).
4. Feed the seeded `input` array in chunks of `chunk_size` samples.
5. After each chunk, collect output streams, events, and taps.
6. Compare to the reference values within tolerance (see Section 4).

For `realistic_smr_setcontrol`: replay the `realistic_smr.ir.json` protocol
with the same signal; at chunk `at_chunk`, call `set_control` for each entry
in `changes`; compare `.events.json` and `.taps.json` to the references.

**Exception — `seed_smr_baseline`, `seed_exprpos`, `seed_scale_emg` and
`reseed_staged`:** baseline-seeding (§2.6) controls are seeded from a statistic
measured *only during warmup*, so these four bundles are generated with
`skip_warmup=False` (warmup intact) instead of step 3 above. `reseed_staged`
needs it for one step more: a re-seed measures the signal since the previous
seeding, so the warm-up seed has to have happened for the boundary re-seed to
mean anything. Every other bundle in the corpus follows step 3; add a stem here
if a fifth seeding bundle is ever generated.
A runtime self-validating one of these bundles must drive it with the
lifecycle-aware entry point that advances the warmup→run cursor (Rust:
`step_chunk_events`, which auto-starts with `skip_warmup=false` and advances
the phase cursor; read the cached per-chunk streams via `last_streams()`) —
the streams-only `step_chunk` does not advance the cursor, so the seed would
never fire and every `output/fb` sample would incorrectly read `0.0`.

---

## 4. Tolerance methodology

| Stream type | Comparison method | Tolerance |
|---|---|---|
| Continuous float streams | `|actual - ref| ≤ atol + rtol * |ref|` | `atol=1e-6, rtol=1e-4` |
| Boolean event/condition streams | exact equality (0.0/1.0) | — |

These are the bench-harness tolerances used in `equivalence.rs`. The observed
worst-case deviation across the entire corpus is approximately **1e-13**
(machine precision), consistent with both evaluators running the same f64
arithmetic on the same coefficients. Boolean streams (above/below conditions,
dwell event, `muted`) are compared exactly — a single-sample timing
disagreement in a reward event is a genuine failure.

---

## 5. Coverage table

Each row maps a fixture stem to the primitives and features it exercises.

**Reach of this table: it is a guide, not an inventory.** It lists 16 of the 30
bundles in `refrain-core/tests/fixtures/` (29 stems with their own `.ir.json`,
plus `realistic_smr_setcontrol`, which replays `realistic_smr.ir.json`). The 14
it does not describe are `advisor_ap`, `autopilot_alpha_theta`,
`autopilot_staged`, `composite_smr_theta`, `micro_10_autocorr`,
`micro_11_control_expr`, `micro_12_control_param_slots`,
`micro_passthrough_identity`, `output_dwell_event`, `output_dwell_rearm`,
`output_dwell_rearm_tunable`, `seed_exprpos`, `seed_run` and
`seed_smr_baseline`. They are part of the corpus and a conforming runtime must
reproduce them; they are simply not written up here yet. Take the directory
listing as the authoritative corpus, never this table.

| Stem | Primary feature(s) |
|---|---|
| `micro_01_passthrough` | Referential montage passthrough (`referential` callee; no derives) |
| `micro_02_bandpass` | Bandpass biquad SOS (`bandpass`; baked `sos` coefficients) |
| `micro_03_envelope` | Hilbert + magnitude + smooth envelope (`hilbert` with baked `fir_taps`/`group_delay`, `magnitude`, `smooth` with baked `alpha`) |
| `micro_04_threshold` | Rolling percentile threshold (`percentile` with baked `window_samples`; `Threshold` block) |
| `micro_05_reward` | Dwell + sigmoid + reward outputs (`dwell` with baked `dwell_samples`, `sigmoid`, `above`/`below`/`all_of`; `events.json` present) |
| `micro_06_coherence` | Welch MSC coherence (`coherence` with baked `nperseg`/`noverlap`) |
| `micro_07_ilf` | Bipolar montage + infra-low-frequency pipeline (`bipolar`, `bandpass`, `differentiate`, `rectify`, `smooth`, `auto_range`, `sigmoid`, `dwell`, `inside`) |
| `micro_08_bandpower` | Bandpower (`bandpower` + auto-range normalization) |
| `micro_09_inhibit` | Inhibit gate + output muting (`Inhibit` block, `muted` tap, `bandpower` metric; `events.json` and `taps.json` present) |
| `micro_13_band_envelope` | One-stage band envelope (`band_envelope` with baked `fir_taps_re`/`fir_taps_im`/`group_delay`; IR-JSON v0.5). Deliberately the same band and channel as `micro_03_envelope`, so the two bundles are the four-stage and one-stage forms of one measurement. |
| `micro_14_tolerance` | Hold dip tolerance (`dwell` with baked `tolerance_samples`; IR-JSON v0.6) paired with a `reward.event.holds`-gated continuous channel, so a disagreement about the dip counter shows up as a whole output value rather than a rounding difference |
| `micro_15_artifact_gate` | Inhibit `kind = "artifact"` — the observability mask, checked before the condition, so artifact-gated samples freeze a hold's streak and dip counter instead of resetting them (IR-JSON v0.6). Covers both hold paths in one bundle: a `hold_for` bound directly in `output { }` and a `dwell` reward event. `release: 350 ms` outlasts the 100 ms tolerance, which is the case the label has to survive |
| `seed_scale_emg` | Seed `scale` multiplier (`percentile { target_pct = 50; scale = 3.0 }`; IR-JSON v0.6) — the worked per-person muscle limit at three times the trainee's own warm-up median, with `sqrt` putting the gate in amplitude so the multiplier means what the spec says. Generated `skip_warmup=False` |
| `reseed_staged` | Block-boundary re-seeding (`reseed` plus the baked `reseed_holds` set; IR-JSON v0.6) — a staged session whose second block re-measures the threshold the first block trained against, resetting the downstream dwell streak and recording two `seed_report()` history entries. Generated `skip_warmup=False` |
| `realistic_smr` | Full clinical protocol: 3-band envelope pipeline, percentile + absolute thresholds, dwell+`all_of`, sigmoid continuous reward, conditional output expressions, `control_ref` thresholds, session phases (`events.json` and `taps.json` present) |
| `realistic_smr_setcontrol` | Live `set_control` retuning mid-run: same signal as `realistic_smr`, SMR and theta percentile targets changed at chunk 64; percentile buffer preserved across the change (`events.json`, `taps.json`, `schedule.json` present) |

---

## 6. Structural validation

The `src/refrain/schema/ir-json-v0.1.schema.json` (JSON Schema Draft 2020-12)
validates the structural shape of any `.ir.json` file: required fields, node
discriminator values, and the closed `Coeffs` field set.

For the full field-level documentation of the IR-JSON format — including the
`Expr` tagged union, all `node` variants, the `Coeffs` fields, and the two
host rules (sample-rate baking, physical channel layout) — see `docs/IR-JSON.md`.
