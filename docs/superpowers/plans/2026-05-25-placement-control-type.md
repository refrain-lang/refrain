# `placement` control type (Modes 1 & 3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a resolve-time-bound `placement` control type so one `.refrain` artifact can be deployed to different electrode sites by *binding* (not editing) — kinds `active` + `bipolar`, Mode 1 (default+override) and Mode 3 (fixed/`final`).

**Architecture:** Pure front-end (parser/resolver). A placement control is substituted into montage channel slots + `requires.channels` **at resolve time** via a new `resolve(..., bindings=...)` argument, producing a fully concrete IR. Placement controls are resolve-time-only, so the IR-JSON emitter omits them → **IR-JSON schema v0.1 and the Rust core are unchanged**.

**Tech Stack:** Python (parser = Lark, resolver, dataclass IR), pytest. Design spec: `docs/superpowers/specs/2026-05-25-placement-control-type-design.md`.

**Branch:** `placement-control-type` (off `main` @ 1c676ea / tag v0.2.0).

**Run tests with:** `VIRTUAL_ENV=.venv .venv/bin/python -m pytest <path> -q` (from the worktree root).

---

## File Structure

- `src/refrain/ir.py` — add `kind`, `allowed`, `final` fields to `IRControl` (line ~239).
- `src/refrain/resolver.py` — `placement` in `_control_kind_dims` (~1259); placement parsing in `_resolve_control` (~583); `bindings` param on `resolve()` (~1304) + `_Resolver.__init__` (~127); resolve-time substitution of placement refs in montage channel slots and `requires.channels` (~341 `_parse_channel_list`, montage via `_resolve_input`/`_resolve_call`); `allowed ∩ device` validation; `final` lock.
- `src/refrain/primitives.py` — `bipolar(pair:)` montage form (specs at ~205 `_BIPOLAR`).
- `src/refrain/compose.py` — `final` protection for controls.
- `src/refrain/ir_json.py` — `_emit_control` caller skips `type_kind="placement"`.
- `pyproject.toml`, `refrain-core/pyproject.toml`, `CHANGELOG.md`, `docs/SPEC.md` — version 0.3.0 + docs.
- Tests: `tests/test_resolver.py`, `tests/test_compose.py`, `tests/test_ir_json.py`.

---

## Task 1: `placement` control type — declaration parses, resolves, self-validates

Foundational: the type exists and resolves its own fields. No montage binding yet.

**Files:**
- Modify: `src/refrain/ir.py` (`IRControl`, ~239)
- Modify: `src/refrain/resolver.py` (`_control_kind_dims` ~1259, `_resolve_control` ~583)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_resolver.py` (uses the existing `parse` + `resolve` imports already at the top of that file):

```python
def test_placement_control_active_resolves():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3","C4"]; label = "Training site" } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    c = ir.controls["site"]
    assert c.type_kind == "placement"
    assert c.kind == "active"
    assert c.allowed == ("Cz", "C3", "C4")
    assert c.final is False


def test_placement_default_must_be_in_allowed():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Fz"; allowed = ["Cz","C3"] } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    with pytest.raises(ResolveError, match="default.*not in allowed|allowed"):
        resolve(parse(src))


def test_placement_rejects_live_tunable():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = "any"; live_tunable = true } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    with pytest.raises(ResolveError, match="live_tunable|frozen"):
        resolve(parse(src))
```

(Confirm `ResolveError` and `parse`/`resolve` are imported at the top of `tests/test_resolver.py`; they are used by existing tests there.)

- [ ] **Step 2: Run to verify they fail**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k placement`
Expected: FAIL — `_control_kind_dims` raises `unknown control type 'placement'`, and `IRControl` has no `kind`/`allowed`/`final`.

- [ ] **Step 3: Add fields to `IRControl`**

In `src/refrain/ir.py`, extend the `IRControl` dataclass (after `type_kind`, keep it frozen/slots). Add:
```python
    kind: str | None = None          # placement only: "active" | "bipolar"; else None
    allowed: tuple = ()              # placement only: tuple of channel names, or tuple of (plus,minus) pairs, or () meaning "any"
    final: bool = False
```
Place these as fields with defaults so existing `IRControl(...)` construction sites that don't pass them still work. (The current constructor in `_resolve_control` passes fields positionally/by-keyword through `tune_strategy` and `loc`; add the new fields with defaults so non-placement controls are unaffected.)

