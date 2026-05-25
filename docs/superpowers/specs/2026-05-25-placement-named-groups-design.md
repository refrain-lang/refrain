# Named `allowed` groups — design

> Status: approved design, ready for an implementation plan.
> Builds on `main` (tag v0.4.0) which shipped the `placement` control type
> (kinds active/bipolar/pair/set) + Mode 2a per-site replication.
> Origin: strawman `coherence-workstation/docs/refrain/PARAMETERIZED_PLACEMENT.md`
> (§63 `allowed = standard_19`, §157 "named groups vs free-form").

## Goal

Let an author name a reusable channel-name list once and reference it from
placement controls, instead of repeating the same channel list across every
`allowed` (and every `set` `default`). The strawman's motivation: "named groups
make the deploy UI cleaner and self-documenting."

Front-end only — groups expand at **resolve time** to the same channel tuples
an author would have written inline, so the IR, IR-JSON schema v0.1, and the
Rust core are all unchanged.

## Scope boundary

In scope: a top-level `groups { … }` block defining named flat channel-name
lists; group-name references usable in two places — a placement control's
`allowed`, and a `set` control's `default`.

Out of scope (deferred): built-in/standard clinical taxonomies (Refrain stays
unopinionated — groups are author-defined only); group references in
`bipolar`/`pair` `allowed` (those are lists of 2-tuples, not flat channel
lists — stay inline); group references in `requires.channels`, montage slots,
or anywhere outside placement; nested groups (a group referencing another
group).

## Decisions (locked in brainstorming)

1. **Author-defined, in-file.** Groups live in a `groups { … }` block in the
   `.refrain` file. Refrain ships no built-in taxonomy — it stays unopinionated
   about clinical montage ontology (consistent with being a description
   language).
2. **Reach = `allowed` + set `default`.** A group name resolves anywhere a
   channel *list* naturally appears in a placement control: as `allowed`, and as
   a `set`'s `default`. Nowhere else.

## Design

### 1. Syntax

A new top-level block, sibling to `controls`/`input`/`derive`/…:

```refrain
groups {
  sensorimotor = ["C3","Cz","C4","CP3","CP4"]
  frontal      = ["F3","Fz","F4"]
}
controls {
  sites = placement {
    kind    = "set"
    allowed = sensorimotor      // group ref (a bare identifier)
    default = sensorimotor      // group ref — start with the whole strip
    min     = 1
    max     = 3
  }
  site = placement { kind = "active"; allowed = frontal; default = "Fz" }
}
```

- Each `groups` entry is `<ident> = [ <string-lit>, … ]` — a non-empty list of
  channel-name string literals.
- A group is referenced by **bare identifier** in `allowed` or a `set`
  `default`. The grammar already distinguishes a bare `NameRef` from a string
  literal and from a list literal, so `allowed = sensorimotor` (NameRef) is
  syntactically distinct from `allowed = ["C3"]` (list) and `allowed = "any"`
  (the any sentinel) — no ambiguity.

### 2. AST

Add an `A.GroupsBlock` (or fold into the existing section-block machinery as a
new section kind) carrying `entries: dict[str, list[A.StringLit]]` plus a
`Loc`. The `File`/protocol body gains an optional `groups` section. A group
reference in `allowed`/`default` is parsed as the existing `A.NameRef` (no new
expression node).

### 3. Resolution — front-end sugar, zero wire impact

In `_resolve_controls`, **before** the existing placement validation runs:

1. Build the group table from the `groups` block (resolved once).
2. When a placement control's `allowed` is a `NameRef` naming a group, expand it
   to the group's channel-name tuple. When a `set` control's `default` is a
   `NameRef` naming a group, expand it to the group's list.
3. Hand the expanded values to the *existing* `_resolve_placement_control` /
   `_bound_placement_value` paths unchanged.

Net effect: `IRControl.allowed` / `default_placement` end up byte-identical to
the inline-list form. **No new `IRControl` field, groups never reach
`ir_to_json_obj`, `IR_JSON_VERSION` stays `0.1`, the drift gate (`check_equivalence.py`)
stays green, the Rust core is untouched.**

The host site-picker recipe (`docs/EMBEDDING.md`) reads the already-expanded
`.allowed`, so it works with no change — groups are purely an authoring
convenience.

### 4. Composition (`extends`)

The `groups` block merges across `extends` like other sections: a child
inherits parent groups and may add new ones or override a parent group by
re-declaring the same name. Reuse `compose.py`'s existing section-merge
mechanism (the same path that merges `controls`); do not add a parallel merge.

### 5. Validation (fail-fast, parallels existing rules)

- **Unknown group:** `allowed`/`default` references a name not in `groups` →
  `ResolveError` ("unknown group 'x'"). (Distinguish from a stray identifier:
  the only valid bare-identifier values for `allowed`/`default` are group names,
  so a non-group name is always this error.)
- **Empty group:** a `groups` entry with `[]` → `ResolveError` (parallels the
  empty-`allowed` rejection already shipped for placement controls).
- **Duplicate channel within a group:** → `ResolveError` ("group 'x' lists 'C3'
  twice") — an authoring mistake, surfaced rather than silently deduped.
- **Name collision:** a group name equal to a control name → `ResolveError`
  (the two share the bare-identifier namespace in value position).
- **Post-expansion checks unchanged:** the existing `allowed ∩ device-capable`
  validation and `set` `min ≤ count ≤ max` (including `default = <group>` whose
  size exceeds `max`) fire exactly as they do for inline lists.

### 6. Reuse (project rule)

Genuinely new: the `groups` grammar block + AST node + the one resolve-time
expansion step in `_resolve_controls`, plus the `groups` case in the compose
section-merge. Everything downstream — placement validation, `IRControl`, the
IR-JSON emitter, host introspection — is unchanged. Groups are deliberately
**not** modeled as controls (controls are clinician-tunable knobs; groups are
static authoring aliases) and **not** a new IR concept.

## Files touched

- `src/refrain/grammar.lark` — `groups` block production; group-ref (`NameRef`)
  permitted as an `allowed`/`default` value.
- `src/refrain/parser.py` — build the `groups` AST node; thread it onto the
  protocol body.
- `src/refrain/ast.py` — `GroupsBlock` node (or new section kind).
- `src/refrain/resolver.py` — group-table build; expand group refs in
  `allowed`/set `default` before placement validation; the four validations.
- `src/refrain/compose.py` — merge `groups` across `extends`.
- `src/refrain/ir.py`, `src/refrain/ir_json.py` — **no change** (verify).
- `docs/SPEC.md` — new `groups` section + cross-ref from the placement §4.9.
- `CHANGELOG.md`; `pyproject.toml` ×2 → **0.5.0**.

## Verification / exit criteria

- TDD: a group expands in `allowed` for an `active` control and a `set` control;
  a group used as a `set` `default`; unknown-group / empty-group /
  duplicate-channel / name-collision each raise `ResolveError`; a group
  inherited and overridden via `extends`; a `default = <group>` whose size
  exceeds `max` is rejected by the existing count check.
- IR-JSON: a protocol using groups produces an IR-JSON identical to the same
  protocol with the lists written inline; `IR_JSON_VERSION` stays `0.1`; the
  drift gate stays green (no wire/Rust change).
- Host introspection: `ir.controls["sites"].allowed` is the expanded channel
  tuple (the EMBEDDING site-picker recipe is unaffected).
- Full `pytest -q` green; versions `0.5.0`.
