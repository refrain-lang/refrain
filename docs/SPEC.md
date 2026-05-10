# Refrain Language Reference

**Version:** v0.0 (strawman, revision 1)
**Status:** working draft, expected to change
**Companion docs:** [`CONCEPT.md`](./CONCEPT.md), [`TOUR.md`](./TOUR.md), [`PRIMITIVES.md`](./PRIMITIVES.md)

This document defines the Refrain language at a level intended to be precise enough for an implementer and complete enough for a peer reviewer. It is paired with the standard library reference (`PRIMITIVES.md`) and a tutorial (`TOUR.md`). Neither this document nor the language is final; both are first drafts.

**Revision history.** v0.0r1 reflects three design decisions resolved after the initial strawman: (1) every `derive` is fundamentally an expression, with `from + pipeline` as sugar for the linear case; (2) the `reward` block has a single shape with two optional expression-valued fields, `continuous` and `event`; (3) protocol composition has default semantics (child replaces parent for named blocks) plus explicit `amend`, `remove`, and `final` modifiers.

---

## 1. Introduction

Refrain is a declarative description language for clinical neurofeedback protocols. A Refrain file describes, in full, what a NF protocol does: required hardware, channel montage, signal-processing pipeline, threshold logic, inhibit gates, reward expression, output bindings, clinician-tunable controls, and session structure.

A Refrain runtime parses a protocol file, type-checks it, validates the connected amplifier against its requirements, computes worst-case latency and resource budgets statically, and executes the protocol in real time. The language is hardware-agnostic by design; the runtime adapts.

### 1.1 Design principles

1. **The artifact is text.** Diffable, reviewable, citable, mergeable.
2. **Declarative pipeline + escape hatch.** Protocols compose curated primitives; novel research math is supported via typed custom primitives.
3. **Hardware-agnostic protocol, hardware-specific runtime.** Same file, different amps.
4. **Static guarantees.** Type-checking, resource budgets, hardware-capability validation happen at protocol load, not at runtime.
5. **CRED-nf alignment by construction.** Every CRED-nf item maps to a structural field.
6. **Composition by libraries, not language extension.** Protocol packs are versioned, importable, extensible.
7. **Expressions are universal.** Derives, reward fields, and output bindings are all expressions of the same kind, with stream arithmetic, primitive calls, and references composing freely.

---

## 2. Lexical syntax

### 2.1 Encoding

UTF-8.

### 2.2 Comments

```refrain
// line comment to end of line
/* block comment, may nest */
```

### 2.3 Identifiers

`[A-Za-z_][A-Za-z0-9_-]*`

Hyphens are permitted to support electrode-position tokens like `T3-T4` as identifiers. The grammar disambiguates from binary subtraction by context (binary `-` requires whitespace; identifier hyphens do not).

### 2.4 Strings

Double-quoted UTF-8 strings. Escape sequences: `\n`, `\t`, `\\`, `\"`, `\uXXXX`.

### 2.5 Numeric literals with units

```
0.01 Hz       250 ms       8 uV       2 min       95 %
```

The unit is part of the value's type. Operations between incompatible units are type errors. Recognized units in v0.0:

| Unit | Meaning | Notes |
|---|---|---|
| `Hz` | frequency | |
| `ms`, `s`, `min` | time | freely interconvertible |
| `uV` | microvolts | |
| `uV2` | microvolts squared | for power |
| `%` | dimensionless ratio (0–100) | |

Unit-less numbers are dimensionless scalars.

### 2.6 Boolean literals

`true`, `false`.

### 2.7 Whitespace

Spaces, tabs, and newlines are insignificant outside literals. Indentation has no semantic meaning.

---

## 3. Grammar (EBNF, draft)

