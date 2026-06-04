# HRV Biofeedback Support — Design

**Date:** 2026-06-03
**Status:** Design (approved in brainstorming; pending written-spec review)
**Scope:** Asks 1, 2, 4 from the Coherence Recorder feature request (the
non-breaking **M1 bundle**). Ask 3 (multi-rate / multi-source inputs) is
explicitly **deferred** to its own scoped effort — see §7.
**Source request:** `coherence-recorder/.../docs/refrain-hrv-feature-request.md`
(evaluated by the recorder team against **v0.6.3**; re-verified here against the
current tree, post-`v0.7.0`).
**Ships as:** `v0.8.0` (additive minor; recorder opts in by moving its pin).

---

## 1. Motivation

The Coherence Recorder is adding **HRV biofeedback** and wants it to ride
Refrain end-to-end rather than grow a parallel feedback engine. Their target
pipeline runs on a 4 Hz cardiac tachogram:

```
bandpass(0.04–0.15 Hz) → hilbert() → magnitude() → smooth(4 s)
  → auto_range(5 min) → percentile threshold(70%) → dwell(5 s) → audio
```

They verified most of what they need already exists (`auto_range`, `coherence`,
`bandpass/hilbert/magnitude/smooth`, percentile thresholds, `dwell`, non-EEG
channel kinds, multiple same-rate named inputs). Re-verification against the
current tree confirms this. Three narrow gaps remain — the M1 bundle:

| Ask | Gap (verified, current tree) | Site |
|---|---|---|
| **1** | `hilbert(kind="iir_allpass")` raises `NotImplementedError`; FIR default `taps=65` → 32-sample group delay = **8 s @ 4 Hz** | `primitive_impls.py:245`, `:219` |
| **2** | `auto_range` / `percentile` start from an empty window every session — no seed, no export. No cross-session adaptive ceiling. | `primitive_impls.py:412`, `:355` |
| **4** | No first-class identity montage; a raw passthrough channel must co-opt `referential(reference:"device")` | `primitive_impls.py:ReferentialImpl` |

### What the request could not see

The recorder team cited only **Python installed-package paths**. This repo now
ships a **parallel Rust core** (`refrain-core/src/{dsp,eval,coherence}.rs`) with
machine-precision parity tests (`equivalence.rs`, `events.rs`, `taps.rs`)
asserting the Rust evaluator reproduces the Python evaluator to 1e-6. Every ask
below is therefore designed around that constraint.

---

## 2. The architectural spine — parity by baking

`refrain-core/src/dsp.rs:3`: *"Coefficients are baked by the Python front-end
(`refrain.ir_json`)."* The Python compiler instantiates the **exact same impl**
the live evaluator uses, reads its computed coefficients (`sos`, FIR taps,
`alpha`, window sizes — `ir_json._extract_coeffs`), and serializes them into
IR-JSON. **Rust never recomputes — it only runs the recurrence**
(`Biquad::new(sos)`, a Direct-Form-II-Transposed match to `scipy.signal.sosfilt`).

Two consequences shape the whole design:

1. **DSP design happens once, in Python.** The hard part of Ask 1 lives entirely
   in `primitive_impls.py`; Rust parity is *structural*, not re-derived.
2. **Runtime state is not IR.** The tracker buffers behind Ask 2 are runtime
   state, not coefficients — so seed/export is an `Evaluator` API concern and the
   **IR-JSON schema stays frozen**.

### Shared guarantees

- **Strictly additive.** No existing protocol changes behavior. Existing parity
  fixtures stay byte-identical; we only *add* fixtures. The parity suite is the
  regression guardrail.
- **Opt-in.** New `Evaluator` kwargs default to today's behavior; the new
  grammar name is used by nothing existing.
- **SemVer-clean.** Additive minor → `v0.8.0`. Nothing is forced on consumers
  pinned to `v0.6.3`/`v0.7.0`.

---

## 3. Ask 1 — `hilbert(kind="iir_allpass")`

### 3.1 Approach

Implement the analytic signal as **two parallel all-pass branches expressed as
second-order sections (SOS)** — an in-phase branch `H_re(z)` and a quadrature
branch `H_im(z)` whose phase responses differ by ~90° across the passband:

```
analytic(x) = H_re(x) + j · H_im(x)
```

Each branch is a cascade of biquad all-pass sections, designed deterministically
in Python (polyphase half-band all-pass decomposition; pure scipy/numpy at
construction, exactly as `bandpass` uses `scipy.signal.butter`). `ir_json` bakes
the two SOS arrays. **Rust reuses its existing `Biquad` runner**, one instance
per branch; the new Rust code is essentially:

```rust
let re = self.branch_re.step(x);   // existing Biquad
let im = self.branch_im.step(x);   // existing Biquad
Complex::new(re, im)               // analytic stream
```

### 3.2 Why this framing

