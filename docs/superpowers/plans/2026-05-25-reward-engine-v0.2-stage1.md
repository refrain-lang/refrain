# Reward Engine v0.2 — Stage 1 (Python front-end + evaluator + IR-JSON v0.2 emission) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add weighted multi-component composite rewards to Refrain's Python pipeline (grammar → AST → resolver → IR → evaluator → IR-JSON), fully testable on `backend="python"`, while keeping every existing single-reward protocol byte-identical at IR-JSON v0.1.

**Architecture:** Named `reward "<name>"` / suppress-`inhibit "<name>"` components each carry a `signal` (a `[0,1]` stream, reused type machinery) and a `weight` (an ordinary numeric control ref — no new weight mechanism). The top-level `reward { combine = "weighted" }` aggregator computes `reward.composite` as the weighted average of per-component success: reward components contribute `signal`, suppress-inhibits contribute `1 − signal`. `combine` extends the existing `"all"/"any"` field. `reward.composite` is a new `IRRewardField` path that `event`/`continuous`/`output` may reference and that the evaluator computes per chunk. Hard-gate inhibits (`metric`/`threshold`/`action`) keep their v0.1 behavior and still gate the whole composite. The IR-JSON emitter becomes version-aware per protocol: a protocol that uses no named components and no `combine="weighted"` emits `"0.1"` byte-identically; only protocols that use the new features emit `"0.2"`.

**Tech Stack:** Python 3.14, Lark (Earley parser), frozen-slots dataclasses (`ast.py`, `ir.py`), numpy evaluator, pytest. No Rust changes in this stage.

---

## Scope (Stage 1 only)

**IN:** grammar + AST + parser for named `reward "<name>"` and suppress-`inhibit "<name>"` (signal+weight); resolver (`_resolve_reward`, new component resolution, `reward.composite`/`reward.<name>` field resolution, `[0,1]` type check reusing `StreamType`); IR dataclasses (`IRRewardComponent`, extend `IRReward`, extend `IRRewardField`); evaluator (compute components + weighted composite per chunk, live `set_control` on weights); IR-JSON v0.2 emission with version-aware `refrain_ir_version`; Python unit tests. `combine` values supported: `weighted` (new), `all`/`any` (kept). Hard-gate inhibits keep v0.1 semantics.

**OUT (Stages 2 & 3 — DO NOT plan):** any `refrain-core/src/*.rs` change; the `refrain-core/schema/ir-json-v0.2.schema.json` file; extending `check_equivalence.py` with v0.2 fixtures; `combine = "independent"`; set-replication / fan-out integration (`fanout.py`).

**DO NOT bump the package version in any task.** The `0.6.0` bump is deferred to the end of Stage 3. `IR_JSON_VERSION` stays the constant `"0.1"`; v0.2 is selected per-protocol by the emitter (Task 8), not by changing the module constant.

---

## Ground-truth references (read before implementing)

- Design spec: `docs/superpowers/specs/2026-05-25-reward-engine-v0.2-design.md`.
- `src/refrain/grammar.lark:51-55` — `section_block: SECTION_KW block`; `SECTION_KW` includes `"reward"`; `named_decl: DECL_KW string_lit block`; `DECL_KW` includes `"inhibit"` but **not** `"reward"`. `reward "smr" { … }` does **not** parse today (verified: `No terminal matches '"'`).
- `src/refrain/ast.py:112-133` — `SectionBlock(keyword, body)`, `NamedDecl(keyword, name, body)`.
- `src/refrain/resolver.py:606-646` — `_resolve_inhibit` (requires `metric`/`threshold`/`action`). `:1162-1192` — `_resolve_reward` (parses `combine` ∈ {"all","any"}, default "all"). `:1385-1421` — `_resolve_member_access` / `_resolve_reward_field` (handles `reward.continuous`/`reward.event`/`reward.event.holds`). `:399-417` — `_resolve_named_decls` dispatch (inhibit → `_resolve_inhibit`).
- `src/refrain/ir.py:110-116` — `IRRewardField(field_path, stream_type)`. `:208-218` — `IRInhibit`. `:230-238` — `IRReward(continuous, event, combine="all", loc)`. `:298-318` — `IRProtocol.reward: IRReward`.
- `src/refrain/eval_.py:685-698` — reward instantiation/eval in `_process_chunk`. `:999-1012` — `IRRewardField` eval. `:946-970` — `set_control` (forwards to `impl.update_control`). `:481-484` — reward expr impl instantiation in `_build_pipeline`.
- `src/refrain/ir_json.py:53` — `IR_JSON_VERSION = "0.1"`. `:288-292` — `_emit_reward`. `:196-201` — `_emit_expr` for `IRRewardField`. `:333-369` — `ir_to_json_obj` envelope (`refrain_ir_version`).
- `src/refrain/primitives.py:182-187` — `sigmoid`/`linear` both output `scalar_stream(DIMENSIONLESS)`. **There is no dedicated `[0,1]`/`probability` stream type.** The `[0,1]` type check therefore means: `stream_type.value_kind == "scalar"` AND `stream_type.dimensions == DIMENSIONLESS`. (`sigmoid` qualifies; a raw `uV` envelope does not.)
- `src/refrain/primitive_impls.py:553-573` — `SigmoidImpl` (`update_control` retunes `midpoint`). `:576-584` — `LinearImpl` (**not clamped to [0,1]**).
- `refrain-core/schema/ir-json-v0.1.schema.json:10-11` — `"refrain_ir_version": { "const": "0.1" }`. The drift gate (`tests/test_ir_json_schema.py`) validates committed golden `*.ir.json` fixtures (all `"0.1"`) against this schema. Stage 1 must keep v0.1 protocols emitting `"0.1"` so the gate is unaffected and no v0.2 fixture is added.
- `tests/test_resolver.py:40-44` — `amp` fixture. `:907-957` — existing `reward.combine` tests (style to match). `tests/test_ir_json.py:134-160` — IR-JSON shape assertions (style to match).

---

## Key design decisions (locked, with rationale)

