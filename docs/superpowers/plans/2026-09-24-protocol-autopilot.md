# Protocol Autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a protocol declare how each clinician-tunable control may be adjusted mid-session, validate that policy at compile time, and have both engines (Python reference and Rust core) emit one identical structured piece of advice after every chunk. Protocols without a policy still get observations and direction hints.

**Architecture:**
- **Language.** Two new pieces of syntax: an `autopilot { }` section (a new `SECTION_KW`), and `as "<name>"` on elements of a reward dwell's `all_of`/`any_of` list, which becomes a new AST node `Labeled`. A per-control `autopilot = <strategy> { … }` field reuses the house `NAME = kind { … }` pattern that `seed` already uses, so it needs no grammar change.
- **Compiler.** The resolver produces `IRAutopilot` / `IRControlAutopilot` / `IRReward.check_names`, then validates the policy by tracing IR-JSON with `advisor_trace.py`. The emitter writes the new keys only when present and tags such protocols `refrain_ir_version = "0.4"`.
- **Runtime.** A pure state machine, `advisor.py`, is built **from IR-JSON**. The Python Evaluator feeds it per-chunk `ChunkFacts`. `refrain-core/src/advisor.rs` is a line-for-line port built from the same IR-JSON and fed by `eval.rs`.
- **Parity.** Three fixture families written by `gen_fixtures.py` gate the two engines: tracer output, scripted advisor scenarios, and one whole session.

**Tech Stack:** Python 3.10+ (dataclasses, NumPy, Lark Earley parser), Rust (serde, serde_json, PyO3 0.24, uniffi 0.29.5), pytest, `cargo test`. `refrain` and `refrain-core` ship in lockstep.

**Spec:** `docs/superpowers/specs/2026-09-24-protocol-autopilot-design.md` (commit `e704153`). Read it before any task. Where this plan narrows the spec, the section "Deviations from the spec" below says so, and the plan wins.

## Global Constraints

- **Lockstep release.** Ships as **v0.22.0**. Bump BOTH `pyproject.toml` files and BOTH CHANGELOGs in one `release:` commit (Task 19); `tests/test_version_lockstep.py` enforces it. Never tag before the bump merges.
- **IR union is closed. Never add an `Expr` node discriminator.** Rust's `Expr` is an internally-tagged serde enum (`refrain-core/src/ir.rs` ~L248), so an unknown variant fails the whole document; `tests/test_ir_json_schema.py::test_unknown_expr_node_is_rejected` defends this. New *fields* are free. `Labeled` is an **AST-only** node and must never reach IR.
- **Omit-when-unused wire idiom.** `autopilot`, `controls.<n>.autopilot` and `reward.check_names` are emitted **only when present**. Every protocol that uses none of them keeps a **byte-identical** IR-JSON and `content_hash` and its current IR version.
- **Version gate.** `_protocol_ir_version` returns `"0.4"` iff any new key is present. Rust `SUPPORTED_IR_VERSIONS` gains `"0.4"`. An engine without it refuses 0.4 at load (SPEC §9.3).
- **Advice is never a tap.** `refrain-core/tests/taps.rs` asserts exact tap key-set equality. Advice has its own accessors, like `seed_report()`.
- **Determinism.** Time is counted in **samples**, never wall clock. No randomness. Ids are the counter `adv-NNNN`. Numbers are rounded with `r6(x) = floor(x·1e6 + 0.5)/1e6` (mirrored for negatives) and percentages shown as `floor(x·100 + 0.5)`, the same formula in both engines. Do not use Python `round()` in the advisor, because it rounds half-to-even and Rust's `round()` does not.
- **Mirror rule.** `src/refrain/advisor.py` ↔ `refrain-core/src/advisor.rs` and `src/refrain/advisor_trace.py` ↔ the `trace` section of `advisor.rs` are ports of each other: same function names (snake_case), same order of operations, same message strings **byte for byte**. Any change to one lands in the same commit as the other once Task 12 exists.
- **Surface syntax.** Fields inside a block are separated by newlines **or** `;`, list elements by `,`. Protocol fixtures are substituted with `%` (never `.format` — the bodies are full of `{}`). Use the verified fixtures below verbatim.
- **Environment.** Python: `.venv/bin/python` (create it with `uv venv .venv && uv pip install --python .venv/bin/python -e ".[eval,dev]"` if missing). Rust: `~/.cargo/bin/cargo`. Run pytest with `-p no:cacheprovider`. Baseline before this work: `902 passed, 28 skipped` (the Rust-backend tests skip until the wheel is built).
- **Plain-language errors.** Every `ResolveError` names the control, entry or check and says what is wrong in words a protocol author understands.

## Deviations from the spec (decided while planning; the plan wins)

| Spec said | Plan does | Why |
|---|---|---|
| §3.2 a single (non-list) dwell condition may be named | Names only on elements of `all_of([...])`/`any_of([...])`. Write `all_of([cond as "x"])` for one check. | The grammar attaches `as` to array elements only. A one-element `all_of` is equivalent. |
| §3.2 names unique within a bundle | Names unique across the **whole protocol** | `fixes`, `limiter` and `tighten_first` refer to a check by name alone. |
| §3.4 per-knob `watch` override | Dropped; per-knob `between_moves` kept | A per-knob evidence window would need a second window per knob. YAGNI. |
| §3.4 eligible kinds include `duration` | `number`, `percent`, `voltage`, `frequency` only | No duration control is a reward lever in any shipped protocol. |
| §3.8 `final autopilot` | Not supported | Compose enforces `final` only on named decls and controls. YAGNI. |
| §4.2 "session paused" | means `set_clock_frozen(True)`. `hold()` is **not** a pause: it keeps a floor phase running. | Matches Evaluator semantics. |
| §5.1 "byte-identical JSON" | Parity compares **parsed** JSON values (numbers compared as f64) | Python and Rust print floats differently (`1e-06` vs `0.000001`). The values are identical, which is what matters. |
| §5.2 some keys present only sometimes | Every key is always present; absent values are `null` | Simpler for hosts, simpler parity. |
| §4.4 no-reward protocols | New reason code `observing` | Protocols with no reward condition need a non-judging state. |
| §4.6 hints "where no policy exists" | Hints only when the protocol declares **no knob policies at all**. With any policy, an uncovered limiter holds `no_knob`. | An author who wrote policies and left one check out made a choice. |
| V5 (knob feeds no check) | Enforced by V2 | A knob that feeds no check cannot feed the check it `fixes`. |

## Verified Protocol Fixtures

`AP` (Task 3, `tests/_autopilot_fixtures.py`) was compiled with today's compiler with its placeholders empty: no errors, IR v0.1, in both `threshold_style` bindings. The bench protocol in Task 16 was compiled the same way, without its new syntax. Everything the new syntax adds is exercised by the tasks' own tests.

---

## File Structure

**Python (`src/refrain/`)**
- `grammar.lark`: add `autopilot` to `SECTION_KW`; `array_elem` rule. (Syntax.)
- `ast.py`: add `Labeled`. (AST shape.)
- `parser.py`: `array_elem` transformer. (Parse.)
- `unparser.py`: print `Labeled`. (Round-trip.)
- `ir.py`: `IRGuard`, `IRLimiter`, `IRAutopilot`, `IRControlAutopilot`; `IRReward.check_names`, `IRControl.autopilot`, `IRProtocol.autopilot`. (IR shapes.)
- `resolver.py`: hoist `autopilot`; strip check labels; parse section + control policies; trace-based validation. (Compile-time semantics.)
- `fanout.py`: reject named checks under fan-out. (Guard.)
- `advisor_trace.py` **new**: reward-check tracing over IR-JSON. (Shared by resolver and advisor.)
- `ir_json.py`: emit new keys; version 0.4. (Wire.)
- `advisor.py` **new**: the state machine. (Runtime.)
- `eval_.py`: build/feed the advisor; host API; `set_control`/seed hooks; Rust delegation. (Runtime wiring.)
- `server.py`: advertise 0.4.
- `schema/ir-json-v0.4.schema.json` **new**.

**Rust (`refrain-core/src/`)**
- `ir.rs`: new decl structs, `ControlDecl` fields, `Reward.check_names`, version 0.4.
- `advisor.rs` **new**: tracer + advisor port.
- `lib.rs`: `pub mod advisor;`.
- `eval.rs`: advisor field; facts; hooks; accessors.
- `python.rs`, `mobile.rs`: expose accessors (JSON strings). Regenerated `bindings/`.

**Tests / fixtures**
- `tests/_autopilot_fixtures.py` **new**, `tests/test_parser_autopilot.py`, `tests/test_resolve_autopilot.py`, `tests/test_advisor_trace.py`, `tests/test_ir_json_autopilot.py`, `tests/test_advisor.py`, `tests/test_eval_advice.py` (all **new**).
- `refrain-core/tests/advisor_parity.rs` **new**; `refrain-core/tests/ir_deser.rs` (extend); `refrain-core/tools/gen_fixtures.py` (extend); `bench/protocols/autopilot_alpha_theta.refrain` **new**.

**Docs / example**
- `examples/alpha_theta_autopilot.refrain` **new**; `docs/AUTOPILOT-AUTHORING.md` **new**; `docs/autopilot-feature-request-response.md` **new**; `docs/SPEC.md`, `docs/EMBEDDING.md`, `docs/IR-JSON.md`, `CHANGELOG.md`, `refrain-core/CHANGELOG.md`.

## Review Focus

Most likely to bite a real user and not otherwise exercised. Each line has a pinned test in the named task.

1. **A clinician sets a knob outside the autopilot `limits` by hand**, e.g. `crossover_target = 0.45` when limits are (0.5, 1.0). Expected: no proposal that jumps *away* from the requested direction. The advisor holds `at_limit` rather than "easing" by raising to 0.5. Pinned in Task 9 (`test_manual_value_outside_limits_never_proposes_wrong_direction`).
2. **Guards keep interrupting so clean evidence trickles in**: 20 s of clean signal spread over 3 minutes. Expected: stale clean time older than `2 × watch` is dropped and the advisor keeps `collecting`. It must never judge on scraps. Pinned in Task 9 (`test_stale_clean_time_drops_out`).
3. **The host auto-applies a stale id** (advice changed between display and click). Expected: `AdviceError` and no knob change. Pinned in Task 9 (`test_apply_stale_id_is_refused_and_value_unchanged`).
4. **An existing protocol recompiled after this release**: every example keeps its exact IR-JSON, hash and version. Pinned in Task 7 (`test_existing_examples_gain_no_autopilot_keys`).
5. **Rust backend through Python** (`backend="rust"`): `advice()` returns a dict equal to the Python backend's, and errors surface as `AdviceError`. Pinned in Task 15 (`test_rust_backend_advice_matches_python`).

---
## Task 1: Syntax — `autopilot` section and `as "<name>"` on array elements

**Files:**
- Modify: `src/refrain/grammar.lark:52` (SECTION_KW), `:100` (array)
- Modify: `src/refrain/ast.py` (new `Labeled`, add to `__all__` ~L279)
- Modify: `src/refrain/parser.py` (`_AstBuilder`, next to `array` ~L296)
- Modify: `src/refrain/unparser.py:_emit_expr` (~L114)
- Test: `tests/test_parser_autopilot.py` (new)

**Interfaces:**
- Produces: `A.Labeled(expr: A.Expr, label: str)`, a frozen slots dataclass subclassing `A.Expr`. Appears only as an element of `A.Array.elements`. `SectionBlock(keyword="autopilot")` parses; `amend autopilot { }` parses automatically (it reuses `SECTION_KW`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_parser_autopilot.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Parser surface for protocol autopilot: `autopilot { }` and `as "<name>"`."""

from refrain import ast as A
from refrain import parse, unparse

SRC = '''protocol "p" {
  meta { version = "1.0"; evidence = "clinical"; description = "x" }
  reward {
    event = dwell(condition: all_of([above("a", "b") as "theta", above("c", k)]), duration: 1 s)
  }
  autopilot {
    evidence = "expert_opinion"
    watch = 2 min
    emg = guard { max = 15%; say = "Muscle." }
  }
}'''


def _section(proto, kw):
    return next(s for s in proto.body if isinstance(s, A.SectionBlock) and s.keyword == kw)


def test_labeled_array_element_parses():
    proto = parse(SRC).protocol
    dwell = _section(proto, "reward").body[0].value
    arr = dwell.args[0].value.args[0].value
    assert isinstance(arr, A.Array)
    first, second = arr.elements
    assert isinstance(first, A.Labeled)
    assert first.label == "theta"
    assert isinstance(first.expr, A.Call) and first.expr.callee == "above"
    assert isinstance(second, A.Call)          # unlabeled elements are unchanged


def test_autopilot_section_parses():
    body = _section(parse(SRC).protocol, "autopilot").body
    entries = {s.target: s.value for s in body}
    assert entries["evidence"] == A.StringLit("expert_opinion")
    assert entries["watch"] == A.NumberLit(2.0, "min")
    assert isinstance(entries["emg"], A.BlockExpr) and entries["emg"].name == "guard"


def test_amend_autopilot_parses():
    f = parse('protocol "c" extends "p" { amend autopilot { watch = 3 min } }')
    stmt = f.protocol.body[0]
    assert isinstance(stmt, A.AmendDecl) and stmt.target_kw == "autopilot"


def test_round_trip_keeps_labels_and_section():
    f = parse(SRC)
    text = unparse(f)
    assert 'as "theta"' in text
    assert parse(text) == f
    assert unparse(parse(text)) == text
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_parser_autopilot.py -v -p no:cacheprovider`
Expected: FAIL with a `ParseError` (`as` is unexpected, `autopilot` is unknown).

- [ ] **Step 3: Grammar**

In `src/refrain/grammar.lark`, change line 52 and line 100:

```lark
SECTION_KW: "meta" | "requires" | "reward" | "output" | "controls" | "session" | "groups" | "bands" | "autopilot"
```

```lark
array: "[" (array_elem ("," array_elem)* ","?)? "]"
// `expr as "name"` names a reward check (SPEC §4.7.1). `?` inlines the plain
// case so unlabeled elements keep their exact old tree shape.
?array_elem: expression ("as" string_lit)?
```

- [ ] **Step 4: AST node**

In `src/refrain/ast.py`, after the `Array` class (~L221-225):

```python
@dataclass(frozen=True, slots=True)
class Labeled(Expr):
    """`<expr> as "<label>"` — an array element carrying a name (SPEC §4.7.1).

    Only legal as an element of a reward dwell's `all_of([...])`/`any_of([...])`
    list; the resolver strips it and records the label in the reward's
    `check_names`. Never reaches IR."""

    expr: Expr
    label: str
```

Add `"Labeled",` to `__all__`.

- [ ] **Step 5: Parser transformer**

In `src/refrain/parser.py`, inside `_AstBuilder` next to `def array`:

```python
    def array_elem(self, meta, items):
        # Only reached when `as "<label>"` is present (the rule is `?`-inlined).
        return A.Labeled(expr=items[0], label=items[1].value, loc=_loc(meta))
```

- [ ] **Step 6: Unparser**

In `src/refrain/unparser.py` `_emit_expr`, before the `A.Array` branch:

```python
    if isinstance(expr, A.Labeled):
        return f"{_emit_expr(expr.expr)} as {_emit_expr(A.StringLit(expr.label))}"
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_parser_autopilot.py tests/test_parser_examples.py tests/test_parser_composition.py -v -p no:cacheprovider`
Expected: PASS (the new tests, and the existing round-trip tests unchanged).

- [ ] **Step 8: Commit**

```bash
git add src/refrain/grammar.lark src/refrain/ast.py src/refrain/parser.py src/refrain/unparser.py tests/test_parser_autopilot.py
git commit -m "feat(parser): autopilot section and 'as \"name\"' on array elements"
```

---

## Task 2: IR shapes

**Files:**
- Modify: `src/refrain/ir.py` (`IRReward` ~L248, `IRControl` ~L276, `IRProtocol` ~L359, `__all__` ~L384)
- Test: `tests/test_ir_autopilot_shapes.py` (new)

**Interfaces:**
- Produces (all `@dataclass(frozen=True, slots=True)`):
  - `IRGuard(inhibit: str, max_frac: float, say: str | None, loc=None)`
  - `IRLimiter(check: str, say: str, loc=None)`
  - `IRAutopilot(evidence: str, citations: tuple[str, ...], rationale: str, reviewed: str | None = None, reward_target: tuple[float, float] | None = None, phases: tuple[str, ...] | None = None, watch_ms: float | None = None, between_moves_ms: float | None = None, equipment_settle_ms: float | None = None, tighten_first: tuple[str, ...] = (), guards: tuple[IRGuard, ...] = (), limiters: tuple[IRLimiter, ...] = (), loc=None)`. `reward_target` holds **fractions** (0.10, 0.35).
  - `IRControlAutopilot(strategy: str, fixes: str, higher_is: str, apply: str, limits: tuple[float, float], round_to: float | None = None, say: str | None = None, between_moves_ms: float | None = None, step: float | None = None, from_entity: str | None = None, window_ms: float | None = None, percentile: float | None = None, citations: tuple[str, ...] = (), loc=None)`. `step` is in knob units for `fixed_step` and a **fraction** (0.10) for `proportional_step`.
  - `IRReward.check_names: tuple = ()` (entries `str | None`, aligned with condition indices)
  - `IRControl.autopilot: IRControlAutopilot | None = None`
  - `IRProtocol.autopilot: IRAutopilot | None = None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ir_autopilot_shapes.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
from refrain.ir import (
    IRAutopilot, IRControlAutopilot, IRGuard, IRLimiter, IRReward,
)


def test_new_fields_default_to_absent():
    r = IRReward(continuous=None, event=None)
    assert r.check_names == ()


def test_autopilot_shapes_construct():
    ap = IRAutopilot(
        evidence="expert_opinion", citations=("x",), rationale="y",
        guards=(IRGuard(inhibit="emg", max_frac=0.15, say=None),),
        limiters=(IRLimiter(check="hb", say="coach"),),
    )
    assert ap.reward_target is None and ap.tighten_first == ()
    pol = IRControlAutopilot(
        strategy="fixed_step", fixes="crossover", higher_is="harder",
        apply="auto", limits=(0.5, 1.0), step=0.05,
    )
    assert pol.round_to is None and pol.citations == ()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_ir_autopilot_shapes.py -v -p no:cacheprovider`
Expected: FAIL (`ImportError: cannot import name 'IRAutopilot'`).

- [ ] **Step 3: Add the dataclasses and fields**

In `src/refrain/ir.py`, add after `IRControlSeed` (~L273):

```python
@dataclass(frozen=True, slots=True)
class IRControlAutopilot:
    """A control's autopilot policy (SPEC §4.9.5): how the advisor may nudge it."""

    strategy: str                     # "fixed_step" | "proportional_step" | "rebaseline"
    fixes: str                        # the named reward check it addresses
    higher_is: str                    # "harder" | "easier"
    apply: str                        # "auto" | "suggest"
    limits: tuple[float, float]       # autopilot bounds, knob units
    round_to: float | None = None
    say: str | None = None
    between_moves_ms: float | None = None
    step: float | None = None         # fixed: knob units; proportional: fraction (0.10)
    from_entity: str | None = None    # rebaseline: "derive/<name>"
    window_ms: float | None = None    # rebaseline
    percentile: float | None = None   # rebaseline, 1..99
    citations: tuple[str, ...] = ()
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRGuard:
    """`<inhibit> = guard { max; say }` inside `autopilot { }`."""

    inhibit: str
    max_frac: float                   # declared as a percent, stored as 0..1
    say: str | None
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRLimiter:
    """`<check> = limiter { say }`: message when that check limits and no knob fixes it."""

    check: str
    say: str
    loc: Loc | None = None


@dataclass(frozen=True, slots=True)
class IRAutopilot:
    """The protocol-wide `autopilot { }` section (SPEC §4.12). Absent settings
    are None and resolved to built-in defaults by the advisor at runtime."""

    evidence: str
    citations: tuple[str, ...]
    rationale: str
    reviewed: str | None = None
    reward_target: tuple[float, float] | None = None   # fractions, 0..1
    phases: tuple[str, ...] | None = None
    watch_ms: float | None = None
    between_moves_ms: float | None = None
    equipment_settle_ms: float | None = None
    tighten_first: tuple[str, ...] = ()
    guards: tuple[IRGuard, ...] = ()
    limiters: tuple[IRLimiter, ...] = ()
    loc: Loc | None = None
```

In `IRReward`, add after `components`:

```python
    check_names: tuple = ()  # tuple[str | None, ...], aligned with dwell condition indices
```

In `IRControl`, add after `seed`:

```python
    autopilot: IRControlAutopilot | None = None   # autopilot policy, or None
```

In `IRProtocol`, add after `reward_bundles` (before `loc`):

```python
    autopilot: IRAutopilot | None = None
```

Add `"IRAutopilot", "IRControlAutopilot", "IRGuard", "IRLimiter",` to `__all__`.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_ir_autopilot_shapes.py tests/test_resolver.py tests/test_ir_json.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/ir.py tests/test_ir_autopilot_shapes.py
git commit -m "feat(ir): autopilot IR shapes and reward check_names"
```

---

## Task 3: Named reward checks in the resolver (+ fixtures, + fan-out guard)

**Files:**
- Create: `tests/_autopilot_fixtures.py`
- Modify: `src/refrain/resolver.py` (`_resolve_reward` ~L1761, `_resolve_reward_bundle` ~L1736, `_resolve_value_expr` ~L1970, `_resolve_stream_expr` ~L2015; new method `_strip_check_labels`)
- Modify: `src/refrain/fanout.py` (`fan_out` after L66; `band_fan_out` after L139; new `_reject_check_labels`)
- Test: `tests/test_resolve_autopilot.py` (new)

**Interfaces:**
- Consumes: `A.Labeled` (Task 1), `IRReward.check_names` (Task 2).
- Produces: `_Resolver._strip_check_labels(event_ast) -> tuple[A.Expr, tuple[str | None, ...]]`; `fanout._reject_check_labels(file_ast, what: str) -> None`; the fixture module used by every later Python test: `ap(**overrides) -> str`, `plain(**overrides) -> str`, `AP`, `DEFAULTS`, `AUTOPILOT_BLOCK`, `XOVER_AP`, `T_PCT_AP`, `T_UV_AP`.

- [ ] **Step 1: Create the fixture module**

Create `tests/_autopilot_fixtures.py`. The base protocol is compile-verified. Every placeholder has a default that yields the full, valid policy protocol. Tests override single placeholders.

```python
# tests/_autopilot_fixtures.py — autopilot protocol fixtures.
# AP's base (all placeholders empty) is compile-verified against the live
# compiler (IR v0.1 in both threshold_style bindings). Substitute with `%`
# (NOT .format — the body is full of literal braces). Use `ap(...)`.

AP = '''protocol "ap_demo" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "autopilot demo" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "t_env" { from = "raw"; pipeline = [ magnitude() ] }
  derive "a_env" { from = "raw"; pipeline = [ magnitude() ] }
  derive "ratio" { formula = "t_env" / "a_env" }
  threshold "t_t" {
    signal = "t_env"
    type = threshold_style == "baseline"
             ? absolute(value: t_uv)
             : percentile(target_pct: t_pct, window: 2 min)
    live_tunable = true
  }
  inhibit "emg" {
    metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
    threshold = percentile(target_pct: %(emg_thr)s, window: 2 min)
    action    = mute(release: 200 ms)
  }
  reward {
    event = dwell(
      condition: all_of([
        above("t_env", "t_t")%(theta_as)s,
        above("ratio", %(xover_thr)s)%(xover_as)s,
      ]),
      duration: 1000 ms
    )
  }
  output { audio_chime = reward.event }
  controls {
    threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }
    t_pct  = percent { default = 15; range = (15, 70); live_tunable = true; %(t_pct_ap)s }
    t_uv   = voltage { default = 8.0 uV; range = (2.0 uV, 30.0 uV); live_tunable = true; %(t_uv_ap)s }
    xover  = number  { default = 0.60; range = (0.5, 1.0); live_tunable = true; label = "Crossover target"; %(xover_ap)s }
    emg_pct = percent { default = 95; range = (50, 99); live_tunable = true; %(emg_ap)s }
    %(extra_controls)s
  }
  %(autopilot)s
  session { phases = [
    phase { name = "settle"; duration = 10 s;  output_muted = true },
    phase { name = "train1"; duration = 300 s },
    phase { name = "rest";   duration = 10 s;  output_muted = true },
    phase { name = "train2"; duration = 300 s },
  ] }
}'''

AUTOPILOT_BLOCK = '''autopilot {
    evidence         = "expert_opinion"
    citation         = "Test policy"
    rationale        = "Test rationale"
    reward_target    = (10%, 35%)
    phases           = ["train1", "train2"]
    watch            = 20 s
    between_moves    = 30 s
    equipment_settle = 5 s
    tighten_first    = ["crossover", "theta"]
    emg = guard { max = 15%; say = "Muscle artifact." }
  }'''

XOVER_AP = ('autopilot = fixed_step { fixes = "crossover"; step = 0.05; higher_is = "harder"; '
            'limits = (0.5, 1.0); apply = "auto"; round_to = 0.01 }')
T_PCT_AP = ('autopilot = fixed_step { fixes = "theta"; step = 5; higher_is = "harder"; '
            'limits = (15, 40); apply = "suggest"; only_when = threshold_style == "adaptive" }')
T_UV_AP = ('autopilot = proportional_step { fixes = "theta"; step = 10%; higher_is = "harder"; '
           'apply = "suggest"; round_to = 0.1 uV; only_when = threshold_style == "baseline" }')

DEFAULTS = {
    "theta_as": ' as "theta"',
    "xover_as": ' as "crossover"',
    "xover_thr": "xover",
    "emg_thr": "emg_pct",
    "t_pct_ap": T_PCT_AP,
    "t_uv_ap": T_UV_AP,
    "xover_ap": XOVER_AP,
    "emg_ap": "",
    "extra_controls": "",
    "autopilot": AUTOPILOT_BLOCK,
}

# Placeholders that yield today's (pre-autopilot) protocol: no names, no policy.
PLAIN = {k: "" for k in DEFAULTS} | {"xover_thr": "xover", "emg_thr": "emg_pct"}


def ap(**overrides: str) -> str:
    """The full-policy protocol with the named placeholders replaced."""
    return AP % (DEFAULTS | overrides)


def plain(**overrides: str) -> str:
    """The pre-autopilot protocol (no names, no policies) with overrides."""
    return AP % (PLAIN | overrides)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_resolve_autopilot.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Resolver: named reward checks, the autopilot section, control policies,
and the trace-based cross-checks (V1–V17 in the spec)."""

import pytest

from refrain import parse
from refrain.compile_json import compile_to_ir_json
from refrain.fanout import _reject_check_labels
from refrain.resolver import ResolveError, resolve
from tests._autopilot_fixtures import ap, plain


def _ir(src, **bindings):
    return resolve(parse(src), bindings=bindings or None)


def _err(src, **bindings) -> str:
    res = compile_to_ir_json(src, bindings=bindings or None)
    assert res.errors, "expected a resolve error"
    return res.errors[0].message


# --- named checks ---------------------------------------------------------

def test_check_names_recorded_in_condition_order():
    assert _ir(plain(theta_as=' as "theta"', xover_as=' as "crossover"')).reward.check_names == (
        "theta", "crossover")


def test_unnamed_checks_leave_check_names_empty():
    assert _ir(plain()).reward.check_names == ()


def test_partially_named_checks_use_none():
    assert _ir(plain(xover_as=' as "crossover"')).reward.check_names == (None, "crossover")


def test_duplicate_check_name_is_an_error():
    msg = _err(plain(theta_as=' as "x"', xover_as=' as "x"'))
    assert "'x'" in msg and "twice" in msg


def test_label_outside_reward_condition_list_is_an_error():
    src = plain().replace(
        "output { audio_chime = reward.event }",
        'output { audio_chime = reward.event; x = all_of([reward.event.holds as "h"]) }')
    assert "reward check" in _err(src)


def test_fanout_rejects_labels():
    with pytest.raises(ResolveError, match="per-site"):
        _reject_check_labels(parse(plain(theta_as=' as "theta"')), "per-site")
    _reject_check_labels(parse(plain()), "per-site")   # no labels: no error
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py -v -p no:cacheprovider`
Expected: FAIL. `_reject_check_labels` doesn't exist, and `check_names` stays `()`.

- [ ] **Step 4: Strip labels in the resolver**

In `src/refrain/resolver.py`, add this method to `_Resolver` near `_resolve_reward_bundle`:

```python
    def _strip_check_labels(self, event_ast):
        """Remove `as "<name>"` from a reward dwell's all_of/any_of list.

        Returns the label-free AST (resolved exactly as before) and the names
        aligned with the condition indices the evaluator exposes as
        `reward/condition[i]` (None for an unnamed check; `()` if none named)."""
        if not (isinstance(event_ast, A.Call) and event_ast.callee == "dwell"):
            return event_ast, ()
        names: tuple = ()
        new_args = []
        for i, arg in enumerate(event_ast.args):
            is_condition = arg.name == "condition" or (arg.name is None and i == 0)
            cond = arg.value
            if not (
                is_condition
                and isinstance(cond, A.Call)
                and cond.callee in ("all_of", "any_of")
                and cond.args
                and isinstance(cond.args[0].value, A.Array)
            ):
                new_args.append(arg)
                continue
            arr = cond.args[0].value
            seen: set[str] = set()
            for el in arr.elements:
                if isinstance(el, A.Labeled):
                    if el.label in seen:
                        raise ResolveError(
                            f"reward check name {el.label!r} is used twice", loc=el.loc)
                    seen.add(el.label)
            if seen:
                names = tuple(el.label if isinstance(el, A.Labeled) else None
                              for el in arr.elements)
            bare = A.Array(
                elements=tuple(el.expr if isinstance(el, A.Labeled) else el
                               for el in arr.elements),
                loc=arr.loc,
            )
            first = A.Arg(name=cond.args[0].name, value=bare, loc=cond.args[0].loc)
            new_cond = A.Call(callee=cond.callee, args=(first,) + cond.args[1:], loc=cond.loc)
            new_args.append(A.Arg(name=arg.name, value=new_cond, loc=arg.loc))
        return A.Call(callee=event_ast.callee, args=tuple(new_args), loc=event_ast.loc), names
```

In `_resolve_reward`, replace

```python
        event_ir = self._resolve_stream_expr(event_expr) if event_expr is not None else None
```

with

```python
        check_names: tuple = ()
        if event_expr is not None:
            event_expr, check_names = self._strip_check_labels(event_expr)
        event_ir = self._resolve_stream_expr(event_expr) if event_expr is not None else None
```

and add `check_names=check_names,` to the final `IRReward(...)` in that method.

In `_resolve_reward_bundle`, make the same change: strip before `event_ir = ...` and pass `check_names=check_names` to its returned `IRReward(...)`.

At the top of both `_resolve_value_expr` and `_resolve_stream_expr`, before any other `isinstance` branch:

```python
        if isinstance(expr, A.Labeled):
            raise ResolveError(
                f'`as "{expr.label}"` names a reward check and is only allowed on an '
                "element of a reward dwell's all_of([...])/any_of([...]) list",
                loc=expr.loc,
            )
```

- [ ] **Step 5: Fan-out guard**

In `src/refrain/fanout.py`, add near the top (after the imports):

```python
def _reject_check_labels(file_ast: A.File, what: str) -> None:
    """Named reward checks (`as "<name>"`) are not supported together with
    fan-out yet: replication would duplicate or rename the checks."""

    def walk(node) -> None:
        if isinstance(node, A.Labeled):
            raise ResolveError(
                f'named reward checks (`as "{node.label}"`) cannot be combined with '
                f"{what} fan-out yet",
                loc=node.loc,
            )
        if isinstance(node, tuple):
            for x in node:
                walk(x)
        elif isinstance(node, A.Node):
            for f in dataclasses.fields(node):
                if f.name != "loc":
                    walk(getattr(node, f.name))

    walk(file_ast)
```

Add `import dataclasses` to the imports. Call `_reject_check_labels(file_ast, "per-site")` in `fan_out` immediately **after** the early `return file_ast` at L65-66, and `_reject_check_labels(file_ast, "band")` in `band_fan_out` immediately **after** its last early return (L136-139). Protocols with nothing to fan out never hit the guard.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py tests/test_fanout.py tests/test_resolver.py tests/test_eval_taps.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/_autopilot_fixtures.py tests/test_resolve_autopilot.py src/refrain/resolver.py src/refrain/fanout.py
git commit -m "feat(resolve): named reward checks (as \"name\") recorded as check_names"
```

---

## Task 4: Resolve the `autopilot { }` section

**Files:**
- Modify: `src/refrain/resolver.py` (`__init__` ~L160-169 and ~L199; `_hoist` ~L280-298; `resolve()` ~L234-273; new helpers)
- Test: `tests/test_resolve_autopilot.py` (extend)

**Interfaces:**
- Consumes: `IRAutopilot`, `IRGuard`, `IRLimiter` (Task 2); `self.reward_ir.check_names`, `self._reward_bundles` (Task 3).
- Produces: `_Resolver._resolve_autopilot(session_ir) -> IRAutopilot | None`; `_Resolver._all_check_names() -> set[str]`; `IRProtocol.autopilot` populated. Settings stored raw: `watch_ms`, `between_moves_ms` and `equipment_settle_ms` in ms, `reward_target` and guard `max_frac` as fractions.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_resolve_autopilot.py`)

