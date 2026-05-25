# Placement Mode 2 (multi-site): coherence pairs + per-site replication — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two `placement` kinds — `pair` (coherence pairs, legs via `.a`/`.b` member access) and `set` (N sites) — plus Mode 2a per-site replication (implicit fan-out with author-selectable `reward.combine = "all"|"any"`).

**Architecture:** Pure front-end, resolve-time. `pair`/`set` extend the shipped `placement` machinery. Mode 2a is an **AST-level fan-out pre-pass**: when a bound `set` placement feeds an input montage, the protocol AST is rewritten to N per-site inputs + replicated dependent derives/thresholds + a combined reward condition, then resolved normally. Placement controls are resolve-time-only (emitter already omits them) → **IR-JSON schema v0.1 and the Rust core are unchanged**.

**Tech Stack:** Python (Lark parser, resolver, dataclass IR), pytest. Spec: `docs/superpowers/specs/2026-05-25-placement-mode2-multisite-design.md`. Builds on the shipped Modes 1&3 (`_resolve_placement_control`, `_bound_placement_value`, `_substitute_placement_args`, `_parse_channel_list`).

**Branch:** `placement-mode2` (off `main` @ 1cc7085 / tag v0.3.0).

**Run tests:** `VIRTUAL_ENV=.venv .venv/bin/python -m pytest <path> -q` from the worktree root. `_AMP` fixture pattern: `load_amp_profile(<repo>/src/refrain/amp_profiles/q21.json)` (q21 has C3/Cz/C4/Pz/F3/F4/T3/T4 etc.; confirm channels exist before using in tests).

---

## File Structure

- `src/refrain/resolver.py` — `pair`/`set` in `_resolve_placement_control` + `_control_kind_dims`; `_bound_placement_value` (pair → 2-tuple, set → list); `_substitute_placement_args` (pair-leg member access); `_parse_channel_list` (pair both legs); `_resolve_reward` (`combine` field); the fan-out pre-pass invocation.
- `src/refrain/fanout.py` (new) — the AST-level set-replication transform (one focused module; keeps `resolver.py` from growing a large new responsibility).
- `src/refrain/ir.py` — `IRControl.set_min`/`set_max` (defaulted).
- `pyproject.toml` ×2, `CHANGELOG.md`, `docs/SPEC.md` — version 0.4.0 + docs.
- Tests: `tests/test_resolver.py` (pair, set, scoping errors), `tests/test_fanout.py` (new — replication), `tests/test_ir_json.py` (omission + flat-IR shape).

---

## Task 1: `kind="pair"` declaration + leg binding (coherence pairs)

**Files:** Modify `src/refrain/resolver.py` (`_control_kind_dims` ~1567, `_resolve_placement_control` ~717, `_bound_placement_value` ~843, `_substitute_placement_args` ~438, `_parse_channel_list` ~346); Test `tests/test_resolver.py`.

- [ ] **Step 1: Write failing tests**

```python
_PAIR_PROTO = '''
    protocol "coh" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { coh = placement { kind = "pair"; default = ("C3","C4"); allowed = [("C3","C4"),("F3","F4")] } }
      requires { channels = [coh] }
      input "a" { montage = referential(active: coh.a, reference: "linked_ears") }
      input "b" { montage = referential(active: coh.b, reference: "linked_ears") }
      derive "c" { from = "a"; pipeline = [smooth(tau: 100 ms)] }
      reward { continuous = sigmoid("c", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''

def _active_of(ir, input_name):
    call = ir.inputs[input_name].montage
    return next(x.value.value for x in call.args if x.name == "active")

def test_pair_legs_bind_default(amp):
    ir = resolve(parse(_PAIR_PROTO), amp)
    assert _active_of(ir, "a") == "C3"
    assert _active_of(ir, "b") == "C4"
    assert set(ir.requires.channels) == {"C3", "C4"}

def test_pair_legs_bind_override(amp):
    ir = resolve(parse(_PAIR_PROTO), amp, bindings={"coh": ("F3", "F4")})
    assert _active_of(ir, "a") == "F3"
    assert _active_of(ir, "b") == "F4"

def test_pair_not_in_allowed_fails(amp):
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_PAIR_PROTO), amp, bindings={"coh": ("Cz", "Pz")})
```

(The `amp` fixture already exists in `tests/test_resolver.py`. Verify q21 has C3/C4/F3/F4; if not, pick channels it has.)