```ebnf
file              = { import_decl } , protocol_decl ;

import_decl       = "import" , string_lit , [ "as" , identifier ] , ";" ;

protocol_decl     = "protocol" , string_lit ,
                    [ "extends" , protocol_ref ] ,
                    block ;

protocol_ref      = string_lit ;       (* "library/othmer/ilf_base@1.2" *)

block             = "{" , { statement } , "}" ;

statement         = section_block
                  | named_decl
                  | amend_decl
                  | remove_decl
                  | assignment ;

section_block     = section_keyword , block ;
section_keyword   = "meta" | "requires" | "reward"
                  | "output" | "controls" | "session" ;

named_decl        = decl_keyword , string_lit , block ;
decl_keyword      = "input" | "derive" | "threshold"
                  | "inhibit" | "custom" ;

amend_decl        = "amend" , ( section_keyword , block
                              | decl_keyword , string_lit , block ) ;

remove_decl       = "remove" , decl_keyword , string_lit , [ ";" ] ;

assignment        = identifier , "=" , expression , [ ";" ] ;

expression        = literal
                  | identifier
                  | reference
                  | call
                  | array
                  | block_expr
                  | binary_op
                  | conditional
                  | member_access ;

reference         = string_lit ;       (* names a sibling block *)

literal           = number , [ unit ]
                  | string_lit
                  | "true" | "false" ;

call              = identifier , "(" , [ arg_list ] , ")" ;
arg_list          = arg , { "," , arg } ;
arg               = expression | named_arg ;
named_arg         = identifier , ":" , expression ;

array             = "[" , [ expression , { "," , expression } ] , "]" ;

block_expr        = block ;            (* anonymous record *)

binary_op         = expression , op , expression ;
op                = "+" | "-" | "*" | "/"
                  | "==" | "!=" | "<" | ">" | "<=" | ">=" ;

conditional       = expression , "?" , expression , ":" , expression ;

member_access     = expression , "." , identifier ;
```

---

## 4. Protocol structure

A protocol is a top-level `protocol "name" { ... }` block, optionally with an `extends` clause. Inside it, sections appear in any order:

```refrain
protocol "othmer_ilf_t3t4_v1" {
  meta { ... }
  requires { ... }
  input "ilf" { ... }
  derive "band" { ... }
  derive "reward_signal" { ... }
  inhibit "emg" { ... }
  reward { ... }
  output { ... }
  controls { ... }
  session { ... }
}
```

### 4.1 `meta`

Metadata. Required fields: `version`, `evidence`, `description`. Optional fields cover author, citation, lineage, control protocol reference, indication, target population, safety monitoring, outcome measures.

```refrain
meta {
  version           = "1.0.0"
  evidence          = "clinical"   // "clinical" | "research" | "experimental"
  description       = "Othmer ILF training, T3-T4 bipolar"
  author            = "Peak Mind"
  citation          = "Othmer & Othmer 2009"
  population        = "adults_18_plus"
  indication        = "regulatory_dysfunction"
  control_ref       = "library/othmer/ilf_sham@1.0"
  outcome_measures  = ["BAI", "BDI-II", "subjective_arousal"]
}
```

The `meta` block carries all CRED-nf-aligned metadata. See §8.

### 4.2 `requires`

Hardware requirements. The runtime validates these against the connected amplifier; failure is fail-fast at protocol load.

```refrain
requires {
  coupling     = "dc"           // "dc" | "ac"
  sample_rate  = ">= 256 Hz"    // comparison expression
  channels     = ["T3", "T4"]   // electrode positions required
  impedance    = "required"     // "required" | "preferred" | "not_required"
  markers      = "not_required"
}
```

### 4.3 `input`

Named input streams derived from raw amplifier channels.

```refrain
input "ilf" {
  montage = bipolar(plus: "T3", minus: "T4")
}

input "raw" {
  montage = referential(active: "Cz", reference: "linked_ears")
}
```

An input has a stream type derived from its montage:
- `bipolar(...)` → `stream<scalar uV>`
- `referential(active: "X", ...)` → `stream<scalar uV>`
- `referential(channels: [...], ...)` → `stream<vector uV>`
- `source_project(...)` → `stream<vector uV>`

### 4.4 `derive` — pipeline stages

A `derive` block declares a named pipeline stage. Two equivalent forms:

**Form 1: pipeline (sugar for the common case)**

```refrain
derive "smr_envelope" {
  from = "raw"
  pipeline = [
    bandpass(band: (12 Hz, 15 Hz), order: 4),
    hilbert(),
    magnitude(),
    smooth(tau: 250 ms),
  ]
}
```

