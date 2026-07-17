# IR-JSON — versioned wire contract

IR-JSON is the boundary between the Python front-end
(`parse` → `resolve` → `refrain.ir_json`) and the portable Rust core
(`refrain-core`). The Python evaluator is the single source of truth for
*authoring*; this format lets any conforming runtime evaluate a protocol
without depending on the Python parser or SciPy.

---

## 1. Overview and version

| Field | Value |
|---|---|
| Version string | `"0.1"` |
| Source constant | `IR_JSON_VERSION = "0.1"` in `src/refrain/ir_json.py` |
| Schema | `src/refrain/schema/ir-json-v0.1.schema.json` |

### Compatibility policy

- **Minor-compatible:** adding new optional top-level fields or new optional
  fields inside existing objects. Consumers MUST ignore unknown fields.
  The Rust core does this — its serde structs are not `deny_unknown_fields`.
- **Breaking (major version bump required):** changing a `node` discriminator
  value; removing or renaming a required field; changing the semantics of any
  coefficient field.

The schema root declares `"additionalProperties": true`, formalizing the
ignore-unknown-fields contract at the structural-validation level.

### Version enforcement

A runtime refuses at load any document whose `refrain_ir_version` is not in the
set it supports (`SUPPORTED_IR_VERSIONS`, `refrain-core/src/ir.rs`), with a
diagnostic naming the offending version. A document with no tag is treated as
`0.1`. This is what makes adding a new IR field safe: an old runtime cannot
silently ignore semantics it does not implement, because it will not load the
document at all.

---

## 2. Two host rules

These rules are enforced by the wire format and must not be violated.

### Rule 1 — sample rate is baked

Coefficients in IR-JSON are designed for the runtime sample rate the host
chooses, which may differ from `requires.sample_rate_chosen_hz`. For example,
a protocol that declares `sample_rate_chosen_hz: 2048` can be baked at 256 Hz
for an amp that streams at that rate; the realistic_smr fixture demonstrates
this exactly (`requires.sample_rate_chosen_hz = 2048`, `sample_rate_hz = 256`
in the baked fixture).

The protocol's `requires` block declares only a *minimum* (`sample_rate_min_hz`).
The host chooses any rate at or above that minimum, bakes the IR-JSON at that
chosen rate, and passes that same rate to the runtime constructor. Shipping a
mismatched rate silently mis-tunes every IIR/FIR filter.

### Rule 2 — the runtime channel layout is a host input, not the wire `channels`

Two channel lists exist and are easy to confuse:

- The IR-JSON **`channels`** field is the protocol's *required* (logical)
  channels. For `realistic_smr` this is `["Cz"]`. It is `ir.requires.channels`
  when the protocol declares one; a protocol may legally omit that declaration
  (a `placement` control substitutes its bound electrodes into the montage at
  resolve time, so the BrainBit `placement_*` protocols declare none at all),
  and the field then lists the electrodes the input montages name. Either way it
  is never empty — the schema pins it to `minItems: 1`. The `requires.channels`
  sub-field always echoes the declaration verbatim, so it *may* be empty; read
  top-level `channels` for the effective list.
- The **physical acquisition layout** the host passes to the runtime constructor
  (`Evaluator.live(..., channel_names=...)` / `RustEvaluator(ir_json,
  sample_rate_hz, channels)`) is the complete electrode set, *including reference
  electrodes*. A protocol that requires only `["Cz"]` but uses
  `referential(active: "Cz", reference: "linked_ears")` needs A1 and A2 present, so
  the host passes `["Cz", "A1", "A2"]`. Montages resolve electrode names against
  this *runtime* layout; passing only the protocol's logical channels causes a
  name-resolution failure.

The physical layout is **not** stored in the `.ir.json` wire object — it is a
runtime input. The conformance vectors record it separately in the `.io.json`
`channels` field (see `docs/CONFORMANCE.md`).

