# Neurofeedback Protocol Fuzzer — Design

**Date:** 2026-05-27
**Status:** Design (approved in brainstorming; pending written-spec review)
**Scope of v1:** the `smr_cz` protocol, full logic, via a `refrain fuzz` CLI.

---

## 1. Motivation

Refrain is a brand-new language with a brand-new evaluator. Two distinct
"does it work?" questions need answering, and today neither has a good tool:

1. **Is the engine correct?** Does the evaluator (Python today, Rust core
   tomorrow) faithfully implement what a `.refrain` protocol *means*?
2. **Is my protocol correct — and do I understand the language?** When an
   author writes a new protocol while learning the DSL, does it actually do
   what they intended?

The existing `src/refrain/synthetic.py` generates pink-noise EEG with
**hand-scheduled** `SMRBurst` windows — you specify "13 Hz, 20 µV, 1 s at Cz"
and eyeball whether reward fires. That validates nothing automatically and
knows nothing about the protocol under test.

This tool reads the **protocol's own definition** to *automatically* synthesize
signals AND *predict* what the evaluator should do with them, then checks
reality against the prediction. One machine, answering both questions, plus a
third bonus:

3. **Where is the spec ambiguous?** Building an independent oracle forces the
   language semantics to be pinned down. Where the SPEC is silent, an
   oracle-vs-evaluator disagreement is neither an engine bug nor a protocol bug
   — it's a **spec gap**. For v0.x that may be the highest-value output.

### Diagnosis table

| Disagreement | Diagnosis |
|---|---|
| oracle (semantics) ≠ evaluator | **engine bug** |
| protocol behavior ≠ author's intent | **protocol bug** (mis-written) |
| a logical branch can't be reached / can't be asserted | **protocol smell** |
| oracle ≠ evaluator where SPEC is silent | **spec gap** |

---

## 2. The core idea

The protocol IR doesn't just describe inputs — it describes a *computation*
(montage → band envelopes → thresholds → conditions → dwell → events). A naive
oracle would reimplement all that DSP, which only tests one implementation
against another.

The trick that makes the oracle independent and tractable:

> **Control the spectral content of the synthetic signal.** If a scenario says
> "from t=2 s to t=3 s, put a 13.5 Hz tone of amplitude A in the signal and keep
> every other band quiet," then the expected band envelopes are known *by
> construction*. The only thing left to predict is the protocol's **discrete
> logic** (threshold crossings → `all_of` → 250 ms dwell → event) — which is
> simple, and predictable without redoing the DSP.

So a **scenario** is a piecewise *band-content-over-time* spec. The oracle
reasons at the band level; the renderer turns the same spec into samples. Both
read the scenario; neither reads the other.

### Independence, made precise

The oracle computes the bandpass steady-state gain and group delay **itself**,
from the filter coefficients **baked into the IR-JSON** — using
`scipy.signal.sosfreqz` / `group_delay` on the same numbers the evaluator runs.
This is Python *evaluating the transfer function*, not *running the cascade*, so
it is independent of the evaluator's streaming implementation.

The confirmed v1 DSP the oracle must model (from `bench/baselines/_dsp.py`,
which mirrors `src/refrain/primitive_impls.py`):

- **Bandpass:** Butterworth SOS, order 4. Steady-state gain at frequency *f* =
  `|H(e^{j2πf/fs})|` from the baked SOS.
- **Hilbert:** 65-tap FIR Hilbert transformer (Hamming), constant 32-sample
  group delay. Analytic magnitude of a steady in-band tone ≈ the tone's
  (bandpass-gained) amplitude.
- **Magnitude:** `abs`.
- **Smooth:** one-pole IIR, `α = 1 − exp(−1/(τ·fs))`. Step response reaches the
  new level with time constant τ (~3τ to settle within 5 %).
- **Percentile threshold:** `np.percentile` (linear interpolation) over a rolling
  deque of the last `window` samples; computed over the *partial* buffer before
  the window fills.
- **Dwell:** exact integer streak — `round(duration_s·fs)` samples (250 ms @
  256 Hz = 64). Fires on the rising edge of `streak ≥ dwell_samples`. The
  counting is exact; all timing uncertainty lives in *when the condition flips*.

---

## 3. Architecture (the pipeline)