The `from` field names the input stream. The `pipeline` field is a list of primitive calls applied left-to-right. Each primitive's first input is the previous stage's output (implicit threading).

**Form 2: formula (general case)**

```refrain
derive "asymmetry" {
  formula = ("left_alpha" - "right_alpha") / ("left_alpha" + "right_alpha")
}

derive "theta_minus_alpha" {
  formula = "theta_envelope" - "alpha_envelope"
}

derive "smr_envelope" {
  formula = smooth(
    magnitude(hilbert(bandpass("raw", band: (12 Hz, 15 Hz)))),
    tau: 250 ms
  )
}
```

The `formula` field is any expression of the appropriate type. Cross-stream arithmetic, multi-input primitive calls, and arbitrary nesting are all handled here.

**Both forms compile to the same IR** — an expression tree of typed primitive calls. The pipeline form desugars to nested calls. The two forms are mutually exclusive within a single `derive` (use one or the other).

#### Stream arithmetic

When stream operands appear in arithmetic or comparison expressions, the operation is **element-wise per sample**:

- `stream<T>` op `stream<T>` → `stream<T>` (units propagate)
- `stream<T>` op `T_literal` → `stream<T>` (broadcast)
- `stream<T1>` op `stream<T2>` → error if T1, T2 are unit-incompatible

Division of compatible-unit streams produces a dimensionless stream:

```refrain
"smr_envelope" / "smr_t"
// stream<scalar uV> / stream<scalar uV> -> stream<scalar dimensionless>
```

See PRIMITIVES.md "Stream arithmetic" for the full operator table.

#### Rate alignment

When two streams in a formula run at different rates, the compiler emits an error by default. To explicitly align rates, use `align_to`:

```refrain
derive "comparison" {
  formula = align_to("raw_envelope", target: "auto_ranged_signal")
            > "auto_ranged_signal"
}
```

This forces the protocol author to make rate decisions visible. Implicit interpolation is too easy to misuse silently. See PRIMITIVES.md "Rate alignment" for `align_to` semantics.

### 4.5 `threshold`

A dynamic or static threshold tracked over a stream. Threshold values become first-class references usable in conditions and reward expressions.

```refrain
threshold "smr_t" {
  signal       = "smr_envelope"
  type         = percentile(target_pct: 70, window: 2 min)
  live_tunable = true
}

threshold "high_beta_max" {
  signal = "high_beta_envelope"
  type   = absolute(8 uV)
}
```

Threshold types:
- `absolute(value)` — fixed
- `percentile(target_pct, window)` — adaptive percentile
- `dynamic(...)` — extensible point for future strategies

### 4.6 `inhibit`

Artifact and EMG gates. An inhibit produces a boolean stream; when true, the inhibit's `action` modifies reward delivery.

```refrain
inhibit "emg" {
  metric    = bandpower(input: "ilf", band: (50 Hz, 100 Hz), window: 100 ms)
  threshold = percentile(target_pct: 95, window: 2 min)
  action    = mute(release: 200 ms)
}
```

Actions:
- `mute(release: <duration>)` — gate reward to zero, with hangover
- `freeze(release: <duration>)` — hold reward at last value
- `flag()` — emit only; do not modify reward (for logging-only inhibits)

### 4.7 `reward`

The reward block has a single shape with two optional expression-valued fields. At least one must be declared.

```refrain
reward {
  continuous = <expression producing stream<scalar>>      // optional
  event      = <expression producing event_stream>         // optional
}
```

The reward block exposes:

- **`reward.continuous`** — `stream<scalar>`, the graded value. Output bindings clamp to [0, 1] for analog channels.
- **`reward.event`** — has two consumption modes:
  - direct binding to event-channel outputs (`audio_chime = reward.event`) — emits on rising edges of the underlying condition
  - `.holds` member access (`reward.event.holds`) — `stream<boolean>` indicating whether the dwell condition is currently satisfied

#### Continuous-only (e.g., Othmer ILF)