1. **Named reward parsing.** Add `"reward"` to `DECL_KW` so `reward "smr" { … }` parses as a `NamedDecl(keyword="reward", name="smr", …)`. The bare `reward { … }` keeps parsing as a `SectionBlock(keyword="reward", …)` because the section production requires a `block` directly after the keyword while the named production requires a `string_lit` first — Earley disambiguates by the presence/absence of the string. No new AST node type is needed; reuse `NamedDecl`/`SectionBlock`. (Verified the conflict is resolvable: `inhibit` already lives in `DECL_KW` while sharing no section keyword; `reward` will be the first keyword in *both* lists, and Earley handles the lookahead.)
2. **Component IR.** A new `IRRewardComponent(name, canonical_name, role, signal, weight, loc)` where `role ∈ {"reward","suppress"}`, `signal: IRExpr` (the `[0,1]` stream), `weight: IRExpr | None` (an `IRControlRef` or `IRNumberLit`; `None` ⇒ implicit 1.0). Components live on `IRReward.components: tuple[IRRewardComponent, ...]` (default `()`). Hard-gate inhibits stay in `IRProtocol.inhibits` as `IRInhibit` (unchanged).
3. **Suppress-inhibit vs hard-gate disambiguation.** A `inhibit "<n>" { … }` block is a **suppress band** (a reward component, `role="suppress"`) iff it has a `signal` field (and no `action`); it is a **hard gate** (existing `IRInhibit`) iff it has `metric`+`threshold`+`action`. The resolver branches on field presence inside `_resolve_named_decls`. (Spec §1 syntax shows `gate = mute(...)` for hard gates; the **implemented** hard-gate form is `metric`/`threshold`/`action` — see Divergences. Stage 1 keeps the implemented form; suppress bands use `signal`+`weight`.)
4. **Weights reuse controls.** A `weight = w_smr` is resolved by the existing `_resolve_value_expr` path: a `NameRef` to a `controls.<name>` entry becomes an `IRControlRef`; a bare literal becomes an `IRNumberLit`. The evaluator reads weight values from `self._controls` exactly like every other control, so author `default`, `resolve(bindings=)` (control overrides) and `set_control(name, v)` live-tune all work with **zero** new machinery. The weighted composite is recomputed each chunk from the current control values.
5. **`reward.composite` field.** Add `"composite"` to the reward field paths in `_resolve_reward_field`, typed `scalar_stream(DIMENSIONLESS)` (a `[0,1]` value). `IRRewardField` gains nothing structurally — `field_path == "composite"` is the new value. The evaluator computes the composite array and passes it alongside `reward_continuous`/`reward_event`.
6. **Version-aware emission.** `IR_JSON_VERSION` stays `"0.1"`. `ir_to_json_obj` computes a per-protocol version: `"0.2"` iff `ir.reward.components` is non-empty OR `ir.reward.combine == "weighted"`, else `"0.1"`. A v0.1 protocol's emitted dict is byte-identical to today (no `components`/`combine` keys added in the v0.1 branch). The `_emit_reward` v0.2 branch adds `components` + `combine`.
7. **Composite math** (spec §2): `composite = (Σ_r w_r·s_r + Σ_i w_i·(1 − s_i)) / (Σ_r w_r + Σ_i w_i)`, computed sample-wise over the chunk. All-zero weights ⇒ `ResolveError` at resolve time when statically knowable (all weights are literal-0 or controls whose default and full range are 0); otherwise a runtime guard yields a clamped `0.0` and the resolver requires ≥1 component.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/refrain/grammar.lark` | Modify (`DECL_KW`) | Allow `reward "<name>" { … }` to parse as a named decl. |
| `src/refrain/ir.py` | Modify (`IRReward`, add `IRRewardComponent`, `__all__`) | IR shapes for components + the aggregator. |
| `src/refrain/resolver.py` | Modify (`_resolve_named_decls`, `_resolve_inhibit` split, new `_resolve_reward_component`, `_resolve_reward`, `_resolve_reward_field`, `[0,1]` check) | Parse/validate components, weights-as-controls, weighted composite wiring, `reward.composite`/`reward.<name>` access. |
| `src/refrain/eval_.py` | Modify (`_build_pipeline`, `_process_chunk`, `_eval_expr`, `last_taps`, `_rust_bool_tap_keys`) | Compute component signals + weighted composite per chunk; expose `reward.composite`; live weight retune (free via controls). |
| `src/refrain/ir_json.py` | Modify (`ir_to_json_obj`, `_emit_reward`, version selection) | Version-aware v0.1/v0.2 emission. |
| `tests/test_parser_primitives.py` | Add tests | Named-reward / suppress-inhibit parse. |
| `tests/test_resolver.py` | Add tests | Component resolution, weights-as-controls, `[0,1]` reject, composite field, back-compat. |
| `tests/test_eval_composite.py` | Create | Per-chunk weighted-composite numerics + live weight retune (backend="python"). |
| `tests/test_ir_json.py` | Add tests | v0.1 byte-identical back-compat; v0.2 emission shape + version. |

---

### Task 1: Grammar — allow named `reward "<name>" { … }`

**Files:**
- Modify: `src/refrain/grammar.lark:55` (the `DECL_KW` terminal)
- Test: `tests/test_parser_primitives.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parser_primitives.py`:

```python
def test_named_reward_parses_as_named_decl():
    from refrain import ast as A
    src = '''
        protocol "p" {
          reward "smr" { signal = sigmoid("env", midpoint: 6, steepness: 1); weight = w_smr }
          reward { combine = "weighted"; continuous = reward.composite }
        }
    '''
    f = parse(src)
    named = [s for s in f.protocol.body
             if isinstance(s, A.NamedDecl) and s.keyword == "reward"]
    assert len(named) == 1
    assert named[0].name == "smr"
    sections = [s for s in f.protocol.body
                if isinstance(s, A.SectionBlock) and s.keyword == "reward"]
    assert len(sections) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_parser_primitives.py::test_named_reward_parses_as_named_decl -v`
Expected: FAIL — `ParseError: No terminal matches '"'` (named reward not yet allowed).

- [ ] **Step 3: Write minimal implementation**

In `src/refrain/grammar.lark`, change line 55 from:

```
DECL_KW: "input" | "derive" | "threshold" | "inhibit" | "custom"
```

to:

```
DECL_KW: "reward" | "input" | "derive" | "threshold" | "inhibit" | "custom"
```

(`"reward"` stays in `SECTION_KW` on line 52 as well; the bare `reward { … }` form still parses via `section_block` because no `string_lit` follows the keyword.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_parser_primitives.py::test_named_reward_parses_as_named_decl -v`
Expected: PASS

- [ ] **Step 5: Run the full parser suite to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_parser_primitives.py tests/test_parser_examples.py tests/test_parser_literals.py tests/test_parser_composition.py -q`
Expected: PASS (no existing parse broken by adding `reward` to `DECL_KW`).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/grammar.lark tests/test_parser_primitives.py
git commit -m "feat(grammar): parse named reward \"<name>\" blocks as named decls

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: IR — add `IRRewardComponent`, extend `IRReward`

**Files:**
- Modify: `src/refrain/ir.py:230-238` (`IRReward`), insert `IRRewardComponent` before it, update `__all__` at `:321-350`
- Test: `tests/test_resolver.py` (construct-and-read smoke test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_resolver.py`:

```python
def test_ir_reward_component_dataclass_shape():
    from refrain.ir import IRRewardComponent, IRReward, IRNumberLit, IRControlRef
    from refrain.types_ import DIMENSIONLESS
    comp = IRRewardComponent(
        name="smr",
        canonical_name="reward/smr",
        role="reward",
        signal=IRNumberLit(value=0.5, dims=DIMENSIONLESS),
        weight=IRControlRef(target="control/w_smr", dims=DIMENSIONLESS),
    )
    assert comp.role == "reward"
    assert comp.canonical_name == "reward/smr"
    r = IRReward(continuous=None, event=None, combine="weighted", components=(comp,))
    assert r.components[0].name == "smr"
    assert r.combine == "weighted"
    # Back-compat default: empty components tuple, combine "all".
    r0 = IRReward(continuous=None, event=None)
    assert r0.components == ()
    assert r0.combine == "all"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_ir_reward_component_dataclass_shape -v`
