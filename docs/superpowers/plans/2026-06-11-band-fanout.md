# Band Fan-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a protocol author write a per-band signal subgraph **once** and have it replicated per frequency band declared in a new `bands { … }` block — and compose with the existing per-site fan-out to produce the band × channel cross product (e.g. 10 bands × {C3, C4} = 20 envelopes).

**Architecture:** A new `band_fan_out` pre-pass in `src/refrain/fanout.py`, structurally parallel to the existing per-site `fan_out`, on a second axis: the **seed** is the set of derives whose `bandpass(band: bands)` references the `bands` placeholder; the **substitution** injects each band's frequency `Tuple` (instead of a channel `StringLit`). It reuses the existing axis-agnostic helpers (`_transitive_per_site`, `_rename_refs_expr`, `_index_decls`). Both passes are extended to replicate `inhibit` decls. `resolve()` runs compose → band_fan_out → (site) fan_out → resolver, so band copies (`@theta`) then get per-site-replicated (`@theta@C3`).

**Tech Stack:** Python 3.10+, the Lark grammar (`src/refrain/grammar.lark`), the AST (`src/refrain/ast.py`), pytest. **Front-end only — no Rust-core or IR-JSON change** (fan-out emits a flat AST of existing node types, exactly like per-site Mode 2a).

**This is plan 2 of 3** from `docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md`. It depends on nothing in plan 1 (`autocorr`); plan 3 (the `critical_fluctuation_cue` protocol) depends on both.

---

## Surface design (what the author writes)

```refrain
bands {
  theta = (4 Hz, 8 Hz)
  alpha = (8 Hz, 12 Hz)
  smr   = (12 Hz, 15 Hz)
}

// `bands` (bare NameRef) is the band-axis placeholder, exactly as a `set`
// control name is the site-axis placeholder in a montage slot.
derive "env"   { from = "raw"; pipeline = [ bandpass(band: bands), hilbert(), magnitude() ] }
derive "score" { from = "env"; pipeline = [ auto_range(window: 30 s) ] }
inhibit "critical_fluctuation" { metric = "score"; threshold = percentile(target_pct: 90, window: 2 min); action = mute(release: 400 ms) }
```

After band fan-out (3 bands): `env@theta`, `env@alpha`, `env@smr`, likewise `score@*` and `critical_fluctuation@*`, each with its band's concrete tuple baked into `bandpass(band: (…))` and refs renamed. `raw` is shared (upstream of the seed, not in the forward closure), so it is **not** replicated. If `raw` is itself bound to a `set` placement of channels, the subsequent per-site `fan_out` replicates every `@band` copy per channel → the cross product.

---

## Reference: existing machinery being reused (do NOT reimplement)

All in `src/refrain/fanout.py` on `main`:

- `_index_decls(proto)` — `{(keyword, name): NamedDecl}`.
- `_transitive_per_site(seed_set, refs)` (lines 329–346) — forward fixpoint closure; **axis-agnostic**, reuse verbatim.
- `_referenced_entities(decl, entity_names)` — the per-decl ref set (builds the `refs` graph).
- `_rename_refs_expr(node, names, suffix)` (lines 406–461) — renames `StringLit` refs to `<name>@<suffix>`; recurses through Call/Array/Tuple/BinaryOp/Conditional/MemberAccess/BlockExpr. **Axis-agnostic**, reuse verbatim (suffix is just a string).
- `_suffix(name, s)` (lines 402–403) → `f"{name}@{s}"`. Reuse — chains naturally: `_suffix("env@theta", "C3") == "env@theta@C3"`.
- `_replicate_dependent(decl, names, suffix)` (lines 513–522) — clones a derive/threshold and renames its refs. **Reuse for derives/thresholds AND inhibits** (an inhibit body is just assignments with ref-bearing `StringLit`s — `metric = "score"`).
- `_check_scoping(...)` (lines 361–394) — verified compatible: our seed derive (`env`) references only the shared `raw` (not in the band closure), so it is not flagged; `continuous = 1.0` has no per-axis refs.
- The site pass `fan_out(file_ast, bindings, *, amp)` (lines 39–101) and `_PER_SITE_KEYWORDS = ("input", "derive", "threshold")`.
- AST nodes (`src/refrain/ast.py`): `SectionBlock(keyword, body)`, `NamedDecl(keyword, name, body)`, `Assignment(target, value)`, `Call(callee, args)`, `Arg(name, value)`, `NameRef(name)`, `Tuple(elements)`, `NumberLit(value, unit)`, `StringLit(value)`, `Array`.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `src/refrain/grammar.lark` | add `"bands"` to `SECTION_KW` (line 52) | Modify |
| `src/refrain/fanout.py` | `_bands_table`, `band_fan_out`, `_bind_band_nameref`, `_band_seed_derives`; add `inhibit` to replicated keywords | Modify |
| `src/refrain/resolver.py` | call `band_fan_out` before `fan_out` (~line 2255); validate the `bands` block (`_resolve_bands`) | Modify |
| `tests/test_fanout.py` | band fan-out + cross-product tests | Modify |
| `docs/SPEC.md` | document the `bands` block + band-axis fan-out | Modify |

