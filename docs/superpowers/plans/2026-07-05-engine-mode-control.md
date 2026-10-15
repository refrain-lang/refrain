# Engine `mode` control — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `mode` control to the Refrain engine so one protocol can select its threshold *type* (percentile vs absolute) and its output *feedback binding* (gated vs ungated) from a value bound at resolve time — collapsing the adaptive/baseline and discrete/modulating sibling files into single configurable protocols.

**Architecture:** A `mode` control is a categorical, resolve-time-bound selector modeled directly on the existing `placement` control. It needs **no grammar/parser/AST changes** — every control already parses as a generic `block_expr` (`NAME block`), so `mode { choices = [...]; default = "..." }` parses for free. All new logic is in the resolver + IR + two host-facing emitters. Selection is expressed with the language's existing ternary + comparison operators (`threshold_style == "baseline" ? absolute(...) : percentile(...)`); a new resolve-time folding pass evaluates any ternary whose condition compares a `mode` control to a string literal, and replaces it with the chosen branch before normal resolution runs.

**Tech Stack:** Python 3.10, Lark grammar (unchanged), `pytest`. Refrain engine repo.

## Global Constraints

- **Working directory / repo:** all files, commands, and commits in this plan are in the Refrain engine repo at `/Users/jcroall/git/refrain/refrain`. Run every command from that directory. Do all work on a dedicated branch (e.g. `feat/mode-control`), not the current working branch.
- **No grammar/parser/AST changes.** Controls parse generically; the `mode` kind is resolver-only. Do not edit `src/refrain/grammar.lark`, `src/refrain/parser.py`, or `src/refrain/ast.py`.
- **Model `mode` on `placement`.** Mirror the pair `_resolve_placement_control` (resolver.py:980) / `_bound_placement_value` (resolver.py:1219). A `mode` control is **never `live_tunable`** (bound at resolve time, like placement) and honours `final = true` (binding is rejected when final).
- **Selection is resolve-time only.** A ternary is folded only when its condition compares a `mode` control to a string literal; every other ternary (e.g. `reward.event.holds ? ... : 0`) must pass through unchanged and resolve as a runtime `IRConditional`.
- **`graded` feedback is out of scope** — it is a distinct reward computation handled later as an `extends` overlay, not a `mode`. This plan covers threshold-style (adaptive/baseline) and feedback-mode (discrete/modulating) only.
- **Spec:** `/Users/jcroall/git/refrain-protocols/docs/superpowers/specs/2026-07-05-refrain-configurable-protocols-design.md`. This plan is Phase 1 of that spec; later phases (library re-authoring, recorder, portal) are separate plans.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit.

## File Structure

- `src/refrain/ir.py` — extend `IRControl` (line 263) with two mode-only fields (`choices`, `default_mode`). One responsibility: the resolved-control data shape.
- `src/refrain/resolver.py` — all new logic: a `_control_kind_dims` arm (line 2204), a `_resolve_control` dispatch arm (line 940), `_resolve_mode_control` + `_parse_mode_choices` (new, modeled on `_resolve_placement_control`), `_bound_mode_value` (new, modeled on `_bound_placement_value`), the folding trio `_fold_mode_conditionals` / `_eval_mode_condition` / `_mode_ref_and_literal` (new), and two one-line wire-ins (threshold `type` at line 609, `_resolve_stream_expr` entry at line 1666).
- `src/refrain/editor/describe.py` — surface `mode` controls to hosts as a `modes` list (line 419) with a `_mode_view` builder.
- `src/refrain/ir_json.py` — exclude `mode` controls from the emitted numeric `controls` map (line 417), same as placement (they resolve away).
- `tests/test_resolver.py` — all resolver/behaviour tests (extend this file; imports at lines 11–39, placement fixtures at 727–772 are the pattern).
- `tests/test_editor_describe.py` *(or the existing editor test file)* — describe/ir_json host-facing tests.