```python
# --- the autopilot section ------------------------------------------------

def test_autopilot_section_resolves():
    a = _ir(ap()).autopilot
    assert a.evidence == "expert_opinion"
    assert a.citations == ("Test policy",)
    assert a.reward_target == (0.10, 0.35)
    assert a.phases == ("train1", "train2")
    assert (a.watch_ms, a.between_moves_ms, a.equipment_settle_ms) == (20000.0, 30000.0, 5000.0)
    assert a.tighten_first == ("crossover", "theta")
    assert [(g.inhibit, g.max_frac, g.say) for g in a.guards] == [("emg", 0.15, "Muscle artifact.")]


def test_protocol_without_block_has_no_autopilot():
    assert _ir(plain()).autopilot is None


def test_citation_list_is_accepted():
    src = ap().replace('citation         = "Test policy"', 'citation = ["A 2020", "B 2021"]')
    assert _ir(src).autopilot.citations == ("A 2020", "B 2021")


@pytest.mark.parametrize("line, needle", [
    ('evidence         = "expert_opinion"', "evidence"),
    ('citation         = "Test policy"', "citation"),
    ('rationale        = "Test rationale"', "rationale"),
])
def test_provenance_is_required(line, needle):
    assert needle in _err(ap().replace(line, ""))


def test_evidence_must_be_a_known_level():
    msg = _err(ap().replace('"expert_opinion"', '"demo"'))
    assert "evidence" in msg and "expert_opinion" in msg


@pytest.mark.parametrize("target", ["(35%, 10%)", "(10, 35)", "(0%, 50%)", "(10%)"])
def test_bad_reward_target(target):
    assert "reward_target" in _err(ap().replace("(10%, 35%)", target))


def test_phase_must_exist_and_be_unmuted():
    assert "'nope'" in _err(ap().replace('["train1", "train2"]', '["train1", "nope"]'))
    assert "muted" in _err(ap().replace('["train1", "train2"]', '["rest"]'))


def test_guard_must_name_an_inhibit():
    assert "inhibit" in _err(ap().replace("emg = guard", "delta = guard"))


def test_unknown_setting_is_an_error():
    assert "wach" in _err(ap().replace("watch            =", "wach ="))


def test_limiter_and_tighten_first_must_name_checks():
    src = ap().replace('emg = guard { max = 15%; say = "Muscle artifact." }',
                       'emg = guard { max = 15%; say = "M." }\n    nope = limiter { say = "x" }')
    assert "'nope'" in _err(src)
    assert "'zzz'" in _err(ap().replace('["crossover", "theta"]', '["zzz"]'))


def test_duration_settings_must_be_positive_durations():
    assert "watch" in _err(ap().replace("watch            = 20 s", "watch = 20"))
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py -v -p no:cacheprovider`
Expected: FAIL (`unknown section block keyword 'autopilot'` / `autopilot` is None).

- [ ] **Step 3: Hoist and wire**

In `_Resolver.__init__`, next to the `groups_ast`/`bands_ast` slots (~L166-169):

```python
        self.autopilot_ast: A.SectionBlock | None = None
```

and next to `self._pending_seeds` (~L199):

```python
        self._pending_control_ap: dict[str, _PendingControlAutopilot] = {}
```

In `_hoist`, add `"autopilot": "autopilot_ast",` to the keyword→attribute dict.

In `resolve()`, after `self._resolve_control_seeds(session_ir)`:

```python
        autopilot_ir = self._resolve_autopilot(session_ir)
        self._resolve_control_autopilots(autopilot_ir)
```

In the `IRProtocol(...)` construction, add `autopilot=autopilot_ir,` and bind the result so a later task can post-validate it:

```python
        protocol = IRProtocol(
            ...,                        # unchanged fields
            autopilot=autopilot_ir,
            loc=proto.loc,
        )
        return self._validate_autopilot_trace(protocol)
```

Add these stubs now; Tasks 5 and 8 replace their bodies:

```python
    def _resolve_control_autopilots(self, autopilot_ir) -> None:
        """Task 5 fills this in."""

    def _validate_autopilot_trace(self, protocol):
        """Task 8 fills this in."""
        return protocol
```

Add the pending-policy record near `_PendingSeed` (~L122):

```python
@dataclass(frozen=True)
class _PendingControlAutopilot:
    strategy: str          # the block kind: fixed_step | proportional_step | rebaseline
    fields: dict           # raw AST fields of the block
    loc: Loc | None = None
```

- [ ] **Step 4: Implement the section resolver**

Add module constants near the top of `resolver.py`:

```python
_EVIDENCE_LEVELS = ("published", "clinical_consensus", "expert_opinion", "experimental")
_AP_SETTINGS = frozenset({
    "evidence", "citation", "rationale", "reviewed", "reward_target", "phases",
    "watch", "between_moves", "equipment_settle", "tighten_first",
})
```

Add to `_Resolver`:

```python
    def _all_check_names(self) -> set[str]:
        rewards = [self.reward_ir, *self._reward_bundles.values()]
        return {n for r in rewards if r is not None for n in r.check_names if n is not None}

    def _resolve_autopilot(self, session_ir) -> IRAutopilot | None:
        """The protocol-wide `autopilot { }` section (SPEC §4.12)."""
        if self.autopilot_ast is None:
            if self._pending_control_ap:
                name, pend = next(iter(self._pending_control_ap.items()))
                raise ResolveError(
                    f"control {name!r} declares an autopilot policy, but the protocol has no "
                    "`autopilot { }` block; add one with evidence, citation and rationale",
                    loc=pend.loc,
                )
            return None
        loc = self.autopilot_ast.loc
        settings: dict[str, A.Expr] = {}
        guards: list[IRGuard] = []
        limiters: list[IRLimiter] = []
        for stmt in self.autopilot_ast.body:
            if not isinstance(stmt, A.Assignment):
                raise ResolveError("autopilot accepts only `name = value` entries", loc=stmt.loc)
            v = stmt.value
            if isinstance(v, A.BlockExpr) and v.name in ("guard", "limiter"):
                if stmt.target in _AP_SETTINGS:
                    raise ResolveError(
                        f"autopilot entry {stmt.target!r} has the same name as a setting",
                        loc=stmt.loc)
                if v.name == "guard":
                    guards.append(self._ap_guard(stmt.target, v))
                else:
                    limiters.append(self._ap_limiter(stmt.target, v))
                continue
            if stmt.target not in _AP_SETTINGS:
                raise ResolveError(f"unknown autopilot setting {stmt.target!r}", loc=stmt.loc)
            settings[stmt.target] = v

        evidence = self._ap_string(settings, "evidence", loc, required=True)
        if evidence not in _EVIDENCE_LEVELS:
            raise ResolveError(
                f"autopilot.evidence must be one of {list(_EVIDENCE_LEVELS)}, got {evidence!r}",
                loc=settings["evidence"].loc)
        checks = self._all_check_names()
        tighten = (self._ap_string_list(settings["tighten_first"], "autopilot.tighten_first")
                   if "tighten_first" in settings else ())
        for name in tighten:
            if name not in checks:
                raise ResolveError(
                    f"autopilot.tighten_first names {name!r}, which is not a named reward check "
                    '(name checks with `as "..."`)', loc=settings["tighten_first"].loc)
        for lim in limiters:
            if lim.check not in checks:
                raise ResolveError(
                    f"autopilot limiter {lim.check!r} does not name a reward check "
                    '(name checks with `as "..."`)', loc=lim.loc)
        return IRAutopilot(
            evidence=evidence,
            citations=self._ap_citations(settings.get("citation"), "autopilot.citation", loc),
            rationale=self._ap_string(settings, "rationale", loc, required=True),
            reviewed=self._ap_string(settings, "reviewed", loc, required=False),
            reward_target=(self._ap_percent_pair(settings["reward_target"], "autopilot.reward_target")
                           if "reward_target" in settings else None),
            phases=self._ap_phases(settings["phases"], session_ir) if "phases" in settings else None,
            watch_ms=self._ap_duration(settings, "watch"),
            between_moves_ms=self._ap_duration(settings, "between_moves"),
            equipment_settle_ms=self._ap_duration(settings, "equipment_settle"),
            tighten_first=tighten,
            guards=tuple(guards),
            limiters=tuple(limiters),
            loc=loc,
        )

    def _ap_string(self, settings, key, loc, *, required):
        v = settings.get(key)
        if v is None:
            if required:
                raise ResolveError(f"autopilot needs `{key}`", loc=loc)
            return None
        if not isinstance(v, A.StringLit) or not v.value.strip():
            raise ResolveError(f"autopilot.{key} must be a non-empty string", loc=v.loc)
        return v.value

    def _ap_citations(self, v, what, loc, *, required=True):
        if v is None:
            if required:
                raise ResolveError(f"{what} is required: cite where these numbers come from",
                                   loc=loc)
            return ()
        items = v.elements if isinstance(v, A.Array) else (v,)
        out = []
        for e in items:
            if not isinstance(e, A.StringLit) or not e.value.strip():
                raise ResolveError(f"{what} must be a string or a list of strings", loc=e.loc)
            out.append(e.value)
        if not out:
            raise ResolveError(f"{what} must name at least one source", loc=v.loc)
        return tuple(out)

    def _ap_percent(self, e, what) -> float:
        if not isinstance(e, A.NumberLit) or e.unit != "%":
            raise ResolveError(f"{what} must be a percent like `15%`", loc=e.loc)
        if not 0 < e.value < 100:
            raise ResolveError(f"{what} must be between 0% and 100%", loc=e.loc)
        return e.value / 100.0

    def _ap_percent_pair(self, v, what) -> tuple[float, float]:
        if not isinstance(v, A.Tuple) or len(v.elements) != 2:
            raise ResolveError(f"{what} must be a pair like `(50%, 75%)`", loc=v.loc)
        lo = self._ap_percent(v.elements[0], what)
        hi = self._ap_percent(v.elements[1], what)
        if lo >= hi:
            raise ResolveError(f"{what}: the low end must be below the high end", loc=v.loc)
        return (lo, hi)

    def _ap_duration_ms(self, v, what) -> float:
        if not isinstance(v, A.NumberLit) or v.unit not in ("ms", "s", "min"):
            raise ResolveError(f"{what} must be a duration like `2 min`", loc=v.loc)
        ms = _to_milliseconds(v)
        if ms <= 0:
            raise ResolveError(f"{what} must be longer than zero", loc=v.loc)
        return ms

    def _ap_duration(self, settings, key) -> float | None:
        return self._ap_duration_ms(settings[key], f"autopilot.{key}") if key in settings else None

    def _ap_string_list(self, v, what) -> tuple[str, ...]:
        if not isinstance(v, A.Array):
            raise ResolveError(f"{what} must be a list of quoted names", loc=v.loc)
        out = []
        for e in v.elements:
            if not isinstance(e, A.StringLit):
                raise ResolveError(f"{what} must be a list of quoted names", loc=e.loc)
            out.append(e.value)
        return tuple(out)

    def _ap_phases(self, v, session_ir) -> tuple[str, ...]:
        names = self._ap_string_list(v, "autopilot.phases")
        by_name = {p.name: p for p in session_ir.phases}
        for n in names:
            p = by_name.get(n)
            if p is None:
                raise ResolveError(
                    f"autopilot.phases names {n!r}, which is not a session phase", loc=v.loc)
            if p.output_muted:
                raise ResolveError(
                    f"autopilot.phases names {n!r}, whose output is muted; advice never runs "
                    "in a muted phase", loc=v.loc)
        return names

    def _ap_guard(self, name, block) -> IRGuard:
        f = self._assignments_dict(block.body)
        if name not in self.inhibits:
            raise ResolveError(
                f"autopilot guard {name!r} does not name a declared inhibit", loc=block.loc)
        extra = set(f) - {"max", "say"}
        if extra:
            raise ResolveError(
                f"autopilot guard {name!r}: unexpected field(s) {sorted(extra)}", loc=block.loc)
        if "max" not in f:
            raise ResolveError(f"autopilot guard {name!r} needs `max` (e.g. `max = 15%`)",
                               loc=block.loc)
        say = f.get("say")
        if say is not None and not isinstance(say, A.StringLit):
            raise ResolveError(f"autopilot guard {name!r}.say must be a string", loc=say.loc)
        return IRGuard(inhibit=name, max_frac=self._ap_percent(f["max"], f"guard {name!r}.max"),
                       say=say.value if say is not None else None, loc=block.loc)

    def _ap_limiter(self, name, block) -> IRLimiter:
        f = self._assignments_dict(block.body)
        say = f.get("say")
        if set(f) != {"say"} or not isinstance(say, A.StringLit):
            raise ResolveError(f"autopilot limiter {name!r} takes exactly `say = \"...\"`",
                               loc=block.loc)
        return IRLimiter(check=name, say=say.value, loc=block.loc)
```

Import `IRAutopilot, IRGuard, IRLimiter` from `.ir` at the top of `resolver.py`.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py -v -p no:cacheprovider`
Expected: PASS. (Tests with control policies in `ap()` pass because `_resolve_control_autopilots` is still a stub: the `autopilot` field on controls is ignored until Task 5.)

- [ ] **Step 6: Composition test** (append)

```python
from tests.test_compose import _dict_loader


def test_child_block_replaces_and_amend_merges():
    parent = ap()
    child_replace = ('protocol "c" extends "base" { autopilot { evidence = "published"; '
                     'citation = "C"; rationale = "R" } }')
    ir = resolve(parse(child_replace), parent_loader=_dict_loader({"base": parent}))
    assert ir.autopilot.evidence == "published" and ir.autopilot.watch_ms is None
    child_amend = 'protocol "c" extends "base" { amend autopilot { watch = 1 min } }'
    ir = resolve(parse(child_amend), parent_loader=_dict_loader({"base": parent}))
    assert ir.autopilot.watch_ms == 60000.0 and ir.autopilot.evidence == "expert_opinion"
```

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py -v -p no:cacheprovider`
Expected: PASS. If "replace" fails because a parent's `autopilot` fields leak into the child, check that `"autopilot"` is **not** in `compose._FIELD_MERGE_SECTIONS`. It must replace, like `reward`.

- [ ] **Step 7: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolve_autopilot.py
git commit -m "feat(resolve): autopilot section — provenance, target, phases, timing, guards, limiters"
```

---

## Task 5: Resolve per-control policies

**Files:**
- Modify: `src/refrain/resolver.py` (`_resolve_control` ~L1037; `_resolve_control_autopilots` body; helpers)
- Test: `tests/test_resolve_autopilot.py` (extend)

**Interfaces:**
- Consumes: `_PendingControlAutopilot`, `IRAutopilot` (Task 4), `_eval_mode_condition`, `_mode_ref_and_literal` (existing, ~L609-640).
- Produces: `IRControl.autopilot` set on each surviving policy. `only_when` is evaluated here, at compile time. A policy whose `only_when` is false is dropped and never reaches IR.

- [ ] **Step 1: Write the failing tests** (append)

```python
# --- per-control policies -------------------------------------------------

def test_policies_resolve_adaptive():
    ir = _ir(ap())
    x = ir.controls["xover"].autopilot
    assert (x.strategy, x.fixes, x.higher_is, x.apply) == ("fixed_step", "crossover", "harder", "auto")
    assert x.limits == (0.5, 1.0) and x.step == 0.05 and x.round_to == 0.01
    assert ir.controls["t_pct"].autopilot.limits == (15.0, 40.0)
    assert ir.controls["t_uv"].autopilot is None          # only_when baseline -> dropped


def test_policies_resolve_baseline():
    ir = _ir(ap(), threshold_style="baseline")
    assert ir.controls["t_pct"].autopilot is None
    p = ir.controls["t_uv"].autopilot
    assert p.strategy == "proportional_step" and p.step == 0.10 and p.round_to == 0.1


def test_limits_default_to_range():
    ir = _ir(ap(xover_ap=XOVER_AP.replace("limits = (0.5, 1.0); ", "")))
    assert ir.controls["xover"].autopilot.limits == (0.5, 1.0)


def test_rebaseline_resolves():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    p = _ir(ap(t_uv_ap=pol), threshold_style="baseline").controls["t_uv"].autopilot
    assert (p.from_entity, p.window_ms, p.percentile) == ("derive/t_env", 10000.0, 60.0)


@pytest.mark.parametrize("override, needle", [
    ({"xover_ap": XOVER_AP.replace("fixed_step", "wobble")}, "wobble"),
    ({"xover_ap": XOVER_AP.replace('"auto"', '"sometimes"')}, "apply"),
    ({"xover_ap": XOVER_AP.replace('"harder"', '"tougher"')}, "higher_is"),
    ({"xover_ap": XOVER_AP.replace("limits = (0.5, 1.0)", "limits = (0.4, 1.0)")}, "range"),
    ({"xover_ap": XOVER_AP.replace("limits = (0.5, 1.0)", "limits = (0.9, 0.6)")}, "limits"),
    ({"xover_ap": XOVER_AP.replace("step = 0.05", "step = 0")}, "step"),
    ({"xover_ap": XOVER_AP.replace("step = 0.05", "step = 0.05 uV")}, "units"),
    ({"xover_ap": XOVER_AP.replace("fixed_step", "proportional_step")}, "percent"),
    ({"xover_ap": XOVER_AP.replace("round_to = 0.01", "round_to = 0.01; colour = 1")}, "colour"),
])
def test_policy_field_errors(override, needle):
    assert needle in _err(ap(**override))


def test_rebaseline_cannot_be_auto():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "auto"; '
           'only_when = threshold_style == "baseline" }')
    assert "suggest" in _err(ap(t_uv_ap=pol), threshold_style="baseline")


def test_rebaseline_window_must_fit_watch():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 30 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    assert "watch" in _err(ap(t_uv_ap=pol), threshold_style="baseline")


def test_not_live_tunable_is_rejected():
    src = ap().replace("xover  = number  { default = 0.60; range = (0.5, 1.0); live_tunable = true;",
                       "xover  = number  { default = 0.60; range = (0.5, 1.0);")
    assert "live_tunable" in _err(src)


def test_mode_control_policy_is_rejected():
    src = ap().replace('threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive" }',
                       'threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive"; '
                       'autopilot = fixed_step { fixes = "theta"; step = 1; higher_is = "harder"; apply = "suggest" } }')
    assert "mode" in _err(src)


@pytest.mark.parametrize("cond, needle", [
    ('t_pct == "adaptive"', "mode"),
    ('threshold_style == "sometimes"', "sometimes"),
    ('threshold_style', "compare"),
])
def test_only_when_errors(cond, needle):
    assert needle in _err(ap(t_pct_ap=T_PCT_AP.replace('threshold_style == "adaptive"', cond)))


def test_policy_without_block_is_an_error():
    assert "autopilot { }" in _err(ap(autopilot=""))
```

Also add `from tests._autopilot_fixtures import T_PCT_AP, T_UV_AP, XOVER_AP` at the top.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py -v -p no:cacheprovider`
Expected: FAIL (policies are `None`; errors not raised).

- [ ] **Step 3: Capture raw policies in `_resolve_control`**

In `_resolve_control`, directly after `fields = self._assignments_dict(block.body)` and **before** the `placement`/`mode` early returns:

```python
        if "autopilot" in fields:
            ap_ast = fields["autopilot"]
            if kind in ("placement", "mode"):
                raise ResolveError(
                    f"control {name!r} is a {kind!r} control; autopilot can only adjust "
                    "number, percent, voltage or frequency controls", loc=ap_ast.loc)
            if not isinstance(ap_ast, A.BlockExpr) or ap_ast.name is None:
                raise ResolveError(
                    f"control {name!r}.autopilot must be a typed block "
                    "(e.g. `autopilot = fixed_step { ... }`)", loc=ap_ast.loc)
            self._pending_control_ap[name] = _PendingControlAutopilot(
                strategy=ap_ast.name, fields=self._assignments_dict(ap_ast.body), loc=ap_ast.loc)
```

- [ ] **Step 4: Implement `_resolve_control_autopilots`**

Add module constants:

```python
_AP_KINDS = ("number", "percent", "voltage", "frequency")
_AP_COMMON_FIELDS = frozenset({
    "fixes", "higher_is", "apply", "limits", "round_to", "say", "between_moves",
    "only_when", "citation",
})
_AP_STRATEGY_FIELDS = {
    "fixed_step": frozenset({"step"}),
    "proportional_step": frozenset({"step"}),
    "rebaseline": frozenset({"from", "window", "percentile"}),
}
_AP_KNOB_UNITS = {
    "number": ((None,), "a plain number"),
    "percent": ((None, "%"), "a plain number or %"),
    "voltage": (("uV",), "uV"),
    "frequency": (("Hz",), "Hz"),
}
_DEFAULT_WATCH_MS = 120_000.0
```

Replace the stub body:

```python
    def _resolve_control_autopilots(self, autopilot_ir) -> None:
        """Validate each control's `autopilot = <strategy> { ... }` and attach it."""
        watch_ms = (autopilot_ir.watch_ms if autopilot_ir and autopilot_ir.watch_ms
                    else _DEFAULT_WATCH_MS)
        for name, pend in self._pending_control_ap.items():
            ctrl = self.controls[name]
            f = pend.fields
            what = f"control {name!r}.autopilot"
            if pend.strategy not in _AP_STRATEGY_FIELDS:
                raise ResolveError(
                    f"{what}: unknown strategy {pend.strategy!r} "
                    "(use fixed_step, proportional_step or rebaseline)", loc=pend.loc)
            extra = set(f) - _AP_COMMON_FIELDS - _AP_STRATEGY_FIELDS[pend.strategy]
            if extra:
                raise ResolveError(f"{what}: unexpected field(s) {sorted(extra)}", loc=pend.loc)
            if "only_when" in f and not self._ap_only_when(what, f["only_when"]):
                continue                          # not this mode: the policy does not exist
            if ctrl.type_kind not in _AP_KINDS:
                raise ResolveError(
                    f"control {name!r} is a {ctrl.type_kind!r} control; autopilot can only "
                    "adjust number, percent, voltage or frequency controls", loc=pend.loc)
            if not ctrl.live_tunable:
                raise ResolveError(
                    f"control {name!r} is not live_tunable, so autopilot cannot change it "
                    "mid-session", loc=pend.loc)
            if not isinstance(ctrl.default, IRNumberLit):
                raise ResolveError(f"control {name!r} needs a numeric `default` for autopilot",
                                   loc=pend.loc)
            apply = self._ap_choice(f, "apply", ("auto", "suggest"), what, pend.loc)
            if pend.strategy == "rebaseline" and apply == "auto":
                raise ResolveError(
                    f"{what}: a rebaseline policy is always suggest-only; "
                    'use `apply = "suggest"`', loc=pend.loc)
            step = from_entity = window_ms = percentile = None
            if pend.strategy == "fixed_step":
                step = self._ap_knob_value(ctrl, f.get("step"), f"{what}.step", pend.loc)
                if step <= 0:
                    raise ResolveError(f"{what}.step must be greater than zero", loc=pend.loc)
            elif pend.strategy == "proportional_step":
                if f.get("step") is None:
                    raise ResolveError(f"{what} needs `step` (a percent, e.g. `10%`)", loc=pend.loc)
                step = self._ap_percent(f["step"], f"{what}.step")
            else:
                src = f.get("from")
                if not isinstance(src, A.StringLit) or src.value not in self.derives:
                    raise ResolveError(f"{what}.from must be a quoted derive name", loc=pend.loc)
                d = self.derives[src.value]
                if d.stream_type.dims != ctrl.dims:
                    raise ResolveError(
                        f"{what}.from = {src.value!r} is not in {name!r}'s units", loc=pend.loc)
                from_entity = d.canonical_name
                if "window" not in f:
                    raise ResolveError(f"{what} needs `window`", loc=pend.loc)
                window_ms = self._ap_duration_ms(f["window"], f"{what}.window")
                if window_ms > watch_ms:
                    raise ResolveError(
                        f"{what}.window ({window_ms / 1000:g} s) is longer than autopilot.watch "
                        f"({watch_ms / 1000:g} s)", loc=pend.loc)
                pv = f.get("percentile")
                if not isinstance(pv, A.NumberLit) or pv.unit is not None or not 1 <= pv.value <= 99:
                    raise ResolveError(f"{what}.percentile must be a number from 1 to 99",
                                       loc=pend.loc)
                percentile = pv.value
            round_to = None
            if "round_to" in f:
                round_to = self._ap_knob_value(ctrl, f["round_to"], f"{what}.round_to", pend.loc)
                if round_to <= 0:
                    raise ResolveError(f"{what}.round_to must be greater than zero", loc=pend.loc)
            say = f.get("say")
            if say is not None and not isinstance(say, A.StringLit):
                raise ResolveError(f"{what}.say must be a string", loc=say.loc)
            pol = IRControlAutopilot(
                strategy=pend.strategy,
                fixes=self._ap_req_string(f, "fixes", what, pend.loc),
                higher_is=self._ap_choice(f, "higher_is", ("harder", "easier"), what, pend.loc),
                apply=apply,
                limits=self._ap_limits(name, ctrl, f.get("limits"), what, pend.loc),
                round_to=round_to,
                say=say.value if say is not None else None,
                between_moves_ms=(self._ap_duration_ms(f["between_moves"], f"{what}.between_moves")
                                  if "between_moves" in f else None),
                step=step, from_entity=from_entity, window_ms=window_ms, percentile=percentile,
                citations=(self._ap_citations(f["citation"], f"{what}.citation", pend.loc)
                           if "citation" in f else ()),
                loc=pend.loc,
            )
            self.controls[name] = replace(ctrl, autopilot=pol)

    def _ap_only_when(self, what, expr) -> bool:
        if not (isinstance(expr, A.BinaryOp) and expr.op in ("==", "!=")):
            raise ResolveError(
                f"{what}.only_when must compare a mode control to a string, e.g. "
                '`threshold_style == "baseline"`', loc=expr.loc)
        pair = self._mode_ref_and_literal(expr.left, expr.right)
        if pair is None:
            raise ResolveError(
                f"{what}.only_when must compare a mode control to a string, e.g. "
                '`threshold_style == "baseline"`', loc=expr.loc)
        mode_name, literal = pair
        choices = self.controls[mode_name].choices
        if literal not in choices:
            raise ResolveError(
                f"{what}.only_when: {literal!r} is not a choice of {mode_name!r} {list(choices)}",
                loc=expr.loc)
        return bool(self._eval_mode_condition(expr))

    def _ap_req_string(self, f, key, what, loc) -> str:
        v = f.get(key)
        if not isinstance(v, A.StringLit) or not v.value:
            raise ResolveError(f"{what} needs `{key} = \"...\"`", loc=loc)
        return v.value

    def _ap_choice(self, f, key, choices, what, loc) -> str:
        v = self._ap_req_string(f, key, what, loc)
        if v not in choices:
            raise ResolveError(f"{what}.{key} must be one of {list(choices)}, got {v!r}", loc=loc)
        return v

    def _ap_knob_value(self, ctrl, e, what, loc) -> float:
        units, want = _AP_KNOB_UNITS[ctrl.type_kind]
        if not isinstance(e, A.NumberLit) or e.unit not in units:
            raise ResolveError(f"{what} must be in the control's units ({want})",
                               loc=getattr(e, "loc", None) or loc)
        return float(e.value)

    def _ap_limits(self, name, ctrl, v, what, loc) -> tuple[float, float]:
        rng = None
        if isinstance(ctrl.range_low, IRNumberLit) and isinstance(ctrl.range_high, IRNumberLit):
            rng = (float(ctrl.range_low.value), float(ctrl.range_high.value))
        if v is None:
            if rng is None:
                raise ResolveError(f"{what} needs `limits` because {name!r} has no range", loc=loc)
            return rng
        if not isinstance(v, A.Tuple) or len(v.elements) != 2:
            raise ResolveError(f"{what}.limits must be a pair like `(0.5, 1.0)`", loc=v.loc)
        lo = self._ap_knob_value(ctrl, v.elements[0], f"{what}.limits", loc)
        hi = self._ap_knob_value(ctrl, v.elements[1], f"{what}.limits", loc)
        if lo >= hi:
            raise ResolveError(f"{what}.limits: the low end must be below the high end", loc=v.loc)
        if rng is not None and (lo < rng[0] or hi > rng[1]):
            raise ResolveError(
                f"{what}.limits ({lo:g}, {hi:g}) must lie inside {name!r}'s range "
                f"({rng[0]:g}, {rng[1]:g})", loc=v.loc)
        return (lo, hi)
```

Import `IRControlAutopilot` from `.ir`. `replace` is already imported (the seed pass uses it).

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py tests/test_resolve_seed.py tests/test_resolver.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolve_autopilot.py
git commit -m "feat(resolve): per-control autopilot policies with compile-time only_when"
```

---

## Task 6: Reward-check tracer over IR-JSON (`advisor_trace.py`)

**Files:**
- Create: `src/refrain/advisor_trace.py`
- Test: `tests/test_advisor_trace.py` (new)

**Interfaces:**
- Consumes: an IR-JSON dict as produced by `ir_to_json_obj` (keys used: `reward`, `reward_bundles`, `derives`, `thresholds`, `inhibits`, `controls`; nodes `call`/`control_ref`/`stream_ref`/`threshold_ref`/`array`/`tuple`/`binop`/`conditional`/`block`).
- Produces:
  - `reward_checks(event: dict | None) -> tuple[list[dict], str]`: the check expressions and `"all"`/`"any"`.
  - `controls_in(expr, ir) -> set[str]`: bare control names reachable, following `derive/` stream refs and threshold refs.
  - `trace_check(check, ir) -> tuple[str, str] | None`: `(control, "harder"|"easier")`.
  - `trace_protocol(ir) -> dict`: `{"bundles": {key: {"combine", "checks": [{"name","knob","higher_is","controls"}]}}, "check_controls": [...], "inhibit_controls": [...]}`, with lists sorted and `""` as the top-level reward's key.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_advisor_trace.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
from refrain import parse, resolve
from refrain.advisor_trace import controls_in, reward_checks, trace_check, trace_protocol
from refrain.ir_json import ir_to_json_obj
from tests._autopilot_fixtures import plain


def _obj(src, **bindings):
    return ir_to_json_obj(resolve(parse(src), bindings=bindings or None))


def test_adaptive_trace():
    t = trace_protocol(_obj(plain()))
    checks = t["bundles"][""]["checks"]
    assert t["bundles"][""]["combine"] == "all"
    assert (checks[0]["knob"], checks[0]["higher_is"]) == ("t_pct", "harder")
    assert (checks[1]["knob"], checks[1]["higher_is"]) == ("xover", "harder")
    assert checks[1]["controls"] == ["xover"]
    assert t["inhibit_controls"] == ["emg_pct"]
    assert t["check_controls"] == ["t_pct", "xover"]


def test_baseline_trace_follows_absolute_threshold():
    checks = trace_protocol(_obj(plain(), threshold_style="baseline"))["bundles"][""]["checks"]
    assert (checks[0]["knob"], checks[0]["higher_is"]) == ("t_uv", "harder")


def _ctl(name, kind="number", live=True):
    return {"canonical_name": f"control/{name}", "type_kind": kind, "live_tunable": live}


def _ref(name):
    return {"node": "control_ref", "target": f"control/{name}", "default": 1.0}


def _sig():
    return {"node": "stream_ref", "target": "derive/s"}


def test_below_with_control_is_easier():
    ir = {"controls": {"k": _ctl("k")}, "derives": {}, "thresholds": {}}
    below = {"node": "call", "callee": "below", "args": [
        {"name": None, "value": _sig()}, {"name": None, "value": _ref("k")}]}
    assert trace_check(below, ir) == ("k", "easier")


