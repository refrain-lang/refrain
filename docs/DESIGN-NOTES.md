# Refrain implementation design notes

**Status:** internal — implementation-side scratchpad, distinct from the
public design corpus (`CONCEPT.md`, `SPEC.md`, `TOUR.md`, `PRIMITIVES.md`).
Things in this file move into the spec once we're confident; until then
they live here so future-us can pick up state without re-discovering it.

This file is updated as the implementation progresses. Each section
notes what session surfaced it and what's still open.

---

## 1. Spec gaps the resolver and type checker will hit

Surfaced in Phase 0a (parser). The parser accepts what SPEC.md §3 says is
grammatical and flags the ambiguities below to the resolver layer. Each
needs a spec-level decision before the type checker can ship.

### 1.1 Hyphenated identifiers (SPEC §2.3)

The spec defines identifiers as `[A-Za-z_][A-Za-z0-9_-]*` with the
disambiguation "binary `-` requires whitespace; identifier hyphens do
not." This cannot be expressed in a context-free grammar without a
whitespace-sensitive lexer; Lark's default lexer treats whitespace as
purely a separator.

Every example expresses electrode-position tokens as string literals
(`"T3-T4"`), so the practical surface doesn't exercise the rule.

**Proposed spec revision:** drop hyphens from §2.3. Electrode positions
inside expressions remain string-literal references. If a future need
arises for typed electrode-position tokens (autocomplete, validation),
add a distinct `position_lit` production with its own delimiters.

### 1.2 `custom` declaration syntax (SPEC §4.11)

The §4.11 example shows two pieces of syntax not defined in §3:

```refrain
custom "my_phase_metric" {
  signature = (stream<vector<19> uV>) -> stream<scalar dimensionless>
  budget    = { state_kb: 4, worst_case_us: 50 }
}
```

- The RHS of `signature =` is a bare type expression. §3's expression
  grammar has no type-literal production. `<` and `>` are comparison
  operators, so the parser sees this as a parse error.
- `{ state_kb: 4, worst_case_us: 50 }` uses `:` separators and `,` joiners
  inside a record. §3's block bodies are statement sequences with
  `=`-form assignments separated by `;` or newlines.

**Parser-level resolution (Phase 0a):** accept `signature` as a string
literal and `budget` as a block-expression with `=`-form fields.

**Proposed spec revision:** rewrite §4.11 to use the §3-compatible forms
(`signature = "..."` and `budget = { state_kb = 4 }`) and defer the
type-literal extension to v0.1. The string-literal form is parser-stable
and the type-string is parseable by a tiny secondary grammar in the
resolver. If type literals as first-class expressions become important
(e.g. to support `match` on a stream type), add them to v0.1 with their
own `type_literal` production.

### 1.3 Negative numeric literals

SPEC §3 has no unary minus production. TOUR §7's LZT sketch uses
`range: (-1, 1)`, but TOUR §7 is itself flagged in SPEC §10 as
incomplete.

The parser does not accept negative numeric literals. Workaround in
tests: use positive ranges.

**Proposed spec revision:** add unary minus to §3's expression grammar
with the same `-` token. The Earley parser handles the resulting
`(- expr | expr)` ambiguity via context: in `(a - 1)`, the `-` is a
binary operator; in `(-1, 1)` (after `(`, `,` or `=`), it's unary.

### 1.4 `block_expr = block` vs the named form

§3 EBNF: `block_expr = block` (anonymous record). Every example uses
the named form: `phase { ... }`, `frequency { ... }`, `voltage { ... }`,
etc. The parser accepts both with `BlockExpr.name: str | None`.

**Proposed spec revision:** update §3 EBNF to
`block_expr = identifier? block`. The name (when present) is a typeishtag
the resolver uses for dispatch (`phase`, `frequency`, control-type tags).

### 1.5 `session.schedule` is named but not defined

SPEC §8's CRED-nf mapping table references `session.schedule` for
"Number of sessions," but §3 and §4.10 only define `session.phases`.

**Proposed spec revision:** either add a `session.schedule` field shape
to §4.10 (proposed: a structured record with `total_sessions`, `cadence`,
`break_weeks`, etc.) or remove the CRED-nf row and acknowledge sessions-
per-week is reported in `meta.session_protocol_summary` as free text.