---

### Task 1: Declare, resolve, and validate a `mode` control

**Files:**
- Modify: `src/refrain/ir.py:263-283` (add two fields to `IRControl`)
- Modify: `src/refrain/resolver.py:2204-2222` (`_control_kind_dims`), `src/refrain/resolver.py:940` (`_resolve_control` dispatch), add `_resolve_mode_control` + `_parse_mode_choices` near `_resolve_placement_control` (resolver.py:980)
- Test: `tests/test_resolver.py`

**Interfaces:**
- Produces: `IRControl` gains `choices: tuple = ()` and `default_mode: str | None = None`. A `mode { choices = [...]; default = "..."; label = "..."; final = <bool> }` control resolves to `IRControl(type_kind="mode", choices=<tuple[str]>, default_mode=<str>, final=<bool>, live_tunable=False)`.
- Consumes: existing helpers `_bool_field(fields, name, *, default)` (resolver.py:1992), `_assignments_dict(body)` (resolver.py:1971), AST nodes `A.Array` (`.elements`), `A.StringLit` (`.value`); constant `DIMENSIONLESS`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_resolver.py` (near the placement tests, ~line 772):

```python
_MODE_DECL_PROTO = '''
    protocol "styled" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive"; label = "Threshold style" }
        reward_pct = percent { default = 70 }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" {
        from = "raw"
        pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
      }
      threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
      reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
      output { audio_gain = reward.continuous }
    }
'''


def test_mode_control_resolves():
    ir = resolve(parse(_MODE_DECL_PROTO), _AMP)
    ctl = ir.controls["threshold_style"]
    assert ctl.type_kind == "mode"
    assert ctl.choices == ("adaptive", "baseline")
    assert ctl.default_mode == "adaptive"
    assert ctl.label == "Threshold style"
    assert ctl.live_tunable is False


def _mode_proto(*, choices='["adaptive", "baseline"]', default='"adaptive"', extra=""):
    return f'''
        protocol "styled" {{
          meta {{ version = "1.0"; evidence = "clinical"; description = "x" }}
          controls {{ m = mode {{ choices = {choices}; default = {default}{extra} }} }}
          input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
          reward {{ continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }}
          output {{ audio_gain = reward.continuous }}
        }}
    '''


def test_mode_default_not_in_choices_fails():
    with pytest.raises(ResolveError, match="not in choices|choices"):
        resolve(parse(_mode_proto(default='"other"')), _AMP)


def test_mode_empty_choices_fails():
    with pytest.raises(ResolveError, match="non-empty|choices"):
        resolve(parse(_mode_proto(choices="[]")), _AMP)


def test_mode_non_string_choice_fails():
    with pytest.raises(ResolveError, match="string"):
        resolve(parse(_mode_proto(choices='["adaptive", 3]')), _AMP)


def test_mode_live_tunable_rejected():
    with pytest.raises(ResolveError, match="live_tunable"):
        resolve(parse(_mode_proto(extra="; live_tunable = true")), _AMP)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_resolver.py::test_mode_control_resolves -v`
Expected: FAIL — resolving a `mode { ... }` control raises `ResolveError: unknown control type 'mode'` (from `_control_kind_dims`, resolver.py:2222).

- [ ] **Step 3: Add the `IRControl` fields**

In `src/refrain/ir.py`, in the `IRControl` dataclass, add after the existing placement fields (after `set_max` at line 283):

```python
    # Mode-control fields (empty/None for non-mode controls):
    choices: tuple = ()                  # mode only: tuple of allowed string choices
    default_mode: str | None = None      # mode only: the default choice
```

- [ ] **Step 4: Add the `mode` dims arm**

In `src/refrain/resolver.py`, in `_control_kind_dims` (line 2204), before the final `raise` at line 2222:

```python
    if kind == "mode":
        return DIMENSIONLESS   # categorical string choices; no unit arithmetic
