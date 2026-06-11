# Dynamical flutter-cue protocol (NeurOptimal-style) — design

> Status: approved design (brainstorm complete), ready for an implementation plan.
> Builds on `main` (v0.9.0 — the `number` control kind).
> Origin: explore whether a NeurOptimal-style *dynamical* neurofeedback protocol —
> detect "flutter" (a surge in EEG variability that precedes a phase-state shift)
> and cue the trainee with a brief audio "takeaway" — is expressible in Refrain, and
> what minimal language additions make it clean.
> Scope decided with the requester: build the **single-configuration** mechanism now;
> the session-arc ("phases" / macro-AutoNav) and infra-low bands go to the gap RFC.

## Goal

Reproduce, transparently and runnably, the *core mechanism* of NeurOptimal — not its
proprietary math. Strip the source material to its signal-processing essence:

1. **Sense** — C3/C4, referential to linked ears, ≥256 Hz (NeurOptimal's montage).
2. **Decompose** — ~10 band envelopes per hemisphere ("Time-Frequency Envelopes").
3. **Detect flutter** — a brief *surge in the variability* of a band envelope, the
   signature that precedes a phase-state shift. The literature calls this an
   *early-warning signal / critical slowing down*: rising **variance** and rising
   **lag-1 autocorrelation** near a critical transition.
4. **Cue** — on flutter, a brief **negative-feedback "takeaway"**: the tonic audio is
   muted for a few hundred ms, then resumes. No targets, no operant reward — the
   interruption is information; the brain's orienting response does the rest.

This is a **defensible reconstruction in the same spirit**, grounded in published
science, fully auditable, and runnable on the reference evaluator. It is research
software, not a clone and not a medical device (see Limitations).

## Scientific grounding

"Variance surge" is one half of the **early-warning-signals / critical-slowing-down**
framework. As a dynamical system approaches a tipping point it recovers more slowly
from perturbations, which shows up as rising variance *and* rising lag-1
autocorrelation of the signal.

- **Scheffer et al. (2009),** *Nature* 461:53–59 — "Early-warning signals for critical
  transitions." The foundational theory: variance + autocorrelation rise before a
  critical transition. <https://www.nature.com/articles/nature08227>
- **Dakos et al. (2012),** *PLoS ONE* 7(7):e41010 — the practical methods toolbox for
  computing these indicators on a time series.
  <https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0041010>
- **Maturana et al. (2020),** *Nat. Commun.* 11:2172 — "Critical slowing down as a
  biomarker for seizure susceptibility": variance + autocorrelation rise before
  seizures in human iEEG — a real brain state-transition reading out as a variance
  surge. <https://www.nature.com/articles/s41467-020-15908-3>
- **Yang, Shew, Roy & Plenz (2012),** *J. Neurosci.* 32(3):1061–1072 — "Maximal
  Variability of Phase Synchrony…": at the cortical critical point the variability of
  synchrony is maximal. (Cited by the source material.)
  <https://www.jneurosci.org/content/32/3/1061>

The detector uses **both** indicators (the full, validated signature): fast **variance**
for responsiveness, **lag-1 autocorrelation** (critical slowing down) for specificity.

## Scope boundary

**In scope (built now):**

- One new primitive — `autocorr(input, lag, window, detrend)` — full shared-core
  treatment (Python + Rust parity + IR-JSON + docs + tests + drift gates).
- A **band fan-out**: author the per-envelope subgraph once, replicate it over a
  `bands { … }` list (× the two channels) — extends the existing per-site fan-out.
- The example protocol `flutter_cue.refrain`: C3/C4, 10 bands/hemisphere (20
  envelopes), per-envelope EWS detector, takeaway feedback, clinician controls.
  Resolves and runs on the evaluator.

**Out of scope (→ gap RFC, `docs/proposals/`):**

- The **ZenX session phases** (warm-up → letting-go → working-deeply → release) and
  **macro-AutoNav** auto-flow between them. Refrain already expresses staged sessions
  (`session { phases }`, staged-protocols design) and the host can orchestrate; the
  flutter mechanism doesn't need the arc. The *micro* level of AutoNav (adaptive
  sensitivity) **is** in scope — it falls out of the adaptive threshold for free.
- **Infra-low / sub-Hz bands** (NeurOptimal claims to 0.001 Hz). Variance/autocorrelation
  there need minutes-long windows — impractical for a live cue. Capped at ~1 Hz.