---

### Task 1: `bands { }` block parses

**Files:**
- Modify: `src/refrain/grammar.lark:52`
- Test: `tests/test_parser_primitives.py` (or the section-block test file — grep for where `groups` parsing is tested)

- [ ] **Step 1: Write the failing parse test**

In the parser test file (mirror the `groups`-block test — grep `test` for `keyword == "groups"` / `SectionBlock`):

```python
def test_bands_block_parses_as_section_block():
    src = '''
    protocol "p" {
      meta { version="0.1.0" evidence="demo" description="x" }
      requires { sample_rate=">= 256 Hz" channels=["Cz"] }
      bands { theta = (4 Hz, 8 Hz); alpha = (8 Hz, 12 Hz) }
      input "raw" { montage = referential(active:"Cz", reference:"device") }
      output { audio_gain = 0 }
    }'''
    proto = parse(src).protocol
    blk = next(s for s in proto.body if isinstance(s, A.SectionBlock) and s.keyword == "bands")
    entries = {s.target: s.value for s in blk.body if isinstance(s, A.Assignment)}
    assert set(entries) == {"theta", "alpha"}
    assert isinstance(entries["theta"], A.Tuple)
    assert entries["theta"].elements == (A.NumberLit(4.0, "Hz"), A.NumberLit(8.0, "Hz"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/flutter-cue && python -m pytest tests/ -k bands_block_parses -q`
Expected: FAIL — Lark parse error (`bands` not a recognized `SECTION_KW`, parsed as something else / unexpected token).

- [ ] **Step 3: Add `"bands"` to the grammar**

In `src/refrain/grammar.lark`, line 52, change:

```lark
SECTION_KW: "meta" | "requires" | "reward" | "output" | "controls" | "session" | "groups"
```
to:
```lark
SECTION_KW: "meta" | "requires" | "reward" | "output" | "controls" | "session" | "groups" | "bands"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/ -k bands_block_parses -q`
Expected: PASS. (Generic section-block parsing already yields `SectionBlock(keyword="bands", body=(Assignment…))`.)

- [ ] **Step 5: Commit**

```bash
git add src/refrain/grammar.lark tests/
git commit -m "feat(parser): recognize bands { } section block"
```

---

### Task 2: `band_fan_out` pre-pass — the band axis

**Files:**
- Modify: `src/refrain/fanout.py`
- Test: `tests/test_fanout.py`

- [ ] **Step 1: Write the failing fan-out test**

In `tests/test_fanout.py` (mirror the `_REPL` per-site tests at lines 18–77). Single-channel here; cross product is Task 4.