```refrain
reward {
  continuous = sigmoid("reward_signal", midpoint: 0.5, steepness: 4)
}
```

#### Operant pattern (e.g., SMR/theta-beta)

The operant pattern is expressed via `dwell` as the event-producing expression:

```refrain
reward {
  event = dwell(
    condition: all_of([
      above("smr_envelope",       "smr_t"),
      below("theta_envelope",     "theta_t"),
      below("high_beta_envelope", "hbeta_t"),
    ]),
    duration: 250 ms
  )
  continuous = sigmoid("smr_envelope" / "smr_t",
                       midpoint: 1.0, steepness: 3)
}
```

Output-side gating is explicit and varies per protocol intent:

```refrain
output {
  audio_chime = reward.event                                   // chime on rising edge
  audio_gain  = reward.event.holds ? reward.continuous : 0     // gated continuous
}
```

#### Both (e.g., alpha-theta)

```refrain
reward {
  continuous = sigmoid("theta_envelope", midpoint: 8 uV, steepness: 0.5)
  event = dwell(
    condition: above("theta_envelope", "alpha_envelope"),
    duration: 1000 ms
  )
}
```

### 4.8 `output`

Bindings to patient-facing modulation channels. Each binding is an expression that evaluates per step at the binding's declared rate.

```refrain
output {
  audio_gain      = 0.2 + 0.8 * reward.continuous
  video_clarity   = reward.continuous
  ambient_density = 0.4 + 0.6 * reward.continuous
  audio_chime     = reward.event   // discrete event channel
}
```

Standard output channels: `audio_gain`, `audio_chime`, `video_clarity`, `video_brightness`, `ambient_density`, `score_increment`. Custom channels can be declared in extensions (out of scope for v0.0).

### 4.9 `controls`

Clinician-tunable parameters. Each control declares its type, range, default, and whether it can be mutated mid-session.

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

Control types: `frequency`, `duration`, `voltage`, `percent`, `enum`, `boolean`. Each value declared in `controls` is referenceable by name (e.g., `orf` in §4.4).

### 4.10 `session`

Session structure. Phases, durations, breaks, schedule.

```refrain
session {
  phases = [
    phase { name = "warmup";   duration = 60 s; output_muted = true },
    phase { name = "training"; duration = 25 min },
    phase { name = "cooldown"; duration = 60 s; output_muted = true },
  ]
}
```

### 4.11 `custom`

Escape-hatch declaration of an external Python primitive.

```refrain
custom "my_phase_metric" {
  module    = "myplugin.phase:compute"
  signature = (stream<vector<19> uV>) -> stream<scalar dimensionless>
  budget    = { state_kb: 4, worst_case_us: 50 }
}
```

The runtime imports the named module's function, validates that its declared signature is honored on first call, and accounts the declared budget against the protocol's resource ceiling.

---

## 5. Type system

### 5.1 Primitive value types

- `scalar T` — single value with unit `T`
- `vector<N> T` — fixed-size vector of values with unit `T`
- `boolean` — true/false
- `string`

### 5.2 Stream types

`stream<S>` — a time-series of values of type `S`. All `derive`, `input`, and `threshold` outputs are streams.

Operations:
- `stream<scalar>` + `scalar` → `stream<scalar>` (broadcast)
- `stream<vector<N>>` + `stream<vector<N>>` → `stream<vector<N>>`
- `stream<vector<N>> -> stream<scalar>` via reduction primitives (e.g., `pct_in_range`)

### 5.3 Units

Units are first-class in the type system. The compiler enforces:
- `Hz + ms` is an error.
- `differentiate(uV)` produces `uV/s` (first derivative units).
- `square(uV)` produces `uV²`.
- Comparisons require unit-compatible operands.

### 5.4 References

A string literal in expression context that names a previously declared block (a `derive`, `input`, `threshold`, or `controls` entry) is a reference. Forward references are an error in v0.0; future versions may relax.

### 5.5 Type inference

Most types are inferred from primitive signatures. Explicit annotation is required only for `custom` primitives.

### 5.6 Event streams

`event_stream` is a structured stream type produced by `dwell(...)` and similar event-emitting primitives. It supports two consumption modes:

- direct binding to event-channel outputs (`audio_chime = reward.event`) — emits on rising edges of the underlying condition
- `.holds` member access — yields `stream<boolean>` indicating whether the dwell condition currently holds

A single `event_stream` value can drive both consumption modes within the same protocol. The runtime maintains the underlying state once and exposes both views.

---

## 6. Static validation

The compiler performs the following checks at protocol load. All produce diagnostics with file/line/span references.

### 6.1 Acyclicity

The dataflow graph (inputs, derives, thresholds, inhibits, reward, outputs) must be a DAG. Cycles are a hard error.

### 6.2 Type compatibility

Pipeline stages must compose: each primitive's output type must match the next primitive's input type. Expressions must type-check, including unit consistency.

### 6.3 Hardware capability

The connected runtime exposes a `capabilities()` report describing the amplifier (coupling, sample rate, channels, impedance support, etc.). The protocol's `requires` block must be satisfiable. Mismatch produces a precise diagnostic.

### 6.4 Resource budget

The compiler computes:
- **Worst-case latency** along the longest path in the DAG, summing primitive `worst_case_us` budgets.
- **Total state RAM** across all primitive instances.

If either exceeds the runtime's declared budget, the protocol is refused.

### 6.5 CRED-nf completeness (warning)

The compiler can optionally check that every CRED-nf-required field has been populated in `meta`. By default, missing fields produce warnings; strict mode promotes them to errors. See §8.

### 6.6 Rate alignment

Cross-stream operations in `formula` derives, reward expressions, and output bindings must have matching stream rates. Mismatch produces an error with a suggested `align_to(...)` call. The compiler does not insert implicit alignment.

---

## 7. Runtime semantics

### 7.1 Session lifecycle

```
load → validate → instantiate → calibrate → warmup → run → stop
```

- **load** — file is parsed.
- **validate** — §6 checks run.
- **instantiate** — primitive instances are created with initial state.
- **calibrate** — optional one-time impedance check, baseline measurement.
- **warmup** — protocol runs internally; output is muted; filter state fills, percentile windows populate. Duration declared in `session.phases[0]` if `output_muted = true`.
- **run** — output is unmuted; reward and inhibits are live.
- **stop** — session ends; controls are detached; state may be persisted for later resume.

### 7.2 Per-step execution

Per recorder buffer chunk (typical: 64 samples at 256 Hz = 250 ms):

1. Walk IR in topological order.
2. For each primitive, call `step(input_chunk, state) → (output_chunk, new_state)`.
3. Propagate outputs to dependents.
4. Evaluate reward expression(s).
5. Evaluate output bindings; emit to recorder bus.

### 7.3 Live control mutations

Controls declared `live_tunable = true` accept runtime mutations via `runtime.set_control(name, value)`. Filter coefficients recompute lazily on next step. State is preserved across coefficient changes (warm-restart, see §7.7).

### 7.4 Inhibit semantics

Each inhibit produces a boolean stream. Inhibits affect the values that reach the patient via output bindings:

```
effective_output = output_expression * AND(NOT inhibit_i for each muting inhibit i)
```

Inhibits with `action = freeze` hold the previous output value; with `action = flag` they only emit telemetry.

Inhibits do not modify `reward.continuous` or `reward.event` directly — they modify what the output bindings deliver. This means downstream protocol logic (e.g., a derive that consumes `reward.continuous`) sees the unmodified value, which matters for protocols that compute derived feedback signals from reward.

### 7.5 Reward semantics

The reward block evaluates per step:

1. If `continuous` is declared, evaluate the expression to a scalar. This becomes `reward.continuous`.
2. If `event` is declared, evaluate the expression to an event_stream. This becomes `reward.event`. The runtime maintains the underlying dwell state once; both rising-edge consumption (`reward.event` directly) and `.holds` consumption are derived from it.
3. Output bindings consume `reward.continuous`, `reward.event`, and/or `reward.event.holds` as their expressions specify.
4. Inhibit gating applies at the output stage (§7.4), not to reward values.

### 7.6 Output binding evaluation