### 1.6 Inside-block statement filtering

§3 EBNF lets any `statement` appear inside any `block`. The parser does
not filter, so `meta { input "X" { ... } }` parses (semantically nonsense).

**Resolver responsibility:** enforce that:
- `meta`, `requires`, `reward`, `output`, `controls`, `session` contain
  only assignments (and possibly typed-block expressions in `controls`).
- Top-level protocol body allows any of: section blocks, named decls,
  amends, removes.
- `input`, `derive`, `threshold`, `inhibit`, `custom` blocks contain
  only assignments.

**Proposed spec revision:** tighten §3 EBNF to enumerate which
statements are legal in which contexts, OR document the contextual
restrictions in each §4.* subsection (less rigorous but more readable).

### 1.7 `final = true` as a field, not a modifier

SPEC §11.4 says "Parent protocols may mark declarations as `final`,
preventing child override or removal" but the example shows `final =
true` as a body field. The parser treats it as an `Assignment`.

This is unambiguous as long as the resolver knows to look for the
`final` field on each named decl. Document explicitly in §11.4 that
`final` is a body-level reserved field name, not a syntactic modifier.

---

## 2. Amp profile JSON schema (Session 2)

The resolver validates a protocol's `requires` block against a connected
amplifier. The amp's capabilities arrive as a JSON document the resolver
consumes. The schema needs to cover what §4.2 references, plus what
runtime engines will need at validation time.

### 2.1 Draft schema shape

```json
{
  "schema": "refrain-amp-profile/v0",
  "model": "neurofield-q21",
  "vendor": "Neurofield",
  "firmware": "2024.03.1",

  "coupling": ["dc", "ac"],
  "sample_rates_hz": [256, 512, 1024, 2048],
  "channels": [
    {"name": "Fp1", "type": "eeg"},
    {"name": "T3",  "type": "eeg"},
    {"name": "Cz",  "type": "eeg"},
    ...
    {"name": "A1",  "type": "reference"},
    {"name": "A2",  "type": "reference"}
  ],
  "supports_impedance_check": true,
  "supports_markers": true,
  "max_simultaneous_channels": 21,
  "adc_bits": 24,
  "input_range_uv": 374000,

  "runtime_limits": {
    "max_protocol_state_kb": 256,
    "max_worst_case_us_per_step": 1000
  }
}
```

### 2.2 Open questions

- **Vendor-supplied vs community-maintained.** CONCEPT.md flags this as
  governance, not technical. Initial implementation: ship Q21 + OpenBCI
  + BrainProducts profiles in `refrain/amp_profiles/` as JSON, document
  how vendors contribute.
- **`linked_ears` and `common_average` virtual references.** These are
  computed from physical channels at runtime; not amp capabilities.
  Don't put them in the profile — the resolver knows about them from
  PRIMITIVES.md's `referential` semantics.
- **Sample-rate ranges vs enumeration.** Real amps support discrete
  rates, but a protocol's `sample_rate = ">= 256 Hz"` is a comparison.
  The resolver picks the highest rate in `sample_rates_hz` that
  satisfies the comparison.
- **Channel name normalisation.** "T3" (old 10-20) vs "T7" (modern
  10-10) refer to the same physical location. Should profiles list both
  names, or should the resolver canonicalize? Lean toward the profile
  exposing the amp's actual labels and the resolver matching with a
  configurable alias table.

### 2.3 Spec impact

§4.2 `requires` should reference this schema explicitly:

> The protocol's `requires` block is matched against an amp-profile JSON
> document conforming to `refrain-amp-profile/v0` (see
> `docs/AMP-PROFILE-SCHEMA.md`). Each `requires` field has a documented
> comparison semantics against a profile field.

This file (`AMP-PROFILE-SCHEMA.md`) does not yet exist; create it in
Session 2 alongside the resolver.

---

## 3. Primitive type-signature registry (Session 2)

Each primitive in `PRIMITIVES.md` needs a machine-readable signature
the resolver can type-check calls against. The registry maps the
primitive name to its parameter shape, defaults, and output-stream
contract.

### 3.1 Sketch

```python
# In refrain.primitives (Session 2)
@register("bandpass")
class BandpassSig:
    """SPEC §4.4 / PRIMITIVES.md 'bandpass'."""
    parametrisations = [
        # band: (low_Hz, high_Hz), order: int
        Sig(
            inputs=(Stream(Scalar(uV)),),
            params={
                "band": Tuple(Number(Hz), Number(Hz)),
                "order": Number(dimensionless, default=4),
            },
            output=Stream(Scalar(uV)),
        ),
        # center: Hz, bandwidth: ratio | (low_Hz, high_Hz), order: int
        Sig(
            inputs=(Stream(Scalar(uV)),),
            params={
                "center": Number(Hz),
                "bandwidth": OneOf(RatioConstructor, Tuple(Number(Hz), Number(Hz))),
                "order": Number(dimensionless, default=4),
            },
            output=Stream(Scalar(uV)),
        ),
    ]
    budget = ResourceBudget(state_kb=2, worst_case_us=15)