```python
_BANDS = """
    protocol "bandfan" {
      meta { version="1.0"; evidence="clinical"; description="x" }
      requires { sample_rate=">= 256 Hz"; channels=["Cz"] }
      bands { theta = (4 Hz, 8 Hz); alpha = (8 Hz, 12 Hz); smr = (12 Hz, 15 Hz) }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env"   { from = "raw"; pipeline = [bandpass(band: bands), hilbert(), magnitude()] }
      derive "score" { from = "env"; pipeline = [auto_range(window: 30 s)] }
      inhibit "critical_fluctuation" { metric = "score"; threshold = percentile(target_pct: 90, window: 2 min); action = mute(release: 400 ms) }
      reward { continuous = 1.0 }
      output { audio_gain = reward.continuous }
    }
"""

def test_band_fan_out_replicates_per_band():
    ir = resolve(parse(_BANDS), _AMP)
    assert set(ir.derives) == {"env@theta","env@alpha","env@smr","score@theta","score@alpha","score@smr"}
    assert set(ir.inhibits) == {"critical_fluctuation@theta","critical_fluctuation@alpha","critical_fluctuation@smr"}
    # `raw` is shared (upstream of the seed), NOT replicated.
    assert set(ir.inputs) == {"raw"}

def test_band_fan_out_substitutes_band_tuple():
    ir = resolve(parse(_BANDS), _AMP)
    # env@theta's bandpass got the concrete (4,8) tuple; env@alpha got (8,12).
    assert _bandpass_band_of(ir, "env@theta") == (4.0, 8.0)
    assert _bandpass_band_of(ir, "env@alpha") == (8.0, 12.0)

def test_band_fan_out_per_band_refs():
    ir = resolve(parse(_BANDS), _AMP)
    assert ir.derives["score@theta"].upstream == ("derive/env@theta",)
    assert ir.inhibits["critical_fluctuation@theta"].metric_ref == "derive/score@theta"  # match real IR field name
```

> Add helper `_bandpass_band_of(ir, derive_name)` near `_active_of` in the test file: walk the derive's IR expr to the `bandpass` call and return its `band` arg as a `(low, high)` float tuple. Use the real `ir.inhibits` accessor + inhibit metric field name (grep `ir.py` for the inhibit IR dataclass — adjust `metric_ref` to the actual attribute).

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_fanout.py -k band_fan_out -q`
Expected: FAIL — bands not replicated (`env@theta` absent); `bandpass(band: bands)` left as a dangling `NameRef` → resolve error or missing derives.

- [ ] **Step 3: Add `_bands_table` + `_band_seed_derives` + `_bind_band_nameref`**

In `src/refrain/fanout.py`, add (mirror `_groups_table` lines 144–162 and `_bind_set_nameref` lines 497–510):

```python
_BAND_PLACEHOLDER = "bands"  # the bare NameRef authors use in `bandpass(band: bands)`

def _bands_table(proto: A.Protocol) -> dict[str, A.Tuple]:
    """name -> frequency 2-tuple, from the protocol's `bands` block. Lenient
    read for the fan-out pre-pass; the resolver's _resolve_bands does the
    authoritative validation (shape, ordering, name collisions)."""
    for stmt in proto.body:
        if isinstance(stmt, A.SectionBlock) and stmt.keyword == "bands":
            out: dict[str, A.Tuple] = {}
            for inner in stmt.body:
                if isinstance(inner, A.Assignment) and isinstance(inner.value, A.Tuple):
                    out[inner.target] = inner.value
            return out
    return {}

def _band_seed_derives(decls: dict[tuple[str, str], A.NamedDecl]) -> set[str]:
    """Derives whose pipeline/formula contains `bandpass(band: bands)` —
    the band-axis replication seeds."""
    seeds: set[str] = set()
    for (kw, name), decl in decls.items():
        if kw == "derive" and _references_band_placeholder(decl):
            seeds.add(name)
    return seeds

