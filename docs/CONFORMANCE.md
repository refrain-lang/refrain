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
Verified against the actual fixture files in `refrain-core/tests/fixtures/`.

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
