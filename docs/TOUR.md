# Refrain by Example — A Tour

**Status:** strawman draft (v0.0r1)
**Audience:** clinicians, researchers, and engineers new to Refrain
**Companion docs:** [`SPEC.md`](./SPEC.md), [`PRIMITIVES.md`](./PRIMITIVES.md), [`examples/`](./examples/)

This tour introduces Refrain by example. We start with the smallest possible protocol that does something useful, add features as we go, and finish with a complete Othmer ILF protocol that exercises most of the language.

You don't need to read the language reference first. The reference is precise; this is friendly.

---

## 1. Hello, SMR

The simplest useful Refrain protocol trains a single band. Here it is in full:

```refrain
protocol "hello_smr" {
  meta {
    version     = "0.1.0"
    evidence    = "demo"
    description = "Minimal single-band SMR uptraining demo"
  }

  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }

  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }

  derive "smr_envelope" {
    from = "raw"
    pipeline = [
      bandpass(band: (12 Hz, 15 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 250 ms),
    ]
  }

  threshold "smr_t" {
    signal = "smr_envelope"
    type   = percentile(target_pct: 70, window: 2 min)
  }

  reward {
    continuous = sigmoid("smr_envelope" / "smr_t",
                         midpoint: 1.0,
                         steepness: 3)
  }

  output {
    audio_gain = reward.continuous
  }
}
```

That's a complete protocol. It says:

1. Use any amplifier sampling at 256 Hz or higher with a Cz electrode.
2. Build a single-channel referential signal at Cz.
3. Compute the SMR-band envelope (12–15 Hz, smoothed).
4. Track an adaptive threshold at the 70th percentile over two minutes.
5. The reward (continuous) is a sigmoid of how far the SMR envelope sits above its threshold.
6. Patient hears audio whose gain follows the reward.

The patient: hears louder audio when their SMR is above their personal recent baseline. The clinician: needs to do nothing — the threshold tracks the patient automatically.

This is enough to be a real protocol. Let's add what's missing for clinical use.

---

## 2. Inhibits — keeping artifacts out of the reward

The protocol above happily rewards EMG, eye blinks, and electrode pops if any of them happen to fall in the 12–15 Hz band. We need inhibits.

Add to the protocol:

```refrain
inhibit "emg" {
  metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
  threshold = percentile(target_pct: 95, window: 2 min)
  action    = mute(release: 200 ms)
}

inhibit "high_beta" {
  metric    = bandpower(input: "raw", band: (22 Hz, 30 Hz), window: 250 ms)
  threshold = absolute(8 uV)
  action    = mute(release: 200 ms)
}
```

Now the *output* is muted (zero) whenever EMG exceeds its 95th-percentile baseline, or whenever high-beta exceeds 8 µV absolute. The 200 ms release prevents flicker — once an inhibit fires, output stays muted for at least 200 ms after the metric returns to acceptable.

Inhibits modify what reaches the patient via output bindings. They don't modify `reward.continuous` or `reward.event` directly — meaning if you have downstream logic that consumes reward, it sees the unmodified value. This matters for protocols where reward is itself an input to further computation.

---

## 3. The operant pattern — `dwell` and event rewards

For a fully operant SMR protocol (reward only when SMR is up *and* inhibits are down for a sustained period), use `dwell` as an event-producing expression:

```refrain
threshold "theta_t" {
  signal = "theta_envelope"
  type   = percentile(target_pct: 30, window: 2 min)
}

reward {
  event = dwell(
    condition: all_of([
      above("smr_envelope",   "smr_t"),
      below("theta_envelope", "theta_t"),
    ]),
    duration: 250 ms
  )
  continuous = sigmoid("smr_envelope" / "smr_t",
                       midpoint: 1.0, steepness: 3)
}

output {
  audio_chime = reward.event                                   // discrete chime on dwell-met
  audio_gain  = reward.event.holds ? reward.continuous : 0     // gated graded modulation
}
```

`reward.event` here represents the rising-edge event that fires when the condition has been satisfied for at least 250 ms. `reward.event.holds` is a continuous boolean that's true for as long as the condition currently holds. The output binding chooses how to use them:

- **chime** on the discrete event (the patient hears a brief tone each time they cross into a sustained criterion-met state)
- **gated continuous gain** during the holds-true window (graded reward only when the operant condition is currently being met)