def _references_band_placeholder(node) -> bool:
    """True if `node` (decl or expr) contains a bandpass call with
    `band: NameRef(_BAND_PLACEHOLDER)`."""
    if isinstance(node, A.Call):
        if node.callee == "bandpass":
            for a in node.args:
                if a.name in (None, "band") and isinstance(a.value, A.NameRef) and a.value.name == _BAND_PLACEHOLDER:
                    return True
        return any(_references_band_placeholder(a.value) for a in node.args)
    if isinstance(node, A.NamedDecl):
        return any(_references_band_placeholder(s) for s in node.body)
    if isinstance(node, A.Assignment):
        return _references_band_placeholder(node.value)
    if isinstance(node, A.Array):
        return any(_references_band_placeholder(e) for e in node.elements)
    return False

def _bind_band_nameref(node: A.Expr, band_tuple: A.Tuple) -> A.Expr:
    """Rewrite `bandpass(band: NameRef(_BAND_PLACEHOLDER))` -> `bandpass(band: <tuple>)`."""
    if isinstance(node, A.Call):
        return A.Call(
            callee=node.callee,
            args=tuple(
                A.Arg(
                    name=a.name,
                    value=(band_tuple
                           if isinstance(a.value, A.NameRef) and a.value.name == _BAND_PLACEHOLDER
                           else _bind_band_nameref(a.value, band_tuple)),
                    loc=a.loc,
                )
                for a in node.args
            ),
            loc=node.loc,
        )
    if isinstance(node, A.Array):
        return A.Array(elements=tuple(_bind_band_nameref(e, band_tuple) for e in node.elements), loc=node.loc)
    return node
```

- [ ] **Step 4: Add the `band_fan_out` entrypoint**

In `src/refrain/fanout.py`, add (mirror `fan_out` lines 39–101; reuse `_index_decls`, `_referenced_entities`, `_transitive_per_site`):

```python
# Band fan-out replicates derive/threshold/inhibit (NOT input — the channel is
# the site axis). Distinct from _PER_SITE_KEYWORDS, which also replicates input.
_PER_BAND_KEYWORDS = ("derive", "threshold", "inhibit")

def band_fan_out(file_ast: A.File) -> A.File:
    """Replicate the band-parameterized subgraph once per `bands` entry.

    No-op when there is no `bands` block. The seed is every derive whose
    `bandpass(band: bands)` references the `bands` placeholder; the forward
    closure (derive/threshold/inhibit) is replicated per band with the band's
    concrete frequency tuple substituted and refs renamed `<name>@<band>`.
    Front-end only — emits a flat AST of existing node types.
    """
    proto = file_ast.protocol
    bands = _bands_table(proto)
    if not bands:
        return file_ast

    decls = _index_decls(proto)
    seeds = _band_seed_derives(decls)
    if not seeds:
        return file_ast  # bands declared but never referenced; resolver flags it

    entity_names = {name for (kw, name) in decls.keys() if kw in _PER_BAND_KEYWORDS}
    refs = {
        name: _referenced_entities(decl, entity_names) - {name}
        for (kw, name), decl in decls.items()
        if kw in _PER_BAND_KEYWORDS
    }
    per_band = _transitive_per_site(seeds, refs)  # forward closure; seed is a set

    return _rewrite_bands(file_ast, proto, bands, decls, per_band, seeds)
