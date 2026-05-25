# Named `allowed` groups — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an author declare a `groups { name = [channels] }` block and reference a group by name in a placement control's `allowed` and a `set` control's `default`.

**Architecture:** Front-end-only resolve-time sugar. `groups` is a new section keyword reusing the generic `SectionBlock`/`Assignment` AST (parser + AST unchanged). The resolver builds a validated group table and expands a group `NameRef` in `allowed`/set-`default` to an `A.Array` of `StringLit`s *before* the existing placement validation runs — so `IRControl.allowed`/`default_placement`, the IR-JSON emitter, the schema (`IR_JSON_VERSION` stays `0.1`), and the Rust core are all unchanged.

**Tech Stack:** Python (lark grammar, dataclass AST, pytest). Reuse the shipped placement machinery; add no parallel mechanism.

**Design:** `docs/superpowers/specs/2026-05-25-placement-named-groups-design.md`.

**Reuse facts (already verified — do not rebuild):**
- `grammar.lark:52` — `SECTION_KW: "meta" | "requires" | ... | "session"`. Adding `"groups"` makes `groups { … }` parse as a `section_block` whose entries are ordinary `assignment`s. **No parser change** (`parser.py:198 section_block` is generic) and **no new AST node** (`A.SectionBlock(keyword, body)` + `A.Assignment(target, value)` + `A.Array(elements)` + `A.StringLit(value)` + `A.NameRef(name)` all exist).
- `resolver.py` section dispatch (~line 227-246) maps `keyword → "<x>_ast"` and raises `"unknown section block keyword"` for anything unmapped — so `groups` MUST be added there + an attr declared (~line 138).
- `resolver.py:768` — `_parse_placement_allowed(name, parse_kind, fields.get("allowed"), loc)` is the `allowed` hook point; `_resolve_set_placement_control` reads the set `default`.
- `compose.py:283` — `_FIELD_MERGE_SECTIONS = {"meta", "requires", "controls"}`; `_merge_section_fields` keys by `stmt.target`. Adding `"groups"` gives child-overrides-parent-by-name merge for free.
- Empty-`allowed` rejection already exists for placement controls (mirror its style).

---

### Task 1: Grammar — `groups` block parses

**Files:**
- Modify: `src/refrain/grammar.lark:52`
- Test: `tests/test_parser.py`

- [ ] **Step 1: Write the failing test**

```python
def test_groups_block_parses():
    import refrain.ast as A
    from refrain import parse
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { sensorimotor = ["C3","Cz","C4"]; frontal = ["F3","Fz","F4"] }
        }
    '''
    proto = parse(src).protocol
    groups = [s for s in proto.body if isinstance(s, A.SectionBlock) and s.keyword == "groups"]
    assert len(groups) == 1
    entries = {s.target: s.value for s in groups[0].body if isinstance(s, A.Assignment)}
    assert set(entries) == {"sensorimotor", "frontal"}
    assert isinstance(entries["sensorimotor"], A.Array)
    assert [e.value for e in entries["sensorimotor"].elements] == ["C3", "Cz", "C4"]
```

(Confirm `parse(src).protocol` is the right accessor by matching an existing `tests/test_parser.py` test; adjust if the suite uses `parse(src)` returning the `File` whose `.protocol` holds the body.)

- [ ] **Step 2: Run it — expect failure** (`groups` is an unknown keyword → ParseError).

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_parser.py::test_groups_block_parses -v`

- [ ] **Step 3: Add the keyword**

In `src/refrain/grammar.lark:52`, add `"groups"`:

```
SECTION_KW: "meta" | "requires" | "reward" | "output" | "controls" | "session" | "groups"
```

- [ ] **Step 4: Run the test — expect PASS.** Also run the full parser suite to confirm no regression: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_parser.py -q`

- [ ] **Step 5: Commit** — `feat(groups): parse a top-level groups block (new section keyword)`

---

### Task 2: Resolver — capture + validate the group table