Expected: FAIL — `ImportError: cannot import name 'IRRewardComponent'`.

- [ ] **Step 3: Write minimal implementation**

In `src/refrain/ir.py`, insert this dataclass immediately **before** `class IRReward` (before line 230):

```python
@dataclass(frozen=True, slots=True)
class IRRewardComponent:
    """A named reward/suppress component of a weighted composite (v0.2).

    `role` is "reward" (contributes `signal`) or "suppress" (contributes
    `1 - signal`). `signal` is a [0,1] scalar-dimensionless stream. `weight`
    is an IRControlRef or IRNumberLit; `None` means an implicit weight of 1.0.
    """

    name: str
    canonical_name: str    # "reward/<name>"
    role: str              # "reward" | "suppress"
    signal: IRExpr
    weight: IRExpr | None
    loc: Loc | None = None
```

Then change `IRReward` (lines 230-238) to add the `components` field (keep `continuous`/`event`/`combine`/`loc` exactly; add `components` with a default so the back-compat constructor still works):

```python
@dataclass(frozen=True, slots=True)
class IRReward:
    """`reward { continuous?, event?, combine?, components? }`.

    For a single-reward (v0.1) protocol, `components` is empty and `combine`
    is "all". A weighted composite (v0.2) carries one IRRewardComponent per
    named reward/suppress block and `combine == "weighted"`.
    """

    continuous: IRExpr | None
    event: IRExpr | None
    combine: str = "all"    # "all" | "any" | "weighted"
    components: tuple = ()   # tuple[IRRewardComponent, ...]
    loc: Loc | None = None
```