- [ ] **Step 2: Run to verify they fail**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k pair`
Expected: FAIL — `_control_kind_dims`/`_resolve_placement_control` reject `kind="pair"`, and `coh.a` member access isn't substituted.

- [ ] **Step 3: Accept `pair` in declaration**

In `_control_kind_dims` add `if kind == "placement"` already returns DIMENSIONLESS for all placement kinds — no change needed there (kind is the block name `placement`; `pair` is the inner `kind=` field). In `_resolve_placement_control` (~717), extend the `kind` validation to accept `"pair"` (currently `("active","bipolar")`): change to `("active","bipolar","pair","set")`. For `pair`, reuse the existing bipolar 2-tuple parsing in `_parse_placement_value`/`_parse_placement_allowed` (a `pair` value is a 2-tuple like bipolar; `allowed` is a list of 2-tuples). `default_placement` = the `(a,b)` tuple.

- [ ] **Step 4: Resolve `pair` leg member access in montage slots**

Extend `_substitute_placement_args` (~438): in addition to the active-NameRef and `bipolar(pair:)` cases, detect an arg whose value is `A.MemberAccess` whose base is an `A.NameRef` naming a `kind="pair"` placement and whose member is `"a"` or `"b"`. Rewrite it to `A.StringLit(leg)` where `leg = self._bound_placement_value(name)[0 if member=="a" else 1]`. (Use `_collect_member_path` or inspect `A.MemberAccess.base`/`.member` — match the AST shape; read `ast.py` for `MemberAccess`.) Extend `_bound_placement_value` (~843) to handle `kind == "pair"`: return the bound `(a,b)` 2-tuple (override or `default_placement`), validate it ∈ `allowed` (reuse `_check_placement_in_allowed`) and device-check both legs.

- [ ] **Step 5: `requires.channels=[coh]` expands both legs**

`_bound_placement_value` for `pair` returns a 2-tuple; `_parse_channel_list` (~346) already handles str (active→append) vs tuple (bipolar→extend) — confirm the tuple branch `extend`s both legs for `pair` too (it should, since pair also returns a 2-tuple).

- [ ] **Step 6: Run tests to verify they pass**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k pair`
Expected: PASS (3 tests).

- [ ] **Step 7: Full resolver suite green**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolver.py
git commit -m "feat(placement): kind=pair (coherence pairs) with .a/.b leg member access

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `kind="set"` declaration + binding + validation

**Files:** Modify `src/refrain/resolver.py` (`_resolve_placement_control`, `_bound_placement_value`), `src/refrain/ir.py` (`IRControl`); Test `tests/test_resolver.py`.

- [ ] **Step 1: Write failing tests**

```python
_SET_DECL = '''
    protocol "ms" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4","Pz"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''

def test_set_control_resolves(amp):
    ir = resolve(parse(_SET_DECL), amp)
    c = ir.controls["sites"]
    assert c.kind == "set"
    assert c.allowed == ("C3","Cz","C4","Pz")
    assert c.set_min == 1 and c.set_max == 3
    assert c.default_placement == ("Cz",)

def test_set_count_below_min_fails(amp):
    src = _SET_DECL.replace("min = 1", "min = 2")
    with pytest.raises(ResolveError, match="at least|min"):
        resolve(parse(src), amp, bindings={"sites": ["Cz"]})

def test_set_count_above_max_fails(amp):
    with pytest.raises(ResolveError, match="at most|max"):
        resolve(parse(_SET_DECL), amp, bindings={"sites": ["C3","Cz","C4","Pz"]})

def test_set_member_not_in_allowed_fails(amp):
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_SET_DECL), amp, bindings={"sites": ["C3","Fz"]})
```

- [ ] **Step 2: Run to verify they fail**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k set`
Expected: FAIL — `IRControl` has no `set_min`/`set_max`; set parsing/validation absent.

- [ ] **Step 3: Add `set_min`/`set_max` to `IRControl`**

In `src/refrain/ir.py`, add to `IRControl` (defaulted, frozen/slots): `set_min: int | None = None`, `set_max: int | None = None`.

- [ ] **Step 4: Parse + validate `set` in `_resolve_placement_control`**

For `kind == "set"`: `default` is an `A.Array` of string literals → `default_placement` = tuple of channel strings; `allowed` is an `A.Array` of strings or `"any"` (reuse `_parse_placement_allowed` with a per-element string parse). Parse `min`/`max` int fields (default `min=1`, `max=None`). Validate `default` length within `[min, max]` and each default ∈ allowed. Set `set_min`/`set_max` on the `IRControl`.

- [ ] **Step 5: `_bound_placement_value` handles `set`**

For `kind == "set"`: return the bound list (override `self.bindings[name]` else `default_placement`) as a tuple; validate each channel ∈ `allowed` ∩ device-capable (reuse the per-channel checks) and `set_min ≤ len ≤ set_max` (raise `ResolveError` with "at least {min}" / "at most {max}" messages). (Final-lock reused.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k set`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add src/refrain/resolver.py src/refrain/ir.py tests/test_resolver.py
git commit -m "feat(placement): kind=set declaration + min/max/allowed/device validation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `reward.combine` field