```

### 3.2 Open questions

- **Overloads.** `bandpass` has two parametrisations; the resolver picks
  by named-arg presence (`band=` vs `center=`). Need a clean disambiguation
  rule when both could match.
- **Unit composition.** `differentiate()` returns `T/s` for any input
  unit `T`. The signature language needs a way to express "preserve
  input unit but multiply by 1/s." `square(uV) -> uV2` is similar.
- **Variadic primitives.** `all_of([cond, cond, ...])` takes an array
  of variable length.
- **Threshold-type "constructors"** (`absolute`, `percentile`, `dynamic`):
  these aren't primitives in the dataflow sense; they're values consumed
  by `threshold` blocks. Probably a separate registry.
- **Inhibit-action constructors** (`mute`, `freeze`, `flag`) similar.

### 3.3 Spec impact

PRIMITIVES.md currently uses informal signature notation
(`bandpass(band: (low_Hz, high_Hz), order: int = 4) -> stream<scalar uV>`).
The registry-Python form should round-trip to this notation for docs.
No spec change required, but PRIMITIVES.md should explicitly note that
the canonical signature lives in the registry and the prose is rendered.

---

## 4. IR sketch (Session 2)

The resolver emits an IR: a fully-resolved, fully-typed dataflow graph
ready for the evaluator. Shape (early draft):

```python
@dataclass(frozen=True, slots=True)
class IRStream:
    """A typed stream node in the dataflow graph."""
    name: str                      # canonical: "input/raw", "derive/smr_envelope", ...
    type: StreamType               # rate, value type, units
    producer: IRProducer           # input, primitive call, expression
    consumers: tuple[str, ...]     # names of streams depending on this one

@dataclass(frozen=True, slots=True)
class IRProtocol:
    name: str
    meta: dict[str, IRValue]
    requires: dict[str, IRValue]
    streams: dict[str, IRStream]   # all named streams, topologically ordered
    reward: IRReward
    output: dict[str, IRExpr]      # channel -> expression
    controls: dict[str, IRControl]
    session: IRSession
    resource_budget: ResourceBudget  # computed from primitives
