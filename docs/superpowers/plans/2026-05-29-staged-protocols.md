# Staged / Segmented Protocols Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the one-shot `warmup→run` lifecycle into a real N-phase staged-session runtime — phases with `timed`/`open`/`timed_with_floor` modes, a host advance/hold/clock-freeze transport, named blocks that gate which threshold/output/reward emits per phase, and `percentile()` windows that freeze across muted rests — identical on the Python and Rust backends.

**Architecture:** Activation gates *emission/selection*, never *computation*: every derive/threshold/reward bundle computes every chunk; a block only selects which named components emit. Build Python-first (parse → resolve → IR → IR-JSON → evaluator), then mirror into the Rust core (`ir.rs`/`eval.rs`/`dsp.rs` + `python.rs`/`mobile.rs` bindings), with a Python↔Rust parity suite as the final gate.

**Tech Stack:** Python 3.10+ (lark, numpy), pytest; Rust core via maturin/pyo3 (`refrain_core` wheel) + uniffi mobile bindings.

**Spec:** `docs/superpowers/specs/2026-05-29-staged-protocols-design.md`. Scope = R1–R4 + R6 (R5 is recorder-side; engine obligation is the warm-signal/seeding invariant).

**Conventions for every task:** run Python tests with `.venv/bin/python -m pytest`. The venv was created during worktree setup; if absent run `python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"`. Commit after each task with the message shown.

---

## File structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/refrain/grammar.lark` | Add `block` to `DECL_KW` | Modify |
| `src/refrain/ir.py` | `IRPhase.mode`/`.block`; new `IRBlock`; `IRProtocol.blocks`/`.reward_bundles` | Modify |
| `src/refrain/resolver.py` | Parse phase `mode`/`block`; resolve `block "…"` decls and `reward "…"{continuous,event}` bundles; validation | Modify |
| `src/refrain/ir_print.py` | Render new IR fields | Modify |
| `src/refrain/ir_json.py` | Emit `phase.mode`/`.block`, `blocks`, reward bundles | Modify |
| `refrain-core/schema/ir-json-v0.2.schema.json` | `Phase.mode`/`.block`, `Block` def, `blocks` map | Modify |
| `src/refrain/eval_.py` | Phase cursor; `advance_phase`/`hold`/`set_clock_frozen`/`current_phase`; generalized suppression; activation masking; reward selection; percentile-freeze; `phase/index` tap | Modify |
| `src/refrain/primitive_impls.py` | `PercentileImpl.set_ingesting()` | Modify |
| `refrain-core/src/ir.rs` | `Phase.mode`/`.block`; `Block`; `Protocol.blocks`/reward bundles | Modify |
| `refrain-core/src/eval.rs` | Mirror of evaluator runtime | Modify |
| `refrain-core/src/dsp.rs` | `Percentile` ingest gate | Modify |
| `refrain-core/src/python.rs` | pyo3: expose new methods + `phase/index` tap | Modify |
| `refrain-core/src/mobile.rs` | uniffi: expose new methods | Modify |
| `tests/test_staged_*.py` | New Python + parity tests | Create |
| `examples/staged_beta_alpha.refrain` | Worked heterogeneous example | Create |

---

## Phase 0 — Rust build baseline

### Task 0: Build the Rust wheel so parity tests run

**Files:** none (environment).

- [ ] **Step 1: Build the wheel into the venv**

Run:
```bash
cd refrain-core && ../.venv/bin/python -m pip install maturin && ../.venv/bin/maturin develop --release && cd ..
```
Expected: `🛠 Installed refrain_core-…`. If `maturin` errors on toolchain, install Rust via `rustup` first.

- [ ] **Step 2: Confirm the Rust backend now imports**

Run: `.venv/bin/python -c "import refrain_core; print(refrain_core.__name__)"`
Expected: `refrain_core`

- [ ] **Step 3: Confirm previously-skipped parity tests now run**

Run: `.venv/bin/python -m pytest tests/test_eval_rust_backend.py -q`
Expected: tests PASS (no longer `SKIPPED … refrain_core wheel not installed`). This is the baseline that lets every later parity task actually execute.

No commit (environment only).

---

## Phase A — IR shape + parse/resolve (Python)

### Task A0: Shared test fixtures module

**Files:** Create `tests/conftest_staged.py`

- [ ] **Step 1: Create the shared protocol strings**

Create `tests/conftest_staged.py` with the three protocol strings reused across the test files (so there is one source of truth):
- `BASE` — single-channel SMR-ish protocol with one `%s` slot for a `session { … }` block (as shown in Task 1).
- `HET` — the heterogeneous two-block protocol (as shown in Task 4).
- `PCT_SRC` — the percentile staged protocol (as shown in Task 11).

Copy the exact strings from Tasks 1, 4, and 11 into module-level constants. Test files then do `from tests.conftest_staged import BASE, HET, PCT_SRC`.

- [ ] **Step 2: Verify importable**

Run: `.venv/bin/python -c "from tests.conftest_staged import BASE, HET, PCT_SRC; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest_staged.py
git commit -m "test: shared staged-protocol fixture strings"
```

### Task 1: `IRPhase` gains `mode` and `block`

**Files:**
- Modify: `src/refrain/ir.py:286-291` (`IRPhase`)
- Modify: `src/refrain/resolver.py:1343-1384` (`_resolve_session`)
- Test: `tests/test_staged_resolve.py` (Create)

- [ ] **Step 1: Write the failing test**

The `BASE` string below is the one created in Task A0 (`tests/conftest_staged.py`);
import it rather than redefining. Shown here in full because Task A0 sources it from
here.

```python
# tests/test_staged_resolve.py
from refrain.parser import parse_protocol
from refrain.resolver import resolve
from tests.conftest_staged import BASE   # defined in Task A0

def _resolve(src: str):
    return resolve(parse_protocol(src))

# BASE (lives in tests/conftest_staged.py):
BASE = '''
protocol "p" {
  requires { channels = ["Cz"]; sample_rate >= 256 hz }
  input raw { montage = referential(active: "Cz", reference: "A1") }
  derive e { from = raw |> bandpass(band: frequency { center = 12 hz; width = ratio(2.5) }) |> magnitude() |> smooth(tau: 200 ms) }
  threshold "t" { signal = "e"; type = absolute(value: 5 uv) }
  reward { continuous = sigmoid(e - "t") }
  output { audio = reward.continuous }
  %s
}
'''

def test_phase_mode_and_block_default_and_explicit():
    ir = _resolve(BASE % '''
      session { phases = [
        phase { name = "warm"; duration = 1 s; output_muted = true },
        phase { name = "go";   duration = 2 s; mode = timed_with_floor },
      ] }
    ''')
    phases = ir.session.phases
    assert phases[0].mode == "timed"            # default
    assert phases[0].block is None
    assert phases[1].mode == "timed_with_floor"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py::test_phase_mode_and_block_default_and_explicit -v`
Expected: FAIL — `AttributeError: 'IRPhase' object has no attribute 'mode'`.

- [ ] **Step 3: Add fields to `IRPhase`**

In `src/refrain/ir.py`, replace the `IRPhase` dataclass:
```python
@dataclass(frozen=True, slots=True)
class IRPhase:
    name: str
    duration_ms: float
    output_muted: bool
    mode: str = "timed"          # "timed" | "open" | "timed_with_floor"
    block: str | None = None     # name of the block active during this phase
    loc: Loc | None = None
```

- [ ] **Step 4: Parse `mode`/`block` in `_resolve_session`**