**Files:**
- Modify: `src/refrain/resolver.py` (add `groups_ast` attr ~line 138; add `"groups": "groups_ast"` to the section-dispatch map ~line 234; add `self.groups: dict[str, tuple[str, ...]] = {}` in `__init__`; add `_resolve_groups`; call it just before `_resolve_controls()` in the resolve pipeline ~line 192)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_groups_table_built():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))           # resolving succeeds; group declared but unused is fine
    assert ir is not None

def test_groups_empty_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = [] }
        }
    '''
    with pytest.raises(ResolveError, match="empty"):
        resolve(parse(src))

def test_groups_duplicate_channel_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","C3"] }
        }
    '''
    with pytest.raises(ResolveError, match="more than once"):
        resolve(parse(src))

def test_group_name_collides_with_control_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { site = ["C3","C4"] }
          controls { site = placement { kind = "active"; default = "C3"; allowed = ["C3","C4"] } }
        }
    '''
    with pytest.raises(ResolveError, match="collides"):
        resolve(parse(src))
```

- [ ] **Step 2: Run — expect failure** (`groups` currently hits the `"unknown section block keyword"` branch).

- [ ] **Step 3: Implement**

In `__init__` (alongside the other `_ast` attrs ~line 138):
```python
        self.groups_ast: A.SectionBlock | None = None
        self.groups: dict[str, tuple[str, ...]] = {}
```
In the section-dispatch map (~line 234, alongside `"controls": "controls_ast"`):
```python
                    "groups": "groups_ast",
```
Add the method and call it in the pipeline immediately before `self._resolve_controls()`:
```python
    def _resolve_groups(self) -> None:
        if self.groups_ast is None:
            return
        control_names = {
            s.target for s in (self.controls_ast.body if self.controls_ast else [])
            if isinstance(s, A.Assignment)
        }
        for stmt in self.groups_ast.body:
            if not isinstance(stmt, A.Assignment):
                raise ResolveError(
                    "groups block may only contain `name = [channels]` entries",
                    loc=getattr(stmt, "loc", None),
                )
            name = stmt.target
            if name in control_names:
                raise ResolveError(
                    f"group {name!r} collides with a control of the same name",
                    loc=stmt.loc,
                )
            if not isinstance(stmt.value, A.Array):
                raise ResolveError(
                    f"group {name!r} must be a list of channel names", loc=stmt.value.loc
                )
            channels: list[str] = []
            for elt in stmt.value.elements:
                if not isinstance(elt, A.StringLit):
                    raise ResolveError(
                        f"group {name!r}: channel names must be strings", loc=elt.loc
                    )
                if elt.value in channels:
                    raise ResolveError(
                        f"group {name!r} lists {elt.value!r} more than once", loc=elt.loc
                    )
                channels.append(elt.value)
            if not channels:
                raise ResolveError(f"group {name!r} is empty", loc=stmt.value.loc)
            self.groups[name] = tuple(channels)
```

- [ ] **Step 4: Run the four tests — expect PASS.**

- [ ] **Step 5: Commit** — `feat(groups): resolver builds + validates the group table`

---

### Task 3: Expand group refs in placement `allowed`

**Files:**
- Modify: `src/refrain/resolver.py` (add `_expand_group_ref`; apply to the `allowed` expr before `_parse_placement_allowed` at ~line 768)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_group_expands_in_active_allowed():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = smr } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    assert ir.controls["site"].allowed == ("C3", "Cz", "C4")

def test_group_allowed_matches_inline_form():
    base = '''
        protocol "p" {{
          meta {{ version = "1.0"; evidence = "clinical"; description = "x" }}
          {groups}controls {{ site = placement {{ kind = "active"; default = "Cz"; allowed = {allowed} }} }}
          input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
          reward {{ continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }}
          output {{ audio_gain = reward.continuous }}
        }}
    '''
    grouped = resolve(parse(base.format(groups='groups { smr = ["C3","Cz","C4"] } ', allowed="smr")))
    inline = resolve(parse(base.format(groups="", allowed='["C3","Cz","C4"]')))
    assert grouped.controls["site"].allowed == inline.controls["site"].allowed

def test_unknown_group_in_allowed_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = nosuch } }
        }
    '''
    with pytest.raises(ResolveError, match="unknown group 'nosuch'"):
        resolve(parse(src))