```

- [ ] **Step 5: Add the dispatch + the resolver methods**

In `src/refrain/resolver.py`, in `_resolve_control` (line 935), after the placement dispatch at line 941:

```python
        if kind == "mode":
            return self._resolve_mode_control(name, fields, block.loc)
```

Then add these two methods next to `_resolve_placement_control` (after it ends, ~line 1045):

```python
    def _resolve_mode_control(self, name: str, fields: dict, loc) -> IRControl:
        """Parse and validate a `mode { ... }` control block.

        A mode control is a categorical, resolve-time-bound selector (like a
        placement, but over abstract string choices instead of channels). It is
        never live_tunable: the value is chosen before the run and folds away
        during resolution.
        """
        if self._bool_field(fields, "live_tunable", default=False):
            raise ResolveError(
                f"mode control {name!r} cannot be live_tunable "
                "(it is bound at resolve time, like a placement)",
                loc=loc,
            )
        choices = self._parse_mode_choices(name, fields.get("choices"), loc)
        default_expr = fields.get("default")
        if not isinstance(default_expr, A.StringLit):
            raise ResolveError(
                f"mode control {name!r} requires a string `default`",
                loc=loc,
            )
        default_mode = default_expr.value
        if default_mode not in choices:
            raise ResolveError(
                f"mode control {name!r}: default {default_mode!r} not in "
                f"choices {list(choices)}",
                loc=loc,
            )
        final = self._bool_field(fields, "final", default=False)
        label_expr = fields.get("label")
        label = label_expr.value if isinstance(label_expr, A.StringLit) else None
        return IRControl(
            name=name,
            canonical_name=f"control/{name}",
            type_kind="mode",
            dims=DIMENSIONLESS,
            default=None,
            range_low=None,
            range_high=None,
            log_scale=False,
            label=label,
            live_tunable=False,
            tune_strategy=None,
            loc=loc,
            final=final,
            choices=choices,
            default_mode=default_mode,
        )

    def _parse_mode_choices(self, name: str, expr, loc) -> tuple:
        """Parse `choices = ["a", "b", ...]` into a tuple of unique strings."""
        if not isinstance(expr, A.Array) or not expr.elements:
            raise ResolveError(
                f"mode control {name!r} needs a non-empty `choices = [...]` of strings",
                loc=loc,
            )
        out: list[str] = []
        for elt in expr.elements:
            if not isinstance(elt, A.StringLit):
                raise ResolveError(
                    f"mode control {name!r}: every choice must be a string literal",
                    loc=loc,
                )
            out.append(elt.value)
        if len(set(out)) != len(out):
            raise ResolveError(
                f"mode control {name!r}: choices must be unique, got {out}",
                loc=loc,
            )
        return tuple(out)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_resolver.py -k "mode" -v`
Expected: PASS — `test_mode_control_resolves`, `test_mode_default_not_in_choices_fails`, `test_mode_empty_choices_fails`, `test_mode_non_string_choice_fails`, `test_mode_live_tunable_rejected` all green.

- [ ] **Step 7: Run the full resolver suite (no regressions)**

Run: `python -m pytest tests/test_resolver.py -q`
Expected: PASS — all pre-existing tests still pass (the new fields default to `()`/`None` for every other control).

- [ ] **Step 8: Commit**

```bash
git add src/refrain/ir.py src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(engine): add mode control kind (declare + validate)"
```

---

### Task 2: Fold a bound `mode` into a threshold's `type` (adaptive ↔ baseline)

**Files:**
- Modify: `src/refrain/resolver.py:606-644` (`_resolve_threshold` — one wire-in), add `_bound_mode_value` (near `_bound_placement_value`, resolver.py:1219) and the folding trio `_fold_mode_conditionals` / `_eval_mode_condition` / `_mode_ref_and_literal`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: Task 1's `IRControl.choices` / `default_mode`; `self.bindings` (resolver.py:132); AST nodes `A.Conditional` (`.cond`, `.then_branch`, `.else_branch`), `A.BinaryOp` (`.op`, `.left`, `.right`), `A.NameRef` (`.name`), `A.StringLit` (`.value`).
- Produces: `_bound_mode_value(name) -> str`; `_fold_mode_conditionals(expr) -> A.Expr` (returns the selected branch for a mode-conditional, else the input unchanged). After this task, `ir.thresholds["env_t"].threshold_call.callee` reflects the bound mode.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_resolver.py`:

```python
_MODE_THRESHOLD_PROTO = '''
    protocol "styled" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }
        reward_pct = percent { default = 70 }
        thr_uv = voltage { default = 2.0 uV }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" {
        from = "raw"
        pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
      }
      threshold "env_t" {
        signal = "env"
        type = threshold_style == "baseline"
                 ? absolute(value: thr_uv)
                 : percentile(target_pct: reward_pct, window: 2 min)
      }
      reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
      output { audio_gain = reward.continuous }
    }
'''


def test_mode_threshold_default_is_percentile():
    ir = resolve(parse(_MODE_THRESHOLD_PROTO), _AMP)
    assert ir.thresholds["env_t"].threshold_call.callee == "percentile"


def test_mode_threshold_baseline_binding_is_absolute():
    ir = resolve(parse(_MODE_THRESHOLD_PROTO), _AMP,
                 bindings={"threshold_style": "baseline"})
    assert ir.thresholds["env_t"].threshold_call.callee == "absolute"


def test_mode_threshold_invalid_binding_fails():
    with pytest.raises(ResolveError, match="not in choices|choices"):
        resolve(parse(_MODE_THRESHOLD_PROTO), _AMP,
                bindings={"threshold_style": "nonsense"})


def test_mode_final_rejects_override():
    proto = _MODE_THRESHOLD_PROTO.replace(
        'default = "adaptive" }',
        'default = "adaptive"; final = true }')
    with pytest.raises(ResolveError, match="final"):
        resolve(parse(proto), _AMP, bindings={"threshold_style": "baseline"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_resolver.py::test_mode_threshold_default_is_percentile -v`
Expected: FAIL — `_resolve_threshold` sees an `A.Conditional` (not an `A.Call`) and raises `ResolveError: threshold "env_t".type must be a constructor call` (resolver.py:626).

- [ ] **Step 3: Add `_bound_mode_value`**

In `src/refrain/resolver.py`, add next to `_bound_placement_value` (~line 1219):

```python
    def _bound_mode_value(self, name: str) -> str:
        """Return the concrete bound choice for a mode control.

        Uses `self.bindings[name]` when present (validated against `choices`,
        forbidden when the control is `final`); otherwise the declared default.
        """
        ctrl = self.controls.get(name)
        if ctrl is None or ctrl.type_kind != "mode":
            raise ResolveError(
                f"internal: _bound_mode_value called for non-mode {name!r}",
            )
        if name in self.bindings:
            if ctrl.final:
                raise ResolveError(
                    f"mode {name!r} is final and cannot be overridden",
                    loc=ctrl.loc,
                )
            value = self.bindings[name]
            if not isinstance(value, str):
                raise ResolveError(
                    f"mode {name!r} binding must be a string, got "
                    f"{type(value).__name__}",
                    loc=ctrl.loc,
                )
            if value not in ctrl.choices:
                raise ResolveError(
                    f"mode {name!r}: {value!r} not in choices {list(ctrl.choices)}",
                    loc=ctrl.loc,
                )
            return value
        return ctrl.default_mode
```

- [ ] **Step 4: Add the folding trio**

In `src/refrain/resolver.py`, add near the placement substitution helpers (~after `_substitute_placement_args`, line 561):