In `src/refrain/resolver.py`, inside the phase loop of `_resolve_session` (after `output_muted = …`, before `phases.append(`), add:
```python
            mode_expr = phase_fields.get("mode")
            mode = "timed"
            if mode_expr is not None:
                # `mode = timed_with_floor` parses as a bare NameRef.
                if isinstance(mode_expr, A.NameRef):
                    mode = mode_expr.name
                elif isinstance(mode_expr, A.StringLit):
                    mode = mode_expr.value
                else:
                    raise ResolveError("phase.mode must be a bare identifier", loc=mode_expr.loc)
                if mode not in ("timed", "open", "timed_with_floor"):
                    raise ResolveError(
                        f'phase.mode must be one of timed/open/timed_with_floor; got {mode!r}',
                        loc=mode_expr.loc,
                    )
            block_expr = phase_fields.get("block")
            block_name = None
            if block_expr is not None:
                if not isinstance(block_expr, A.StringLit):
                    raise ResolveError("phase.block must be a string", loc=block_expr.loc)
                block_name = block_expr.value
```
Then make `duration` optional for `open` phases: replace the existing `if name_expr is None or duration_expr is None:` guard so an `open` phase may omit `duration`:
```python
            if name_expr is None:
                raise ResolveError("session phase needs a `name` field", loc=elt.loc)
            mode_peek = phase_fields.get("mode")
            is_open = isinstance(mode_peek, A.NameRef) and mode_peek.name == "open"
            if duration_expr is None and not is_open:
                raise ResolveError(
                    "session phase needs a `duration` field (only `mode = open` may omit it)",
                    loc=elt.loc,
                )
```
Guard the duration type-check + `duration_ms` computation so they only run when `duration_expr is not None`, defaulting `duration_ms = 0.0` for open phases. Finally pass `mode=mode, block=block_name` to the `IRPhase(...)` constructor.

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py::test_phase_mode_and_block_default_and_explicit -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/ir.py src/refrain/resolver.py tests/test_staged_resolve.py
git commit -m "feat(ir): phase mode + block fields; open phases may omit duration"
```

### Task 2: `IRBlock` + `IRProtocol.blocks`/`reward_bundles`

**Files:**
- Modify: `src/refrain/ir.py` (add `IRBlock`, extend `IRProtocol`, `__all__`)
- Test: `tests/test_staged_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
def test_protocol_has_blocks_and_reward_bundles_maps():
    ir = _resolve(BASE % 'session { phases = [ phase { name="w"; duration=1 s; output_muted=true } ] }')
    assert ir.blocks == {}               # no blocks declared
    assert ir.reward_bundles == {}       # no named bundles declared
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py::test_protocol_has_blocks_and_reward_bundles_maps -v`
Expected: FAIL — `AttributeError: 'IRProtocol' object has no attribute 'blocks'`.

- [ ] **Step 3: Add `IRBlock` and extend `IRProtocol`**

In `src/refrain/ir.py`, add after `IRPhase`:
```python
@dataclass(frozen=True, slots=True)
class IRBlock:
    """A named activation set referenced by `phase.block`.

    `thresholds` is declarative (validation + telemetry). `reward` names a
    reward bundle (key into `IRProtocol.reward_bundles`) or None to use the
    default top-level reward. `outputs` is the set of output channels that may
    emit during this block (empty tuple ⇒ all channels). `inhibits` is the set
    of inhibits that gate during this block (empty tuple ⇒ all inhibits).
    """

    name: str
    thresholds: tuple[str, ...]
    reward: str | None
    outputs: tuple[str, ...]
    inhibits: tuple[str, ...]
    loc: Loc | None = None
```
Add two fields to `IRProtocol` (after `session: IRSession`):
```python
    blocks: dict[str, IRBlock] = field(default_factory=dict)
    reward_bundles: dict[str, IRReward] = field(default_factory=dict)