```
.refrain ──parse/resolve──▶ IR (+ baked filter coeffs in IR-JSON)
                              │
                    [1] LogicalSurface         ◀── the shared "knowledge of the protocol"
                              │
                    [2] ScenarioGenerator (directed + probe)
                              │
                         [Scenario]*            ◀── shared contract
                         ╱         ╲
              [3] Oracle           [4] Renderer
        (expected 3-valued      (band-content → EEG;
         event timeline)         extends SignalGenerator)
                 │                     │
                 │              SyntheticSource → [5] bench ChunkedRunner
                 │                     │
                 │              actual event stream
                 ╲                    ╱
                  [6] Checker  (align within derived timing collar)
                          │
                  [7] Report (balanced) ──▶ `refrain fuzz` CLI
```

Each unit has one job, a defined interface, and named dependencies.

### [1] `LogicalSurface` — `fuzz/surface.py`

Extracts the protocol's testable structure from the resolved IR into a plain
data model: relevant bands (edges, baked coefficients, smoothing τ, computed
group delay), threshold specs (kind = absolute | percentile, target, window,
which signal), the condition tree (`all_of`/`any_of` + `above`/`below` leaves),
dwell duration, controls + ranges, output bindings + their gating, session
phases (and which mute output), and the sample rate. The single source of
"knowledge of the protocol" both the generator and the oracle read.

*Depends on:* resolver output + IR-JSON. No DSP.

### [2] `ScenarioGenerator` — `fuzz/generate.py`

Walks the surface and emits a covering set of scenarios, each tagged with the
coverage targets it intends to hit:

- **Per-leaf pivotal scenarios:** for each condition leaf, drive it the
  interesting way (true / false, with rank/amplitude margin) while holding the
  others favorable, so the leaf is pivotal.
- **Dwell scenarios:** dwell clearly met; dwell clearly missed (hold for
  `dwell − collar − safety`, *not* one sample short).
- **Percentile warm-up scenario:** fill the 2-min window quiet, then a
  high-rank spike.
- **Negative control:** all-quiet (nothing should ever fire).
- **Band-response characterization probe** (see §6a): a tone sweep across the
  spectrum.
- **Metamorphic sweeps** (see §6b/§6c): a rank sweep (below→above the percentile
  boundary) and a hold-duration sweep (shorter→longer than dwell).

*Depends on:* surface only.

### [3] `Scenario` — `fuzz/scenario.py`

The contract. Fields:

- `duration_s`, `sample_rate_hz`
- `segments: list[BandSegment]`, where
  `BandSegment(band, channel, start_s, end_s, content)` and `content` is either
  `Tone(amplitude_uv)` (sharp, for crisp absolute-threshold and characterization
  predictions) or `BandNoise(rms_uv)` (realistic, for shaping percentile
  distributions). Bands not covered by a segment stay at the pink-noise floor.
- `controls: dict[str, float]` — control overrides for this run.
- `phase_override: PhaseSpec | None` — optional shortened session for tractable
  run-times (see §7 open question).
- `label: str`, `coverage_tags: set[str]`.

Oracle and renderer each consume a `Scenario`; **neither consumes the other**.

### [4] `Renderer` — extends `src/refrain/synthetic.py`

Keeps the pink-noise floor; generalizes `SMRBurst` → a `BandSegment` injector
that adds either a tone or band-limited noise at the requested power, in the
requested band, on the requested channel, deterministically by seed. The
existing burst mechanism is ~90 % of this. Output wrapped by the existing
`SyntheticSource`.

*Depends on:* scenario. Independent of the oracle.

### [5] Runner — reuse `bench` `ChunkedRunner` / `Evaluator.live`

