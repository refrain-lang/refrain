# Placement Mode 2 (multi-site): coherence pairs + per-site replication — design

> Status: approved design, ready for an implementation plan.
> Builds on `main` (commit 1cc7085, tag v0.3.0) which shipped the `placement`
> control type Modes 1 & 3 (kinds `active` + `bipolar`).
> Origin: strawman `coherence-workstation/docs/refrain/PARAMETERIZED_PLACEMENT.md` (Modes 2a + the coherence-pairs refinement).

## Goal

Finish multi-site parameterized placement: deploy one `.refrain` artifact as a
**coherence-pair** protocol (train the relationship between a coupled pair of
sites) or as a **replicated** protocol (train the same pipeline at *each* of N
sites). Both are bound at **resolve time** and are **front-end only** — the Rust
core and IR-JSON schema v0.1 are unchanged (the bound/unrolled IR uses only
existing node types).

## Scope boundary

In scope: two new `placement` kinds — **`pair`** (coupled two-site pair for
coherence, legs referenced by member access) and **`set`** (N sites) — plus
**Mode 2a** per-site replication via implicit fan-out with an author-selectable
`combine = "all" | "any"`.

Out of scope (deferred to their own cycles): **Mode 2b** (aggregate across a set
via vector streams + reductions — the first IR-JSON v0.2 / Rust-core change);
**independent per-site feedback** (N separate output channels — Mode 2a here
collapses to one combined reward); named `allowed` groups.

## Current state (what we extend)

Shipped on v0.3.0 (`src/refrain/resolver.py`): the `placement` control type with
`kind ∈ {active, bipolar}` (`_resolve_placement_control`, `_control_kind_dims`);
resolve-time binding via `resolve(..., bindings=...)`; `_bound_placement_value`
(final-lock, `allowed ∩ device` validation); `_substitute_placement_args` (rewrites
active-placement NameRefs and `bipolar(pair:)` into concrete montages before
`_resolve_call`); `_parse_channel_list` placement expansion; the IR-JSON emitter
omits `type_kind="placement"` controls. Member access today (`_resolve_member_access`)
only supports `reward.*`. `IRControl` already carries `kind`, `allowed`, `final`,
`default_placement`.

## Design

### 1. `kind="pair"` — coherence pairs

A coupled two-site pair, deployed as one unit, whose legs feed two separate inputs:

```refrain
controls { coh = pair { default = ("F3","F4"); allowed = [("F3","F4"),("C3","C4")]; label = "Coherence pair" } }
input "a" { montage = referential(active: coh.a, reference: "linked_ears") }
input "b" { montage = referential(active: coh.b, reference: "linked_ears") }
derive "c" { formula = coherence("a", "b") }
```

- `pair` is added to the `placement` kind set (alongside active/bipolar). Its
  `default`/`allowed` values are pairs (2-tuples of channel strings); `allowed`
  is a list of pairs or `"any"`. `default_placement` stores the `(a, b)` tuple.
  `_resolve_placement_control` already validates 2-tuple shape for `bipolar`;
  reuse that for `pair`.
- **Leg member access** `coh.a` / `coh.b` (symmetric — coherence has no plus/minus):
  in a montage channel slot, an `A.MemberAccess(base=NameRef→pair-placement,
  member ∈ {"a","b"})` resolves at resolve time to the bound leg's concrete
  channel string. Implement by EXTENDING `_substitute_placement_args` to detect
  this member-access form (in addition to the active-NameRef case) and rewrite
  it to an `A.StringLit`; `_bound_placement_value` returns the `(a,b)` 2-tuple,
  and the substitution picks `[0]` for `.a`, `[1]` for `.b`. (Also extend
  `_resolve_member_access` so a stray `coh.a` outside a montage gives a clear
  error rather than the current "member access only on reward".)
- `requires.channels = [coh]` expands to **both** legs (extend `_parse_channel_list`'s
  placement branch to handle a `pair` → extend with both legs).
- Validation: the bound pair ∈ `allowed`; each leg device-capable; `final` lock
  applies (reuse `_bound_placement_value`).

`bipolar` is unchanged — `pair` (compared) and `bipolar` (subtracted) are distinct
kinds.

### 2. `kind="set"` — a multi-site set

```refrain
controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4","Pz"]; min = 1; max = 4; label = "Training sites" } }
```

- `default` is a list of channel strings; `allowed` is a list of channel strings
  or `"any"`. New optional `min`/`max` integer fields (set-size bounds; default
  `min=1`, `max=` len(allowed) or unbounded). Add `min`/`max` to `IRControl`
  (defaulted) or store in `default_placement`'s sibling — add explicit
  `set_min: int | None`, `set_max: int | None` fields to `IRControl` (defaulted
  None) to keep it simple.
- Bound via `resolve(..., bindings={"sites": ["C3","Cz","C4"]})` (a list).
  `_bound_placement_value` for `set` returns the bound list (tuple of channels);
  validates each ∈ `allowed` ∩ device-capable and `min ≤ count ≤ max`.

### 3. Mode 2a — implicit fan-out (the resolver replication pass)

The author writes an ordinary single-site protocol referencing the `set`
placement in an input montage; binding the set drives replication:

```refrain
input "raw" { montage = referential(active: sites) }      // sites is kind="set"
derive "smr" { from = "raw"; pipeline = [ … ] }
threshold "smr_t" { signal = "smr"; type = percentile(target_pct: 70, window: 2 min) }
reward {
  combine = "all"                                          // NEW field: "all" | "any"
  event = dwell(condition: above("smr","smr_t"), duration: 250 ms)
}
output { audio_chime = reward.event }
```

**Replication algorithm** (new resolver pass, runs when a `set` placement is bound
into an input montage):
1. Identify the **set-bound input** (montage references a `set` placement in a
   channel slot).