- A **first-class negative-feedback "takeaway" output**. We express the takeaway today
  by repurposing `inhibit { … mute(release:) }` over a contingency-free `reward = 1.0`;
  the RFC proposes a real takeaway output so this isn't a (clean but inverted) hack.
- **AND-combine over arbitrary booleans.** We get tunable OR↔AND today by fusing
  *normalized* indicators into one score (below); a general boolean→takeaway path is an
  RFC item alongside the first-class output.

## Current state (what we extend)

- **Primitives** are registered in `src/refrain/primitives.py`, implemented as streaming
  classes in `src/refrain/primitive_impls.py`, budget-accounted in `src/refrain/cost.py`,
  resolved in `src/refrain/resolver.py`, wired in `src/refrain/eval_.py`, emitted to wire
  in `src/refrain/ir_json.py`. **`coherence` is the exact template** for `autocorr`: a
  windowed streaming statistic with warm-up, a dedicated Rust module
  (`refrain-core/src/coherence.rs`), and Python↔Rust parity gated by
  `refrain-core/tools/check_equivalence.py`. `autocorr` gets a sibling `autocorr.rs`.
- **Fan-out** lives in `src/refrain/fanout.py` (27 KB): given a parameterized input it
  computes the transitive-closure subgraph and replicates it, naming copies
  `<name>@<site>`, emitting a **flat AST using only existing node types**. It currently
  handles **one** `set` placement (the site axis). The band fan-out reuses this machinery
  with the replication axis swapped to a band list (substituting the `(low, high)` tuple
  into the `bandpass(band:)` slot) and composes with the channel axis for the cross
  product (the one genuinely new capability — see Design §3).
- **Inhibits** (`src/refrain/…`, SPEC §4.6): `metric` + `threshold` + `action`; the action
  modifies output delivery, and multiple active inhibits **OR-compose** on muting.
  `mute(release: d)` gates output to zero and holds for `d` after the metric clears.
- The **`number` control kind** (v0.9.0) is the tunable scalar we use for the fuse weight.

## Design

### 1. Protocol architecture (signal flow)

