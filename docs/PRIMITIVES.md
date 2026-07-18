# Refrain Standard Library v0.0

**Status:** strawman draft (v0.0r1)
**Companion docs:** [`SPEC.md`](./SPEC.md), [`TOUR.md`](./TOUR.md)

This document lists the primitives in the Refrain v0.0 standard library, plus the cross-cutting facilities (stream arithmetic, rate alignment, event streams) that protocols use throughout. The library is intentionally small — it covers what's needed to faithfully express SMR/theta-beta, Othmer ILF, alpha-theta, and basic z-score training. Coverage of more advanced clinical NF protocols (source-space NF, network coherence training, phase-based protocols) requires additional primitives slated for v0.1+.

Each primitive entry follows a consistent shape:

- **Signature** — input and output stream types
- **Parameters** — required and optional, with defaults
- **Behavior** — semantics in plain language
- **Example** — minimal usage
- **Notes** — caveats, citations, related primitives

---

## Stream arithmetic

Within `formula` derives, reward expressions, and output bindings, streams can be combined using arithmetic and comparison operators. Operations are **element-wise per sample** with strict unit checking.

### Arithmetic operators

```
stream<T> + stream<T>   ->  stream<T>
stream<T> - stream<T>   ->  stream<T>
stream<T> * stream<T>   ->  stream<T·T>      // unit composition
stream<T> / stream<T>   ->  stream<dimensionless>  if T's match
                            stream<T1/T2>          otherwise
```

Mixing scalar literals with streams broadcasts:

```
stream<T> + T_literal   ->  stream<T>
T_literal * stream<T>   ->  stream<T>
```

### Comparison operators

```
stream<T> > stream<T>   ->  stream<boolean>
stream<T> > T_literal   ->  stream<boolean>
```

Same for `<`, `>=`, `<=`, `==`, `!=`.

### Conditional expressions

The ternary operator `cond ? a : b` produces a stream from a boolean stream and two value streams:

```
stream<boolean> ? stream<T> : stream<T>   ->  stream<T>
stream<boolean> ? stream<T> : T_literal   ->  stream<T>
```

Used heavily in output gating:

```refrain
audio_gain = reward.event.holds ? reward.continuous : 0
```

### Unit safety

The compiler rejects unit-incompatible operations:

```refrain
"smr_envelope" + "orf"   // ERROR: stream<scalar uV> + Hz
```

Operations that reduce dimensionality (e.g., `uV / uV → dimensionless`) are tracked through the type system. Operations that compose units (e.g., `Hz * s → dimensionless`) are also tracked.

---

## Rate alignment

### `align_to`

```
align_to(source: stream_ref, target: stream_ref)  -> stream<T>
align_to(source: stream_ref, rate: Hz)             -> stream<T>
align_to(source: stream_ref, target: stream_ref,
         mode: "hold" | "interpolate" | "average") -> stream<T>
```

Aligns a stream's rate to either another stream's rate or a fixed rate.

- **`mode: "hold"`** (default) — sample-and-hold (forward-fill). Last value persists between samples.
- **`mode: "interpolate"`** — linear interpolation between samples.
- **`mode: "average"`** — when downsampling, average over the window (more accurate for noisy signals).

```refrain
formula = align_to("raw_envelope", target: "auto_ranged_signal") > "auto_ranged_signal"
```

The compiler emits an error when streams of different rates appear in arithmetic without alignment, suggesting the appropriate `align_to` call.

---

## Acquisition

Acquisition primitives transform raw amplifier channels into named input streams.

### `bipolar`

```
bipolar(plus: channel_name, minus: channel_name) -> stream<scalar uV>
```

Differential signal between two electrodes. Output equals `samples[plus] - samples[minus]` per sample.

```refrain
input "ilf" {
  montage = bipolar(plus: "T3", minus: "T4")
}
```

### `referential`

```
// scalar form
referential(active: channel_name,
            reference: channel_name | "linked_ears" | "common_average")
            -> stream<scalar uV>

// vector form
referential(channels: [channel_name, ...],
            reference: ...) -> stream<vector<N> uV>
```

Single-active-electrode signal referenced to another electrode, the average of two earlobe electrodes (`linked_ears`), or the running mean of all available channels (`common_average`).

```refrain
input "raw_19ch" {
  montage = referential(channels: standard_19, reference: "linked_ears")
}
```

### `passthrough`

```
passthrough() -> stream<scalar uV>
```