**Files:** Modify `src/refrain/resolver.py` (`_resolve_reward` ~922), `src/refrain/ir.py` (`IRReward`); Test `tests/test_resolver.py`.

- [ ] **Step 1: Write failing test**

```python
def test_reward_combine_parsed(amp):
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
          threshold "t" { signal = "env"; type = absolute(8 uV) }
          reward { combine = "any"; event = dwell(condition: above("env","t"), duration: 100 ms) }
          output { audio_chime = reward.event }
        }
    '''
    ir = resolve(parse(src), amp)
    assert ir.reward.combine == "any"

def test_reward_combine_defaults_all(amp):
    # (same protocol without `combine`) — default "all"
    ...  # assert ir.reward.combine == "all"

def test_reward_combine_invalid_fails(amp):
    # combine = "most" → ResolveError
    ...
```

(Fill in the two omitted bodies by copying the first and changing/removing `combine`.)

- [ ] **Step 2: Run to verify it fails**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k reward_combine`
Expected: FAIL — `IRReward` has no `combine`.

- [ ] **Step 3: Add `combine` to `IRReward` + parse it**

In `ir.py`, add `combine: str = "all"` to `IRReward` (defaulted). In `_resolve_reward` (~922), after extracting `fields`, parse `combine = fields.get("combine")`: if present must be `A.StringLit` ∈ `{"all","any"}` (else `ResolveError`); default `"all"`. Pass `combine=` into the `IRReward(...)` construction.

- [ ] **Step 4: Run test to verify it passes**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_resolver.py -q -k reward_combine`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py src/refrain/ir.py tests/test_resolver.py
git commit -m "feat(placement): reward.combine field (all|any) for set replication

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Mode 2a — the set-replication fan-out pre-pass

This is the substantial task. An **AST-level transform** that runs in `resolve()` after `compose` and before `_Resolver`, given `bindings`.

**Files:** Create `src/refrain/fanout.py`; Modify `src/refrain/resolver.py` (invoke the transform in the public `resolve()` ~1304); Test `tests/test_fanout.py` (new).

- [ ] **Step 1: Write the failing acceptance test**

Create `tests/test_fanout.py`:

```python
from pathlib import Path
import pytest
from refrain.amp_profile import load_amp_profile
from refrain.parser import parse
from refrain.resolver import resolve, ResolveError

_AMP = load_amp_profile(Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json")

_REPL = '''
    protocol "poise_ms" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "smr" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "smr_t" { signal = "smr"; type = absolute(8 uV) }
      reward { combine = "all"; event = dwell(condition: above("smr","smr_t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
'''

def test_fan_out_replicates_per_site():
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["C3","Cz","C4"]})
    # Per-site inputs/derives/thresholds, named <name>@<site>.
    assert set(ir.inputs) == {"raw@C3", "raw@Cz", "raw@C4"}
    assert set(ir.derives) == {"smr@C3", "smr@Cz", "smr@C4"}
    assert set(ir.thresholds) == {"smr_t@C3", "smr_t@Cz", "smr_t@C4"}
    # Each per-site input names its own channel.
    assert next(a.value.value for a in ir.inputs["raw@C3"].montage.args if a.name == "active") == "C3"

def test_fan_out_combine_all_wraps_conditions():
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["C3","Cz","C4"]})
    # The dwell condition is all_of over the 3 per-site `above(...)` conditions.
    event = ir.reward.event           # dwell(...)
    cond = next(a.value for a in event.args if a.name == "condition")
    assert cond.callee == "all_of"    # combine="all"
    # 3 elements, one per site (read the IRArray / args shape from ir.py)

def test_fan_out_combine_any():
    ir = resolve(parse(_REPL.replace('combine = "all"', 'combine = "any"')), _AMP, bindings={"sites": ["C3","Cz","C4"]})
    cond = next(a.value for a in ir.reward.event.args if a.name == "condition")
    assert cond.callee == "any_of"

def test_fan_out_single_site_degenerates():
    # min=1, bind one site → still works (one input/derive/threshold, combine over 1).
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["Cz"]})
    assert set(ir.inputs) == {"raw@Cz"}
```

