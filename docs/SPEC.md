# Refrain Language Reference

**Version:** v0.0 (strawman, revision 1)
**Status:** working draft, expected to change
**Companion docs:** [`CONCEPT.md`](./CONCEPT.md), [`TOUR.md`](./TOUR.md), [`PRIMITIVES.md`](./PRIMITIVES.md)

This document defines the Refrain language at a level intended to be precise enough for an implementer and complete enough for a peer reviewer. It is paired with the standard library reference (`PRIMITIVES.md`) and a tutorial (`TOUR.md`). Neither this document nor the language is final; both are first drafts.

**Revision history.** v0.0r1 reflects three design decisions resolved after the initial strawman: (1) every `derive` is fundamentally an expression, with `from + pipeline` as sugar for the linear case; (2) the `reward` block has a single shape with two optional expression-valued fields, `continuous` and `event`; (3) protocol composition has default semantics (child replaces parent for named blocks) plus explicit `amend`, `remove`, and `final` modifiers.

---

## 1. Introduction

Refrain is a declarative description language for biosignal training paradigms. A Refrain file describes, in full, what a training protocol does: required hardware, signal-source binding, signal-processing pipeline, threshold logic, inhibit gates, reward expression, output bindings, practitioner-tunable controls, and session structure. EEG neurofeedback is the most developed worked example; HRV coherence training uses the same structure.

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

Metadata. Required fields: `version`, `evidence`, `description`. Optional fields cover author, citation, lineage, control protocol reference, indication, target population, safety monitoring, outcome measures, and the **sham strategies whitelist** for research-mode operation (§7.9).

```refrain
meta {
  version           = "1.0.0"
  evidence          = "established"   // how established the APPROACH is: "established" | "probable" | "exploratory"
  description       = "Othmer ILF training, T3-T4 bipolar"
  author            = "Peak Mind"
  citation          = "Othmer & Othmer 2009"
  population        = "adults_18_plus"
  indication        = "regulatory_dysfunction"
  control_ref       = "library/othmer/ilf_sham@1.0"
  outcome_measures  = ["BAI", "BDI-II", "subjective_arousal"]
  sham_strategies   = ["time_shifted_self", "phase_scrambled"]
}
```

The `meta` block carries all CRED-nf-aligned metadata. See §8.

#### `meta.sham_strategies`

A whitelist of chunk-transformer type names that are *methodologically valid* sham conditions for this protocol. Runtimes that support research mode (§7.9) MUST reject host attempts to use a sham type not in this list. An empty list or absent field means **no sham is permitted for this protocol** — strict by default, since sham appropriateness depends on what the protocol is training.

Permitted values are runtime-defined; the reference implementation accepts `"time_shifted_self"`, `"phase_scrambled"`, and `"yoked_replay"` (§7.9.2). Protocol authors choose which subset is methodologically appropriate for the training they describe; e.g., `phase_scrambled` is inappropriate for protocols that train phase-coherence features.

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

The `channels` array may include bare placement-control names alongside string literals. Each placement name is expanded at resolve time to its bound channel(s): one channel for `active` placements, both legs for `bipolar` placements. The expanded channels are then validated against the connected device (see §4.9 `placement`).

```refrain
controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3","C4"] } }
requires { channels = [site] }   // expands to the bound channel at resolve time
```

### 4.3 `input`

Named input streams derived from the hardware's raw channels.

`montage` names one kind of **signal-source binding** — how a named input is
derived from those channels. `referential(...)`, `bipolar(...)`, and
`laplacian(...)` are EEG montages in the conventional sense. `passthrough()` is
the identity binding for a single-channel non-EEG source: an HRV tachogram, a
skin-conductance level, a temperature trace. The keyword is EEG-flavored for
historical reasons; the concept is not. Nothing about a `passthrough` input
requires an EEG amplifier.

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

Montage channel-name slots accept **placement-control names** (bare identifier, resolved to a concrete channel at resolve time) in addition to string literals. The bound concrete channel is substituted before IR-JSON emission, so the resolved IR is identical in shape to a hardcoded-site protocol.

```refrain
controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3","C4"] } }
input "raw" { montage = referential(active: site, reference: "linked_ears") }
```

For `bipolar` placements a paired montage form is also available:

```refrain
controls { site = placement { kind = "bipolar"; default = ("T3","T4"); allowed = [("T3","T4"),("C3","C4")] } }
input "raw" { montage = bipolar(pair: site) }  // expands to bipolar(plus: ..., minus: ...) at resolve time
```

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
  combine    = "all"                                       // optional; "all" | "any" (Mode 2a only)
}
```

The `combine` field is used with Mode 2a per-site replication (§4.9.1). It selects how the per-site reward conditions are joined — `"all"` (every site must fire) or `"any"` (any site firing is sufficient). Default is `"all"`. This field has no effect unless a `kind="set"` placement is bound into the protocol.

The reward block exposes:

- **`reward.continuous`** — `stream<scalar>`, the graded value. Output bindings clamp to [0, 1] for analog channels.
- **`reward.event`** — has two consumption modes:
  - direct binding to event-channel outputs (`audio_chime = reward.event`) — emits on rising edges of the underlying condition
  - `.holds` member access (`reward.event.holds`) — `stream<boolean>` indicating whether the dwell condition is currently satisfied

#### Named reward checks

A direct element of the reward dwell's `all_of([...])` / `any_of([...])` list
(or the single condition of a one-condition dwell, wrapped in a one-element
`all_of`) may carry a name, for the autopilot advisor (§7.10) to address it
by:

```refrain
reward {
  event = dwell(
    condition: all_of([
      above("theta_envelope", "theta_t")           as "theta",
      above("theta_alpha_ratio", crossover_target) as "crossover",
    ]),
    duration: 1000 ms
  )
}
```

- `as` is legal only on an element of a reward dwell condition list; anywhere
  else is a `ResolveError` (§6.7 V15).
- Names must be unique across the whole protocol: a name used in the
  top-level `reward { }` or in one named, block-selectable `reward "<name>" { }`
  bundle (staged protocols) cannot be used again in any other, because
  `fixes`, `limiter` and `tighten_first` address a check by name alone. An
  empty name (`as ""`) is rejected (§6.7 V15).
- Naming a check changes no expression node and no tap key —
  `reward/condition[i]` is unaffected; names are recorded separately as
  `reward.check_names`, a list aligned with the condition indices (`None` for
  an unnamed element). A protocol with no named checks omits the field
  entirely.
- Unnamed checks are addressed in advice as `check 0`, `check 1`, … (0-based,
  matching `reward/condition[i]`).
- Named checks cannot yet be combined with band fan-out (`bands { }`, §4.9.3)
  or per-site fan-out (a `placement { kind = "set" }` montage, §4.9.1) —
  replication would have to duplicate or rename the checks. The compiler
  refuses this at resolve time.
- See `docs/AUTOPILOT-AUTHORING.md` for when and how to use names.

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

Bindings to person-facing modulation channels. Each binding is an expression that evaluates per step at the binding's declared rate.

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

Practitioner-tunable parameters. Each control declares its type, range, default, and whether it can be mutated mid-session.

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

Control types: `frequency`, `duration`, `voltage`, `percent`, `number`, `enum`, `boolean`, and `placement` (see below). Each value declared in `controls` is referenceable by name (e.g., `orf` in §4.4). `number` is a **unitless scalar** (dimensionless, value used as-is) — the right kind for relative weights and gains; prefer it over `percent` when the value is not actually a percentage (a host renders `percent` with a "%" unit, `number` with none).

#### `placement` control type

A categorical/dimensionless control for electrode site binding. Unlike numeric controls, a placement control is **resolved at deploy time** (not live-tunable mid-session); the resolved concrete channel is substituted into montage slots and `requires.channels` before IR-JSON emission. The wire format carries no placement control — the emitted IR is identical in shape to a hardcoded-site protocol.

Four `kind` values are supported:

| `kind` | Default shape | `allowed` element | Use case |
|--------|--------------|-------------------|----------|
| `"active"` | channel string | channel string | single-electrode site (Mode 1 & 3) |
| `"bipolar"` | 2-tuple `("plus","minus")` | 2-tuple | subtracted bipolar pair (Mode 1 & 3) |
| `"pair"` | 2-tuple `("a","b")` | 2-tuple | coherence pair — legs accessed via `.a`/`.b` (Mode 2) |
| `"set"` | list of channel strings | channel string | multi-site set for per-site replication (Mode 2a) |

**`kind="active"` and `kind="bipolar"` (Modes 1 & 3):**

```refrain
controls {
  site = placement {
    kind         = "active"            // "active" | "bipolar"
    default      = "Cz"               // active: a channel name; bipolar: a pair ("T3","T4")
    allowed      = ["Cz","C3","C4"]   // explicit allowlist; or "any" to permit all device channels
    label        = "Training site"    // optional, for the deploy UI
    live_tunable = false              // always false for placement (frozen per session)
    final        = false              // true → locked site; cannot be overridden (§11.4)
  }
}
```

**`kind="pair"` — coherence pairs (Mode 2):**

A coupled two-site pair whose two legs feed two separate inputs, for training the relationship between sites (e.g., inter-hemispheric coherence). The legs are accessed via `.a` and `.b` member access in montage channel slots.

```refrain
controls {
  coh = placement {
    kind    = "pair"
    default = ("F3","F4")
    allowed = [("F3","F4"), ("C3","C4")]
    label   = "Coherence pair"
  }
}
input "a" { montage = referential(active: coh.a, reference: "linked_ears") }
input "b" { montage = referential(active: coh.b, reference: "linked_ears") }
requires { channels = [coh] }   // expands to both legs at resolve time
```

`coh.a` resolves to the first element of the bound pair; `coh.b` to the second. The distinction is symmetric — there is no plus/minus polarity for coherence pairs. Binding:

```python
ir = resolve(parse(src), amp, bindings={"coh": ("C3","C4")})
```

`requires.channels = [coh]` expands to both legs. Each leg is validated against the device and against `allowed`. The `final` lock applies.

**`kind="set"` — multi-site set (Mode 2a):**

A set of N sites (N chosen at deploy time), used to replicate a single-site protocol across all bound sites via implicit fan-out (see §4.9.1 below).

```refrain
controls {
  sites = placement {
    kind    = "set"
    default = ["Cz"]
    allowed = ["C3","Cz","C4","Pz"]   // or "any"
    min     = 1                        // minimum set size (default 1)
    max     = 4                        // maximum set size (default: unbounded)
    label   = "Training sites"
  }
}
```

Binding:

```python
ir = resolve(parse(src), amp, bindings={"sites": ["C3","Cz","C4"]})
```

Validation: each member ∈ `allowed` ∩ device-capable; `min ≤ count ≤ max`.

**Common fields (all kinds):**
- `kind` (required): `"active"`, `"bipolar"`, `"pair"`, or `"set"`.
- `default` (required): the default site(s). Shape depends on `kind`. Must satisfy `allowed` and `min`/`max` constraints.
- `allowed`: explicit allowlist or `"any"`. For `active`/`set`, elements are channel strings; for `bipolar`/`pair`, elements are 2-tuples. For `active`/`set` it may also be a bare group name (see §4.9.2 `groups`); `bipolar`/`pair` allowlists must be written inline.
- `label`: optional display name for the deploy UI.
- `live_tunable`: must be `false` (or absent); placement is frozen per session.
- `final`: when `true`, the site is locked — `bindings` overrides are rejected, and child protocols cannot redeclare this control (§11.4).
- `min`, `max`: integer size bounds, `"set"` only.

Validation at resolve time (fail-fast, all kinds):
- Bound value must be in `allowed` (unless `"any"`).
- Bound channel(s) must be present on the connected device (`amp.has_channel`).
- For `"set"`: `min ≤ len(bound) ≤ max`.
- A `bindings` entry for a `final` placement control is a `ResolveError`.

**All placement kinds are omitted from the IR-JSON wire `controls` section**; IR-JSON schema version remains `0.1`.

#### 4.9.1 Mode 2a — per-site replication (implicit fan-out)

When a `kind="set"` placement is bound into an input montage channel slot, the resolver performs an **AST-level fan-out pre-pass** that rewrites the protocol to N per-site copies before resolution proceeds:

```refrain
controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4"]; min = 1; max = 3 } }
input "raw" { montage = referential(active: sites, reference: "linked_ears") }
derive "smr" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
threshold "smr_t" { signal = "smr"; type = absolute(8 uV) }
reward {
  combine = "all"   // "all" (default) | "any"
  event = dwell(condition: above("smr","smr_t"), duration: 250 ms)
}
output { audio_chime = reward.event }
```

When bound to `["C3","Cz","C4"]`, this resolves to a flat IR with:
- Three inputs: `raw@C3`, `raw@Cz`, `raw@C4`, each with its own concrete montage.
- Three derives: `smr@C3`, `smr@Cz`, `smr@C4`.
- Three thresholds: `smr_t@C3`, `smr_t@Cz`, `smr_t@C4`.
- One combined reward: `dwell(condition: all_of([above("smr@C3","smr_t@C3"), above("smr@Cz","smr_t@Cz"), above("smr@C4","smr_t@C4")]), ...)`.

The fan-out computes the **per-site subgraph** as the transitive closure of derives, thresholds, and inhibits downstream of the set-bound input; only those entities are replicated. Entities that do not depend on the set-bound input (e.g., a fixed-channel inhibit) remain single.

**`reward.combine`** selects how per-site conditions are joined:
- `"all"` (default): `all_of([...])` — reward fires only when every site meets its condition.
- `"any"`: `any_of([...])` — reward fires when any site meets its condition.

**Scoping constraints (resolve-time errors):**
- A `reward.continuous` expression that depends on a per-site replicated stream raises `ResolveError`. Continuous reward over a replicated set requires vector aggregation (Mode 2b, deferred). A continuous reward that depends only on non-replicated streams is permitted.
- A derive that mixes a per-site stream with a non-replicated one (ambiguous replication boundary) raises `ResolveError`. The per-site subgraph boundary must be unambiguous.

**Canonical naming:** per-site entities are named `<name>@<site>` (e.g., `derive/smr@C3`). These names flow through to `last_taps()` and event keys, enabling per-site state observation in practitioner dashboards.

**Wire format:** the fan-out-unrolled IR uses only existing IR-JSON node types. The `sites` control is omitted from the wire (resolve-time-only); the emitted IR is shaped identically to a hand-written multi-site protocol. IR-JSON schema version remains `0.1`.

#### 4.9.2 `groups`

A `groups` block is a **top-level block** (a sibling of `controls`) that declares **named channel-name lists** which can be referenced from placement controls. Groups are resolve-time aliases — they expand to the same channel tuples as an inline list and never appear in the IR-JSON wire format.

#### Syntax

```refrain
groups {
  sensorimotor = ["C3","Cz","C4","CP3","CP4"]
  frontal      = ["F3","Fz","F4"]
}
```

Each entry is `<ident> = [ <string-lit>, ... ]`: a non-empty, duplicate-free list of channel-name string literals.

#### Reference sites

A group name may appear as a **bare identifier** in two positions:

1. **`allowed`** of an `active` or `set` placement control (`bipolar`/`pair` allowlists are 2-tuple lists and must be written inline):

   ```refrain
   controls {
     site = placement { kind = "active"; default = "Cz"; allowed = sensorimotor }
   }
   ```

2. **`default`** of a `set` placement control:

   ```refrain
   controls {
     sites = placement { kind = "set"; allowed = sensorimotor; default = sensorimotor; min = 1; max = 3 }
   }
   ```

A bare identifier in either position that does not name a declared group raises `ResolveError` (`"unknown group 'x'"`).

#### Validation

- **Empty group** — a `groups` entry with `[]` raises `ResolveError`.
- **Duplicate channel within a group** — listing the same channel string more than once raises `ResolveError`.
- **Name collision** — a group name equal to a control name raises `ResolveError` (both share the bare-identifier namespace in `allowed`/`default` value position).
- **Post-expansion checks** — the existing `allowed` and `min`/`max` validations run on the expanded value, so `default = <group>` whose size exceeds `max` is still rejected.

#### Composition

The `groups` block merges across `extends` using the same field-level merge as `controls`: child groups override parent same-named groups; unmentioned parent groups are inherited; the child may add new groups.

#### Wire invariant

Groups expand at resolve time. The IR-JSON wire format carries no `groups` key; `IR_JSON_VERSION` remains `0.1`. See also §4.9 `controls` for the placement control types that accept group references.

#### 4.9.3 `bands` — band-axis fan-out

A `bands` block is a **top-level block** (a sibling of `controls`/`groups`) that declares **named frequency bands** and drives a **second fan-out axis**: a band-parameterized subgraph is replicated once per band, analogous to per-site replication (§4.9.1).

```refrain
bands {
  theta = (4 Hz, 8 Hz)
  alpha = (8 Hz, 12 Hz)
  smr   = (12 Hz, 15 Hz)
}
derive "env" { from = "raw"; pipeline = [ bandpass(band: bands, order: 4), hilbert(), magnitude() ] }
```

Each entry is `<ident> = (low Hz, high Hz)`: a frequency 2-tuple with `low < high`. The bare NameRef `bands` in a `bandpass(band: bands)` slot marks the **band-axis seed**. The resolver replicates that derive and its forward closure (derive/threshold/inhibit) once per band, substituting the band's concrete tuple into `bandpass(band:)` and naming copies `<name>@<band>`. Upstream entities the seed merely *consumes* (e.g. the shared input) are not replicated.

**Composition with per-site fan-out (the cross product).** Band fan-out runs *before* the per-site pass. When a protocol declares both a `bands` block and a `kind="set"` placement bound into the shared input, the band copies are then per-site replicated, yielding the **band × channel cross product** `<name>@<band>@<site>` (e.g. 10 bands × {C3, C4} = 20 envelopes). Inhibits participate in both axes.

**Validation:** a band value must be a frequency 2-tuple with `low < high`; a band name colliding with a control name raises `ResolveError`.

**Wire invariant:** front-end only — the fanned IR uses existing node types; the `bands` block is resolve-time-only and omitted from the wire. `IR_JSON_VERSION` unchanged.

#### 4.9.4 `seed` — control baseline seeding

A control field, not a top-level block — unrelated to the band-axis "seed" reference in §4.9.3 above (same word, different feature). `seed` lets a control derive its initial value from the person's own signal instead of a fixed `default`, the "set the threshold from the first two minutes of baseline" pattern:

```refrain
controls {
  smr_target_pct = percent {
    default      = 70
    live_tunable = true
    seed = percentile {
      from        = "smr_envelope"   // a declared derive, by bare name
      window      = 120 s            // trailing window measured during warmup
      target_pct  = 70                // number literal, or a `percent` control_ref
    }
  }
}
```

**Statistic is the block kind.** `seed = <statistic> { ... }` — the block's typed name names the statistic. v1 supports only `percentile`; an unrecognized statistic is a `ResolveError`. The fields inside the block belong to that statistic (`percentile` takes `from`/`window`/`target_pct`); a future statistic would define its own field set under its own block kind.

**Fields (`percentile`):**
- `from` (required): a quoted derive name — the signal the percentile is measured over. Must name a derive declared elsewhere in the protocol.
- `window` (required): a duration literal (`ms`/`s`/`min`) — the trailing window, in real time, the statistic is measured over. Rate-independent; baked to samples at emit time (below).
- `target_pct` (required): the percentile to measure — either a number literal or a `control_ref` to a sibling `percent` control (so the practitioner can tune *which* percentile seeds the value, not just the value itself).

**Resolve-time guarantees:**
- **Real derive.** `from` is validated against the resolved derive set; a name that doesn't resolve is a `ResolveError`, not a silent no-op.
- **`target_pct` typing.** A number literal is accepted as-is; a `control_ref` is accepted only if it targets a control declared `percent` — binding it to any other control kind is a `ResolveError`.
- **Warmup fits.** If the protocol's first session phase is a timed, output-muted warmup (not `mode = "open"`), `window` must fit inside that phase's duration. A seed whose window can never fill is refused at compile time, not discovered fail-closed at runtime.
- **Dead-seed elimination.** If the seeded control is never referenced anywhere in the resolved pipeline (derives, thresholds, inhibits, reward, output), the seed is dropped rather than attached — an unused control's seed rule would just be dead weight on the wire.

**Runtime semantics (informative; full behavior in `docs/EMBEDDING.md`'s `seed_report()` section):** during warmup the engine measures `from`'s samples into a trailing buffer sized to `window`; at the warmup→run edge it computes the `target_pct` percentile of that buffer and writes the control exactly once, then holds. A practitioner who calls `set_control` on the seeded control before it fires disarms the seed for the rest of the session instead of racing it. If the buffer can't be filled with finite samples by the run edge, the seed fails closed and the control keeps its declared `default`.

**Wire format.** A seeded control's IR-JSON carries a `seed` object (`statistic`/`from`/`window_samples`/`target_pct`) under `controls.<name>`; `window_samples` is `window` baked to samples **at the emitted `sample_rate_hz`**, not the resolver's chosen rate — the same rebaking discipline as every other rate-dependent coefficient. A protocol using `seed` is tagged `refrain_ir_version: "0.3"`. See `docs/IR-JSON.md`.

**§9.3 is now enforced.** A runtime that doesn't understand IR-JSON `"0.3"` refuses a seeded protocol at load (§9.3) rather than silently ignoring the `seed` field and running an unbaselined default — the version gate described in §9.3 is implemented, not aspirational, as of this feature.

#### 4.9.5 `autopilot` — per-control policy

A control field, following the `seed` precedent: `autopilot = <strategy> { ... }`, where the strategy is the block kind. Requires a protocol-wide `autopilot { }` block to exist (§4.12) and at least one named reward check (§4.7) for `fixes` to name:

```refrain
controls {
  crossover_target = number {
    default = 0.60; range = (0.5, 1.0); live_tunable = true
    autopilot = fixed_step {
      fixes     = "crossover"
      step      = 0.05
      higher_is = "harder"
      limits    = (0.5, 1.0)
      apply     = "auto"
      round_to  = 0.01
      say       = "Crossover target"
    }
  }

  theta_threshold_uv = voltage {
    default = 8.0 uV; range = (2.0 uV, 30.0 uV); live_tunable = true
    autopilot = proportional_step {
      fixes     = "theta"
      step      = 10%
      higher_is = "harder"
      apply     = "suggest"
      round_to  = 0.1 uV
      only_when = threshold_style == "baseline"
    }
  }
}
```

**Strategies** (the block kind names which one; eligible control kinds are `number`, `percent`, `voltage`, and `frequency` — not `duration`, and not `mode`/`boolean`/`enum`/`placement`):

| Strategy | Proposed value | Fields specific to it |
|---|---|---|
| `fixed_step` | `current ± step` | `step`: in the knob's own units (a bare number for `number`/`percent`; `uV`/`Hz` for `voltage`/`frequency`). |
| `proportional_step` | `current × (1 ± step)` | `step`: a percent, `0% < step < 100%`. |
| `rebaseline` | The `percentile` of the last `window` of clean samples of derive `from` | `from` (a declared derive, by bare name), `window` (duration, must fit inside the protocol-wide `watch`), `percentile` (number, 1–99). Always suggest-only — `apply = "auto"` is a `ResolveError`. |

**Fields common to all three strategies:**

| Field | Required | Meaning |
|---|---|---|
| `fixes` | yes | The named reward check this knob addresses. |
| `higher_is` | yes | `"harder"` or `"easier"` — what raising the knob's value does to the named check. Must agree with the direction the resolver traces from the check's own expression (§7.10.5), when that trace is unambiguous. |
| `apply` | yes | `"auto"` or `"suggest"`. `"auto"` is refused on a knob that feeds any inhibit, in any mode branch, and on a `rebaseline` policy. |
| `limits` | no | Autopilot's own bounds, which may be narrower than the control's `range`. Defaults to `range`; required if the control declares no `range`. |
| `round_to` | no | Snap every proposed value to this precision, in the knob's units. |
| `say` | no | Display name used in advice; defaults to the control's `label`, then its bare name. |
| `watch`, `between_moves` | no | Per-knob overrides of the protocol-wide values in §4.12. |
| `only_when` | no | Restricts the policy to one mode branch: a comparison of a `mode` control to one of its own string choices (e.g. `threshold_style == "baseline"`). Mode controls are bound at resolve time (the same folding other mode-dependent expressions use), so `only_when` is evaluated once at compile time — the policy is kept for the binding being compiled and silently dropped, not emitted, when the comparison doesn't hold. It never reaches the IR. |
| `citation` | no | Per-knob source, when this knob's numbers come from a different source than the block-level `citation`. |

The proposed value is always clamped to `limits` and snapped to `round_to`. If the result equals the current value, the knob is reported "at limit" instead of a no-op move.

See §6.7 for the compiler's full cross-reference validation (V1–V17) and `docs/AUTOPILOT-AUTHORING.md` for a worked, step-by-step guide.

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

### 4.12 `autopilot`

Protocol-wide settings for the autopilot advisor (§7.10): when advice runs, what target it judges reward against, and messages for guards and unaddressed limiters. New top-level section keyword, alongside `meta`, `reward`, `controls`, and so on.

```refrain
autopilot {
  // provenance — required whenever the block exists
  evidence  = "exploratory"
  citation  = "Peak Mind practice team (2026). Adapted from Peniston & Kulkosky 1989 practice."
  rationale = "Crossover is rare by nature; a 50-75% target would drive the ratio target to its floor."
  reviewed  = "J. Croall, 2026-09-24"          // optional

  // when and how advice runs — all optional, defaults below
  reward_target     = (10%, 35%)
  phases            = ["deep1", "deep2"]
  watch             = 2 min
  between_moves     = 3 min
  equipment_settle  = 60 s
  tighten_first     = ["crossover", "theta"]

  // guards: hold and explain when an inhibit is active too often
  delta = guard { max = 15%; say = "Client may be drifting toward sleep; check alertness." }
  emg   = guard { max = 15%; say = "Muscle artifact; check jaw/neck tension or the electrode." }

  // a reward check with no knob, or whose knob must not be touched
  high_beta = limiter { say = "Likely client tension; coach relaxation rather than loosening." }
}
```

**Settings:**

| Field | Type | Meaning |
|---|---|---|
| `evidence` | string, **required** | `established` \| `probable` \| `exploratory` — the same tiers as `meta.evidence` in the reference protocol library (how established the approach behind the numbers is in prior art; provenance, not an outcome claim). A closed set the compiler checks, unlike `meta.evidence`. |
| `citation` | string or array of strings, **required** | Source(s) for the numbers. |
| `rationale` | string, **required** | One or two sentences on why these numbers. |
| `reviewed` | string, optional | Who reviewed it and when. |
| `reward_target` | pair of percents | Target share of clean training time during which the reward condition is met (§7.10.2). `low < high`, both strictly between 0% and 100%. |
| `phases` | array of phase names | Phases in which advice may run. Each name must be a declared session phase whose output is not muted. |
| `watch` | duration | Clean time required before any judgement. |
| `between_moves` | duration | Minimum time between two applied changes (to any knob). |
| `equipment_settle` | duration | Hold after the host reports an equipment change. |
| `tighten_first` | array of check names | Order in which named checks are tightened when reward is above target (§7.10.4). |

**Entries** (distinguished from settings by their block kind; an entry whose name collides with a setting name is a `ResolveError`):

| Entry | Meaning |
|---|---|
| `<inhibit> = guard { max; say }` | Ceiling (percent) on that inhibit's active share of training time, and the message shown when exceeded. The name must be a declared inhibit. |
| `<check> = limiter { say }` | Message used when this check is the limiter and no eligible policy fixes it. The name must be a named reward check. |

**Built-in defaults**, used for any setting the block omits — including protocols with no `autopilot { }` block at all — versioned as `ADVISOR_VERSION = "1"` (`advisor_version` in every advice result and audit event, §7.10):

| Setting | Default | Source |
|---|---|---|
| `reward_target` | (50%, 75%) | Coherence Recorder's `nf-coach` sweet-spot band |
| `phases` | Every session phase whose output is not muted; the whole run if the protocol has no `session` block | — |
| `watch` | 2 min | Coherence Recorder `guidance.py`'s window |
| `between_moves` | 3 min | Coherence Recorder `guidance.py`'s suggestion cadence |
| `equipment_settle` | 60 s | Coherence Recorder `guidance.py`'s equipment freeze |
| guard `max` (every inhibit, when no `guard` entry names it) | 15% | Coherence Recorder `guidance.py`'s guard ceiling |
| guard `say` (an entry — or the default — with none) | "`<inhibit>` guard active `<n>`% of training time." | — |
| `tighten_first` | none: tighten whichever check currently passes most often | — |

Defaults are not part of the protocol and are not in `content_hash`.

**Composition:** `autopilot` is a singleton section, merged like `reward` — a child's block replaces the parent's; `amend autopilot { ... }` overrides individual settings and entries. `remove` does not apply to sections. A control's `autopilot` field merges with the rest of the control at field level, exactly as `seed` does. `final` protects named declarations and controls (§11.4); it does not apply to whole sections, so `final autopilot { ... }` is not supported.

See §4.9.5 for per-control policies, §6.7 for the compiler's cross-reference validation, §7.10 for the decision procedure, and `docs/AUTOPILOT-AUTHORING.md` for a worked, step-by-step guide.

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

### 6.7 Autopilot validation

Checks specific to named reward checks (§4.7), the `autopilot` block (§4.12), and per-control policies (§4.9.5), run on the final, merged protocol (after `extends`, `amend`, mode folding). Every rule is a `ResolveError` naming the offending control or entry and the problem, in plain words; a protocol that fails any rule does not compile — there is no "compiles but autopilot is disabled" fallback.

| # | Rejected | Why |
|---|---|---|
| V1 | `fixes` names a check that does not exist | A typo would silently disable the policy. |
| V2 | The knob does not feed the check it `fixes` (traced through thresholds, derives, and control refs, after mode folding) | A policy may not claim a knob fixes what it cannot affect; also forces `only_when` onto mode-dependent knobs. |
| V3 | Declared `higher_is` contradicts the traced direction, when the trace is unambiguous (§7.10.5) | Catches a reversed direction. |
| V4 | `apply = "auto"` on a knob that feeds any inhibit, in any mode branch (checked before folding) | Guards are never loosened automatically. |
| V5 | A policy on a knob that feeds no reward check (volume-like knobs, band edges) | Enforced by V2: such a knob cannot feed the check it claims to `fixes`. |
| V6 | `rebaseline` with `apply = "auto"` | Large jumps are always the practitioner's call. |
| V7 | Two surviving policies `fixes` the same check | One knob per check; mode-exclusive pairs are resolved by `only_when` before this check runs. |
| V8 | Knob is not `live_tunable`, or its kind is `mode`, `boolean`, `enum`, or `placement` | Cannot be changed mid-session. |
| V9 | `limits` outside `range`; no `limits` and no `range`; `low ≥ high` | Scale errors. |
| V10 | `step ≤ 0`; a `fixed_step` step in the wrong units; a `proportional_step` step that isn't a percent, or is `≥ 100%` | Scale errors. |
| V11 | `rebaseline.from` is not a declared derive; its unit is incompatible with the knob's; `percentile` outside 1–99 | |
| V12 | `guard` entry names no inhibit; `limiter` entry or `tighten_first` names no check; `phases` names an unknown or output-muted phase | Typos cannot silently disable a safety check. |
| V13 | Knob policies present with no `autopilot` block; `autopilot` block missing `evidence`, `citation`, or `rationale`; `evidence` outside the closed set | No unsourced policy. |
| V14 | `reward_target` malformed or outside (0%, 100%); durations ≤ 0 | |
| V15 | `as "<name>"` outside a reward dwell condition list; an empty name; the same name used twice anywhere in the protocol (within one bundle or across bundles) | `fixes`, `limiter` and `tighten_first` refer to a check by name alone. |
| V16 | `autopilot` targets or knob policies on a protocol with no reward condition, or one using a weighted composite reward | There is no per-sample condition for autopilot to judge. |
| V17 | `only_when` that is not `<mode control> == "<choice>"` / `!=`, or names a choice the mode does not declare | |
| V18 | `apply = "auto"` on a knob the tracer cannot follow to the check it `fixes` (the check does not compare its signal directly against this control — e.g. `above(S, k * 1.0)`, or two knobs in one threshold) | V3 can only verify `higher_is` when the trace is unambiguous. An unverified direction may be wrong, and an automatic move in the wrong direction would walk the knob to its limit before anyone looks. Such a knob may only suggest (`apply = "suggest"`). |
| V19 | The same setting, `guard` or `limiter` entry declared twice inside one `autopilot { }` block | One of them would silently win. |

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

Each inhibit produces a boolean stream. Inhibits affect the values that reach the person via output bindings:

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

### 7.8 Tap API (host introspection)

A Refrain runtime SHOULD expose per-chunk last-sample values of the internal stream computations through a host-facing introspection method. The canonical signature in the reference implementation is `Evaluator.last_taps() -> dict[str, float | bool]`; other runtimes MAY name the method differently as long as they expose the same keyset and value contract.

The keyset is derived from the resolved IR's named entities, with a uniform `<kind>/<name>` convention. A tap is present in the dict iff the corresponding entity exists in the resolved protocol:

| Tap key | Type | Meaning |
|---|---|---|
| `input/<name>` | float | last sample of the post-montage input stream |
| `derive/<name>` | float | last sample of the derive's output |
| `threshold/<name>` | float | current adaptive threshold value (last sample) |
| `inhibit/<name>` | boolean | whether this inhibit is currently active |
| `muted` | boolean | combined inhibit-gate state |
| `reward/continuous` | float | the `reward.continuous` value *before* output-stage gating |
| `reward/event` | boolean | whether the dwell event fired *any* sample in this chunk |
| `reward/event.holds` | boolean | whether the dwell condition is currently held |
| `reward/condition[i]` | boolean | i-th sub-condition of the dwell. Single-condition dwells uniformly emit `reward/condition[0]`. |
| `reward/composite` | float | the weighted-composite success in [0,1] (v0.2; present only when the protocol declares named reward/suppress components — §4.7) |
| `reward/component[<name>]` | float | a named component's [0,1] success signal (v0.2; one per component) |
| `output/<channel>` | float \| boolean | post-gating, post-clamp value of the person-facing channel |

Taps are populated identically during the `warmup` and `run` lifecycle states (§7.1). Hosts that render a practitioner observation window need warmup-state taps so the warmup progress is visualisable.

Implementation guidance (non-normative): tap collection should be a pure read of values the evaluator has already computed for reward/output evaluation. The reference implementation captures taps before the warmup output-suppression branch so the values are available even when person-facing events are suppressed.

See `docs/EMBEDDING.md` for the host-side API surface and a code example.

### 7.9 Research mode (sham conditions and allocation concealment)

Refrain runtimes SHOULD support a research-mode operating mode that enables CRED-nf-grade controlled studies: blinded allocation between real and sham conditions, with the allocation decision sealed cryptographically so neither practitioner nor researcher can decode it until the study is unblinded. The reference implementation is described here; runtimes that match this contract can claim conformance regardless of internal architecture.

The full threat model, cryptographic protocol, and constant-time guarantees are in `docs/RESEARCH-MODE.md`. This section establishes the language-level contract.

#### 7.9.1 The chunk-transformer abstraction

A *chunk transformer* is a stateful object that sits between the host's raw EEG chunks and the evaluator's reward/output pipeline. Its surface contract:

```python
class ChunkTransformer:
    def step(self, raw_chunk: np.ndarray) -> np.ndarray: ...
    def reset(self) -> None: ...