Identity montage: carries a **single** raw channel through unchanged, with no
software re-referencing. The first-class form of the
`referential(reference: "device")` workaround — use it for non-EEG single-channel
inputs (e.g. an HRV tachogram). Requires a one-channel source; for a
multi-channel source, name the channel with `referential`/`bipolar` instead.

```refrain
input "tachogram" {        // 4 Hz cardiac tachogram, single channel
  montage = passthrough()
}
```

### `select_channel`

```
select_channel(channel_name) : stream<vector uV> -> stream<scalar uV>
```

Extracts a single channel from a multi-channel stream. Used inside `formula` or `pipeline` when you have a 19-channel input but want a single-channel derivation.

### `source_project` *(stub for v0.1)*

```
source_project(operator: norms.inverse_operator,
               roi: roi_name | "all") -> stream<scalar | vector uV>
```

Projects sensor-space signal to source space using a precomputed inverse operator. The operator is a runtime-supplied asset; sensor-space recorder doesn't ship one.

> **v0.0:** stub only; `source_project` is named here for completeness and is subject to design.

---

## Spectral operators

### `bandpass`

```
bandpass(band: (low_Hz, high_Hz), order: int = 4)        -> stream<scalar uV>
bandpass(center: Hz, bandwidth: ratio | (low_Hz, high_Hz),
         order: int = 4)                                  -> stream<scalar uV>
```

Bandpass filter via Butterworth biquad cascade. Two parametrizations supported:

- **Edge-frequency form:** `band: (low, high)` — the standard.
- **Center-bandwidth form:** `center: f, bandwidth: ratio(R)` — convenient for ORF-based protocols where center is a control. The helper `ratio(R)` is a constructor that means edges at `(center / sqrt(R), center * sqrt(R))`.

State is reinitialized via warm-restart on coefficient change (see SPEC §7.7).

```refrain
bandpass(band: (12 Hz, 15 Hz), order: 4)
bandpass(center: orf, bandwidth: ratio(2.5))
```

### `hilbert`

```
hilbert() -> stream<complex uV>
```

Analytic signal via Hilbert transform. Default `kind="fir"` is a windowed FIR with bounded group delay declared in the primitive's budget. Output is complex; pair with `magnitude()` for envelope.

```refrain
pipeline = [
  bandpass(band: (8 Hz, 13 Hz)),
  hilbert(),
  magnitude(),  // -> envelope in uV
]
```

**Low sample rates (e.g. a 4 Hz HRV tachogram).** The FIR Hilbert's group delay is fixed in *samples* (`taps=65` → 32 samples = **8 s at 4 Hz**), which is far too slow for biofeedback. `hilbert(kind="iir_allpass")` provides a low-group-delay analytic signal for EEG-rate bands, but near DC (the 0.04–0.15 Hz band sits at 2–7.5 % of a 4 Hz Nyquist) even an IIR Hilbert is latency-bound. **For low-Fs envelopes, prefer `rectify() + smooth(tau)`** — it adds essentially no latency beyond the `smooth` the protocol already budgets and tracks the rhythm's amplitude faithfully (validated correlation ≈0.96 against a known envelope on a 0.1 Hz rhythm at `tau=4 s`):

```refrain
pipeline = [
  bandpass(band: (0.04 Hz, 0.15 Hz), order: 4),
  rectify(),
  smooth(tau: 4 s),   // -> low-latency LF envelope at 4 Hz
]
```

### `bandpower`

```
bandpower(input: stream_ref, band: (low_Hz, high_Hz),
          window: duration) -> stream<scalar uV2>
```

Total power in a frequency band, computed over a sliding window via Welch's method (or equivalent). The window length determines spectral resolution and reactivity.

```refrain
inhibit "emg" {
  metric = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
  ...
}
```

### `coherence`

```
coherence(input_a: stream_ref, input_b: stream_ref,
          band: (low_Hz, high_Hz),
          window: duration = 1 s) -> stream<scalar in [0, 1]>
```

Magnitude-squared coherence (MSC) between two time-domain streams, averaged over a frequency band. Streaming Welch's method on a sliding window. The output is dimensionless in `[0, 1]`: `1.0` means perfect phase-consistency between the streams in the band; `0.0` means no phase relationship.

```refrain
derive "alpha_coh" {
  formula = coherence(
    input_a: "raw_c3",
    input_b: "raw_c4",
    band:    (8 Hz, 12 Hz),
    window:  2 s
  )
}
```

