# Parameterized placement — `placement` control type (Modes 1 & 3) — design

> Status: approved design, ready for an implementation plan.
> Origin: strawman `coherence-workstation/docs/refrain/PARAMETERIZED_PLACEMENT.md` (Part A).
> Builds on `main` (commit 1c676ea, release tag v0.2.0).

## Goal

Make electrode **placement a deploy-time binding, not a source edit**: one `.refrain` artifact
("Poise anywhere") that a clinician binds to a site (or a fixed/locked site) when deploying, without
editing the file. Scope here is **Mode 1** (default + override) and **Mode 3** (fixed/`final`), kinds
**`active`** and **`bipolar`**. The binding is resolved entirely in the **front-end (parser/resolver)
at resolve time**, so the Rust core and IR-JSON v0.1 are **unchanged**.

## Scope boundary

In scope: a `placement` control type; resolve-time binding of placement into montage channel slots
and `requires.channels`; `allowed ∩ device` validation; `final` placement controls (locked site);
`kind = "active" | "bipolar"`; coherence via two `active` placements.

Out of scope (deferred): `kind = "set"`; Mode 2a (parametric graph replication); Mode 2b (vector
streams + reductions); named `allowed` groups (`standard_19`, …); live-tunable placement; reference-
electrode parameterization (the reference stays literal in v1; only active/bipolar legs bind). The host
deploy **UI** (strawman Part B) is implemented by host apps — this spec defines the **contract** the
resolver gives them, not the UI.

## Current state (what we extend, not reinvent)

- Control types are gated in `resolver.py::_control_kind_dims` (today: frequency/duration/voltage/
  percent/enum/boolean); declared as `controls { name = <type> { … } }` (generic `SectionBlock` +
  `BlockExpr`), resolved by `_resolve_control` into `ir.py::IRControl`.
- Numeric control *references* in expressions become `IRControlRef` and are substituted at **eval
  time** (so `set_control` can retune). Placement is different: it binds at **resolve time** (frozen
  per session), so it is substituted into the montage *before* IR-JSON emission.
- Montage channel slots (`referential(active:, reference:)`, `bipolar(plus:, minus:)`) and
  `requires.channels` are **string-literal-only** today (`primitives.py` ParamSpec `channel_name` /
  `array_of_strings`; `resolver.py::_parse_channel_list`). No resolve-time channel validation exists
  except the `requires.channels` hardware check (`amp.has_channel`).
- `final` exists for **named decls** (input/derive/threshold/inhibit/custom) via `compose.py`; **not**
  for controls. `IRControl` has no `final` field.

## Design

### 1. The `placement` control type

A 7th control type. Categorical/dimensionless (channel identifiers — no unit arithmetic, not range/log
checked). Declaration:

```refrain
controls {
  site = placement {
    kind         = "active"          // "active" | "bipolar"
    default      = "Cz"              // active: a channel name. bipolar: a pair (T3, T4) = (plus, minus)
    allowed      = ["Cz","C3","C4"]  // explicit list; or "any". bipolar: list of pairs, or "any"
    label        = "Training site"   // optional, for the deploy UI
    live_tunable = false             // default false; v1 forbids true for placement (frozen per session)
    final        = false             // NEW for controls — see §5
  }
}
```

Resolution (`_resolve_control` + a new `_control_kind_dims` entry for `placement`):
- `kind` is required, ∈ {`"active"`, `"bipolar"`}.
- `default` shape depends on `kind`: a string for `active`; a 2-tuple `(plus, minus)` for `bipolar`.
- `allowed` is a list of channel names (active) or a list of 2-tuples (bipolar), or the literal `"any"`.
- `default` must satisfy `allowed` (resolve error otherwise).
- `live_tunable = true` on a placement is a resolve error in v1 (placement is frozen per session).
- Produces an `IRControl` with `type_kind = "placement"`, a new `kind` field, `allowed`, `final`,
  `label`. (Dimensions: a dimensionless/categorical marker.)

### 2. Binding mechanism (resolve-time)