```

When configured, the evaluator calls `transformer.step(chunk)` on each incoming chunk and processes the returned chunk as if it were the raw input. **Every downstream observable — reward events, output bindings, tap values per §7.8 — reflects the transformed signal, not the original.** This is required for blinding: a practitioner's observation window plotting envelopes from the "real" signal during a sham session would instantly unblind.

The identity transformer (which returns chunks unchanged) is the default. Hosts opt into a custom transformer via the runtime's `Evaluator.live(..., chunk_transformer=...)` API or equivalent.

#### 7.9.2 Standard sham types

The reference implementation ships three first-class sham transformers, each preserving and destroying different signal properties:

| Sham type | Preserves | Destroys | Use case |
|---|---|---|---|
| `time_shifted_self` | spectral statistics, artifact structure, channel relationships | temporal correlation with the person's current state | training paradigms where the *type* of signal matters but the *timing* is what carries the conditioning |
| `phase_scrambled` | power spectrum within the scrambling window | phase coherence, transient structure | training paradigms that depend on amplitude-based features (most envelope-based NF) |
| `yoked_replay` | inter-channel and temporal structure of a real recording | any link to the current person's state | designs that need a "control person's signal" as the sham, common in multi-arm studies |

Runtimes MAY ship additional sham types; the protocol whitelist (`meta.sham_strategies`, §4.1) is the safety mechanism that prevents methodologically inappropriate shams from being applied to a given protocol.

#### 7.9.3 Sealed allocation

For studies that require true blinding, runtimes SHOULD support a *sealed allocation* mode in which Refrain itself owns the randomization and conceals the result. The mechanism:

1. The host provides a list of permitted sham candidates and an X25519 public key.
2. Refrain rolls a cryptographically-random allocation (real vs sham; if sham, which type).
3. Refrain selects the corresponding transformer for the session.
4. Refrain seals the allocation decision with libsodium `crypto_box_seal` against the public key, producing an opaque token.
5. The host stores the token. Neither host nor practitioner can decrypt it.
6. Post-hoc, the holder of the matching X25519 private key (typically an independent statistician) decrypts every session's token and reconstructs the allocation matrix.

The sealed plaintext is a JSON object whose schema is fixed at the language level (so cross-runtime tokens are interoperable):

```json
{
  "version": 1,
  "condition": "real" | "sham",
  "sham_type": "time_shifted_self" | "phase_scrambled" | "yoked_replay" | null,
  "sham_params": { ... },
  "candidate_index": 0,
  "seed": "0x...",
  "timestamp": "2026-05-12T...Z",
  "refrain_version": "0.0.5",
  "protocol_id": "smr_cz_brainbit_v1",
  "protocol_hash": "sha256:..."
}
```

`protocol_hash` is the SHA-256 of the canonical-unparsed *resolved* IR — not the source file. Resolved-IR hashing means the hash captures composition (`extends`, `amend`, `remove`) and is invariant to whitespace, comment, and source-file-arrangement changes. Two sessions with the same `protocol_hash` are guaranteed to have run the same computation.

#### 7.9.4 Constraints

Runtimes claiming research-mode conformance MUST guarantee:

- **No within-session side channels.** A practitioner observing the person's session in real time MUST NOT be able to distinguish real from sham via timing patterns, event distributions, latency profiles, or any other observable. Constant-time-within-session is mandatory.
- **Observable internals reflect the processed signal.** The §7.8 tap API MUST expose values from the transformed (sham) signal during sham mode, not from the underlying raw input. Otherwise tap-based observation windows unblind the practitioner.
- **Whitelist enforcement.** Runtimes MUST reject sham candidates whose type name is not in `meta.sham_strategies`.

Constant-time *across* sessions (i.e., resistance to timing attacks aggregated over many runs) is OPTIONAL and host-configurable. See `docs/RESEARCH-MODE.md` for the threat model and the opt-in `strict_constant_time` mode.

See `docs/RESEARCH-MODE.md` for the full cryptographic protocol, per-sham-type constant-time guarantees, sealed-token format spec, and test fixtures. See `docs/EMBEDDING.md` for host-side integration.

### 7.10 Autopilot advisor

Refrain runs a fixed, deterministic decision procedure — the *advisor* — after
every chunk, turning aggregated per-sample facts into one structured piece of
advice: hold steady, keep collecting evidence, offer a direction hint, or
propose a concrete adjustment. It runs identically whether or not the
protocol declares an `autopilot { }` block (§4.12) or any per-control policy
(§4.9.5); a protocol with neither still gets observations and, where
unambiguous, hints, judged against the built-in defaults. The reference
implementation is `src/refrain/advisor.py`, ported line-for-line (same
function names, same order of operations, same message strings) by
`refrain-core/src/advisor.rs`; both are exercised by the same scripted-fact
tests and a parity check that runs identical inputs through both.

The advisor is a passive observer of the same host actions that already
exist — `step_chunk`, `set_control`, seed firing — nothing in the host
integration changes to enable it. See `docs/EMBEDDING.md` "Autopilot advice"
for the host-facing calls.

#### 7.10.1 Where it runs

The Evaluator owns one advisor instance per session and feeds it aggregated
facts at the end of every `step_chunk`, the same way it runs the baseline-seed
latch. A knob change made through `set_control` cannot bypass it — the
advisor sees every control mutation, whichever path made it (§7.10.6).

Time is counted in **session sample time** (samples processed ÷ sample rate),
never wall-clock time, and no randomness is used anywhere in the procedure.
Window edges fall on chunk boundaries, so identical chunk sequences fed to
both engines produce the same advice values (§7.10.7's rounding rules make
this exact, once compared as parsed numbers rather than raw JSON bytes —
Python and Rust format floats differently).

#### 7.10.2 Evidence window

- The window is the most recent `watch` of **clean** samples. A sample is
  *in training* when its phase is in the advisor's `phases` set, output is
  not phase-muted, and the session is not held or clock-frozen; it is
  **clean** when it is in training and no inhibit is active.
- Clean samples older than `2 × watch` of session time drop out of the
  window entirely, so evidence cannot be assembled from scraps scattered
  across a long, guard-interrupted stretch.
- Guard activity (for the guard check in §7.10.3) is measured over
  **in-training** samples in the same span — it does not require clean time,
  since an active guard is exactly what makes a sample unclean.
- **Reward rate** = clean samples with the reward condition met ÷ clean
  samples. This is the *instantaneous* condition, not the dwell-held state.
- **Check pass rate** = clean samples with that check true ÷ clean samples,
  one per named or unnamed check in the active reward bundle.
- **Chimes per minute** = reward events in the window ÷ window minutes.
  Reported for information only; it never drives a decision.

**The window restarts** when any control that feeds a reward check or an
inhibit changes — by manual `set_control`, by `apply_advice`, or by a
baseline seed firing — when the host calls `mark_equipment_change()`, or when
a new phase in `phases` begins. A manual `set_control` on such a control also
starts the `between_moves` cooldown (§7.10.3 step 11, §7.10.6), exactly as an
applied advice does — the advisor treats "the practitioner just moved this knob
by hand" the same as "autopilot just moved it" for cooldown purposes.

#### 7.10.3 Decision steps

Evaluated after every chunk; the first step that applies decides the result.

| # | Condition | Result `state` / `reason` |
|---|---|---|
| 1 | Current sample not in training | `hold` / `not_training_phase` |
| 2 | Within `equipment_settle` of the last equipment change | `hold` / `equipment_settling` (with remaining time) |
| 3 | Some inhibit's active share of in-training samples in the window exceeds its guard `max` (the worst one is reported). Only inhibits that can mute output count: when the active block declares a non-empty inhibit set, only those; otherwise every inhibit | `hold` / `guard`, with that guard's message (below) |
| 4 | Clean time in the window is less than `watch` | `collecting` / `collecting`, with progress |
| 5 | The protocol has no reward condition to judge (a continuous-only reward, or a weighted-composite reward) | `hold` / `observing` |
| 6 | The last change made through `apply_advice` went toward *harder* and reward rate is now below `low`, or toward *easier* and reward rate is now above `high`; evaluated once, on the first full window after that change | `adjust` / `reversal` — propose the pre-change value; bypasses `between_moves`. Once fired it stands until applied or dismissed, or until a later full window's reward rate is back inside the target band, which drops it and lets the normal decision take over |
| 7 | `low ≤ reward rate ≤ high` | `hold` / `on_track` |
| 8 | Select the limiting check (§7.10.4) | — |
| 9 | No surviving policy fixes that check | `hold` / `no_knob` (with the `limiter` entry's message, if any), or `hint` (§7.10.5) when the protocol declares no knob policies at all and the trace is unambiguous |
| 10 | The knob is at its limit in the needed direction | `hold` / `at_limit` |
| 11 | Less than `between_moves` since the last applied (or manual) change, or the same knob and direction was dismissed within `between_moves` | `hold` / `cooldown` (with `eligible_at_s`) |
| 12 | Otherwise | `adjust` / `too_strict` or `too_easy`, with the proposed value |

A protocol with no knob policies still runs every step; step 6 never fires
(there is no applied change to reverse), and step 9 yields `hint` or
`no_knob`. `observing` (step 5) is a reason, not a special case elsewhere in
the procedure — a continuous-only protocol reports collecting/guard/phase
holds exactly like any other, it simply never reaches a reward-rate judgement.

**Guard message.** A guard with no `say` reports "`<inhibit>` guard active
`<n>`% of training time."; a guard with `say = "<text>"` reports "`<text>`
(`<inhibit>` guard active `<n>`% of training time)."

#### 7.10.4 Which check

- **Too strict** (reward rate below `low`): the check with the **lowest**
  pass rate, ties broken by declaration order. Autopilot never loosens a
  different check to compensate — if the limiting check has no knob, it
  holds and says why.
- **Too easy** (reward rate above `high`): the first check named in
  `tighten_first` that has a surviving policy not already at its harder
  limit; if `tighten_first` is absent or every named check in it is
  exhausted, the check with the **highest** pass rate.

#### 7.10.5 Direction hints (no policy)

When a protocol declares **no knob policies at all**, the advisor still tells
a practitioner which way to move a setting, when it can determine that
unambiguously from the protocol's own expressions — without ever proposing a
value or allowing automatic application. (An uncovered limiter in a protocol
that *does* declare some knob policies gets `no_knob` instead, not a hint —
an author who wrote policies and left one check uncovered made a choice.)

The resolver traces, for each reward check, the single knob that determines
its threshold and the direction raising that knob moves the check:

| Check shape | Knob position | Higher knob value makes the check… |
|---|---|---|
| `above(S, k)`, `k` a control ref | — | harder |
| `below(S, k)`, `k` a control ref | — | easier |
| `above(S, "t")`, `t = absolute(value: k)` | | harder |
| `below(S, "t")`, `t = absolute(value: k)` | | easier |
| `above(S, "t")`, `t = percentile(target_pct: k, …)` | | harder |
| `below(S, "t")`, `t = percentile(target_pct: k, …)` | | easier |
| anything else (knob inside a derive formula, arithmetic on the threshold, more than one knob) | | ambiguous — no hint |

A hint is emitted only when exactly one live-tunable knob of an eligible kind
(§4.9.5) determines the limiting check's threshold, the trace is unambiguous,
and the knob feeds no inhibit. The hint names the knob, its label, and the
direction ("lower is easier") — never a proposed value. The same trace backs
compile-time rule V3 (§6.7): a declared `higher_is` that contradicts this
trace is refused. When the trace is ambiguous, `higher_is` cannot be checked,
so rule V18 limits that knob to `apply = "suggest"`.

The tracer is a small, table-driven function implemented identically in both
engines (`src/refrain/advisor_trace.py`, the `trace` section of
`advisor.rs`) and run at load time over the resolved IR alone. It needs no
new wire field, so hints are available for every existing protocol without
changing its compiled IR-JSON or `content_hash`.

#### 7.10.6 Recommendation lifecycle

Each `adjust` or `hint` result carries an id — `adv-NNNN`, a per-session
counter, deterministic and identical across engines. The id is kept
unchanged while the knob, direction, and proposed value don't change, so
advice does not flicker chunk to chunk.

| Event `kind` | Emitted by | When |
|---|---|---|
| `suggested` | advisor | A new id is first emitted. |
| `superseded` | advisor | A standing id is replaced by a different result at decision steps 4–12, or by a manual control change. |
| `blocked` | advisor | A standing id is replaced by a hold at steps 1–3 (carries the hold reason). |
| `applied` | `apply_advice` | With `by: "clinician"` or `by: "autopilot"`, and the from/to values. |
| `dismissed` | `dismiss_advice` | |
| `changed_manually` | `set_control` | Knob, from, to — only for a control that feeds a reward check or an inhibit. Writing the value a control already has is a no-op: no event, no window restart, no cooldown. |
| `equipment_change` | `mark_equipment_change` | |

Routine `collecting` and `on_track` results are not events; only transitions
between results are logged.

#### 7.10.7 Determinism, and advice is not a tap

Every advice result and every audit event carries `advisor_version`
(currently `"1"`), so a session record shows exactly which built-in defaults
and decision rules produced it, independent of the protocol's own version.

Numbers are rounded to 6 decimal places before serialization
(`r6(x) = floor(x·1e6 + 0.5)/1e6`, mirrored for negatives) and percentages are
shown as `floor(x·100 + 0.5)` — the identical formula in both engines.
Neither engine uses the host language's built-in `round()`, because Python
rounds half-to-even and Rust's `round()` does not; using either directly
would desynchronize the two engines on exact half-way values. Every key of
the advice result described in `docs/EMBEDDING.md` is always present, with
`null` where a field does not apply to the current state — hosts never need
to check for a missing key.

**Advice is not a tap.** `advice()`, `autopilot_policy()`, and
`drain_advice_events()` are dedicated `Evaluator` accessors, not part of the
§7.8 tap API — the tap key-set is pinned by an exact-equality parity test,
and autopilot introduces no new tap keys, the same decision `seed_report()`
made for baseline seeding.

---

## 8. CRED-nf mapping

Every CRED-nf checklist item about what the protocol *computes* maps to a Refrain field; the two items about who a study enrolled are host-side (see the table). A complete protocol generates a CRED-nf-compliant supplement table via `refrain export cred-nf`.

| CRED-nf item | Refrain location |
|---|---|
| Pre/post outcome measures | `meta.outcome_measures` |
| Indication / population | host-side only — the reference library ships no indication or population tags (see `refrain-protocols`) |
| Control / sham condition | `meta.control_ref`, `meta.sham_strategies` (§4.1), plus the sealed-allocation token (§7.9) for studies that ran randomised sham |
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

Implemented in `refrain-core`: a runtime built against IR-JSON `"0.3"` refuses a protocol tagged with any newer schema at load, rather than deserializing it with unrecognized fields silently ignored. Control baseline seeding (§4.9.4) is the feature that first depends on this gate — an older runtime that ignored an unrecognized `seed` field would run the protocol unbaselined, which is worse than refusing to load it.

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

The principle: *the most common operation in practice is "I want this protocol but with a different placement / different threshold / different ORF default."* Named-block default is replace (unambiguous intent when re-declared); for partial override, use `amend`; for deletion, use `remove`.

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

Parent protocols may mark declarations as `final`, preventing child override or removal. `final` applies to **named declarations** (`input`, `derive`, `threshold`, `inhibit`, `custom`) and to **controls** (including `placement` controls).

**Named declarations:**

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

**Controls (including `placement`):**

```refrain
controls {
  site = placement { kind = "active"; default = "F3"; allowed = ["F3"]; final = true }
  //                                                                     ^^^^^^^^^^
  // children cannot redeclare this control; bindings overrides are rejected at resolve time
}
```

A `final` control is:
- Protected from child redeclaration in composition (a compile error if a child redeclares it).
- Protected from `bindings` override at resolve time: `resolve(..., bindings={"site": "Cz"})` raises `ResolveError` when `site` is `final`.

### 11.5 Schema version compatibility

`extends "library/foo@1.2"` requires the parent's schema version (declared in its `meta`) to be `1.x` for some `x ≤ 2` if the child's schema version is `1.x`. Major-version mismatch is an error. Newer-child-on-older-parent within the same major is permitted. Older-child-on-newer-parent within the same major produces a warning ("you may not be using new features available in the parent").

### 11.6 Chained inheritance

Chained inheritance (`A → B → C`) is supported. Multiple inheritance is *not* supported in v0.0; a protocol may extend at most one parent.

When B amends a field of A, and C also amends the same field, C wins (last-amend-wins along the inheritance chain). Children always see their immediate parent's view, with their own overrides applied on top.

---

*This is v0.0r1 strawman. Comments, disagreements, counter-proposals welcome.*