The signature uses `input_a` / `input_b` rather than `channel_a` / `channel_b` because the operands are streams (potentially already montaged, filtered, or otherwise derived), not raw electrode channels.

**Window choice matters.** Coherence requires multi-segment Welch averaging to produce meaningful values — a single-segment MSC reduces to `1.0` trivially regardless of input. The implementation uses ~250 ms segments at 50% overlap; `window` must be at least 500 ms (~2 segments) and produces meaningful coherence estimates only after the buffer fills. A 1-second window yields ~7 segments of averaging; 2 seconds yields ~15.

**Frequency resolution** is `sample_rate / nperseg`, not `1 / window`. With default settings (~250 ms `nperseg`), that's ~4 Hz resolution — coarse for the 4 Hz–wide alpha band, where a single FFT bin will dominate the band's MSC. For narrow-band coherence work, longer `window` does NOT improve resolution (it only adds more segments); the only way to tighten resolution is to use a higher sample rate.

**Warm-up** behavior: returns `0.0` until the buffer accumulates ≥ 2 segments of data. Approximately 500 ms at typical NF sample rates. Downstream `above` / `dwell` / threshold comparisons handle this correctly (a zero coherence reading is not above any positive threshold).

**Pre-filter or not?** Coherence operates on time-domain signals. You can pass raw referential channels directly (recommended for most NF use cases), or pre-bandpass the streams if you want to isolate a specific frequency before computing coherence. The `band` parameter inside `coherence` is the *analysis* band (which frequencies to average MSC over), not a pre-filter.

`coherence` is the right primitive for *coherence training* — rewarding the patient for increased inter-hemispheric synchrony in a target band. For amplitude-symmetry training (rewarding equal power between channels), use bandpower on each channel and a difference/ratio in the formula derive instead.

> **Out of scope for v0.1:** Phase-locking value (PLV) — a strict phase-alignment measure decoupled from amplitude. Multi-channel coherence reductions (network coherence, weighted average across multiple pairs). Cross-frequency coupling (phase-amplitude coupling, PAC). These may land in v0.2+ as motivated by clinical demand.

---

## Time-series math

### `differentiate`

```
differentiate() -> stream<scalar T/s>
```

First-order time derivative via centered finite differences. Output units are input units per second (e.g., `uV/s`).

```refrain
pipeline = [
  bandpass(center: orf, bandwidth: ratio(2.5)),
  differentiate(),  // uV -> uV/s
]
```

### `smooth`

```
smooth(tau: duration) -> stream<scalar T>
```

Exponential moving average (one-pole IIR low-pass) with time constant `tau`. Preserves units.

```refrain
smooth(tau: 250 ms)   // typical for envelope smoothing
smooth(tau: 1500 ms)  // typical for ILF reward signal
```

### `magnitude`

```
magnitude() -> stream<scalar T>
```

Absolute value of a complex stream (envelope of an analytic signal). For real-valued streams, equivalent to `rectify`.

### `rectify`

```
rectify() -> stream<scalar T>
```

Absolute value of a real-valued stream. Use after differentiation to reward magnitude of change regardless of direction.

```refrain
pipeline = [
  bandpass(center: orf, bandwidth: ratio(2.5)),
  differentiate(),
  rectify(),
]
```

### `decimate` *(internal, usually inserted by compiler)*

```
decimate(target_rate: Hz) -> stream<scalar T>
```

Resamples a stream to a lower rate via polyphase FIR with anti-aliasing. The compiler inserts decimators automatically when downstream primitives declare lower input rates; explicit usage is rare.

---

## Statistics

### `auto_range`

```
auto_range(window: duration,
           percentile: (low_pct, high_pct) = (5, 95))
           -> stream<scalar dimensionless [0, 1]>
```

Maps an input stream to [0, 1] based on rolling percentile statistics. The 5th percentile of recent values maps to 0; the 95th maps to 1; values outside are clipped. Used to provide drift-immune dynamic range maintenance for reward signals.

```refrain
auto_range(window: 5 min, percentile: (5, 95))
```

The percentile estimator keeps the full trailing window and calls `numpy.percentile` (Python) / `percentile_linear` (Rust) once per sample — memory is O(window), not constant. The P² online algorithm (constant-memory, five running markers) is not implemented; see `docs/DESIGN-NOTES.md`. This matters beyond `auto_range`: control baseline seeding (`seed = percentile { ... }`, see `docs/SPEC.md`) leans on the full-buffer representation to read a percentile straight off the warmup prefill. If a P² estimator ever lands, seeding needs rework — P² state is five markers, not a buffer a percentile can be read from directly.