```

- [ ] **Step 2: Run — expect failure** (a `NameRef` in `allowed` is currently not handled / errors differently).

- [ ] **Step 3: Implement**

Add the helper:
```python
    def _expand_group_ref(self, expr):
        """If `expr` is a bare NameRef in allowed/default position, it names a
        group; expand it to an Array of StringLits. Else return it unchanged."""
        if isinstance(expr, A.NameRef):
            if expr.name not in self.groups:
                raise ResolveError(f"unknown group {expr.name!r}", loc=expr.loc)
            return A.Array(
                elements=tuple(A.StringLit(value=c, loc=expr.loc) for c in self.groups[expr.name]),
                loc=expr.loc,
            )
        return expr
```
At the `allowed` hook (`resolver.py:768`), expand before parsing:
```python
        allowed_expr = self._expand_group_ref(fields.get("allowed"))
        allowed = self._parse_placement_allowed(name, parse_kind, allowed_expr, loc)
```

- [ ] **Step 4: Run the tests — expect PASS.**

- [ ] **Step 5: Commit** — `feat(groups): expand group refs in placement allowed`

---

### Task 4: Expand group refs in `set` `default`

**Files:**
- Modify: `src/refrain/resolver.py` (`_resolve_set_placement_control` — expand the `default` expr via `_expand_group_ref` before parsing it as the channel list)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_group_expands_in_set_default():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          controls { sites = placement { kind = "set"; default = smr; allowed = smr; min = 1; max = 3 } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    assert ir.controls["sites"].default_placement == ("C3", "Cz", "C4")
    assert ir.controls["sites"].allowed == ("C3", "Cz", "C4")

def test_group_default_exceeding_max_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          controls { sites = placement { kind = "set"; default = smr; allowed = smr; min = 1; max = 2 } }
        }
    '''
    with pytest.raises(ResolveError):       # existing min/max count check fires on the expanded default
        resolve(parse(src))
```

- [ ] **Step 2: Run — expect failure.**

- [ ] **Step 3: Implement** — in `_resolve_set_placement_control`, locate where the `default` expr is read and wrap it: `default_expr = self._expand_group_ref(fields.get("default"))`, then parse `default_expr` exactly as before. Do NOT duplicate the count/allowed validation — it must run on the expanded value.

- [ ] **Step 4: Run the tests — expect PASS.**

- [ ] **Step 5: Commit** — `feat(groups): expand group refs in set default`

---

### Task 5: Compose — `groups` merge across `extends`

**Files:**
- Modify: `src/refrain/compose.py:283` (add `"groups"` to `_FIELD_MERGE_SECTIONS`)
- Test: `tests/test_compose.py`

- [ ] **Step 1: Write the failing test** — model it on an existing `test_compose.py` controls-merge test (same loader/harness). Cover: child inherits a parent group; child adds a new group; child overrides a parent group by re-declaring the same name (child wins).

```python
def test_groups_merge_across_extends(tmp_path):
    # parent defines `smr` and `frontal`; child overrides `smr` and adds `occ`.
    # (Use the same parent_loader / filesystem_loader pattern as the existing
    #  controls-merge test in this file.)
    parent = '''
        protocol "base" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"]; frontal = ["F3","Fz","F4"] }
        }
    '''
    child = '''
        protocol "p" extends "base" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","C4"]; occ = ["O1","O2"] }
          controls { site = placement { kind = "active"; default = "C3"; allowed = smr } }
          input "raw" { montage = referential(active: "C3", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    # resolve child with a loader returning `base` for "base":
    ir = resolve(parse(child), parent_loader=_loader_returning({"base": parent}))
    assert ir.controls["site"].allowed == ("C3", "C4")            # child override won
    # frontal (inherited) + occ (added) are also usable — assert via a second control if desired
```