Extend the resolver entry point: `resolve(ast, amp=None, *, bindings: dict[str, <value>] | None = None)`.
For each control, the resolved value is `bindings[name]` if present, else the control's `default`.
A **placement** control's resolved value is then **substituted into the montage channel slots and
`requires.channels`** during resolution, yielding a fully concrete IR (montage names real channels —
identical in shape to a hardcoded-site protocol). Non-placement controls: a `bindings` entry overrides
that control's resolve-time default (live-tunable numeric controls still retune at runtime as today).

Validation is **fail-fast at resolve time**, with precise `ResolveError`s (never silently substitute a
nearby channel):
- bound value(s) must be ∈ `allowed` (unless `"any"`),
- and ∈ device-capable when an `amp`/profile is supplied (`amp.has_channel`) — reusing the existing
  `requires.channels` hardware check, now fed by placement-derived channels,
- a `bindings` entry for a `final` placement control is rejected (locked — see §5).

### 3. How montages / `requires` reference a placement

Channel-name slots gain the ability to reference a placement control (a bare `NameRef` resolving to a
placement control), in addition to a string literal:

- **`active`** → `referential(active: site, reference: "linked_ears")`. Also usable in either leg of a
  literal `bipolar(plus: a, minus: b)` and as a coherence input's active. Resolves to one concrete
  channel name.
- **`bipolar` (coupled pair as one deploy unit)** → a new montage form **`bipolar(pair: site)`** where
  `site` is a `kind="bipolar"` placement; the resolver expands it to the montage's plus/minus from the
  bound `(plus, minus)`. The existing literal `bipolar(plus:, minus:)` form is unchanged. (Ad-hoc
  bipolar of two independent sites is still expressible as two `active` placements:
  `bipolar(plus: a, minus: b)`.)
- **`requires { channels = [site] }`** → `_parse_channel_list` / `_resolve_requires` accept placement-
  control references and expand them to the bound channel(s): one for `active`, both legs for
  `bipolar`. This drives the hardware check.

**Coherence** needs no new construct: two `active` placements feed two inputs, compared by
`coherence("a","b")` downstream (worked example below).

### 4. `final` on controls (Mode 3)

Add `final: bool` to `IRControl`, parsed in `_resolve_control`. Semantics:
- A `final` placement control **cannot be overridden by a `bindings` value** — it resolves to its
  `default` (the "visible but uneditable" locked site); an override attempt is a `ResolveError`.
- For consistency with named-decl `final`, extend `compose.py` so a child protocol cannot
  amend/remove/redeclare a `final` control. (This is a small, separable sub-task — controls are not
  `NamedDecl`s today, so it is a distinct code path from `_has_final_true`.)
- A literal montage (`referential(active: "F3", …)`) remains the zero-mechanism way to fix a site; the
  `final` control adds the deploy-visible-but-locked UX on top.

### 5. IR / wire / Rust-core impact — none new

Placement controls are **resolve-time-only**: once bound, the montage carries concrete channels.
Therefore the IR-JSON emitter **omits `type_kind="placement"` controls** (`ir_json.py::_emit_control`
skips them) — the emitted `controls` section keeps only runtime (numeric/live) controls. Consequences:
- **IR-JSON schema stays v0.1**, `refrain_ir_version` stays `"0.1"`, the golden vectors and
  `check_equivalence` are unaffected (verify: a placement-bound protocol's emitted IR-JSON is
  byte-shaped like its hardcoded-site equivalent).
- **Rust core unchanged.** `IRControl.final` lives on the Python IR only (never on the wire).

### 6. CRED-nf

The **bound** sites appear in the resolved montage + `requires.channels`, so the generated CRED-nf
supplement reports the actual electrode locations, not the parameter name. The host stores the
immutable `(protocol, bindings)` pair; the `.refrain` artifact is never edited (strawman B.4/B.5).

### 7. Host contract (Part B is host-implemented)

The resolved `IRProtocol` exposes per-control metadata (`type_kind`, `kind`, `allowed`, `default`,
`final`, `label`) so a host can render the correct picker (single-site / bipolar-pair) and pre-validate.
**Python hosts** (Coherence Workstation, Coherence Recorder) bind directly via
`resolve(..., bindings=...)`. **Mobile hosts** (no parser/resolver) consume **pre-bound IR-JSON
assets** — binding is performed upstream by a Python build/deploy step (analogous to the existing
per-sample-rate asset pattern). This spec does not implement mobile deploy; it is flagged as a host
concern.