```python
    def _fold_mode_conditionals(self, expr):
        """Collapse resolve-time ternaries whose condition tests a `mode` control.

        Returns the selected branch (recursively folded) when `expr` is a
        `cond ? a : b` whose `cond` compares a mode control to a string literal.
        Any other expression is returned unchanged, so runtime ternaries
        (e.g. `reward.event.holds ? ... : 0`) pass through untouched.
        """
        if isinstance(expr, A.Conditional):
            picked = self._eval_mode_condition(expr.cond)
            if picked is not None:
                chosen = expr.then_branch if picked else expr.else_branch
                return self._fold_mode_conditionals(chosen)
        return expr

    def _eval_mode_condition(self, cond):
        """Evaluate a `<mode> == "x"` / `<mode> != "x"` comparison at resolve time.

        Returns True/False when `cond` is such a comparison (operands in either
        order); None when it does not reference a mode control (leave to runtime).
        """
        if not isinstance(cond, A.BinaryOp) or cond.op not in ("==", "!="):
            return None
        pair = self._mode_ref_and_literal(cond.left, cond.right)
        if pair is None:
            return None
        mode_name, literal = pair
        bound = self._bound_mode_value(mode_name)
        equal = bound == literal
        return equal if cond.op == "==" else not equal

    def _mode_ref_and_literal(self, a, b):
        """If one operand is a NameRef to a mode control and the other a string
        literal, return (mode_name, literal_value); otherwise None."""
        for x, y in ((a, b), (b, a)):
            if (
                isinstance(x, A.NameRef)
                and x.name in self.controls
                and self.controls[x.name].type_kind == "mode"
                and isinstance(y, A.StringLit)
            ):
                return x.name, y.value
        return None
```

- [ ] **Step 5: Wire folding into `_resolve_threshold`**

In `src/refrain/resolver.py`, in `_resolve_threshold` (line 606), immediately after `type_expr = fields.get("type")` (line 609) and before the `isinstance(type_expr, A.Call)` check (line 626):

```python
        if type_expr is not None:
            type_expr = self._fold_mode_conditionals(type_expr)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_resolver.py -k "mode_threshold or mode_final" -v`
Expected: PASS — default resolves to `percentile`, `bindings={"threshold_style": "baseline"}` resolves to `absolute`, invalid binding and `final` override both raise `ResolveError`.

- [ ] **Step 7: Run the full resolver suite (no regressions)**

Run: `python -m pytest tests/test_resolver.py -q`
Expected: PASS — existing runtime-ternary behaviour is untouched (folding returns non-mode conditionals unchanged).

- [ ] **Step 8: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(engine): fold mode conditionals into threshold type (adaptive/baseline)"
```

---

### Task 3: Fold a bound `mode` into output bindings (discrete ↔ modulating)

**Files:**
- Modify: `src/refrain/resolver.py:1666` (`_resolve_stream_expr` entry — one wire-in)
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: Task 2's `_fold_mode_conditionals`; IR node `IRConditional` (import in the test from `refrain.ir`).
- Produces: mode-conditional output bindings fold at resolve time; the inner runtime ternary still resolves to an `IRConditional`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_resolver.py` (add `IRConditional` to the `from refrain.ir import (...)` block at lines 19–32 if not already present):

```python
_MODE_OUTPUT_PROTO = '''
    protocol "styled" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        feedback_mode = mode { choices = ["discrete", "modulating"]; default = "discrete" }
        reward_pct = percent { default = 70 }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" {
        from = "raw"
        pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
      }
      threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
      reward {
        event = dwell(condition: above("env", "env_t"), duration: 250 ms)
        continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3)
      }
      output {
        audio_chime = reward.event
        audio_gain  = feedback_mode == "modulating"
                        ? reward.continuous
                        : (reward.event.holds ? reward.continuous : 0)
      }
    }
'''


def test_feedback_mode_default_is_gated_conditional():
    ir = resolve(parse(_MODE_OUTPUT_PROTO), _AMP)
    assert isinstance(ir.output["audio_gain"], IRConditional)


def test_feedback_mode_modulating_is_ungated():
    ir = resolve(parse(_MODE_OUTPUT_PROTO), _AMP,
                 bindings={"feedback_mode": "modulating"})
    assert not isinstance(ir.output["audio_gain"], IRConditional)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_resolver.py::test_feedback_mode_default_is_gated_conditional -v`