```
(Import `field` is already imported at top: `from dataclasses import dataclass, field`.) Add `"IRBlock"` to `__all__`.

- [ ] **Step 4: Default the new args at the `IRProtocol` construction site**

In `src/refrain/resolver.py` find the single `IRProtocol(` construction (near `session=session_ir`) and add `blocks={}, reward_bundles={}` for now (Task 4 fills them). Run the test.

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py::test_protocol_has_blocks_and_reward_bundles_maps -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/ir.py src/refrain/resolver.py tests/test_staged_resolve.py
git commit -m "feat(ir): IRBlock + IRProtocol.blocks/reward_bundles"
```

### Task 3: Resolve `reward "…" { continuous, event }` as a bundle

**Files:**
- Modify: `src/refrain/resolver.py` (`_resolve_named_decls` reward branch; new `_resolve_reward_bundle`; collect into `self._reward_bundles`)
- Test: `tests/test_staged_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
def test_reward_bundle_disambiguated_from_component():
    ir = _resolve('''
    protocol "p" {
      requires { channels = ["Cz"]; sample_rate >= 256 hz }
      input raw { montage = referential(active: "Cz", reference: "A1") }
      derive e { from = raw |> bandpass(band: frequency { center = 12 hz; width = ratio(2.5) }) |> magnitude() |> smooth(tau: 200 ms) }
      threshold "t" { signal = "e"; type = absolute(value: 5 uv) }
      reward "beta_reward" { continuous = sigmoid(e - "t") }
      output { audio = reward.continuous }
      session { phases = [ phase { name="w"; duration=1 s; output_muted=true } ] }
    }
    ''')
    assert "beta_reward" in ir.reward_bundles
    assert ir.reward_bundles["beta_reward"].continuous is not None
    # The existing weighted-component path is untouched (no components here):
    assert ir.reward.components == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py::test_reward_bundle_disambiguated_from_component -v`
Expected: FAIL — currently the `reward` keyword branch calls `_resolve_reward_component`, which raises on `continuous`/`event` fields (`unexpected field(s)`).

- [ ] **Step 3: Add a bundle collector and branch on field shape**

In `src/refrain/resolver.py` `__init__` (near `self._reward_components: list[...] = []`, line ~163) add:
```python
        self._reward_bundles: dict[str, IRReward] = {}
```
Replace the `elif stmt.keyword == "reward":` branch in `_resolve_named_decls` with:
```python
                elif stmt.keyword == "reward":
                    rfields = self._assignments_dict(stmt.body)
                    if "continuous" in rfields or "event" in rfields:
                        self._reward_bundles[stmt.name] = self._resolve_reward_bundle(stmt)
                    else:
                        self._reward_components.append(
                            self._resolve_reward_component(stmt, role="reward")
                        )
```
Add the resolver method (next to `_resolve_reward`):
```python
    def _resolve_reward_bundle(self, decl: A.NamedDecl) -> IRReward:
        """Resolve a named, block-selectable reward bundle:
        `reward "<name>" { continuous?, event? }`. Distinguished from a
        weighted component (`signal`/`weight`) by these fields."""
        fields = self._assignments_dict(decl.body)
        extra = set(fields) - {"continuous", "event"}
        if extra:
            raise ResolveError(
                f'reward "{decl.name}" bundle: unexpected field(s) {sorted(extra)}; '
                "a block-selectable bundle uses `continuous` and/or `event`.",
                loc=decl.loc,
            )
        cont = fields.get("continuous")
        event = fields.get("event")
        if cont is None and event is None:
            raise ResolveError(
                f'reward "{decl.name}" bundle must declare `continuous`, `event`, or both',
                loc=decl.loc,
            )
        cont_ir = self._resolve_stream_expr(cont) if cont is not None else None
        event_ir = self._resolve_stream_expr(event) if event is not None else None
        return IRReward(continuous=cont_ir, event=event_ir, combine="all", components=(), loc=decl.loc)
```
Import `IRReward` if not already imported in resolver (it is — used by `_resolve_reward`). Then in the `IRProtocol(` construction, pass `reward_bundles=dict(self._reward_bundles)`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py::test_reward_bundle_disambiguated_from_component -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_staged_resolve.py
git commit -m "feat(resolve): named reward bundles (continuous/event) vs components"
```

### Task 4: Resolve `block "…" { … }` declarations + validation

**Files:**
- Modify: `src/refrain/grammar.lark:55` (add `block` to `DECL_KW`)
- Modify: `src/refrain/resolver.py` (`_resolve_named_decls` block branch; `_resolve_block`; cross-validation in a post-pass; pass `blocks=` to `IRProtocol`)
- Test: `tests/test_staged_resolve.py`

- [ ] **Step 1: Write the failing tests**

```python
HET = '''
protocol "p" {
  requires { channels = ["Cz"]; sample_rate >= 256 hz }
  input raw { montage = referential(active: "Cz", reference: "A1") }
  derive be { from = raw |> bandpass(band: frequency { center = 15 hz; width = ratio(2.5) }) |> magnitude() |> smooth(tau: 200 ms) }
  derive ae { from = raw |> bandpass(band: frequency { center = 10 hz; width = ratio(2.5) }) |> magnitude() |> smooth(tau: 200 ms) }
  threshold "bt" { signal = "be"; type = absolute(value: 5 uv) }
  threshold "at" { signal = "ae"; type = absolute(value: 5 uv) }
  reward "br" { continuous = sigmoid(be - "bt") }
  reward "ar" { continuous = sigmoid(ae - "at") }
  output { audio = reward.continuous }
  block "beta_up"  { threshold = "bt"; reward = "br"; output = ["audio"] }
  block "alpha_up" { threshold = "at"; reward = "ar"; output = ["audio"] }
  session { phases = [
    phase { name="warm";  duration=1 s; output_muted=true },
    phase { name="b1";    duration=2 s; block="beta_up";  mode=timed_with_floor },
    phase { name="rest";  output_muted=true; mode=open },
    phase { name="b2";    duration=2 s; block="alpha_up"; mode=timed_with_floor },
  ] }
}
'''

def test_blocks_resolved():
    ir = _resolve(HET)
    assert set(ir.blocks) == {"beta_up", "alpha_up"}
    assert ir.blocks["beta_up"].reward == "br"
    assert ir.blocks["beta_up"].thresholds == ("bt",)
    assert ir.blocks["beta_up"].outputs == ("audio",)

def test_nonmuted_phase_without_block_errors():
    import pytest
    from refrain.resolver import ResolveError
    src = HET.replace('phase { name="b1";    duration=2 s; block="beta_up";  mode=timed_with_floor },',
                      'phase { name="b1";    duration=2 s; mode=timed_with_floor },')
    with pytest.raises(ResolveError, match="non-muted phase"):
        _resolve(src)

def test_block_unknown_reward_errors():
    import pytest
    from refrain.resolver import ResolveError
    src = HET.replace('reward = "br"', 'reward = "nope"')
    with pytest.raises(ResolveError, match="unknown reward bundle"):
        _resolve(src)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py -k "blocks_resolved or without_block or unknown_reward" -v`
Expected: FAIL — `block` is not a valid `DECL_KW` (parse error) / no block resolution.

- [ ] **Step 3: Add `block` to the grammar**

In `src/refrain/grammar.lark` line 55:
```
DECL_KW: "reward" | "input" | "derive" | "threshold" | "inhibit" | "custom" | "block"
```

- [ ] **Step 4: Resolve blocks + validate**

In `resolver.py` `__init__` add `self._blocks: dict[str, IRBlock] = {}` and import `IRBlock`. Add a branch in `_resolve_named_decls`:
```python
                elif stmt.keyword == "block":
                    self._blocks[stmt.name] = self._resolve_block(stmt)
```
Add the method:
```python
    def _resolve_block(self, decl: A.NamedDecl) -> IRBlock:
        fields = self._assignments_dict(decl.body)
        extra = set(fields) - {"threshold", "reward", "output", "inhibit"}
        if extra:
            raise ResolveError(
                f'block "{decl.name}": unexpected field(s) {sorted(extra)}; '
                "allowed: threshold, reward, output, inhibit.",
                loc=decl.loc,
            )

        def _str_list(key: str) -> tuple[str, ...]:
            e = fields.get(key)
            if e is None:
                return ()
            if isinstance(e, A.StringLit):
                return (e.value,)
            if isinstance(e, A.Array):
                out = []
                for elt in e.elements:
                    if not isinstance(elt, A.StringLit):
                        raise ResolveError(f'block "{decl.name}".{key} entries must be strings', loc=elt.loc)
                    out.append(elt.value)
                return tuple(out)
            raise ResolveError(f'block "{decl.name}".{key} must be a string or list of strings', loc=e.loc)

        reward_e = fields.get("reward")
        reward_name = None
        if reward_e is not None:
            if not isinstance(reward_e, A.StringLit):
                raise ResolveError(f'block "{decl.name}".reward must be a string', loc=reward_e.loc)
            reward_name = reward_e.value
        return IRBlock(
            name=decl.name,
            thresholds=_str_list("threshold"),
            reward=reward_name,
            outputs=_str_list("output"),
            inhibits=_str_list("inhibit"),
            loc=decl.loc,
        )
```
Add a validation pass. Create `_validate_staging(self)` called after blocks, reward bundles, thresholds, outputs, inhibits, and session are all resolved (call it right before constructing `IRProtocol`, when `self.thresholds`, `self.output`, `self.inhibits`, `session_ir`, `self._blocks`, `self._reward_bundles` exist):
```python
    def _validate_staging(self, session_ir: "IRSession") -> None:
        threshold_names = set(self.thresholds)
        output_names = set(self.output)
        inhibit_names = set(self.inhibits)
        bundle_names = set(self._reward_bundles)
        for b in self._blocks.values():
            for t in b.thresholds:
                if t not in threshold_names:
                    raise ResolveError(f'block "{b.name}": unknown threshold "{t}"', loc=b.loc)
            for o in b.outputs:
                if o not in output_names:
                    raise ResolveError(f'block "{b.name}": unknown output channel "{o}"', loc=b.loc)
            for ih in b.inhibits:
                if ih not in inhibit_names:
                    raise ResolveError(f'block "{b.name}": unknown inhibit "{ih}"', loc=b.loc)
            if b.reward is not None and b.reward not in bundle_names:
                raise ResolveError(f'block "{b.name}": unknown reward bundle "{b.reward}"', loc=b.loc)
        if self._blocks:
            for ph in session_ir.phases:
                if not ph.output_muted and ph.block is None:
                    raise ResolveError(
                        f'non-muted phase "{ph.name}" must name a `block` when blocks are declared',
                        loc=ph.loc,
                    )
                if ph.block is not None and ph.block not in self._blocks:
                    raise ResolveError(
                        f'phase "{ph.name}": unknown block "{ph.block}"', loc=ph.loc
                    )
```
Wire it: in the resolve driver, after `session_ir = self._resolve_session()`, call `self._validate_staging(session_ir)`, then pass `blocks=dict(self._blocks)` to `IRProtocol(`.

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_staged_resolve.py -k "blocks_resolved or without_block or unknown_reward" -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the full resolver suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_resolver.py tests/test_parser_examples.py -q`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add src/refrain/grammar.lark src/refrain/resolver.py tests/test_staged_resolve.py
git commit -m "feat(resolve): block declarations + staging validation"
```

### Task 5: Render new IR fields in `ir_print`

**Files:**
- Modify: `src/refrain/ir_print.py`
- Test: `tests/test_ir_print.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_print_ir_includes_block_and_mode():
    from refrain.parser import parse_protocol
    from refrain.resolver import resolve
    from refrain.ir_print import print_ir
    from tests.conftest_staged import HET    # see "Shared fixtures" note below
    text = print_ir(resolve(parse_protocol(HET)))
    assert "beta_up" in text
    assert "timed_with_floor" in text
```
The module's entry function is `print_ir(ir) -> str` (`src/refrain/ir_print.py:44`).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ir_print.py::test_ir_print_includes_block_and_mode -v`
Expected: FAIL — block/mode not rendered.

- [ ] **Step 3: Render blocks + phase mode/block**

In `src/refrain/ir_print.py`, locate where session phases are rendered and append `mode`/`block` when non-default. Add a blocks section printing each `IRBlock` (name, thresholds, reward, outputs, inhibits). Follow the file's existing formatting helpers.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ir_print.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/ir_print.py tests/test_ir_print.py
git commit -m "feat(ir_print): render block decls and phase mode/block"
```

---

## Phase B — IR-JSON + schema

### Task 6: Emit phase `mode`/`block`, `blocks`, reward bundles to IR-JSON

**Files:**
- Modify: `src/refrain/ir_json.py:337-342` (`_emit_phase`/`_emit_session`); top-level dict in `ir_to_json_obj:380-396`
- Test: `tests/test_ir_json.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_ir_json_emits_blocks_and_phase_fields():
    from refrain.parser import parse_protocol
    from refrain.resolver import resolve
    from refrain.ir_json import ir_to_json_obj
    ir = resolve(parse_protocol(HET))   # inline HET or import it
    obj = ir_to_json_obj(ir)
    phases = obj["session"]["phases"]
    assert phases[1]["mode"] == "timed_with_floor"
    assert phases[1]["block"] == "beta_up"
    assert obj["blocks"]["beta_up"]["reward"] == "br"
    assert obj["blocks"]["beta_up"]["output"] == ["audio"]
    assert "br" in obj["reward_bundles"]
    assert obj["reward_bundles"]["br"]["continuous"] is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ir_json.py::test_ir_json_emits_blocks_and_phase_fields -v`
Expected: FAIL — `KeyError: 'blocks'` / phase has no `mode`.

- [ ] **Step 3: Extend the emitters**

In `src/refrain/ir_json.py`:
```python
def _emit_phase(p: IRPhase) -> dict:
    return {
        "name": p.name,
        "duration_ms": p.duration_ms,
        "output_muted": p.output_muted,
        "mode": p.mode,
        "block": p.block,
    }


def _emit_block(b) -> dict:
    return {
        "name": b.name,
        "thresholds": list(b.thresholds),
        "reward": b.reward,
        "output": list(b.outputs),
        "inhibits": list(b.inhibits),
    }
```
In `ir_to_json_obj`, add to the returned dict (after `"session": _emit_session(ir.session),`):
```python
        "blocks": {name: _emit_block(b) for name, b in ir.blocks.items()},
        "reward_bundles": {
            name: _emit_reward(rb, ctx, version) for name, rb in ir.reward_bundles.items()
        },
```
Import `IRBlock` is not needed (duck-typed); `_emit_reward` already exists.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ir_json.py::test_ir_json_emits_blocks_and_phase_fields -v`
Expected: PASS.

- [ ] **Step 5: Confirm back-compat JSON is unchanged for blockless protocols**

Run: `.venv/bin/python -m pytest tests/test_ir_json.py -q`
Expected: PASS. (`blocks`/`reward_bundles` are empty maps for existing protocols; `phase.mode="timed"`, `phase.block=None` are additive.)

- [ ] **Step 6: Commit**

```bash
git add src/refrain/ir_json.py tests/test_ir_json.py
git commit -m "feat(ir_json): emit phase mode/block, blocks, reward bundles"
```

### Task 7: Extend the IR-JSON schema

**Files:**
- Modify: `refrain-core/schema/ir-json-v0.2.schema.json` (`Phase`, add `Block`, top-level `blocks`/`reward_bundles`)
- Test: `tests/test_ir_json_schema.py`

- [ ] **Step 1: Write the failing test**

```python
def test_staged_ir_validates_against_schema():
    import json, jsonschema, pathlib
    from refrain.parser import parse_protocol
    from refrain.resolver import resolve
    from refrain.ir_json import ir_to_json_obj
    schema = json.loads(pathlib.Path("refrain-core/schema/ir-json-v0.2.schema.json").read_text())
    obj = ir_to_json_obj(resolve(parse_protocol(HET)))   # inline HET
    jsonschema.validate(obj, schema)   # raises on failure
```
(This file already skips when `jsonschema` is missing; with `[dev]` installed it runs.)

- [ ] **Step 2: Run to verify it fails or to confirm `additionalProperties` already lets it pass**

Run: `.venv/bin/python -m pytest tests/test_ir_json_schema.py::test_staged_ir_validates_against_schema -v`
Expected: PASS only if the schema's `additionalProperties: true` already admits the new keys. Either way, proceed to make the new fields *first-class* (typed) rather than relying on the permissive escape hatch.

- [ ] **Step 3: Add `mode`/`block` to `Phase`, add `Block`, declare `blocks`/`reward_bundles`**

In `refrain-core/schema/ir-json-v0.2.schema.json`, extend `$defs.Phase.properties`:
```json
"mode": { "type": "string", "enum": ["timed", "open", "timed_with_floor"] },
"block": { "type": ["string", "null"] }
```
Add a `$defs.Block`:
```json
"Block": {
  "type": "object",
  "required": ["name"],
  "additionalProperties": true,
  "properties": {
    "name": { "type": "string" },
    "thresholds": { "type": "array", "items": { "type": "string" } },
    "reward": { "type": ["string", "null"] },
    "output": { "type": "array", "items": { "type": "string" } },
    "inhibits": { "type": "array", "items": { "type": "string" } }
  }
}
```
In the top-level protocol object's `properties`, add:
```json
"blocks": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Block" } },
"reward_bundles": { "type": "object", "additionalProperties": { "$ref": "#/$defs/Reward" } }
```
(Use the existing reward `$def` name — match whatever `_emit_reward` validates against in this schema.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ir_json_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add refrain-core/schema/ir-json-v0.2.schema.json tests/test_ir_json_schema.py
git commit -m "feat(schema): phase mode/block, Block def, blocks/reward_bundles"
```

---

## Phase C — Python evaluator runtime

> All evaluator tasks use a shared synthetic helper. Add it once at the top of `tests/test_staged_eval.py`:
> ```python
> import numpy as np
> from refrain.parser import parse_protocol
> from refrain.resolver import resolve
> from refrain.eval_ import Evaluator
>
> SR = 256
> def live(src, *, backend="python"):
>     ir = resolve(parse_protocol(src))
>     ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("Cz",), backend=backend)
>     ev.start()
>     return ev
> def chunk(n=64, val=1.0):
>     return np.full((n, 1), val, dtype=np.float64)
> def feed(ev, seconds, **kw):
>     total = int(seconds * SR); pushed = 0
>     while pushed < total:
>         n = min(64, total - pushed); ev.step_chunk(chunk(n, **kw)); pushed += n
> ```
> Reuse the `HET` / `BASE` protocol strings (copy them into this file or import from `tests.test_staged_resolve`).

### Task 8: Phase cursor — N-phase sequencing + generalized mute + state mapping

**Files:**
- Modify: `src/refrain/eval_.py` (replace `_compute_warmup_samples`/`start`/`step_chunk` cursor logic; add `_phases_samples`, `_phase_index`, `_phase_elapsed`, `_advance_if_due`; generalize suppression in `_process_chunk`)
- Test: `tests/test_staged_eval.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_phases_run_in_order_to_stopped():
    ev = live(HET)
    feed(ev, 1.0)   # warm (muted)
    assert ev.state == "run"            # left warmup once
    feed(ev, 2.0)   # b1 timed_with_floor (auto-advances at 2 s)
    # rest is open → must be advanced
    assert ev.current_phase()["name"] == "rest"
    ev.advance_phase()
    feed(ev, 2.0)   # b2 (2 s) auto-advances → stopped
    assert ev.state == "stopped"

def test_state_never_returns_to_warmup():
    ev = live(HET)
    seen = []
    for _ in range(int(8 * SR / 64) + 4):
        ev.step_chunk(chunk(64))
        seen.append(ev.state)
        if ev.current_phase()["name"] == "rest":
            ev.advance_phase()
        if ev.state == "stopped":
            break
    # warmup appears only as a contiguous leading run, never after "run"
    assert "warmup" not in seen[seen.index("run"):] if "run" in seen else True

def test_midsession_rest_mutes_output():
    ev = live(HET)
    feed(ev, 1.0)                 # warm
    feed(ev, 2.0)                 # b1 active — emits
    assert ev.current_phase()["name"] == "rest"
    out = ev.last_taps().get("output/audio", 0.0)
    assert out == 0.0             # muted during the open rest
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py -k "run_in_order or returns_to_warmup or rest_mutes" -v`
Expected: FAIL — no `current_phase`; rest does not mute (today only first phase mutes).

- [ ] **Step 3: Replace warmup scalar with a phase cursor**

In `Evaluator.__init__` (around line 316-318), replace:
```python
        self._state: str = "ready"
        self._samples_pushed: int = 0
        self._warmup_samples: int = self._compute_warmup_samples()
```
with:
```python
        self._state: str = "ready"
        self._samples_pushed: int = 0
        # Phase cursor (N-phase runtime). _phases_samples[i] is phase i's
        # duration in samples (0 for open / zero-duration phases).
        self._phases_samples: list[int] = self._compute_phase_samples()
        self._phase_index: int = 0
        self._phase_elapsed: int = 0
        self._held: bool = False
        self._clock_frozen: bool = False
        # Snapshot of the phase active during the chunk last processed, kept
        # aligned with last_taps (see current_phase()). index -1 == terminal.
        self._phase_snapshot: dict = {"index": -1}
```
Replace `_compute_warmup_samples` with:
```python
    def _compute_phase_samples(self) -> list[int]:
        out: list[int] = []
        for ph in self.ir.session.phases:
            out.append(int(round(ph.duration_ms / 1000.0 * self.sample_rate_hz)))
        return out

    def _current_phase_ir(self):
        phases = self.ir.session.phases
        if 0 <= self._phase_index < len(phases):
            return phases[self._phase_index]
        return None
```
Update `start()` (lines 433-439, Python branch) to seed the cursor and coarse state:
```python
        self._samples_pushed = 0
        self._phase_index = 0
        self._phase_elapsed = 0
        self._held = False
        self._clock_frozen = False
        first = self._current_phase_ir()
        if skip_warmup or first is None:
            self._state = "run"
        elif first.output_muted and self._phase_index == 0:
            self._state = "warmup"
        else:
            self._state = "run"
```
Add the advance helper:
```python
    def _advance_if_due(self, n: int) -> None:
        """Accumulate elapsed for the current phase and auto-advance per its
        mode rule. Called once per step_chunk, after _process_chunk."""
        ph = self._current_phase_ir()
        if ph is None:
            return
        if not self._clock_frozen:
            self._phase_elapsed += n
        duration = self._phases_samples[self._phase_index]
        auto = (ph.mode == "timed" or (ph.mode == "timed_with_floor" and not self._held))
        if auto and not self._clock_frozen and ph.mode != "open" and self._phase_elapsed >= duration:
            self._goto_next_phase()

    def _goto_next_phase(self) -> None:
        # leaving the warmup phase flips coarse state to run, once.
        if self._state == "warmup":
            self._state = "run"
        self._phase_index += 1
        self._phase_elapsed = 0
        self._held = False
        if self._phase_index >= len(self.ir.session.phases):
            self._state = "stopped"
```
In `step_chunk` (Python branch, lines 635-638), replace:
```python
        self._samples_pushed += actual_chunk_size
        if self._state == "warmup" and self._samples_pushed >= self._warmup_samples:
            self._state = "run"
```
with:
```python
        self._samples_pushed += actual_chunk_size
        self._advance_if_due(actual_chunk_size)
```
In `_process_chunk` (line 651) replace the suppression flag:
```python
        ph = self._current_phase_ir()
        suppress_output = bool(ph.output_muted) if ph is not None else False
```

- [ ] **Step 4: Add a minimal `current_phase()` (full version in Task 9)**

Add to the class (near `warmup_remaining_s`):
```python
    def current_phase(self) -> dict:
        if self._rust is not None:
            return dict(self._rust.current_phase())
        return dict(self._phase_snapshot)
```
And populate the snapshot inside `_process_chunk`, right after computing `suppress_output`:
```python
        idx = self._phase_index
        if ph is not None:
            dur = self._phases_samples[idx]
            has_clock = ph.mode in ("timed", "timed_with_floor") and not self._held and not self._clock_frozen
            remaining = max(0, dur - self._phase_elapsed) / self.sample_rate_hz if has_clock else None
            self._phase_snapshot = {
                "index": idx, "name": ph.name, "mode": ph.mode,
                "output_muted": bool(ph.output_muted), "block": ph.block,
                "remaining_s": remaining, "clock_frozen": self._clock_frozen, "held": self._held,
            }
        else:
            self._phase_snapshot = {"index": -1, "name": None, "mode": None,
                                    "output_muted": False, "block": None,
                                    "remaining_s": None, "clock_frozen": False, "held": False}
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py -k "run_in_order or returns_to_warmup or rest_mutes" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/eval_.py tests/test_staged_eval.py
git commit -m "feat(eval): N-phase cursor, generalized output mute, state mapping"
```

### Task 9: advance/hold/clock-freeze/current_phase + `warmup_remaining_s` + `phase/index` tap

**Files:**
- Modify: `src/refrain/eval_.py` (add `advance_phase`, `hold`, `set_clock_frozen`; re-express `warmup_remaining_s`; add `phase/index` tap in `_capture_taps`)
- Test: `tests/test_staged_eval.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_open_phase_needs_advance():
    ev = live(HET)
    feed(ev, 1.0); feed(ev, 2.0)         # warm, b1
    assert ev.current_phase()["name"] == "rest"
    feed(ev, 5.0)                         # plenty of time
    assert ev.current_phase()["name"] == "rest"   # open never auto-advances
    assert ev.advance_phase() is True
    assert ev.current_phase()["name"] == "b2"

def test_timed_with_floor_hold_extends_and_releases():
    ev = live(HET)
    feed(ev, 1.0)                         # warm
    assert ev.hold() is True              # extend b1 past its 2 s floor
    feed(ev, 5.0)
    assert ev.current_phase()["name"] == "b1"   # held past floor
    assert ev.hold(False) is True         # re-arm countdown
    feed(ev, 0.1)                         # already past duration → advances
    assert ev.current_phase()["name"] == "rest"

def test_clock_freeze_pauses_countdown():
    ev = live(HET)
    feed(ev, 1.0)                         # warm
    feed(ev, 1.0)                         # 1 s into b1 (of 2 s)
    ev.set_clock_frozen(True)
    feed(ev, 5.0)                         # frozen — must not advance
    assert ev.current_phase()["name"] == "b1"
    assert ev.advance_phase() is True     # Next works while frozen
    assert ev.current_phase()["name"] == "rest"

def test_advance_past_last_is_noop():
    ev = live(HET)
    feed(ev, 1.0); feed(ev, 2.0)
    ev.advance_phase()                    # rest -> b2
    ev.advance_phase()                    # b2 -> stopped
    assert ev.state == "stopped"
    assert ev.advance_phase() is False

def test_phase_index_tap_present():
    ev = live(HET)
    feed(ev, 1.0)
    assert "phase/index" in ev.last_taps()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py -k "open_phase or hold_extends or clock_freeze or past_last or index_tap" -v`
Expected: FAIL — methods/tap not defined.

- [ ] **Step 3: Implement the control surface**

Add to `Evaluator` (Python branch; each forwards to `self._rust` when set — Task 13 implements the Rust side):
```python
    def advance_phase(self) -> bool:
        if self._rust is not None:
            return bool(self._rust.advance_phase())
        if self._state == "stopped":
            return False
        self._goto_next_phase()
        return True

    def hold(self, held: bool = True) -> bool:
        if self._rust is not None:
            return bool(self._rust.hold(held))
        ph = self._current_phase_ir()
        if ph is None or ph.mode != "timed_with_floor":
            return False
        self._held = bool(held)
        return True

    def set_clock_frozen(self, frozen: bool) -> None:
        if self._rust is not None:
            self._rust.set_clock_frozen(bool(frozen))
            return
        self._clock_frozen = bool(frozen)
```
Re-express `warmup_remaining_s` (Python branch):
```python
        if self._state != "warmup":
            return 0.0
        dur = self._phases_samples[self._phase_index] if self._phase_index < len(self._phases_samples) else 0
        return max(0, dur - self._phase_elapsed) / self.sample_rate_hz
```
Add the `phase/index` + `phase/output_muted` taps in `_capture_taps` (end of the method, after output taps):
```python
        taps["phase/index"] = float(self._phase_index)
        ph = self._current_phase_ir()
        taps["phase/output_muted"] = 1.0 if (ph is not None and ph.output_muted) else 0.0
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py -k "open_phase or hold_extends or clock_freeze or past_last or index_tap" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/eval_.py tests/test_staged_eval.py
git commit -m "feat(eval): advance_phase/hold/set_clock_frozen/current_phase + phase taps"
```

### Task 10: Activation masking + reward-bundle selection (R4)

**Files:**
- Modify: `src/refrain/eval_.py` (`_build_pipeline` to instantiate bundle exprs; `_process_chunk` to compute per-bundle reward, select active, mask outputs/inhibits)
- Test: `tests/test_staged_eval.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_active_block_selects_reward_bundle():
    # be(15 Hz) and ae(10 Hz) get the same flat input; assert the audio
    # output value tracks the *active* block's bundle by toggling thresholds
    # so the two bundles differ. Use distinct thresholds (5 uv vs 50 uv) so
    # sigmoid(be-"bt") and sigmoid(ae-"at") differ markedly.
    src = HET.replace('absolute(value: 5 uv) }\n  threshold "at"',
                      'absolute(value: 0 uv) }\n  threshold "at"').replace(
                      '"at" { signal = "ae"; type = absolute(value: 5 uv)',
                      '"at" { signal = "ae"; type = absolute(value: 1000 uv)')
    ev = live(src)
    feed(ev, 1.0)                 # warm
    feed(ev, 1.0)                 # b1 (beta bundle, low threshold → high reward)
    beta_out = ev.last_taps()["output/audio"]
    ev.advance_phase() if ev.current_phase()["name"] != "rest" else None
    # advance through to b2
    while ev.current_phase()["name"] != "b2" and ev.state != "stopped":
        ev.advance_phase(); feed(ev, 0.1)
    feed(ev, 0.5)
    alpha_out = ev.last_taps()["output/audio"]
    assert beta_out > alpha_out   # alpha bundle has a huge threshold → ~0 reward

def test_derives_stay_warm_regardless_of_active_block():
    ev = live(HET)
    feed(ev, 1.0); feed(ev, 1.0)  # in b1
    assert ev.last_taps()["derive/ae"] != 0.0   # alpha derive runs though beta block is active
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py -k "selects_reward_bundle or stay_warm" -v`
Expected: FAIL — reward always resolves to the single global reward; outputs not masked.

- [ ] **Step 3: Instantiate bundle expressions for warm timers**

In `_build_pipeline` (after the existing reward-component loop, ~line 487), add:
```python
        # Reward bundles (staged protocols): instantiate every bundle's
        # continuous/event so dwell timers stay warm even when inactive.
        for rb in self.ir.reward_bundles.values():
            if rb.continuous is not None:
                self._instantiate_expr(rb.continuous)
            if rb.event is not None:
                self._instantiate_expr(rb.event)
```

- [ ] **Step 4: Compute per-bundle reward and select active in `_process_chunk`**

In `_process_chunk`, after the existing `reward_continuous` / `reward_event` computation (which uses `self.ir.reward`), add bundle evaluation and active-selection. Determine the active block + bundle:
```python
        active_block = None
        if ph is not None and ph.block is not None:
            active_block = self.ir.blocks.get(ph.block)
        # Active reward bundle's continuous/event override the defaults.
        if active_block is not None and active_block.reward is not None:
            rb = self.ir.reward_bundles[active_block.reward]
            if rb.continuous is not None:
                reward_continuous = self._eval_expr(
                    rb.continuous, stream_values, control_chunks_cache, actual_chunk_size)
            if rb.event is not None:
                reward_event, reward_sub_chunks = self._eval_reward_event(
                    rb.event, stream_values, control_chunks_cache, actual_chunk_size)
```
(Keep `reward_sub_chunks` initialized to `[]` earlier in the method if not already.) Then mask outputs by the active block's `output` set. In the loop that builds `per_channel_output`, compute an "active channel" predicate:
```python
        if active_block is not None and active_block.outputs:
            active_channels = set(active_block.outputs)
        else:
            active_channels = set(self.ir.output.keys())   # all live (back-compat)
```
and treat a non-active channel like a muted one — extend the mute used for that channel:
```python
        for channel, expr in self.ir.output.items():
            chan_muted = muted | (np.True_ if channel not in active_channels else np.False_)
            values = self._eval_expr(... )
            is_event = self._is_event_channel(expr)
            if is_event:
                gated_bool = values.astype(bool) & ~chan_muted
                per_channel_output[channel] = (gated_bool, True)
            else:
                clamped = np.clip(values, 0.0, 1.0)
                gated = np.where(chan_muted, 0.0, clamped)
                per_channel_output[channel] = (gated, False)
```
(`np.True_`/`np.False_` broadcast against the boolean `muted` array.) Mask inhibits in `_compute_muted` by passing the active inhibit set: change the call to `self._compute_muted(inhibit_active, actual_chunk_size, active_block)` and in `_compute_muted` skip inhibits not in `active_block.inhibits` when that tuple is non-empty:
```python
    def _compute_muted(self, inhibit_active, chunk_size, active_block=None):
        if not inhibit_active:
            return np.zeros(chunk_size, dtype=bool)
        allowed = None
        if active_block is not None and active_block.inhibits:
            allowed = set(active_block.inhibits)
        muted = np.zeros(chunk_size, dtype=bool)
        for canonical, active in inhibit_active.items():
            short = canonical.split("/", 1)[-1]
            if allowed is not None and short not in allowed:
                continue
            action = self._inhibit_actions.get(canonical)
            if action is None or isinstance(action, impls.FlagAction):
                continue
            muted |= action.gate(active)
        return muted
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py -k "selects_reward_bundle or stay_warm" -v`
Expected: PASS.

- [ ] **Step 6: Run the warm-signal + seeding invariant test (R5 confirmation)**

Add and run:
```python
def test_setcontrol_on_inactive_block_threshold_during_warmup():
    # control-backed absolute() threshold seeded during warmup; the block it
    # belongs to is not active yet. The update must take effect.
    src = '''
    protocol "p" {
      requires { channels = ["Cz"]; sample_rate >= 256 hz }
      controls { at_uv = voltage { default = 5 uv; live_tunable = true } }
      input raw { montage = referential(active: "Cz", reference: "A1") }
      derive be { from = raw |> bandpass(band: frequency { center = 15 hz; width = ratio(2.5) }) |> magnitude() |> smooth(tau: 200 ms) }
      derive ae { from = raw |> bandpass(band: frequency { center = 10 hz; width = ratio(2.5) }) |> magnitude() |> smooth(tau: 200 ms) }
      threshold "bt" { signal = "be"; type = absolute(value: 5 uv) }
      threshold "at" { signal = "ae"; type = absolute(value: at_uv) }
      reward "br" { continuous = sigmoid(be - "bt") }
      reward "ar" { continuous = sigmoid(ae - "at") }
      output { audio = reward.continuous }
      block "beta_up"  { threshold = "bt"; reward = "br"; output = ["audio"] }
      block "alpha_up" { threshold = "at"; reward = "ar"; output = ["audio"] }
      session { phases = [
        phase { name="warm"; duration=1 s; output_muted=true },
        phase { name="b1";   duration=1 s; block="beta_up";  mode=timed },
        phase { name="b2";   duration=1 s; block="alpha_up"; mode=timed },
      ] }
    }
    '''
    ev = live(src)
    feed(ev, 0.5)                       # mid-warmup; alpha block inactive
    ev.set_control("at_uv", 42.0)       # seed inactive block's threshold
    feed(ev, 0.5)
    assert ev.last_taps()["threshold/at"] == 42.0   # took effect though b2 inactive
```
Run: `.venv/bin/python -m pytest tests/test_staged_eval.py::test_setcontrol_on_inactive_block_threshold_during_warmup -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/refrain/eval_.py tests/test_staged_eval.py
git commit -m "feat(eval): active-block masking + reward-bundle selection"
```

### Task 11: Percentile-window freeze across muted rests (R6)

**Files:**
- Modify: `src/refrain/primitive_impls.py:355-399` (`PercentileImpl.set_ingesting` + honor it in `step`)
- Modify: `src/refrain/eval_.py` (compute `freeze_ingest`; call `set_ingesting` on threshold impls before stepping)
- Test: `tests/test_staged_eval.py`

- [ ] **Step 1: Write the failing test**

```python
def test_percentile_window_freezes_during_midsession_rest():
    src = '''
    protocol "p" {
      requires { channels = ["Cz"]; sample_rate >= 256 hz }
      input raw { montage = referential(active: "Cz", reference: "A1") }
      derive e { from = raw |> bandpass(band: frequency { center = 12 hz; width = ratio(2.5) }) |> magnitude() |> smooth(tau: 50 ms) }
      threshold "t" { signal = "e"; type = percentile(target_pct: 75, window: 2 s) }
      reward { continuous = sigmoid(e - "t") }
      output { audio = reward.continuous }
      session { phases = [
        phase { name="warm"; duration=1 s; output_muted=true },
        phase { name="b1";   duration=1 s; mode=timed },
        phase { name="rest"; duration=1 s; output_muted=true; mode=timed },
        phase { name="b2";   duration=1 s; mode=timed },
      ] }
    }
    '''
    # Feed a big spike ONLY during the rest; the frozen window must not ingest it.
    ev = live(src)
    feed(ev, 1.0, val=1.0)            # warm
    feed(ev, 1.0, val=1.0)            # b1
    t_before = ev.last_taps()["threshold/t"]
    feed(ev, 1.0, val=50.0)          # rest — huge artifact, but frozen
    t_after_rest = ev.last_taps()["threshold/t"]
    assert abs(t_after_rest - t_before) < 1e-9   # window did not move during rest
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py::test_percentile_window_freezes_during_midsession_rest -v`
Expected: FAIL — the rest's spike is ingested, moving the percentile.

- [ ] **Step 3: Add an ingest gate to `PercentileImpl`**

In `src/refrain/primitive_impls.py`, in `PercentileImpl.__init__` add `self._ingesting = True`. Add:
```python
    def set_ingesting(self, ingesting: bool) -> None:
        self._ingesting = bool(ingesting)
```
Change `step` so a frozen impl emits the current percentile without growing the buffer:
```python
    def step(self, x: np.ndarray) -> np.ndarray:
        out = np.empty(x.shape[0], dtype=np.float64)
        for i, v in enumerate(x):
            if self._ingesting:
                self._buffer.append(float(v))
            arr = np.fromiter(self._buffer, dtype=np.float64) if self._buffer else np.zeros(1)
            out[i] = float(np.percentile(arr, self.target_pct))
        return out
```

- [ ] **Step 4: Drive the gate from the evaluator**

In `_process_chunk`, before the threshold loop (line 667), compute:
```python
        freeze_ingest = bool(ph.output_muted and self._phase_index > 0) if ph is not None else False
```
In the threshold loop, set the gate on impls that support it:
```python
        for t in self.ir.thresholds.values():
            impl = self._impls[id(t.threshold_call)]
            setter = getattr(impl, "set_ingesting", None)
            if setter is not None:
                setter(not freeze_ingest)
            if isinstance(impl, impls.AbsoluteThresholdImpl):
                stream_values[t.canonical_name] = impl.step(np.zeros(actual_chunk_size))
            else:
                stream_values[t.canonical_name] = impl.step(stream_values[t.signal])
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py::test_percentile_window_freezes_during_midsession_rest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/primitive_impls.py src/refrain/eval_.py tests/test_staged_eval.py
git commit -m "feat(eval): freeze percentile windows during mid-session muted rests"
```

### Task 12: Back-compat — existing protocols byte-identical

**Files:**
- Test: `tests/test_staged_eval.py`

- [ ] **Step 1: Write the test**

```python
def test_existing_examples_emit_identically():
    import glob, numpy as np
    from refrain.parser import parse_protocol
    from refrain.resolver import resolve
    from refrain.eval_ import Evaluator
    from refrain.synthetic import SignalGenerator
    for path in glob.glob("examples/*.refrain"):
        ir = resolve(parse_protocol(open(path).read()))
        ev = Evaluator.live(ir, sample_rate_hz=256,
                            channel_names=tuple(ir.requires.channels), backend="python")
        ev.start()
        gen = SignalGenerator(sample_rate_hz=256, channels=tuple(ir.requires.channels), seed=42)
        evs = []
        for _ in range(40):
            evs += ev.step_chunk(gen.next_chunk(64))
        # Smoke: it runs without error and emits a stable, finite stream.
        assert all(e.value is None or np.isfinite(e.value) for e in evs)
```
(This is a regression guard; the stronger byte-identity check is the existing `tests/test_eval_*` suite, run next.)

- [ ] **Step 2: Run the full pre-existing evaluator suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_eval_lifecycle.py tests/test_eval_taps.py tests/test_eval_composite.py tests/test_eval_control_refs.py -q`
Expected: PASS (no regressions from the cursor/masking changes).

- [ ] **Step 3: Run the new back-compat test**

Run: `.venv/bin/python -m pytest tests/test_staged_eval.py::test_existing_examples_emit_identically -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_staged_eval.py
git commit -m "test(eval): back-compat regression guard for staged-protocol changes"
```

---

## Phase D — Rust parity

### Task 13: Mirror the IR in `refrain-core/src/ir.rs`

**Files:**
- Modify: `refrain-core/src/ir.rs` (`Phase`, `Block`, `Protocol`)
- Test: `refrain-core/tests/ir_deser.rs` (append) OR a Python-side parity deser test

- [ ] **Step 1: Add the failing Rust deser test**

In `refrain-core/tests/ir_deser.rs` add:
```rust
#[test]
fn deserializes_phase_mode_block_and_blocks() {
    let json = r#"{
      "sample_rate_hz": 256.0, "channels": ["Cz"],
      "inputs": {}, "derives": {}, "thresholds": {}, "inhibits": {},
      "output": {}, "controls": {}, "topological_order": [],
      "session": { "phases": [
        { "name": "w", "duration_ms": 1000.0, "output_muted": true },
        { "name": "b", "duration_ms": 2000.0, "output_muted": false, "mode": "timed_with_floor", "block": "beta_up" }
      ]},
      "blocks": { "beta_up": { "name": "beta_up", "thresholds": ["bt"], "reward": "br", "output": ["audio"], "inhibits": [] } },
      "reward_bundles": { "br": { "continuous": null, "event": null, "combine": "all", "components": [] } }
    }"#;
    let p: refrain_core::ir::Protocol = serde_json::from_str(json).unwrap();
    let s = p.session.unwrap();
    assert_eq!(s.phases[0].mode, "timed");          // serde default
    assert_eq!(s.phases[1].mode, "timed_with_floor");
    assert_eq!(s.phases[1].block.as_deref(), Some("beta_up"));
    assert_eq!(p.blocks.get("beta_up").unwrap().reward.as_deref(), Some("br"));
    assert!(p.reward_bundles.contains_key("br"));
}
```
(Adjust the module path / field visibility to match the crate — make `ir` and the new fields `pub`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd refrain-core && cargo test deserializes_phase_mode_block_and_blocks 2>&1 | tail -20; cd ..`
Expected: FAIL to compile (`Phase` has no `mode`/`block`; `Protocol` has no `blocks`).

- [ ] **Step 3: Extend the structs with serde defaults**

In `refrain-core/src/ir.rs`, extend `Phase`:
```rust
#[derive(Debug, Clone, Deserialize)]
pub struct Phase {
    pub name: String,
    #[serde(default)]
    pub duration_ms: f64,
    #[serde(default)]
    pub output_muted: bool,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub block: Option<String>,
}
fn default_mode() -> String { "timed".to_string() }
```
Add a `Block` struct:
```rust
#[derive(Debug, Clone, Deserialize)]
pub struct Block {
    pub name: String,
    #[serde(default)]
    pub thresholds: Vec<String>,
    #[serde(default)]
    pub reward: Option<String>,
    #[serde(default)]
    pub output: Vec<String>,
    #[serde(default)]
    pub inhibits: Vec<String>,
}
```
Extend `Protocol`:
```rust
    #[serde(default)]
    pub blocks: BTreeMap<String, Block>,
    #[serde(default)]
    pub reward_bundles: BTreeMap<String, Reward>,
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd refrain-core && cargo test deserializes_phase_mode_block_and_blocks 2>&1 | tail -20; cd ..`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add refrain-core/src/ir.rs refrain-core/tests/ir_deser.rs
git commit -m "feat(rust-ir): Phase mode/block, Block, Protocol blocks/reward_bundles"
```

### Task 14: Mirror the runtime in `refrain-core/src/eval.rs` + `dsp.rs`

**Files:**
- Modify: `refrain-core/src/eval.rs` (replace `compute_warmup_samples`/`advance`/`step_chunk_events` with a phase cursor; add `advance_phase`/`hold`/`set_clock_frozen`/`current_phase`; per-bundle reward + active selection; output/inhibit masking; `phase/index` tap)
- Modify: `refrain-core/src/dsp.rs:208-241` (`Percentile` ingest gate)
- Test: deferred to the Python parity suite (Task 16) + a Rust unit test for the cursor

- [ ] **Step 1: Add a Rust unit test for the phase cursor**

In `refrain-core/src/eval.rs` (or `tests/`), add a test that builds an `Evaluator` from the staged JSON in Task 13, feeds chunks, and asserts: it leaves warmup once, a mid-session muted phase suppresses output, `advance_phase()` past the last phase yields `state()=="stopped"`, and `set_clock_frozen(true)` prevents auto-advance. Model it on the existing eval.rs tests.

- [ ] **Step 2: Run to verify it fails**

Run: `cd refrain-core && cargo test phase_cursor 2>&1 | tail -20; cd ..`
Expected: FAIL to compile (`advance_phase` etc. not defined).

- [ ] **Step 3: Add cursor state**

In the `Evaluator` struct add fields mirroring Python: `phase_index: usize`, `phase_elapsed: usize`, `held: bool`, `clock_frozen: bool`, `phases_samples: Vec<usize>`, and a `phase_snapshot` (a small struct or `BTreeMap<String, ...>`). In the constructor, replace `warmup_samples` with `phases_samples` computed from `session.phases` durations; set initial coarse `state` exactly as the Python `start()` does.

- [ ] **Step 4: Replace `advance()` with the cursor logic**

```rust
fn advance(&mut self, n: usize) {
    self.samples_pushed += n;
    let Some(session) = self.session.as_ref() else { return; };
    if self.phase_index >= session.phases.len() { return; }
    let ph = &session.phases[self.phase_index];
    if !self.clock_frozen {
        self.phase_elapsed += n;
    }
    let dur = self.phases_samples[self.phase_index];
    let auto = ph.mode == "timed" || (ph.mode == "timed_with_floor" && !self.held);
    if auto && !self.clock_frozen && ph.mode != "open" && self.phase_elapsed >= dur {
        self.goto_next_phase();
    }
}

fn goto_next_phase(&mut self) {
    if self.state == State::Warmup { self.state = State::Run; }
    self.phase_index += 1;
    self.phase_elapsed = 0;
    self.held = false;
    let n = self.session.as_ref().map(|s| s.phases.len()).unwrap_or(0);
    if self.phase_index >= n { self.state = State::Stopped; }
}
```

- [ ] **Step 5: Generalize suppression + add control methods + masking + reward selection**

In `step_chunk_events`, change `let suppress_output = self.state == State::Warmup;` to read the current phase's `output_muted`. In `eval_chunk`, compute the active block (from `phases[phase_index].block` → `self.blocks`), select the active reward bundle's `continuous`/`event` to override the defaults (compute all bundles for warm timers — mirror the Python `_build_pipeline` instantiation by compiling bundle nodes once at construction), mask non-active output channels (force muted), mask inhibits by the active block's `inhibits` set, gate `Percentile` ingestion by `output_muted && phase_index > 0`, and add the `phase/index` (and `phase/output_muted`) tap. Add public methods:
```rust
pub fn advance_phase(&mut self) -> bool {
    if self.state == State::Stopped { return false; }
    self.goto_next_phase();
    true
}
pub fn hold(&mut self, held: bool) -> bool {
    let Some(session) = self.session.as_ref() else { return false; };
    if self.phase_index >= session.phases.len() { return false; }
    if session.phases[self.phase_index].mode != "timed_with_floor" { return false; }
    self.held = held; true
}
pub fn set_clock_frozen(&mut self, frozen: bool) { self.clock_frozen = frozen; }
pub fn current_phase(&self) -> BTreeMap<String, ...> { /* return the snapshot */ }
```
In `dsp.rs` `Percentile`, add `ingesting: bool` (default true), a `set_ingesting(&mut self, bool)`, and skip the `push_back` when `!ingesting` (still emit the current percentile).

- [ ] **Step 6: Run the Rust tests**

Run: `cd refrain-core && cargo test 2>&1 | tail -25; cd ..`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add refrain-core/src/eval.rs refrain-core/src/dsp.rs
git commit -m "feat(rust-eval): N-phase cursor, masking, reward selection, percentile freeze"
```

### Task 15: Expose the new surface in the pyo3 + uniffi bindings

**Files:**
- Modify: `refrain-core/src/python.rs:65-177` (add `advance_phase`/`hold`/`set_clock_frozen`/`current_phase`)
- Modify: `refrain-core/src/mobile.rs:67-127` (same, behind the `Mutex`)
- Test: `tests/test_eval_rust_backend.py` (append parity-of-API smoke)

- [ ] **Step 1: Add pyo3 methods**

In `python.rs`, on the `RustEvaluator` `#[pymethods]` impl:
```rust
fn advance_phase(&mut self) -> bool { self.inner.advance_phase() }
fn hold(&mut self, held: bool) -> bool { self.inner.hold(held) }
fn set_clock_frozen(&mut self, frozen: bool) { self.inner.set_clock_frozen(frozen) }
fn current_phase(&self, py: Python<'_>) -> PyObject {
    // build a dict mirroring the Python current_phase() keys:
    // index, name, mode, output_muted, block, remaining_s, clock_frozen, held
}
```
Ensure `last_taps()` includes `phase/index` and `phase/output_muted` (they flow through automatically since they're in the Rust taps map).

- [ ] **Step 2: Add uniffi methods**

In `mobile.rs`, add the same four methods on the `RefrainCore` object, each locking the inner `Mutex` and delegating. Add them to the `.udl` / proc-macro interface as the file's convention requires.

- [ ] **Step 3: Rebuild the wheel**

Run: `cd refrain-core && ../.venv/bin/maturin develop --release 2>&1 | tail -5; cd ..`
Expected: build succeeds.

- [ ] **Step 4: Smoke test the Rust backend API**

```python
def test_rust_backend_exposes_phase_api():
    from refrain.parser import parse_protocol
    from refrain.resolver import resolve
    from refrain.eval_ import Evaluator
    ir = resolve(parse_protocol(HET))   # inline HET
    ev = Evaluator.live(ir, sample_rate_hz=256, channel_names=("Cz",), backend="rust")
    ev.start()
    cp = ev.current_phase()
    assert set(cp) >= {"index", "name", "mode", "block", "remaining_s", "clock_frozen", "held"}
```
Run: `.venv/bin/python -m pytest tests/test_eval_rust_backend.py::test_rust_backend_exposes_phase_api -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add refrain-core/src/python.rs refrain-core/src/mobile.rs tests/test_eval_rust_backend.py
git commit -m "feat(rust-bindings): expose advance_phase/hold/set_clock_frozen/current_phase"
```

### Task 16: Python↔Rust parity suite

**Files:**
- Test: `tests/test_staged_parity.py` (Create)

- [ ] **Step 1: Write the parity tests**

```python
import numpy as np, pytest
from refrain.parser import parse_protocol
from refrain.resolver import resolve
from refrain.eval_ import Evaluator
# import HET and the staged percentile/seeding protocols used above

refrain_core = pytest.importorskip("refrain_core")
SR = 256

def _run(src, script):
    """script: list of ('feed', seconds) | ('feed', seconds, val) | ('advance',) | ('hold', bool) | ('freeze', bool)."""
    ir = resolve(parse_protocol(src))
    out = {}
    for backend in ("python", "rust"):
        ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("Cz",), backend=backend)
        ev.start()
        taps_trace, phase_trace, events = [], [], []
        for step in script:
            if step[0] == "feed":
                seconds = step[1]; val = step[2] if len(step) > 2 else 1.0
                total = int(seconds * SR); pushed = 0
                while pushed < total:
                    n = min(64, total - pushed)
                    events += ev.step_chunk(np.full((n, 1), val))
                    pushed += n
                taps_trace.append(dict(ev.last_taps()))
                phase_trace.append(ev.current_phase())
            elif step[0] == "advance":
                ev.advance_phase()
            elif step[0] == "hold":
                ev.hold(step[1])
            elif step[0] == "freeze":
                ev.set_clock_frozen(step[1])
        out[backend] = (taps_trace, phase_trace,
                        [(round(e.timestamp_s, 6), e.channel, e.kind,
                          None if e.value is None else round(e.value, 9)) for e in events])
    return out

def _assert_parity(out):
    (pt, pp, pe), (rt, rp, re) = out["python"], out["rust"]
    assert pe == re                                   # identical event stream
    assert pp == rp                                   # identical phase introspection
    for a, b in zip(pt, rt):                           # identical taps (within fp tol)
        assert set(a) == set(b)
        for k in a:
            if isinstance(a[k], bool):
                assert a[k] == b[k]
            else:
                assert abs(float(a[k]) - float(b[k])) < 1e-9

def test_parity_full_staged_session():
    _assert_parity(_run(HET, [
        ("feed", 1.0), ("feed", 2.0), ("advance",), ("feed", 2.0),
    ]))

def test_parity_clock_freeze_and_hold():
    _assert_parity(_run(HET, [
        ("feed", 1.0), ("feed", 1.0), ("freeze", True), ("feed", 3.0),
        ("freeze", False), ("hold", True), ("feed", 3.0), ("hold", False), ("feed", 1.0),
    ]))

def test_parity_percentile_freeze():
    # reuse the percentile staged protocol from Task 11
    _assert_parity(_run(PCT_SRC, [("feed",1.0,1.0),("feed",1.0,1.0),("feed",1.0,50.0),("feed",1.0,1.0)]))
```

- [ ] **Step 2: Run the parity suite**

Run: `.venv/bin/python -m pytest tests/test_staged_parity.py -v`
Expected: PASS (all parity cases). If a tap key differs (e.g. bool coercion), reconcile via the existing `_rust_bool_tap_keys` path in `eval_.py`.

- [ ] **Step 3: Run the entire test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — `506 + new` passed; the previously-skipped Rust-backend tests now run.

- [ ] **Step 4: Commit**

```bash
git add tests/test_staged_parity.py
git commit -m "test(parity): Python<->Rust staged-protocol parity suite"
```

---

## Phase E — Example + docs

### Task 17: Worked example + CHANGELOG

**Files:**
- Create: `examples/staged_beta_alpha.refrain`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Write the example protocol**

Create `examples/staged_beta_alpha.refrain` using the spec's DSL surface (warmup → beta_up block → open rest → alpha_up block → timed rest → cooldown), with `absolute()` thresholds (baseline-fixed, per the recorder's default).

- [ ] **Step 2: Verify it parses, resolves, and round-trips IR-JSON**

Run:
```bash
.venv/bin/python -c "
from refrain.parser import parse_protocol
from refrain.resolver import resolve
from refrain.ir_json import ir_to_json
ir = resolve(parse_protocol(open('examples/staged_beta_alpha.refrain').read()))
print(len(ir.session.phases), 'phases;', list(ir.blocks), 'blocks'); ir_to_json(ir); print('ok')
"
```
Expected: prints phase/block counts and `ok`.

- [ ] **Step 3: Add it to the example-parse test corpus**

Confirm `tests/test_parser_examples.py` (or the bench protocol corpus) picks up `examples/*.refrain` automatically; if it enumerates explicitly, add the new file.

Run: `.venv/bin/python -m pytest tests/test_parser_examples.py -q`
Expected: PASS.

- [ ] **Step 4: Update CHANGELOG**

Add an entry under a new version heading summarizing staged/segmented protocols (R1–R4 + R6): N-phase runtime, `timed`/`open`/`timed_with_floor`, `advance_phase`/`hold`/`set_clock_frozen`/`current_phase`, named blocks + reward bundles, percentile freeze; note R5 is recorder-side.

- [ ] **Step 5: Commit**

```bash
git add examples/staged_beta_alpha.refrain CHANGELOG.md
git commit -m "docs(examples): staged beta/alpha heterogeneous protocol + CHANGELOG"
```

### Task 18: Final verification

- [ ] **Step 1: Full suite, both backends**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, 0 failures; Rust-backend + parity tests run (not skipped).

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check src/refrain tests && cd refrain-core && cargo clippy --quiet; cd ..`
Expected: clean (fix any findings).

- [ ] **Step 3: Map acceptance criteria → tests (manual check)**

Confirm each spec acceptance-criteria bullet maps to a passing test (Tasks 8–16). Note any gaps in the PR description.

- [ ] **Step 4: Final commit if anything was fixed**

```bash
git add -A && git commit -m "chore: lint + final verification for staged protocols"
```

---

## Notes for the implementer

- **Reward `combine="weighted"` × bundles:** per-block *weighted composites* are out of scope (spec decision). A block selects one bundle's `continuous`/`event`; the existing top-level weighted composite remains for blockless protocols. Don't try to make blocks reference components.
- **`current_phase()` wire type:** the Python dict and the pyo3 dict must carry identical keys/values (the parity suite asserts `pp == rp`). Coerce Rust numerics to match (e.g. `remaining_s` is `None` not `NaN` for open/held).
- **Bool taps:** `phase/index`/`phase/output_muted` are numeric (float) taps — do NOT add them to `_rust_bool_tap_keys`.
- **If the Rust toolchain is unavailable:** Tasks 13–16 block. Land Phases A–C (Python) behind a clear "Rust parity pending" note rather than marking the feature done — the spec requires backend parity for completion.