- [ ] **Step 4: Register `placement` in `_control_kind_dims`**

In `src/refrain/resolver.py::_control_kind_dims` (~1259), before the final `raise`, add:
```python
    if kind == "placement":
        return DIMENSIONLESS   # categorical (channel identifiers); no unit arithmetic
```

- [ ] **Step 5: Parse placement fields in `_resolve_control`**

In `src/refrain/resolver.py::_resolve_control` (~583), after `dims = _control_kind_dims(...)` and `fields = self._assignments_dict(...)`, add a placement branch that parses `kind`, `allowed`, `final`, validates, and returns an `IRControl`. Concretely:

```python
        if kind == "placement":
            return self._resolve_placement_control(name, fields, block.loc)
```

Add the helper (near `_resolve_control`):
```python
    def _resolve_placement_control(self, name, fields, loc) -> IRControl:
        # kind: "active" | "bipolar"
        kind_expr = fields.get("kind")
        if not isinstance(kind_expr, A.StringLit) or kind_expr.value not in ("active", "bipolar"):
            raise ResolveError(
                f"placement control {name!r} needs kind = \"active\" or \"bipolar\"", loc=loc
            )
        place_kind = kind_expr.value
        if self._bool_field(fields, "live_tunable", default=False):
            raise ResolveError(
                f"placement control {name!r} cannot be live_tunable (site is frozen per session)",
                loc=loc,
            )
        final = self._bool_field(fields, "final", default=False)
        label_expr = fields.get("label")
        label = label_expr.value if isinstance(label_expr, A.StringLit) else None
        allowed = self._parse_allowed(name, place_kind, fields.get("allowed"), loc)
        default = self._parse_placement_value(name, place_kind, fields.get("default"), loc)
        if default is None:
            raise ResolveError(f"placement control {name!r} requires a default", loc=loc)
        self._check_in_allowed(name, default, allowed, loc)
        return IRControl(
            name=name, canonical_name=f"control/{name}", type_kind="placement",
            dims=DIMENSIONLESS, default=None, range_low=None, range_high=None,
            log_scale=False, label=label, live_tunable=False, tune_strategy=None,
            kind=place_kind, allowed=allowed, final=final, loc=loc,
        )
```

Add three small helpers in the resolver:
- `_parse_placement_value(name, place_kind, expr, loc)` → for `active`, expects `A.StringLit` → returns the channel string; for `bipolar`, expects `A.Tuple` of two `A.StringLit` → returns `(plus, minus)`. Returns `None` if `expr is None`. Raise `ResolveError` on the wrong shape.
- `_parse_allowed(name, place_kind, expr, loc)` → `A.StringLit "any"` → `()` (sentinel for any); else `A.Array` whose elements are parsed via `_parse_placement_value` (channel strings for active, pair tuples for bipolar) → tuple. Default (absent) → `()` ("any").
- `_check_in_allowed(name, value, allowed, loc)` → if `allowed` is non-empty and `value not in allowed`, raise `ResolveError(f"placement {name!r}: {value} not in allowed {list(allowed)}")`.