### 8. Versioning

This is an additive minor language feature. Bump the package version `pyproject.toml` (both `refrain`
and `refrain-core`) from the current `0.1.0` to **`0.3.0`** — reconciling the existing drift (the
`v0.2.0` git tag was cut at commit 4e7cfa8 without a pyproject bump; v0.2.0 is the current tagged state,
so placement is the next minor, 0.3.0). The **IR-JSON schema version is unaffected** (stays `0.1`).
Add a `CHANGELOG.md` entry.

## Worked examples

**Mode 1 (Poise, single active site):**
```refrain
controls { site = placement { kind="active"; default="Cz"; allowed=["Cz","C3","C4","Pz"]; label="Training site" } }
requires { channels = [site]; sample_rate = ">= 256 Hz" }
input "raw" { montage = referential(active: site, reference: "linked_ears") }
// derives/thresholds/reward unchanged …
```
Deploy at C3 by `resolve(ast, amp, bindings={"site": "C3"})`.

**Coherence (two active placements):**
```refrain
controls {
  site_a = placement { kind="active"; default="C3"; allowed=["C3","F3","P3"] }
  site_b = placement { kind="active"; default="C4"; allowed=["C4","F4","P4"] }
}
requires { channels = [site_a, site_b] }
input "a" { montage = referential(active: site_a, reference: "linked_ears") }
input "b" { montage = referential(active: site_b, reference: "linked_ears") }
derive "coh" { formula = coherence("a", "b") }
```

**Mode 3 (fixed, locked) — both forms:**
```refrain
// literal (zero mechanism):
input "raw" { montage = referential(active: "F3", reference: "linked_ears") }
// or a deploy-visible-but-locked control:
controls { site = placement { kind="active"; default="F3"; allowed=["F3"]; final = true } }
```

**Bipolar (coupled pair, e.g. Othmer ILF):**
```refrain
controls { site = placement { kind="bipolar"; default=("T3","T4"); allowed=[("T3","T4"),("C3","C4")] } }
requires { channels = [site] }
input "raw" { montage = bipolar(pair: site) }
```

## Files touched

- `src/refrain/grammar.lark` / `parser.py` — placement block; placement (NameRef) refs allowed in
  channel-name slots and in `requires.channels`; the `bipolar(pair:)` montage form; pair literals
  `(plus, minus)` in placement `default`/`allowed`.
- `src/refrain/resolver.py` — `placement` in `_control_kind_dims`; `_resolve_control` (kind/allowed/
  final/live_tunable rules); `bindings` param + resolve-time placement substitution into montages and
  `requires.channels`; `allowed ∩ device` validation; `final` lock.
- `src/refrain/primitives.py` — montage specs accept a placement reference in channel slots; the
  `bipolar(pair:)` form.
- `src/refrain/ir.py` — `IRControl.kind`, `IRControl.allowed`, `IRControl.final`.
- `src/refrain/compose.py` — `final` protection for controls.
- `src/refrain/ir_json.py` — `_emit_control` omits `type_kind="placement"` controls.
- `pyproject.toml` (×2) — version → `0.3.0`; `CHANGELOG.md` entry.
- `docs/SPEC.md` — fold in the `placement` control type (§4.9), placement refs in montage/`requires`
  (§4.2/§4.3), `final` on controls (§11.4).

## Verification / exit criteria

- TDD: placement parse/resolve (active + bipolar); binding substitution into montage + `requires`
  produces correct concrete channels; `allowed ∩ device` validation (pass + fail-fast); `default ∉
  allowed` → error; `final` override rejected; `live_tunable=true` on placement → error; the
  two-active coherence pattern resolves; `bipolar(pair:)` expands to plus/minus.
- IR-JSON: a placement-bound protocol emits no `placement` control and is byte-shaped like its
  hardcoded-site equivalent; the existing golden vectors + `check_equivalence` stay green (no wire
  change).
- Full `pytest -q` green; `cargo test` unaffected/green.
- Independent-validation bar: a clinician can deploy the same artifact at different sites purely by
  binding, and the resolved IR + CRED-nf report the actual electrodes.