Each output binding is an expression evaluated per step at the binding's declared rate. Output values for analog channels (`audio_gain`, `video_clarity`, etc.) are clamped to [0, 1]. Event channels (`audio_chime`, `score_increment`) emit on rising edges of boolean / event-stream values.

### 7.7 ORF (and other live-tunable filter parameter) changes

Three options for handling live filter-coefficient changes:
1. **Hard reset** — reinitialize state. Causes multi-second transient. Not recommended.
2. **Warm-restart** (default) — reuse previous state with new coefficients. Numerically fine; brief drift possible.
3. **Crossfade** — run dual filter banks for a configurable interval. Smoothest; more memory.

Default is warm-restart; protocols may opt in to crossfade via `controls.<name>.tune_strategy = "crossfade"`.

---

## 8. CRED-nf mapping

Every CRED-nf checklist item maps to a Refrain field. A complete protocol generates a CRED-nf-compliant supplement table via `refrain export cred-nf`.

| CRED-nf item | Refrain location |
|---|---|
| Pre/post outcome measures | `meta.outcome_measures` |
| Indication / clinical population | `meta.indication`, `meta.population` |
| Control / sham condition | `meta.control_ref` |
| Citation / prior literature | `meta.citation` |
| Adverse event monitoring | `meta.safety_monitoring` |
| Exact electrode locations | `requires.channels`, `input.*.montage` |
| Reference electrode | `input.*.montage` (referential) |
| Sampling rate | `requires.sample_rate` |
| Filter design (band, order) | `derive.*.pipeline` (bandpass entries) or `derive.*.formula` |
| Online artifact rejection | `inhibit.*.metric`, `inhibit.*.action` |
| Threshold algorithm | `threshold.*.type` |
| Number of sessions | `session.schedule` |
| Session duration | `session.phases[*].duration` |
| Reward modality | `output.*` bindings |
| Reward contingency timing | declared latency + `output.*` smoothing |
| Feedback signal computation | `derive.*` chain |
| Software / hardware used | `meta.runtime`, `meta.amplifier_model` (auto-filled by runtime) |

The compiler's CRED-nf coverage report flags any unfilled fields.

---

## 9. Versioning

Protocols and the language schema are versioned independently.

### 9.1 Protocol version

Declared in `meta.version` per protocol file. Semantic-versioned. Useful for protocol-pack consumers.

### 9.2 Schema version

Declared via the file header convention (or a `schema_version` field in `meta` if not at file top). Format: `v<major>.<minor>`.

- **Minor bumps** are backward-compatible: new constructs added; old constructs still parse and execute.
- **Major bumps** may break compatibility. Runtimes must not silently load major-mismatched files.

### 9.3 Runtime compatibility

A runtime declares the schema versions it supports. A protocol whose schema is newer than the runtime supports is refused at load with a clear diagnostic.

---

## 10. Open design questions

The strawman is incomplete in several places. These are flagged for discussion, not decision:

1. **Vector stream syntax.** §5.2 sketches the type, but the v0.0 surface syntax for vector reductions (e.g., `pct_in_range`) is underspecified. LZT-class protocols depend on getting this right.

2. **Source-space NF.** The `source_project` montage is named in §4.3 but its semantics — particularly how the inverse operator is sourced — is hand-waved.