Expected: FAIL — without folding at the output entry, `_resolve_stream_expr` hits the `feedback_mode` NameRef inside the comparison and raises a `ResolveError` (a `mode` control has no numeric/stream value), OR mis-resolves the outer ternary. Either way, not the expected `IRConditional`.

- [ ] **Step 3: Wire folding into `_resolve_stream_expr`**

In `src/refrain/resolver.py`, at the very top of `_resolve_stream_expr` (the method containing the dispatch at line 1666), before the first `isinstance` dispatch:

```python
        expr = self._fold_mode_conditionals(expr)
```

(Folding is idempotent on non-mode expressions, so recursive calls from `_resolve_conditional`/`_resolve_binary` are safe.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_resolver.py -k "feedback_mode" -v`
Expected: PASS — default folds to the inner gated ternary (`IRConditional`); `modulating` folds to the bare `reward.continuous` (not an `IRConditional`).

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python -m pytest tests/test_resolver.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(engine): fold mode conditionals into output bindings (discrete/modulating)"
```

---

### Task 4: Surface `mode` controls to hosts (describe + IR JSON)

**Files:**
- Modify: `src/refrain/editor/describe.py:16-37` (add `_mode_view`), `src/refrain/editor/describe.py:419-433` (route `mode` into a `modes` list)
- Modify: `src/refrain/ir_json.py:414-418` (exclude `mode` from emitted numeric controls)
- Test: `tests/test_editor_describe.py` (create if absent) and `tests/test_ir_json.py`

**Interfaces:**
- Consumes: Task 1's `IRControl` mode fields; `describe_protocol(source, *, amp=None)` (describe.py:407); `ir_to_json_obj(ir, sample_rate_hz=...)` (ir_json.py).
- Produces: `describe_protocol(...)["modes"]` is a list of `{name, kind: "mode", choices, default, label, final}`; `mode` controls are absent from `ir_to_json_obj(...)["controls"]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_editor_describe.py` (or add to the existing editor describe test file):

```python
from refrain.editor.describe import describe_protocol

_SRC = '''
    protocol "styled" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive"; label = "Threshold style" }
        reward_pct = percent { default = 70 }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" { from = "raw"; pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ] }
      threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
      reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
      output { audio_gain = reward.continuous }
    }
'''


def test_describe_lists_mode_control():
    desc = describe_protocol(_SRC)
    assert desc["ok"] is True
    modes = {m["name"]: m for m in desc["modes"]}
    assert modes["threshold_style"]["choices"] == ["adaptive", "baseline"]
    assert modes["threshold_style"]["default"] == "adaptive"
    assert modes["threshold_style"]["label"] == "Threshold style"
    # A mode control must not leak into the numeric controls list.
    assert "threshold_style" not in {c["name"] for c in desc["controls"]}
```

Add to `tests/test_ir_json.py` (imports mirror the existing file; use `parse` + `resolve` + `ir_to_json_obj`):

```python
def test_ir_json_excludes_mode_controls():
    from refrain.parser import parse
    from refrain.resolver import resolve
    from refrain.ir_json import ir_to_json_obj
    src = '''
        protocol "styled" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls {
            threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }
            reward_pct = percent { default = 70 }
          }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          derive "env" { from = "raw"; pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ] }
          threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
          reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    obj = ir_to_json_obj(ir, sample_rate_hz=250)
    assert "threshold_style" not in obj["controls"]
    assert "reward_pct" in obj["controls"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_editor_describe.py::test_describe_lists_mode_control tests/test_ir_json.py::test_ir_json_excludes_mode_controls -v`
Expected: FAIL — `desc` has no `"modes"` key (KeyError), and the mode control currently lands in the numeric `controls` (via `_control_view`, so `obj["controls"]` still contains `threshold_style`).

- [ ] **Step 3: Add `_mode_view` and route modes in `describe.py`**

In `src/refrain/editor/describe.py`, add near `_placement_view` (~line 37):

```python
def _mode_view(name: str, ctl: Any) -> dict:
    return {
        "name": name,
        "kind": "mode",
        "choices": list(ctl.choices),
        "default": ctl.default_mode,
        "label": ctl.label or name,
        "final": bool(ctl.final),
    }
```

Then replace the control-splitting loop in `describe_protocol` (lines 419–421) with:

```python
    controls, placements, modes = [], [], []
    for name, ctl in ir.controls.items():
        if ctl.type_kind == "placement":
            placements.append(_placement_view(name, ctl))
        elif ctl.type_kind == "mode":
            modes.append(_mode_view(name, ctl))
        else:
            controls.append(_control_view(name, ctl))
```

And add `modes` to the returned dict (line 431–433):

```python
    return {"ok": True, "diagnostics": [], "meta": _meta_from_ast(ast),
            "in_subset": in_subset, "controls": controls,
            "placements": placements, "modes": modes, "model": model}
```

- [ ] **Step 4: Exclude `mode` from emitted controls in `ir_json.py`**

In `src/refrain/ir_json.py`, change the `controls` comprehension filter (line 417) from:

```python
            if c.type_kind != "placement"
```

to:

```python
            if c.type_kind not in ("placement", "mode")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_editor_describe.py tests/test_ir_json.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole engine suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS — full suite green.

- [ ] **Step 7: Commit**

```bash
git add src/refrain/editor/describe.py src/refrain/ir_json.py tests/test_editor_describe.py tests/test_ir_json.py
git commit -m "feat(engine): surface mode controls to hosts (describe modes + ir_json exclusion)"
```

---

## Self-Review

**Spec coverage (Phase 1 slice of the spec):**
- Spec "Engine feature: the `mode` control" → Task 1 (declare/validate) + Task 2 (`_bound_mode_value`, resolve-time selection). ✓
- Spec "Threshold style … one `threshold` block expresses both forms, selected by a `threshold_style` mode" → Task 2 (folds `type` to percentile/absolute). ✓
- Spec "Feedback mode (discrete ↔ modulating) … one `output` block selects the audio binding" → Task 3. ✓
- Spec "graded feedback is not a mode" → Global Constraints exclude it; no task implements it. ✓
- Spec host-facing decision (`describe.py` + `ir_json.py`) → Task 4. ✓
- Out of Phase-1 scope (library re-authoring, recorder, portal, amp-conditional EMG) → correctly deferred to later plans.

**Placeholder scan:** every code step shows complete code; every run step gives an exact command + expected result. No "TBD"/"add validation"/"similar to Task N". ✓

**Type consistency:** `IRControl.choices` / `default_mode` (Task 1) are read by `_bound_mode_value` (Task 2), `_mode_view` (Task 4). `_fold_mode_conditionals` (Task 2) is reused verbatim in Task 3. `threshold_call.callee` (Task 2 assertions) matches `IRThreshold.threshold_call: IRCall` / `IRCall.callee`. `ir.output["audio_gain"]` / `IRConditional` (Task 3) match `IRProtocol.output: dict[str, IRExpr]`. ✓

**Note for the executor:** the test fixtures are modeled on the real `protocols/smr_theta_cz.refrain` shape and the existing `_SITE_PROTO` pattern in `tests/test_resolver.py`. If a fixture fails to parse at Step 2 for a syntax reason (rather than the intended feature-missing reason), reconcile it against a known-good protocol before implementing — the failing test must fail because the feature is absent, not because the fixture is malformed.
