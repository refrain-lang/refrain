# Explicit-Band Envelope Editing — Design

**Date:** 2026-06-17
**Status:** Approved (design); ready for plan
**Repos touched:** `refrain` (catalog), `refrain-editor` (TS package), `coherence-recorder` (editor UI + pin), `refrain-protocols` (library)

---

## Problem

The protocol editor ("Clone & edit…") can only clone protocols whose structure the
editor catalog recognizes. In practice the clinician-facing protocols bundled in
the recorder (`coherence-recorder/recorder/plugins/refrain/protocols/`,
20 brainbit, device-specific protocols) **all decline** — 16 on the envelope
shape, 4 on placement. So there is effectively nothing to clone in the recorder
today except one HRV protocol.

The decline is **not** a band-accuracy problem. It is purely that the bundled
protocols express their band as explicit edges:

```
bandpass(band: (12 Hz, 15 Hz), order: 4)
```

while the editor catalog's only envelope block, `derive.envelope`, matches the
center-and-ratio form:

```
bandpass(center: env_center, bandwidth: ratio(1.25), order: 4)
```

### The two forms are exactly equivalent

The engine resolves a center+ratio band to edge frequencies as
(`refrain/src/refrain/primitive_impls.py:225`):

```
low  = center / sqrt(ratio)
high = center * sqrt(ratio)
```

so `center = sqrt(low * high)` (geometric mean) and `ratio = high / low`. For
example `center 13.4164 Hz, ratio 1.25` is exactly `(12 Hz, 15 Hz)`. Neither form
distorts the band; they differ only in **what is exposed as tunable**:

- **center+ratio** exposes a single slide-able center — natural for
  individualized-peak protocols (e.g. peak-alpha, where tuning the center to a
  person's alpha peak is the clinical intent).
- **explicit edges** expose two band edges — natural and more legible for fixed
  standard bands (SMR 12–15, theta 4–8), which is how those are specified
  clinically.

## Goal

Teach the editor the explicit-edge envelope form, and let clinicians edit the two
band edges directly. Standardize the fixed-band protocols in the `refrain-protocols`
library on the same form, keeping center-controls only where the center is the
genuine clinical variable.

---

## Design

### 1. New catalog block `derive.envelope_band` (refrain)

A second envelope shape alongside `derive.envelope`.

- **Matcher** (`src/refrain/editor/describe.py`, `_match_derive`): recognize a
  pipeline whose call sequence is `[bandpass, hilbert, magnitude, smooth]` (len 4)
  where `bandpass` has a literal `band: (lo Hz, hi Hz)` tuple and **no** `center`
  argument. The absence of `center` (and presence of `band`) is what disambiguates
  it from `derive.envelope`. Produce:

  ```json
  {"name": "...", "block": "derive.envelope_band", "from": "raw",
   "slots": {"band_low_hz": 12, "band_high_hz": 15, "order": 4, "smooth_tau_ms": 250}}
  ```

  `band_low_hz`/`band_high_hz` are the two `NumberLit` Hz values from the tuple;
  `order` from the bandpass `order` arg (default 4); `smooth_tau_ms` from the
  `smooth(tau:)` arg via the existing `_to_ms`.

- **Catalog entry** (`src/refrain/editor/catalog.json`): a new block with slot
  types `band_low_hz: frequency`, `band_high_hz: frequency`, `order: number`,
  `smooth_tau_ms: duration_ms`, and a template that emits the explicit-edge form:

  ```
  bandpass(band: ({band_low_hz} Hz, {band_high_hz} Hz), order: {order}),
        hilbert(),
        magnitude(),
        smooth(tau: {smooth_tau_ms} ms)
  ```

- **Render** (`src/refrain/editor/render.py`): data-driven from the catalog
  template like every other block; no special-casing. Round-trips byte-for-byte.

- **Schema** (`src/refrain/editor/protocol-model.schema.json`): allow the new slot
  shape on a derive node.

### 2. Editable band edges in the editor (refrain-editor + recorder)

This is the **first editor surface for a derive slot** — everything to date edits
*control* defaults via `ControlDefaultField`. The model/describe/render plumbing
already round-trips derive slots; only the form UI is missing.

- A **"Bands" section** in `ProtocolEditor`: for each `derive.envelope_band` derive,
  render two number inputs — **Low Hz** and **High Hz** — editing
  `model.derives[i].slots.band_low_hz` / `band_high_hz`. `order` and
  `smooth_tau_ms` are shown read-only (out of Phase-1 tuning scope).
- The live `.refrain` preview updates on edit (existing `renderProtocol`); Save
  renders the edited model (existing save path — no new round-trip machinery).
- **Validation:** `0 < band_low_hz < band_high_hz`. Block Save while violated,
  consistent with how control-range validation already gates Save.

### 3. Byte-exact TS parity (refrain-editor)

Port the matcher + catalog block + render to `@refrain-lang/editor` (the catalog
is inlined at build time; render is the data-driven interpreter). Add golden
fixtures: a band-form protocol round-trips describe → model → render to **identical
`.refrain`** under both Python and the TS port. This is the existing parity gate
(`test/parity.test.ts`).

### 4. Recorder integration (coherence-recorder)

- Bump the `refrain` / `refrain-core` pins to the new refrain version that ships
  `derive.envelope_band`.
- Re-vendor the built `@refrain-lang/editor` dist into
  `recorder/frontend/vendor/refrain-editor/`.
- Wire the Bands section into `ProtocolEditor`.

**Result:** the 16 brainbit envelope protocols clone and tune — band edges **and**
their existing `reward%`/`inhibit%` controls.

### 5. refrain-protocols (min + convert)

- **Regression sweep (mandatory):** run `describe_protocol` over all 37 library
  protocols under the new catalog; assert every one still `ok` and `in_subset`.
  Capture as a test/fixture so future catalog changes can't silently drop one.
- **Convert fixed standard-band protocols** (SMR, theta, beta, hi-beta — bands
  that are clinical constants) from center+ratio to the explicit-edge form. Each
  conversion is lossless (`center sqrt(lo*hi), ratio hi/lo` → `band (lo Hz, hi Hz)`).
  **Keep the center-control form** for individualized protocols where the center is
  the clinical variable: `peak_alpha_up_pz`, `alpha_up_pz`/`alpha_down_pz` and
  their baselines, and any other protocol whose `env_center` carries a wide
  individualizing range and "band center" intent. The plan will enumerate the
  exact convert/keep list per file before editing.

### Out of scope (deferred, stated)

- The **4 `placement_*` protocols** stay declining — they require montage/placement
  editing, which is Phase 2 of the editor.
- **Robustness fix (included, not a feature):** `describe._match_requires` currently
  raises `KeyError: 'channels'` on a placement protocol that declares no `channels`
  list; the exception is caught and surfaced as "out of subset", but the matcher
  should decline cleanly (treat missing `channels` as `[]`) rather than rely on the
  catch-all. Small, makes the decline intentional.

---

## Testing

- **refrain:** unit tests for `_match_derive` recognizing `derive.envelope_band`
  and rejecting near-misses (center+band both present, wrong pipeline order);
  render round-trip test; schema-validation test for the new slot shape.
- **refrain-editor:** golden parity fixtures (Python render === TS render) for at
  least one band-form protocol; describe/round-trip unit tests mirroring Python.
- **coherence-recorder:** extend `test_nf_editor.py` — clone a band-form protocol,
  edit an edge, save, assert the written `.refrain` resolves and carries the edited
  edge. Frontend test: editing Low/High Hz updates the preview and gates Save on
  `low < high`.
- **refrain-protocols:** the regression sweep above; spot-check a converted
  protocol resolves to an IR identical to its pre-conversion form (band edges
  unchanged).

## Success criteria

1. A bundled brainbit SMR protocol clones in the recorder, its band edges and
   reward% are editable, and Save writes a resolvable `.refrain`.
2. All 37 library protocols still clone; converted ones produce identical bands.
3. Byte-exact Python/TS render parity holds for band-form protocols.
4. The 4 placement protocols decline cleanly (no incidental KeyError).