If you wanted ungated continuous reward (graded all the time, regardless of condition), you'd just write `audio_gain = reward.continuous`. The gating choice is per-protocol, made visible in the output block.

You now have a faithful SMR/theta-beta protocol. See [`examples/smr_cz.refrain`](./examples/smr_cz.refrain) for the polished version.

---

## 4. The Othmer ILF protocol, ground-up

ILF is a different shape — slow signal, no operant trial, continuous gentle modulation, single bipolar pair. Let's build it from scratch.

### 4.1 Hardware requirements

ILF requires DC-coupled acquisition. Refrain lets us declare it:

```refrain
requires {
  coupling     = "dc"
  sample_rate  = ">= 256 Hz"
  channels     = ["T3", "T4"]
  impedance    = "preferred"
}
```

If the runtime tries to load this protocol with an AC-coupled amplifier, it refuses with a clear diagnostic — the protocol won't silently run on incompatible hardware.

### 4.2 Bipolar acquisition

Othmer protocols are placement-defining. T3-T4 means one electrode at T3, one at T4, and the trained signal is their *difference*:

```refrain
input "ilf" {
  montage = bipolar(plus: "T3", minus: "T4")
}
```

### 4.3 The bandpass / differentiate / rectify pipeline

This is the heart of ILF. We bandpass narrowly around the ORF (Optimal Reinforcement Frequency, the per-patient knob), differentiate to capture state changes, rectify so any change counts as reward:

```refrain
derive "band" {
  from = "ilf"
  pipeline = [
    bandpass(center: orf, bandwidth: ratio(2.5), order: 4),
    differentiate(),
    rectify(),
    smooth(tau: 1500 ms),
  ]
}
```

Notice `orf` — that's not yet defined. It's a clinician-tunable control (declared below). The bandpass's center frequency is whatever the clinician has dialed in for this session.

### 4.4 Auto-ranging

DC-coupled signals drift. Even with a narrow bandpass, the post-derivative rectified signal will have a slowly changing dynamic range across a 30-minute session. We auto-range:

```refrain
derive "reward_signal" {
  from = "band"
  pipeline = [
    auto_range(window: 5 min, percentile: (5, 95)),
  ]
}
```

`auto_range` emits a value normalized to [0, 1] based on a rolling 5-minute window of the 5th and 95th percentiles. Stable, drift-immune, no hard saturation.

### 4.5 Reward mapping

Continuous, sigmoid, no event:

```refrain
reward {
  continuous = sigmoid("reward_signal", midpoint: 0.5, steepness: 4)
}
```

The reward exposes only `reward.continuous` — there's no operant event for ILF.

### 4.6 Output bindings

Multi-modal, coordinated:

```refrain
output {
  audio_gain      = 0.2 + 0.8 * reward.continuous
  video_clarity   = reward.continuous
  ambient_density = 0.4 + 0.6 * reward.continuous
}
```

The audio has a base level (0.2) so the patient never hears silence; the video and ambient effects modulate over their full range.

### 4.7 Controls — the clinician's knob

ORF is the single most important parameter and the clinician adjusts it live during the session:

```refrain
controls {
  orf = frequency {
    range        = (0.0001 Hz, 0.5 Hz)
    default      = 0.01 Hz
    log          = true
    label        = "Optimal Reinforcement Frequency"
    live_tunable = true
  }
}
```

`live_tunable = true` means the clinician can change ORF mid-session via the runtime's control API. The bandpass coefficients in §4.3 will recompute lazily; state is preserved (warm-restart by default). `log = true` means the GUI should present a logarithmic slider — appropriate when the range spans four decades.

### 4.8 Inhibits — light-touch artifact gating

ILF doesn't need aggressive artifact rejection. A single EMG inhibit suffices:

```refrain
inhibit "emg" {
  metric    = bandpower(input: "ilf", band: (50 Hz, 100 Hz), window: 100 ms)
  threshold = percentile(target_pct: 95, window: 2 min)
  action    = mute(release: 200 ms)
}
```

### 4.9 Putting it all together

The full protocol fits on a page. See [`examples/othmer_ilf_t3t4.refrain`](./examples/othmer_ilf_t3t4.refrain) for the runnable version with `meta` block, session structure, and CRED-nf-aligned metadata.

---

## 5. Composition — extending a base