def test_below_percentile_threshold_is_easier():
    ir = {"controls": {"k": _ctl("k", "percent")}, "derives": {},
          "thresholds": {"t": {"threshold_call": {"node": "call", "callee": "percentile", "args": [
              {"name": "target_pct", "value": _ref("k")},
              {"name": "window", "value": {"node": "number", "value": 2.0}}]}}}}
    below = {"node": "call", "callee": "below", "args": [
        {"name": None, "value": _sig()},
        {"name": None, "value": {"node": "threshold_ref", "target": "threshold/t"}}]}
    assert trace_check(below, ir) == ("k", "easier")


def test_ambiguous_shapes_give_no_trace():
    ir = {"controls": {"k": _ctl("k"), "j": _ctl("j")}, "derives": {}, "thresholds": {}}
    two = {"node": "binop", "op": "*", "left": _ref("k"), "right": _ref("j")}
    call = {"node": "call", "callee": "above", "args": [
        {"name": None, "value": _sig()}, {"name": None, "value": two}]}
    assert trace_check(call, ir) is None
    not_live = {"controls": {"k": _ctl("k", live=False)}, "derives": {}, "thresholds": {}}
    above = {"node": "call", "callee": "above", "args": [
        {"name": None, "value": _sig()}, {"name": None, "value": _ref("k")}]}
    assert trace_check(above, not_live) is None
    wrong_kind = {"controls": {"k": _ctl("k", "boolean")}, "derives": {}, "thresholds": {}}
    assert trace_check(above, wrong_kind) is None


def test_controls_in_follows_derives():
    ir = {"derives": {"s": {"expression": _ref("k")}}, "thresholds": {}}
    assert controls_in(_sig(), ir) == {"k"}


def test_single_condition_dwell_is_one_check():
    dwell = {"node": "call", "callee": "dwell", "args": [
        {"name": "condition", "value": {"node": "call", "callee": "above", "args": []}}]}
    checks, combine = reward_checks(dwell)
    assert len(checks) == 1 and combine == "all"
    assert reward_checks(None) == ([], "all")
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_advisor_trace.py -v -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError: refrain.advisor_trace`).

- [ ] **Step 3: Implement**

Create `src/refrain/advisor_trace.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Reward-check tracing over IR-JSON (SPEC §7.10.6).

Answers, from the compiled protocol alone: which reward checks exist, which
controls feed each one (and each inhibit), and — where unambiguous — which
single control sets a check's threshold and whether raising it makes the check
harder or easier. Used by the resolver's compile-time checks and by the
advisor's direction hints. Mirrored by the `trace` section of
`refrain-core/src/advisor.rs`; parity-gated. Change both or neither.
"""

from __future__ import annotations

from typing import Any

ELIGIBLE_KINDS = ("number", "percent", "voltage", "frequency")


def _bare(canonical: str) -> str:
    return canonical.split("/", 1)[1] if "/" in canonical else canonical


def _arg(call: dict, name: str, position: int) -> Any:
    for i, a in enumerate(call.get("args") or []):
        if a.get("name") == name or (a.get("name") is None and i == position):
            return a.get("value")
    return None


def reward_checks(event: dict | None) -> tuple[list[dict], str]:
    """The check expressions of a `dwell(...)` reward event, and how they combine."""
    if not event or event.get("node") != "call" or event.get("callee") != "dwell":
        return [], "all"
    cond = _arg(event, "condition", 0)
    if cond is None:
        return [], "all"
    if (
        cond.get("node") == "call"
        and cond.get("callee") in ("all_of", "any_of")
        and cond.get("args")
        and (cond["args"][0].get("value") or {}).get("node") == "array"
    ):
        combine = "all" if cond["callee"] == "all_of" else "any"
        return list(cond["args"][0]["value"].get("elements") or []), combine
    return [cond], "all"


def controls_in(expr: Any, ir: dict) -> set[str]:
    """Bare control names reachable from `expr`, following derive and threshold refs."""
    out: set[str] = set()
    seen: set[str] = set()

    def walk(e: Any) -> None:
        if isinstance(e, list):
            for x in e:
                walk(x)
            return
        if not isinstance(e, dict):
            return
        node = e.get("node")
        if node == "control_ref":
            out.add(_bare(e["target"]))
        elif node == "stream_ref":
            t = e.get("target", "")
            if t.startswith("derive/") and t not in seen:
                seen.add(t)
                d = (ir.get("derives") or {}).get(_bare(t))
                if d is not None:
                    walk(d.get("expression"))
        elif node == "threshold_ref":
            t = e.get("target", "")
            if t not in seen:
                seen.add(t)
                th = (ir.get("thresholds") or {}).get(_bare(t))
                if th is not None:
                    walk(th.get("threshold_call"))
        elif node == "call":
            for a in e.get("args") or []:
                walk(a.get("value"))
        elif node in ("array", "tuple"):
            walk(e.get("elements") or [])
        elif node == "binop":
            walk(e.get("left"))
            walk(e.get("right"))
        elif node == "conditional":
            walk(e.get("cond"))
            walk(e.get("then"))
            walk(e.get("else"))
        elif node == "block":
            for v in (e.get("fields") or {}).values():
                walk(v)

    walk(expr)
    return out


def trace_check(check: dict, ir: dict) -> tuple[str, str] | None:
    """(control, "harder"|"easier") when exactly one live-tunable, eligible
    control sets this check's threshold; None when ambiguous."""
    if check.get("node") != "call" or check.get("callee") not in ("above", "below"):
        return None
    thr = _arg(check, "threshold", 1)
    if not isinstance(thr, dict):
        return None
    base = "harder" if check["callee"] == "above" else "easier"
    node = thr.get("node")
    if node == "control_ref":
        cand = _bare(thr["target"])
        side = {cand}
    elif node == "threshold_ref":
        th = (ir.get("thresholds") or {}).get(_bare(thr.get("target", "")))
        if th is None:
            return None
        call = th.get("threshold_call") or {}
        key = {"absolute": "value", "percentile": "target_pct"}.get(call.get("callee"))
        if key is None:
            return None
        v = _arg(call, key, 0)
        if not isinstance(v, dict) or v.get("node") != "control_ref":
            return None
        cand = _bare(v["target"])
        side = controls_in(call, ir)
    else:
        return None
    controls = ir.get("controls") or {}
    live = {c for c in side if (controls.get(c) or {}).get("live_tunable")}
    decl = controls.get(cand)
    if live != {cand} or decl is None or decl.get("type_kind") not in ELIGIBLE_KINDS:
        return None
    return cand, base


def trace_protocol(ir: dict) -> dict:
    """Every reward's checks with their traced knob/direction and feeding controls."""
    rewards = [("", ir.get("reward"))] + sorted((ir.get("reward_bundles") or {}).items())
    bundles: dict[str, dict] = {}
    check_controls: set[str] = set()
    for key, r in rewards:
        if not r:
            continue
        checks, combine = reward_checks(r.get("event"))
        if not checks:
            continue
        names = r.get("check_names") or []
        entries = []
        for i, c in enumerate(checks):
            traced = trace_check(c, ir)
            feeds = sorted(controls_in(c, ir))
            check_controls.update(feeds)
            entries.append({
                "name": names[i] if i < len(names) else None,
                "knob": traced[0] if traced else None,
                "higher_is": traced[1] if traced else None,
                "controls": feeds,
            })
        bundles[key] = {"combine": combine, "checks": entries}
    inhibit_controls: set[str] = set()
    for ih in (ir.get("inhibits") or {}).values():
        inhibit_controls |= controls_in(ih.get("metric"), ir)
        inhibit_controls |= controls_in(ih.get("threshold"), ir)
    return {
        "bundles": bundles,
        "check_controls": sorted(check_controls),
        "inhibit_controls": sorted(inhibit_controls),
    }
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_advisor_trace.py -v -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/advisor_trace.py tests/test_advisor_trace.py
git commit -m "feat(advisor): reward-check tracer over IR-JSON"
```

---

## Task 7: Emit IR-JSON 0.4 (+ schema, server, stability)

**Files:**
- Modify: `src/refrain/ir_json.py` (`_protocol_ir_version` L124-142; `_emit_reward` L379-398; `_emit_control` L410-425; `ir_to_json_obj` L467-515)
- Create: `src/refrain/schema/ir-json-v0.4.schema.json` (generated, below)
- Modify: `src/refrain/server.py:53-54`, `tests/test_ir_json_schema.py:27-31`, `tests/test_ir_json.py:359` (skip the new example)
- Test: `tests/test_ir_json_autopilot.py` (new)

**Interfaces:**
- Consumes: `IRAutopilot`, `IRControlAutopilot`, `IRReward.check_names`.
- Produces the wire shapes both advisors read:
  - top-level `autopilot`: `{evidence, citation: [str], rationale, reviewed|null, reward_target: [lo, hi]|null (fractions), phases: [str]|null, watch_samples|null, between_moves_samples|null, equipment_settle_samples|null, tighten_first: [str], guards: {inhibit: {max, say|null}}, limiters: {check: {say}}}`
  - `controls.<n>.autopilot`: `{strategy, fixes, higher_is, apply, limits: [lo, hi], round_to|null, decimals: int, say|null, between_moves_samples|null, step|null, from|null, window_samples|null, percentile|null, citation: [str]}`
  - `reward.check_names` / `reward_bundles.<b>.check_names`: `[str|null]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ir_json_autopilot.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
import json
from pathlib import Path

import jsonschema

from refrain import parse, resolve
from refrain.compile_json import compile_to_ir_json
from refrain.ir_json import ir_to_json_obj
from tests._autopilot_fixtures import ap, plain

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "src/refrain/schema/ir-json-v0.4.schema.json").read_text())


def test_policy_protocol_is_v04_and_schema_valid():
    obj = compile_to_ir_json(ap()).ir_json
    assert obj["refrain_ir_version"] == "0.4"
    assert obj["reward"]["check_names"] == ["theta", "crossover"]
    a = obj["autopilot"]
    assert a["reward_target"] == [0.10, 0.35]
    assert a["watch_samples"] == 20 * 256 and a["equipment_settle_samples"] == 5 * 256
    assert a["guards"] == {"emg": {"max": 0.15, "say": "Muscle artifact."}}
    assert a["citation"] == ["Test policy"]
    x = obj["controls"]["xover"]["autopilot"]
    assert x == {
        "strategy": "fixed_step", "fixes": "crossover", "higher_is": "harder", "apply": "auto",
        "limits": [0.5, 1.0], "round_to": 0.01, "decimals": 2, "say": None,
        "between_moves_samples": None, "step": 0.05, "from": None, "window_samples": None,
        "percentile": None, "citation": [],
    }
    assert "autopilot" not in obj["controls"]["t_uv"]            # dropped by only_when
    jsonschema.Draft202012Validator(SCHEMA).validate(obj)


def test_names_alone_make_v04():
    obj = compile_to_ir_json(plain(theta_as=' as "theta"')).ir_json
    assert obj["refrain_ir_version"] == "0.4" and "autopilot" not in obj


def test_plain_protocol_is_unchanged():
    obj = compile_to_ir_json(plain()).ir_json
    assert obj["refrain_ir_version"] == "0.1"
    assert "autopilot" not in obj and "check_names" not in obj["reward"]


def test_decimals_follow_round_to():
    from refrain.ir_json import _decimals
    assert [_decimals(x) for x in (None, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)] == [2, 2, 2, 1, 1, 0, 0]
```

Add to `tests/test_ir_json.py` (near the existing examples test at L359):

```python
def test_existing_examples_gain_no_autopilot_keys():
    """Review Focus #4: no existing example emits any new key or changes IR
    version (the byte-identical guarantee follows: new keys are the only
    emitter change, and they are omitted when unused)."""
    for path in sorted(EXAMPLES.glob("*.refrain")):
        if path.name in {"othmer_ilf_cz_pz.refrain", "dyadic_alpha_coherence_pz.refrain",
                         "alpha_theta_autopilot.refrain"}:
            continue
        obj = ir_to_json_obj(resolve(parse_file(path), _AMP))
        text = json.dumps(obj)
        assert '"autopilot"' not in text and '"check_names"' not in text, path.name
        assert obj["refrain_ir_version"] in ("0.1", "0.2", "0.3"), path.name
```

and add `"alpha_theta_autopilot.refrain",` to the skip set of `test_v01_emission_byte_identical_for_examples` (it is a 0.4 protocol by design; Task 19 adds it).

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_ir_json_autopilot.py -v -p no:cacheprovider`
Expected: FAIL (schema file missing; version is `0.1`).

- [ ] **Step 3: Emitter**

In `src/refrain/ir_json.py`, add `import math` and these helpers after `_emit_seed`:

```python
def _samples(ms: float | None, ctx: _EmitCtx) -> int | None:
    if ms is None:
        return None
    return max(1, int(round(ms / 1000.0 * ctx.sample_rate_hz)))


def _decimals(round_to: float | None) -> int:
    """Digits after the point for displaying a knob snapped to `round_to`."""
    if round_to is None:
        return 2
    return max(0, math.ceil(-math.log10(round_to) - 1e-9))


def _emit_autopilot(ap: IRAutopilot, ctx: _EmitCtx) -> dict:
    return {
        "evidence": ap.evidence,
        "citation": list(ap.citations),
        "rationale": ap.rationale,
        "reviewed": ap.reviewed,
        "reward_target": list(ap.reward_target) if ap.reward_target is not None else None,
        "phases": list(ap.phases) if ap.phases is not None else None,
        "watch_samples": _samples(ap.watch_ms, ctx),
        "between_moves_samples": _samples(ap.between_moves_ms, ctx),
        "equipment_settle_samples": _samples(ap.equipment_settle_ms, ctx),
        "tighten_first": list(ap.tighten_first),
        "guards": {g.inhibit: {"max": g.max_frac, "say": g.say} for g in ap.guards},
        "limiters": {lim.check: {"say": lim.say} for lim in ap.limiters},
    }


def _emit_control_autopilot(p: IRControlAutopilot, ctx: _EmitCtx) -> dict:
    return {
        "strategy": p.strategy,
        "fixes": p.fixes,
        "higher_is": p.higher_is,
        "apply": p.apply,
        "limits": [p.limits[0], p.limits[1]],
        "round_to": p.round_to,
        "decimals": _decimals(p.round_to),
        "say": p.say,
        "between_moves_samples": _samples(p.between_moves_ms, ctx),
        "step": p.step,
        "from": p.from_entity,
        "window_samples": _samples(p.window_ms, ctx),
        "percentile": p.percentile,
        "citation": list(p.citations),
    }
```

Import `IRAutopilot, IRControlAutopilot` from `.ir`.

In `_protocol_ir_version`, add as the **first** check:

```python
    if (
        ir.autopilot is not None
        or any(c.autopilot is not None for c in ir.controls.values())
        or ir.reward.check_names
        or any(rb.check_names for rb in ir.reward_bundles.values())
    ):
        return "0.4"
```

In `_emit_reward`, after `base["components"] = [...]` (the non-0.1 path; names imply 0.4):

```python
    if r.check_names:
        base["check_names"] = list(r.check_names)
```

In `_emit_control`, before `return out`:

```python
    if c.autopilot is not None:
        out["autopilot"] = _emit_control_autopilot(c.autopilot, ctx)
```

In `ir_to_json_obj`: if the dict literal is returned directly, bind it to `obj` first, then before returning:

```python
    if ir.autopilot is not None:
        obj["autopilot"] = _emit_autopilot(ir.autopilot, ctx)
```

- [ ] **Step 4: Generate the v0.4 schema**

```bash
.venv/bin/python - <<'EOF'
import json, pathlib
d = pathlib.Path("src/refrain/schema")
s = json.loads((d / "ir-json-v0.3.schema.json").read_text())
s["$id"] = "https://refrainlang.org/schema/ir-json-v0.4.schema.json"
s["title"] = "Refrain IR-JSON v0.4"
s["properties"]["refrain_ir_version"] = {"const": "0.4"}
str_or_null = {"type": ["string", "null"]}
num_or_null = {"type": ["number", "null"]}
int_or_null = {"type": ["integer", "null"], "minimum": 1}
s["properties"]["autopilot"] = {
    "type": "object",
    "required": ["evidence", "citation", "rationale"],
    "additionalProperties": True,
    "properties": {
        "evidence": {"enum": ["published", "clinical_consensus", "expert_opinion", "experimental"]},
        "citation": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "rationale": {"type": "string"},
        "reviewed": str_or_null,
        "reward_target": {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "phases": {"type": ["array", "null"], "items": {"type": "string"}},
        "watch_samples": int_or_null,
        "between_moves_samples": int_or_null,
        "equipment_settle_samples": int_or_null,
        "tighten_first": {"type": "array", "items": {"type": "string"}},
        "guards": {"type": "object", "additionalProperties": {
            "type": "object", "required": ["max"],
            "properties": {"max": {"type": "number"}, "say": str_or_null}}},
        "limiters": {"type": "object", "additionalProperties": {
            "type": "object", "required": ["say"], "properties": {"say": {"type": "string"}}}},
    },
}
s["$defs"]["ControlDecl"].setdefault("properties", {})["autopilot"] = {
    "type": "object",
    "required": ["strategy", "fixes", "higher_is", "apply", "limits", "decimals"],
    "additionalProperties": True,
    "properties": {
        "strategy": {"enum": ["fixed_step", "proportional_step", "rebaseline"]},
        "fixes": {"type": "string"},
        "higher_is": {"enum": ["harder", "easier"]},
        "apply": {"enum": ["auto", "suggest"]},
        "limits": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "round_to": num_or_null, "decimals": {"type": "integer", "minimum": 0},
        "say": str_or_null, "between_moves_samples": int_or_null, "step": num_or_null,
        "from": str_or_null, "window_samples": int_or_null, "percentile": num_or_null,
        "citation": {"type": "array", "items": {"type": "string"}},
    },
}
s["$defs"]["Reward"].setdefault("properties", {})["check_names"] = {
    "type": "array", "items": {"type": ["string", "null"]}}
(d / "ir-json-v0.4.schema.json").write_text(json.dumps(s, indent=2) + "\n")
EOF
```

- [ ] **Step 5: Advertise 0.4**

`src/refrain/server.py` L53-54: `"ir_versions_supported": ["0.1", "0.2", "0.3", "0.4"]` and `"schema_versions": ["0.1", "0.2", "0.3", "0.4"]`.
`tests/test_ir_json_schema.py` `SCHEMA_BY_VERSION`: add `"0.4": SCHEMA_DIR / "ir-json-v0.4.schema.json",`.
Grep for other hard-coded version lists and update any that enumerate supported versions: `grep -rn '"0.3"' src/refrain tests | grep -v schema/`. (`tests/test_server.py` may assert the `/version` payload; update its expected list.)

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_ir_json_autopilot.py tests/test_ir_json.py tests/test_ir_json_schema.py tests/test_server.py tests/test_compile_json.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/refrain/ir_json.py src/refrain/schema/ir-json-v0.4.schema.json src/refrain/server.py tests/test_ir_json_autopilot.py tests/test_ir_json.py tests/test_ir_json_schema.py tests/test_server.py
git commit -m "feat(ir-json): emit autopilot + check_names; IR-JSON v0.4 and schema"
```

---

## Task 8: Trace-based compile-time checks (V1–V4, V7, V16)

**Files:**
- Modify: `src/refrain/resolver.py` (`_validate_autopilot_trace` body; new `_ast_inhibit_controls`)
- Test: `tests/test_resolve_autopilot.py` (extend)

**Interfaces:**
- Consumes: `trace_protocol` (Task 6), `ir_to_json_obj` (Task 7), `self.file` (the composed AST, both mode branches intact).
- Produces: `_Resolver._ast_inhibit_controls() -> set[str]`, the control names reachable from any inhibit across **every** mode branch.

- [ ] **Step 1: Write the failing tests** (append)

```python
# --- cross-reference checks ---------------------------------------------

def test_full_policy_protocol_compiles_both_modes():
    assert not compile_to_ir_json(ap()).errors
    assert not compile_to_ir_json(ap(), bindings={"threshold_style": "baseline"}).errors


def test_fixes_unknown_check():                                              # V1
    assert "'nope'" in _err(ap(xover_ap=XOVER_AP.replace('"crossover"', '"nope"')))


def test_fixes_a_check_the_knob_does_not_feed():                            # V2
    assert "does not feed" in _err(ap(xover_ap=XOVER_AP.replace('"crossover"', '"theta"'),
                                      t_pct_ap=""))


def test_missing_only_when_on_mode_dependent_knob():                        # V2 via folding
    msg = _err(ap(t_uv_ap=T_UV_AP.replace('; only_when = threshold_style == "baseline"', "")))
    assert "does not feed" in msg and "only_when" in msg


def test_declared_direction_contradicts_trace():                            # V3
    msg = _err(ap(xover_ap=XOVER_AP.replace('"harder"', '"easier"')))
    assert "harder" in msg and "xover" in msg


def test_auto_on_a_guard_knob_is_rejected_in_any_branch():                  # V4
    auto_t_pct = T_PCT_AP.replace('"suggest"', '"auto"')
    assert "guard" in _err(ap(t_pct_ap=auto_t_pct, emg_thr="t_pct"))
    branchy = 'threshold_style == "baseline" ? t_pct : emg_pct'
    assert "guard" in _err(ap(t_pct_ap=auto_t_pct, emg_thr=branchy))   # folded away, still refused


def test_two_knobs_one_check():                                             # V7
    k2 = 'k2 = number { default = 1.0; range = (0.5, 2.0); live_tunable = true; ' \
         'autopilot = fixed_step { fixes = "crossover"; step = 0.1; higher_is = "harder"; apply = "suggest" } }'
    msg = _err(ap(xover_thr="xover * k2", extra_controls=k2))
    assert "both fix" in msg


def test_policy_on_protocol_without_reward_condition():                     # V16
    from tests._seed_fixtures import NON_SEEDING
    src = NON_SEEDING.replace(
        "session {",
        'autopilot { evidence = "experimental"; citation = "x"; rationale = "y"; '
        "reward_target = (40%, 60%) }\n  session {")
    assert "reward condition" in _err(src)
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py -v -p no:cacheprovider`
Expected: the new tests FAIL (no errors raised). The first test passes.

- [ ] **Step 3: Implement**

Add `import dataclasses` to `resolver.py` if absent. Replace the `_validate_autopilot_trace` stub:

```python
    def _validate_autopilot_trace(self, protocol):
        """Cross-reference checks that need the whole protocol (spec §3.7)."""
        policies = {n: c.autopilot for n, c in protocol.controls.items()
                    if c.autopilot is not None}
        ap = protocol.autopilot
        if ap is None and not policies:
            return protocol
        from .advisor_trace import trace_protocol  # noqa: PLC0415 (import cycle via ir_json)
        from .ir_json import ir_to_json_obj  # noqa: PLC0415
        tr = trace_protocol(ir_to_json_obj(protocol))
        judgeable = bool(tr["bundles"]) and protocol.reward.combine != "weighted"
        if not judgeable and (policies or (ap is not None and ap.reward_target is not None)):
            loc = ap.loc if ap is not None else next(iter(policies.values())).loc
            raise ResolveError(
                "autopilot needs a reward condition to judge: this protocol's reward has no "
                "`event = dwell(...)` condition (or uses a weighted composite), so "
                "`reward_target` and control policies cannot apply", loc=loc)
        by_name = {e["name"]: e for b in tr["bundles"].values() for e in b["checks"]
                   if e["name"] is not None}
        guarded = self._ast_inhibit_controls()
        fixed: dict[str, str] = {}
        for name, p in policies.items():
            e = by_name.get(p.fixes)
            if e is None:
                raise ResolveError(
                    f"control {name!r}.autopilot.fixes = {p.fixes!r}, but no reward check has "
                    'that name (name checks with `as "..."`)', loc=p.loc)
            if name not in e["controls"]:
                raise ResolveError(
                    f"control {name!r} claims to fix check {p.fixes!r}, but it does not feed that "
                    "check in this configuration; if it only matters in one mode, add "
                    '`only_when` (e.g. `only_when = threshold_style == "baseline"`)', loc=p.loc)
            if e["knob"] == name and e["higher_is"] != p.higher_is:
                raise ResolveError(
                    f"control {name!r}.autopilot.higher_is = {p.higher_is!r}, but raising "
                    f"{name!r} makes check {p.fixes!r} {e['higher_is']}", loc=p.loc)
            if p.apply == "auto" and name in guarded:
                raise ResolveError(
                    f"control {name!r} feeds a guard (inhibit), so autopilot may only suggest "
                    'changes to it: use `apply = "suggest"`', loc=p.loc)
            if p.fixes in fixed:
                raise ResolveError(
                    f"controls {fixed[p.fixes]!r} and {name!r} both fix check {p.fixes!r}; "
                    "one control per check (use `only_when` to split them by mode)", loc=p.loc)
            fixed[p.fixes] = name
        return protocol

    def _ast_inhibit_controls(self) -> set[str]:
        """Controls reachable from any inhibit across EVERY mode branch — walks the
        composed AST before mode folding, following derive/threshold names."""
        decls = {(s.keyword, s.name): s for s in self.file.protocol.body
                 if isinstance(s, A.NamedDecl) and s.keyword in ("derive", "threshold", "inhibit")}
        found: set[str] = set()
        seen: set[tuple[str, str]] = set()

        def walk(node) -> None:
            if isinstance(node, A.NameRef):
                if node.name in self.controls:
                    found.add(node.name)
            elif isinstance(node, A.StringLit):
                for kw in ("derive", "threshold"):
                    key = (kw, node.value)
                    if key in decls and key not in seen:
                        seen.add(key)
                        walk(decls[key].body)
            elif isinstance(node, tuple):
                for x in node:
                    walk(x)
            elif isinstance(node, A.Node):
                for f in dataclasses.fields(node):
                    if f.name != "loc":
                        walk(getattr(node, f.name))

        for (kw, _n), d in decls.items():
            if kw == "inhibit":
                walk(d.body)
        return found
```

If `self.file` is not the attribute that holds the composed AST, find the attribute `_Resolver.__init__` stores its first argument in (`grep -n "def __init__" -A6 src/refrain/resolver.py` at ~L153) and use that.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_resolve_autopilot.py tests/test_resolve_seed.py tests/test_resolver.py tests/test_compile_json.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_resolve_autopilot.py
git commit -m "feat(resolve): trace-based autopilot checks — fixes, direction, guards, one knob per check"
```

---
## Task 9: The Python advisor (`advisor.py`)

One cohesive state machine, so one task. The tests are grouped by the spec's decision table (§4.4) and lifecycle (§4.7).

**Files:**
- Create: `src/refrain/advisor.py`
- Test: `tests/test_advisor.py` (new)

**Interfaces:**
- Consumes: `trace_protocol` (Task 6); IR-JSON as emitted by Task 7.
- Produces (Task 10 and the Rust port rely on these exact names):
  - `Advisor(ir_json: dict, sample_rate_hz: float)`
  - `Advisor.feed(facts: ChunkFacts) -> None`
  - `Advisor.advice() -> dict`: the current result; every key always present (§5.2 shape plus `advisor_version`).
  - `Advisor.apply(advice_id: str, by: str) -> tuple[str, float, dict]`: returns `(control, new_value, applied_event)`. Raises `AdviceError` (a `ValueError`) on a stale id or a forbidden auto-apply, and `ValueError` on a bad `by`.
  - `Advisor.dismiss(advice_id: str) -> dict`: returns the dismissed event.
  - `Advisor.note_control(control: str, value: float, source: str) -> None`, where `source` is `"manual"` or `"seed"`.
  - `Advisor.mark_equipment_change() -> None`, `Advisor.drain_events() -> list[dict]`, `Advisor.policy() -> dict`
  - `Advisor.rebaseline_sources() -> list[str]`: canonical `derive/<x>` names whose samples the Evaluator must pass in.
  - `ChunkFacts(n, running, phase_index, phase_name, output_muted, clock_frozen, bundle, muted, inhibits, checks, events, derive_samples={})`
  - `facts_from_json(op: dict) -> ChunkFacts` (expands the compact scenario encoding, used by parity fixtures)
  - `AdviceError`, `ADVISOR_VERSION = "1"`, helpers `r6`, `pct`, `mmss`, `fmt_value`, `percentile_of`, `snap`

**Message strings are part of the contract.** Rust must produce them byte for byte. Here they are in one place:

| reason | message |
|---|---|
| not_training_phase | `Advice paused: not a training phase.` |
| equipment_settling | `Settling after equipment change ({s} s left).` (s = ceil of seconds left) |
| guard | `{say} ({g} guard active {p}% of training time).` with default say `Frequent {g} guard activity.` |
| collecting | `Collecting clean signal ({m:ss} of {m:ss}).` |
| observing | `Observing: this protocol has no reward condition to judge.` |
| on_track | `On track. {HEAD}` |
| no_knob | `{HEAD} The limiter is {check}. {say or "No adjustable setting addresses it; holding."}` |
| at_limit | `{HEAD} {label} is already at its {easiest\|hardest} allowed value ({value}).` |
| cooldown | `{HEAD} {label} can change again in {m:ss}.` |
| too_strict / too_easy (adjust) | `{HEAD} The limiter is {check}. {Raise\|Lower} {label} {cur} -> {new}.` |
| too_strict / too_easy (hint) | `{HEAD} The limiter is {check}. Consider {easing\|tightening} {label} ({higher\|lower} is {easier\|harder}).` |
| cooldown (dismissed hint) | `{HEAD} The limiter is {check}. Hint dismissed; holding for {m:ss}.` |
| reversal | `The last change made reward worse ({p}% of clean time). Return {label} {cur} -> {prev}.` |