---

## 3. Top-level object

The keys below are emitted by `refrain.ir_json.ir_to_json_obj`. All are
present in the serialized output (verified against `realistic_smr.ir.json`):

| Key | JSON type | Meaning |
|---|---|---|
| `refrain_ir_version` | `"0.1"` (string const) | Wire-format version; MUST be `"0.1"`. |
| `name` | `string \| null` | Protocol name (`smr_cz_v1`, etc.). |
| `extends` | `string \| null` | Parent protocol name, if any. |
| `sample_rate_hz` | number | Baked runtime sample rate (host choice, ≥ `requires.sample_rate_min_hz`). |
| `channels` | array of string (non-empty) | Protocol's *required* channels, e.g. `["Cz"]` — `ir.requires.channels`, or the electrodes the montages name when the protocol declares none (see Rule 2). NOT the host's physical electrode layout (that is a runtime input). |
| `requires` | object | Hardware requirements block (`coupling`, `sample_rate_min_hz`, `sample_rate_chosen_hz`, `channels`, `impedance`, `markers`). Its `channels` echoes the author's declaration verbatim and may be empty; top-level `channels` is the effective list. |
| `meta` | object | Authoring metadata; values are `Expr` nodes (typically `string` or `array`). |
| `inputs` | object | Named `Input` objects, keyed by user name. |
| `derives` | object | Named `Derive` objects, keyed by user name. |
| `thresholds` | object | Named `Threshold` objects, keyed by user name. |
| `inhibits` | object | Named `Inhibit` objects, keyed by user name. |
| `reward` | object or null | Single `Reward` object (`continuous`/`event` exprs) or null. |
| `output` | object | Named `Expr` output bindings. **Declaration order is preserved** (insertion order = event-emission order). |
| `controls` | object | Named `ControlDecl` objects, keyed by bare control name. |
| `session` | object or null | `Session` with `phases` array, or null. |
| `topological_order` | array of string | Evaluation order of all named nodes (canonical prefixed names, e.g. `control/smr_target_pct`, `input/raw`, `derive/smr_envelope`, `threshold/smr_t`). Drives derive evaluation in the runtime. |

**Order semantics:**
- `output` must be deserialized in wire order; the Rust core uses `IndexMap`
  to preserve it, and `eval_chunk` emits events in that same sequence.
- `topological_order` governs when each derive is evaluated relative to its
  upstream inputs and thresholds.

---

## 4. The `Expr` tagged union

Every expression node is a JSON object with a `"node"` field that acts as the
discriminator. The Rust side deserializes this with `#[serde(tag = "node")]`
on the `Expr` enum.

The full list of variants, taken from the schema `$defs` and confirmed against
`src/refrain/ir_json.py` and `refrain-core/src/ir.rs`:

| `"node"` value | Required fields | Optional fields | Notes |
|---|---|---|---|
| `number` | `value` (number) | `dims`, `unit` | Numeric literal with dimensional annotation. |
| `string` | `value` (string) | — | String literal. |
| `bool` | `value` (boolean) | — | Boolean literal. |
| `stream_ref` | `target` (string) | `stream_type` | Reference to a named input or derive stream by canonical name (e.g. `"input/raw"`, `"derive/smr_envelope"`). |
| `threshold_ref` | `target` (string) | `stream_type` | Reference to a named threshold by canonical name (e.g. `"threshold/smr_t"`). |
| `control_ref` | `target` (string), `default` (number) | `dims` | Reference to a clinician control. `target` is the canonical name (e.g. `"control/smr_target_pct"`); `default` is the value used until `set_control` arrives. Preserves the binding so the runtime can route live updates. |
| `reward_field` | `field_path` (string) | `stream_type` | Accesses a named field of the reward block (e.g. `"event.holds"`). |
| `call` | `callee` (string) | `args` (array of `Arg`), `coeffs` (`Coeffs` or null), `stream_type` | Primitive call. Each `Arg` has `name` (string or null) and `value` (Expr). `coeffs` is present when Python successfully baked coefficients at emit time. |
| `array` | `elements` (array of Expr) | — | Homogeneous array literal. |
| `tuple` | `elements` (array of Expr) | — | Heterogeneous tuple literal (e.g. frequency band `(12.0, 15.0)`). |
| `binop` | `op` (string), `left` (Expr), `right` (Expr) | `stream_type` | Binary operator. `op` is `"/"`, `"+"`, `"-"`, or `"*"`. |
| `conditional` | `cond` (Expr), `then` (Expr), `else` (Expr) | `stream_type` | Ternary conditional. The key is literally `"else"` in the JSON; the Rust field is renamed `els` due to keyword conflict. |
| `block` | `fields` (object: string → Expr) | `kind` (string or null) | Structured block used internally; `kind` names the block type. |