```

> **Note on `_transitive_per_site` seed type:** it currently takes a single seed name (`per_site = {seed}`). Generalize its first param to accept either a `str` or a set — change the body's first line to `per_site = set(seed) if not isinstance(seed, str) else {seed}`. This keeps the per-site caller working (passes a str) and lets the band caller pass the seed set. Make this change in Task 2 Step 4.

- [ ] **Step 5: Add `_rewrite_bands`**

In `src/refrain/fanout.py`, add (mirror `_rewrite` lines 597–659, but: substitute band tuples via `_bind_band_nameref`; replicate inhibits too; do NOT rewrite reward — band axis has no dwell-combine in v1):

```python
def _rewrite_bands(file_ast, proto, bands, decls, per_band, seeds):
    ordered = [
        s for s in proto.body
        if isinstance(s, A.NamedDecl) and s.keyword in _PER_BAND_KEYWORDS and s.name in per_band
    ]
    new_body = [
        s for s in proto.body
        if not (isinstance(s, A.NamedDecl) and s.keyword in _PER_BAND_KEYWORDS and s.name in per_band)
    ]
    replicas: list[A.Statement] = []
    for band_name, band_tuple in bands.items():
        for decl in ordered:
            renamed = _replicate_dependent(decl, per_band, band_name)  # renames refs + suffix
            if decl.name in seeds:
                # also substitute the concrete band tuple into bandpass(band: bands)
                renamed = A.NamedDecl(
                    keyword=renamed.keyword,
                    name=renamed.name,
                    body=tuple(
                        A.Assignment(target=s.target, value=_bind_band_nameref(s.value, band_tuple), loc=s.loc)
                        if isinstance(s, A.Assignment) else s
                        for s in renamed.body
                    ),
                    loc=renamed.loc,
                )
            replicas.append(renamed)

    insert_at = _first_per_site_index(new_body, proto, per_band)  # reuse existing helper
    final = tuple(new_body[:insert_at] + replicas + new_body[insert_at:])
    return A.File(imports=file_ast.imports,
                  protocol=A.Protocol(name=proto.name, extends=proto.extends, body=final, loc=proto.loc),
                  loc=file_ast.loc)
```

> `_replicate_dependent` renames refs but does NOT substitute the band tuple — that is why the seed gets the extra `_bind_band_nameref` pass. Verify `_first_per_site_index` accepts the `(new_body, proto, name_set)` signature it has on `main`; if its name differs, grep `fanout.py` for the ins-position helper and use it.

- [ ] **Step 6: Extend the site `fan_out` to replicate inhibits**

In `src/refrain/fanout.py`, change:
```python
_PER_SITE_KEYWORDS = ("input", "derive", "threshold")
```
to:
```python
_PER_SITE_KEYWORDS = ("input", "derive", "threshold", "inhibit")
```
This lets the per-site pass replicate the per-band inhibits in the cross product (Task 4). Existing per-site tests have no inhibit in the replicated subgraph, so behavior is unchanged for them (verified in Task 5).

- [ ] **Step 7: Wire `band_fan_out` into `resolve()`**

In `src/refrain/resolver.py`, in `resolve()` (~line 2255, where `fan_out` is called after `compose`), add the band pass **before** the site pass:

```python
    from .fanout import band_fan_out, fan_out

    composed = band_fan_out(composed)
    composed = fan_out(composed, bindings or {}, amp=amp)
```

- [ ] **Step 8: Run the band fan-out tests**

Run: `python -m pytest tests/test_fanout.py -k band_fan_out -q`
Expected: PASS (3 tests).

- [ ] **Step 9: Commit**

```bash
git add src/refrain/fanout.py src/refrain/resolver.py tests/test_fanout.py
git commit -m "feat(fanout): band axis fan-out (replicate per-band subgraph incl. inhibits)"
```

---

### Task 3: validate the `bands` block in the resolver

**Files:**
- Modify: `src/refrain/resolver.py` (add `_resolve_bands`, mirror `_resolve_groups` lines 836–872; call it in the resolve sequence; capture `self.bands_ast` where `groups_ast` is captured ~lines 203–217)
- Test: `tests/test_fanout.py`

- [ ] **Step 1: Write the failing validation tests**

```python
def test_bands_block_rejects_non_tuple():
    src = _BANDS.replace("theta = (4 Hz, 8 Hz)", 'theta = ["a","b"]')
    with pytest.raises(ResolveError, match="band .*theta.* must be a frequency 2-tuple"):
        resolve(parse(src), _AMP)

def test_bands_block_rejects_inverted_range():
    src = _BANDS.replace("theta = (4 Hz, 8 Hz)", "theta = (8 Hz, 4 Hz)")
    with pytest.raises(ResolveError, match="low .*<.* high"):
        resolve(parse(src), _AMP)