3. **Custom output channels.** v0.0 limits `output` to a fixed channel list. Allowing extensions (e.g., a research lab's haptic feedback channel) is plausible but not specified.

4. **Time-locked paradigms.** ERP-NF and stimulus-locked NF do not fit the streaming-pipeline model. v0.0 declares them out of scope; whether a future v0.x adds an `epoch_locked` derivation is undecided.

5. **The norms-provider interface.** Live z-score training requires external normative data. The interface (`norms.power_db.lookup(age, channel, band)` or similar) is sketched but not specified.

6. **Session-level scheduling.** `session.phases` is declared; `session.schedule` (multi-session arc, e.g., "30 sessions over 8 weeks, every 2 days") is not.

7. **Reserved-word collisions.** Identifiers like `bipolar`, `bandpass` may collide with `custom` declarations. The naming convention for primitives may need a namespace separator.

8. **Diamond inheritance order.** §11 specifies "last-amend-wins" for amendments along a single inheritance chain. Multi-inheritance is excluded for v0.0.

9. **Event-stream cost trade-offs.** `event.holds` and the rising-edge event are derivable from the same underlying state, but the runtime cost of supporting both consumption modes for every event_stream by default may motivate an opt-in declaration.

10. **`align_to` interpolation modes.** v0.0 defaults to sample-and-hold for explicit alignment; whether `mode: "interpolate"`, `mode: "average"`, or other reduction strategies belong in the standard library is open.

---

## 11. Composition and inheritance

A protocol may extend another protocol via the `extends` clause:

```refrain
protocol "child" extends "library/parent@1.2" { ... }
```

The protocol reference must include a major version (`@1`, `@1.2`, etc.). The parent is loaded recursively at compile time; the merged protocol is what executes.

### 11.1 Default merge semantics

Different blocks have different default behaviors when both parent and child declare them:

| Parent / child collision | Default behavior |
|---|---|
| `meta.<field>` | Child field overrides parent field; unmentioned fields inherit |
| `requires.<field>` | Child field overrides parent field; unmentioned fields inherit |
| Named `input "X"`, `derive "X"`, `threshold "X"`, `inhibit "X"`, `custom "X"` | Child re-declaration **replaces** parent's same-named block in full |
| `reward` (singleton) | Child's reward block replaces parent's |
| `output` (singleton) | Child's output block replaces parent's |
| `controls.<name>` | Child control overrides parent same-named control; unmentioned controls inherit |
| `session` | Child replaces parent |

A new named block in the child (one whose name doesn't appear in the parent) extends the set.

The principle: *the most common operation in clinical practice is "I want this protocol but with a different placement / different threshold / different ORF default."* Named-block default is replace (unambiguous intent when re-declared); for partial override, use `amend`; for deletion, use `remove`.

### 11.2 `amend` — partial override

To override specific fields of a parent's block while keeping the rest:

```refrain
amend inhibit "emg" {
  threshold = absolute(15 uV2)   // override just this field
  // metric, action inherited from parent's emg inhibit
}

amend reward {
  continuous = sigmoid("smr" / "smr_t", midpoint: 1.0, steepness: 5)
  // event inherited from parent
}

amend meta {
  description = "Stricter emg threshold variant"
}
```

`amend` requires the named target to exist in the parent. Amending a non-existent target is an error: "amend target not found in parent."

### 11.3 `remove` — explicit deletion

To remove a parent's named block:

```refrain
remove inhibit "emg"
remove inhibit "high_beta"
remove derive "high_beta_envelope"
```

`remove` requires the named target to exist; removing a non-existent target is an error. Removing a `final` declaration is also an error (see §11.4).

### 11.4 `final` — un-overridable parent declarations

Parent protocols may mark declarations as `final`, preventing child override or removal:

```refrain
// In library/safety_base@1.0
inhibit "safety_emg" {
  metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
  threshold = percentile(target_pct: 99, window: 2 min)
  action    = mute(release: 200 ms)
  final     = true   // children cannot remove or amend
}
```

Children attempting to redeclare, amend, or remove a `final` parent declaration produce a compile error. Useful for safety-critical artifact rejection that should survive composition.

### 11.5 Schema version compatibility

`extends "library/foo@1.2"` requires the parent's schema version (declared in its `meta`) to be `1.x` for some `x ≤ 2` if the child's schema version is `1.x`. Major-version mismatch is an error. Newer-child-on-older-parent within the same major is permitted. Older-child-on-newer-parent within the same major produces a warning ("you may not be using new features available in the parent").

### 11.6 Chained inheritance

Chained inheritance (`A → B → C`) is supported. Multiple inheritance is *not* supported in v0.0; a protocol may extend at most one parent.

When B amends a field of A, and C also amends the same field, C wins (last-amend-wins along the inheritance chain). Children always see their immediate parent's view, with their own overrides applied on top.

---

*This is v0.0r1 strawman. Comments, disagreements, counter-proposals welcome.*