---

## 5. Baked-coefficient fields (`Coeffs`)

When a `call` node's primitive has precomputable coefficients, the emitter
instantiates the Python impl and reads its computed attributes, then includes
them as a `"coeffs"` object. This means Python owns filter *design* (via SciPy)
and the wire format carries the *designed* coefficients; the runtime only runs
the deterministic recurrence or convolution — it never reimplements SciPy.

All fields are optional; which are present depends on the primitive:

| Field | JSON type | Populated by |
|---|---|---|
| `sos` | array of `[number × 6]` rows | `bandpass` (biquad SOS cascade). Each row is `[b0, b1, b2, a0, a1, a2]`. |
| `fir_taps` | array of number | `hilbert` (FIR Hilbert transformer coefficients). |
| `group_delay` | integer | `hilbert` (half the FIR length; used to compensate latency). |
| `alpha` | number | `smooth` (exponential decay coefficient `α = dt / (τ + dt)`). |
| `dt` | number | `smooth` (sample period in seconds). |
| `window_samples` | integer | `percentile` (rolling window length in samples). |
| `dwell_samples` | integer | `dwell` (minimum dwell duration in samples). |
| `nperseg` | integer | `coherence` (Welch segment length). |
| `noverlap` | integer | `coherence` (Welch segment overlap). |

The schema declares `"additionalProperties": false` on `Coeffs` — this is the
one object in the format that is closed to extension, so runtimes can rely on
the exact field set above.

---

## 6. Worked example

The following is an annotated excerpt of
`refrain-core/tests/fixtures/realistic_smr.ir.json`, showing the flow from a
montage input through a DSP derive carrying baked coefficients, a percentile
threshold, reward, and conditional output expressions.