**Cross-session persistence (seed/export).** `auto_range` (and `percentile`)
trackers start cold each session. To carry a user-adaptive ceiling across
sessions, the host can read the final compact state with
`Evaluator.export_state()` — `{ "<entity>.auto_range": {low, high, n_eff}, … }` —
persist it to the patient record, and re-prime the next run with
`Evaluator.live(..., seed_state=<prior export>)`. State is a small,
rate-independent summary (not a raw buffer) and is runtime-only — it does not
change the protocol IR. See `docs/EMBEDDING.md`.

### `percentile`

```
percentile(target_pct: float, window: duration) -> stream<scalar T>
```

Tracks a running percentile of an input stream. Used inside `threshold` declarations as an adaptive threshold.

```refrain
threshold "smr_t" {
  signal = "smr_envelope"
  type   = percentile(target_pct: 70, window: 2 min)
}
```

### `autocorr`

```
autocorr(lag: duration, window: duration) -> stream<scalar in [-1, 1]>
```

Rolling lag-`k` Pearson autocorrelation of a stream over a sliding `window`,
emitted per sample. Returns `0.0` during warm-up (until `lag + 2` samples
accumulate) and `0.0` for a constant window (zero variance). This is the
**critical-slowing-down** early-warning indicator: as a system nears a
phase-state transition it recovers more slowly from perturbations, so its lag-1
autocorrelation rises (Scheffer et al., *Nature* 2009, 461:53–59; validated in
EEG by Maturana et al., *Nat. Commun.* 2020, 11:2172).

```refrain
derive "ac1" {
  from = "alpha_envelope"
  pipeline = [ autocorr(lag: 125 ms, window: 1 s) ]
}
```

**Avoid the oversampling footgun.** At 256 Hz, lag-1-*sample* autocorrelation is
≈1 always (adjacent samples are nearly identical). Compute `autocorr` on a slow
signal (a band *envelope*) and set `lag` to a meaningful interval (e.g.
`125 ms`). `autocorr` mean-centers within its window but does not remove a slow
linear trend — detrend upstream (subtract a long `smooth`) if the signal drifts.

---

## Mappings

### `sigmoid`

```
sigmoid(input: stream<scalar T>, midpoint: T, steepness: float)
  -> stream<scalar [0, 1]>
```

Logistic curve: `1 / (1 + exp(-steepness * (input - midpoint)))`. Used for reward mapping.

```refrain
continuous = sigmoid("reward_signal", midpoint: 0.5, steepness: 4)
```

### `linear`

```
linear(input: stream<scalar T>, midpoint: T, slope: float) -> stream<scalar>
```

Linear function: `slope * (input - midpoint)`. Output is *not* clamped; downstream output bindings clamp to [0, 1] for analog channels.

### `dead_zone` *(planned, v0.1)*

```
dead_zone(input, center: T, width: T) -> stream<scalar>
```

Output is zero within `width / 2` of `center`, then linearly increases. Useful for protocols where small deviations should not modulate reward.

> **v0.0:** named for forward compatibility; not yet implemented.

---

## Conditions

Conditions consume scalar streams and produce boolean streams.

### `above`, `below`, `inside`

```
above(signal_ref, threshold_ref)             -> stream<boolean>
below(signal_ref, threshold_ref)             -> stream<boolean>
inside(signal_ref, low: T, high: T)          -> stream<boolean>
```

Compare a signal to a threshold or range. The threshold may be a literal value or a `threshold` block reference.

```refrain
above("smr_envelope", "smr_t")
below("theta_envelope", "theta_t")
inside("alpha_envelope", low: 5 uV, high: 25 uV)
```

### `all_of`, `any_of`

```
all_of([condition, condition, ...]) -> stream<boolean>
any_of([condition, condition, ...]) -> stream<boolean>
```

Boolean reduction over a list of condition streams.

```refrain
condition = all_of([
  above("smr_envelope", "smr_t"),
  below("theta_envelope", "theta_t"),
])
```

---

## Event-producing primitives

These primitives produce `event_stream` values. An event_stream supports two consumption modes:

- **Direct binding** to event-channel outputs — emits on rising edges of the underlying condition (chime-style)
- **`.holds` member access** — yields `stream<boolean>` indicating current state

A single event_stream supports both consumption modes simultaneously; the runtime maintains the underlying state once.

### `dwell`

```
dwell(condition: stream<boolean>, duration: time) -> event_stream
```