Feed the `SyntheticSource` through the evaluator and collect the actual event
stream (and, optionally, recorded streams for the report's behavioral summary).
Reused wholesale per the project's reuse-over-reinvent rule.

### [6] `Checker` — `fuzz/check.py`

Aligns actual events to the oracle's 3-valued timeline within the derived
timing collar (§6c):

- event inside a SHOULD-FIRE window → **PASS**
- SHOULD-FIRE window with no event → **VIOLATION (missed)**
- event inside a SHOULD-NOT-FIRE window → **VIOLATION (spurious)**
- DON'T-CARE → **ignored** (counted, with reason)

Also evaluates the metamorphic relations (monotonic non-decreasing firing rate
across rank / hold-duration sweeps) and the characterization assertion.
Aggregates branch coverage **and** assertion coverage (§6d).

### [7] Report + CLI — `fuzz/report.py`, `refrain fuzz` in `cli.py`

`refrain fuzz protocol.refrain` runs the scenarios and prints a **balanced**
report with two co-equal top sections:

- **"What your protocol does"** — plain-language behavior synthesized from the
  oracle across scenarios ("reward fires when SMR↑, theta↓, high-beta quiet; it
  stops the instant theta crosses its threshold"), plus structural smells
  (unreachable / unassertable branches).
- **"Engine check"** — oracle-vs-evaluator verdict (PASS / violations), the
  coverage matrix, and the assertion-coverage / don't-care breakdown.

Exits nonzero on engine violations, so it can become a CI gate later.

---

## 4. v1 scope: the `smr_cz` protocol, exactly

From `bench/protocols/realistic_smr.refrain` (= `examples/smr_cz.refrain`):

- montage: `referential(active: "Cz", reference: "linked_ears")`
- three envelopes: SMR (12–15 Hz), theta (4–8 Hz), high-beta (22–30 Hz), each
  `bandpass(order 4) → hilbert → magnitude → smooth(τ 250 ms)`
- thresholds: `smr_t` = percentile(70, 2 min), `theta_t` = percentile(30, 2 min),
  `hbeta_t` = **absolute(8 µV)**
- reward: `dwell(all_of([above(smr, smr_t), below(theta, theta_t),
  below(hbeta, hbeta_t)]), 250 ms)`; continuous = `sigmoid(smr/smr_t, …)`
- outputs: `audio_chime = reward.event`; `audio_gain` / `game_speed` gated by
  `reward.event.holds`
- controls: `smr_target_pct` (default 70), `theta_target_pct` (default 30)
- session: warmup 90 s (output muted) → training 30 min → cooldown 30 s (muted)

**v1 asserts crisply on:** `audio_chime` (the dwell rising edge) and the
`reward.event.holds` intervals.

**v1 treats loosely (metamorphic / don't-care):** the continuous `sigmoid`
magnitude on `audio_gain`/`game_speed` — asserted only as monotonic in the
SMR/threshold ratio, not as an absolute value.

**Note — no `inhibit` primitive here:** artifact rejection is the
`below("high_beta_envelope", "hbeta_t")` leaf with an absolute threshold, not a
dedicated `inhibit` block. The `inhibit`-masking path (mute/freeze/flag) is
deferred with the broader IR generalization.

---

## 5. The oracle, concretely

For a scenario, per band segment:

1. **Predicted envelope.** For a `Tone(A)` at frequency *f* in band *b*:
   steady-state smoothed envelope ≈ `A · |H_b(f)|`, where `|H_b|` is from the
   baked SOS — trusted only after the settle collar (§6c). For `BandNoise`, the
   envelope is a fluctuating quantity; absolute value is **not** predicted —
   only its rank within a window is used (§6b).

2. **Per-leaf 3-valued truth over time.**
   - **Absolute** (`hbeta_t = 8 µV`): tone envelope clearly > 8 + margin →
     TRUE; clearly < 8 − margin → FALSE; within ±margin → DON'T-CARE.
   - **Percentile** (`smr_t`, `theta_t`): **ordinal**. The threshold is
     scale-relative, so the oracle reasons by *rank*: a sample at the 95th
     percentile of its window is robustly above a 70th-pct threshold regardless
     of absolute gain; the ≈60–80th band is DON'T-CARE. Pre-window-fill region
     is DON'T-CARE.

3. **Combine** via the condition tree in 3-valued logic (`all_of` is TRUE iff
   all TRUE, FALSE iff any FALSE, else DON'T-CARE).

4. **Dwell.** A SHOULD-FIRE window opens only where the combined condition is
   robustly TRUE for ≥ `dwell_samples` after the collar; a clearly-short hold is
   SHOULD-NOT-FIRE. The dwell count is exact; the collar absorbs condition-flip
   timing.

5. **Output gating.** SHOULD-FIRE for `audio_chime` only during unmuted phases
   (training); muted phases (warmup/cooldown) suppress output.

---

## 6. The four soundness treatments (all adopted for v1)

### (a) Coefficient/design blind spot → band-response characterization probe

If the *coefficients themselves* are wrong (the filter-*design* step:
band edges → coefficients), the oracle's `|H|` and the evaluator's cascade share
the error and agree. The bench gate catches this for its corpus (hand-written
baselines design their own filters — confirmed at `_dsp.py:97`), but not for
arbitrary author-written protocols.

**Treatment:** a characterization scenario renders a tone sweep and asserts the
evaluator's measured per-band envelope **peaks at the declared band edges**
(±tolerance) with adequate stopband rejection. The expectation derives from the
band *declaration* (`12 Hz, 15 Hz` as written), not the coefficients — so it
catches a misplaced passband independent of the coefficients. Irreducible
residual: the SPEC's definition of "band edge" (−3 dB? endpoints?) → surfaced as
a spec question, not a silent miss.

### (b) Percentile boundaries → ordinal reasoning + metamorphic sliver

Reason by **rank**, not absolute µV (see §5.2). Most of the range is crisply
assertable; only the genuinely-ambiguous middle is DON'T-CARE — and that is
exactly where the evaluator's own output is stochastic, so crisp assertion there
would be flaky. **Calibration is rejected** for v1: it sharpens the absolute
threshold value but buys nothing where the problem is noise, and it sacrifices
independence.

**Boundary sensitivity** instead comes from a **metamorphic sliver:** sweep the
test sample's intended rank from clearly-below to clearly-above and assert the
reward firing-rate is **monotonically non-decreasing**.

### (c) Timing → derived asymmetric collar + robust-margin dwell-miss

"group delay + τ" understates the lag (IIR settling > group delay; one-pole
reaches level at ~3τ). The dwell *count* is exact; uncertainty is purely in
condition-flip timing. **Treatment:** a DON'T-CARE collar around every condition
transition = worst-case (filter settle + ~3τ + one chunk), computed from the
surface, applied asymmetrically (wider is safe). Dwell-miss scenarios miss by a
robust margin, not one sample. A second metamorphic sliver: **longer hold ⇒ at
least as likely to fire**.

### (d) DON'T-CARE → principled, explained, measured, fail-loud-on-vacuity

The danger of a 3-valued oracle is the **vacuous pass**. Three guards:

1. Every DON'T-CARE interval carries a **reason code** (near-boundary /
   settle-collar / pre-fill / phase-muted), shown in the report.
2. The coverage matrix distinguishes "branch driven true/false **and crisply
   asserted**" from "driven but only in don't-care." A branch only ever seen in
   don't-care is **"reachable but unassertable"** — flagged as seriously as
   unreachable.
3. **Fail loud on vacuity:** a scenario that made *zero* crisp assertions is a
   generator bug, not a pass.

---

## 7. Reuse vs. new

**Reuse:** `SignalGenerator` (extend), `SyntheticSource`, `Source`, the bench
`ChunkedRunner`, the resolver/IR, the IR-JSON baked coefficients, and the
`cli.py` subcommand pattern.

**New:** a `src/refrain/fuzz/` package — `surface.py`, `scenario.py`,
`generate.py`, `oracle.py`, `check.py`, `report.py` — plus extensions to
`synthetic.py` and a `refrain fuzz` command.

---

## 8. Testing strategy (TDD)

- **`surface`:** assert extraction from the `smr_cz` IR matches the known
  structure (bands, threshold kinds, condition tree, dwell, phases).
- **`oracle`:** feed hand-built `Scenario`s, assert the predicted 3-valued
  timeline — fully independent of the evaluator.
- **`renderer`:** assert injected band content appears at the target band (FFT
  check) and other bands stay at the noise floor.
- **`checker`:** synthetic event streams vs synthetic oracle timelines, covering
  PASS / missed / spurious / don't-care / vacuity-fail.
- **End-to-end:** the `smr_cz` directed set passes the engine check; then a
  **mutation test** — flip `above`→`below` on the SMR leaf and assert the report
  flags the behavior change (the negative-control / pivotal scenarios flip).

---

## 9. Deferred (YAGNI for v1)

- Randomized + shrinking generation (layer 2).
- Broader metamorphic relations beyond the two v1 slivers (layer 2).
- Calibrated oracle (maybe never).
- Coherence, weighted-composite reward, multi-site placement, and the
  `inhibit`-primitive masking path — i.e., the rest of the IR surface.
- Recording-splice augmentation (real recordings + spliced bursts).
- A pytest-gate frontend (CLI first; gate wiring later — the nonzero exit code
  is already in place for it).

---

## 10. Open spec questions this work surfaces

These are expected outputs, not blockers — the tool exists partly to find them:

1. **Percentile warm-up policy.** v1 treats the pre-window-fill region as
   DON'T-CARE. What does the language *intend* before the window fills
   (percentile over partial buffer, as the impl does today, vs. "invalid until
   full")? Pin down in SPEC.
2. **Band-edge definition.** Does `band: (12 Hz, 15 Hz)` mean −3 dB points,
   passband endpoints, or the design argument to `butter`? The characterization
   probe's tolerance depends on the answer.
3. **Session-phase override for testing.** The 2-min percentile window + 31-min
   session makes full runs slow. Can the fuzzer legitimately shorten/scale
   session phases (a `phase_override`), or must engine-checks bypass session
   muting and test the reward pipeline directly, with phase-muting verified
   separately? Decide before implementation.
```