Suppose you want a Cz-Pz variant of Othmer ILF. You don't rewrite the protocol; you extend it:

```refrain
protocol "my_clinic_othmer_cz_pz" extends "library/othmer/ilf_base@1.2" {
  meta {
    version     = "0.1.0"
    description = "Othmer ILF, Cz-Pz, attention-focus protocol"
  }

  requires {
    channels = ["Cz", "Pz"]
  }

  input "ilf" {
    montage = bipolar(plus: "Cz", minus: "Pz")
  }

  // Everything else inherited from ilf_base@1.2
}
```

The default behavior is "child replaces parent for named blocks." Above, the child's `input "ilf"` block replaces the parent's `input "ilf"` block. Other named blocks (`derive`, `inhibit`, etc.) that the child doesn't mention are inherited unchanged.

### 5.1 Partial override with `amend`

If you want to change just *one field* of a parent block, use `amend`:

```refrain
protocol "stricter_emg_variant" extends "library/othmer/ilf_base@1.2" {
  meta {
    version     = "0.1.0"
    description = "Tighter EMG threshold for noisy environments"
  }

  amend inhibit "emg" {
    threshold = percentile(target_pct: 90, window: 2 min)
    // metric and action inherited from parent
  }
}
```

`amend` is the cleanest way to express "I want this protocol with one parameter different." Most clinical variants are 5-10 line files of this shape.

### 5.2 Removing parent declarations

To remove a parent's block entirely:

```refrain
protocol "no_inhibits_research_variant" extends "library/smr_cz@1.0" {
  meta {
    version     = "0.1.0"
    description = "Research variant without artifact inhibits"
    evidence    = "research"
  }

  remove inhibit "emg"
  remove inhibit "high_beta"
}
```

This is rare in clinical use but useful for research where you want to study what the unmodified signal does.

### 5.3 Safety guards with `final`

Parent protocols can mark declarations as un-overridable using `final = true`. Children cannot amend, remove, or replace `final` declarations. This is useful for clinical safety guards:

```refrain
// In library/clinical_base@1.0
protocol "clinical_base" {
  // ...
  inhibit "safety_emg" {
    metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
    threshold = percentile(target_pct: 99, window: 2 min)
    action    = mute(release: 200 ms)
    final     = true   // mandatory safety inhibit
  }
}
```

Any protocol extending `clinical_base@1.0` will inherit `safety_emg` and cannot override or remove it. Children can still add additional inhibits.

---

## 6. Cross-stream arithmetic with `formula`

The pipeline form (`from + pipeline`) handles the linear case where one input flows through a chain of operations. For cross-stream arithmetic — combining two or more streams — use the `formula` form instead.

### 6.1 Asymmetry training

```refrain
derive "left_alpha" {
  from = "raw_19ch"
  pipeline = [
    select_channel("F3"),
    bandpass(band: (8 Hz, 13 Hz)),
    hilbert(), magnitude(), smooth(tau: 250 ms),
  ]
}

derive "right_alpha" {
  from = "raw_19ch"
  pipeline = [
    select_channel("F4"),
    bandpass(band: (8 Hz, 13 Hz)),
    hilbert(), magnitude(), smooth(tau: 250 ms),
  ]
}

// Formula form: (L - R) / (L + R)
derive "alpha_asymmetry" {
  formula = ("left_alpha" - "right_alpha") / ("left_alpha" + "right_alpha")
}
```

The `formula` field accepts any expression. Cross-stream arithmetic, multi-input primitive calls, references — all compose freely. This is how to express any operation that doesn't fit a linear pipeline.

### 6.2 Crossover detection (alpha-theta)

```refrain
derive "theta_minus_alpha" {
  formula = "theta_envelope" - "alpha_envelope"
}
```

Now `"theta_minus_alpha"` is a stream that's positive when theta dominates, negative when alpha dominates, zero at the crossover.

### 6.3 Rate alignment

When two streams in a formula run at different rates, the compiler errors and asks you to make the alignment explicit:

```refrain
// auto_ranged_signal runs at ~4 Hz; raw_envelope at ~64 Hz
derive "comparison" {
  formula = align_to("raw_envelope", target: "auto_ranged_signal")
            > "auto_ranged_signal"
}
```

Implicit rate-matching would be too easy to misuse silently. Explicit `align_to` makes the choice visible.