Store `default` as the parsed Python value on the IRControl via `allowed`/a dedicated field? — Simplest: keep the resolved default channel(s) accessible. Add a `default_value` to carry the parsed string/pair, **or** reuse: store the parsed default in a new `IRControl` slot. To avoid bloating `IRControl`, store the placement default as a 1-tuple/2-tuple in a new field:
```python
    default_placement: tuple = ()    # active: ("Cz",); bipolar: ("T3","T4"); () if none
```
Add this field to `IRControl` (Step 3) and set it here (`default_placement=(default,)` for active, `default_placement=default` for bipolar). (Numeric controls keep `default` as the `IRExpr`; placement uses `default_placement`.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k placement`
Expected: PASS (3 tests).

- [ ] **Step 7: Full resolver + parser suites green**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py tests/test_parser_primitives.py -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/ir.py src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(placement): placement control type declaration + validation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: resolve-time binding — `active` placement in a montage channel slot

Adds the `bindings` argument and substitutes an `active` placement into a montage's channel slot, with `allowed ∩ device` validation and the `final` lock.

**Files:**
- Modify: `src/refrain/resolver.py` (`resolve()` ~1304, `_Resolver.__init__` ~127, montage resolution via `_resolve_input` ~395 / `_resolve_call` ~906, `_resolve_name_ref` ~821)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
from refrain.amp_profile import load_amp_profile
from pathlib import Path
_AMP = load_amp_profile(Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json")

_SITE_PROTO = '''
    protocol "poise" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3","C4"] } }
      input "raw" { montage = referential(active: site, reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''

def _active_channel(ir):
    # The bound channel appears as the montage call's `active` arg (a string literal in the IR).
    call = ir.inputs["raw"].montage
    return next(a.value.value for a in call.args if a.name == "active")

def test_placement_binds_default_site():
    ir = resolve(parse(_SITE_PROTO), _AMP)
    assert _active_channel(ir) == "Cz"

def test_placement_binds_override_site():
    ir = resolve(parse(_SITE_PROTO), _AMP, bindings={"site": "C3"})
    assert _active_channel(ir) == "C3"

def test_placement_binding_not_in_allowed_fails():
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_SITE_PROTO), _AMP, bindings={"site": "Fz"})

def test_placement_binding_not_device_capable_fails():
    src = _SITE_PROTO.replace('allowed = ["Cz","C3","C4"]', 'allowed = "any"')
    with pytest.raises(ResolveError, match="missing|capable|channel"):
        resolve(parse(src), _AMP, bindings={"site": "ZZ9"})

def test_final_placement_rejects_override():
    src = _SITE_PROTO.replace('allowed = ["Cz","C3","C4"]', 'allowed = ["Cz"]; final = true')
    with pytest.raises(ResolveError, match="final|locked|cannot override"):
        resolve(parse(src), _AMP, bindings={"site": "C3"})


def test_coherence_two_active_placements_bind():
    # Coherence = two inputs, each with its own active placement (no new construct).
    src = '''
        protocol "coh" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls {
            site_a = placement { kind = "active"; default = "C3"; allowed = ["C3","F3"] }
            site_b = placement { kind = "active"; default = "C4"; allowed = ["C4","F4"] }
          }
          requires { channels = [site_a, site_b] }
          input "a" { montage = referential(active: site_a, reference: "linked_ears") }
          input "b" { montage = referential(active: site_b, reference: "linked_ears") }
          derive "coh" { formula = coherence("a", "b") }
          reward { continuous = sigmoid("coh", midpoint: 0.5, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src), _AMP, bindings={"site_a": "F3", "site_b": "F4"})
    a_call = ir.inputs["a"].montage
    b_call = ir.inputs["b"].montage
    assert next(x.value.value for x in a_call.args if x.name == "active") == "F3"
    assert next(x.value.value for x in b_call.args if x.name == "active") == "F4"
    assert set(ir.requires.channels) == {"F3", "F4"}
```

(`coherence(...)` may need its exact arg form / a `derive { formula = ... }` shape matched to the corpus `micro_06_coherence` protocol — copy that protocol's coherence/derive syntax if the inline form above doesn't parse.)

(Confirm the exact IR montage-arg accessor by inspecting one resolved montage: `ir.inputs["raw"].montage` is an `IRCall`; its `args` carry the channel value. Adjust `_active_channel` to match the real IR arg structure — read `ir.py` `IRCall`/`IRArg`. The intent: assert the bound concrete channel string.)

- [ ] **Step 2: Run to verify they fail**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k "placement and (bind or final)"`
Expected: FAIL — `resolve()` has no `bindings` kwarg; `referential(active: site)` currently turns `site` into an `IRControlRef`, not a bound string.

- [ ] **Step 3: Thread `bindings` through `resolve` and `_Resolver`**

`resolve()` (~1304): add keyword-only `bindings: dict[str, object] | None = None`; pass to `_Resolver(composed, amp, bindings)`. `_Resolver.__init__` (~127): accept `bindings=None`, store `self.bindings = bindings or {}`.

- [ ] **Step 4: Resolve placement-bound channels at montage resolution**

Add a resolver helper `_bound_placement_value(name)` → returns the bound value: `self.bindings[name]` if present (and the control is not `final`; if `final` and an override is supplied, raise `ResolveError(f"placement {name!r} is final and cannot be overridden")`), else the control's `default_placement` (a 1- or 2-tuple). Validate the bound value against `allowed` (`_check_in_allowed`) and, when `self.amp` is set, against the device (`amp.has_channel`) for each channel; raise `ResolveError` on failure (mirror the `_resolve_requires` "missing required channels" message).

In montage channel-slot resolution: when an `active:`/`reference:`/`plus:`/`minus:` slot is a bare `NameRef` that names a **placement** control of `kind="active"`, replace it with the bound concrete channel **string** (build an `A.StringLit`/`IRString` as the existing literal path produces). The cleanest hook: in `_resolve_input` (~395), before `self._resolve_call(montage_ast)`, walk the montage `A.Call` args and rewrite any arg whose value is `A.NameRef` naming an active placement control into an `A.StringLit(bound_channel)`. Then `_resolve_call` proceeds unchanged and the montage IR carries a concrete channel — identical to a literal-site protocol.

(Do NOT route placement refs through `_resolve_name_ref`'s `IRControlRef` path — that's for runtime numeric controls. Placement is bound to a literal here, at resolve time.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k "placement and (bind or final)"`
Expected: PASS (5 tests).

- [ ] **Step 6: Full resolver suite green**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(placement): resolve(bindings=) + active-site montage substitution

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `requires.channels = [site]` derives from the bound placement

**Files:**
- Modify: `src/refrain/resolver.py` (`_parse_channel_list` ~341, `_resolve_requires` ~257)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing test**

```python
_SITE_PROTO_REQ = '''
    protocol "poise" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3"] } }
      requires { channels = [site] }
      input "raw" { montage = referential(active: site, reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''

def test_requires_channels_from_placement():
    ir = resolve(parse(_SITE_PROTO_REQ), _AMP, bindings={"site": "C3"})
    assert ir.requires.channels == ("C3",)
```

- [ ] **Step 2: Run to verify it fails**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k requires_channels_from_placement`
Expected: FAIL — `_parse_channel_list` rejects the non-string-literal `site` (raises "must be string literals").

- [ ] **Step 3: Accept placement refs in `_parse_channel_list`**

In `_parse_channel_list` (~341): for each array element, if it is an `A.StringLit`, keep current behavior; if it is an `A.NameRef` naming a **placement** control, expand it via `_bound_placement_value(name)` to its channel(s) (one for `active`, both legs for `bipolar`) and extend the list. Reject any other expr type with the existing error. The downstream `amp.has_channel` check in `_resolve_requires` (~284) then validates the bound channels for free.

- [ ] **Step 4: Run test to verify it passes**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k requires_channels_from_placement`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(placement): requires.channels derives from bound placement

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `bipolar` placement + `bipolar(pair: site)` montage form

**Files:**
- Modify: `src/refrain/primitives.py` (`_BIPOLAR` ~205), `src/refrain/resolver.py` (montage substitution from Task 2)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing tests**

```python
_BIPOLAR_PROTO = '''
    protocol "ilf" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { site = placement { kind = "bipolar"; default = ("T3","T4"); allowed = [("T3","T4"),("C3","C4")] } }
      requires { channels = [site] }
      input "raw" { montage = bipolar(pair: site) }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''

def _bipolar_legs(ir):
    call = ir.inputs["raw"].montage
    args = {a.name: a.value.value for a in call.args}
    return (args["plus"], args["minus"])

def test_bipolar_placement_binds_default():
    ir = resolve(parse(_BIPOLAR_PROTO), _AMP)
    assert _bipolar_legs(ir) == ("T3", "T4")
    assert ir.requires.channels == ("T3", "T4")

def test_bipolar_placement_binds_override():
    ir = resolve(parse(_BIPOLAR_PROTO), _AMP, bindings={"site": ("C3","C4")})
    assert _bipolar_legs(ir) == ("C3", "C4")

def test_bipolar_pair_not_in_allowed_fails():
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_BIPOLAR_PROTO), _AMP, bindings={"site": ("F3","F4")})
```

(Adjust `_bipolar_legs` to the real IR montage-arg structure as in Task 2.)

- [ ] **Step 2: Run to verify they fail**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k bipolar_placement`
Expected: FAIL — `bipolar(pair:)` is not an accepted montage form; pair-tuple `default`/`allowed`/`bindings` not yet expanded to plus/minus.

- [ ] **Step 3: Add the `bipolar(pair:)` form**

In `src/refrain/primitives.py`, extend `_BIPOLAR` to accept a `pair` form: a `ParamSpec("pair", "placement_pair")` overload (alongside the existing `plus`/`minus` signature). In the resolver montage-substitution (Task 2, `_resolve_input`): when the montage is `bipolar(pair: <NameRef to a bipolar placement>)`, rewrite it into a `bipolar(plus: <plus>, minus: <minus>)` `A.Call` using the bound pair (two `A.StringLit`s), then resolve normally. (This keeps the IR montage shape identical to a literal `bipolar(plus:, minus:)`.) `_bound_placement_value` already returns the 2-tuple for bipolar; `_check_in_allowed` validates the pair against `allowed`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k bipolar_placement`
Expected: PASS (3 tests).

- [ ] **Step 5: Full resolver + parser suites green**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py tests/test_parser_primitives.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/primitives.py src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(placement): bipolar placement + bipolar(pair:) montage form

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `final` on controls — composition protection

**Files:**
- Modify: `src/refrain/compose.py` (final handling, ~183–253)
- Test: `tests/test_compose.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compose.py` (follow the existing final-named-decl tests at lines ~290–334 for the helper/import pattern — child protocol via `extends`, resolved through the composition loader):

```python
def test_final_control_blocks_child_override():
    # Parent declares a final placement control; child trying to redeclare it must error.
    parent = '''
        protocol "base" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "F3"; allowed = ["F3"]; final = true } }
          input "raw" { montage = referential(active: site, reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    child = '''
        protocol "v2" extends "base" {
          controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz"] } }
        }
    '''
    with pytest.raises((ComposeError, ResolveError), match="final"):
        _resolve_with_parent(child, {"base": parent})   # use this file's existing parent-loader helper
```

(Use whatever helper `tests/test_compose.py` already uses to resolve a child against an in-memory parent; match the existing final tests' setup exactly.)

- [ ] **Step 2: Run to verify it fails**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_compose.py -q -k final_control`
Expected: FAIL — composition currently ignores `final` on controls (it only checks named decls).

- [ ] **Step 3: Enforce final for controls in compose**

In `src/refrain/compose.py`: extend the composition merge so that when the parent's `controls` block has an assignment whose `BlockExpr` body contains `final = true`, a child re-declaring the same-named control raises `ComposeError(f"cannot override final control {name!r}")`. Reuse the existing `_has_final_true`-style scan (it currently scans `NamedDecl` bodies; add a parallel check over the parent `controls` section's assignments). Keep the message consistent with the named-decl final errors.

- [ ] **Step 4: Run test to verify it passes**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_compose.py -q -k final_control`
Expected: PASS.

- [ ] **Step 5: Full compose suite green**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_compose.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/compose.py tests/test_compose.py
git commit -m "feat(placement): final protection for controls in composition

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: IR-JSON omits placement controls + version bump + docs

Proves the no-wire-change invariant and ships the version/docs.

**Files:**
- Modify: `src/refrain/ir_json.py` (`_emit_control` caller / `ir_to_json_obj`)
- Modify: `pyproject.toml`, `refrain-core/pyproject.toml`, `CHANGELOG.md`, `docs/SPEC.md`
- Test: `tests/test_ir_json.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_ir_json_omits_placement_controls():
    src = '''
        protocol "poise" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls {
            site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3"] }
            gain_pct = percent { default = 65 %; range = (50%, 90%); live_tunable = true }
          }
          input "raw" { montage = referential(active: site, reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src), _AMP, bindings={"site": "C3"})
    obj = ir_to_json_obj(ir)
    assert "site" not in obj["controls"]          # placement omitted (resolve-time only)
    assert "gain_pct" in obj["controls"]          # numeric/live control still emitted


def test_placement_bound_ir_json_matches_literal_site():
    site_src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "C3"; allowed = ["C3"] } }
          input "raw" { montage = referential(active: site, reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    literal_src = site_src.replace(
        'controls { site = placement { kind = "active"; default = "C3"; allowed = ["C3"] } }', ''
    ).replace('referential(active: site,', 'referential(active: "C3",')
    a = ir_to_json_obj(resolve(parse(site_src), _AMP, bindings={"site": "C3"}))
    b = ir_to_json_obj(resolve(parse(literal_src), _AMP))
    # Same montage/input/derive/output shape; controls differ only by the (omitted) placement.
    assert a["inputs"] == b["inputs"]
    assert a["output"] == b["output"]
```

(Import `ir_to_json_obj` from `refrain.ir_json`, `_AMP` as in Task 2.)

- [ ] **Step 2: Run to verify they fail**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_ir_json.py -q -k placement`
Expected: FAIL — the emitter currently includes the placement control in `obj["controls"]`.

- [ ] **Step 3: Omit placement controls in the emitter**

In `src/refrain/ir_json.py`, find where controls are emitted (the loop building `obj["controls"]` that calls `_emit_control`). Skip controls with `type_kind == "placement"`:
```python
    "controls": {
        c.name: _emit_control(c, ctx)
        for c in ir.controls.values()
        if c.type_kind != "placement"
    },
```
(Match the actual current construction — it may iterate a dict; just add the `type_kind != "placement"` guard.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_ir_json.py -q -k placement`
Expected: PASS.

- [ ] **Step 5: Confirm no wire drift — full suite + drift gate**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q`
Expected: PASS (full Python suite, incl. existing IR-JSON/schema tests — placement adds tests, no regressions).
Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py`
Expected: `RESULT: PASS` — the golden vectors and schema are unchanged (no placement protocol is in the corpus; the wire format is untouched).

- [ ] **Step 6: Version bump + CHANGELOG + SPEC**

- `pyproject.toml` and `refrain-core/pyproject.toml`: set `version = "0.3.0"` (both currently `0.1.0`).
- `CHANGELOG.md`: add a `0.3.0` entry — "Added: `placement` control type (resolve-time site binding, kinds active + bipolar; Mode 1 default+override and Mode 3 fixed/`final`); `resolve(bindings=...)`; `final` on controls. IR-JSON schema unchanged (v0.1)."
- `docs/SPEC.md`: fold the `placement` control type into the controls section (§4.9), note placement references are accepted in montage channel slots and `requires.channels` (§4.2/§4.3), and that `final` now applies to controls (§11.4). Keep it consistent with the design spec.

- [ ] **Step 7: Commit**

```bash
git add src/refrain/ir_json.py tests/test_ir_json.py pyproject.toml refrain-core/pyproject.toml CHANGELOG.md docs/SPEC.md
git commit -m "feat(placement): omit placement controls from IR-JSON; bump 0.3.0 + SPEC

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Definition of done

- All new resolver/compose/ir_json tests green; full `pytest -q` green; `check_equivalence.py` PASS (no wire change).
- `active` and `bipolar` placements bind at resolve time (default + override), with `allowed ∩ device` fail-fast validation and `final` lock; coherence works via two `active` placements (covered implicitly — two placement controls, two inputs).
- IR-JSON omits placement controls; a placement-bound protocol's IR-JSON matches its hardcoded-site equivalent.
- `placement` controls cannot be `live_tunable`; `final` controls are protected through composition.
- Versions are `0.3.0`; CHANGELOG + SPEC updated; IR-JSON schema stays `v0.1`.
```