def test_bands_block_rejects_band_name_control_collision():
    # if a control named `theta` exists, the band name collides
    src = _BANDS.replace('controls', 'controls')  # construct a collision case per the controls surface
    # (skip if the protocol has no controls; otherwise assert ResolveError match="collides")
```

> Keep the collision test only if you can construct a control named the same as a band cleanly; otherwise drop it (the `_resolve_groups` collision check is the template if you keep it).

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_fanout.py -k "bands_block_rejects" -q`
Expected: FAIL — no validation yet (resolve succeeds or raises a different/unclear error).

- [ ] **Step 3: Add `_resolve_bands`**

In `src/refrain/resolver.py`, capture the block where `groups_ast` is captured (~lines 203–217): `self.bands_ast = self._section_block(proto, "bands")` (use the same helper `_resolve_groups` relies on). Then add, mirroring `_resolve_groups` (lines 836–872):

```python
def _resolve_bands(self) -> None:
    if self.bands_ast is None:
        return
    control_names = {
        s.target for s in (self.controls_ast.body if self.controls_ast else [])
        if isinstance(s, A.Assignment)
    }
    for stmt in self.bands_ast.body:
        if not isinstance(stmt, A.Assignment):
            raise ResolveError("bands block may only contain `name = (low Hz, high Hz)` entries",
                               loc=getattr(stmt, "loc", None))
        name = stmt.target
        if name in control_names:
            raise ResolveError(f"band {name!r} collides with a control of the same name", loc=stmt.loc)
        v = stmt.value
        if not (isinstance(v, A.Tuple) and len(v.elements) == 2
                and all(isinstance(e, A.NumberLit) and e.unit == "Hz" for e in v.elements)):
            raise ResolveError(f"band {name!r} must be a frequency 2-tuple like (4 Hz, 8 Hz)", loc=v.loc)
        lo, hi = v.elements[0].value, v.elements[1].value
        if not (lo < hi):
            raise ResolveError(f"band {name!r}: low ({lo}) must be < high ({hi})", loc=v.loc)
        self.bands[name] = (lo, hi)
```

Initialize `self.bands: dict[str, tuple[float, float]] = {}` beside `self.groups`, and call `self._resolve_bands()` next to `self._resolve_groups()` in the resolve sequence.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_fanout.py -k "bands_block_rejects" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/resolver.py tests/test_fanout.py
git commit -m "feat(resolver): validate bands block (frequency 2-tuples, ordering, collisions)"
```

---

### Task 4: band × channel cross product

**Files:**
- Test: `tests/test_fanout.py`

This composes Task 2's band pass with the existing per-site pass (now inhibit-aware via Task 2 Step 6). No new production code is expected — this task proves the composition and fixes any gap it reveals.

- [ ] **Step 1: Write the failing cross-product test**

```python
_BANDS_X_SITES = """
    protocol "xfan" {
      meta { version="1.0"; evidence="clinical"; description="x" }
      requires { sample_rate=">= 256 Hz"; channels=["C3","C4"] }
      bands { theta = (4 Hz, 8 Hz); alpha = (8 Hz, 12 Hz) }
      controls { sites = placement { kind="set"; default=["C3","C4"]; allowed=["C3","C4"]; min=1; max=2 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "env"   { from = "raw"; pipeline = [bandpass(band: bands), hilbert(), magnitude()] }
      derive "score" { from = "env"; pipeline = [auto_range(window: 30 s)] }
      inhibit "critical_fluctuation" { metric = "score"; threshold = percentile(target_pct: 90, window: 2 min); action = mute(release: 400 ms) }
      reward { continuous = 1.0 }
      output { audio_gain = reward.continuous }
    }
"""

def test_band_cross_site_produces_full_grid():
    ir = resolve(parse(_BANDS_X_SITES), _AMP, bindings={"sites": ["C3", "C4"]})
    # 2 bands x 2 channels = 4 of each replicated entity.
    assert set(ir.inputs) == {"raw@C3", "raw@C4"}
    assert set(ir.derives) == {
        "env@theta@C3","env@theta@C4","env@alpha@C3","env@alpha@C4",
        "score@theta@C3","score@theta@C4","score@alpha@C3","score@alpha@C4",
    }
    assert set(ir.inhibits) == {
        "critical_fluctuation@theta@C3","critical_fluctuation@theta@C4","critical_fluctuation@alpha@C3","critical_fluctuation@alpha@C4",
    }