(Adjust the condition/IRArray accessors to the real IR shapes — read `ir.py` for `IRCall`/`IRArg`/`IRArray`.)

- [ ] **Step 2: Run to verify it fails**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_fanout.py -q`
Expected: FAIL — no fan-out; `referential(active: sites)` with a set isn't replicated (and likely errors that `sites` isn't a single channel).

- [ ] **Step 3: Implement the fan-out transform (`src/refrain/fanout.py`)**

Write `fan_out(file_ast: A.File, bindings: dict, *, amp) -> A.File`. Algorithm:
1. **Light-scan the `controls` block** of `file_ast.protocol` for a `placement { kind = "set" }` assignment. If none, return `file_ast` unchanged. (Only one set placement supported in v1; if >1, raise `ResolveError`.)
2. Resolve the bound site list: `bindings[set_name]` if present, else the declared `default` list. (Validation of allowed/device/min/max stays in `_bound_placement_value` during the later resolve — OR validate here; pick one place. Recommend: validate here for the count/allowed, since the AST is rewritten before resolve. Reuse channel checks against `amp`.)
3. **Find the set-bound input**: the `input` decl whose montage `A.Call` has a channel-slot arg that is an `A.NameRef` naming the set placement.
4. **Compute the per-site subgraph** at the AST level: starting from the set-bound input name, find all `derive`/`threshold` decls transitively reachable (a derive's `from`/pipeline stream-refs, a threshold's `signal`) — these are the per-site entities. Follow the string-ref dependency edges. The reward `event`/`continuous` condition expression that references any per-site stream/threshold is the **combine point**.
5. **Scoping checks** (spec (a)/(b)): if `reward.continuous` references a per-site stream → `ResolveError` ("continuous reward over a replicated set needs aggregation — Mode 2b"); if a per-site entity also depends on a non-replicated stream ambiguously → `ResolveError`.
6. **Rewrite**: for each site `s`, duplicate the set-bound input decl (montage `referential(active: "<s>", …)`) and each per-site `derive`/`threshold` decl, renaming `<name>` → `<name>@<s>` and rewriting every stream/threshold string-ref inside them from `<name>` → `<name>@<s>`.
7. **Reward**: replace the reward `event` dwell's `condition` with `all_of([...])`/`any_of([...])` (per `reward.combine`) over the N per-site condition expressions (each = the original condition with refs rewritten to `@s`).
8. Remove the original (un-suffixed) input/derive/threshold decls; insert the per-site copies. Return the rewritten `A.File`.

Work at the AST level using `ast.py` node constructors (mirror how `compose.py` rewrites the AST). Keep `fanout.py` focused on this transform only.

- [ ] **Step 4: Invoke the transform in `resolve()`**

In `resolver.py`'s public `resolve()` (~1304), after `composed = compose(...)` and before `_Resolver(composed, amp, bindings).resolve()`, call `composed = fan_out(composed, bindings or {}, amp=amp)`. (Import `fan_out` from `.fanout`.) The set placement is consumed by fan-out and the bound channels baked into the per-site montages; `_substitute_placement_args` no longer sees a set placement in a montage slot (it's been rewritten to literals).

- [ ] **Step 5: Run tests to verify they pass**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_fanout.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Full suite green**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q`
Expected: PASS — existing single-site protocols unaffected (fan-out returns early when no set placement).

- [ ] **Step 7: Commit**

```bash
git add src/refrain/fanout.py src/refrain/resolver.py tests/test_fanout.py
git commit -m "feat(placement): Mode 2a set-replication fan-out pre-pass + reward.combine

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: scoping errors (continuous-over-set; ambiguous boundary)

If not already covered by Task 4 Step 3, lock them with explicit tests.

**Files:** Test `tests/test_fanout.py`; Modify `src/refrain/fanout.py` if needed.

- [ ] **Step 1: Write the failing/locking tests**

```python
def test_continuous_reward_over_set_rejected():
    src = _REPL.replace(
        'reward { combine = "all"; event = dwell(condition: above("smr","smr_t"), duration: 100 ms) }',
        'reward { continuous = sigmoid("smr", midpoint: 0 uV, steepness: 1) }'
    )
    with pytest.raises(ResolveError, match="continuous.*aggregat|Mode 2b|aggregation"):
        resolve(parse(src), _AMP, bindings={"sites": ["C3","Cz"]})