Montage — two referential inputs (NeurOptimal's C3/C4, linked ears):

```refrain
input "raw_c3" { montage = referential(active: "C3", reference: "linked_ears") }
input "raw_c4" { montage = referential(active: "C4", reference: "linked_ears") }
```

Per **band b × channel ch** (authored once, fanned out — see §3), the EWS detector:

```refrain
// band envelope
derive "env"   { from = "raw"; pipeline = [ bandpass(band: b), hilbert(), magnitude() ] }

// fast indicator: rolling variance  Var = E[e²] − E[e]²
derive "var"   { formula = smooth("env" * "env", tau) - smooth("env", tau) * smooth("env", tau) }
derive "varN"  { from = "var"; pipeline = [ auto_range(window: norm_win) ] }   // → [0,1]

// principled indicator: critical slowing down (lag-1 autocorrelation)
derive "ac1"   { from = "env"; pipeline = [ autocorr(lag: ac_lag, window: ac_win) ] }  // → [−1,1]
derive "acN"   { from = "ac1"; pipeline = [ auto_range(window: norm_win) ] }   // → [0,1]

// fuse into one EWS score; the fuse function IS the OR↔AND knob
derive "score" { formula = ("varN" > "acN") ? "varN" : "acN" }   // max() = OR-like (default)
//             weighted blend:  fuse_w * "varN" + (1 - fuse_w) * "acN"
//             AND-like:        "varN" * "acN"

// adaptive, self-calibrating threshold (= micro-AutoNav) + the takeaway
inhibit "flutter" {
  metric    = "score"
  threshold = percentile(target_pct: sensitivity, window: 2 min)
  action    = mute(release: hold_ms)
}
```

Global — tonic audio, taken away on any flutter:

```refrain
reward { continuous = 1.0 }                  // contingency-free; no operant target
output { audio_gain = reward.continuous }    // 1.0 tonically; any active flutter inhibit mutes it
```

The host supplies the actual audio track and maps `audio_gain` onto playback gain;
the protocol contains no audio.

### 2. New primitive — `autocorr`

```
autocorr(input: stream_ref,
         lag:    int | duration,
         window: duration,
         detrend: bool = true)         -> stream<scalar in [-1, 1]>
```

**Behavior.** Rolling lag-`k` Pearson autocorrelation over `window`:

```
ρ_k(t) = Σ_i (x_i − x̄)(x_{i−k} − x̄) / Σ_i (x_i − x̄)²       over the window ending at t
```

- `lag` as `int` = samples; as `duration` = converted to samples at the input rate
  (must resolve to ≥1 sample). The window-mean centering (`x̄`) provides the baseline
  detrend; `detrend=true` additionally removes a slow within-window linear trend
  (standard EWS practice). Output dimensionless in `[-1, 1]`.
- **Warm-up:** returns `0.0` until the window holds ≥ `window` samples (and ≥ `lag`+2),
  exactly like `coherence`. Downstream `auto_range`/`percentile`/`above` handle a 0
  reading correctly.
- **Streaming/cost:** ring buffer of `window` samples + running sums (Σx, Σx²,
  Σ x_i x_{i−k}); O(1) per sample. State ≈ `window × rate × 8 B`; declared in `cost.py`.
- **Authoring guard (the oversampling footgun):** at 256 Hz, lag-1-*sample*
  autocorrelation ≈ 1 always. Compute `autocorr` on the **band envelope** (already slow)
  with `lag` set to a meaningful interval, and/or `decimate` first. The default control
  values pick a sane envelope-timescale lag; documented in PRIMITIVES.md.

**Plug-in points** (mirror `coherence`): register signature in `primitives.py`; streaming
class in `primitive_impls.py`; budget in `cost.py`; resolver entry (positional→named like
coherence) in `resolver.py`; evaluator wiring in `eval_.py`; IR-JSON node + schema bump in
`ir_json.py` + `refrain-core/schema/`; Rust module `refrain-core/src/autocorr.rs` + wire in
`eval.rs`/`lib.rs`/`python.rs`; PRIMITIVES.md + SPEC entries; parity fixtures gated by
`check_equivalence.py`.

### 3. Band fan-out (two-axis replication)

Author the §1 subgraph **once**; declare the bands; the resolver fans it out.

```refrain
bands {
  delta  = (1 Hz, 4 Hz)    theta1 = (4 Hz, 6 Hz)    theta2 = (6 Hz, 8 Hz)
  alpha1 = (8 Hz, 10 Hz)   alpha2 = (10 Hz, 12 Hz)  smr    = (12 Hz, 15 Hz)
  beta1  = (15 Hz, 18 Hz)  beta2  = (18 Hz, 22 Hz)  beta3  = (22 Hz, 30 Hz)
  gamma  = (30 Hz, 45 Hz)
}
```

- The band axis reuses `fanout.py`'s transitive-closure replication, substituting the
  `(low, high)` tuple into `bandpass(band:)` (analogous to substituting a site into a
  montage channel slot), naming copies `env@beta1`, `score@beta1`, `flutter@beta1`, ….
- **The one genuinely new capability:** a *second* replication axis (the two channels)
  → the cross product of `{bands} × {C3, C4}` = 20 envelopes. Current `fanout.py` handles
  a single `set`. The implementation plan chooses the composition: (a) reuse the existing
  per-site `set` for the channel axis and add a band axis, composing the two passes into a
  cross product; or (b) a generalized multi-axis fan-out. Foundation (single-axis
  transitive-closure replication, flat-AST emission) is unchanged; this is the design
  question the plan resolves.
- Emits a **flat AST of existing node types** — the Rust core and IR-JSON are unaffected
  by fan-out itself (front-end only, like placement Mode 2a). Only `autocorr` touches the
  core/wire.

### 4. Band plan

10 bands/hemisphere × C3/C4 = **20 envelopes** (matching NeurOptimal's "10 TFEs per
hemisphere"), practical EEG range:

| band | Hz | band | Hz |
|---|---|---|---|
| delta | 1–4 | beta1 | 15–18 |
| theta1 | 4–6 | beta2 | 18–22 |
| theta2 | 6–8 | beta3 | 22–30 |
| alpha1 | 8–10 | gamma | 30–45 |
| alpha2 | 10–12 | | |
| smr | 12–15 | | |

Sub-Hz (→0.001 Hz) is out (RFC). Edges are clinician-adjustable via the `bands` block.

### 5. Clinician controls (the AutoNav-analog surface)

| control | kind | role |
|---|---|---|
| `sensitivity` | `percent` | target percentile of the EWS score; higher ⇒ fewer, more selective takeaways. Self-calibrating per trainee per band = **micro-AutoNav**. |
| `hold_ms` | `number`/duration | takeaway length + refractory (`mute(release:)`). |
| `tau` | duration | variance time-constant. |
| `norm_win` | duration | `auto_range` window for normalizing each indicator before fusion. |
| `ac_lag`, `ac_win` | duration | autocorrelation lag + window (latency vs. stability). |
| `fuse_w` | `number` (v0.9.0) | OR↔AND blend weight when the weighted fuse is used. |

### 6. The takeaway, precisely

`reward.continuous = 1.0` ⇒ `audio_gain` is tonic 1.0. Each `flutter@<band>` inhibit, when
its EWS score crosses the adaptive percentile, fires `mute(release: hold_ms)`; because
inhibits OR-compose on output delivery, **any** envelope's flutter mutes the audio for
`hold_ms`, then it resumes. That is the brief informational interruption — no reward to
chase, which is the entire point.

> **Implementation-verification item:** confirm `mute` gates a *constant* output binding
> (`audio_gain = 1.0`), not only reward-derived terms. PRIMITIVES.md says inhibits gate
> output delivery (it should); the constant-`reward`-plus-`audio_gain = reward.continuous`
> shape is chosen specifically so the muted value is unambiguously reward-derived and the
> behavior is guaranteed. A parity test asserts the takeaway fires on both backends.

## Limitations / honest caveats

- **Reconstruction, not a clone.** Variance + lag-1 autocorrelation is *a* defensible
  operationalization of "flutter," grounded in the EWS literature the source cites. It is
  not NeurOptimal's proprietary JTFA/NDS math.
- **Autocorrelation latency.** ρ_k needs a window to estimate, so CSD is inherently
  laggier than variance — hence variance is kept as the fast co-indicator.
- **Real-time CSD is less validated than slow (seizure-forecast) CSD,** which used
  hours-long windows. Short-window CSD for a live cue is a reasonable but research-grade
  choice; the protocol's `evidence` field reflects this.
- **Not a medical device.** Research software; no regulatory clearance; clinical use is
  the investigator's responsibility (per repo disclaimer).

## Testing

- **Parity:** `autocorr` Python↔Rust to machine precision via `check_equivalence.py`
  fixtures (the `coherence` parity pattern). Add `flutter_cue` to the protocol corpus.
- **Numerical:** `autocorr` vs. a reference (e.g. NumPy windowed autocorrelation) on
  synthetic signals with known ρ; verify warm-up returns 0 and bounds `[-1, 1]`.
- **Fan-out:** assert the fanned 20-envelope AST matches a hand-written reference, and
  that the cross-product naming (`env@beta1`, …) and scoping check pass.
- **Behavioral:** synthetic "calm → variance/autocorrelation surge → calm" drives a
  takeaway (mute) on both backends; quiet input produces tonic audio with no mutes.

## Deliverables

1. **`autocorr` primitive** + **band fan-out** (the two new language pieces).
2. **`flutter_cue.refrain`** example protocol (resolves + runs; demo uses host-side music).
3. **Gap RFC** in `docs/proposals/` (next section).

## Gap RFC outline (separate doc)

`docs/proposals/2026-06-11-dynamical-neurofeedback-gaps.md`:

- **Session phases / macro-AutoNav** — modeling the ZenX arc and automatic flow between
  configurations (relation to `session { phases }` + staged-protocols).
- **First-class negative-feedback "takeaway" output** — so pure-negative-feedback
  protocols don't need the constant-reward-plus-inhibit shape; plus a general
  boolean→takeaway path (enables AND-combine on raw conditions).
- **Infra-low / sub-Hz bands** — DC coupling, minute-scale windows; relation to the
  Othmer ILF single-slow-band example; why fast variance/CSD doesn't apply there.
- **Critical-slowing-down methodology** — detrending options, decimation, window-vs-latency
  guidance; possible higher-level `early_warning()` composite.
- **Band fan-out generalization** — multi-axis fan-out as a first-class feature (now
  prototyped here for band × channel).

## References

See "Scientific grounding". Source material: the NeurOptimal overview PDF (Zengar
Institute) supplied with this request.