`HEAD` = `Reward met {p}% of clean time (target {lo}-{hi}%).` Values are formatted with the knob's `decimals` and units: `0.55`, `15%`, `7.2 uV`, `12.0 Hz`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_advisor.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The autopilot advisor state machine (SPEC §7.10), driven by scripted facts.
1 chunk = 256 samples = 1 s. The ap() fixture has watch 20 s, between_moves
30 s, settle 5 s, target 10-35%, tighten_first [crossover, theta]."""

import numpy as np
import pytest

from refrain import parse, resolve
from refrain.advisor import (
    AdviceError, Advisor, ChunkFacts, facts_from_json, fmt_value, mmss, pct,
    percentile_of, r6,
)
from refrain.ir_json import ir_to_json_obj
from tests._autopilot_fixtures import AUTOPILOT_BLOCK, ap, plain

SR = 256


def make(src=None, **bindings):
    ir = resolve(parse(src or ap()), bindings=bindings or None)
    return Advisor(ir_to_json_obj(ir), SR)


def facts(*, checks=(True, True), muted=False, emg=None, phase=1, name="train1",
          running=True, output_muted=False, frozen=False, events=0, t_env=5.0, n=SR):
    b = lambda v: np.full(n, bool(v))  # noqa: E731
    ev = np.zeros(n, dtype=bool)
    ev[:events] = True
    return ChunkFacts(
        n=n, running=running, phase_index=phase, phase_name=name,
        output_muted=output_muted, clock_frozen=frozen, bundle=None,
        muted=b(muted), inhibits={"emg": b(muted if emg is None else emg)},
        checks=[b(c) for c in checks], events=ev,
        derive_samples={"derive/t_env": np.full(n, t_env)},
    )


def run(adv, pattern, **kw):
    for checks in pattern:
        adv.feed(facts(checks=checks, **kw))
    return adv.advice()


def strict(k=20, hits=1):
    """k chunks, theta always passes, crossover passes in `hits` chunks."""
    return [(True, i < hits) for i in range(k)]


EASY = [(True, True)] * 20


# --- helpers --------------------------------------------------------------

def test_helpers():
    assert r6(0.1234566) == 0.123457 and r6(-0.1234566) == -0.123457
    assert pct(0.125) == 13 and pct(0.05) == 5
    assert mmss(65.9) == "1:05" and mmss(20) == "0:20"
    assert fmt_value(0.55, 2, "") == "0.55"
    assert fmt_value(15.0, 0, "%") == "15%"
    assert fmt_value(7.2, 1, "uV") == "7.2 uV"
    assert percentile_of([5, 1, 3, 2, 4], 60) == pytest.approx(3.4)


# --- observation steps (§4.4 steps 1-4, 6) ---------------------------------

def test_before_training_and_in_muted_phase():
    adv = make()
    assert adv.advice()["reason"] == "not_training_phase"
    adv.feed(facts(phase=0, name="settle", output_muted=True))
    a = adv.advice()
    assert (a["state"], a["reason"]) == ("hold", "not_training_phase")
    assert a["message"] == "Advice paused: not a training phase."


def test_clock_frozen_is_not_training():
    adv = make()
    adv.feed(facts(frozen=True))
    assert adv.advice()["reason"] == "not_training_phase"


def test_collecting_shows_progress():
    a = run(make(), [(True, True)] * 5)
    assert (a["state"], a["reason"]) == ("collecting", "collecting")
    assert a["message"] == "Collecting clean signal (0:05 of 0:20)."
    assert a["evidence"]["clean_s"] == 5.0 and a["evidence"]["required_s"] == 20.0


def test_on_track():
    a = run(make(), [(True, i < 4) for i in range(20)])
    assert (a["state"], a["reason"]) == ("hold", "on_track")
    assert a["message"] == "On track. Reward met 20% of clean time (target 10-35%)."
    assert a["evidence"]["checks"] == {"theta": 1.0, "crossover": 0.2}
    assert a["id"] is None


def test_guard_holds_with_its_message():
    adv = make()
    run(adv, [(True, True)] * 16)
    a = run(adv, [(True, True)] * 4, muted=True)
    assert (a["state"], a["reason"]) == ("hold", "guard")
    assert a["message"] == "Muscle artifact. (emg guard active 20% of training time)."


def test_stale_clean_time_drops_out():
    """Review Focus #2: 1 clean chunk in every 8 never adds up to 20 s of
    evidence, because clean time older than 2 x watch (40 s) is dropped."""
    adv = make(ap(autopilot=AUTOPILOT_BLOCK.replace("max = 15%", "max = 99%")))
    for _ in range(25):
        adv.feed(facts())
        for _ in range(7):
            adv.feed(facts(muted=True, emg=False))
    a = adv.advice()
    assert a["reason"] == "collecting"
    assert a["evidence"]["clean_s"] <= 5.0


def test_equipment_change_settles_then_restarts():
    adv = make()
    run(adv, strict())
    adv.mark_equipment_change()
    a = adv.advice()
    assert (a["reason"], a["message"]) == (
        "equipment_settling", "Settling after equipment change (5 s left).")
    run(adv, [(True, True)] * 5)                       # the settle window: not counted
    a = run(adv, [(True, True)])
    assert a["reason"] == "collecting" and a["evidence"]["clean_s"] == 1.0
    assert [e["kind"] for e in adv.drain_events()][-2:] == ["equipment_change", "blocked"]


def test_new_training_phase_restarts_window():
    adv = make()
    run(adv, [(True, True)] * 20)
    adv.feed(facts(phase=2, name="rest", output_muted=True))
    a = run(adv, [(True, True)], phase=3, name="train2")
    assert a["reason"] == "collecting" and a["evidence"]["clean_s"] == 1.0


def test_observing_when_protocol_has_no_reward_condition():
    from tests._seed_fixtures import NON_SEEDING
    adv = make(NON_SEEDING)
    for _ in range(120):
        adv.feed(facts(checks=(), name="run"))
    assert adv.advice()["reason"] == "observing"


# --- decisions (§4.4 steps 7-11, §4.5) -------------------------------------

def test_too_strict_lowers_the_crossover_target():
    adv = make()
    a = run(adv, strict())
    assert (a["state"], a["reason"], a["level"], a["id"]) == ("adjust", "too_strict", "policy", "adv-0001")
    assert a["message"] == ("Reward met 5% of clean time (target 10-35%). "
                            "The limiter is crossover. Lower Crossover target 0.60 -> 0.55.")
    assert a["limiter"] == {"check": "crossover", "pass_rate": 0.05}
    c = a["control"]
    assert (c["name"], c["current"], c["proposed"], c["direction"], c["auto_allowed"]) == (
        "xover", 0.6, 0.55, "easier", True)
    assert a["eligible_at_s"] == a["t_s"] == 20.0
    assert adv.drain_events() == [{"t_s": 20.0, "kind": "suggested", "id": "adv-0001",
                                   "reason": "too_strict", "control": "xover",
                                   "from": 0.6, "to": 0.55}]


def test_same_suggestion_keeps_its_id():
    adv = make()
    run(adv, strict())
    a = run(adv, [(True, False)])
    assert a["id"] == "adv-0001" and adv.drain_events()[-1]["kind"] == "suggested"


def test_too_easy_tightens_the_first_listed_check():
    a = run(make(), EASY)
    assert (a["reason"], a["control"]["name"], a["control"]["proposed"]) == ("too_easy", "xover", 0.65)
    assert a["message"].endswith("Raise Crossover target 0.60 -> 0.65.")


def test_too_easy_falls_back_when_first_is_at_limit():
    adv = make()
    adv.note_control("xover", 1.0, "seed")
    a = run(adv, EASY)
    assert (a["control"]["name"], a["control"]["proposed"], a["control"]["auto_allowed"]) == (
        "t_pct", 20.0, False)


def test_limiter_without_policy_holds():
    a = run(make(ap(xover_ap="")), strict())
    assert (a["state"], a["reason"]) == ("hold", "no_knob")
    assert a["message"].endswith("The limiter is crossover. No adjustable setting addresses it; holding.")


def test_limiter_message_is_used():
    block = AUTOPILOT_BLOCK.replace(
        'emg = guard { max = 15%; say = "Muscle artifact." }',
        'emg = guard { max = 15%; say = "Muscle artifact." }\n    crossover = limiter { say = "Coach." }')
    a = run(make(ap(xover_ap="", autopilot=block)), strict())
    assert a["message"].endswith("The limiter is crossover. Coach.")


def test_manual_value_outside_limits_never_proposes_wrong_direction():
    """Review Focus #1: the clinician set 0.45 (below the 0.5 limit). Easing
    means lowering; clamping would raise it to 0.5, the wrong way — hold."""
    adv = make()
    adv.note_control("xover", 0.45, "manual")
    a = run(adv, strict())
    assert (a["state"], a["reason"]) == ("hold", "at_limit")
    assert a["message"].endswith("Crossover target is already at its easiest allowed value (0.45).")


def test_proportional_step_in_baseline_mode():
    a = run(make(threshold_style="baseline"), [(i < 1, True) for i in range(20)])
    c = a["control"]
    assert (c["name"], c["current"], c["proposed"], c["auto_allowed"]) == ("t_uv", 8.0, 7.2, False)
    assert a["message"].endswith("Lower t_uv 8.0 uV -> 7.2 uV.")


def test_rebaseline_proposes_signal_percentile():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    adv = make(ap(t_uv_ap=pol), threshold_style="baseline")
    a = run(adv, [(i < 1, True) for i in range(20)], t_env=5.0)
    assert (a["control"]["strategy"], a["control"]["proposed"]) == ("rebaseline", 5.0)


def test_hint_when_protocol_has_no_policies():
    adv = make(plain(theta_as=' as "theta"', xover_as=' as "crossover"'))
    a = run(adv, strict(k=120, hits=6))
    assert (a["state"], a["level"], a["id"]) == ("hint", "hint", "adv-0001")
    assert a["message"] == ("Reward met 5% of clean time (target 50-75%). The limiter is crossover. "
                            "Consider easing Crossover target (lower is easier).")
    assert a["control"]["proposed"] is None and a["control"]["auto_allowed"] is False


def test_no_hint_for_a_knob_that_feeds_a_guard():
    adv = make(plain(emg_thr="xover"))
    a = run(adv, strict(k=120, hits=6))
    assert a["reason"] == "no_knob" and a["message"].endswith("The limiter is check 1. "
                                                              "No adjustable setting addresses it; holding.")


# --- lifecycle (§4.7) -------------------------------------------------------

def test_apply_then_cooldown_then_next_step():
    adv = make()
    a = run(adv, strict())
    control, value, ev = adv.apply(a["id"], "autopilot")
    assert (control, value) == ("xover", 0.55)
    assert ev == {"t_s": 20.0, "kind": "applied", "id": "adv-0001", "control": "xover",
                  "from": 0.6, "to": 0.55, "by": "autopilot"}
    assert adv.advice()["reason"] == "collecting"
    a = run(adv, strict())
    assert (a["reason"], a["eligible_at_s"]) == ("cooldown", 50.0)
    assert a["message"].endswith("Crossover target can change again in 0:10.")
    a = run(adv, strict(k=10, hits=1))
    assert (a["reason"], a["id"], a["control"]["proposed"]) == ("too_strict", "adv-0002", 0.5)


def test_reversal_after_a_bad_tightening_bypasses_cooldown():
    adv = make()
    a = run(adv, EASY)
    adv.apply(a["id"], "clinician")                  # 0.60 -> 0.65
    a = run(adv, strict(hits=0))                      # reward collapses to 0%
    assert (a["state"], a["reason"], a["control"]["proposed"]) == ("adjust", "reversal", 0.6)
    assert a["message"] == ("The last change made reward worse (0% of clean time). "
                            "Return Crossover target 0.65 -> 0.60.")


def test_apply_stale_id_is_refused_and_value_unchanged():
    """Review Focus #3."""
    adv = make()
    a = run(adv, strict())
    with pytest.raises(AdviceError):
        adv.apply("adv-9999", "autopilot")
    adv.dismiss(a["id"])
    with pytest.raises(AdviceError):
        adv.apply(a["id"], "clinician")
    assert adv.values["xover"] == 0.6


def test_autopilot_cannot_apply_a_suggest_only_change():
    adv = make()
    adv.note_control("xover", 1.0, "seed")
    a = run(adv, EASY)                                   # t_pct, suggest-only
    with pytest.raises(AdviceError, match="suggestion"):
        adv.apply(a["id"], "autopilot")
    assert adv.apply(a["id"], "clinician")[1] == 20.0
    with pytest.raises(ValueError):
        adv.apply("adv-0001", "robot")


def test_dismiss_suppresses_the_same_move():
    adv = make()
    a = run(adv, strict())
    ev = adv.dismiss(a["id"])
    assert ev["kind"] == "dismissed" and ev["control"] == "xover"
    b = adv.advice()
    assert (b["reason"], b["eligible_at_s"]) == ("cooldown", 50.0)


def test_manual_change_supersedes_and_restarts():
    adv = make()
    run(adv, strict())
    adv.drain_events()
    adv.note_control("xover", 0.7, "manual")
    assert [e["kind"] for e in adv.drain_events()] == ["changed_manually", "superseded"]
    assert adv.advice()["reason"] == "collecting"


def test_guard_hold_blocks_a_standing_suggestion():
    adv = make()
    run(adv, strict())
    adv.drain_events()
    run(adv, [(True, False)] * 5, muted=True)
    assert [e["kind"] for e in adv.drain_events()] == ["blocked"]


def test_policy_description():
    p = make().policy()
    assert p["provenance"] == {"evidence": "expert_opinion", "citation": ["Test policy"],
                               "rationale": "Test rationale", "reviewed": None}
    assert p["controls"]["xover"]["apply"] == "auto" and p["watch_s"] == 20.0
    assert p["guards"]["emg"] == {"max": 0.15, "say": "Muscle artifact."}


def test_facts_from_json_expands_compact_fields():
    f = facts_from_json({"op": "feed", "n": 4, "running": True, "phase_index": 1,
                         "phase_name": "train1", "output_muted": False, "clock_frozen": False,
                         "bundle": None, "muted": [False, True, False, False],
                         "inhibits": {"emg": False}, "checks": [True, False], "events": 1,
                         "derive_samples": {"derive/t_env": 2.5}})
    assert f.muted.tolist() == [False, True, False, False]
    assert f.checks[1].tolist() == [False] * 4
    assert f.events.tolist() == [True, False, False, False]
    assert f.derive_samples["derive/t_env"].tolist() == [2.5] * 4
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_advisor.py -q -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError: refrain.advisor`).

- [ ] **Step 3: Implement**

Create `src/refrain/advisor.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Protocol autopilot advisor (SPEC §7.10).

A pure, deterministic state machine. The Evaluator hands it one `ChunkFacts`
per chunk; it keeps exactly one current advice result (`advice()`) and an
audit-event log (`drain_events()`). Time is counted in samples. Mirrored
line-for-line by `refrain-core/src/advisor.rs`, parity-gated by
`refrain-core/tests/advisor_parity.rs`. Change both or neither — including
every message string.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .advisor_trace import trace_protocol

ADVISOR_VERSION = "1"
DEFAULT_TARGET = (0.50, 0.75)
DEFAULT_WATCH_S = 120.0
DEFAULT_BETWEEN_S = 180.0
DEFAULT_SETTLE_S = 60.0
DEFAULT_GUARD_MAX = 0.15
UNITS = {"number": "", "percent": "%", "voltage": "uV", "frequency": "Hz"}
_BLOCKING = ("not_training_phase", "equipment_settling", "guard")
_NOT_TRAINING = "Advice paused: not a training phase."


class AdviceError(ValueError):
    """apply/dismiss with an id that is not the current advice, or an
    automatic application the protocol does not allow."""


# --- deterministic formatting (identical formulas in advisor.rs) -----------

def r6(x: float) -> float:
    """Round half away from zero to 6 decimal places."""
    if x < 0:
        return -r6(-x)
    return math.floor(x * 1e6 + 0.5) / 1e6


def pct(x: float) -> int:
    return int(math.floor(x * 100.0 + 0.5))


def mmss(seconds: float) -> str:
    s = int(math.floor(seconds))
    return f"{s // 60}:{s % 60:02d}"


def fmt_value(v: float, decimals: int, units: str) -> str:
    text = f"{v:.{decimals}f}"
    if units == "%":
        return text + "%"
    return f"{text} {units}" if units else text