(Use the file's existing helper for building a `parent_loader`; if none, follow `compose.filesystem_loader` with `tmp_path`.)

- [ ] **Step 2: Run — expect failure** (without the merge entry, the child's `groups` replaces the parent's wholesale, so `frontal` is lost; or override semantics differ).

- [ ] **Step 3: Implement** — `src/refrain/compose.py:283`:
```python
_FIELD_MERGE_SECTIONS = {"meta", "requires", "controls", "groups"}
```

- [ ] **Step 4: Run the test + full `tests/test_compose.py` — expect PASS.**

- [ ] **Step 5: Commit** — `feat(groups): merge groups across extends (child overrides by name)`

---

### Task 6: Wire-invariant + docs + version

**Files:**
- Test: `tests/test_ir_json.py`
- Modify: `docs/SPEC.md`, `CHANGELOG.md`, `pyproject.toml`, `refrain-core/pyproject.toml`

- [ ] **Step 1: Write the wire-invariant test**

```python
def test_groups_form_emits_identical_ir_json():
    """A protocol using a group emits IR-JSON byte-identical to the inline-list form."""
    base = '''
        protocol "p" {{
          meta {{ version = "1.0"; evidence = "clinical"; description = "x" }}
          {groups}controls {{ site = placement {{ kind = "active"; default = "Cz"; allowed = {allowed} }} }}
          input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
          reward {{ continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }}
          output {{ audio_gain = reward.continuous }}
        }}
    '''
    grouped = resolve(parse(base.format(groups='groups { smr = ["C3","Cz","C4"] } ', allowed="smr")))
    inline = resolve(parse(base.format(groups="", allowed='["C3","Cz","C4"]')))
    assert ir_to_json_obj(grouped) == ir_to_json_obj(inline)
    # placement controls are omitted from IR-JSON either way; groups never appear
    assert "groups" not in ir_to_json_obj(grouped)
```

(Match the import style for `ir_to_json_obj` used elsewhere in `tests/test_ir_json.py`.)

- [ ] **Step 2: Run — expect PASS already** (no emitter change needed). If it fails, the expansion leaked into the IR — fix the resolver, do not touch the emitter.

- [ ] **Step 3: Docs + version**
  - `docs/SPEC.md`: add a `groups` subsection (syntax + the two reference sites + validation), cross-referenced from the placement §4.9.
  - `CHANGELOG.md`: a `0.5.0` entry.
  - `pyproject.toml` + `refrain-core/pyproject.toml`: bump `version` to `0.5.0`.

- [ ] **Step 4: Run the full suite + drift gate**
  - `VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q` — expect all pass, 5 XDF skips.
  - `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py` — expect `RESULT: PASS` (proves no wire/Rust change; `IR_JSON_VERSION` stays `0.1`).

- [ ] **Step 5: Commit** — `feat(groups): wire-invariant test + SPEC/CHANGELOG + version 0.5.0`

---

## Self-Review

- **Spec coverage:** `groups` block (T1) · table + validations: unknown/empty/dup/collision (T2/T3) · `allowed` expansion (T3) · set `default` expansion (T4) · `extends` merge (T5) · wire-invariant + version (T6). All design items mapped.
- **No placeholders:** every code step shows the code; tests are concrete.
- **Type consistency:** `A.Assignment.target/.value`, `A.Array.elements`, `A.StringLit.value`, `A.NameRef.name`, `IRControl.allowed/.default_placement` used consistently and verified against the source.
- **Reuse:** new section keyword (not a new grammar production), generic AST reused, single `_expand_group_ref` helper called in two spots, compose merge via the existing `_FIELD_MERGE_SECTIONS`. No parallel mechanism. `ir.py`/`ir_json.py` untouched.