A from-scratch IIR recurrence in Rust would duplicate DSP and invite parity
drift. SOS-over-`Biquad` makes Rust parity structural — the same numbers, the
same Direct-Form-II-Transposed cascade already pinned by `equivalence.rs` for
`bandpass`. The new primitive inherits that proven path.

### 3.3 Streaming contract

`HilbertFirImpl` returns `delayed_real + j·hilbert_imag` with the real branch
delayed by the FIR group delay. The IIR version is analogous but both branches
are all-pass filters with persistent biquad state (`zi`) carried across chunks,
matching `BandpassImpl`'s streaming form. Output dtype is complex, same as the
FIR path, so `magnitude()` downstream is unchanged.

### 3.4 The FIR path is untouched

`kind="fir"` (the default) keeps `taps=65` and every coefficient. Filling in the
`iir_allpass` branch converts a `raise` into working behavior — **error → working
is strictly additive**. No existing protocol can reach the new branch today
(it raises), so no fixture moves.

### 3.5 Latency: honest gate, not a promise

At 4 Hz the recorder's band (0.04–0.15 Hz) sits at **2–7.5% of Nyquist** — very
close to DC, where *any* IIR Hilbert has its worst in-band group delay and phase
accuracy. The primitive is general and clearly benefits EEG (bands at higher %
of Nyquist). For the recorder's exact config, acceptance is a **hard test gate**:

> LF-band envelope on a 4 Hz tachogram, **added in-band group delay < ~1 s**,
> phase-split accuracy stable across 0.04–0.15 Hz, measured from the baked
> coefficients via `scipy.signal.group_delay` / `sosfreqz` (transfer-function
> evaluation, independent of the streaming cascade).

If the near-DC config cannot make the budget at a sane order, the spec records
the **sanctioned fallback** — the recorder's own option 3, `rectify() + smooth()`
— and flags **complex demodulation** as the natural follow-up primitive. Either
way `iir_allpass` ships and helps EEG; we do not assert the 4 Hz latency number
until a test proves it.

### 3.6 Components touched

- `primitive_impls.py`: replace `HilbertIirAllpassImpl.__init__` stub with the
  SOS-pair design + streaming `step`. Expose the two SOS arrays for baking.
- `ir_json.py`: `_extract_coeffs` learns the two-SOS shape for the IIR Hilbert.
- `dsp.rs`: a small `HilbertIir` consuming two baked SOS, reusing `Biquad`.
- `eval.rs`: wire the IR node to `HilbertIir` (mirrors the FIR wiring).

---

## 4. Ask 2 — seed + export of adaptive state (compact summary)

### 4.1 What is saved (decided in brainstorming)

A **compact, rate-independent, human-readable summary** per stateful tracker —
*not* a raw buffer blob. Continuity means "feedback well-scaled from sample 1
and a ceiling that rises week to week," not bit-exact buffer restoration.

```jsonc
export_state = {
  "rhythm_strength.auto_range": { "low": 0.0123, "high": 0.0481, "n_eff": 1200 },
  "rs_t.percentile":            { "value": 0.0402, "target_pct": 70, "n_eff": 1200 }
}
```

**Scope of trackers:** `auto_range` and `percentile` (including the
`PercentileThreshold` subclass). `smooth` is fast-settling and **out of scope**.

### 4.2 Export

New `Evaluator.export_state() -> dict[str, dict]`, **separate from
`last_taps()`** so the strict tap key-set parity test (`taps.rs`) is untouched.
Per tracker:

- `auto_range` → `{low, high, n_eff}` (the live percentile anchors + effective
  sample count).
- `percentile` → `{value, target_pct, n_eff}`.

Keys reuse the **canonical tap-key naming** already proven in `taps.rs`
(`<entity>.<primitive>`), so the host addresses state with names it already
knows.

### 4.3 Seed

New optional `Evaluator(..., seed_state: dict | None = None)` kwarg. At pipeline
build, each named tracker **pre-fills its rolling window with a deterministic
synthetic distribution that reproduces its anchors**, sized by `n_eff`
(clamped to `window_samples`). Because the seed is expressed as *initial buffer
contents*:

- the existing `step()` runs unchanged — no new blend logic in the hot path;
- **both backends apply the identical pre-fill**, so parity holds by
  construction;
- unseeded construction (`seed_state=None`) is **bit-identical to today's cold
  start**.

The synthetic filler is a fixed, documented function of `(low, high, n_eff)`
(resp. `value, target_pct, n_eff`) so seed→export round-trips are stable to
within the tracker's quantization — the accepted approximation of the
compact-summary choice.

### 4.4 The Rust side

Seeding is a runtime operation in **both** evaluators. The seed dict crosses the
uniffi boundary as an optional construction argument; `eval.rs` applies the same
deterministic pre-fill to the Rust tracker buffers; `export_state()` reads the
Rust anchors back. A new parity test pins (a) export equality and (b)
seed-determinism Python↔Rust to 1e-6.

### 4.5 Non-breaking confirmation