Emits an event when a boolean condition has been continuously true for at least `duration`. The `.holds` view reports current condition state. The rising-edge event fires on the transition from "duration not yet met" to "duration just met."

```refrain
reward {
  event = dwell(
    condition: above("smr_envelope", "smr_t"),
    duration: 250 ms
  )
}

output {
  audio_chime = reward.event             // chime on rising edge
  game_speed  = reward.event.holds ? 1.0 : 0.5
}
```

`dwell` is the canonical way to express the operant reward pattern. It replaces the older `condition + dwell + continuous` reward shape from earlier strawman drafts.

---

## Inhibit actions

### `mute`, `freeze`, `flag`

```
mute(release: duration)
freeze(release: duration)
flag()
```

Specifies how an inhibit modifies the values delivered to output bindings when active:

- **`mute(release: ...)`** — gate output to zero; hold for `release` after metric clears.
- **`freeze(release: ...)`** — hold output at last value; release after `release`.
- **`flag()`** — emit telemetry only; do not modify output. Used for logging.

Inhibits modify what reaches the patient via output bindings. They do *not* modify `reward.continuous` or `reward.event` directly. Downstream protocol logic that consumes reward sees the unmodified value.

---

## Outputs

Output bindings are declarative named channels. The standard set:

| Channel | Type | Range | Description |
|---|---|---|---|
| `audio_gain` | scalar | [0, 1] | Multiplicative gain on audio source |
| `audio_chime` | event | — | Discrete chime on event |
| `video_clarity` | scalar | [0, 1] | Clarity / unblur of video source |
| `video_brightness` | scalar | [0, 1] | Brightness multiplier |
| `ambient_density` | scalar | [0, 1] | Density of ambient particle effect |
| `score_increment` | event | — | Discrete score increment |

Custom output channels (research labs adding haptic feedback, etc.) are out of scope for v0.0.

---

## Vector reductions *(sketches; v0.0 does not yet specify all of these)*

### `pct_in_range`

```
pct_in_range(stream<vector<N>>, range: (low, high)) -> stream<scalar percent>
```

Counts the fraction of vector components that fall within `[low, high]`, returns as a percentage. Used for live z-score training.

### `weighted_sum`

```
weighted_sum(stream<vector<N>>, weights: array<float, N>) -> stream<scalar>
```

Linear combination of vector components.

> Vector reduction primitives are sketched for completeness. The v0.0 surface syntax for these is incomplete; SPEC §10 lists this as an open question.

---

## External providers

Some primitives consume runtime-supplied external assets, accessed via the `norms.*` and `client.*` namespaces.

### `norms`

```
norms.power_db.lookup(age: int, channel: channel_name, band: band_name)
  -> scalar uV2
```

A normative database lookup. The runtime supplies the actual database; the protocol references it abstractly. Open-source recorder ships an interface but no normative data; clinical runtimes ship their licensed norms.

### `client`

```
client.age          -> int
client.handedness   -> enum
client.session_n    -> int
```

Session-time values supplied by the recorder UI. Treated as constants within a session.

---

## Coverage

The v0.0 standard library is sufficient for these clinical protocol families:

- **SMR / theta-beta** — fully expressible.
- **Othmer ILF** — fully expressible.
- **Alpha-theta (Peniston)** — fully expressible.
- **Asymmetry training** — expressible with `formula` and existing primitives.
- **Live z-score training (LZT)** — sketched but vector-reduction syntax is incomplete.
- **Coherence training** — fully expressible via `coherence(input_a, input_b, band, window)` (Spectral operators section).
- **Source-space NF** — `source_project` is stubbed; needs concrete semantics.
- **Phase-based protocols** — outside scope of v0.0.

The list of additions for v0.1 will be driven by the protocols people actually want to express that v0.0 can't.

---

## Custom primitives — the escape hatch

If your protocol needs a primitive the standard library doesn't have, declare a custom one:

```refrain
custom "plv" {
  module    = "mylab.phase:plv"
  signature = (stream<vector<19> uV>) -> stream<scalar dimensionless>
  budget    = { state_kb: 16, worst_case_us: 80 }
}

derive "phase_lock" {
  formula = plv("raw_19ch")
}
```

The runtime imports the named Python function, validates its signature on first call, accounts the declared budget, and treats it as a first-class primitive thereafter. Custom primitives can be packaged with their protocols for sharing.

This is how the standard library stays small without the language being limiting. See `SPEC.md` §4.11 for the formal definition.