```

### 4.1 Open questions

- **Should formula derives stay as expression trees, or be flattened
  to a sequence of intermediate streams?** Expression trees are more
  compact and preserve author intent; flattening makes the evaluator
  uniform. Lean toward expression trees with the evaluator handling
  them recursively.
- **Where do `controls` references resolve?** A control like `orf` is
  a runtime-mutable scalar. References to it (in `bandpass(center: orf)`)
  resolve to a special `IRControlRef` node that the evaluator dereferences
  per step.
- **Inhibit gating model.** §7.4 says inhibits modify *what reaches the
  patient* at the output stage, not reward values. So the IR's reward
  node is unconditioned and each output binding gets implicit
  gate-by-inhibits wrapping. Document this explicitly in IR.
- **`event_stream` representation.** SPEC §10 open question 9 asks
  whether both consumption modes (rising-edge event + `.holds` boolean)
  should be opt-in. Lean toward both-always: it costs one extra boolean
  per event-stream, runtime cost is negligible. Document opinion in IR;
  let spec follow.

---

## 4a. Resolver / type checker — Phase 0b complete (what landed)

Implemented in `src/refrain/resolver.py`, `src/refrain/types_.py`,
`src/refrain/primitives.py`, `src/refrain/ir.py`,
`src/refrain/ir_print.py`. Two amp profiles shipped: Q21 and
OpenBCI Cyton.

What works:
- Reference resolution (SPEC §5.4): string-lits in expression position
  classified as `IRStreamRef` / `IRThresholdRef` / `IRStringLit`
  contextually.
- NameRef resolution: bare identifiers like `orf` resolve to
  `IRControlRef`, with `controls` pre-hoisted before named decls so
  Othmer ILF's `bandpass(center: orf, ...)` works.
- Acyclicity via strict source-order processing.
- Primitive call type-checking against the signature registry; unit
  composition (uV/s, dimensionless, etc.) flows through `differentiate`,
  `magnitude`, division, etc.
- Hardware validation: channel existence, coupling support, sample-rate
  selection (highest-supported-rate-at-least-protocol-minimum).
- Resource budget summed across primitives.
- `IRReward` enforces at-least-one-of-{continuous, event}; reward.event
  must be event_stream.

Deferred (called out at resolve time with a friendly diagnostic):
- Composition: `extends` / `amend` / `remove` / `final`. Library path
  resolution is the bigger half of this; it needs design.
- Rate alignment (SPEC §6.6). No three-example protocol triggers a
  rate mismatch; landing this needs every primitive's rate behaviour
  encoded in the registry.

Spec ambiguities resolved while building this:
- **Controls pre-hoisting** (NEW spec note). SPEC §5.4 says
  string-literal references must be "previously declared." NameRefs
  (bare identifiers — used for control names) are a separate
  expression form per §3 EBNF, not constrained to source order. The
  Othmer ILF example exercises this: `derive "band" {...
  bandpass(center: orf, ...) ...}` references `orf` which is declared
  in a `controls` block later in source. The resolver pre-hoists
  controls before named decls. **Recommended spec note in §5.4:** add
  a sentence clarifying that NameRefs to controls are protocol-global
  and not subject to the "previously declared" rule.
- **`session.schedule`** is named in §8 (CRED-nf mapping table) for
  "Number of sessions" but not defined in §3 or §4.10. The CRED-nf
  export now pulls from `meta.session_count` instead, falling back to
  `[NOT SPECIFIED]`. **Recommended spec change:** either define
  `session.schedule` in §4.10 with a concrete shape, or relabel the
  CRED-nf row to point at `meta.session_count` (free text).

---

## 4b. Composition — Phase 0c complete (what landed)

Implemented in `src/refrain/compose.py`. The composer runs as an AST
pass before the resolver: it walks the `extends` chain, recursively
loads parents, applies the SPEC §11 semantics, and emits a merged AST
that the existing resolver consumes unchanged.

**Merge rules implemented per SPEC §11.1:**

| Parent / child collision | Behaviour |
|---|---|
| `meta.<field>` | Field-level merge: child overrides same-named parent fields; unmentioned inherit. |
| `requires.<field>` | Same field-level merge. |
| `controls.<name>` | Same field-level merge (control names are the field keys). |
| `reward`, `output`, `session` | Child replaces parent wholesale. |
| Named decls `input "X"` / `derive "X"` / etc. | Child re-declaration replaces parent's same-named block. |
| `amend section { ... }` | Field-level merge into the named section. |
| `amend kw "X" { ... }` | Field-level merge into the named decl. |
| `remove kw "X"` | Deletes the parent's named decl. |
| `final = true` in parent body | Blocks child amend / remove / redeclaration of that decl. |

**Ordering invariant**: parent's body items stay in their original
positions; child replacements / amends happen in-place at the parent's
position; new child decls append at the end. This preserves the SPEC
§5.4 source-order rule the resolver relies on for reference resolution.

**Chained inheritance** (A → B → C): supported via recursive
composition with cycle detection on the ref chain.

**Loader API**: composition is loader-agnostic. The `ParentLoader`
protocol is a callable `(ref: str) -> File`; tests pass an in-memory
map, the CLI passes `filesystem_loader([library_dirs...])`.

### Library-path convention (NEW — proposing for spec)

SPEC §11 references parent protocols by string like
`"library/othmer/ilf_base@1.2"` but doesn't specify the on-disk
mapping. Phase 0c picks the following convention:

1. The ref splits as `(path, version)` on the final `@`. `library/foo/bar@1.2` → path `library/foo/bar`, version `1.2`.
2. The path component is interpreted as a literal filesystem subpath under each search root: `<root>/library/foo/bar.refrain`.
3. The `library/` prefix in the ref is part of the path (not stripped). This lets protocol packs live under a `library/` subdirectory by convention; first-party packs live alongside `examples/`.
4. Search path is built from `--library DIR` CLI args (repeatable, leftmost wins) plus the `REFRAIN_LIBRARY_PATH` env var (`:`-separated, like `PATH`).

**Recommended spec note in §11**: codify this convention so other
runtimes interoperate. Add a short subsection: *"A protocol reference
`<path>@<version>` resolves to the first existing
`<library_root>/<path>.refrain` along the implementation's library
search path. Implementations should support a configurable search
path."*

### Schema-version handling (light-touch)

SPEC §11.5 frames version compatibility in terms of *schema version*
(language version), distinct from `meta.version` (protocol version).
SPEC §9.2 says schema version lives in a "file header convention" or
`meta.schema_version`. None of the three example protocols declare a
schema version, and the §11.5 rule ("major version match, minor must
be <= the ref constraint") is underspecified.

Phase 0c does the pragmatic 80%: it parses `@<version>` from the ref,
reads `meta.version` from the composed parent, and rejects on major-
number mismatch. Minor-version compatibility, schema-version
declarations, and warnings for older-child-on-newer-parent are all
deferred.

**Recommended spec revisions**:
- Decide whether `@<version>` refers to schema or protocol version. The example string `"library/othmer/ilf_base@1.2"` is more naturally read as "version 1.2 of this specific protocol pack."
- Mandate one declaration site (probably `meta.schema_version`) and require it on every protocol that aims to be portable.

---

## 4c. Evaluator + primitive library — Phase 0d complete (what landed)

Implemented in:
- `src/refrain/types_.py` (extended), `src/refrain/primitives.py` (extended)
- `src/refrain/primitive_impls.py` (NEW) — streaming implementations
- `src/refrain/sources.py` (NEW) — FIF/EDF/XDF/Synthetic source ABC
- `src/refrain/synthetic.py` (NEW) — pink-noise EEG + scheduled bursts
- `src/refrain/eval_.py` (NEW) — IR → event-stream walker
- `src/refrain/cli.py` (extended) — `refrain run` subcommand

The evaluator runs SMR Cz end-to-end against both synthetic and real
(`data/CRJA_20240228_EO.xdf`) EEG, with reward events firing during
SMR-enhanced segments and no NaN / crash on a 5-minute real EO baseline.

### `kind:` parameter for filter families (NEW — proposing for v0.1 spec)

`bandpass(..., kind: "butterworth" | "bessel" | "chebyshev2", attenuation_db: 40)`

The `kind` argument is additive on existing primitive signatures and
backward-compatible (defaults to `"butterworth"`). Cheby II takes an
optional `attenuation_db` (default 40 dB) for its stopband floor.
Cheby I and Elliptic are deliberately excluded because their passband
ripple corrupts amplitude estimates — actively wrong for envelope-based
NF.

`hilbert(..., kind: "fir" | "iir_allpass", taps: 65)` — same shape.

**Recommended spec addition for v0.1**:
- §4.4 / PRIMITIVES.md: document `kind:` and the permitted values.
- Mandate Butterworth as default so existing protocols load unchanged.
- Add Bessel for phase-fidelity-sensitive research NF; add Cheby II for
  protocols that need tight band separation.
- Explicitly call out that Cheby I and Elliptic are not part of the
  recommended NF filter set (rationale in PRIMITIVES.md).

The resolver validates `kind` values against the permitted set
(`refrain.primitives.BANDPASS_KINDS`, `HILBERT_KINDS`) at static-check
time. Typos like `"butterwroth"` or out-of-set choices like `"elliptic"`
fail with a clear diagnostic before the evaluator even instantiates the
filter.

### What's implemented in the evaluator

Acquisition: `bipolar`, `referential` (with `linked_ears` falling back
to common-average when ear channels aren't in the source).

Spectral: `bandpass` (all three kinds; both edge-frequency and
center/bandwidth parametrisations), `hilbert` (FIR; IIR all-pass is
grammar-accepted but raises `NotImplementedError` at evaluator
instantiation — future runtimes can supply it without spec churn).

Time-series math: `magnitude`, `rectify`, `smooth` (one-pole IIR with
α derived from τ and rate), `differentiate` (centered finite differences).

Statistics: `percentile` (windowed `numpy.percentile`; the P² online
algorithm is a future optimization), `auto_range` (rolling-percentile
normalisation to [0, 1]).

Conditions / events: `above`, `below`, `inside`, `all_of`, `any_of`,
`dwell` (state machine producing both rising-edge events and `.holds`
boolean view from one underlying counter).

Mappings: `sigmoid`, `linear`. Inhibit actions: `mute` (with release
hangover), `freeze`, `flag`. `bandpower` (windowed RMS over a
Butterworth-filtered band).

### What's NOT in the Phase 0d evaluator

- **Vector primitives** (`pct_in_range`, `weighted_sum`) — needed for
  LZT-class protocols; lands when vector-stream syntax in §10 is
  resolved.
- **Norms providers** (`norms.power_db.*`, `client.*`) — research IP
  boundary; runtime-supplied assets.
- **Custom Python primitives** (SPEC §4.11) — the language admits them,
  the IR represents them, but the evaluator's dynamic-import + signature-
  validation harness is deferred to Phase 0e.
- **`hilbert(kind: "iir_allpass")`** — the language accepts it; the
  evaluator raises `NotImplementedError`. Documented; future fix.

### Synthetic validation methodology

`tests/test_eval_validation.py` synthesizes 60 s of pink-noise EEG
with controllable SMR-band bursts at known timestamps, runs SMR Cz,
and asserts:

  1. No NaN at any sample.
  2. `audio_gain` in [0, 1] throughout (SPEC §7.6 clamping).
  3. Mean `audio_gain` during burst windows exceeds quiet windows.
  4. At least one `audio_chime` event fires during the burst windows
     (precise count is loose because dwell + adaptive thresholds make
     per-burst counts noisy).
  5. Most chime events occur in or near (within 1 s of) a burst window.
  6. Baseline pink noise without bursts produces a sane (low) chime
     rate — not zero (the adaptive 70th-percentile threshold means the
     condition is met some of the time even on noise) but not runaway.

Also runs each filter `kind` end-to-end on an SMR-like protocol so a
typo or numerical issue in any family surfaces at test time.

### Real-data smoke test

`data/CRJA_20240228_EO.xdf` — a 5-minute 19-channel Q21 eyes-open
recording. The validation tests it for non-crash, no NaN, plausible
chime rate (0–5 events/sec on EO baseline). This is not clinical
validation (which would need ground-truth events from existing software
to compare against) — that's Phase 0e.

### Things spec needs to grow

- **`kind:` parameter** — codify per the above.
- **Inhibit-output gating** — SPEC §7.4 is clear that inhibits modify
  *output values* and not *reward values*. The evaluator implements
  this; recommend adding a worked example to TOUR §2 showing the
  distinction (a downstream derive that consumes `reward.continuous`
  sees the un-gated value, deliberately).
- **`reward.event` semantics** — what's a "rising-edge event" precisely?
  Phase 0d implements it as "boolean true on the sample where streak
  first reaches dwell_samples." Documenting this explicitly in §5.6
  would help.
- **Synthetic source standardization** — the synthetic test generator
  is implementation-internal. If multiple runtimes want shared
  validation, we'd need a small standard for "synthetic test signal X"
  (frequency / amplitude / scheduling) that they all agree on.

---

## 4d. Embedding API — Phase 0e-a complete (what landed)

The evaluator can now be embedded in a host application (recorder,
LSL relay, BCI middleware) without going through a Source. Three
additions:

  - **`Evaluator.live(ir, sample_rate_hz, channel_names)`** — push-mode
    factory. The host owns acquisition, hands chunks in via
    `step_chunk(chunk)`, receives events back synchronously. No Source
    object is constructed.
  - **Lifecycle state machine** (SPEC §7.1): `ready → warmup → run →
    stopped`. `start()` advances to warmup (or to run if the protocol
    has no muted phase, or via `skip_warmup=True` for tests). During
    `warmup`, primitive state still updates (filters settle, percentile
    windows populate) but output events are suppressed so the patient
    doesn't hear settling artifacts. After enough samples have been
    pushed to satisfy the protocol's first muted phase, the evaluator
    transitions to `run` and starts emitting.
  - **`set_control(name, value)`** with warm-restart. Phase 0e-a
    supports `target_pct` on PercentileImpl, `tau` on SmoothImpl, and
    `midpoint` on SigmoidImpl. Filter-coefficient recompute for
    BandpassImpl (the Othmer ORF case) is deferred to Phase 0e-c.

A `BrainBit Flex` amp profile ships at
`amp_profiles/brainbit_flex.json` (4 user-placeable scalp electrodes
+ dedicated hardware reference + ground; 250 Hz; AC-coupled; no
impedance check). The matching example protocol is
`examples/smr_cz_brainbit.refrain` — SMR Cz with a monopolar Cz
montage via `reference: "device"`.

### `reference: "device"` (NEW v0.1 spec proposal)

The `referential` montage's `reference` argument gains a third magic
value beyond `"linked_ears"` and `"common_average"`:

  `reference: "device"` — use the active channel as-recorded; the
  amp's hardware reference is already baked into the channel value, so
  Refrain applies no software re-referencing.

This is the right model for amps like BrainBit Flex, OpenBCI Cyton's
built-in reference, and any other device that delivers channels
already referenced to a dedicated hardware electrode. Without it,
users would have to either fake an A1/A2 placement on user-placeable
electrodes (wasting a channel) or rely on the `linked_ears` →
`common_average` fallback (which is wrong for 4-channel amps where
common-average isn't a meaningful reference).

**Recommended spec addition**: PRIMITIVES.md acquisition section
extends `referential(reference: ...)` to accept `"device"`. The
semantic note: "use when the amp delivers channels already referenced
to a dedicated hardware electrode; no software re-referencing is
applied."

### Bug fix that surfaced during embedding work

The existing `examples/smr_cz.refrain` declared
`smr_target_pct` / `theta_target_pct` controls but never used them —
the percentile thresholds had literal `70` / `30` baked in. Fixed:
thresholds now reference the controls, so `evaluator.set_control(
"smr_target_pct", 65)` actually changes behaviour live. The
BrainBit-tailored example follows the same wiring.

---

## 4e. Host-introspection tap API — Phase 0e-b complete (what landed)

`Evaluator.last_taps() -> dict[str, float | bool]` exposes the per-chunk
last-sample values of internal stream computations so a host application
can plot a clinician observation window (envelope traces, threshold
lines, dwell sub-conditions, pre-gating reward.continuous, post-gating
output).

Triggered by host-side feedback: the Coherence Recorder had three UI
placeholders waiting for this — `⚠ NEEDS REFRAIN`. The four asks were
envelope-per-derive, current adaptive threshold values, boolean dwell
components, and pre-gating `reward.continuous`. Shipped that set plus
two adds the Plan-agent design review recommended:

- **`muted` combined inhibit-gate** — convenient single boolean for "is
  the patient currently being muted by any inhibit"
- **`output/<channel>` post-gating values** — lets the clinician
  compare "what the patient actually heard" vs "what they would have
  heard absent the inhibit"

### Key design choices

- **Tap capture lands BEFORE the warmup output-suppression early-return.**
  Hosts plotting a warmup observation window need envelope/threshold
  values during the 90-second warmup phase. Tap values populate
  identically in `warmup` and `run` states; the only thing the warmup
  branch suppresses is patient-facing event emission.
- **Uniform `reward/condition[i]` naming.** Single-condition dwells
  emit `reward/condition[0]` so the host's iteration loop is
  `for i = 0..` until a missing key. Avoids a special case.
- **`reward/event` is `.any()` per chunk**, not last-sample. The
  regular Event stream from `step_chunk` is the source of truth for
  edge timing; this tap is a status indicator ("anything fired in
  this chunk").
- **Returns a copy.** `last_taps()` returns `dict(self._last_taps)` so
  the host can persist or aggregate snapshots without state
  interference from the next `step_chunk`.
- **Sub-condition discovery is localised in `_eval_reward_event`.** No
  general expression-eval hook; the existing path already classifies
  `dwell`'s condition expression, so the all_of/any_of unrolling
  happens in one place. Handles non-Call sub-conditions (binops,
  refs) naturally via `_eval_expr` recursion.

### `reference: "device"` — already shipped in Phase 0e-a

Worth flagging as still-current advice: amps with a hardware reference
electrode (BrainBit Flex, OpenBCI Cyton with built-in ref) should use
`referential(active: "X", reference: "device")` to consume channels
as-recorded.

### Recommended spec entries

§7.8 "Tap API" was added to SPEC.md establishing the runtime-SHOULD-
expose contract and the canonical naming scheme. EMBEDDING.md gained a
full "Introspection: live taps" section with code example for hosts.

This unblocks the Coherence Recorder's clinician observation window;
the recorder is expected to pin to `refrain==0.0.1` after the tag.

---

## 5. Source location coverage (Phase 0b — done)

What's covered:
- Every parser-produced AST node has a populated `loc: Loc` with
  1-based line and column from Lark.
- `loc` is excluded from equality and repr so round-tripping and test
  ergonomics still work.

What's coarse:
- Inner nodes of left-folded chains (`a + b + c + d`) and member chains
  (`a.b.c.d`) all share the outermost rule's span. Tightening to
  per-node spans is a follow-up if a diagnostic surface needs it.
- `Arg` nodes (positional) get the loc of their wrapped value, not the
  full positional-arg span (which is the same in practice — no leading
  syntax). Named args get the full `name: value` span.
- `paren_expr` and `literal` pass through; the inner node's tighter loc
  wins, which is the right call for diagnostics that point at content.

---

## 6. Things to revisit before v0.1

- **Hyphenated identifiers** — drop or split into `position_lit`.
- **§4.11 `custom`** — pick string-form signatures or commit to a
  type-literal extension.
- **Negative literals** — add unary minus.
- **`block_expr = identifier? block`** — bring §3 in line with usage.
- **`session.schedule`** — define or remove from §8.
- **Statement-context restrictions** — tighten §3 or document per §4.
- **`final` as a body field** — explicit note in §11.4.
- **Vector reduction syntax** (§10 open question 1) — needed for LZT.
- **Event-stream consumption opt-in** (§10 open question 9) — IR
  decision feeds spec.
- **`source_project` semantics** (§10 open question 2) — needs concrete
  shape before z-score / source-space NF can ship.
- **Reserved-word collisions** (§10 open question 7) — pick a namespace
  separator before custom primitives can shadow stdlib names.
- **Library-path convention** (Phase 0c) — codify the
  `<root>/library/<path>.refrain` mapping in §11.
- **Schema vs protocol version** (§11.5 vs §9) — decide which one
  `@<version>` in extends refers to, and mandate a single declaration
  site.
- **`kind:` parameter on filter primitives** (Phase 0d) — codify
  Butterworth/Bessel/Chebyshev II as the recommended NF set with
  Butterworth as the implicit default; document why Cheby I and
  Elliptic are excluded.
- **`reward.event` rising-edge semantics** — make the "fires on the
  sample where streak first reaches dwell_samples" rule explicit in
  §5.6.
- **`reference: "device"`** (Phase 0e-a) — add to PRIMITIVES.md's
  `referential` documentation as a third magic reference value
  alongside `linked_ears` and `common_average`. Used for amps with a
  dedicated hardware reference electrode.