def percentile_of(values: list[float], p: float) -> float:
    s = sorted(values)
    n = len(s)
    if n == 1:
        return s[0]
    rank = p / 100.0 * (n - 1)
    lo = int(math.floor(rank))
    hi = min(lo + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def snap(v: float, round_to: float | None) -> float:
    if round_to is None:
        return v
    return math.floor(v / round_to + 0.5) * round_to


def _default_samples(seconds: float, sr: float) -> int:
    return max(1, int(math.floor(seconds * sr + 0.5)))


# --- inputs ----------------------------------------------------------------

@dataclass
class ChunkFacts:
    """What the Evaluator observed during one chunk (all arrays length n)."""

    n: int
    running: bool                 # evaluator state == "run"
    phase_index: int              # -1 when the protocol has no session phases
    phase_name: str | None
    output_muted: bool            # phase mute or failed-seed mute
    clock_frozen: bool            # host paused the session clock
    bundle: str | None            # active reward bundle; None = top-level reward
    muted: Any                    # bool[n] combined inhibit gate
    inhibits: dict[str, Any]      # bare inhibit name -> bool[n]
    checks: list[Any]             # active reward's sub-conditions, bool[n] each
    events: Any                   # dwell rising edges, bool[n]
    derive_samples: dict[str, Any] = field(default_factory=dict)  # "derive/x" -> float[n]


def facts_from_json(op: dict) -> ChunkFacts:
    """Expand the compact scenario encoding: a bool/float stands for a constant
    array of length n; `events` is the count of leading true samples."""
    n = int(op["n"])

    def bools(v: Any) -> np.ndarray:
        return np.asarray(v, dtype=bool) if isinstance(v, list) else np.full(n, bool(v))

    def floats(v: Any) -> np.ndarray:
        return (np.asarray(v, dtype=np.float64) if isinstance(v, list)
                else np.full(n, float(v)))

    ev = np.zeros(n, dtype=bool)
    ev[: int(op.get("events", 0))] = True
    return ChunkFacts(
        n=n, running=bool(op["running"]), phase_index=int(op["phase_index"]),
        phase_name=op.get("phase_name"), output_muted=bool(op["output_muted"]),
        clock_frozen=bool(op["clock_frozen"]), bundle=op.get("bundle"),
        muted=bools(op["muted"]),
        inhibits={k: bools(v) for k, v in (op.get("inhibits") or {}).items()},
        checks=[bools(c) for c in op.get("checks") or []],
        events=ev,
        derive_samples={k: floats(v) for k, v in (op.get("derive_samples") or {}).items()},
    )


# --- configuration (built once from IR-JSON) --------------------------------

@dataclass(frozen=True)
class KnobPolicy:
    control: str
    label: str
    units: str
    strategy: str
    fixes: str
    higher_is: str
    apply: str
    lo: float
    hi: float
    round_to: float | None
    decimals: int
    between: int
    step: float | None
    from_entity: str | None
    window: int | None
    percentile: float | None
    citations: tuple[str, ...]


@dataclass(frozen=True)
class CheckInfo:
    name: str | None
    knob: str | None
    higher_is: str | None


@dataclass(frozen=True)
class BundleInfo:
    combine: str
    checks: tuple[CheckInfo, ...]


@dataclass(frozen=True)
class Config:
    target: tuple[float, float]
    phases: frozenset[str] | None
    watch: int
    between: int
    settle: int
    guards: dict[str, tuple[float, str | None]]
    limiters: dict[str, str]
    tighten_first: tuple[str, ...]
    knobs: dict[str, KnobPolicy]
    knob_for_check: dict[str, str]
    bundles: dict[str, BundleInfo]
    relevant: frozenset[str]
    inhibit_controls: frozenset[str]
    labels: dict[str, str]
    units: dict[str, str]
    defaults: dict[str, float]
    provenance: dict[str, Any] | None


def build_config(ir: dict, sr: float) -> Config:
    ap = ir.get("autopilot") or {}
    tr = trace_protocol(ir)
    controls = ir.get("controls") or {}
    labels: dict[str, str] = {}
    units: dict[str, str] = {}
    defaults: dict[str, float] = {}
    for name, c in controls.items():
        labels[name] = c.get("label") or name
        units[name] = UNITS.get(c.get("type_kind"), "")
        d = c.get("default")
        if isinstance(d, dict) and d.get("node") == "number":
            defaults[name] = float(d["value"])
    phase_list = (ir.get("session") or {}).get("phases") or []
    if ap.get("phases") is not None:
        phases: frozenset[str] | None = frozenset(ap["phases"])
    elif phase_list:
        phases = frozenset(p["name"] for p in phase_list if not p.get("output_muted"))
    else:
        phases = None

    def samples(key: str, default_s: float) -> int:
        v = ap.get(key)
        return int(v) if v is not None else _default_samples(default_s, sr)

    between = samples("between_moves_samples", DEFAULT_BETWEEN_S)
    declared = ap.get("guards") or {}
    guards = {}
    for name in sorted(ir.get("inhibits") or {}):
        g = declared.get(name)
        guards[name] = (float(g["max"]), g.get("say")) if g else (DEFAULT_GUARD_MAX, None)
    knobs: dict[str, KnobPolicy] = {}
    for name, c in controls.items():
        p = c.get("autopilot")
        if not p:
            continue
        kb = p.get("between_moves_samples")
        knobs[name] = KnobPolicy(
            control=name, label=p.get("say") or labels[name], units=units[name],
            strategy=p["strategy"], fixes=p["fixes"], higher_is=p["higher_is"],
            apply=p["apply"], lo=float(p["limits"][0]), hi=float(p["limits"][1]),
            round_to=p.get("round_to"), decimals=int(p.get("decimals", 2)),
            between=int(kb) if kb is not None else between,
            step=p.get("step"), from_entity=p.get("from"),
            window=p.get("window_samples"), percentile=p.get("percentile"),
            citations=tuple(p.get("citation") or ()),
        )
    bundles = {
        key: BundleInfo(
            combine=b["combine"],
            checks=tuple(CheckInfo(e["name"], e["knob"], e["higher_is"]) for e in b["checks"]),
        )
        for key, b in tr["bundles"].items()
    }
    provenance = None
    if ir.get("autopilot"):
        provenance = {"evidence": ap["evidence"], "citation": list(ap["citation"]),
                      "rationale": ap["rationale"], "reviewed": ap.get("reviewed")}
    return Config(
        target=tuple(ap["reward_target"]) if ap.get("reward_target") else DEFAULT_TARGET,
        phases=phases,
        watch=samples("watch_samples", DEFAULT_WATCH_S),
        between=between,
        settle=samples("equipment_settle_samples", DEFAULT_SETTLE_S),
        guards=guards,
        limiters={k: v["say"] for k, v in (ap.get("limiters") or {}).items()},
        tighten_first=tuple(ap.get("tighten_first") or ()),
        knobs=knobs,
        knob_for_check={p.fixes: name for name, p in knobs.items()},
        bundles=bundles,
        relevant=frozenset(tr["check_controls"]) | frozenset(tr["inhibit_controls"]),
        inhibit_controls=frozenset(tr["inhibit_controls"]),
        labels=labels, units=units, defaults=defaults, provenance=provenance,
    )


# --- evidence ---------------------------------------------------------------

@dataclass
class _Bucket:
    start: int
    end: int
    n_train: int
    n_clean: int
    n_cond: int
    checks: list[int]
    guards: dict[str, int]
    events: int


@dataclass
class _Evidence:
    start: int
    end: int
    n_train: int
    n_clean: int
    reward_rate: float | None
    check_rates: list[float]
    guard_rates: dict[str, float]
    chimes_per_min: float


# --- the advisor ------------------------------------------------------------

class Advisor:
    def __init__(self, ir: dict, sample_rate_hz: float) -> None:
        self.sr = float(sample_rate_hz)
        self.cfg = build_config(ir, self.sr)
        self.values: dict[str, float] = dict(self.cfg.defaults)
        self.now = 0
        self.buckets: list[_Bucket] = []
        self.rebase: dict[str, list[float]] = {
            name: [] for name, p in sorted(self.cfg.knobs.items()) if p.strategy == "rebaseline"}
        self.last_phase_index: int | None = None
        self.in_training = False
        self.bundle_key = ""
        self.settle_until = 0
        self.last_move_at: int | None = None
        self.dismissed: dict[tuple[str, str], int] = {}
        self.reversal: dict[str, Any] | None = None
        self.counter = 0
        self.standing: tuple[tuple, str] | None = None
        self.events: list[dict] = []
        self.current = self._result("hold", "not_training_phase", _NOT_TRAINING)

    # ---- host-facing ----

    def rebaseline_sources(self) -> list[str]:
        return sorted({self.cfg.knobs[c].from_entity for c in self.rebase})

    def advice(self) -> dict:
        return copy.deepcopy(self.current)

    def drain_events(self) -> list[dict]:
        out, self.events = self.events, []
        return out

    def feed(self, f: ChunkFacts) -> None:
        start = self.now
        self.now += int(f.n)
        phase_ok = self.cfg.phases is None or f.phase_name in self.cfg.phases
        if f.phase_index != self.last_phase_index:
            self.last_phase_index = f.phase_index
            if phase_ok:
                self._reset()
        self.in_training = bool(f.running and phase_ok and not f.output_muted
                                and not f.clock_frozen)
        self.bundle_key = f.bundle or ""
        if self.in_training and start >= self.settle_until:
            self._ingest(f, start)
        self._evaluate()

    def apply(self, advice_id: str, by: str) -> tuple[str, float, dict]:
        if by not in ("clinician", "autopilot"):
            raise ValueError(f"by must be 'clinician' or 'autopilot', got {by!r}")
        res = self.current
        if res["id"] != advice_id or res["state"] != "adjust":
            raise AdviceError(f"advice {advice_id!r} is not the current suggestion")
        ctl = res["control"]
        if by == "autopilot" and not ctl["auto_allowed"]:
            raise AdviceError(
                f"advice {advice_id!r} changes {ctl['name']!r}, which this protocol allows "
                "only as a suggestion")
        control, to = ctl["name"], ctl["proposed"]
        frm = self.values.get(control)
        self.values[control] = to
        ev = self._emit("applied", id=advice_id, control=control,
                        **{"from": r6(frm) if frm is not None else None, "to": to}, by=by)
        if res["reason"] == "reversal":
            self.reversal = None
        else:
            self.reversal = {"control": control, "from": frm, "direction": ctl["direction"],
                             "checked": False, "fire": False}
        self.last_move_at = self.now
        self.standing = None
        self._reset()
        self._evaluate()
        return control, to, ev

    def dismiss(self, advice_id: str) -> dict:
        res = self.current
        if res["id"] != advice_id or res["state"] not in ("adjust", "hint"):
            raise AdviceError(f"advice {advice_id!r} is not the current suggestion")
        ctl = res["control"]
        policy = self.cfg.knobs.get(ctl["name"])
        between = policy.between if policy is not None else self.cfg.between
        self.dismissed[(ctl["name"], ctl["direction"])] = self.now + between
        if res["reason"] == "reversal":
            self.reversal = None
        ev = self._emit("dismissed", id=advice_id, control=ctl["name"])
        self.standing = None
        self._evaluate()
        return ev

    def note_control(self, control: str, value: float, source: str) -> None:
        frm = self.values.get(control)
        self.values[control] = float(value)
        if control not in self.cfg.relevant:
            return
        if source == "manual":
            self._emit("changed_manually", control=control,
                       **{"from": r6(frm) if frm is not None else None, "to": r6(float(value))})
            if self.standing is not None:
                self._emit("superseded", id=self.standing[1], reason="changed_manually")
                self.standing = None
            if self.reversal is not None and self.reversal["control"] == control:
                self.reversal = None
            self.last_move_at = self.now
        self._reset()
        self._evaluate()

    def mark_equipment_change(self) -> None:
        self._emit("equipment_change")
        self.settle_until = self.now + self.cfg.settle
        self._reset()
        self._evaluate()

    def policy(self) -> dict:
        c = self.cfg
        return {
            "advisor_version": ADVISOR_VERSION,
            "provenance": c.provenance,
            "reward_target": [r6(c.target[0]), r6(c.target[1])],
            "phases": sorted(c.phases) if c.phases is not None else None,
            "watch_s": r6(c.watch / self.sr),
            "between_moves_s": r6(c.between / self.sr),
            "equipment_settle_s": r6(c.settle / self.sr),
            "tighten_first": list(c.tighten_first),
            "guards": {g: {"max": r6(m), "say": s} for g, (m, s) in c.guards.items()},
            "limiters": dict(c.limiters),
            "controls": {
                name: {
                    "label": p.label, "units": p.units, "strategy": p.strategy,
                    "fixes": p.fixes, "higher_is": p.higher_is, "apply": p.apply,
                    "limits": [r6(p.lo), r6(p.hi)], "round_to": p.round_to, "step": p.step,
                    "percentile": p.percentile, "between_moves_s": r6(p.between / self.sr),
                    "citation": list(p.citations),
                }
                for name, p in sorted(c.knobs.items())
            },
        }

    # ---- evidence ----

    def _bundle(self) -> BundleInfo | None:
        return self.cfg.bundles.get(self.bundle_key)

    def _reset(self) -> None:
        self.buckets = []
        for buf in self.rebase.values():
            buf.clear()

    def _ingest(self, f: ChunkFacts, start: int) -> None:
        clean = ~np.asarray(f.muted, dtype=bool)
        checks = [np.asarray(c, dtype=bool) for c in f.checks]
        n_cond = 0
        if checks:
            stacked = np.stack(checks)
            b = self._bundle()
            cond = stacked.any(axis=0) if (b is not None and b.combine == "any") else stacked.all(axis=0)
            n_cond = int(np.count_nonzero(cond & clean))
        self.buckets.append(_Bucket(
            start=start, end=self.now, n_train=int(f.n),
            n_clean=int(np.count_nonzero(clean)), n_cond=n_cond,
            checks=[int(np.count_nonzero(c & clean)) for c in checks],
            guards={k: int(np.count_nonzero(np.asarray(v, dtype=bool)))
                    for k, v in f.inhibits.items()},
            events=int(np.count_nonzero(np.asarray(f.events, dtype=bool) & clean)),
        ))
        for control, buf in self.rebase.items():
            p = self.cfg.knobs[control]
            src = f.derive_samples.get(p.from_entity)
            if src is None:
                continue
            for v in np.asarray(src, dtype=np.float64)[clean]:
                if math.isfinite(v):
                    buf.append(float(v))
            if len(buf) > p.window:
                del buf[: len(buf) - p.window]
        horizon = self.now - 2 * self.cfg.watch
        self.buckets = [b for b in self.buckets if b.end > horizon]

    def _evidence(self) -> _Evidence | None:
        horizon = self.now - 2 * self.cfg.watch
        sel: list[_Bucket] = []
        clean = 0
        for b in reversed(self.buckets):
            if b.end <= horizon:
                break
            sel.append(b)
            clean += b.n_clean
            if clean >= self.cfg.watch:
                break
        if not sel:
            return None
        sel.reverse()
        n_train = sum(b.n_train for b in sel)
        n_clean = sum(b.n_clean for b in sel)
        bundle = self._bundle()
        k = len(bundle.checks) if bundle is not None else 0
        has = k > 0 and n_clean > 0 and all(len(b.checks) == k for b in sel)
        return _Evidence(
            start=sel[0].start, end=sel[-1].end, n_train=n_train, n_clean=n_clean,
            reward_rate=sum(b.n_cond for b in sel) / n_clean if has else None,
            check_rates=[sum(b.checks[i] for b in sel) / n_clean for i in range(k)] if has else [],
            guard_rates={g: (sum(b.guards.get(g, 0) for b in sel) / n_train if n_train else 0.0)
                         for g in self.cfg.guards},
            chimes_per_min=(sum(b.events for b in sel) / (n_clean / self.sr / 60.0)
                            if n_clean else 0.0),
        )

    def _check_label(self, i: int) -> str:
        b = self._bundle()
        name = b.checks[i].name if b is not None and i < len(b.checks) else None
        return name or f"check {i}"

    def _evidence_dict(self, ev: _Evidence) -> dict:
        lo, hi = self.cfg.target
        return {
            "window_start_s": r6(ev.start / self.sr),
            "window_end_s": r6(ev.end / self.sr),
            "clean_s": r6(ev.n_clean / self.sr),
            "required_s": r6(self.cfg.watch / self.sr),
            "reward_rate": r6(ev.reward_rate) if ev.reward_rate is not None else None,
            "target": [r6(lo), r6(hi)],
            "checks": {self._check_label(i): r6(r) for i, r in enumerate(ev.check_rates)},
            "guards": {g: r6(r) for g, r in ev.guard_rates.items()},
            "chimes_per_min": r6(ev.chimes_per_min),
        }

    # ---- decision ----

    def _result(self, state, reason, message, *, level="observation", limiter=None,
                control=None, evidence=None, eligible_at=None) -> dict:
        return {
            "advisor_version": ADVISOR_VERSION, "id": None, "state": state, "level": level,
            "reason": reason, "message": message, "t_s": r6(self.now / self.sr),
            "limiter": limiter, "control": control, "evidence": evidence,
            "eligible_at_s": r6(eligible_at / self.sr) if eligible_at is not None else None,
        }

    def _evaluate(self) -> None:
        res, key = self._decide()
        self._publish(res, key)

    def _decide(self) -> tuple[dict, tuple | None]:
        if not self.in_training:
            return self._result("hold", "not_training_phase", _NOT_TRAINING), None
        if self.now < self.settle_until:
            left = int(math.ceil((self.settle_until - self.now) / self.sr))
            return self._result("hold", "equipment_settling",
                                f"Settling after equipment change ({left} s left)."), None
        ev = self._evidence()
        evd = self._evidence_dict(ev) if ev is not None else None
        if ev is not None:
            worst = None
            for g in sorted(self.cfg.guards):
                mx = self.cfg.guards[g][0]
                r = ev.guard_rates[g]
                if r > mx and (worst is None or r > ev.guard_rates[worst]):
                    worst = g
            if worst is not None:
                say = self.cfg.guards[worst][1] or f"Frequent {worst} guard activity."
                return self._result(
                    "hold", "guard",
                    f"{say} ({worst} guard active {pct(ev.guard_rates[worst])}% of training time).",
                    evidence=evd), None
        clean = ev.n_clean if ev is not None else 0
        if clean < self.cfg.watch:
            return self._result(
                "collecting", "collecting",
                f"Collecting clean signal ({mmss(clean / self.sr)} of {mmss(self.cfg.watch / self.sr)}).",
                evidence=evd), None
        if ev.reward_rate is None:
            return self._result("hold", "observing",
                                "Observing: this protocol has no reward condition to judge.",
                                evidence=evd), None
        rate = ev.reward_rate
        lo, hi = self.cfg.target
        head = f"Reward met {pct(rate)}% of clean time (target {pct(lo)}-{pct(hi)}%)."
        rev = self._reversal_step(rate, evd)
        if rev is not None:
            return rev
        if lo <= rate <= hi:
            return self._result("hold", "on_track", f"On track. {head}", evidence=evd), None
        return self._outside_band(ev, evd, "easier" if rate < lo else "harder", head)

    def _reversal_step(self, rate: float, evd: dict) -> tuple[dict, tuple] | None:
        r = self.reversal
        if r is None:
            return None
        if not r["checked"]:
            r["checked"] = True
            lo, hi = self.cfg.target
            worse = ((r["direction"] == "harder" and rate < lo)
                     or (r["direction"] == "easier" and rate > hi))
            if not worse:
                self.reversal = None
                return None
            r["fire"] = True
        if not r["fire"]:
            return None
        p = self.cfg.knobs[r["control"]]
        cur = self.values[p.control]
        back = "easier" if r["direction"] == "harder" else "harder"
        prev = r6(r["from"])
        msg = (f"The last change made reward worse ({pct(rate)}% of clean time). Return "
               f"{p.label} {fmt_value(cur, p.decimals, p.units)} -> "
               f"{fmt_value(prev, p.decimals, p.units)}.")
        return (self._result("adjust", "reversal", msg, level="policy",
                             control=self._control_dict(p, cur, prev, back), evidence=evd,
                             eligible_at=self.now),
                ("adjust", "reversal", p.control, back, prev))

    def _pick_check(self, ev: _Evidence, need: str) -> int:
        rates = ev.check_rates
        if need == "easier":
            best = 0
            for i in range(1, len(rates)):
                if rates[i] < rates[best]:
                    best = i
            return best
        names = [c.name for c in self._bundle().checks]
        for name in self.cfg.tighten_first:
            if name in names:
                control = self.cfg.knob_for_check.get(name)
                if control is not None and self._propose(self.cfg.knobs[control], "harder") is not None:
                    return names.index(name)
        best = 0
        for i in range(1, len(rates)):
            if rates[i] > rates[best]:
                best = i
        return best

    def _propose(self, p: KnobPolicy, need: str) -> float | None:
        cur = self.values[p.control]
        up = (need == "easier") == (p.higher_is == "easier")
        if p.strategy == "fixed_step":
            v = cur + p.step if up else cur - p.step
        elif p.strategy == "proportional_step":
            v = cur * (1.0 + p.step) if up else cur * (1.0 - p.step)
        else:
            buf = self.rebase.get(p.control, [])
            if len(buf) < p.window:
                return None
            v = percentile_of(buf, p.percentile)
        v = r6(min(max(snap(v, p.round_to), p.lo), p.hi))
        if abs(v - cur) < 1e-9 or (v > cur) != up:
            return None
        return v

    def _control_dict(self, p: KnobPolicy, cur: float, proposed: float | None, need: str) -> dict:
        return {
            "name": p.control, "label": p.label, "units": p.units, "round_to": p.round_to,
            "current": r6(cur), "proposed": proposed, "direction": need, "strategy": p.strategy,
            "auto_allowed": p.apply == "auto" and p.strategy != "rebaseline",
        }

    def _eligible_at(self, p: KnobPolicy, need: str) -> int | None:
        t = self.last_move_at + p.between if self.last_move_at is not None else None
        d = self.dismissed.get((p.control, need))
        if d is not None and (t is None or d > t):
            t = d
        return t

    def _outside_band(self, ev: _Evidence, evd: dict, need: str, head: str):
        idx = self._pick_check(ev, need)
        info = self._bundle().checks[idx]
        label = self._check_label(idx)
        limiter = {"check": label, "pass_rate": r6(ev.check_rates[idx])}
        control = self.cfg.knob_for_check.get(info.name) if info.name is not None else None
        p = self.cfg.knobs.get(control) if control is not None else None
        if p is None:
            say = self.cfg.limiters.get(info.name) if info.name is not None else None
            if say is None and not self.cfg.knobs:
                hint = self._hint(info, need, head, label, limiter, evd)
                if hint is not None:
                    return hint
            text = say or "No adjustable setting addresses it; holding."
            return self._result("hold", "no_knob", f"{head} The limiter is {label}. {text}",
                                limiter=limiter, evidence=evd), None
        cur = self.values[p.control]
        proposed = self._propose(p, need)
        ctl = self._control_dict(p, cur, proposed, need)
        if proposed is None:
            edge = "easiest" if need == "easier" else "hardest"
            return self._result(
                "hold", "at_limit",
                f"{head} {p.label} is already at its {edge} allowed value "
                f"({fmt_value(cur, p.decimals, p.units)}).",
                level="policy", limiter=limiter, control=ctl, evidence=evd), None
        eligible = self._eligible_at(p, need)
        if eligible is not None and self.now < eligible:
            return self._result(
                "hold", "cooldown",
                f"{head} {p.label} can change again in {mmss((eligible - self.now) / self.sr)}.",
                level="policy", limiter=limiter, control=ctl, evidence=evd,
                eligible_at=eligible), None
        verb = "Raise" if proposed > cur else "Lower"
        reason = "too_strict" if need == "easier" else "too_easy"
        msg = (f"{head} The limiter is {label}. {verb} {p.label} "
               f"{fmt_value(cur, p.decimals, p.units)} -> {fmt_value(proposed, p.decimals, p.units)}.")
        return (self._result("adjust", reason, msg, level="policy", limiter=limiter, control=ctl,
                             evidence=evd, eligible_at=self.now),
                ("adjust", reason, p.control, need, proposed))

    def _hint(self, info: CheckInfo, need, head, label, limiter, evd):
        if info.knob is None or info.knob in self.cfg.inhibit_controls:
            return None
        control = info.knob
        d = self.dismissed.get((control, need))
        if d is not None and self.now < d:
            return self._result(
                "hold", "cooldown",
                f"{head} The limiter is {label}. Hint dismissed; holding for "
                f"{mmss((d - self.now) / self.sr)}.",
                level="hint", limiter=limiter, evidence=evd, eligible_at=d), None
        up = (need == "easier") == (info.higher_is == "easier")
        name = self.cfg.labels.get(control, control)
        ctl = {
            "name": control, "label": name, "units": self.cfg.units.get(control, ""),
            "round_to": None,
            "current": r6(self.values[control]) if control in self.values else None,
            "proposed": None, "direction": need, "strategy": None, "auto_allowed": False,
        }
        verb = "easing" if need == "easier" else "tightening"
        word = "higher" if up else "lower"
        msg = f"{head} The limiter is {label}. Consider {verb} {name} ({word} is {need})."
        reason = "too_strict" if need == "easier" else "too_easy"
        return (self._result("hint", reason, msg, level="hint", limiter=limiter, control=ctl,
                             evidence=evd),
                ("hint", control, need))

    # ---- lifecycle ----

    def _emit(self, kind: str, **fields: Any) -> dict:
        ev = {"t_s": r6(self.now / self.sr), "kind": kind, **fields}
        self.events.append(ev)
        return ev

    def _publish(self, res: dict, key: tuple | None) -> None:
        if key is not None:
            if self.standing is not None and self.standing[0] == key:
                res["id"] = self.standing[1]
            else:
                if self.standing is not None:
                    self._emit("superseded", id=self.standing[1], reason=res["reason"])
                self.counter += 1
                new_id = f"adv-{self.counter:04d}"
                self.standing = (key, new_id)
                res["id"] = new_id
                ctl = res["control"]
                extra = ({"from": ctl["current"], "to": ctl["proposed"]}
                         if res["state"] == "adjust" else {})
                self._emit("suggested", id=new_id, reason=res["reason"], control=ctl["name"],
                           **extra)
        elif self.standing is not None:
            kind = "blocked" if res["reason"] in _BLOCKING else "superseded"
            self._emit(kind, id=self.standing[1], reason=res["reason"])
            self.standing = None
        self.current = res
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_advisor.py -v -p no:cacheprovider`
Expected: PASS. If a message assertion fails, fix the code, not the test: the table above is the contract the Rust port copies.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/advisor.py tests/test_advisor.py
git commit -m "feat(advisor): deterministic autopilot advisor state machine (Python reference)"
```

---

## Task 10: Wire the advisor into the Python Evaluator

**Files:**
- Modify: `src/refrain/eval_.py` (`__init__` near `_build_seed_latches` call; `_step_seeds` ~L787-835; `_process_chunk` after `muted = ...` at L1147; `set_control` L1455; new public methods after `seed_report` L1481)
- Test: `tests/test_eval_advice.py` (new)

**Interfaces:**
- Consumes: `Advisor`, `ChunkFacts`, `AdviceError` (Task 9); `ir_to_json_obj`.
- Produces (public `Evaluator` API; the Rust delegation arrives in Task 15):
  - `advice() -> dict`
  - `apply_advice(advice_id: str, by: str = "clinician") -> dict`: returns the applied event
  - `dismiss_advice(advice_id: str) -> dict`
  - `mark_equipment_change() -> None`
  - `drain_advice_events() -> list[dict]`
  - `autopilot_policy() -> dict`
  - `set_control` now also notifies the advisor (`source="manual"`), and a seed firing notifies it (`source="seed"`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_advice.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Evaluator host API for autopilot advice (SPEC §5)."""

import numpy as np
import pytest

from refrain import parse, resolve
from refrain.advisor import AdviceError
from refrain.eval_ import Evaluator
from tests._autopilot_fixtures import ap, plain

SR = 256.0


def _live(src, backend="python", **bindings):
    ir = resolve(parse(src), bindings=bindings or None)
    ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("Cz",), backend=backend)
    ev.start(skip_warmup=False)
    return ev


def _chunks(ev, n, value=1.0):
    for _ in range(n):
        ev.step_chunk(np.full((256, 1), value, dtype=np.float64))


def test_advice_before_and_during_settle():
    ev = _live(ap(emg_thr="100"))
    assert ev.advice()["reason"] == "not_training_phase"
    _chunks(ev, 5)                                   # inside the 10 s muted settle
    assert ev.advice()["reason"] == "not_training_phase"


def test_training_phase_collects_then_judges():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 12)                                  # 10 s settle + 2 s training
    a = ev.advice()
    assert a["reason"] == "collecting" and a["evidence"]["clean_s"] == 2.0
    _chunks(ev, 20)
    # Constant input: ratio == 1.0 > xover 0.6 always; t_env == its own
    # percentile, so theta (strictly above) rarely passes -> too strict on theta.
    a = ev.advice()
    assert a["state"] in ("adjust", "hold") and a["evidence"]["clean_s"] >= 20.0


def test_set_control_is_a_manual_change():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 15)
    ev.drain_advice_events()
    ev.set_control("xover", 0.7)
    kinds = [e["kind"] for e in ev.drain_advice_events()]
    assert kinds[0] == "changed_manually"
    assert ev.advice()["reason"] == "collecting"


def test_apply_advice_changes_the_control():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 12)
    adv = ev._advisor
    adv.values["xover"] = 0.6
    # Force a known adjust through the advisor, then apply via the Evaluator.
    from tests.test_advisor import facts, strict
    for checks in strict():
        adv.feed(facts(checks=checks))
    a = ev.advice()
    assert a["state"] == "adjust" and a["control"]["name"] == "xover"
    event = ev.apply_advice(a["id"], by="autopilot")
    assert event["kind"] == "applied" and ev._controls["control/xover"] == 0.55
    with pytest.raises(AdviceError):
        ev.apply_advice(a["id"])


def test_equipment_change_and_policy():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 12)
    ev.mark_equipment_change()
    assert ev.advice()["reason"] == "equipment_settling"
    assert ev.autopilot_policy()["controls"]["xover"]["apply"] == "auto"


def test_plain_protocol_gets_observations():
    ev = _live(plain(emg_thr="100"))
    _chunks(ev, 12)
    a = ev.advice()
    assert a["reason"] == "collecting" and a["evidence"]["required_s"] == 120.0
```

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m pytest tests/test_eval_advice.py -v -p no:cacheprovider`
Expected: FAIL (`AttributeError: 'Evaluator' object has no attribute 'advice'`).

- [ ] **Step 3: Build the advisor**

In `src/refrain/eval_.py`, add imports:

```python
from .advisor import AdviceError, Advisor, ChunkFacts
from .ir_json import ir_to_json_obj
```

(If `ir_to_json_obj` is already imported for the Rust path, reuse it.)

Add a module helper near `_build_rust_evaluator`:

```python
def _build_advisor(ir: IRProtocol, sample_rate_hz: float) -> tuple[Advisor | None, str | None]:
    """The advisor is built from the same IR-JSON the Rust core loads, so both
    engines start from identical configuration. A protocol the emitter cannot
    serialise gets no advisor; advice() then raises with the reason."""
    try:
        return Advisor(ir_to_json_obj(ir, sample_rate_hz=sample_rate_hz), sample_rate_hz), None
    except Exception as exc:  # noqa: BLE001 — surfaced by advice(), never swallowed
        return None, f"{type(exc).__name__}: {exc}"
```

In `__init__`, immediately after the call that builds seed latches (`self._build_seed_latches()`):

```python
        self._advisor, self._advisor_error = _build_advisor(self.ir, self.sample_rate_hz)
```

(If `sample_rate_hz` is not yet known at that point in pull mode, place the line where `self.sample_rate_hz` is first assigned. Both call sites of `_build_seed_latches` already have it.)

- [ ] **Step 4: Feed facts each chunk**

In `_process_chunk`, immediately after `muted = self._compute_muted(inhibit_active, actual_chunk_size, active_block)` (L1147):

```python
        if self._advisor is not None:
            ph = self._current_phase_ir()
            self._advisor.feed(ChunkFacts(
                n=actual_chunk_size,
                running=self._state == "run",
                phase_index=self._phase_index if ph is not None else -1,
                phase_name=ph.name if ph is not None else None,
                output_muted=bool(suppress_output),
                clock_frozen=bool(self._clock_frozen),
                bundle=active_bundle,
                muted=muted,
                inhibits={k.split("/", 1)[1]: v for k, v in inhibit_active.items()},
                checks=list(reward_sub_chunks),
                events=(reward_event.events if reward_event is not None
                        else np.zeros(actual_chunk_size, dtype=bool)),
                derive_samples={s: stream_values[s] for s in self._advisor.rebaseline_sources()
                                if s in stream_values},
            ))
```

`active_bundle` is defined in the staged block at L1118-1135. If it's scoped inside an `if`, hoist `active_bundle = None` above that block so it always exists.

- [ ] **Step 5: Seed and manual hooks**

In `_step_seeds`, right after the fired latch's `self._apply_control(...)` call:

```python
                if self._advisor is not None:
                    self._advisor.note_control(latch.control_name, latch.value, "seed")
```

In `set_control`, after `self._apply_control(name, value)`:

```python
        if self._advisor is not None:
            self._advisor.note_control(name, float(value), "manual")
```

- [ ] **Step 6: Public methods** (after `seed_report`)

```python
    # --- Autopilot advice (SPEC §5) --------------------------------------

    def _require_advisor(self) -> Advisor:
        if self._advisor is None:
            raise RuntimeError(f"autopilot advice is unavailable: {self._advisor_error}")
        return self._advisor

    def advice(self) -> dict:
        """The current structured advice (collecting / hold / hint / adjust)."""
        return self._require_advisor().advice()

    def apply_advice(self, advice_id: str, by: str = "clinician") -> dict:
        """Apply the current `adjust` advice. `by="autopilot"` is refused when
        the protocol allows that change only as a suggestion."""
        control, value, event = self._require_advisor().apply(advice_id, by)
        self._apply_control(control, value)
        return event

    def dismiss_advice(self, advice_id: str) -> dict:
        return self._require_advisor().dismiss(advice_id)

    def mark_equipment_change(self) -> None:
        self._require_advisor().mark_equipment_change()

    def drain_advice_events(self) -> list[dict]:
        return self._require_advisor().drain_events()

    def autopilot_policy(self) -> dict:
        return self._require_advisor().policy()
```

Re-export `AdviceError` from `refrain/__init__.py` (`from .advisor import AdviceError` and add it to `__all__`).

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_eval_advice.py tests/test_eval_seed.py tests/test_eval_taps.py tests/test_eval_lifecycle.py tests/test_staged_eval.py -q -p no:cacheprovider`
Expected: PASS. Taps must be unchanged: advice is not a tap.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/eval_.py src/refrain/__init__.py tests/test_eval_advice.py
git commit -m "feat(eval): feed the advisor each chunk; advice host API (Python backend)"
```

---
## Task 11: Rust IR — read IR-JSON 0.4

**Files:**
- Modify: `refrain-core/src/ir.rs` (`Protocol` :13-53, version gate :60, `ControlDecl` :79-84, `Reward` :171-180; new structs)
- Test: `refrain-core/tests/ir_deser.rs` (extend)

**Interfaces:**
- Produces (all `#[derive(Debug, Deserialize, Clone)]`):
  - `AutopilotDecl { evidence, citation: Vec<String>, rationale, reviewed: Option<String>, reward_target: Option<Vec<f64>>, phases: Option<Vec<String>>, watch_samples: Option<u64>, between_moves_samples: Option<u64>, equipment_settle_samples: Option<u64>, tighten_first: Vec<String>, guards: BTreeMap<String, GuardDecl>, limiters: BTreeMap<String, LimiterDecl> }`
  - `GuardDecl { max: f64, say: Option<String> }`, `LimiterDecl { say: String }`
  - `ControlAutopilot { strategy, fixes, higher_is, apply, limits: Vec<f64>, round_to: Option<f64>, decimals: usize, say: Option<String>, between_moves_samples: Option<u64>, step: Option<f64>, from: Option<String>, window_samples: Option<u64>, percentile: Option<f64>, citation: Vec<String> }`
  - `ControlDecl` gains `type_kind: String`, `label: Option<String>`, `live_tunable: bool`, `default: Option<Expr>`, `autopilot: Option<ControlAutopilot>` (all `#[serde(default)]`)
  - `Protocol.autopilot: Option<AutopilotDecl>`, `Reward.check_names: Vec<Option<String>>`
  - `SUPPORTED_IR_VERSIONS = ["0.1", "0.2", "0.3", "0.4"]`

- [ ] **Step 1: Write the failing test**

Append to `refrain-core/tests/ir_deser.rs`:

```rust
#[test]
fn v04_autopilot_fields_deserialize() {
    let doc = serde_json::json!({
        "refrain_ir_version": "0.4",
        "sample_rate_hz": 256.0, "channels": ["Cz"], "inputs": {}, "derives": {},
        "reward": {"continuous": null, "event": null, "check_names": ["theta", null]},
        "controls": {"xover": {
            "canonical_name": "control/xover", "type_kind": "number", "label": "X",
            "live_tunable": true,
            "default": {"node": "number", "value": 0.6},
            "autopilot": {"strategy": "fixed_step", "fixes": "crossover", "higher_is": "harder",
                          "apply": "auto", "limits": [0.5, 1.0], "round_to": 0.01,
                          "decimals": 2, "step": 0.05, "citation": []}
        }},
        "autopilot": {"evidence": "expert_opinion", "citation": ["x"], "rationale": "y",
                      "reward_target": [0.1, 0.35], "watch_samples": 5120,
                      "guards": {"emg": {"max": 0.15, "say": null}}, "tighten_first": ["crossover"]},
        "output": {}, "topological_order": []
    });
    let p: refrain_core::ir::Protocol = serde_json::from_value(doc).unwrap();
    refrain_core::ir::check_ir_version(&p).unwrap();
    assert_eq!(p.reward.as_ref().unwrap().check_names, vec![Some("theta".to_string()), None]);
    let c = &p.controls["xover"];
    assert_eq!(c.type_kind, "number");
    assert!(c.live_tunable);
    let pol = c.autopilot.as_ref().unwrap();
    assert_eq!((pol.step, pol.decimals), (Some(0.05), 2));
    let ap = p.autopilot.as_ref().unwrap();
    assert_eq!(ap.watch_samples, Some(5120));
    assert_eq!(ap.guards["emg"].max, 0.15);
}
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd refrain-core && ~/.cargo/bin/cargo test --test ir_deser v04 2>&1 | tail -5`
Expected: FAIL to compile (`no field check_names`).

- [ ] **Step 3: Implement**

In `refrain-core/src/ir.rs`:

```rust
pub const SUPPORTED_IR_VERSIONS: &[&str] = &["0.1", "0.2", "0.3", "0.4"];
```

Add to `Protocol` (with the other `#[serde(default)]` fields):

```rust
    /// Protocol-wide autopilot settings (IR-JSON 0.4). Absent ⇒ built-in defaults.
    #[serde(default)]
    pub autopilot: Option<AutopilotDecl>,
```

Add to `Reward`:

```rust
    /// Names of the dwell's sub-conditions (IR-JSON 0.4), aligned with
    /// `reward/condition[i]`; empty when no check is named.
    #[serde(default)]
    pub check_names: Vec<Option<String>>,
```

Add to `ControlDecl`:

```rust
    #[serde(default)]
    pub type_kind: String,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub live_tunable: bool,
    #[serde(default)]
    pub default: Option<Expr>,
    /// Autopilot policy (IR-JSON 0.4).
    #[serde(default)]
    pub autopilot: Option<ControlAutopilot>,
```

Add the new structs after `ControlSeed`:

```rust
/// `autopilot { }` (spec §6.2). Durations are pre-baked to samples.
#[derive(Debug, Deserialize, Clone)]
pub struct AutopilotDecl {
    pub evidence: String,
    #[serde(default)]
    pub citation: Vec<String>,
    pub rationale: String,
    #[serde(default)]
    pub reviewed: Option<String>,
    #[serde(default)]
    pub reward_target: Option<Vec<f64>>,
    #[serde(default)]
    pub phases: Option<Vec<String>>,
    #[serde(default)]
    pub watch_samples: Option<u64>,
    #[serde(default)]
    pub between_moves_samples: Option<u64>,
    #[serde(default)]
    pub equipment_settle_samples: Option<u64>,
    #[serde(default)]
    pub tighten_first: Vec<String>,
    #[serde(default)]
    pub guards: BTreeMap<String, GuardDecl>,
    #[serde(default)]
    pub limiters: BTreeMap<String, LimiterDecl>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct GuardDecl {
    pub max: f64,
    #[serde(default)]
    pub say: Option<String>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct LimiterDecl {
    pub say: String,
}

fn default_decimals() -> usize {
    2
}

/// A control's autopilot policy (spec §3.4).
#[derive(Debug, Deserialize, Clone)]
pub struct ControlAutopilot {
    pub strategy: String,
    pub fixes: String,
    pub higher_is: String,
    pub apply: String,
    pub limits: Vec<f64>,
    #[serde(default)]
    pub round_to: Option<f64>,
    #[serde(default = "default_decimals")]
    pub decimals: usize,
    #[serde(default)]
    pub say: Option<String>,
    #[serde(default)]
    pub between_moves_samples: Option<u64>,
    #[serde(default)]
    pub step: Option<f64>,
    #[serde(default)]
    pub from: Option<String>,
    #[serde(default)]
    pub window_samples: Option<u64>,
    #[serde(default)]
    pub percentile: Option<f64>,
    #[serde(default)]
    pub citation: Vec<String>,
}
```

If `ControlDecl` is constructed by hand anywhere in `src/` or `tests/` (`grep -rn "ControlDecl {" refrain-core`), add the new fields there with defaults.

- [ ] **Step 4: Run the tests**

Run: `cd refrain-core && ~/.cargo/bin/cargo test 2>&1 | grep -E "test result|FAILED|panicked" | head -20`
Expected: every suite `ok`, including the existing version-gate tests (they refuse `"99.0"` and accept existing tags).

- [ ] **Step 5: Commit**

```bash
git add refrain-core/src/ir.rs refrain-core/tests/ir_deser.rs
git commit -m "feat(core): deserialize IR-JSON 0.4 autopilot fields"
```

---

## Task 12: Rust advisor port (`advisor.rs`)

**Files:**
- Create: `refrain-core/src/advisor.rs`
- Modify: `refrain-core/src/lib.rs` (add `pub mod advisor;`)
- Test: unit tests inside `advisor.rs` (`#[cfg(test)]`)

**Interfaces:**
- Consumes: `Protocol`, `Expr`, `Arg`, `ControlAutopilot`, `AutopilotDecl` (Task 11).
- Produces:
  - `trace_protocol(p: &Protocol) -> Trace` and `trace_to_json(t: &Trace) -> serde_json::Value`, the same shape as Python's `trace_protocol`
  - `Advisor::new(p: &Protocol, sample_rate_hz: f64) -> Advisor`
  - `Advisor::feed(&mut self, f: &ChunkFacts)`
  - `advice(&self) -> Value`
  - `apply(&mut self, id: &str, by: &str) -> Result<(String, f64, Value), String>`
  - `dismiss(&mut self, id: &str) -> Result<Value, String>`
  - `note_control(&mut self, control: &str, value: f64, source: &str)`
  - `mark_equipment_change(&mut self)`, `drain_events(&mut self) -> Vec<Value>`, `policy(&self) -> Value`, `rebaseline_sources(&self) -> Vec<String>`
  - `ChunkFacts<'a>` (borrowed slices), `OwnedFacts` with `OwnedFacts::from_json(op: &Value) -> OwnedFacts` and `OwnedFacts::view(&self) -> ChunkFacts<'_>`
  - `pub fn r6(x: f64) -> f64`

Every function below is the Python function of the same name. Order of operations and every message string are identical on purpose. Do not "improve" either side alone.

- [ ] **Step 1: Write the failing unit tests**

Create `refrain-core/src/advisor.rs` containing only the test module for now:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers_match_python() {
        assert_eq!(r6(0.1234566), 0.123457);
        assert_eq!(r6(-0.1234566), -0.123457);
        assert_eq!(pct(0.125), 13);
        assert_eq!(mmss(65.9), "1:05");
        assert_eq!(fmt_value(0.55, 2, ""), "0.55");
        assert_eq!(fmt_value(15.0, 0, "%"), "15%");
        assert_eq!(fmt_value(7.2, 1, "uV"), "7.2 uV");
        assert!((percentile_of(&[5.0, 1.0, 3.0, 2.0, 4.0], 60.0) - 3.4).abs() < 1e-12);
        assert_eq!(snap(0.55, Some(0.01)), 0.55);
    }

    fn proto() -> Protocol {
        serde_json::from_value(serde_json::json!({
            "refrain_ir_version": "0.4", "sample_rate_hz": 10.0, "channels": ["Cz"],
            "inputs": {}, "derives": {}, "thresholds": {}, "inhibits": {},
            "reward": {"continuous": null, "check_names": ["a"],
                "event": {"node": "call", "callee": "dwell", "args": [
                    {"name": "condition", "value": {"node": "call", "callee": "all_of", "args": [
                        {"name": null, "value": {"node": "array", "elements": [
                            {"node": "call", "callee": "above", "args": [
                                {"name": null, "value": {"node": "stream_ref", "target": "input/raw"}},
                                {"name": null, "value": {"node": "control_ref", "target": "control/k", "default": 1.0}}]}]}}]}}]}},
            "controls": {"k": {"canonical_name": "control/k", "type_kind": "number",
                "live_tunable": true, "default": {"node": "number", "value": 1.0},
                "autopilot": {"strategy": "fixed_step", "fixes": "a", "higher_is": "harder",
                    "apply": "auto", "limits": [0.5, 2.0], "round_to": 0.1, "decimals": 1,
                    "step": 0.1, "citation": []}}},
            "autopilot": {"evidence": "experimental", "citation": ["t"], "rationale": "r",
                "reward_target": [0.4, 0.6], "watch_samples": 20},
            "output": {}, "topological_order": []
        })).unwrap()
    }

    #[test]
    fn trace_finds_knob_and_direction() {
        let t = trace_protocol(&proto());
        let c = &t.bundles[""].checks[0];
        assert_eq!((c.knob.as_deref(), c.higher_is.as_deref()), (Some("k"), Some("harder")));
    }

    #[test]
    fn too_strict_then_apply() {
        // watch = 20 samples; 2 of 20 clean samples meet the check (10%),
        // below the (40%, 60%) target -> ease k (higher is harder): 1.0 -> 0.9.
        let mut adv = Advisor::new(&proto(), 10.0);
        let mut first = vec![false; 10];
        first[0] = true;
        first[1] = true;
        let no = vec![false; 10];
        for check in [&first, &no] {
            adv.feed(&ChunkFacts {
                n: 10, running: true, phase_index: -1, phase_name: None,
                output_muted: false, clock_frozen: false, bundle: None,
                muted: &no, inhibits: vec![], checks: vec![check.as_slice()], events: &no,
                derive_samples: vec![],
            });
        }
        let a = adv.advice();
        assert_eq!(a["reason"], "too_strict", "{a}");
        assert_eq!(a["control"]["proposed"], 0.9);
        assert_eq!(a["id"], "adv-0001");
        let (control, value, ev) = adv.apply("adv-0001", "autopilot").unwrap();
        assert_eq!((control.as_str(), value), ("k", 0.9));
        assert_eq!(ev["kind"], "applied");
        assert!(adv.apply("adv-0001", "autopilot").is_err());
    }
}
```

- [ ] **Step 2: Run and confirm failure**

Add `pub mod advisor;` to `refrain-core/src/lib.rs` (after `pub mod eval;`).
Run: `cd refrain-core && ~/.cargo/bin/cargo test --lib advisor 2>&1 | tail -5`
Expected: FAIL to compile (`cannot find function r6`).

- [ ] **Step 3: Implement**

Put this above the test module in `refrain-core/src/advisor.rs`:

```rust
// Copyright 2026 Refrain Language Authors.
// Licensed under the Apache License, Version 2.0 (see LICENSE).
//! Protocol autopilot advisor (SPEC §7.10). A line-for-line port of
//! `src/refrain/advisor_trace.py` (the trace section) and
//! `src/refrain/advisor.py` (the rest): same names, same order of
//! operations, same message strings. Parity-gated by
//! `tests/advisor_parity.rs`. Change both or neither.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use crate::ir::{Arg, Expr, Protocol, Reward};

// ============================================================ trace

const ELIGIBLE_KINDS: [&str; 4] = ["number", "percent", "voltage", "frequency"];

fn bare(canonical: &str) -> &str {
    canonical.split_once('/').map(|(_, n)| n).unwrap_or(canonical)
}

fn arg<'a>(args: &'a [Arg], name: &str, position: usize) -> Option<&'a Expr> {
    for (i, a) in args.iter().enumerate() {
        if a.name.as_deref() == Some(name) || (a.name.is_none() && i == position) {
            return Some(&a.value);
        }
    }
    None
}

pub fn reward_checks(event: Option<&Expr>) -> (Vec<&Expr>, &'static str) {
    let Some(Expr::Call { callee, args, .. }) = event else { return (vec![], "all") };
    if callee != "dwell" {
        return (vec![], "all");
    }
    let Some(cond) = arg(args, "condition", 0) else { return (vec![], "all") };
    if let Expr::Call { callee: c, args: cargs, .. } = cond {
        if (c == "all_of" || c == "any_of") && !cargs.is_empty() {
            if let Expr::Array { elements } = &cargs[0].value {
                let combine = if c == "all_of" { "all" } else { "any" };
                return (elements.iter().collect(), combine);
            }
        }
    }
    (vec![cond], "all")
}

fn walk(e: &Expr, p: &Protocol, seen: &mut BTreeSet<String>, out: &mut BTreeSet<String>) {
    match e {
        Expr::ControlRef { target, .. } => {
            out.insert(bare(target).to_string());
        }
        Expr::StreamRef { target, .. } => {
            if target.starts_with("derive/") && seen.insert(target.clone()) {
                if let Some(d) = p.derives.get(bare(target)) {
                    walk(&d.expression, p, seen, out);
                }
            }
        }
        Expr::ThresholdRef { target, .. } => {
            if seen.insert(target.clone()) {
                if let Some(t) = p.thresholds.get(bare(target)) {
                    walk(&t.threshold_call, p, seen, out);
                }
            }
        }
        Expr::Call { args, .. } => {
            for a in args {
                walk(&a.value, p, seen, out);
            }
        }
        Expr::Array { elements } | Expr::Tuple { elements } => {
            for x in elements {
                walk(x, p, seen, out);
            }
        }
        Expr::Binop { left, right, .. } => {
            walk(left, p, seen, out);
            walk(right, p, seen, out);
        }
        Expr::Conditional { cond, then, els } => {
            walk(cond, p, seen, out);
            walk(then, p, seen, out);
            walk(els, p, seen, out);
        }
        Expr::Block { fields, .. } => {
            for v in fields.values() {
                walk(v, p, seen, out);
            }
        }
        _ => {}
    }
}

pub fn controls_in(expr: &Expr, p: &Protocol) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    let mut seen = BTreeSet::new();
    walk(expr, p, &mut seen, &mut out);
    out
}

pub fn trace_check(check: &Expr, p: &Protocol) -> Option<(String, &'static str)> {
    let Expr::Call { callee, args, .. } = check else { return None };
    if callee != "above" && callee != "below" {
        return None;
    }
    let thr = arg(args, "threshold", 1)?;
    let base = if callee == "above" { "harder" } else { "easier" };
    let (cand, side) = match thr {
        Expr::ControlRef { target, .. } => {
            let c = bare(target).to_string();
            (c.clone(), BTreeSet::from([c]))
        }
        Expr::ThresholdRef { target, .. } => {
            let th = p.thresholds.get(bare(target))?;
            let Expr::Call { callee: tc, args: targs, .. } = &th.threshold_call else { return None };
            let key = match tc.as_str() {
                "absolute" => "value",
                "percentile" => "target_pct",
                _ => return None,
            };
            let Some(Expr::ControlRef { target: ct, .. }) = arg(targs, key, 0) else { return None };
            (bare(ct).to_string(), controls_in(&th.threshold_call, p))
        }
        _ => return None,
    };
    let live: BTreeSet<String> = side
        .into_iter()
        .filter(|c| p.controls.get(c).map_or(false, |d| d.live_tunable))
        .collect();
    let decl = p.controls.get(&cand)?;
    if live != BTreeSet::from([cand.clone()]) || !ELIGIBLE_KINDS.contains(&decl.type_kind.as_str()) {
        return None;
    }
    Some((cand, base))
}

pub struct TraceCheck {
    pub name: Option<String>,
    pub knob: Option<String>,
    pub higher_is: Option<String>,
    pub controls: Vec<String>,
}

pub struct TraceBundle {
    pub combine: String,
    pub checks: Vec<TraceCheck>,
}

pub struct Trace {
    pub bundles: BTreeMap<String, TraceBundle>,
    pub check_controls: Vec<String>,
    pub inhibit_controls: Vec<String>,
}

pub fn trace_protocol(p: &Protocol) -> Trace {
    let mut rewards: Vec<(String, &Reward)> = Vec::new();
    if let Some(r) = p.reward.as_ref() {
        rewards.push((String::new(), r));
    }
    for (k, r) in p.reward_bundles.iter() {
        rewards.push((k.clone(), r));
    }
    let mut bundles = BTreeMap::new();
    let mut check_controls = BTreeSet::new();
    for (key, r) in rewards {
        let (checks, combine) = reward_checks(r.event.as_ref());
        if checks.is_empty() {
            continue;
        }
        let mut entries = Vec::new();
        for (i, c) in checks.iter().enumerate() {
            let traced = trace_check(c, p);
            let feeds: Vec<String> = controls_in(c, p).into_iter().collect();
            check_controls.extend(feeds.iter().cloned());
            entries.push(TraceCheck {
                name: r.check_names.get(i).cloned().flatten(),
                knob: traced.as_ref().map(|t| t.0.clone()),
                higher_is: traced.map(|t| t.1.to_string()),
                controls: feeds,
            });
        }
        bundles.insert(key, TraceBundle { combine: combine.to_string(), checks: entries });
    }
    let mut inhibit_controls = BTreeSet::new();
    for ih in p.inhibits.values() {
        inhibit_controls.extend(controls_in(&ih.metric, p));
        inhibit_controls.extend(controls_in(&ih.threshold, p));
    }
    Trace {
        bundles,
        check_controls: check_controls.into_iter().collect(),
        inhibit_controls: inhibit_controls.into_iter().collect(),
    }
}

pub fn trace_to_json(t: &Trace) -> Value {
    let mut bundles = Map::new();
    for (k, b) in &t.bundles {
        let checks: Vec<Value> = b
            .checks
            .iter()
            .map(|c| json!({"name": c.name, "knob": c.knob, "higher_is": c.higher_is,
                            "controls": c.controls}))
            .collect();
        bundles.insert(k.clone(), json!({"combine": b.combine, "checks": checks}));
    }
    json!({"bundles": bundles, "check_controls": t.check_controls,
           "inhibit_controls": t.inhibit_controls})
}

// ============================================================ helpers

pub const ADVISOR_VERSION: &str = "1";
const DEFAULT_TARGET: (f64, f64) = (0.50, 0.75);
const DEFAULT_WATCH_S: f64 = 120.0;
const DEFAULT_BETWEEN_S: f64 = 180.0;
const DEFAULT_SETTLE_S: f64 = 60.0;
const DEFAULT_GUARD_MAX: f64 = 0.15;
const BLOCKING: [&str; 3] = ["not_training_phase", "equipment_settling", "guard"];
const NOT_TRAINING: &str = "Advice paused: not a training phase.";

pub fn r6(x: f64) -> f64 {
    if x < 0.0 {
        return -r6(-x);
    }
    (x * 1e6 + 0.5).floor() / 1e6
}

pub fn pct(x: f64) -> i64 {
    (x * 100.0 + 0.5).floor() as i64
}

pub fn mmss(seconds: f64) -> String {
    let s = seconds.floor() as i64;
    format!("{}:{:02}", s / 60, s % 60)
}

pub fn fmt_value(v: f64, decimals: usize, units: &str) -> String {
    let text = format!("{v:.decimals$}");
    if units == "%" {
        format!("{text}%")
    } else if units.is_empty() {
        text
    } else {
        format!("{text} {units}")
    }
}

pub fn percentile_of(values: &[f64], p: f64) -> f64 {
    let mut s = values.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = s.len();
    if n == 1 {
        return s[0];
    }
    let rank = p / 100.0 * (n as f64 - 1.0);
    let lo = rank.floor() as usize;
    let hi = (lo + 1).min(n - 1);
    s[lo] + (s[hi] - s[lo]) * (rank - lo as f64)
}

pub fn snap(v: f64, round_to: Option<f64>) -> f64 {
    match round_to {
        Some(r) => (v / r + 0.5).floor() * r,
        None => v,
    }
}

fn default_samples(seconds: f64, sr: f64) -> u64 {
    ((seconds * sr + 0.5).floor() as u64).max(1)
}

fn units_of(kind: &str) -> &'static str {
    match kind {
        "percent" => "%",
        "voltage" => "uV",
        "frequency" => "Hz",
        _ => "",
    }
}

// ============================================================ facts

/// One chunk's observations (mirrors Python `ChunkFacts`).
pub struct ChunkFacts<'a> {
    pub n: usize,
    pub running: bool,
    pub phase_index: i64,
    pub phase_name: Option<&'a str>,
    pub output_muted: bool,
    pub clock_frozen: bool,
    pub bundle: Option<&'a str>,
    pub muted: &'a [bool],
    pub inhibits: Vec<(&'a str, &'a [bool])>,
    pub checks: Vec<&'a [bool]>,
    pub events: &'a [bool],
    pub derive_samples: Vec<(&'a str, &'a [f64])>,
}

/// Owned facts decoded from the compact scenario encoding (`facts_from_json`).
pub struct OwnedFacts {
    n: usize,
    running: bool,
    phase_index: i64,
    phase_name: Option<String>,
    output_muted: bool,
    clock_frozen: bool,
    bundle: Option<String>,
    muted: Vec<bool>,
    inhibits: Vec<(String, Vec<bool>)>,
    checks: Vec<Vec<bool>>,
    events: Vec<bool>,
    derive_samples: Vec<(String, Vec<f64>)>,
}

impl OwnedFacts {
    pub fn from_json(op: &Value) -> OwnedFacts {
        let n = op["n"].as_u64().unwrap() as usize;
        let bools = |v: &Value| -> Vec<bool> {
            match v {
                Value::Array(a) => a.iter().map(|x| x.as_bool().unwrap()).collect(),
                other => vec![other.as_bool().unwrap(); n],
            }
        };
        let floats = |v: &Value| -> Vec<f64> {
            match v {
                Value::Array(a) => a.iter().map(|x| x.as_f64().unwrap()).collect(),
                other => vec![other.as_f64().unwrap(); n],
            }
        };
        let mut events = vec![false; n];
        let k = op.get("events").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
        for e in events.iter_mut().take(k) {
            *e = true;
        }
        let obj = |key: &str| op.get(key).and_then(|v| v.as_object()).cloned().unwrap_or_default();
        OwnedFacts {
            n,
            running: op["running"].as_bool().unwrap(),
            phase_index: op["phase_index"].as_i64().unwrap(),
            phase_name: op["phase_name"].as_str().map(String::from),
            output_muted: op["output_muted"].as_bool().unwrap(),
            clock_frozen: op["clock_frozen"].as_bool().unwrap(),
            bundle: op["bundle"].as_str().map(String::from),
            muted: bools(&op["muted"]),
            inhibits: obj("inhibits").iter().map(|(k, v)| (k.clone(), bools(v))).collect(),
            checks: op["checks"].as_array().map(|a| a.iter().map(&bools).collect()).unwrap_or_default(),
            events,
            derive_samples: obj("derive_samples").iter().map(|(k, v)| (k.clone(), floats(v))).collect(),
        }
    }

    pub fn view(&self) -> ChunkFacts<'_> {
        ChunkFacts {
            n: self.n,
            running: self.running,
            phase_index: self.phase_index,
            phase_name: self.phase_name.as_deref(),
            output_muted: self.output_muted,
            clock_frozen: self.clock_frozen,
            bundle: self.bundle.as_deref(),
            muted: &self.muted,
            inhibits: self.inhibits.iter().map(|(k, v)| (k.as_str(), v.as_slice())).collect(),
            checks: self.checks.iter().map(|v| v.as_slice()).collect(),
            events: &self.events,
            derive_samples: self.derive_samples.iter().map(|(k, v)| (k.as_str(), v.as_slice())).collect(),
        }
    }
}

// ============================================================ config

struct KnobPolicy {
    control: String,
    label: String,
    units: String,
    strategy: String,
    fixes: String,
    higher_is: String,
    apply: String,
    lo: f64,
    hi: f64,
    round_to: Option<f64>,
    decimals: usize,
    between: u64,
    step: Option<f64>,
    from_entity: Option<String>,
    window: Option<u64>,
    percentile: Option<f64>,
    citations: Vec<String>,
}

struct CheckInfo {
    name: Option<String>,
    knob: Option<String>,
    higher_is: Option<String>,
}

struct BundleInfo {
    combine: String,
    checks: Vec<CheckInfo>,
}

struct Config {
    target: (f64, f64),
    phases: Option<BTreeSet<String>>,
    watch: u64,
    between: u64,
    settle: u64,
    guards: BTreeMap<String, (f64, Option<String>)>,
    limiters: BTreeMap<String, String>,
    tighten_first: Vec<String>,
    knobs: BTreeMap<String, KnobPolicy>,
    knob_for_check: BTreeMap<String, String>,
    bundles: BTreeMap<String, BundleInfo>,
    relevant: BTreeSet<String>,
    inhibit_controls: BTreeSet<String>,
    labels: BTreeMap<String, String>,
    units: BTreeMap<String, String>,
    defaults: BTreeMap<String, f64>,
    provenance: Value,
}

fn build_config(p: &Protocol, sr: f64) -> Config {
    let ap = p.autopilot.as_ref();
    let tr = trace_protocol(p);
    let mut labels = BTreeMap::new();
    let mut units = BTreeMap::new();
    let mut defaults = BTreeMap::new();
    for (name, c) in &p.controls {
        let label = c.label.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| name.clone());
        labels.insert(name.clone(), label);
        units.insert(name.clone(), units_of(&c.type_kind).to_string());
        if let Some(Expr::Number { value, .. }) = &c.default {
            defaults.insert(name.clone(), *value);
        }
    }
    let phase_list = p.session.as_ref().map(|s| s.phases.as_slice()).unwrap_or(&[]);
    let phases: Option<BTreeSet<String>> = if let Some(ph) = ap.and_then(|a| a.phases.clone()) {
        Some(ph.into_iter().collect())
    } else if !phase_list.is_empty() {
        Some(phase_list.iter().filter(|x| !x.output_muted).map(|x| x.name.clone()).collect())
    } else {
        None
    };
    let samples = |v: Option<u64>, d: f64| v.unwrap_or_else(|| default_samples(d, sr));
    let between = samples(ap.and_then(|a| a.between_moves_samples), DEFAULT_BETWEEN_S);
    let mut guards = BTreeMap::new();
    for name in p.inhibits.keys() {
        let g = ap.and_then(|a| a.guards.get(name));
        guards.insert(
            name.clone(),
            match g {
                Some(g) => (g.max, g.say.clone()),
                None => (DEFAULT_GUARD_MAX, None),
            },
        );
    }
    let mut knobs = BTreeMap::new();
    for (name, c) in &p.controls {
        let Some(pol) = &c.autopilot else { continue };
        knobs.insert(
            name.clone(),
            KnobPolicy {
                control: name.clone(),
                label: pol.say.clone().filter(|s| !s.is_empty()).unwrap_or_else(|| labels[name].clone()),
                units: units[name].clone(),
                strategy: pol.strategy.clone(),
                fixes: pol.fixes.clone(),
                higher_is: pol.higher_is.clone(),
                apply: pol.apply.clone(),
                lo: pol.limits[0],
                hi: pol.limits[1],
                round_to: pol.round_to,
                decimals: pol.decimals,
                between: pol.between_moves_samples.unwrap_or(between),
                step: pol.step,
                from_entity: pol.from.clone(),
                window: pol.window_samples,
                percentile: pol.percentile,
                citations: pol.citation.clone(),
            },
        );
    }
    let knob_for_check = knobs.iter().map(|(n, k)| (k.fixes.clone(), n.clone())).collect();
    let bundles = tr
        .bundles
        .iter()
        .map(|(k, b)| {
            (
                k.clone(),
                BundleInfo {
                    combine: b.combine.clone(),
                    checks: b
                        .checks
                        .iter()
                        .map(|c| CheckInfo { name: c.name.clone(), knob: c.knob.clone(), higher_is: c.higher_is.clone() })
                        .collect(),
                },
            )
        })
        .collect();
    let provenance = match ap {
        Some(a) => json!({"evidence": a.evidence, "citation": a.citation,
                          "rationale": a.rationale, "reviewed": a.reviewed}),
        None => Value::Null,
    };
    let target = match ap.and_then(|a| a.reward_target.clone()) {
        Some(v) if v.len() == 2 => (v[0], v[1]),
        _ => DEFAULT_TARGET,
    };
    let mut relevant: BTreeSet<String> = tr.check_controls.iter().cloned().collect();
    relevant.extend(tr.inhibit_controls.iter().cloned());
    Config {
        target,
        phases,
        watch: samples(ap.and_then(|a| a.watch_samples), DEFAULT_WATCH_S),
        between,
        settle: samples(ap.and_then(|a| a.equipment_settle_samples), DEFAULT_SETTLE_S),
        guards,
        limiters: ap.map(|a| a.limiters.iter().map(|(k, v)| (k.clone(), v.say.clone())).collect()).unwrap_or_default(),
        tighten_first: ap.map(|a| a.tighten_first.clone()).unwrap_or_default(),
        knobs,
        knob_for_check,
        bundles,
        relevant,
        inhibit_controls: tr.inhibit_controls.iter().cloned().collect(),
        labels,
        units,
        defaults,
        provenance,
    }
}

// ============================================================ advisor

struct Bucket {
    start: u64,
    end: u64,
    n_train: u64,
    n_clean: u64,
    n_cond: u64,
    checks: Vec<u64>,
    guards: BTreeMap<String, u64>,
    events: u64,
}

struct Evidence {
    start: u64,
    end: u64,
    n_clean: u64,
    reward_rate: Option<f64>,
    check_rates: Vec<f64>,
    guard_rates: BTreeMap<String, f64>,
    chimes_per_min: f64,
}

struct Reversal {
    control: String,
    from: f64,
    direction: String,
    checked: bool,
    fire: bool,
}

type Decision = (Value, Option<String>);

pub struct Advisor {
    sr: f64,
    cfg: Config,
    pub values: BTreeMap<String, f64>,
    now: u64,
    buckets: Vec<Bucket>,
    rebase: BTreeMap<String, Vec<f64>>,
    last_phase_index: Option<i64>,
    in_training: bool,
    bundle_key: String,
    settle_until: u64,
    last_move_at: Option<u64>,
    dismissed: BTreeMap<(String, String), u64>,
    reversal: Option<Reversal>,
    counter: u64,
    standing: Option<(String, String)>,
    events: Vec<Value>,
    current: Value,
}

fn set(v: &mut Value, key: &str, x: Value) {
    v.as_object_mut().unwrap().insert(key.to_string(), x);
}

impl Advisor {
    pub fn new(p: &Protocol, sample_rate_hz: f64) -> Advisor {
        let cfg = build_config(p, sample_rate_hz);
        let rebase = cfg
            .knobs
            .iter()
            .filter(|(_, k)| k.strategy == "rebaseline")
            .map(|(n, _)| (n.clone(), Vec::new()))
            .collect();
        let values = cfg.defaults.clone();
        let mut adv = Advisor {
            sr: sample_rate_hz,
            cfg,
            values,
            now: 0,
            buckets: Vec::new(),
            rebase,
            last_phase_index: None,
            in_training: false,
            bundle_key: String::new(),
            settle_until: 0,
            last_move_at: None,
            dismissed: BTreeMap::new(),
            reversal: None,
            counter: 0,
            standing: None,
            events: Vec::new(),
            current: Value::Null,
        };
        adv.current = adv.result("hold", "not_training_phase", NOT_TRAINING.to_string(), "observation",
                                 Value::Null, Value::Null, Value::Null, None);
        adv
    }

    // ---- host-facing ----

    pub fn rebaseline_sources(&self) -> Vec<String> {
        let set: BTreeSet<String> =
            self.rebase.keys().filter_map(|c| self.cfg.knobs[c].from_entity.clone()).collect();
        set.into_iter().collect()
    }

    pub fn advice(&self) -> Value {
        self.current.clone()
    }

    pub fn drain_events(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.events)
    }

    pub fn feed(&mut self, f: &ChunkFacts) {
        let start = self.now;
        self.now += f.n as u64;
        let phase_ok = match &self.cfg.phases {
            None => true,
            Some(set) => f.phase_name.map_or(false, |n| set.contains(n)),
        };
        if Some(f.phase_index) != self.last_phase_index {
            self.last_phase_index = Some(f.phase_index);
            if phase_ok {
                self.reset();
            }
        }
        self.in_training = f.running && phase_ok && !f.output_muted && !f.clock_frozen;
        self.bundle_key = f.bundle.unwrap_or("").to_string();
        if self.in_training && start >= self.settle_until {
            self.ingest(f, start);
        }
        self.evaluate();
    }

    pub fn apply(&mut self, advice_id: &str, by: &str) -> Result<(String, f64, Value), String> {
        if by != "clinician" && by != "autopilot" {
            return Err(format!("by must be 'clinician' or 'autopilot', got {by:?}"));
        }
        let res = self.current.clone();
        if res["id"].as_str() != Some(advice_id) || res["state"] != "adjust" {
            return Err(format!("advice {advice_id:?} is not the current suggestion"));
        }
        let ctl = &res["control"];
        let control = ctl["name"].as_str().unwrap().to_string();
        if by == "autopilot" && !ctl["auto_allowed"].as_bool().unwrap() {
            return Err(format!(
                "advice {advice_id:?} changes {control:?}, which this protocol allows only as a suggestion"
            ));
        }
        let to = ctl["proposed"].as_f64().unwrap();
        let frm = self.values.get(&control).copied();
        self.values.insert(control.clone(), to);
        let ev = self.emit("applied", vec![
            ("id", json!(advice_id)), ("control", json!(control)),
            ("from", json!(frm.map(r6))), ("to", json!(to)), ("by", json!(by)),
        ]);
        if res["reason"] == "reversal" {
            self.reversal = None;
        } else {
            self.reversal = Some(Reversal {
                control: control.clone(),
                from: frm.unwrap_or(to),
                direction: ctl["direction"].as_str().unwrap().to_string(),
                checked: false,
                fire: false,
            });
        }
        self.last_move_at = Some(self.now);
        self.standing = None;
        self.reset();
        self.evaluate();
        Ok((control, to, ev))
    }

    pub fn dismiss(&mut self, advice_id: &str) -> Result<Value, String> {
        let res = self.current.clone();
        let state = res["state"].as_str().unwrap_or("");
        if res["id"].as_str() != Some(advice_id) || (state != "adjust" && state != "hint") {
            return Err(format!("advice {advice_id:?} is not the current suggestion"));
        }
        let name = res["control"]["name"].as_str().unwrap().to_string();
        let direction = res["control"]["direction"].as_str().unwrap().to_string();
        let between = self.cfg.knobs.get(&name).map_or(self.cfg.between, |k| k.between);
        self.dismissed.insert((name.clone(), direction), self.now + between);
        if res["reason"] == "reversal" {
            self.reversal = None;
        }
        let ev = self.emit("dismissed", vec![("id", json!(advice_id)), ("control", json!(name))]);
        self.standing = None;
        self.evaluate();
        Ok(ev)
    }

    pub fn note_control(&mut self, control: &str, value: f64, source: &str) {
        let frm = self.values.get(control).copied();
        self.values.insert(control.to_string(), value);
        if !self.cfg.relevant.contains(control) {
            return;
        }
        if source == "manual" {
            self.emit("changed_manually", vec![
                ("control", json!(control)), ("from", json!(frm.map(r6))), ("to", json!(r6(value))),
            ]);
            if let Some((_, id)) = self.standing.take() {
                self.emit("superseded", vec![("id", json!(id)), ("reason", json!("changed_manually"))]);
            }
            if self.reversal.as_ref().is_some_and(|r| r.control == control) {
                self.reversal = None;
            }
            self.last_move_at = Some(self.now);
        }
        self.reset();
        self.evaluate();
    }

    pub fn mark_equipment_change(&mut self) {
        self.emit("equipment_change", vec![]);
        self.settle_until = self.now + self.cfg.settle;
        self.reset();
        self.evaluate();
    }

    pub fn policy(&self) -> Value {
        let c = &self.cfg;
        let mut guards = Map::new();
        for (g, (m, s)) in &c.guards {
            guards.insert(g.clone(), json!({"max": r6(*m), "say": s}));
        }
        let mut controls = Map::new();
        for (name, p) in &c.knobs {
            controls.insert(name.clone(), json!({
                "label": p.label, "units": p.units, "strategy": p.strategy, "fixes": p.fixes,
                "higher_is": p.higher_is, "apply": p.apply, "limits": [r6(p.lo), r6(p.hi)],
                "round_to": p.round_to, "step": p.step, "percentile": p.percentile,
                "between_moves_s": r6(p.between as f64 / self.sr), "citation": p.citations,
            }));
        }
        json!({
            "advisor_version": ADVISOR_VERSION,
            "provenance": c.provenance,
            "reward_target": [r6(c.target.0), r6(c.target.1)],
            "phases": c.phases.as_ref().map(|s| s.iter().cloned().collect::<Vec<_>>()),
            "watch_s": r6(c.watch as f64 / self.sr),
            "between_moves_s": r6(c.between as f64 / self.sr),
            "equipment_settle_s": r6(c.settle as f64 / self.sr),
            "tighten_first": c.tighten_first,
            "guards": guards,
            "limiters": c.limiters,
            "controls": controls,
        })
    }

    // ---- evidence ----

    fn bundle(&self) -> Option<&BundleInfo> {
        self.cfg.bundles.get(&self.bundle_key)
    }

    fn reset(&mut self) {
        self.buckets.clear();
        for buf in self.rebase.values_mut() {
            buf.clear();
        }
    }

    fn ingest(&mut self, f: &ChunkFacts, start: u64) {
        let n = f.n;
        let clean: Vec<bool> = f.muted.iter().map(|m| !m).collect();
        let any = self.bundle().is_some_and(|b| b.combine == "any");
        let mut n_cond = 0u64;
        if !f.checks.is_empty() {
            for i in 0..n {
                let cond = if any {
                    f.checks.iter().any(|c| c[i])
                } else {
                    f.checks.iter().all(|c| c[i])
                };
                if cond && clean[i] {
                    n_cond += 1;
                }
            }
        }
        let count = |xs: &[bool]| xs.iter().zip(&clean).filter(|(x, c)| **x && **c).count() as u64;
        let bucket = Bucket {
            start,
            end: self.now,
            n_train: n as u64,
            n_clean: clean.iter().filter(|c| **c).count() as u64,
            n_cond,
            checks: f.checks.iter().map(|c| count(c)).collect(),
            guards: f.inhibits.iter().map(|(k, v)| (k.to_string(), v.iter().filter(|x| **x).count() as u64)).collect(),
            events: count(f.events),
        };
        self.buckets.push(bucket);
        let controls: Vec<String> = self.rebase.keys().cloned().collect();
        for control in controls {
            let (from, window) = {
                let k = &self.cfg.knobs[&control];
                (k.from_entity.clone(), k.window.unwrap_or(0) as usize)
            };
            let Some(src) = f.derive_samples.iter().find(|(k, _)| Some(*k) == from.as_deref()).map(|(_, v)| *v) else {
                continue;
            };
            let buf = self.rebase.get_mut(&control).unwrap();
            for (v, c) in src.iter().zip(&clean) {
                if *c && v.is_finite() {
                    buf.push(*v);
                }
            }
            if buf.len() > window {
                let drop = buf.len() - window;
                buf.drain(..drop);
            }
        }
        let horizon = self.now.saturating_sub(2 * self.cfg.watch);
        self.buckets.retain(|b| b.end > horizon);
    }

    fn evidence(&self) -> Option<Evidence> {
        let horizon = self.now.saturating_sub(2 * self.cfg.watch);
        let mut sel: Vec<&Bucket> = Vec::new();
        let mut clean = 0u64;
        for b in self.buckets.iter().rev() {
            if b.end <= horizon {
                break;
            }
            sel.push(b);
            clean += b.n_clean;
            if clean >= self.cfg.watch {
                break;
            }
        }
        if sel.is_empty() {
            return None;
        }
        sel.reverse();
        let n_train: u64 = sel.iter().map(|b| b.n_train).sum();
        let n_clean: u64 = sel.iter().map(|b| b.n_clean).sum();
        let k = self.bundle().map_or(0, |b| b.checks.len());
        let has = k > 0 && n_clean > 0 && sel.iter().all(|b| b.checks.len() == k);
        let nc = n_clean as f64;
        let mut guard_rates = BTreeMap::new();
        for g in self.cfg.guards.keys() {
            let a: u64 = sel.iter().map(|b| b.guards.get(g).copied().unwrap_or(0)).sum();
            guard_rates.insert(g.clone(), if n_train > 0 { a as f64 / n_train as f64 } else { 0.0 });
        }
        let events: u64 = sel.iter().map(|b| b.events).sum();
        Some(Evidence {
            start: sel[0].start,
            end: sel[sel.len() - 1].end,
            n_clean,
            reward_rate: if has { Some(sel.iter().map(|b| b.n_cond).sum::<u64>() as f64 / nc) } else { None },
            check_rates: if has {
                (0..k).map(|i| sel.iter().map(|b| b.checks[i]).sum::<u64>() as f64 / nc).collect()
            } else {
                vec![]
            },
            guard_rates,
            chimes_per_min: if n_clean > 0 { events as f64 / (nc / self.sr / 60.0) } else { 0.0 },
        })
    }

    fn check_label(&self, i: usize) -> String {
        let name = self.bundle().and_then(|b| b.checks.get(i)).and_then(|c| c.name.clone());
        name.unwrap_or_else(|| format!("check {i}"))
    }

    fn evidence_dict(&self, ev: &Evidence) -> Value {
        let mut checks = Map::new();
        for (i, r) in ev.check_rates.iter().enumerate() {
            checks.insert(self.check_label(i), json!(r6(*r)));
        }
        let mut guards = Map::new();
        for (g, r) in &ev.guard_rates {
            guards.insert(g.clone(), json!(r6(*r)));
        }
        json!({
            "window_start_s": r6(ev.start as f64 / self.sr),
            "window_end_s": r6(ev.end as f64 / self.sr),
            "clean_s": r6(ev.n_clean as f64 / self.sr),
            "required_s": r6(self.cfg.watch as f64 / self.sr),
            "reward_rate": ev.reward_rate.map(r6),
            "target": [r6(self.cfg.target.0), r6(self.cfg.target.1)],
            "checks": checks,
            "guards": guards,
            "chimes_per_min": r6(ev.chimes_per_min),
        })
    }

    // ---- decision ----

    #[allow(clippy::too_many_arguments)]
    fn result(&self, state: &str, reason: &str, message: String, level: &str, limiter: Value,
              control: Value, evidence: Value, eligible_at: Option<u64>) -> Value {
        json!({
            "advisor_version": ADVISOR_VERSION, "id": Value::Null, "state": state, "level": level,
            "reason": reason, "message": message, "t_s": r6(self.now as f64 / self.sr),
            "limiter": limiter, "control": control, "evidence": evidence,
            "eligible_at_s": eligible_at.map(|e| r6(e as f64 / self.sr)),
        })
    }

    fn evaluate(&mut self) {
        let (res, key) = self.decide();
        self.publish(res, key);
    }

    fn decide(&mut self) -> Decision {
        let null = Value::Null;
        if !self.in_training {
            return (self.result("hold", "not_training_phase", NOT_TRAINING.to_string(), "observation",
                                null.clone(), null.clone(), null, None), None);
        }
        if self.now < self.settle_until {
            let left = ((self.settle_until - self.now) as f64 / self.sr).ceil() as i64;
            return (self.result("hold", "equipment_settling",
                                format!("Settling after equipment change ({left} s left)."),
                                "observation", null.clone(), null.clone(), null, None), None);
        }
        let ev = self.evidence();
        let evd = ev.as_ref().map_or(Value::Null, |e| self.evidence_dict(e));
        if let Some(e) = &ev {
            let mut worst: Option<&String> = None;
            for (g, (mx, _)) in &self.cfg.guards {
                let r = e.guard_rates[g];
                if r > *mx && worst.map_or(true, |w| r > e.guard_rates[w]) {
                    worst = Some(g);
                }
            }
            if let Some(w) = worst {
                let say = self.cfg.guards[w].1.clone().unwrap_or_else(|| format!("Frequent {w} guard activity."));
                let msg = format!("{say} ({w} guard active {}% of training time).", pct(e.guard_rates[w]));
                return (self.result("hold", "guard", msg, "observation", null.clone(), null, evd, None), None);
            }
        }
        let clean = ev.as_ref().map_or(0, |e| e.n_clean);
        if clean < self.cfg.watch {
            let msg = format!("Collecting clean signal ({} of {}).",
                              mmss(clean as f64 / self.sr), mmss(self.cfg.watch as f64 / self.sr));
            return (self.result("collecting", "collecting", msg, "observation", null.clone(), null, evd, None), None);
        }
        let ev = ev.unwrap();
        let Some(rate) = ev.reward_rate else {
            return (self.result("hold", "observing",
                                "Observing: this protocol has no reward condition to judge.".to_string(),
                                "observation", null.clone(), null, evd, None), None);
        };
        let (lo, hi) = self.cfg.target;
        let head = format!("Reward met {}% of clean time (target {}-{}%).", pct(rate), pct(lo), pct(hi));
        if let Some(rev) = self.reversal_step(rate, &evd) {
            return rev;
        }
        if lo <= rate && rate <= hi {
            return (self.result("hold", "on_track", format!("On track. {head}"), "observation",
                                null.clone(), null, evd, None), None);
        }
        let need = if rate < lo { "easier" } else { "harder" };
        self.outside_band(&ev, evd, need, &head)
    }

    fn reversal_step(&mut self, rate: f64, evd: &Value) -> Option<Decision> {
        let (lo, hi) = self.cfg.target;
        let r = self.reversal.as_mut()?;
        if !r.checked {
            r.checked = true;
            let worse = (r.direction == "harder" && rate < lo) || (r.direction == "easier" && rate > hi);
            if !worse {
                self.reversal = None;
                return None;
            }
            r.fire = true;
        }
        if !r.fire {
            return None;
        }
        let (control, from, direction) = (r.control.clone(), r.from, r.direction.clone());
        let p = &self.cfg.knobs[&control];
        let cur = self.values[&control];
        let back = if direction == "harder" { "easier" } else { "harder" };
        let prev = r6(from);
        let msg = format!(
            "The last change made reward worse ({}% of clean time). Return {} {} -> {}.",
            pct(rate), p.label, fmt_value(cur, p.decimals, &p.units), fmt_value(prev, p.decimals, &p.units));
        let ctl = self.control_dict(p, cur, Some(prev), back);
        let res = self.result("adjust", "reversal", msg, "policy", Value::Null, ctl, evd.clone(), Some(self.now));
        Some((res, Some(format!("adjust|reversal|{control}|{back}|{prev:?}"))))
    }

    fn pick_check(&self, ev: &Evidence, need: &str) -> usize {
        let rates = &ev.check_rates;
        if need == "easier" {
            let mut best = 0;
            for i in 1..rates.len() {
                if rates[i] < rates[best] {
                    best = i;
                }
            }
            return best;
        }
        let names: Vec<Option<String>> = self.bundle().unwrap().checks.iter().map(|c| c.name.clone()).collect();
        for name in &self.cfg.tighten_first {
            if let Some(idx) = names.iter().position(|n| n.as_deref() == Some(name.as_str())) {
                if let Some(control) = self.cfg.knob_for_check.get(name) {
                    if self.propose(&self.cfg.knobs[control], "harder").is_some() {
                        return idx;
                    }
                }
            }
        }
        let mut best = 0;
        for i in 1..rates.len() {
            if rates[i] > rates[best] {
                best = i;
            }
        }
        best
    }

    fn propose(&self, p: &KnobPolicy, need: &str) -> Option<f64> {
        let cur = self.values[&p.control];
        let up = (need == "easier") == (p.higher_is == "easier");
        let v = match p.strategy.as_str() {
            "fixed_step" => {
                let s = p.step.unwrap();
                if up { cur + s } else { cur - s }
            }
            "proportional_step" => {
                let s = p.step.unwrap();
                if up { cur * (1.0 + s) } else { cur * (1.0 - s) }
            }
            _ => {
                let buf = self.rebase.get(&p.control)?;
                if (buf.len() as u64) < p.window.unwrap_or(0) {
                    return None;
                }
                percentile_of(buf, p.percentile.unwrap())
            }
        };
        let v = r6(snap(v, p.round_to).max(p.lo).min(p.hi));
        if (v - cur).abs() < 1e-9 || (v > cur) != up {
            return None;
        }
        Some(v)
    }

    fn control_dict(&self, p: &KnobPolicy, cur: f64, proposed: Option<f64>, need: &str) -> Value {
        json!({
            "name": p.control, "label": p.label, "units": p.units, "round_to": p.round_to,
            "current": r6(cur), "proposed": proposed, "direction": need, "strategy": p.strategy,
            "auto_allowed": p.apply == "auto" && p.strategy != "rebaseline",
        })
    }

    fn eligible_at(&self, p: &KnobPolicy, need: &str) -> Option<u64> {
        let mut t = self.last_move_at.map(|m| m + p.between);
        if let Some(d) = self.dismissed.get(&(p.control.clone(), need.to_string())) {
            if t.map_or(true, |t0| *d > t0) {
                t = Some(*d);
            }
        }
        t
    }

    fn outside_band(&self, ev: &Evidence, evd: Value, need: &str, head: &str) -> Decision {
        let idx = self.pick_check(ev, need);
        let info = &self.bundle().unwrap().checks[idx];
        let label = self.check_label(idx);
        let limiter = json!({"check": label, "pass_rate": r6(ev.check_rates[idx])});
        let control = info.name.as_ref().and_then(|n| self.cfg.knob_for_check.get(n));
        let p = control.and_then(|c| self.cfg.knobs.get(c));
        let Some(p) = p else {
            let say = info.name.as_ref().and_then(|n| self.cfg.limiters.get(n));
            if say.is_none() && self.cfg.knobs.is_empty() {
                if let Some(h) = self.hint(info, need, head, &label, &limiter, &evd) {
                    return h;
                }
            }
            let text = say.cloned().unwrap_or_else(|| "No adjustable setting addresses it; holding.".to_string());
            return (self.result("hold", "no_knob", format!("{head} The limiter is {label}. {text}"),
                                "observation", limiter, Value::Null, evd, None), None);
        };
        let cur = self.values[&p.control];
        let proposed = self.propose(p, need);
        let ctl = self.control_dict(p, cur, proposed, need);
        let Some(proposed) = proposed else {
            let edge = if need == "easier" { "easiest" } else { "hardest" };
            let msg = format!("{head} {} is already at its {edge} allowed value ({}).",
                              p.label, fmt_value(cur, p.decimals, &p.units));
            return (self.result("hold", "at_limit", msg, "policy", limiter, ctl, evd, None), None);
        };
        if let Some(eligible) = self.eligible_at(p, need) {
            if self.now < eligible {
                let msg = format!("{head} {} can change again in {}.", p.label,
                                  mmss((eligible - self.now) as f64 / self.sr));
                return (self.result("hold", "cooldown", msg, "policy", limiter, ctl, evd, Some(eligible)), None);
            }
        }
        let verb = if proposed > cur { "Raise" } else { "Lower" };
        let reason = if need == "easier" { "too_strict" } else { "too_easy" };
        let msg = format!("{head} The limiter is {label}. {verb} {} {} -> {}.", p.label,
                          fmt_value(cur, p.decimals, &p.units), fmt_value(proposed, p.decimals, &p.units));
        let key = format!("adjust|{reason}|{}|{need}|{proposed:?}", p.control);
        (self.result("adjust", reason, msg, "policy", limiter, ctl, evd, Some(self.now)), Some(key))
    }

    fn hint(&self, info: &CheckInfo, need: &str, head: &str, label: &str, limiter: &Value,
            evd: &Value) -> Option<Decision> {
        let control = info.knob.as_ref()?;
        if self.cfg.inhibit_controls.contains(control) {
            return None;
        }
        if let Some(d) = self.dismissed.get(&(control.clone(), need.to_string())) {
            if self.now < *d {
                let msg = format!("{head} The limiter is {label}. Hint dismissed; holding for {}.",
                                  mmss((*d - self.now) as f64 / self.sr));
                return Some((self.result("hold", "cooldown", msg, "hint", limiter.clone(), Value::Null,
                                         evd.clone(), Some(*d)), None));
            }
        }
        let up = (need == "easier") == (info.higher_is.as_deref() == Some("easier"));
        let name = self.cfg.labels.get(control).cloned().unwrap_or_else(|| control.clone());
        let ctl = json!({
            "name": control, "label": name, "units": self.cfg.units.get(control).cloned().unwrap_or_default(),
            "round_to": Value::Null, "current": self.values.get(control).map(|v| r6(*v)),
            "proposed": Value::Null, "direction": need, "strategy": Value::Null, "auto_allowed": false,
        });
        let verb = if need == "easier" { "easing" } else { "tightening" };
        let word = if up { "higher" } else { "lower" };
        let msg = format!("{head} The limiter is {label}. Consider {verb} {name} ({word} is {need}).");
        let reason = if need == "easier" { "too_strict" } else { "too_easy" };
        Some((self.result("hint", reason, msg, "hint", limiter.clone(), ctl, evd.clone(), None),
              Some(format!("hint|{control}|{need}"))))
    }

    // ---- lifecycle ----

    fn emit(&mut self, kind: &str, fields: Vec<(&str, Value)>) -> Value {
        let mut ev = Map::new();
        ev.insert("t_s".to_string(), json!(r6(self.now as f64 / self.sr)));
        ev.insert("kind".to_string(), json!(kind));
        for (k, v) in fields {
            ev.insert(k.to_string(), v);
        }
        let ev = Value::Object(ev);
        self.events.push(ev.clone());
        ev
    }

    fn publish(&mut self, mut res: Value, key: Option<String>) {
        if let Some(key) = key {
            if let Some((k, id)) = &self.standing {
                if *k == key {
                    let id = id.clone();
                    set(&mut res, "id", json!(id));
                    self.current = res;
                    return;
                }
            }
            if let Some((_, old)) = self.standing.take() {
                let reason = res["reason"].clone();
                self.emit("superseded", vec![("id", json!(old)), ("reason", reason)]);
            }
            self.counter += 1;
            let new_id = format!("adv-{:04}", self.counter);
            self.standing = Some((key, new_id.clone()));
            set(&mut res, "id", json!(new_id));
            let mut fields = vec![
                ("id", json!(new_id)),
                ("reason", res["reason"].clone()),
                ("control", res["control"]["name"].clone()),
            ];
            if res["state"] == "adjust" {
                fields.push(("from", res["control"]["current"].clone()));
                fields.push(("to", res["control"]["proposed"].clone()));
            }
            self.emit("suggested", fields);
        } else if let Some((_, old)) = self.standing.take() {
            let reason = res["reason"].as_str().unwrap_or("").to_string();
            let kind = if BLOCKING.contains(&reason.as_str()) { "blocked" } else { "superseded" };
            self.emit(kind, vec![("id", json!(old)), ("reason", json!(reason))]);
        }
        self.current = res;
    }
}
```

**Two traps to re-check against the Python while porting:**
- Python keys the standing suggestion on a **tuple** containing the proposed float. Rust uses a string with `{proposed:?}`. Two keys are equal iff the floats are bit-identical, which is the same rule.
- In `reversal_step`, Python reads `self.reversal` and may set it to `None` in the middle of the step. The Rust version captures `(lo, hi)` before borrowing `self.reversal` mutably, for the borrow checker. The behaviour is unchanged.

- [ ] **Step 4: Run the tests**

Run: `cd refrain-core && ~/.cargo/bin/cargo test --lib advisor 2>&1 | tail -8`
Expected: PASS.
Run: `~/.cargo/bin/cargo clippy --lib 2>&1 | grep -E "^(warning|error)" | head` and fix new warnings in `advisor.rs`.

- [ ] **Step 5: Commit**

```bash
git add refrain-core/src/advisor.rs refrain-core/src/lib.rs
git commit -m "feat(core): Rust port of the autopilot advisor and reward-check tracer"
```

---

## Task 13: Parity fixtures — tracer and scripted scenarios

**Files:**
- Create: `refrain-core/tools/advisor_scenarios.py`
- Modify: `refrain-core/tools/gen_fixtures.py` (new `_gen_advisor_fixtures()`; call it from `main`)
- Create: `refrain-core/tests/advisor_parity.rs`
- Generated: `refrain-core/tests/fixtures/advisor_trace.json`, `advisor_scenarios.json`, `advisor_ap.ir.json`

**Interfaces:**
- Consumes: Python `Advisor`, `facts_from_json`, `trace_protocol` (Tasks 6, 9); Rust `Advisor`, `OwnedFacts`, `trace_protocol`, `trace_to_json` (Task 12).
- Produces:
  - `advisor_scenarios.json`: `{name: {"ir": <IR-JSON>, "sample_rate_hz": 256.0, "steps": [{"op": <op>, "result": <event|null>, "error": bool, "advice": <dict>, "events": [<event>]}]}}`
  - `advisor_trace.json`: `{fixture_stem: <trace_protocol output>}`
  - `advisor_ap.ir.json`: `ap(emg_thr="100")` compiled, used by Task 14.

Scenario ops: `feed` (compact facts), `apply {id, by}`, `dismiss {id}`, `note {control, value, source}`, `equipment {}`.

- [ ] **Step 1: Scenario definitions**

Create `refrain-core/tools/advisor_scenarios.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Scripted advisor scenarios for the Python<->Rust parity fixtures. Each is
(protocol source, resolve bindings, ops). Keep them covering every reason
code, every event kind, both strategies' arithmetic and the compact-facts
decoder's list forms."""

from tests._autopilot_fixtures import AUTOPILOT_BLOCK, ap, plain

REBASE = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
          'percentile = 60; higher_is = "harder"; apply = "suggest"; '
          'only_when = threshold_style == "baseline" }')


def feed(checks=(True, True), *, muted=False, emg=None, phase=1, name="train1", running=True,
         output_muted=False, frozen=False, events=0, t_env=5.0, n=256, k=1):
    op = {"op": "feed", "n": n, "running": running, "phase_index": phase, "phase_name": name,
          "output_muted": output_muted, "clock_frozen": frozen, "bundle": None,
          "muted": muted, "inhibits": {"emg": muted if emg is None else emg},
          "checks": list(checks), "events": events, "derive_samples": {"derive/t_env": t_env}}
    return [op] * k


def strict(k=20, hits=1):
    return [feed((True, i < hits))[0] for i in range(k)]


EASY = feed(k=20)


def op(kind, **kw):
    return [{"op": kind, **kw}]


SCENARIOS = {
    "too_strict_apply_cooldown": (ap(), {}, strict() + op("apply", id="adv-0001", by="autopilot")
                                  + strict() + strict(10, 1)),
    "too_easy_reversal": (ap(), {}, EASY + op("apply", id="adv-0001", by="clinician")
                          + strict(hits=0)),
    "fallback_and_refusals": (ap(), {}, op("note", control="xover", value=1.0, source="seed")
                              + EASY + op("apply", id="adv-0001", by="autopilot")
                              + op("apply", id="adv-0001", by="clinician")
                              + op("apply", id="adv-0009", by="clinician")),
    "manual_outside_limits": (ap(), {}, op("note", control="xover", value=0.45, source="manual")
                              + strict()),
    "guard_block_and_equipment": (ap(), {}, strict() + feed((True, False), muted=True, k=5)
                                  + op("equipment") + feed(k=7)),
    "dismiss": (ap(), {}, strict() + op("dismiss", id="adv-0001") + strict(k=12)),
    "phases": (ap(), {}, feed(phase=0, name="settle", output_muted=True, k=3) + feed(k=20)
               + feed(phase=2, name="rest", output_muted=True) + feed(phase=3, name="train2", k=2)
               + feed(phase=3, name="train2", frozen=True)),
    "baseline_proportional": (ap(), {"threshold_style": "baseline"},
                              [feed((i < 1, True))[0] for i in range(20)]),
    "rebaseline": (ap(t_uv_ap=REBASE), {"threshold_style": "baseline"},
                   [feed((i < 1, True), t_env=5.0 + 0.1 * i)[0] for i in range(20)]),
    "hint": (plain(theta_as=' as "theta"', xover_as=' as "crossover"'), {},
             strict(k=120, hits=6) + op("dismiss", id="adv-0001") + strict(k=3, hits=0)),
    "no_hint_guard_knob": (plain(emg_thr="xover"), {}, strict(k=120, hits=6)),
    "stale": (ap(autopilot=AUTOPILOT_BLOCK.replace("max = 15%", "max = 99%")), {},
              (feed() + feed(muted=True, emg=False, k=7)) * 25),
    "mixed_lists": (ap(), {}, [
        feed((True, i % 3 == 0), muted=[j % 5 == 0 for j in range(256)],
             emg=[j % 7 == 0 for j in range(256)], events=i % 4)[0] for i in range(40)]),
}
```

- [ ] **Step 2: Generator**

In `refrain-core/tools/gen_fixtures.py`, add:

```python
def _gen_advisor_fixtures() -> None:
    """Python-reference outputs the Rust advisor must reproduce (advisor_parity.rs)."""
    from refrain import parse, resolve
    from refrain.advisor import AdviceError, Advisor, facts_from_json
    from refrain.advisor_trace import trace_protocol
    from refrain.ir_json import ir_to_json_obj
    from tests._autopilot_fixtures import ap
    from advisor_scenarios import SCENARIOS  # same directory as this script

    sr = 256.0
    scenarios = {}
    for name, (src, bindings, ops) in SCENARIOS.items():
        ir = ir_to_json_obj(resolve(parse(src), bindings=bindings or None), sample_rate_hz=sr)
        adv = Advisor(ir, sr)
        steps = []
        for o in ops:
            result, error = None, False
            try:
                if o["op"] == "feed":
                    adv.feed(facts_from_json(o))
                elif o["op"] == "apply":
                    result = adv.apply(o["id"], o["by"])[2]
                elif o["op"] == "dismiss":
                    result = adv.dismiss(o["id"])
                elif o["op"] == "note":
                    adv.note_control(o["control"], o["value"], o["source"])
                elif o["op"] == "equipment":
                    adv.mark_equipment_change()
            except (AdviceError, ValueError):
                error = True
            steps.append({"op": o, "result": result, "error": error,
                          "advice": adv.advice(), "events": adv.drain_events()})
        scenarios[name] = {"ir": ir, "sample_rate_hz": sr, "steps": steps}
    (FIXTURES / "advisor_scenarios.json").write_text(json.dumps(scenarios) + "\n")

    traces = {}
    for path in sorted(FIXTURES.glob("*.ir.json")):
        traces[path.name] = trace_protocol(json.loads(path.read_text()))
    ap_ir = ir_to_json_obj(resolve(parse(ap(emg_thr="100"))), sample_rate_hz=sr)
    (FIXTURES / "advisor_ap.ir.json").write_text(json.dumps(ap_ir, indent=2) + "\n")
    traces["advisor_ap.ir.json"] = trace_protocol(ap_ir)
    (FIXTURES / "advisor_trace.json").write_text(json.dumps(traces, indent=1) + "\n")
```

Use the script's existing name for the fixtures directory. It writes to `tests/fixtures/`, so match its constant: `grep -n "fixtures" refrain-core/tools/gen_fixtures.py | head`. Add `sys.path.insert(0, str(Path(__file__).parent))` near the top if the script doesn't already import siblings. Call `_gen_advisor_fixtures()` at the end of the script's `main()`.

Run: `PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/gen_fixtures.py`
Expected: the three new files exist, and `git status` shows **no** change to any existing fixture.

- [ ] **Step 3: Write the parity test**

Create `refrain-core/tests/advisor_parity.rs`:

```rust
// Copyright 2026 Refrain Language Authors.
// Licensed under the Apache License, Version 2.0 (see LICENSE).
//! The Rust advisor must reproduce the Python reference exactly (values
//! compared as numbers, so `1` == `1.0`). Fixtures: tools/gen_fixtures.py.

use std::path::PathBuf;

use refrain_core::advisor::{trace_protocol, trace_to_json, Advisor, OwnedFacts};
use refrain_core::ir::Protocol;
use serde_json::Value;

fn fixture(name: &str) -> Value {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures").join(name);
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

fn same(got: &Value, want: &Value, path: &str) {
    match (got, want) {
        (Value::Number(a), Value::Number(b)) => {
            assert_eq!(a.as_f64(), b.as_f64(), "{path}: rust {a} vs python {b}")
        }
        (Value::Object(a), Value::Object(b)) => {
            let ka: Vec<_> = a.keys().collect();
            let kb: Vec<_> = b.keys().collect();
            assert_eq!(ka, kb, "{path}: keys differ");
            for (k, v) in a {
                same(v, &b[k], &format!("{path}.{k}"));
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{path}: length rust {} vs python {}", a.len(), b.len());
            for (i, (x, y)) in a.iter().zip(b).enumerate() {
                same(x, y, &format!("{path}[{i}]"));
            }
        }
        _ => assert_eq!(got, want, "{path}"),
    }
}

#[test]
fn tracer_matches_python() {
    let want = fixture("advisor_trace.json");
    for (stem, expected) in want.as_object().unwrap() {
        let p: Protocol = serde_json::from_value(fixture(stem)).unwrap();
        same(&trace_to_json(&trace_protocol(&p)), expected, stem);
    }
}

#[test]
fn scenarios_match_python() {
    let all = fixture("advisor_scenarios.json");
    for (name, sc) in all.as_object().unwrap() {
        let p: Protocol = serde_json::from_value(sc["ir"].clone()).unwrap();
        let mut adv = Advisor::new(&p, sc["sample_rate_hz"].as_f64().unwrap());
        for (i, step) in sc["steps"].as_array().unwrap().iter().enumerate() {
            let op = &step["op"];
            let at = format!("{name}#{i}({})", op["op"]);
            let mut result = Value::Null;
            let mut error = false;
            match op["op"].as_str().unwrap() {
                "feed" => {
                    let f = OwnedFacts::from_json(op);
                    adv.feed(&f.view());
                }
                "apply" => match adv.apply(op["id"].as_str().unwrap(), op["by"].as_str().unwrap()) {
                    Ok((_, _, ev)) => result = ev,
                    Err(_) => error = true,
                },
                "dismiss" => match adv.dismiss(op["id"].as_str().unwrap()) {
                    Ok(ev) => result = ev,
                    Err(_) => error = true,
                },
                "note" => adv.note_control(op["control"].as_str().unwrap(),
                                           op["value"].as_f64().unwrap(),
                                           op["source"].as_str().unwrap()),
                "equipment" => adv.mark_equipment_change(),
                other => panic!("unknown op {other}"),
            }
            assert_eq!(error, step["error"].as_bool().unwrap(), "{at}: error flag");
            same(&result, &step["result"], &format!("{at}.result"));
            same(&adv.advice(), &step["advice"], &format!("{at}.advice"));
            same(&Value::Array(adv.drain_events()), &step["events"], &format!("{at}.events"));
        }
    }
}
```

- [ ] **Step 4: Run it**

Run: `cd refrain-core && ~/.cargo/bin/cargo test --test advisor_parity 2>&1 | tail -15`
Expected: PASS. A failure names the scenario, step and JSON path, e.g. `stale#57(feed).advice.evidence.clean_s`. Fix the Rust side to match Python unless Python is wrong. If Python is wrong, fix both and regenerate.

- [ ] **Step 5: Coverage self-check**

```bash
.venv/bin/python - <<'EOF'
import json
s = json.load(open("refrain-core/tests/fixtures/advisor_scenarios.json"))
reasons = {st["advice"]["reason"] for sc in s.values() for st in sc["steps"]}
kinds = {e["kind"] for sc in s.values() for st in sc["steps"] for e in st["events"]}
print(sorted(reasons)); print(sorted(kinds))
EOF
```

Expected: reasons include `at_limit, collecting, cooldown, equipment_settling, guard, no_knob, not_training_phase, on_track, reversal, too_easy, too_strict`, and kinds include `applied, blocked, changed_manually, dismissed, equipment_change, suggested, superseded`. (`observing` is covered by Task 9's Python test; add a scenario for it if you touch that path.)

- [ ] **Step 6: Commit**

```bash
git add refrain-core/tools/advisor_scenarios.py refrain-core/tools/gen_fixtures.py refrain-core/tests/advisor_parity.rs refrain-core/tests/fixtures/advisor_*.json
git commit -m "test(core): Python<->Rust advisor parity over tracer output and scripted scenarios"
```

---

## Task 14: Wire the advisor into the Rust Evaluator

**Files:**
- Modify: `refrain-core/src/eval.rs` (`Evaluator` struct :799-869; `new` :910; `step_reward_event` :561; `eval_chunk` :1349-1647; `set_control` :1178; `step_seeds` writes loop ~:1705; new accessors after `seed_report`)
- Test: `refrain-core/tests/advice.rs` (new)

**Interfaces:**
- Consumes: `Advisor`, `ChunkFacts` (Task 12); `tests/fixtures/advisor_ap.ir.json` (Task 13).
- Produces (on `Evaluator`):
  - `advice(&self) -> Value`
  - `apply_advice(&mut self, id: &str, by: &str) -> Result<Value, String>`
  - `dismiss_advice(&mut self, id: &str) -> Result<Value, String>`
  - `mark_equipment_change(&mut self)`
  - `drain_advice_events(&mut self) -> Vec<Value>`
  - `autopilot_policy(&self) -> Value`

- [ ] **Step 1: Write the failing test**

Create `refrain-core/tests/advice.rs`:

```rust
// Copyright 2026 Refrain Language Authors.
// Licensed under the Apache License, Version 2.0 (see LICENSE).
use std::path::PathBuf;

use refrain_core::eval::Evaluator;
use refrain_core::ir::Protocol;

fn proto() -> Protocol {
    let p = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/advisor_ap.ir.json");
    serde_json::from_str(&std::fs::read_to_string(p).unwrap()).unwrap()
}

fn chunks(ev: &mut Evaluator, n: usize) {
    for _ in 0..n {
        ev.step_chunk_events(&vec![vec![1.0_f64]; 256]);
    }
}

#[test]
fn advice_follows_the_session() {
    let mut ev = Evaluator::new(&proto(), 256.0, &["Cz".to_string()]);
    ev.start(false);
    assert_eq!(ev.advice()["reason"], "not_training_phase");
    chunks(&mut ev, 12);
    let a = ev.advice();
    assert_eq!(a["reason"], "collecting");
    assert_eq!(a["evidence"]["clean_s"], 2.0);
    ev.drain_advice_events();
    ev.set_control("xover", 0.7).unwrap();
    assert_eq!(ev.drain_advice_events()[0]["kind"], "changed_manually");
    ev.mark_equipment_change();
    assert_eq!(ev.advice()["reason"], "equipment_settling");
    assert_eq!(ev.autopilot_policy()["controls"]["xover"]["apply"], "auto");
    assert!(ev.apply_advice("adv-0001", "clinician").is_err());
}
```

- [ ] **Step 2: Run and confirm failure**

Run: `cd refrain-core && ~/.cargo/bin/cargo test --test advice 2>&1 | tail -5`
Expected: FAIL to compile (`no method named advice`).

- [ ] **Step 3: Implement**

In `eval.rs`:

1. `use crate::advisor::{Advisor, ChunkFacts};` and `use serde_json::Value;` at the top.
2. Add a field to `Evaluator`: `advisor: Advisor,`. In `new()`, add `advisor: Advisor::new(p, sample_rate_hz),` to the struct literal.
3. `step_reward_event` returns the sub-condition streams too:

```rust
fn step_reward_event(
    re: &mut RewardEvent,
    env: &HashMap<String, Val>,
    n: usize,
) -> (Vec<bool>, Vec<bool>, Vec<bool>, Vec<Vec<bool>>) {
    // ... body unchanged ...
    let (events, holds) = re.dwell.step(&condition);
    (events, holds, sub_lasts, sub_streams)
}
```

   Update its call site in the bundle loop to `let (events, holds, sub_lasts, sub_streams) = step_reward_event(re, &env, n);`.

4. In `eval_chunk`, before the inhibits loop (:1432):

```rust
        let mut adv_inhibits: Vec<(String, Vec<bool>)> = Vec::new();
        let mut adv_checks: Vec<Vec<bool>> = Vec::new();
        let mut adv_events: Vec<bool> = vec![false; n];
```

   Inside the inhibits loop, right after `let active: Vec<bool> = ...;`:

```rust
            let bare_name = ih.canonical_name.split_once('/').map(|(_, s)| s)
                .unwrap_or(ih.canonical_name.as_str()).to_string();
            adv_inhibits.push((bare_name, active.clone()));
```

   In the default reward-event block (:1533-1560), right after `let (events, holds) = re.dwell.step(&condition);`:

```rust
            adv_checks = sub_streams.clone();
            adv_events = events.clone();
```

   In the bundle loop, inside `if is_active { ... }` for the event:

```rust
                    adv_checks = sub_streams.clone();
                    adv_events = events.clone();
```

   (Place these before `events` is moved into `env`.)

   Immediately before `self.last_taps = taps;` (:1641):

```rust
        let sources = self.advisor.rebaseline_sources();
        let derive_samples: Vec<(&str, &[f64])> = sources
            .iter()
            .filter_map(|canon| {
                let bare = canon.split_once('/').map(|(_, s)| s).unwrap_or(canon.as_str());
                match env.get(bare) {
                    Some(Val::F(f)) => Some((canon.as_str(), f.as_slice())),
                    _ => None,
                }
            })
            .collect();
        let phase_name: Option<String> = self.phases.get(self.phase_index).map(|p| p.name.clone());
        let phase_index = if phase_name.is_some() { self.phase_index as i64 } else { -1 };
        let facts = ChunkFacts {
            n,
            running: self.state == State::Run,
            phase_index,
            phase_name: phase_name.as_deref(),
            output_muted: mutes_output,
            clock_frozen: self.clock_frozen,
            bundle: active_bundle.as_deref(),
            muted: &muted,
            inhibits: adv_inhibits.iter().map(|(k, v)| (k.as_str(), v.as_slice())).collect(),
            checks: adv_checks.iter().map(|v| v.as_slice()).collect(),
            events: &adv_events,
            derive_samples,
        };
        self.advisor.feed(&facts);
```

   If `Val::F` holds something other than `Vec<f64>`, adapt `.as_slice()` to its accessor (the taps code uses `last_f` on the same values).

5. `set_control`: replace the final `self.apply_control_value(name, value)` with

```rust
        self.apply_control_value(name, value)?;
        self.advisor.note_control(name, value, "manual");
        Ok(())
```

6. `step_seeds` writes loop:

```rust
        for (name, value) in writes {
            let _ = self.apply_control_value(&name, value); // NOT set_control -> no self-disarm
            self.advisor.note_control(&name, value, "seed");
        }
```

7. Accessors after `seed_report`:

```rust
    /// Current autopilot advice (spec §5.2).
    pub fn advice(&self) -> Value {
        self.advisor.advice()
    }

    /// Apply the current `adjust` advice; refuses a stale id or a forbidden auto-apply.
    pub fn apply_advice(&mut self, advice_id: &str, by: &str) -> Result<Value, String> {
        let (control, value, event) = self.advisor.apply(advice_id, by)?;
        self.apply_control_value(&control, value)?;
        Ok(event)
    }

    pub fn dismiss_advice(&mut self, advice_id: &str) -> Result<Value, String> {
        self.advisor.dismiss(advice_id)
    }

    pub fn mark_equipment_change(&mut self) {
        self.advisor.mark_equipment_change();
    }

    pub fn drain_advice_events(&mut self) -> Vec<Value> {
        self.advisor.drain_events()
    }

    pub fn autopilot_policy(&self) -> Value {
        self.advisor.policy()
    }
```

- [ ] **Step 4: Run the tests**

Run: `cd refrain-core && ~/.cargo/bin/cargo test 2>&1 | grep -E "test result|FAILED|panicked"`
Expected: all `ok`, including `taps.rs` (unchanged key set) and `equivalence.rs`.

- [ ] **Step 5: Commit**

```bash
git add refrain-core/src/eval.rs refrain-core/tests/advice.rs
git commit -m "feat(core): feed the advisor from eval_chunk; advice accessors on Evaluator"
```

---

## Task 15: PyO3 + uniffi surfaces, regenerated bindings, Python delegation

**Files:**
- Modify: `refrain-core/src/python.rs` (`#[pymethods] impl RustEvaluator`, after `seed_report` :163-177)
- Modify: `refrain-core/src/mobile.rs` (`RefrainError` :22; `impl RefrainCore`)
- Regenerate: `refrain-core/bindings/swift/*`, `refrain-core/bindings/kotlin/**`
- Modify: `src/refrain/eval_.py` (Rust delegation in the six advice methods)
- Test: `tests/test_eval_advice.py` (extend)

**Interfaces:**
- Produces:
  - PyO3 `RustEvaluator.advice() -> str`, `apply_advice(id, by) -> str` (raises `ValueError`), `dismiss_advice(id) -> str`, `mark_equipment_change()`, `drain_advice_events() -> str`, `autopilot_policy() -> str`. Strings are JSON.
  - uniffi `RefrainCore` gets the same six methods returning `String` / `Result<String, RefrainError>`, plus a new error variant `RefrainError::Advice { message }`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_eval_advice.py`)

```python
def test_rust_backend_advice_matches_python():
    """Review Focus #5."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    src = ap(emg_thr="100")
    py, rs = _live(src, "python"), _live(src, "rust")
    rng = np.random.default_rng(3)
    for _ in range(40):
        chunk = rng.normal(0.0, 5.0, size=(256, 1))
        py.step_chunk(chunk)
        rs.step_chunk(chunk)
        assert rs.advice() == py.advice()
        assert rs.drain_advice_events() == py.drain_advice_events()
    assert rs.autopilot_policy() == py.autopilot_policy()
    with pytest.raises(AdviceError):
        rs.apply_advice("adv-9999")
```

- [ ] **Step 2: PyO3 methods**

In `refrain-core/src/python.rs` inside `#[pymethods] impl RustEvaluator`:

```rust
    /// `eval_.Evaluator.advice` — JSON text (the Python wrapper parses it).
    fn advice(&self) -> String {
        self.inner.advice().to_string()
    }

    fn apply_advice(&mut self, advice_id: &str, by: &str) -> PyResult<String> {
        self.inner
            .apply_advice(advice_id, by)
            .map(|v| v.to_string())
            .map_err(pyo3::exceptions::PyValueError::new_err)
    }

    fn dismiss_advice(&mut self, advice_id: &str) -> PyResult<String> {
        self.inner
            .dismiss_advice(advice_id)
            .map(|v| v.to_string())
            .map_err(pyo3::exceptions::PyValueError::new_err)
    }

    fn mark_equipment_change(&mut self) {
        self.inner.mark_equipment_change()
    }

    fn drain_advice_events(&mut self) -> String {
        serde_json::Value::Array(self.inner.drain_advice_events()).to_string()
    }

    fn autopilot_policy(&self) -> String {
        self.inner.autopilot_policy().to_string()
    }
```

- [ ] **Step 3: uniffi methods**

In `refrain-core/src/mobile.rs`, add the variant `Advice { message: String }` to `RefrainError` and a matching `Display` arm (`RefrainError::Advice { message } => write!(f, "advice: {message}")`, following the existing arms). Then in `#[uniffi::export] impl RefrainCore`:

```rust
    /// Current autopilot advice as JSON (spec §5.2).
    pub fn advice(&self) -> String {
        self.inner.lock().unwrap().advice().to_string()
    }

    pub fn apply_advice(&self, advice_id: String, by: String) -> Result<String, RefrainError> {
        self.inner.lock().unwrap().apply_advice(&advice_id, &by)
            .map(|v| v.to_string())
            .map_err(|message| RefrainError::Advice { message })
    }

    pub fn dismiss_advice(&self, advice_id: String) -> Result<String, RefrainError> {
        self.inner.lock().unwrap().dismiss_advice(&advice_id)
            .map(|v| v.to_string())
            .map_err(|message| RefrainError::Advice { message })
    }

    pub fn mark_equipment_change(&self) {
        self.inner.lock().unwrap().mark_equipment_change()
    }

    pub fn drain_advice_events(&self) -> String {
        serde_json::Value::Array(self.inner.lock().unwrap().drain_advice_events()).to_string()
    }

    pub fn autopilot_policy(&self) -> String {
        self.inner.lock().unwrap().autopilot_policy().to_string()
    }
```

- [ ] **Step 4: Python delegation**

In `src/refrain/eval_.py`, make each advice method delegate first (keep the Python bodies from Task 10 below the `if`):

```python
    def advice(self) -> dict:
        if self._rust is not None:
            return json.loads(self._rust.advice())
        return self._require_advisor().advice()

    def apply_advice(self, advice_id: str, by: str = "clinician") -> dict:
        if self._rust is not None:
            try:
                return json.loads(self._rust.apply_advice(advice_id, by))
            except ValueError as exc:
                raise AdviceError(str(exc)) from None
        control, value, event = self._require_advisor().apply(advice_id, by)
        self._apply_control(control, value)
        return event

    def dismiss_advice(self, advice_id: str) -> dict:
        if self._rust is not None:
            try:
                return json.loads(self._rust.dismiss_advice(advice_id))
            except ValueError as exc:
                raise AdviceError(str(exc)) from None
        return self._require_advisor().dismiss(advice_id)

    def mark_equipment_change(self) -> None:
        if self._rust is not None:
            self._rust.mark_equipment_change()
            return
        self._require_advisor().mark_equipment_change()

    def drain_advice_events(self) -> list[dict]:
        if self._rust is not None:
            return json.loads(self._rust.drain_advice_events())
        return self._require_advisor().drain_events()

    def autopilot_policy(self) -> dict:
        if self._rust is not None:
            return json.loads(self._rust.autopilot_policy())
        return self._require_advisor().policy()
```

(`json` is already imported in `eval_.py`, since `_build_rust_evaluator` uses `json.dumps`.)

- [ ] **Step 5: Build the wheel and regenerate bindings**

```bash
uv pip install --python .venv/bin/python maturin
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 uv pip install --python .venv/bin/python --reinstall --no-deps ./refrain-core
cd refrain-core
~/.cargo/bin/cargo build --release --features uniffi
LIB=$(ls target/release/librefrain_core.{dylib,so} 2>/dev/null | head -1)
~/.cargo/bin/cargo run --release --features uniffi --bin uniffi-bindgen -- generate --library "$LIB" --language swift --out-dir bindings/swift
~/.cargo/bin/cargo run --release --features uniffi --bin uniffi-bindgen -- generate --library "$LIB" --language kotlin --no-format --out-dir bindings/kotlin
cd ..
```

Then run the drift gate the CI runs: `PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_bindings.py`. It needs the iOS/Android rustup targets. If they are missing locally, add them with `rustup target add aarch64-apple-ios aarch64-linux-android`, or report that the check was skipped. Do not claim it passed.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_eval_advice.py tests/test_eval_rust_backend.py -v -p no:cacheprovider`
Expected: PASS, with the Rust test running (not skipped).
Run: `REFRAIN_EVAL_BACKEND=rust .venv/bin/python -m pytest tests/test_eval_*.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add refrain-core/src/python.rs refrain-core/src/mobile.rs refrain-core/bindings src/refrain/eval_.py tests/test_eval_advice.py
git commit -m "feat(core): advice over PyO3 and uniffi; Python delegates to the Rust backend"
```

---

## Task 16: Whole-session parity (real DSP → advice, both engines)

**Files:**
- Create: `bench/protocols/autopilot_alpha_theta.refrain`
- Modify: `refrain-core/tools/gen_fixtures.py` (new `_gen_advice_session()`)
- Modify: `refrain-core/tests/advisor_parity.rs` (new test)
- Generated: `refrain-core/tests/fixtures/autopilot_alpha_theta.advice.json`, `autopilot_alpha_theta.ir.json`

**Interfaces:**
- Consumes: the Evaluators of both engines (Tasks 10, 14).
- Produces: `<stem>.advice.json`: `{"sample_rate_hz", "channels", "chunk_size", "input": [[x], …], "steps": [{"advice", "applied": <event|null>, "events": [...]}]}`.

- [ ] **Step 1: The bench protocol**

Create `bench/protocols/autopilot_alpha_theta.refrain`. The base was compile-verified during planning. The names, policies and `autopilot` block are the Task 1–8 syntax:

```refrain
// Parity bench for the autopilot advisor: short phases and windows so an
// 80 s run exercises collecting, judging, applying and cooldown in both
// engines. Not a clinical protocol.
protocol "autopilot_alpha_theta" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "autopilot parity bench" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "t_env" { from = "raw"; pipeline = [ magnitude(), smooth(tau: 200 ms) ] }
  derive "a_env" { from = "raw"; pipeline = [ magnitude(), smooth(tau: 2000 ms) ] }
  derive "ratio" { formula = "t_env" / "a_env" }
  threshold "t_t" { signal = "t_env"; type = percentile(target_pct: t_pct, window: 30 s); live_tunable = true }
  inhibit "emg" {
    metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
    threshold = percentile(target_pct: emg_pct, window: 30 s)
    action    = mute(release: 200 ms)
  }
  reward {
    event = dwell(condition: all_of([
      above("t_env", "t_t") as "theta",
      above("ratio", xover) as "crossover",
    ]), duration: 250 ms)
  }
  output { audio_chime = reward.event }
  controls {
    t_pct = percent {
      default = 15; range = (15, 70); live_tunable = true
      autopilot = fixed_step { fixes = "theta"; step = 5; higher_is = "harder"; limits = (15, 60); apply = "auto" }
    }
    xover = number {
      default = 0.60; range = (0.5, 1.5); live_tunable = true; label = "Crossover target"
      autopilot = fixed_step { fixes = "crossover"; step = 0.05; higher_is = "harder"; apply = "auto"; round_to = 0.01 }
    }
    emg_pct = percent { default = 95; range = (50, 99); live_tunable = true }
  }
  autopilot {
    evidence = "experimental"
    citation = "Parity bench (not clinical)"
    rationale = "Short windows so an 80 s run exercises every advisor state."
    reward_target = (20%, 40%)
    watch = 8 s
    between_moves = 10 s
    equipment_settle = 3 s
    tighten_first = ["crossover"]
    emg = guard { max = 30%; say = "Muscle." }
  }
  session { phases = [
    phase { name = "settle"; duration = 5 s;  output_muted = true },
    phase { name = "train1"; duration = 35 s },
    phase { name = "rest";   duration = 5 s;  output_muted = true },
    phase { name = "train2"; duration = 35 s },
  ] }
}
```

Verify: `.venv/bin/python -c "from refrain.compile_json import compile_to_ir_json as c; r=c(open('bench/protocols/autopilot_alpha_theta.refrain').read()); print(r.errors, r.ir_json['refrain_ir_version'])"` → `[] 0.4`.

- [ ] **Step 2: Generator**

Add to `gen_fixtures.py`:

```python
def _gen_advice_session(stem: str = "autopilot_alpha_theta") -> None:
    """One real session through the Python evaluator, auto-applying whatever
    the protocol permits; the Rust test replays it and must match."""
    import numpy as np
    from refrain import parse_file, resolve
    from refrain.eval_ import Evaluator
    from refrain.ir_json import ir_to_json_obj

    sr, chunk = 256.0, 256
    ir = resolve(parse_file(BENCH / f"{stem}.refrain"))
    (FIXTURES / f"{stem}.ir.json").write_text(
        json.dumps(ir_to_json_obj(ir, sample_rate_hz=sr), indent=2) + "\n")
    n = int(sr) * 80
    t = np.arange(n) / sr
    rng = np.random.default_rng(7)
    x = (np.sin(2 * np.pi * 6 * t) * (1 + 0.8 * np.sin(2 * np.pi * t / 20))
         + 0.6 * np.sin(2 * np.pi * 10 * t) + 0.05 * rng.standard_normal(n))
    ev = Evaluator.live(ir, sample_rate_hz=sr, channel_names=("Cz",), backend="python")
    ev.start(skip_warmup=False)
    steps = []
    for i in range(0, n, chunk):
        ev.step_chunk(x[i:i + chunk].reshape(-1, 1))
        a = ev.advice()
        applied = None
        if a["state"] == "adjust" and a["control"]["auto_allowed"]:
            applied = ev.apply_advice(a["id"], by="autopilot")
        steps.append({"advice": a, "applied": applied, "events": ev.drain_advice_events()})
    out = {"sample_rate_hz": sr, "channels": ["Cz"], "chunk_size": chunk,
           "input": [[float(v)] for v in x], "steps": steps}
    (FIXTURES / f"{stem}.advice.json").write_text(json.dumps(out) + "\n")
```

Use the script's own constant for `bench/protocols` (`grep -n "bench" refrain-core/tools/gen_fixtures.py`). Call `_gen_advice_session()` from `main()`. Run the generator, then check the run exercised the states:

```bash
PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/gen_fixtures.py
.venv/bin/python -c "
import json; s=json.load(open('refrain-core/tests/fixtures/autopilot_alpha_theta.advice.json'))['steps']
print(sorted({x['advice']['reason'] for x in s}), sum(x['applied'] is not None for x in s))"
```

Expected: the reasons include `not_training_phase`, `collecting`, and at least one of `too_strict`/`too_easy`/`on_track`, and at least one auto-applied change. If no change is applied, adjust `reward_target` in the bench protocol (not the advisor) until one is, and note the final band in the commit message.

- [ ] **Step 3: Rust replay** (append to `advisor_parity.rs`)

```rust
#[test]
fn whole_session_matches_python() {
    use refrain_core::eval::Evaluator;
    let fx = fixture("autopilot_alpha_theta.advice.json");
    let p: Protocol = serde_json::from_value(fixture("autopilot_alpha_theta.ir.json")).unwrap();
    let sr = fx["sample_rate_hz"].as_f64().unwrap();
    let chunk = fx["chunk_size"].as_u64().unwrap() as usize;
    let input: Vec<Vec<f64>> = fx["input"].as_array().unwrap().iter()
        .map(|r| r.as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect())
        .collect();
    let mut ev = Evaluator::new(&p, sr, &["Cz".to_string()]);
    ev.start(false);
    for (i, (rows, step)) in input.chunks(chunk).zip(fx["steps"].as_array().unwrap()).enumerate() {
        ev.step_chunk_events(rows);
        let a = ev.advice();
        same(&a, &step["advice"], &format!("chunk{i}.advice"));
        let mut applied = Value::Null;
        if a["state"] == "adjust" && a["control"]["auto_allowed"] == true {
            applied = ev.apply_advice(a["id"].as_str().unwrap(), "autopilot").unwrap();
        }
        same(&applied, &step["applied"], &format!("chunk{i}.applied"));
        same(&Value::Array(ev.drain_advice_events()), &step["events"], &format!("chunk{i}.events"));
    }
}
```

Run: `cd refrain-core && ~/.cargo/bin/cargo test --test advisor_parity whole_session 2>&1 | tail -8`
Expected: PASS. **If it fails**, the message names the chunk and field. A mismatch in `evidence.checks` or `reward_rate` usually means a threshold comparison landed on an exact tie that the two DSP paths resolve differently (they agree to ~1e-13, not bit-for-bit). Confirm with a debug print of both engines' `last_taps()` at that chunk, and only then change the input's random seed. Report the seed change and the reason. Never loosen `same()`.

- [ ] **Step 4: Run the full gates**

```bash
PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py
```

Expected: exit 0 (fixtures regenerate, `cargo test`, wheel install, Rust-backend pytest, schema test).

- [ ] **Step 5: Commit**

```bash
git add bench/protocols/autopilot_alpha_theta.refrain refrain-core/tools/gen_fixtures.py refrain-core/tests/advisor_parity.rs refrain-core/tests/fixtures/autopilot_alpha_theta.*
git commit -m "test(core): whole-session advice parity from real DSP in both engines"
```

---
## Task 17: The alpha/theta worked example

**Files:**
- Create: `examples/alpha_theta_autopilot.refrain`
- Modify: `tests/test_parser_examples.py:17` (add to `EXAMPLES`)
- Test: `tests/test_example_alpha_theta_autopilot.py` (new)

**Interfaces:**
- Consumes: everything above.
- Produces: the canonical example the authoring guide (Task 18) walks through. The production protocol in `refrain-protocols` adopts it in a follow-up change after release (spec §7).

- [ ] **Step 1: Write the failing test**

Create `tests/test_example_alpha_theta_autopilot.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The worked alpha/theta autopilot example compiles in both modes, carries
the intended policy, and never lets a guard knob be auto-adjusted."""

from pathlib import Path

import numpy as np
import pytest

from refrain import parse, parse_file, resolve
from refrain.amp_profile import load_amp_profile
from refrain.compile_json import compile_to_ir_json
from refrain.eval_ import Evaluator

REPO = Path(__file__).resolve().parent.parent
PATH = REPO / "examples" / "alpha_theta_autopilot.refrain"
AMP = load_amp_profile(REPO / "src" / "refrain" / "amp_profiles" / "q21.json")


@pytest.mark.parametrize("style", ["adaptive", "baseline"])
def test_compiles_in_both_modes(style):
    res = compile_to_ir_json(PATH.read_text(), amp="q21", bindings={"threshold_style": style})
    assert not res.errors, [e.message for e in res.errors]
    assert res.ir_json["refrain_ir_version"] == "0.4"
    assert res.ir_json["reward"]["check_names"] == ["theta", "crossover"]


def test_policy_shape():
    ir = resolve(parse_file(PATH), AMP)
    c = {n: ctl.autopilot for n, ctl in ir.controls.items()}
    assert (c["crossover_target"].step, c["crossover_target"].apply) == (0.05, "auto")
    assert (c["theta_reward_pct"].step, c["theta_reward_pct"].apply) == (5.0, "suggest")
    assert c["theta_threshold_uv"] is None                  # baseline-only, adaptive is default
    for guard_knob in ("delta_inhibit_rate", "artifact_strictness"):
        assert c[guard_knob] is None
    ap = ir.autopilot
    assert ap.reward_target == (0.10, 0.35) and ap.phases == ("deep1", "deep2")
    assert {g.inhibit for g in ap.guards} == {"delta", "emg"}


def test_auto_on_a_guard_knob_is_refused():
    src = PATH.read_text().replace(
        'label        = "Artifact guard strictness"',
        'label        = "Artifact guard strictness"\n      autopilot = fixed_step { fixes = "theta"; '
        'step = 1; higher_is = "easier"; apply = "auto" }')
    res = compile_to_ir_json(src, amp="q21")
    assert res.errors  # does not feed theta (V2) or feeds a guard (V4): refused either way


def test_runs_and_advises():
    ir = resolve(parse_file(PATH), AMP)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Pz", "A1", "A2"),
                        backend="python")
    ev.start(skip_warmup=False)
    rng = np.random.default_rng(0)
    for _ in range(125):                                     # 2 min settle + 5 s
        ev.step_chunk(rng.normal(0, 10, size=(256, 3)))
    a = ev.advice()
    assert a["reason"] in ("collecting", "guard")
    assert ev.autopilot_policy()["provenance"]["evidence"] == "expert_opinion"
```

Run: `.venv/bin/python -m pytest tests/test_example_alpha_theta_autopilot.py -q -p no:cacheprovider`
Expected: FAIL (file missing).

- [ ] **Step 2: Create the example**

Copy the production template verbatim:

```bash
cp /Users/jcroall/git/refrain-protocols/protocols/eeg/alpha_theta.refrain examples/alpha_theta_autopilot.refrain
```

(refrain-protocols commit `5eb4886`, protocol `alpha_theta` v1.3.0. If that checkout isn't available, fetch the file at that commit from the `refrain-protocols` repository.)

Then make exactly these edits:

1. At the very top, replace the first comment line with:

```refrain
// Alpha/theta crossover WITH AUTOPILOT — worked example for
// docs/AUTOPILOT-AUTHORING.md. Identical to refrain-protocols'
// protocols/eeg/alpha_theta.refrain v1.3.0 except: the two reward checks are
// named, and an `autopilot { }` block plus three control policies are added.
//
// The 10-35% reward target was carried over from the recorder's guidance
// engine, which measured the one-second HELD reward. The advisor measures the
// instantaneous checks, so re-confirm the band on recorded sessions before
// clinical use (spec §10.1).
```

2. `protocol "alpha_theta" {` → `protocol "alpha_theta_autopilot" {` and `version = "1.3.0"` → `version = "1.4.0"`.

3. In `reward`, name the checks:

```refrain
    event = dwell(
      condition: all_of([
        above("theta_envelope", "theta_t")           as "theta",
        above("theta_alpha_ratio", crossover_target) as "crossover",
      ]),
      duration: 1000 ms
    )
```

4. Add policies inside the existing controls (keep every existing field):
   - in `theta_reward_pct = percent { ... }`:
     `autopilot = fixed_step { fixes = "theta"; step = 5; higher_is = "harder"; limits = (15, 40); apply = "suggest"; only_when = threshold_style == "adaptive"; say = "Theta reward rate" }`
   - in `crossover_target = number { ... }`:
     `autopilot = fixed_step { fixes = "crossover"; step = 0.05; higher_is = "harder"; limits = (0.5, 1.0); apply = "auto"; round_to = 0.01; say = "Crossover target" }`
   - in `theta_threshold_uv = voltage { ... }`:
     `autopilot = proportional_step { fixes = "theta"; step = 10%; higher_is = "harder"; apply = "suggest"; round_to = 0.1 uV; only_when = threshold_style == "baseline"; say = "Theta threshold" }`
   - leave `delta_inhibit_rate`, `artifact_strictness`, band edges and `site` without a policy (manual only).

5. Immediately before `session {`, add:

```refrain
  // Autopilot (docs/AUTOPILOT-AUTHORING.md). Shapes toward strict crossover in
  // small steps; guards are never loosened automatically (the compiler refuses
  // `apply = "auto"` on any knob that feeds an inhibit).
  autopilot {
    evidence      = "expert_opinion"
    citation      = [
      "Peniston & Kulkosky 1989, 1991 (protocol)",
      "Peak Mind clinical team 2026: step sizes and target band adapted from Coherence Recorder guidance v1",
    ]
    rationale     = "Crossover is rare by nature; a 50-75% target would drive the ratio target to its floor. Tighten crossover first, one 0.05 step at a time."
    reward_target = (10%, 35%)
    phases        = ["deep1", "deep2"]
    watch         = 2 min
    between_moves = 3 min
    tighten_first = ["crossover", "theta"]
    delta = guard { max = 15%; say = "Client may be drifting toward sleep; check alertness." }
    emg   = guard { max = 15%; say = "Muscle artifact; check jaw/neck tension or the electrode." }
  }
```

Add `"alpha_theta_autopilot.refrain",` to `EXAMPLES` in `tests/test_parser_examples.py`.

- [ ] **Step 3: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_example_alpha_theta_autopilot.py tests/test_parser_examples.py tests/test_ir_json.py -q -p no:cacheprovider`
Expected: PASS. If `test_runs_and_advises` fails because the q21 profile names the reference channels differently, use the channel names from `load_amp_profile(...)` (inspect `AMP`) rather than hard-coding them.

- [ ] **Step 4: Commit**

```bash
git add examples/alpha_theta_autopilot.refrain tests/test_example_alpha_theta_autopilot.py tests/test_parser_examples.py
git commit -m "docs(example): alpha/theta with a full autopilot policy"
```

---

## Task 18: Documentation

**Files:**
- Create: `docs/AUTOPILOT-AUTHORING.md`, `docs/autopilot-feature-request-response.md`
- Modify: `docs/SPEC.md` (new §4.12 after §4.11; new §4.9.5 after §4.9.4; new §7.10 after §7.9; §4.7 gains named checks), `docs/EMBEDDING.md` (new section after "seed reports"), `docs/IR-JSON.md` (new §3.2 after "Control `seed` (v0.3)")

Docs are reviewed for accuracy against the code, not just presence. Every example below must compile. Paste each `refrain` block into a scratch test and run `compile_to_ir_json`.

- [ ] **Step 1: `docs/AUTOPILOT-AUTHORING.md`**

Write it with these sections and content:

1. **What autopilot is.** Refrain watches the session and says, every chunk, whether to hold or nudge one setting, with the numbers behind it. The recorder shows it and applies it when the clinician has turned autopilot on *and* the protocol allows it. Three levels (the table from spec §3.1).
2. **Before you write a policy: does this protocol need one?** A single adaptive check self-adjusts (`percentile(target_pct: k)` passes ~100−k% of the time by construction), so autopilot has nothing to observe. Worth it: several checks, baseline (µV) thresholds, plain targets like `crossover_target`. Guard knobs (anything feeding an inhibit) can be suggested but never auto-applied.
3. **Step 1 — name the reward checks.** `above(...) as "crossover"`. Single check: `all_of([cond as "x"])`. Names must be unique across the protocol.
4. **Step 2 — the `autopilot { }` block.** The settings table from spec §3.3 plus the defaults table from §3.5. Explain `tighten_first` in one sentence: loosening follows the evidence; tightening is your clinical choice.
5. **Step 3 — a policy per knob.** The three strategies with when to use each (fixed for fixed scales; proportional for µV thresholds that differ by client; rebaseline for "today's threshold is clearly wrong", always suggest-only). `higher_is`, `limits` (may be narrower than `range`), `round_to`, `only_when` (mode-only, evaluated at compile time).
6. **What the compiler refuses and why.** Rules V1–V17 as a plain-language table ("You wrote… / Why it's refused / Fix"), including the two most common: forgetting `only_when` on a mode-dependent knob, and `apply = "auto"` on a guard knob.
7. **Provenance and citations.**
   - `evidence` scale with what qualifies:
     - `published`: the numbers come from a peer-reviewed source you cite.
     - `clinical_consensus`: a written team protocol or guideline.
     - `expert_opinion`: an experienced clinician's judgement.
     - `experimental`: untested; hosts should label it.
   - `citation`: one string or a list. Cite the protocol's origin *and* the source of the autopilot numbers separately.
     - Paper: `"Author A, Author B (Year). Title. Journal, vol(issue), pages. doi:…"`
     - Team consensus: `"<Team> clinical protocol <id>, <date>"`
     - Adapted numbers: `"Adapted from <source>; re-confirm on recorded sessions"`
   - `rationale`: why these numbers, in one or two sentences.
   - `reviewed = "Name, YYYY-MM-DD"` when someone other than the author checked it.
   - A per-knob `citation` when one knob's step comes from a different source.
   - Provenance is inside the protocol hash, so changing a citation is a protocol change: bump `meta.version`.
8. **Worked example.** Walk through `examples/alpha_theta_autopilot.refrain`: each line of the block and each policy, plus two sample advice results. Generate them with `test_advisor.py`'s scenarios so the text matches real output.
9. **Existing protocols.** No change needed. They compile identically (same hash), stay manual, and get observations and hints. Opting in step by step: names → `reward_target` → policies.

- [ ] **Step 2: `docs/SPEC.md`**

- §4.7: add a paragraph and example for named checks (`as "<name>"`, only in dwell lists, unique, recorded as `check_names`).
- §4.9.5 `autopilot` control field: the strategy table and common-fields table from spec §3.4, with the Deviations applied (no per-knob `watch`; kinds number/percent/voltage/frequency).
- §4.12 `autopilot`: settings table, entries (`guard`, `limiter`), defaults, composition (replace/amend; no `final`).
- §6.7 (new, under Static validation): the V1–V17 list in one table.
- §7.10 Autopilot advisor: evidence window (§4.3 of the design spec), the decision table (§4.4), check selection (§4.5), hints (§4.6), lifecycle (§4.7), determinism (sample time, `r6`, counter ids), and "advice is not a tap".

- [ ] **Step 3: `docs/EMBEDDING.md`**

New section "Autopilot advice": the six calls, a recorder-shaped loop, the `advice()` JSON with every field explained, the reason codes, the audit events, and who does what (Refrain never changes a knob; the host decides to auto-apply; `apply_advice(by="autopilot")` refuses what the protocol forbids). Recorder-shaped loop:

```python
for chunk in amp_chunks():
    events = ev.step_chunk(chunk)
    a = ev.advice()
    ui.show_advice(a)                              # every chunk: collecting / hold / hint / adjust
    if a["state"] == "adjust" and session.autopilot_on and a["control"]["auto_allowed"]:
        ev.apply_advice(a["id"], by="autopilot")
    audit.extend(ev.drain_advice_events())         # persist with the session record
```

- [ ] **Step 4: `docs/IR-JSON.md`**

§3.2 "Autopilot (v0.4)": the three new keys with their exact shapes (Task 7 Interfaces), baking rules (`*_samples`), `decimals`, the omit-when-unused rule, and the version gate.

- [ ] **Step 5: `docs/autopilot-feature-request-response.md`**

Addressed to the recorder team, in the style of `docs/baseline-seeding-feature-request-response.md`:
- what shipped and in which version
- what replaces `recorder/backend/nf/guidance.py` and nf-coach's `findCoachingControl` / `deriveControlMode` / `assessEfficacy` verdict (a mapping table)
- the host API
- the per-session switch and audit trail the recorder owns
- the one thing to re-confirm (the alpha/theta band)
- the follow-up in `refrain-protocols`

- [ ] **Step 6: Verify the doc examples compile**

```bash
.venv/bin/python - <<'EOF'
import re, pathlib
from refrain.compile_json import compile_to_ir_json
for doc in ["docs/AUTOPILOT-AUTHORING.md", "docs/SPEC.md"]:
    text = pathlib.Path(doc).read_text()
    for block in re.findall(r"```refrain\n(.*?)```", text, re.S):
        if block.lstrip().startswith("protocol"):
            res = compile_to_ir_json(block, amp="q21")
            print(doc, "OK" if not res.errors else [e.message for e in res.errors])
EOF
```

Expected: every full-protocol block prints `OK`. (Fragments that are not whole protocols are skipped.)

- [ ] **Step 7: Commit**

```bash
git add docs/AUTOPILOT-AUTHORING.md docs/autopilot-feature-request-response.md docs/SPEC.md docs/EMBEDDING.md docs/IR-JSON.md
git commit -m "docs: autopilot authoring guide, SPEC/EMBEDDING/IR-JSON sections, recorder response"
```

---

## Task 19: Release v0.22.0

**Files:**
- Modify: `pyproject.toml` (`version = "0.22.0"`), `refrain-core/pyproject.toml` (`version = "0.22.0"`), `CHANGELOG.md`, `refrain-core/CHANGELOG.md`

- [ ] **Step 1: Full verification before the bump**

```bash
.venv/bin/python -m pytest -p no:cacheprovider -q -W ignore 2>&1 | tail -2
PYTHONPATH="$PWD" .venv/bin/python refrain-core/tools/check_equivalence.py; echo "equivalence exit=$?"
.venv/bin/ruff check src/refrain --select F,E9
```

Expected: pytest reports no failures, with more passed than the 902 baseline and the Rust-backend tests no longer skipped now the wheel is installed. `equivalence exit=0`. Ruff clean. Paste the actual summary lines into the release commit body. Do not paraphrase them.

- [ ] **Step 2: Bump and changelog**

Set both `pyproject.toml` versions to `0.22.0`. Add to `CHANGELOG.md`, above `## [0.21.0]`:

```markdown
## [0.22.0] — <release date>

### Added
- **Protocol-declared autopilot.** A protocol can name its reward checks
  (`above(...) as "crossover"`), declare an `autopilot { }` block (target
  reward band, phases, evidence window, cadence, guard ceilings, provenance)
  and give each tunable control a policy (`fixed_step`, `proportional_step`,
  `rebaseline`; direction, limits, auto vs suggest). The compiler refuses
  unsafe or ambiguous policies (a knob that cannot affect the check it claims
  to fix, a reversed direction, auto-adjusting a guard). Both engines run the
  same deterministic advisor and emit one structured advice result per chunk
  (`Evaluator.advice()`), with `apply_advice`, `dismiss_advice`,
  `mark_equipment_change`, `drain_advice_events` and `autopilot_policy`.
  Protocols without a policy stay manual and receive observations and
  direction hints. IR-JSON **0.4** (emitted only when the new keys are used —
  every existing protocol keeps its IR-JSON and hash). Python<->Rust parity is
  gated over tracer output, scripted scenarios and a whole session. See
  docs/AUTOPILOT-AUTHORING.md.
```

Add a matching entry to `refrain-core/CHANGELOG.md` covering: `advisor.rs`, the new `Evaluator` accessors, the PyO3/uniffi methods, the new `RefrainError::Advice` variant (**a uniffi enum change, so mobile consumers regenerate bindings**), and IR-JSON 0.4 support.

- [ ] **Step 3: Commit**

```bash
.venv/bin/python -m pytest tests/test_version_lockstep.py -q -p no:cacheprovider
git add pyproject.toml refrain-core/pyproject.toml CHANGELOG.md refrain-core/CHANGELOG.md
git commit -m "release: v0.22.0 — protocol-declared autopilot"
```

Tag only after the release PR merges (Global Constraints).

---

## Self-Review

**Spec coverage** (design spec section → task):

| Spec | Task |
|---|---|
| §3.1 three levels | 9 (observations, hints, policy), 10 |
| §3.2 named checks | 1, 3, 7 |
| §3.3 `autopilot` block | 1, 4, 7 |
| §3.4 per-control policy + strategies | 5, 7, 9 |
| §3.5 defaults | 9 (`DEFAULT_*`), 12 |
| §3.6 no-reward / weighted protocols | 8 (V16), 9 (`observing`) |
| §3.7 V1–V17 | 3 (V15), 4 (V12–V14), 5 (V6, V8–V11, V17), 8 (V1–V4, V7, V16); V5 ⊂ V2 |
| §3.8 composition | 4 (replace + amend test) |
| §4.1–4.7 decision procedure + lifecycle | 9 (Python), 12 (Rust), 13 + 16 (parity) |
| §5.1 host API | 10, 14, 15 |
| §5.2 advice shape | 9 (every key), 15 (JSON across FFI) |
| §6 IR / IR-JSON / versioning / hash | 2, 7, 11 |
| §7 migration | 7 (no change to existing examples), 17 (example), 18 (response note) |
| §8 testing | every task; parity 13, 16 |
| §9 documentation | 18 |
| §10.1 alpha/theta band | 17 (header note), 18 (response note) |

**Placeholder scan:** none. The two places that tell the engineer to look something up are the exact attribute holding the composed AST (Task 8) and the fixture-directory constant name in `gen_fixtures.py` (Tasks 13, 16). Each names the grep that finds it.

**Type consistency:**
- `Advisor.apply` returns `(control, value, event)` in Python and `Result<(String, f64, Value), String>` in Rust.
- `note_control(control, value, source)` has the same signature in both.
- `ChunkFacts` fields match in both.
- `check_names` is a `tuple[str | None]` in Python IR, `list[str|null]` on the wire, and `Vec<Option<String>>` in Rust.
- Durations are ms in IR and `*_samples` on the wire.
- `reward_target` and guard `max` are fractions everywhere after resolve.

**Review Focus:** each of the five lines has a named test in its owning task (Tasks 9, 9, 9, 7, 15).