def test_cross_product_wiring_and_bands():
    ir = resolve(parse(_BANDS_X_SITES), _AMP, bindings={"sites": ["C3", "C4"]})
    assert _bandpass_band_of(ir, "env@theta@C3") == (4.0, 8.0)
    assert _active_of(ir, "raw@C3") == "C3"
    assert ir.derives["score@theta@C3"].upstream == ("derive/env@theta@C3",)
    assert ir.inhibits["critical_fluctuation@alpha@C4"].metric_ref == "derive/score@alpha@C4"
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/test_fanout.py -k "cross" -q`
Expected: likely PASS if band→site composition is clean. If it FAILS, the most probable cause is the band pass leaving the seed derive's `from = "raw"` such that the site pass's forward closure from `raw` still reaches the `@band` copies — verify `_transitive_per_site` (site pass) treats `env@theta` as consuming `input/raw` (it does, via the unchanged `from = "raw"` ref). Fix any naming/closure gap revealed; do not add a new pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_fanout.py
git commit -m "test(fanout): band x channel cross-product grid"
```

---

### Task 5: regression + docs

**Files:**
- Test: full `tests/test_fanout.py` + resolver suite
- Modify: `docs/SPEC.md`

- [ ] **Step 1: Run the full fan-out + resolver suites (regression for the `inhibit` keyword change)**

Run: `python -m pytest tests/test_fanout.py tests/ -k "fanout or resolve or placement" -q`
Expected: PASS — existing per-site tests unaffected by adding `inhibit` to `_PER_SITE_KEYWORDS` (their replicated subgraphs contain no inhibit).

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 3: Document the `bands` block in SPEC.md**

In `docs/SPEC.md`, near the `groups` block and the placement Mode 2a fan-out sections, add a `bands { }` subsection: the block syntax (`name = (low Hz, high Hz)`), the `bands` placeholder in `bandpass(band: bands)`, the per-band replication (`<name>@<band>`), inhibit replication, and composition with per-site fan-out for the cross product (`<name>@<band>@<site>`). Note it is front-end only (no IR-JSON/Rust change).

- [ ] **Step 4: Commit**

```bash
git add docs/SPEC.md
git commit -m "docs(spec): bands block + band-axis fan-out + cross product"
```

---

## Self-review (completed by plan author)

- **Spec coverage:** Implements the spec's Design §3 "Band fan-out (two-axis replication)": the `bands` block (Task 1), the band axis reusing `fanout.py`'s closure/rename machinery with tuple substitution (Task 2), validation (Task 3), and the band × channel cross product — "the one genuinely new capability" — via composing band + per-site passes with inhibit replication (Tasks 2.6 + 4). Front-end-only invariant preserved.
- **Placeholder scan:** No TBD/TODO. Code steps show code; run steps show command + expected result. Three steps flag a verify-then-adjust against real `main` code (the `_transitive_per_site` seed-type generalization in 2.4; the `_first_per_site_index` signature in 2.5; the real `ir.inhibits` metric field name in test helpers) — each with the exact change/where-to-check, not vague deferral.
- **Type consistency:** `_BAND_PLACEHOLDER = "bands"` used identically in `_references_band_placeholder`, `_bind_band_nameref`, and the surface. `_PER_BAND_KEYWORDS` (band pass) vs the extended `_PER_SITE_KEYWORDS` (site pass) are distinct and used consistently. `_suffix` chaining yields `<name>@<band>@<site>`, matching the cross-product test assertions. `band_fan_out(file_ast)` (single arg, pre-compose-bindings) vs `fan_out(file_ast, bindings, *, amp)` signatures match their call sites in `resolve()`.
```