- IR-JSON: **unchanged** (state is runtime, not IR).
- `last_taps()` key set: **unchanged** (export is a separate accessor).
- `Evaluator.__init__`: **additive** kwarg, defaults to current behavior.
- uniffi: an additive optional ctor arg + one new accessor; existing mobile call
  sites still compile, consumers recompile to use the new surface.

### 4.6 Components touched

- `primitive_impls.py`: `AutoRangeImpl` / `PercentileImpl` gain
  `export_state()` and a `seed(...)` pre-fill; deterministic filler helper.
- `eval_.py`: `Evaluator.__init__(seed_state=...)`, `Evaluator.export_state()`,
  pipeline wiring keyed by canonical name.
- `eval.rs` / `python.rs` / `mobile.rs`: mirror seed apply + export read across
  the boundary.

---

## 5. Ask 4 — `passthrough()` montage

### 5.1 Approach

A first-class identity montage. `montage = passthrough()` resolves to a
single-channel identity (the channel unchanged), replacing the
`referential(reference:"device")` workaround.

- **Parser** (`parser.py` / grammar): accept `passthrough()` as a montage call.
- **Resolver** (`resolver.py`): map it to an identity montage IR node.
- **Impl**: a trivial `PassthroughImpl` returning the channel unchanged (or
  reuse the proven `reference == "device"` / `use_hardware_reference` path).
- **Rust**: resolves to identity in the IR; **no new DSP** — the montage stage
  passes the channel through.

### 5.2 Non-breaking confirmation

A new grammar name nothing existing uses. Old IR never emits a `passthrough`
node, so old IR still deserializes. Purely additive; no behavioral change vs.
the documented workaround.

---

## 6. Testing strategy (TDD)

Failing test first for every ask. New fixtures via `tools/gen_fixtures.py`.

| Ask | Python tests | Parity / Rust tests |
|---|---|---|
| 1 | IIR Hilbert produces analytic signal; **latency/accuracy gate** at 4 Hz (0.04–0.15 Hz) via `group_delay`/`sosfreqz` on baked coeffs; EEG-rate sanity | new `equivalence` fixture: Rust `HilbertIir` matches Python to 1e-6 |
| 2 | `export_state()` shape + values; `seed_state` pre-fill reproduces anchors; round-trip stability; unseeded == cold-start (bit-identical) | new fixture: seed-determinism + export equality Python↔Rust to 1e-6 |
| 4 | `passthrough()` parses, resolves to identity, evaluates unchanged vs. raw channel | identity montage parity (trivial) |
| all | **existing fixtures asserted unchanged** (regression guardrail) | `equivalence/events/taps.rs` green |

---

## 7. Out of scope — Ask 3 (deferred)

Ask 3 (≥2 named inputs at **independent sample rates / sources** for true
breath↔HR coherence) is the only ask that touches a **breaking surface**:
`step_chunk(raw_chunk: np.ndarray)` encodes the single-input, single-rate
assumption, and `fanout.py:79` hard-caps "at most one input montage in v1."
Making it additive (keep `step_chunk` identical, add a multi-input feed path,
default per-input rate to the chosen amp rate, backward-compatible IR
deserialization) is feasible but is its own multi-session effort, and it is
**shared with the dual-device-sync project** (EEG↔EEG synchrony). It gets its own
spec. The `coherence` operator it needs already exists.

---

## 8. Risks & open questions

1. **Ask 1 latency at 4 Hz (primary risk).** Near-DC band may exceed the ~1 s
   budget at a sane all-pass order. Mitigation: the gate test decides; documented
   fallback (`rectify+smooth`) keeps the recorder unblocked; the primitive ships
   regardless for EEG. Resolve empirically in TDD before claiming the number.
2. **Seed round-trip drift.** Synthetic-buffer pre-fill reproduces anchors
   approximately. Acceptable per the compact-summary decision; pinned by a
   round-trip-stability test with a documented tolerance.
3. **uniffi surface growth.** Additive, but mobile consumers must regenerate
   bindings. Flag in `CHANGELOG.md` / `docs/RUST-CORE-HOST-BRIEF.md`.

---

## 9. Files touched (summary)

- `src/refrain/primitive_impls.py` — IIR Hilbert; tracker seed/export.
- `src/refrain/ir_json.py` — bake two-SOS IIR Hilbert coefficients.
- `src/refrain/eval_.py` — `seed_state` kwarg, `export_state()`, wiring.
- `src/refrain/parser.py` + grammar, `src/refrain/resolver.py` — `passthrough()`.
- `refrain-core/src/{dsp,eval,python,mobile}.rs` — IIR Hilbert runner; seed/export
  across the boundary; identity montage.
- `tests/…`, `refrain-core/tests/…`, `tools/gen_fixtures.py` — new fixtures.
- `docs/PRIMITIVES.md`, `docs/IR-JSON.md`, `CHANGELOG.md` — document the new
  surface and version bump.