```json
{
  "refrain_ir_version": "0.1",
  "name": "smr_cz_v1",
  "sample_rate_hz": 256.0,
  "channels": ["Cz"],              // electrodes to acquire, in channel-index order
                                   // (ir.requires.channels; falls back to the
                                   // electrodes the montages name — see below)
  ...

  // Physical montage: Cz re-referenced to linked ears (the host passes the
  // physical layout ["Cz","A1","A2"] to the runtime constructor — not shown here)
  "inputs": {
    "raw": {
      "canonical_name": "input/raw",
      "montage": {
        "node": "call",
        "callee": "referential",
        "args": [
          { "name": "active",    "value": { "node": "string", "value": "Cz" } },
          { "name": "reference", "value": { "node": "string", "value": "linked_ears" } }
        ]
      }
    }
  },

  // Derive: bandpass(12-15 Hz) → hilbert → magnitude → smooth
  // Each stage carries its baked coefficients
  "derives": {
    "smr_envelope": {
      "canonical_name": "derive/smr_envelope",
      "expression": {
        "node": "call", "callee": "smooth",
        "args": [
          { "name": null, "value": {
              "node": "call", "callee": "magnitude",
              "args": [{ "name": null, "value": {
                  "node": "call", "callee": "hilbert",
                  "args": [{ "name": null, "value": {
                      "node": "call", "callee": "bandpass",
                      "args": [
                        { "name": null,   "value": { "node": "stream_ref", "target": "input/raw" } },
                        { "name": "band", "value": { "node": "tuple", "elements": [
                            { "node": "number", "value": 12.0, "unit": "Hz" },
                            { "node": "number", "value": 15.0, "unit": "Hz" }
                        ]}},
                        { "name": "order","value": { "node": "number", "value": 4.0 } }
                      ],
                      "coeffs": { "sos": [[1.67e-6, 3.34e-6, 1.67e-6, 1.0, -1.819, 0.932], ...] }
                  }}],
                  "coeffs": { "fir_taps": [0.0, -0.00169, ...], "group_delay": 32 }
              }}]
          }},
          { "name": "tau", "value": { "node": "number", "value": 250.0, "unit": "ms" } }
        ],
        "coeffs": { "alpha": 0.015503562994591547 }
      },
      "upstream": ["input/raw"]
    }
  },

  // Threshold: rolling 70th-percentile over 2-minute window
  // target_pct is a control_ref — clinician-tunable live
  "thresholds": {
    "smr_t": {
      "canonical_name": "threshold/smr_t",
      "signal": "derive/smr_envelope",
      "threshold_call": {
        "node": "call", "callee": "percentile",
        "args": [
          { "name": "target_pct", "value": {
              "node": "control_ref",
              "target": "control/smr_target_pct",
              "default": 70.0
          }},
          { "name": "window", "value": { "node": "number", "value": 2.0, "unit": "min" } }
        ],
        "coeffs": { "window_samples": 30720 }
      },
      "live_tunable": true
    }
  },

  // Reward: sigmoid continuous + dwell event (SMR up, theta/hbeta down)
  "reward": {
    "continuous": { "node": "call", "callee": "sigmoid", "args": [
      { "name": null, "value": {
          "node": "binop", "op": "/",
          "left":  { "node": "stream_ref",    "target": "derive/smr_envelope" },
          "right": { "node": "threshold_ref", "target": "threshold/smr_t" }
      }},
      { "name": "midpoint",  "value": { "node": "number", "value": 1.0 } },
      { "name": "steepness", "value": { "node": "number", "value": 3.0 } }
    ]},
    "event": { "node": "call", "callee": "dwell",
      "args": [{ "name": "condition", "value": { "node": "call", "callee": "all_of", ... } },
               { "name": "duration",  "value": { "node": "number", "value": 250.0, "unit": "ms" } }],
      "coeffs": { "dwell_samples": 64 }
    }
  },

  // Output: declaration order is preserved; the runtime emits events in this order
  "output": {
    "audio_chime": { "node": "reward_field", "field_path": "event" },
    "audio_gain":  { "node": "conditional", "cond": ..., "then": ..., "else": ... },
    "game_speed":  { "node": "conditional", "cond": ..., "then": ..., "else": ... }
  },

  // Topological order drives derive evaluation sequence
  "topological_order": [
    "control/smr_target_pct", "control/theta_target_pct",
    "input/raw",
    "derive/smr_envelope", "derive/theta_envelope", "derive/high_beta_envelope",
    "threshold/smr_t", "threshold/theta_t", "threshold/hbeta_t"
  ]
}
```

---

## 7. Validation pointers

- **Structural validation** — `src/refrain/schema/ir-json-v0.1.schema.json`
  (JSON Schema Draft 2020-12). Validates node shapes, required fields, and
  the closed `Coeffs` field set.

- **Behavioral validation** — `docs/CONFORMANCE.md` describes the golden-vector
  conformance suite: the set of `(IR-JSON, seeded input, reference outputs)`
  bundles that any runtime must reproduce.

See also `docs/REPRODUCIBILITY.md` for the CRED-nf reproducibility argument
built on top of this wire contract.