Add `"IRRewardComponent"` to `__all__` (keep it sorted near `"IRReward"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_ir_reward_component_dataclass_shape -v`
Expected: PASS

- [ ] **Step 5: Confirm no resolver/eval/json regression from the IRReward field add**

Run: `.venv/bin/python -m pytest tests/test_resolver.py tests/test_ir_json.py -q`
Expected: PASS (the `components=()` default keeps every existing `IRReward(...)` construction valid).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/ir.py tests/test_resolver.py
git commit -m "feat(ir): add IRRewardComponent and IRReward.components for v0.2 composite

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Resolver — resolve named reward/suppress components

**Files:**
- Modify: `src/refrain/resolver.py:399-417` (`_resolve_named_decls`), `:606-646` (`_resolve_inhibit` — add a suppress-band branch), add `_resolve_reward_component`; add imports for `IRRewardComponent`
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_resolver.py`:

```python
_COMPONENTS_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        w_smr   = percent { default = 1; range = (0, 4) }
        w_theta = percent { default = 0.6; range = (0, 4) }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "smr_env"   { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      derive "theta_env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      reward  "smr"   { signal = sigmoid("smr_env",   midpoint: 6 uV, steepness: 1); weight = w_smr }
      inhibit "theta" { signal = sigmoid("theta_env", midpoint: 8 uV, steepness: 1); weight = w_theta }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_reward_components_resolve_with_roles_and_weights(amp):
    ir = resolve(parse(_COMPONENTS_PROTO), amp)
    comps = {c.name: c for c in ir.reward.components}
    assert set(comps) == {"smr", "theta"}
    assert comps["smr"].role == "reward"
    assert comps["theta"].role == "suppress"
    assert comps["smr"].canonical_name == "reward/smr"
    # Weight resolves to a control ref (weights are ordinary controls).
    assert comps["smr"].weight.target == "control/w_smr"
    assert ir.reward.combine == "weighted"
    # The suppress-band inhibit is NOT a hard-gate IRInhibit.
    assert "theta" not in ir.inhibits
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_reward_components_resolve_with_roles_and_weights -v`
Expected: FAIL — currently `inhibit "theta"` (no `metric`/`threshold`/`action`) raises `ResolveError("inhibit \"theta\" needs metric, threshold, and action")`, and `reward "smr"` (NamedDecl keyword "reward") is silently ignored by `_resolve_named_decls`.

- [ ] **Step 3: Write minimal implementation**

In `src/refrain/resolver.py`, add the import at the existing `from .ir import (` block (line 44):

```python
    IRRewardComponent,
```

Replace the `_resolve_named_decls` dispatch body (lines 399-417) so `reward` named decls and suppress-band inhibits route to component resolution. Replace the whole method with:

```python
    def _resolve_named_decls(self, proto: A.Protocol) -> None:
        for stmt in proto.body:
            if isinstance(stmt, A.NamedDecl):
                if stmt.keyword == "input":
                    self.inputs[stmt.name] = self._resolve_input(stmt)
                    self._topo.append(f"input/{stmt.name}")
                elif stmt.keyword == "derive":
                    self.derives[stmt.name] = self._resolve_derive(stmt)
                    self._topo.append(f"derive/{stmt.name}")
                elif stmt.keyword == "threshold":
                    self.thresholds[stmt.name] = self._resolve_threshold(stmt)
                    self._topo.append(f"threshold/{stmt.name}")
                elif stmt.keyword == "inhibit":
                    fields = self._assignments_dict(stmt.body)
                    if "signal" in fields and "action" not in fields:
                        # Suppress band → a weighted reward component (role=suppress).
                        self._reward_components.append(
                            self._resolve_reward_component(stmt, role="suppress")
                        )
                    else:
                        # Hard gate → existing IRInhibit (v0.1 semantics).
                        self.inhibits[stmt.name] = self._resolve_inhibit(stmt)
                        self._topo.append(f"inhibit/{stmt.name}")
                elif stmt.keyword == "reward":
                    self._reward_components.append(
                        self._resolve_reward_component(stmt, role="reward")
                    )
                elif stmt.keyword == "custom":
                    self.customs[stmt.name] = self._resolve_custom(stmt)
                    self._topo.append(f"custom/{stmt.name}")
```

Add the new method directly after `_resolve_inhibit` (after line 646):

```python
    def _resolve_reward_component(self, decl: A.NamedDecl, *, role: str) -> IRRewardComponent:
        """Resolve a named `reward "<n>"` or suppress-`inhibit "<n>"` component.

        `signal` must type-check to a [0,1] success metric (scalar,
        dimensionless — e.g. sigmoid/linear). `weight` is an ordinary
        numeric control ref or a literal; absent means an implicit 1.0.
        """
        fields = self._assignments_dict(decl.body)
        signal_expr = fields.get("signal")
        if signal_expr is None:
            raise ResolveError(
                f'{decl.keyword} "{decl.name}" component needs a `signal` field',
                loc=decl.loc,
            )
        signal_ir = self._resolve_stream_expr(signal_expr)
        st = _expr_stream_type(signal_ir)
        if not (st.value_kind == "scalar" and st.dimensions == DIMENSIONLESS):
            raise ResolveError(
                f'{decl.keyword} "{decl.name}".signal must be a [0,1] success '
                f"metric (scalar, dimensionless — wrap it in sigmoid/linear); "
                f"got {st}",
                loc=signal_expr.loc,
            )
        weight_expr = fields.get("weight")
        weight_ir = self._resolve_value_expr(weight_expr) if weight_expr is not None else None
        return IRRewardComponent(
            name=decl.name,
            canonical_name=f"reward/{decl.name}",
            role=role,
            signal=signal_ir,
            weight=weight_ir,
            loc=decl.loc,
        )
```

Add `DIMENSIONLESS` to the `from .types_ import (` block (line 74) if not already imported — it is already imported (line 77 region lists `DIMENSIONLESS`); verify and skip if present.

In `_Resolver.__init__` (after `self.controls: dict[...] = {}` at line 149), add the component accumulator:

```python
        # Reward components (named reward / suppress-inhibit) for v0.2 composite.
        self._reward_components: list[IRRewardComponent] = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_reward_components_resolve_with_roles_and_weights -v`
Expected: PASS

> NOTE: `ir.reward.combine == "weighted"` and `ir.reward.components` being populated depend on Task 4 wiring the accumulator into `IRReward`. This test asserts both; run it again after Task 4. If executing tasks strictly in order, expect this test to PASS on the component fields (`name`, `role`, `weight`) but the `combine`/`components` assertions only after Task 4. Split is acceptable: keep this test, and Task 4's Step 4 re-runs it green.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(resolver): resolve named reward/suppress components with [0,1] check

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Resolver — wire components into `IRReward`, accept `combine="weighted"`, require ≥1 positive weight

**Files:**
- Modify: `src/refrain/resolver.py:1162-1192` (`_resolve_reward`)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_resolver.py`:

```python
def test_reward_combine_weighted_accepted(amp):
    ir = resolve(parse(_COMPONENTS_PROTO), amp)
    assert ir.reward.combine == "weighted"
    assert len(ir.reward.components) == 2


_WEIGHTED_NO_COMPONENTS_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_reward_weighted_requires_at_least_one_component(amp):
    with pytest.raises(ResolveError):
        resolve(parse(_WEIGHTED_NO_COMPONENTS_PROTO), amp)


_ALL_ZERO_WEIGHTS_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { w0 = percent { default = 0; range = (0, 0) } }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      reward  "a" { signal = sigmoid("env", midpoint: 6 uV, steepness: 1); weight = w0 }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_reward_weighted_all_zero_weights_rejected(amp):
    with pytest.raises(ResolveError):
        resolve(parse(_ALL_ZERO_WEIGHTS_PROTO), amp)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_reward_combine_weighted_accepted tests/test_resolver.py::test_reward_weighted_requires_at_least_one_component tests/test_resolver.py::test_reward_weighted_all_zero_weights_rejected -v`
Expected: FAIL — `combine="weighted"` currently raises `ResolveError('reward.combine must be "all" or "any"')`; no component wiring exists.

- [ ] **Step 3: Write minimal implementation**

Replace `_resolve_reward` (lines 1162-1192) with:

```python
    def _resolve_reward(self) -> None:
        components = tuple(self._reward_components)
        if self.reward_ast is None:
            self.reward_ir = IRReward(continuous=None, event=None, components=components)
            if components:
                # Components require a `reward { combine = "weighted" }` aggregator.
                raise ResolveError(
                    "named reward/suppress components require a top-level "
                    '`reward { combine = "weighted" }` aggregator block',
                    loc=components[0].loc,
                )
            return
        fields = self._assignments_dict(self.reward_ast.body)
        cont_expr = fields.get("continuous")
        event_expr = fields.get("event")
        combine_expr = fields.get("combine")
        if combine_expr is not None:
            if not isinstance(combine_expr, A.StringLit) or combine_expr.value not in {
                "all", "any", "weighted",
            }:
                raise ResolveError(
                    'reward.combine must be "all", "any", or "weighted"',
                    loc=combine_expr.loc if hasattr(combine_expr, "loc") else None,
                )
            combine = combine_expr.value
        else:
            combine = "all"

        if combine == "weighted":
            if not components:
                raise ResolveError(
                    'reward.combine = "weighted" requires at least one named '
                    "reward/suppress component",
                    loc=self.reward_ast.loc,
                )
            self._check_positive_weight(components)
        elif components:
            raise ResolveError(
                'named reward/suppress components require `combine = "weighted"`',
                loc=self.reward_ast.loc,
            )

        if cont_expr is None and event_expr is None:
            raise ResolveError(
                "reward block must declare `continuous`, `event`, or both",
                loc=self.reward_ast.loc,
            )
        # Set reward_ir before resolving cont/event so reward.composite /
        # reward.<name> member access can see the components.
        self.reward_ir = IRReward(
            continuous=None, event=None, combine=combine, components=components,
            loc=self.reward_ast.loc,
        )
        cont_ir = self._resolve_stream_expr(cont_expr) if cont_expr is not None else None
        event_ir = self._resolve_stream_expr(event_expr) if event_expr is not None else None
        if event_ir is not None and _expr_stream_type(event_ir) != EVENT_STREAM:
            raise ResolveError(
                f"reward.event must produce event_stream, got {_expr_stream_type(event_ir)}",
                loc=event_expr.loc if event_expr else None,
            )
        self.reward_ir = IRReward(
            continuous=cont_ir, event=event_ir, combine=combine,
            components=components, loc=self.reward_ast.loc,
        )
```

Add this helper method to `_Resolver` (place it directly after `_resolve_reward`):

```python
    def _check_positive_weight(self, components: tuple) -> None:
        """At least one component weight must be capable of being > 0.

        Statically reject the case where every component's weight is a
        literal 0 or a control whose default is 0 and whose range upper
        bound is 0 (so it can never be tuned positive). When a weight is a
        control with a positive default or a positive range upper bound,
        treat it as potentially positive and accept. A runtime guard in the
        evaluator handles the dynamic all-zero case.
        """
        def max_weight(comp) -> float:
            w = comp.weight
            if w is None:
                return 1.0  # implicit weight 1.0
            if isinstance(w, IRNumberLit):
                return float(w.value)
            if isinstance(w, IRControlRef):
                ctrl = self.controls.get(w.target.split("/", 1)[-1])
                if ctrl is None:
                    return 1.0
                hi = ctrl.range_high
                default = ctrl.default
                if isinstance(hi, IRNumberLit):
                    return float(hi.value)
                if isinstance(default, IRNumberLit):
                    return float(default.value)
                return 1.0
            return 1.0

        if all(max_weight(c) <= 0.0 for c in components):
            raise ResolveError(
                "reward composite has no positive weight: at least one "
                "component must have a weight that can be > 0",
                loc=components[0].loc,
            )
```

- [ ] **Step 4: Run test to verify it passes (incl. Task 3's component test)**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_reward_combine_weighted_accepted tests/test_resolver.py::test_reward_weighted_requires_at_least_one_component tests/test_resolver.py::test_reward_weighted_all_zero_weights_rejected tests/test_resolver.py::test_reward_components_resolve_with_roles_and_weights -v`
Expected: PASS (all four).

- [ ] **Step 5: Confirm existing combine tests still pass**

Run: `.venv/bin/python -m pytest tests/test_resolver.py -k "combine" -q`
Expected: PASS (`test_reward_combine_parsed`, `test_reward_combine_defaults_all`, `test_reward_combine_invalid_fails` — note the invalid test uses `"most"` which is still rejected).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(resolver): accept combine=weighted, wire components, require positive weight

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Resolver — `reward.composite` and `reward.<name>` member access

**Files:**
- Modify: `src/refrain/resolver.py:1396-1421` (`_resolve_reward_field`)
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_resolver.py`:

```python
def test_reward_composite_member_access_resolves(amp):
    ir = resolve(parse(_COMPONENTS_PROTO), amp)
    # output.audio_gain = reward.composite
    field = ir.output["audio_gain"]
    assert isinstance(field, IRRewardField)
    assert field.field_path == "composite"
    assert field.stream_type.value_kind == "scalar"


_COMPONENT_NAME_ACCESS_PROTO = _COMPONENTS_PROTO.replace(
    "output { audio_gain = reward.composite }",
    "output { audio_gain = reward.composite; video_clarity = reward.smr.signal }",
)


def test_reward_component_signal_access_resolves(amp):
    ir = resolve(parse(_COMPONENT_NAME_ACCESS_PROTO), amp)
    field = ir.output["video_clarity"]
    assert isinstance(field, IRRewardField)
    assert field.field_path == "smr.signal"


def test_reward_unknown_component_access_rejected(amp):
    bad = _COMPONENTS_PROTO.replace("reward.composite }", "reward.nope.signal }")
    with pytest.raises(ResolveError):
        resolve(parse(bad), amp)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_reward_composite_member_access_resolves tests/test_resolver.py::test_reward_component_signal_access_resolves tests/test_resolver.py::test_reward_unknown_component_access_rejected -v`
Expected: FAIL — `_resolve_reward_field` raises `ResolveError("unknown reward field path 'composite'")`.

- [ ] **Step 3: Write minimal implementation**

In `_resolve_reward_field` (lines 1396-1421), insert the `composite` and `<name>.signal` cases **before** the final `raise ResolveError(f"unknown reward field path …")`. Replace the method body's tail (after the `("event", "holds")` branch) with:

```python
        if parts == ("composite",):
            if not self.reward_ir.components and self.reward_ir.combine != "weighted":
                raise ResolveError(
                    "reward.composite is only available with named components "
                    'and combine = "weighted"',
                    loc=loc,
                )
            return IRRewardField(
                field_path="composite",
                stream_type=scalar_stream(DIMENSIONLESS),
                loc=loc,
            )
        if len(parts) == 2 and parts[1] == "signal":
            comp_names = {c.name for c in self.reward_ir.components}
            if parts[0] not in comp_names:
                raise ResolveError(
                    f"unknown reward component {parts[0]!r}; "
                    f"declared components: {sorted(comp_names)}",
                    loc=loc,
                )
            return IRRewardField(
                field_path=f"{parts[0]}.signal",
                stream_type=scalar_stream(DIMENSIONLESS),
                loc=loc,
            )
        raise ResolveError(
            f"unknown reward field path {'.'.join(parts)!r}",
            loc=loc,
        )
```

`scalar_stream` and `DIMENSIONLESS` are already imported (lines 74-86).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_resolver.py::test_reward_composite_member_access_resolves tests/test_resolver.py::test_reward_component_signal_access_resolves tests/test_resolver.py::test_reward_unknown_component_access_rejected -v`
Expected: PASS

- [ ] **Step 5: Confirm existing reward member-access tests pass**

Run: `.venv/bin/python -m pytest tests/test_resolver.py -k "reward" -q`
Expected: PASS (incl. `test_reward_member_access_resolves`, `test_reward_event_without_dwell_raises`).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(resolver): resolve reward.composite and reward.<name>.signal access

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Evaluator — compute component signals + weighted composite per chunk

**Files:**
- Modify: `src/refrain/eval_.py:481-484` (`_build_pipeline` reward-instantiation), `:639-698` (`_process_chunk`), `:974-1012` (`_eval_expr` `IRRewardField`)
- Test: `tests/test_eval_composite.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_eval_composite.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Weighted-composite evaluation (Stage 1, backend='python')."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

AMP_PATH = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json"


@pytest.fixture(scope="module")
def amp():
    return load_amp_profile(AMP_PATH)


# One reward (smr, weight 1), one suppress (theta, weight 1). Identity-ish
# derives so the sigmoids are driven by the raw input directly.
_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        w_smr   = percent { default = 1; range = (0, 4); live_tunable = true }
        w_theta = percent { default = 1; range = (0, 4); live_tunable = true }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "smr_env"   { from = "raw"; pipeline = [rectify()] }
      derive "theta_env" { from = "raw"; pipeline = [rectify()] }
      reward  "smr"   { signal = sigmoid("smr_env",   midpoint: 0, steepness: 1000); weight = w_smr }
      inhibit "theta" { signal = sigmoid("theta_env", midpoint: 0, steepness: 1000); weight = w_theta }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_composite_is_weighted_average_of_success(amp):
    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    # Positive input → smr_env rectified > 0 → smr sigmoid ≈ 1; theta sigmoid ≈ 1
    # → suppress contributes (1 - 1) = 0. composite = (1*1 + 1*0)/(1+1) = 0.5.
    chunk = np.full((64, 2), 5.0, dtype=np.float64)
    ev.step_chunk(chunk)
    comp = ev.last_streams()["reward.composite"]
    assert np.allclose(comp, 0.5, atol=1e-9)


def test_composite_reweight_via_set_control(amp):
    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    chunk = np.full((64, 2), 5.0, dtype=np.float64)
    # Drop the suppress weight to 0 → composite = (1*1)/(1) = 1.0.
    ev.set_control("w_theta", 0.0)
    ev.step_chunk(chunk)
    comp = ev.last_streams()["reward.composite"]
    assert np.allclose(comp, 1.0, atol=1e-9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_composite.py -v`
Expected: FAIL — `reward.composite` is unknown to `_eval_expr`/`last_streams`; composite is never computed (`KeyError: 'reward.composite'` or a `ValueError` for the unhandled field path).

- [ ] **Step 3: Write minimal implementation**

In `src/refrain/eval_.py`, **instantiate component signal impls** in `_build_pipeline`. After the reward `continuous`/`event` instantiation block (after line 484), add:

```python
        # Component signals (named reward / suppress-inhibit, v0.2 composite).
        for comp in self.ir.reward.components:
            self._instantiate_expr(comp.signal)
```

In `_process_chunk`, compute the composite. After the reward `continuous`/`event` block (after line 698, before `muted = self._compute_muted(...)`), add:

```python
        # Weighted composite (v0.2). Recomputed each chunk from current
        # control values, so a live set_control on any weight moves it.
        reward_composite: np.ndarray | None = None
        reward_component_signals: dict[str, np.ndarray] = {}
        if self.ir.reward.components:
            num = np.zeros(actual_chunk_size, dtype=np.float64)
            weight_sum = np.zeros(actual_chunk_size, dtype=np.float64)
            for comp in self.ir.reward.components:
                signal = np.clip(
                    self._eval_expr(
                        comp.signal, stream_values, control_chunks_cache,
                        actual_chunk_size,
                    ),
                    0.0, 1.0,
                )
                reward_component_signals[comp.name] = signal
                w = self._component_weight_chunk(comp, control_chunks_cache, actual_chunk_size)
                success = signal if comp.role == "reward" else (1.0 - signal)
                num += w * success
                weight_sum += w
            # Runtime all-zero-weight guard: composite is 0 where no weight.
            reward_composite = np.where(weight_sum > 0.0, num / np.where(weight_sum > 0.0, weight_sum, 1.0), 0.0)
```

Thread `reward_composite` and `reward_component_signals` into the output-binding eval loop. Change the `self._eval_expr(expr, ...)` call inside the `for channel, expr in self.ir.output.items():` loop (line 710) to pass them:

```python
            values = self._eval_expr(
                expr,
                stream_values, control_chunks_cache, actual_chunk_size,
                reward_continuous=reward_continuous,
                reward_event=reward_event,
                reward_composite=reward_composite,
                reward_component_signals=reward_component_signals,
            )
```

Add a helper method to `Evaluator` (place after `_eval_reward_event`):

```python
    def _component_weight_chunk(
        self, comp, control_chunks: dict[str, np.ndarray], chunk_size: int
    ) -> np.ndarray:
        """Resolve a component's weight to a per-sample chunk. A control-ref
        weight reads the live control value (so set_control retunes it); a
        literal weight is a constant; absent weight is implicit 1.0."""
        w = comp.weight
        if w is None:
            return np.ones(chunk_size, dtype=np.float64)
        if isinstance(w, IRControlRef):
            return control_chunks[w.target]
        if isinstance(w, IRNumberLit):
            return np.full(chunk_size, float(w.value), dtype=np.float64)
        # Fallback: evaluate as a stream expression.
        return self._eval_expr(w, {}, control_chunks, chunk_size)
```

Extend `_eval_expr`'s signature (line 974) and the `IRRewardField` branch (lines 999-1012). Add the two new keyword args to the signature:

```python
        reward_continuous: np.ndarray | None = None,
        reward_event: impls.DwellResult | None = None,
        reward_composite: np.ndarray | None = None,
        reward_component_signals: dict[str, np.ndarray] | None = None,
```

In the `IRRewardField` branch, add `composite` and `<name>.signal` handling before the final `raise`:

```python
            if expr.field_path == "composite":
                if reward_composite is None:
                    return np.zeros(chunk_size, dtype=np.float64)
                return reward_composite
            if expr.field_path.endswith(".signal"):
                name = expr.field_path[: -len(".signal")]
                if reward_component_signals is None or name not in reward_component_signals:
                    return np.zeros(chunk_size, dtype=np.float64)
                return reward_component_signals[name]
```

Forward the two new args through every recursive `self._eval_expr(...)` call inside `_eval_expr` (the `IRBinaryOp`, `IRConditional`, `IRCall` branches) — add `reward_composite=reward_composite, reward_component_signals=reward_component_signals` to each, mirroring how `reward_continuous`/`reward_event` are already threaded. Also forward them in `_eval_call`'s signature and its inner `_eval_expr` loop the same way.

Add `IRControlRef`, `IRNumberLit` to the `from .ir import (` block in `eval_.py` (lines 32-48) if not already present — `IRControlRef` and `IRNumberLit` are already imported there; verify and skip.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_eval_composite.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Confirm no eval regression**

Run: `.venv/bin/python -m pytest tests/test_eval_lifecycle.py tests/test_eval_control_refs.py tests/test_eval_record_streams.py tests/test_eval_validation.py -q`
Expected: PASS (the new keyword args default to `None`, so single-reward protocols are unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/eval_.py tests/test_eval_composite.py
git commit -m "feat(eval): compute weighted reward.composite per chunk with live weight retune

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Evaluator — expose composite + component signals in `last_streams` / `last_taps`

**Files:**
- Modify: `src/refrain/eval_.py:738-750` (`record_streams` capture), `:779-859` (`_capture_taps`)
- Test: `tests/test_eval_composite.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eval_composite.py`:

```python
def test_composite_exposed_in_taps_and_streams(amp):
    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    ev.step_chunk(np.full((64, 2), 5.0, dtype=np.float64))
    taps = ev.last_taps()
    assert "reward/composite" in taps
    assert abs(taps["reward/composite"] - 0.5) < 1e-9
    assert "reward/component[smr]" in taps
    assert "reward/component[theta]" in taps
    streams = ev.last_streams()
    assert "reward.composite" in streams
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_composite.py::test_composite_exposed_in_taps_and_streams -v`
Expected: FAIL — `"reward/composite"` not in `last_taps()`; `"reward.composite"` may be present (Task 6) but the component taps are missing — at minimum the `reward/composite` tap and `reward/component[...]` assertions fail.

- [ ] **Step 3: Write minimal implementation**

In `_process_chunk`, the `record_streams` capture block (lines 738-750) — add composite + component signals. After the existing `if reward_event is not None:` capture block and before the `for channel, (out_arr, _is_event)` loop, add:

```python
            if reward_composite is not None:
                captured["reward.composite"] = np.asarray(reward_composite).copy()
            for cname, csig in reward_component_signals.items():
                captured[f"reward.component.{cname}"] = np.asarray(csig).copy()
```

Pass the new values into `_capture_taps`. Change the `self._capture_taps(...)` call (lines 729-737) to add:

```python
            reward_composite=reward_composite,
            reward_component_signals=reward_component_signals,
```

Extend `_capture_taps`'s signature (lines 779-789) with:

```python
        reward_composite: np.ndarray | None = None,
        reward_component_signals: dict[str, np.ndarray] | None = None,
```

In `_capture_taps`, after the existing `reward_continuous` tap block (after line 831), add:

```python
        if reward_composite is not None and reward_composite.size:
            taps["reward/composite"] = float(reward_composite[-1])
        if reward_component_signals:
            for cname, csig in reward_component_signals.items():
                if csig.size:
                    taps[f"reward/component[{cname}]"] = float(csig[-1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_eval_composite.py::test_composite_exposed_in_taps_and_streams -v`
Expected: PASS

- [ ] **Step 5: Confirm tap suite unaffected**

Run: `.venv/bin/python -m pytest tests/test_eval_taps.py -q`
Expected: PASS (single-reward protocols emit no `reward/composite` tap; the new keys appear only when components exist).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/eval_.py tests/test_eval_composite.py
git commit -m "feat(eval): expose reward.composite and component signals in taps/streams

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: IR-JSON — version-aware v0.1/v0.2 emission

**Files:**
- Modify: `src/refrain/ir_json.py:288-292` (`_emit_reward`), `:333-369` (`ir_to_json_obj`)
- Test: `tests/test_ir_json.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ir_json.py`:

```python
_V01_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''

_V02_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        w_smr   = percent { default = 1; range = (0, 4) }
        w_theta = percent { default = 0.6; range = (0, 4) }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "smr_env"   { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      derive "theta_env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      reward  "smr"   { signal = sigmoid("smr_env",   midpoint: 6 uV, steepness: 1); weight = w_smr }
      inhibit "theta" { signal = sigmoid("theta_env", midpoint: 8 uV, steepness: 1); weight = w_theta }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_single_reward_protocol_emits_v01_unchanged():
    ir = resolve(parse(_V01_PROTO), _AMP)
    obj = ir_to_json_obj(ir)
    assert obj["refrain_ir_version"] == "0.1"
    # v0.1 reward shape: exactly continuous + event keys, no components/combine.
    assert set(obj["reward"]) == {"continuous", "event"}


def test_weighted_protocol_emits_v02_with_components():
    ir = resolve(parse(_V02_PROTO), _AMP)
    obj = ir_to_json_obj(ir)
    assert obj["refrain_ir_version"] == "0.2"
    assert obj["reward"]["combine"] == "weighted"
    comps = {c["name"]: c for c in obj["reward"]["components"]}
    assert set(comps) == {"smr", "theta"}
    assert comps["smr"]["role"] == "reward"
    assert comps["theta"]["role"] == "suppress"
    # Weight is emitted as a control_ref node (weights are controls).
    assert comps["smr"]["weight"]["node"] == "control_ref"
    assert comps["smr"]["signal"]["callee"] == "sigmoid"
    # The composite is reachable via the continuous binding as a reward_field.
    assert obj["reward"]["continuous"]["node"] == "reward_field"
    assert obj["reward"]["continuous"]["field_path"] == "composite"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ir_json.py::test_single_reward_protocol_emits_v01_unchanged tests/test_ir_json.py::test_weighted_protocol_emits_v02_with_components -v`
Expected: FAIL — `test_weighted_protocol_emits_v02_with_components` fails: version is always `"0.1"`, and `_emit_reward` emits only `continuous`/`event`. (The v0.1 test should pass already; that is the back-compat guard.)

- [ ] **Step 3: Write minimal implementation**

In `src/refrain/ir_json.py`, add a version selector helper (after `IR_JSON_VERSION = "0.1"`, line 53):

```python
def _protocol_ir_version(ir: IRProtocol) -> str:
    """Lowest IR-JSON version that represents this protocol.

    A protocol that uses no named components and no weighted combine emits
    v0.1 (byte-identical to the pre-v0.2 emitter); anything using the new
    composite features emits v0.2.
    """
    if ir.reward.components or ir.reward.combine == "weighted":
        return "0.2"
    return IR_JSON_VERSION
```

Replace `_emit_reward` (lines 288-292) with a version-aware emitter:

```python
def _emit_reward(r: IRReward, ctx: _EmitCtx, version: str) -> dict:
    base = {
        "continuous": _emit_expr(r.continuous, ctx) if r.continuous is not None else None,
        "event": _emit_expr(r.event, ctx) if r.event is not None else None,
    }
    if version == "0.1":
        # Byte-identical to the pre-v0.2 emitter: no components/combine keys.
        return base
    base["combine"] = r.combine
    base["components"] = [
        {
            "name": c.name,
            "canonical_name": c.canonical_name,
            "role": c.role,
            "signal": _emit_expr(c.signal, ctx),
            "weight": _emit_expr(c.weight, ctx) if c.weight is not None else None,
        }
        for c in r.components
    ]
    return base
```

In `ir_to_json_obj` (lines 333-369), compute the version once and thread it through. Change the `refrain_ir_version` line and the `reward` line:

```python
    version = _protocol_ir_version(ir)
    ...
        "refrain_ir_version": version,
    ...
        "reward": _emit_reward(ir.reward, ctx, version),
```

(Place `version = _protocol_ir_version(ir)` right after `ctx = _EmitCtx(...)` is built, before the `return {` dict.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ir_json.py::test_single_reward_protocol_emits_v01_unchanged tests/test_ir_json.py::test_weighted_protocol_emits_v02_with_components -v`
Expected: PASS

- [ ] **Step 5: Confirm the v0.1 schema drift gate is unaffected**

Run: `.venv/bin/python -m pytest tests/test_ir_json.py tests/test_ir_json_schema.py -q`
Expected: PASS. The golden `*.ir.json` fixtures are all single-reward → still emit `"0.1"` with no `components`/`combine` keys, so they still validate against `ir-json-v0.1.schema.json` (`refrain_ir_version` const `"0.1"`).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/ir_json.py tests/test_ir_json.py
git commit -m "feat(ir-json): version-aware v0.1/v0.2 emission for weighted composites

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Back-compat byte-identity guard + full-suite green

**Files:**
- Test: `tests/test_ir_json.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ir_json.py`:

```python
def test_v01_emission_byte_identical_for_examples():
    # Every shipped example is single-reward (v0.1). Their emitted JSON must
    # be unchanged by the v0.2 work: version "0.1", reward has exactly
    # continuous/event keys, and the doc validates against the v0.1 schema.
    import jsonschema
    schema_path = REPO / "refrain-core" / "schema" / "ir-json-v0.1.schema.json"
    validator = jsonschema.Draft202012Validator(json.loads(schema_path.read_text()))
    for path in sorted(EXAMPLES.glob("*.refrain")):
        ir = resolve(parse_file(path), _AMP)
        obj = ir_to_json_obj(ir)
        assert obj["refrain_ir_version"] == "0.1", path.name
        assert set(obj["reward"]) == {"continuous", "event"}, path.name
        errors = list(validator.iter_errors(obj))
        assert not errors, f"{path.name}: {[e.message for e in errors]}"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `.venv/bin/python -m pytest tests/test_ir_json.py::test_v01_emission_byte_identical_for_examples -v`
Expected: PASS if all examples resolve against the Q21 amp; if an example needs a different amp/bindings it will error at resolve — in that case narrow the glob to the examples that resolve cleanly under `_AMP` (the smr_cz / alpha_theta / othmer families do). This test is the back-compat exit criterion from the spec ("v0.1 single-reward protocol round-trips byte-identically").

- [ ] **Step 3: (If needed) adjust the example set**

If a specific example requires placement bindings, skip it explicitly with a comment rather than weakening the assertion:

```python
        if path.name in {"othmer_ilf_t3t4.refrain"}:  # needs a `set`/placement binding
            continue
```

(Only add this if Step 2 surfaced a resolve error for that file. Do not pre-emptively skip.)

- [ ] **Step 4: Run the entire test suite (python backend)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all existing tests plus the new composite/resolver/ir-json/parser tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ir_json.py
git commit -m "test(ir-json): guard v0.1 byte-identity for shipped examples

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

### 1. Spec coverage (each spec requirement → task)

| Spec requirement | Task |
|---|---|
| §1 Named `reward "<name>"` blocks parse | Task 1 (grammar), Task 3 (resolve) |
| §1 Bare `reward { }` remains the aggregator (back-compat) | Task 1 (section form kept), Task 4 |
| §1/§2 Weighted composite = weighted average of `[0,1]` success | Task 6 (eval math) |
| §2 reward components contribute `s`, suppress contribute `1−s` | Task 6 (`role` branch) |
| §3 Weights are ordinary numeric controls (default + bindings + set_control) | Task 3 (weight→IRControlRef), Task 6 (`_component_weight_chunk` reads live `_controls`) |
| §2 component signals type-check to `[0,1]`; non-`[0,1]` is a ResolveError | Task 3 (`scalar`+`DIMENSIONLESS` check) |
| §2 all-zero weights → resolve error / runtime guard | Task 4 (`_check_positive_weight`), Task 6 (runtime `np.where(weight_sum>0…)`) |
| §2 `reward.composite` reachable by `event`/`continuous`/`output` | Task 5 (resolve), Task 6 (eval) |
| §2 `reward.<name>.signal` exposes a component | Task 5 (resolve), Task 6 (eval), Task 7 (tap) |
| §3 `combine = "weighted"` (new); `all`/`any` kept | Task 4 |
| §4 Hard-gate inhibits keep v0.1 semantics, gate whole composite | Task 3 (field-presence split keeps `IRInhibit`); existing `_compute_muted` gates output/composite |
| §5 live `set_control` on a weight moves the composite | Task 6 (`test_composite_reweight_via_set_control`) |
| IR: `IRReward` carries named components + combine; `IRRewardField` gains composite path | Task 2, Task 5 |
| Back-compat: v0.1 single-reward → v0.1 IR-JSON byte-identical; golden vectors stay green; gate's schema step unaffected | Task 8 (version-aware), Task 9 (byte-identity + schema validation) |
| `IR_JSON_VERSION` version-aware per protocol; lowest version representing the protocol | Task 8 (`_protocol_ir_version`) |

**Explicitly OUT of Stage 1 (correctly absent):** Rust core, v0.2 schema file, drift-gate v0.2 fixtures (Stage 2); `combine="independent"`, fan-out/set-replication integration (Stage 3). The version bump to `0.6.0` is deferred to end of Stage 3 — noted at the top and not in any task.

### 2. Placeholder scan

No "TBD"/"TODO"/"implement later"/"add error handling" placeholders. Every code step shows the actual code. The one conditional step (Task 9 Step 3) is gated on an observed resolve error and includes the exact skip code; it is not a vague instruction. Task 3's Step 4 NOTE flags an intentional cross-task dependency (component accumulator is read by Task 4) and tells the executor to re-run the test after Task 4 — this is sequencing guidance, not a placeholder.

### 3. Type consistency

- `IRRewardComponent` fields (`name`, `canonical_name`, `role`, `signal`, `weight`, `loc`) are identical across Task 2 (def), Task 3 (construct), Task 6/7 (`comp.signal`, `comp.role`, `comp.weight`, `comp.name`).
- `IRReward.components` (tuple) and `IRReward.combine` (str incl. `"weighted"`) consistent across Tasks 2, 4, 5, 6, 8.
- `IRRewardField.field_path` new values `"composite"` and `"<name>.signal"` consistent: produced in Task 5, consumed in Task 6 (`endswith(".signal")`, `== "composite"`) and Task 8 (`field_path == "composite"`).
- Evaluator keyword args `reward_composite` / `reward_component_signals` are threaded with identical names through `_process_chunk`, `_eval_expr`, `_eval_call`, `_capture_taps`, and `_component_weight_chunk`.
- Tap key conventions match existing code: `reward/composite` and `reward/component[<name>]` use the slash form of `last_taps` (cf. `reward/continuous` at `eval_.py:831`); `reward.composite` and `reward.component.<name>` use the dot form of `last_streams` (cf. `reward.continuous` at `eval_.py:744`).
- The `[0,1]` check uses `StreamType.value_kind == "scalar"` and `dimensions == DIMENSIONLESS`, matching `sigmoid`/`linear` outputs (`primitives.py:182-187`). No invented `probability` type.

### Notes for the executor

- **Divergence from the spec's syntax (hard gates):** the spec §1 example writes a hard gate as `inhibit "emg" { signal = "emg_env"; gate = mute(above: 20 uV) }`. The **implemented** hard-gate form is `inhibit "<n>" { metric = …; threshold = …; action = mute(release: …) }` (see `resolver.py:606-646` and `examples/*.refrain`). Stage 1 does **not** add the `gate =` sugar; it keeps the existing hard-gate form and introduces only the suppress-band form (`signal` + `weight`). Disambiguation is by field presence: `signal` present and `action` absent ⇒ suppress band; `metric`/`threshold`/`action` ⇒ hard gate. This is the only place the real code diverges from the spec's literal syntax.
- **`LinearImpl` is not clamped** (`primitive_impls.py:576-584`), so Task 6 clamps each component signal to `[0,1]` at composite time (`np.clip(..., 0.0, 1.0)`) as a defensive runtime guarantee in addition to the resolver's static `[0,1]` type check. `sigmoid` is already in range; the clamp is a no-op for it.
- **Stage-1 risk — `set_control` weight forwarding:** weights work via `control_chunks_cache` read fresh each chunk (Task 6), *not* via `impl.update_control`. So `set_control` moves the composite even though no DSP impl consumes the weight. The `test_composite_reweight_via_set_control` test pins this. (`set_control` still updates `self._controls`, which feeds `control_chunks_cache` on the next `step_chunk` — `eval_.py:625-628`, `:966`.)
- **Stage-1 risk — Rust parity is out of scope but `Evaluator.live(backend="auto")` may pick Rust.** All new tests pin `backend="python"` explicitly. A v0.2 protocol fed to the Rust core would fail to deserialize (the Rust core only knows v0.1) — that is Stage 2's job. Do not run the composite tests under `REFRAIN_EVAL_BACKEND=rust`.
- **Stage-1 risk — grammar ambiguity.** Adding `"reward"` to `DECL_KW` while it remains in `SECTION_KW` relies on Earley's lookahead (string-lit ⇒ named decl; `{` ⇒ section). Task 1 Step 5 runs the full parser suite to catch any regression; if Earley reports an ambiguity, the fallback is a dedicated `reward_decl: "reward" string_lit block` production instead of overloading `DECL_KW` (the AST/resolver code is unaffected because both forms still build a `NamedDecl(keyword="reward", …)` — adjust the transformer's `named_decl`/new `reward_decl` method accordingly).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-25-reward-engine-v0.2-stage1.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