2. Compute the **per-site subgraph**: the transitive closure of entities
   downstream of that input — derives, thresholds, and the reward *condition*
   expression — via the existing upstream/dependency tracking.
3. For each bound site `s`: emit a per-site copy of the input
   (`referential(active: s)`) and of every subgraph entity, renamed `<name>@<s>`
   (canonical `input/raw@C3`, `derive/smr@C3`, `threshold/smr_t@C3`), rewriting
   each per-site entity's stream/threshold refs to the matching `@s` names.
4. Build the reward dwell's condition as `all_of`/`any_of` (per `reward.combine`)
   over the N per-site condition expressions. The dwell, reward, and outputs stay
   single (one combined reward → existing single output channels).

The emitted IR is a flat graph — N inputs, N×derives/thresholds, one combined
reward — using only existing node types. The Rust core runs it unchanged, exactly
like the 3-band `realistic_smr`.

**Confirmed scoping decisions:**
- **(a) Continuous reward over a replicated set is rejected.** 2a covers the
  event/condition path. If `reward.continuous` depends on a per-site replicated
  stream, raise a `ResolveError` ("a continuous reward over a replicated `set`
  needs aggregation — see Mode 2b"). (A continuous reward that depends only on
  non-replicated streams is fine.)
- **(b) Ambiguous replication boundary is an error, not a guess.** The per-site
  subgraph is the transitive closure of the set-bound input's dependents. If a
  single derive/threshold would mix a replicated stream with a non-replicated one
  such that it can't be unambiguously assigned to "per-site," raise a
  `ResolveError` rather than inferring. Entities that don't depend on the set
  input (e.g. a fixed-channel inhibit) stay single.

`reward.combine` is a new optional field on the `reward` block (only meaningful
when a `set` placement is replicated; default `"all"`). Parse it in
`_resolve_reward`; reuse the existing `all_of`/`any_of` primitive construction for
the combined condition.

### 4. Naming, taps, CRED-nf

Per-site canonical names `<name>@<site>` flow through to `last_taps()`/events
(string-keyed; the Rust core is agnostic), so a host can plot per-site state. The
bound sites live in the per-site input montages → CRED-nf reports the actual
electrodes used.

### 5. IR / wire / Rust impact — none new

`pair`/`set` controls are resolve-time-only → the IR-JSON emitter omits them
(extend the shipped `type_kind != "placement"` guard — `pair`/`set` are placement
controls, so they're already omitted). The unrolled IR and member-access-resolved
montages use only existing node types → **IR-JSON schema v0.1 and the Rust core
are unchanged; golden vectors + `check_equivalence` stay green.** Package version
→ **0.4.0** (next minor; schema stays 0.1).

### 6. Reuse (project rule)

Extends the shipped placement machinery: `kind` dispatch in
`_resolve_placement_control`/`_control_kind_dims` (add `pair`, `set`),
`_bound_placement_value` (pair → 2-tuple legs, set → validated list),
`_substitute_placement_args` (add member-access for `pair` legs; trigger the
fan-out for `set`), `_parse_channel_list` (pair → both legs, set → all),
`_resolve_member_access` (add `pair` legs), the emitter omit-guard (already covers
placement). The one genuinely-new unit is the **resolver replication pass** for
2a. No Rust-core or wire-format change.

## Files touched

- `src/refrain/grammar.lark` / `parser.py` — `pair`/`set` declarations, `coh.a`
  member access in channel slots, `combine` on the reward block, list/pair
  binding values.
- `src/refrain/resolver.py` — `pair`/`set` kinds in `_resolve_placement_control` +
  `_control_kind_dims`; member-access substitution; `set` fan-out replication
  pass; `reward.combine`; `_bound_placement_value`/`_parse_channel_list`/
  `_resolve_member_access` extensions; validation (`allowed ∩ device`, `min`/`max`,
  scoping errors (a)/(b)).
- `src/refrain/primitives.py` — member-access accepted in montage channel slots
  (if the channel-name ParamSpec needs it).
- `src/refrain/ir.py` — `IRControl.set_min`/`set_max` (defaulted); confirm
  `default_placement` carries pair/list shapes.
- `src/refrain/ir_json.py` — no change expected (placement controls already
  omitted); verify.
- `pyproject.toml` ×2 → `0.4.0`; `CHANGELOG.md`; `docs/SPEC.md` (fold in `pair`/
  `set` kinds, member access, `reward.combine`, replication semantics).

## Verification / exit criteria

- TDD: `pair` declare/resolve + `coh.a`/`coh.b` bind into two coherence inputs
  (default + override); `requires.channels=[coh]` → both legs; `pair` allowed ∩
  device validation. `set` declare/resolve + `min`/`max` + allowed ∩ device.
  Mode 2a: a single-site protocol bound to a 3-site set unrolls into 3 inputs +
  3×derives/thresholds + a `combine="all"` (`all_of`) reward over the 3 per-site
  conditions; `combine="any"` → `any_of`; per-site canonical names `<name>@<site>`;
  outputs driven by the combined reward. Scoping errors: continuous reward over a
  replicated set → ResolveError (a); ambiguous mixed boundary → ResolveError (b).
- IR-JSON: a `set`-bound protocol emits no placement control and produces a flat
  IR shaped like a hand-written N-site protocol; existing golden vectors +
  `check_equivalence` stay green (no wire change). `IR_JSON_VERSION` stays `0.1`.
- Full `pytest -q` green; `cargo test` unaffected/green. Versions `0.4.0`.
- A coherence protocol deploys at different pairs, and an SMR protocol trains at a
  bound set of sites, both purely by binding — resolved IR + CRED-nf report the
  actual electrodes.