```

(For the ambiguous-boundary case, construct a protocol where a derive mixes a per-site stream with a non-replicated input and assert `ResolveError`. If the boundary algorithm in Task 4 already makes such a protocol unrepresentable or errors, a focused test that exercises the error path is sufficient.)

- [ ] **Step 2: Run / implement / verify**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_fanout.py -q -k "rejected or ambiguous"`
Expected: FAIL → implement the guards in `fanout.py` (Task 4 Step 5) → PASS.

- [ ] **Step 3: Commit**

```bash
git add src/refrain/fanout.py tests/test_fanout.py
git commit -m "feat(placement): reject continuous-reward / ambiguous-boundary over a replicated set

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: wire-invariant verification + version 0.4.0 + docs

**Files:** Test `tests/test_ir_json.py`; Modify `pyproject.toml` ×2, `CHANGELOG.md`, `docs/SPEC.md`.

- [ ] **Step 1: Write the wire-invariant tests**

```python
def test_pair_and_set_controls_omitted_from_ir_json():
    # A pair-bound + a set-bound protocol emit NO placement controls.
    ir = resolve(parse(_PAIR_PROTO), _AMP)         # import _PAIR_PROTO or inline
    obj = ir_to_json_obj(ir)
    assert "coh" not in obj["controls"]

def test_set_bound_ir_json_is_flat_multisite():
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["C3","Cz","C4"]})
    obj = ir_to_json_obj(ir)
    assert {"raw@C3","raw@Cz","raw@C4"} <= set(obj["inputs"])
    assert "sites" not in obj["controls"]   # set placement omitted
```

(Inline the protocols or import them; `ir_to_json_obj` from `refrain.ir_json`, `_AMP` per the file's pattern.)

- [ ] **Step 2: Run to verify pass (emitter already omits placement controls)**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest tests/test_ir_json.py -q -k "pair or set or multisite"`
Expected: PASS without emitter changes (the shipped `type_kind != "placement"` guard already omits pair/set). If a test fails, the only fix should be confirming the guard — do NOT add new emission logic.

- [ ] **Step 3: Full suite + drift gate (the no-wire-change proof)**

Run: `VIRTUAL_ENV=.venv .venv/bin/python -m pytest -q` (expect green).
Run: `PATH="$HOME/.cargo/bin:$PATH" PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py`
Expected: `RESULT: PASS` — golden vectors + schema unchanged (no Mode-2 protocol in the corpus; wire format untouched). Paste the summary.

- [ ] **Step 4: Version + CHANGELOG + SPEC**

- `pyproject.toml` + `refrain-core/pyproject.toml`: `version = "0.4.0"` (both currently `0.3.0`).
- `CHANGELOG.md`: `0.4.0` entry — Added: placement `kind="pair"` (coherence pairs, `.a`/`.b` leg member access) and `kind="set"` + Mode 2a per-site replication (implicit fan-out, `reward.combine = "all"|"any"`). IR-JSON schema unchanged (v0.1).
- `docs/SPEC.md`: fold in the `pair`/`set` kinds, leg member access, `reward.combine`, and the fan-out replication semantics (consistent with the design spec). `IR_JSON_VERSION` stays `"0.1"`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ir_json.py pyproject.toml refrain-core/pyproject.toml CHANGELOG.md docs/SPEC.md
git commit -m "feat(placement): Mode 2 wire-invariant tests + version 0.4.0 + SPEC/CHANGELOG

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Definition of done

- `pair`: `.a`/`.b` legs bind into two inputs (default+override), `requires=[coh]` → both legs, allowed∩device validation.
- `set`: declares with min/max, binds a list, allowed∩device + count validation.
- Mode 2a: a single-site protocol bound to an N-site set fans out to N inputs + N×derives/thresholds (`<name>@<site>`), reward condition = `all_of`/`any_of` per `reward.combine`; single-site degenerates cleanly.
- Scoping: continuous-reward-over-set and ambiguous-boundary both raise `ResolveError`.
- IR-JSON omits pair/set controls; set-bound IR is a flat multi-site graph; `check_equivalence` PASS (no wire change); `IR_JSON_VERSION` stays `0.1`.
- Full `pytest -q` green; `cargo test` unaffected. Versions `0.4.0`.
```