### 6.4 The two forms compile to the same IR

`pipeline` is sugar for `formula` with nested calls. These are equivalent:

```refrain
// Pipeline form
derive "smr_envelope" {
  from = "raw"
  pipeline = [
    bandpass(band: (12 Hz, 15 Hz)),
    hilbert(),
    magnitude(),
    smooth(tau: 250 ms),
  ]
}

// Formula form
derive "smr_envelope" {
  formula = smooth(
    magnitude(hilbert(bandpass("raw", band: (12 Hz, 15 Hz)))),
    tau: 250 ms
  )
}
```

The pipeline form reads better for linear chains; the formula form is needed for everything else.

---

## 7. Vector streams — z-score training (sketch)

Live z-score training (LZT, Thatcher) is more demanding because it operates on *vectors* of metrics, not scalars. Refrain handles this with vector stream types:

```refrain
metric "delta_powers" {
  type = bandpower(
    band:     (1 Hz, 4 Hz),
    channels: standard_19,
    window:   2 sec
  )
  // produces stream<vector<19> uV2>
}

zscore "all_deltas" {
  metric = "delta_powers"
  norms  = norms.power_db        // external provider
  age    = client.age            // session-time value
  // produces stream<vector<19> dimensionless>
}

reward {
  continuous = linear(
    pct_in_range("all_deltas", range: (-1, 1)),
    midpoint: 70,
    steepness: 0.05
  )
  // pct_in_range is a vector reduction:
  //    stream<vector<N>> -> stream<scalar percent>
}
```

This sketch reveals two facts about LZT-class protocols:
- The math is *cheap* once you have the norms.
- The norms (`norms.power_db`) are external assets supplied by the runtime, not the protocol — the same protocol runs unchanged whether the runtime ships free open norms, Thatcher LZT norms, or NeuroGuide norms.

The full v0.0 syntax for vector reductions and norms providers is one of the open questions in `SPEC.md` §10.

---

## 8. The escape hatch — custom primitives

Sometimes the standard library doesn't have what you need. Maybe you're researching a phase-locking metric that hasn't been productized yet. You declare a custom primitive:

```refrain
custom "my_phase_lock" {
  module    = "mylab.phase:plv"
  signature = (stream<vector<19> uV>) -> stream<scalar dimensionless>
  budget    = { state_kb: 16, worst_case_us: 80 }
}

derive "plv" {
  formula = my_phase_lock("raw_19ch")
}
```

The runtime imports `mylab.phase.plv` (a Python function), validates that its signature matches on first call, accounts the declared budget against the protocol's resource ceiling, and then it's just another primitive. You can publish your custom primitive as part of a protocol pack so collaborators can use it.

This is what keeps the language honest. The standard library doesn't try to cover everything; the long tail of research math goes through the escape hatch with its budgets and types declared explicitly.

---

## 9. CRED-nf and your protocol

Every protocol you write in Refrain is automatically CRED-nf-aligned. The `meta` block carries the metadata; the `requires`, `input`, `derive`, `threshold`, `inhibit`, `reward`, `output`, and `session` blocks carry the specifics. To produce a paper-supplement table for CRED-nf:

```bash
$ refrain export cred-nf my_protocol.refrain --output supplement.md
```

The output is a markdown table covering every CRED-nf checklist item with the values pulled from your protocol file. Items the protocol doesn't cover show up as `[NOT SPECIFIED]` so reviewers can tell at a glance what's missing.

This is the load-bearing reason to use Refrain. CRED-nf compliance becomes a tooling feature, not a manual obligation.

---

## 10. Where to go next

- **Read a complete worked example.** [`examples/othmer_ilf_t3t4.refrain`](./examples/othmer_ilf_t3t4.refrain) is the polished version of the protocol we built in §4.
- **Browse the standard library.** [`PRIMITIVES.md`](./PRIMITIVES.md) lists every primitive with signatures and examples.
- **Read the formal reference.** [`SPEC.md`](./SPEC.md) defines the language precisely.
- **Read the rationale.** [`CONCEPT.md`](./CONCEPT.md) explains why Refrain exists and what we're hoping it changes about the field.

If you find a feature missing, a syntax that feels wrong, or a clinical pattern that doesn't fit — that's the most valuable feedback at this stage. The strawman exists to be argued with.
