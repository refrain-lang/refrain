# refrain-compiler `extends`/parent resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the compiler service compile protocols that use `extends`, by resolving parents from a caller-supplied `parents` map (HTTP) or a `--library` path (CLI), reporting missing parents as recoverable data.

**Architecture:** `compile_to_ir_json` builds a composite `ParentLoader` (request map first, then filesystem) and threads it into `resolve(parent_loader=…)`, which runs the existing composer (`compose.py`). A parent missing from both sources raises a distinct `_ParentNotFound` → surfaced as `unresolved_parents` (HTTP 200, recoverable); a genuine composition failure (cycle/illegal amend/version) → a `compose` diagnostic. The protocol's original parent ref is echoed in `meta.extends`.

**Tech Stack:** Python ≥3.10, the existing `refrain.compose` composer, FastAPI (server extra), pytest.

## Global Constraints

- Python floor `>=3.10`; `from __future__ import annotations` in new/edited modules.
- Strict mypy; ruff `E,F,W,I,N,B,UP,PL`, line-length 100, `target-version py310`.
- **Canonical compiler, no fork:** reuse `refrain.compose` (composer + `parse_ref` + `ComposeError` + `ParentLoader`); do not reimplement composition.
- **Errors are data:** a missing parent is HTTP **200** with `unresolved_parents`, not a 4xx/5xx. Reserve 422 (malformed request) and 500 (emitted-IR schema bug) as today.
- **In-memory keyed by exact ref** (incl. `@version`); filesystem fallback strips `@version` via `parse_ref`.
- `compile_to_ir_json` **always builds a loader** (never `None`) so an `extends` child with nothing supplied yields `_ParentNotFound`, not the composer's "no loader configured" error.
- Bundled standard library, version-enforcement hardening, DoS limits → **out of scope (PEA-256)**.

## File Structure
- Modify `src/refrain/compile_json.py` — composite loader, `_ParentNotFound`, `parents`/`library_dirs` params, `meta.extends`, `unresolved_parents`, exception mapping. *(Task 1)*
- Modify `src/refrain/server.py` — `CompileRequest.parents`, pass it through, add `unresolved_parents` to the response. *(Task 2)*
- Modify `src/refrain/cli.py` — `compile-json --library`, treat unresolved parent as a hard error. *(Task 3)*
- Tests: `tests/test_compile_json.py`, `tests/test_server.py`, `tests/test_compile_json_cli.py`.

## Shared test fixtures (paste where referenced)

`PARENT_SRC` — a standalone, valid base protocol:
```python
PARENT_SRC = '''protocol "smr_base" {
  meta {
    version  = "1.0.0"
    evidence = "demo"
    description = "base"
  }
  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  output {
    audio_gain = 0
  }
}'''
```

`CHILD_SRC` — extends the base, overrides one meta field (composition merges the rest):
```python
CHILD_SRC = '''protocol "smr_child" extends "smr_base" {
  meta {
    description = "child override"
  }
}'''
```

---

### Task 1: Core — parent resolution in `compile_to_ir_json`

**Files:**
- Modify: `src/refrain/compile_json.py`
- Test: `tests/test_compile_json.py`

**Interfaces:**
- Consumes: `refrain.compose.{ComposeError, ParentLoader, parse_ref}`; `refrain.parser.{parse, parse_file}`; `refrain.ast as A`; `resolve(file_ast, parent_loader=…)` (`resolver.py:2287` runs `compose`).
- Produces:
  - `compile_to_ir_json(source, *, sample_rate_hz=None, validate=True, parents: dict[str,str]|None=None, library_dirs: list[str]|None=None) -> CompileResult`.
  - `CompileResult.unresolved_parents: list[str]` (default `[]`); `meta["extends"]: str | None`.
  - `_ParentNotFound(Exception)` with `.ref`; `_build_loader(parents, library_dirs) -> ParentLoader`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compile_json.py` (paste `PARENT_SRC` and `CHILD_SRC` from the fixtures section, then):
```python
def test_extends_request_supplied_parent_merges():
    r = compile_to_ir_json(CHILD_SRC, sample_rate_hz=256.0, parents={"smr_base": PARENT_SRC})
    assert r.errors == []
    assert r.unresolved_parents == []
    assert r.ir_json is not None
    assert r.ir_json["name"] == "smr_child"      # child's identity
    assert r.ir_json["channels"] == ["Cz"]        # parent's requires merged in
    assert r.meta["extends"] == "smr_base"


def test_missing_parent_reported_as_unresolved():
    r = compile_to_ir_json(CHILD_SRC, sample_rate_hz=256.0)  # no parents supplied
    assert r.ir_json is None
    assert r.errors == []
    assert r.unresolved_parents == ["smr_base"]
    assert r.meta["extends"] == "smr_base"


def test_standalone_protocol_has_null_extends():
    r = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0)
    assert r.meta["extends"] is None
    assert r.unresolved_parents == []


def test_composition_error_is_compose_diagnostic():
    bad_child = '''protocol "bad" extends "smr_base" {
  amend input "ghost" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
}'''
    r = compile_to_ir_json(bad_child, sample_rate_hz=256.0, parents={"smr_base": PARENT_SRC})
    assert r.ir_json is None
    assert r.unresolved_parents == []
    assert len(r.errors) == 1
    assert r.errors[0].stage == "compose"


def test_malformed_supplied_parent_is_compose_diagnostic():
    r = compile_to_ir_json(
        CHILD_SRC, sample_rate_hz=256.0, parents={"smr_base": 'protocol "smr_base" {'}
    )
    assert r.ir_json is None
    assert len(r.errors) == 1
    assert r.errors[0].stage == "compose"


def test_extends_filesystem_library_parent(tmp_path):
    (tmp_path / "smr_base.refrain").write_text(PARENT_SRC)
    r = compile_to_ir_json(CHILD_SRC, sample_rate_hz=256.0, library_dirs=[str(tmp_path)])
    assert r.ir_json is not None
    assert r.ir_json["name"] == "smr_child"
    assert r.ir_json["channels"] == ["Cz"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compile_json.py -q`
Expected: FAIL — `compile_to_ir_json() got an unexpected keyword argument 'parents'` (and `AttributeError` on `unresolved_parents` / `meta["extends"]`).

- [ ] **Step 3: Update imports**

In `src/refrain/compile_json.py`, replace the import block (current lines 9–21):
```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

from . import __version__
from .ir_json import ir_to_json_obj
from .parser import ParseError, parse
from .resolver import ResolveError, resolve
```
with:
```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import __version__
from . import ast as A
from .compose import ComposeError, ParentLoader, parse_ref
from .ir_json import ir_to_json_obj
from .parser import ParseError, parse, parse_file
from .resolver import ResolveError, resolve
```

- [ ] **Step 4: Add the `unresolved_parents` field to `CompileResult`**

Replace the `CompileResult` field block (current lines 51–55):
```python
    ir_json: dict[str, Any] | None
    meta: dict[str, Any]
    errors: list[Diagnostic]
    schema_error: str | None = None
    ir_json_text: str | None = None
```
with:
```python
    ir_json: dict[str, Any] | None
    meta: dict[str, Any]
    errors: list[Diagnostic]
    schema_error: str | None = None
    ir_json_text: str | None = None
    unresolved_parents: list[str] = field(default_factory=list)
```

- [ ] **Step 5: Add the loader + diagnostic helper**

In `src/refrain/compile_json.py`, insert after `_content_hash` (current line 59) and before `_load_schema`:
```python
class _ParentNotFound(Exception):
    """A parent ref resolved in neither the request map nor the library path.

    Recoverable: the caller supplies the named parent and retries. Distinct
    from ComposeError (a genuine composition failure).
    """

    def __init__(self, ref: str):
        self.ref = ref
        super().__init__(ref)


def _build_loader(
    parents: dict[str, str] | None, library_dirs: list[str] | None
) -> ParentLoader:
    """Composite ParentLoader: request-supplied map first, then filesystem.

    Always returns a loader (never None): a child with `extends` and nothing
    supplied then yields `_ParentNotFound` rather than the composer's
    "no loader configured" error. Parent parse failures become ComposeError.
    """
    pmap = parents or {}
    dirs = [Path(d) for d in (library_dirs or [])]

    def loader(ref: str) -> A.File:
        if ref in pmap:
            try:
                return parse(pmap[ref])
            except ParseError as exc:
                raise ComposeError(f"parent {ref!r}: parse failed: {exc}") from exc
        path, _ = parse_ref(ref)
        for d in dirs:
            cand = d / f"{path}.refrain"
            if cand.exists():
                try:
                    return parse_file(cand)
                except ParseError as exc:
                    raise ComposeError(
                        f"parent {ref!r} at {cand}: parse failed: {exc}"
                    ) from exc
        raise _ParentNotFound(ref)

    return loader


def _located(stage: str, exc: ResolveError | ComposeError) -> Diagnostic:
    """A Diagnostic carrying the exception's source span (if any)."""
    loc = exc.loc
    return Diagnostic(
        stage=stage,
        message=str(exc),
        line=loc.line if loc is not None else None,
        col=loc.col if loc is not None else None,
        end_line=loc.end_line if loc is not None else None,
        end_col=loc.end_col if loc is not None else None,
    )
```

- [ ] **Step 6: Rewrite `compile_to_ir_json`**

Replace the whole function (current lines 103–147) with:
```python
def compile_to_ir_json(
    source: str,
    *,
    sample_rate_hz: float | None = None,
    validate: bool = True,
    parents: dict[str, str] | None = None,
    library_dirs: list[str] | None = None,
) -> CompileResult:
    base_meta: dict[str, Any] = {
        "refrain_version": __version__,
        "ir_version": None,
        "sample_rate_hz": sample_rate_hz,
        "content_hash": None,
        "extends": None,
    }

    try:
        file_ast = parse(source)
    except ParseError as exc:
        return CompileResult(None, base_meta, [Diagnostic("parse", str(exc))])

    base_meta["extends"] = file_ast.protocol.extends
    loader = _build_loader(parents, library_dirs)

    try:
        ir = resolve(file_ast, parent_loader=loader)
    except _ParentNotFound as exc:
        return CompileResult(None, base_meta, [], unresolved_parents=[exc.ref])
    except ComposeError as exc:
        return CompileResult(None, base_meta, [_located("compose", exc)])
    except ResolveError as exc:
        return CompileResult(None, base_meta, [_located("resolve", exc)])

    obj = ir_to_json_obj(ir, sample_rate_hz=sample_rate_hz)
    canonical = json.dumps(obj, indent=2)
    meta = {
        "refrain_version": __version__,
        "ir_version": obj["refrain_ir_version"],
        "sample_rate_hz": obj["sample_rate_hz"],
        "content_hash": _content_hash(canonical),
        "extends": file_ast.protocol.extends,
    }
    schema_error = _validate(obj) if validate else None
    return CompileResult(
        ir_json=obj,
        meta=meta,
        errors=[],
        schema_error=schema_error,
        ir_json_text=canonical,
    )
```

- [ ] **Step 7: Run tests + lint + types**

Run: `.venv/bin/python -m pytest tests/test_compile_json.py -q && .venv/bin/python -m ruff check src/refrain/compile_json.py && .venv/bin/python -m mypy src/refrain/compile_json.py`
Expected: all tests pass; ruff "All checks passed!"; mypy "Success".

- [ ] **Step 8: Commit**

```bash
git add src/refrain/compile_json.py tests/test_compile_json.py
git commit -m "feat(compile): resolve extends parents (composite loader, unresolved_parents, meta.extends)"
```

---

### Task 2: HTTP — `/compile` accepts `parents`, returns `unresolved_parents`

**Files:**
- Modify: `src/refrain/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `compile_to_ir_json(..., parents=…)`; `CompileResult.unresolved_parents`; `meta["extends"]`.
- Produces: `CompileRequest.parents: dict[str,str]|None`; response gains `"unresolved_parents"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py` (paste `PARENT_SRC` and `CHILD_SRC` from the fixtures section, then):
```python
def test_compile_resolves_supplied_parent():
    r = client.post("/compile", json={
        "refrain": CHILD_SRC, "sample_rate_hz": 256.0, "parents": {"smr_base": PARENT_SRC}})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"]["name"] == "smr_child"
    assert body["unresolved_parents"] == []
    assert body["meta"]["extends"] == "smr_base"


def test_compile_missing_parent_is_200_unresolved():
    r = client.post("/compile", json={"refrain": CHILD_SRC, "sample_rate_hz": 256.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["unresolved_parents"] == ["smr_base"]
    assert body["errors"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_server.py -q -p no:warnings`
Expected: FAIL — `parents` is ignored (extra field) so the child fails to resolve, and `unresolved_parents` is absent from the response (`KeyError`).

- [ ] **Step 3: Add `parents` to the request model**

In `src/refrain/server.py`, replace:
```python
class CompileRequest(BaseModel):
    refrain: str
    sample_rate_hz: float | None = None
```
with:
```python
class CompileRequest(BaseModel):
    refrain: str
    sample_rate_hz: float | None = None
    parents: dict[str, str] | None = None
```

- [ ] **Step 4: Thread it through the endpoint**

In `src/refrain/server.py`, replace the `compile_endpoint` body:
```python
    result = compile_to_ir_json(req.refrain, sample_rate_hz=req.sample_rate_hz)
    if result.schema_error is not None:
        raise HTTPException(status_code=500, detail=result.schema_error)
    return {
        "ir_json": result.ir_json,
        "ir_json_text": result.ir_json_text,
        "meta": result.meta,
        "errors": [asdict(d) for d in result.errors],
    }
```
with:
```python
    result = compile_to_ir_json(
        req.refrain, sample_rate_hz=req.sample_rate_hz, parents=req.parents
    )
    if result.schema_error is not None:
        raise HTTPException(status_code=500, detail=result.schema_error)
    return {
        "ir_json": result.ir_json,
        "ir_json_text": result.ir_json_text,
        "meta": result.meta,
        "errors": [asdict(d) for d in result.errors],
        "unresolved_parents": result.unresolved_parents,
    }
```

- [ ] **Step 5: Run tests + lint + types**

Run: `.venv/bin/python -m pytest tests/test_server.py -q -p no:warnings && .venv/bin/python -m ruff check src/refrain/server.py && .venv/bin/python -m mypy src/refrain/server.py`
Expected: pass; ruff clean; mypy Success.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/server.py tests/test_server.py
git commit -m "feat(server): /compile accepts parents map, returns unresolved_parents"
```

---

### Task 3: CLI — `compile-json --library`

**Files:**
- Modify: `src/refrain/cli.py`
- Test: `tests/test_compile_json_cli.py`

**Interfaces:**
- Consumes: `compile_to_ir_json(..., library_dirs=…)`; `CompileResult.unresolved_parents`; `default_library_dirs` (already imported in `cli.py`).
- Produces: `refrain compile-json … --library DIR` (repeatable); unresolved parent → exit 1.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compile_json_cli.py` (paste `PARENT_SRC` and `CHILD_SRC` from the fixtures section, then):
```python
def test_compile_json_extends_library(tmp_path, capsys):
    (tmp_path / "smr_base.refrain").write_text(PARENT_SRC)
    child = tmp_path / "child.refrain"
    child.write_text(CHILD_SRC)
    code = main([
        "compile-json", str(child), "--sample-rate", "256", "--library", str(tmp_path)])
    assert code == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["name"] == "smr_child"


def test_compile_json_missing_parent_exits_1(tmp_path, capsys):
    child = tmp_path / "child.refrain"
    child.write_text(CHILD_SRC)
    code = main(["compile-json", str(child)])
    assert code == 1
    assert "unresolved parent" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_compile_json_cli.py -q`
Expected: FAIL — `--library` is an unrecognized argument (SystemExit), and the missing-parent case currently doesn't emit "unresolved parent".

- [ ] **Step 3: Register the `--library` argument**

In `src/refrain/cli.py`, in the `compile-json` subparser block, after the `--meta` argument and before `compile_json_cmd.set_defaults(func=_cmd_compile_json)`, add:
```python
    compile_json_cmd.add_argument(
        "--library", action="append", default=[], metavar="DIR",
        help="Directory to search for `extends`-referenced parent protocols (repeatable).",
    )
```

- [ ] **Step 4: Pass `library_dirs` and handle unresolved parents**

In `src/refrain/cli.py`, replace the `_cmd_compile_json` body lines:
```python
    result = compile_to_ir_json(path.read_text(), sample_rate_hz=args.sample_rate)

    if result.errors:
        for d in result.errors:
            loc = f"{d.line}:{d.col}: " if d.line is not None else ""
            print(f"error: {path}: {d.stage}: {loc}{d.message}", file=sys.stderr)
        return 1
    if result.schema_error is not None:
        print(f"error: {path}: schema: {result.schema_error}", file=sys.stderr)
        return 1
```
with:
```python
    library_dirs = [*args.library, *(str(d) for d in default_library_dirs())]
    result = compile_to_ir_json(
        path.read_text(), sample_rate_hz=args.sample_rate, library_dirs=library_dirs
    )

    if result.errors:
        for d in result.errors:
            loc = f"{d.line}:{d.col}: " if d.line is not None else ""
            print(f"error: {path}: {d.stage}: {loc}{d.message}", file=sys.stderr)
        return 1
    if result.unresolved_parents:
        for ref in result.unresolved_parents:
            print(f"error: {path}: unresolved parent: {ref}", file=sys.stderr)
        return 1
    if result.schema_error is not None:
        print(f"error: {path}: schema: {result.schema_error}", file=sys.stderr)
        return 1
```

- [ ] **Step 5: Run tests + lint + types**

Run: `.venv/bin/python -m pytest tests/test_compile_json_cli.py -q && .venv/bin/python -m ruff check src/refrain/cli.py`
Expected: tests pass; ruff reports **no new** errors versus baseline (`cli.py` carries pre-existing E501/typing debt — confirm the count is unchanged, do not fix unrelated lines).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/cli.py tests/test_compile_json_cli.py
git commit -m "feat(cli): compile-json --library resolves extends parents"
```

---

### Task 4: Full regression + review

**Files:** none (verification only).

- [ ] **Step 1: Whole suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: exit 0, no regressions; the new parent tests in all three files included.

- [ ] **Step 2: Lint + types on the changed surface**

Run: `.venv/bin/python -m ruff check src/refrain/compile_json.py src/refrain/server.py && .venv/bin/python -m mypy src/refrain/compile_json.py src/refrain/server.py`
Expected: ruff clean on these two; mypy Success. (`cli.py` retains only its pre-existing debt — verify no new findings.)

- [ ] **Step 3: Core import stays lean**

Run: `.venv/bin/python -c "import sys, refrain.compile_json; assert 'fastapi' not in sys.modules; print('ok')"`
Expected: `ok` — `compile_json` (now importing `compose`) still pulls no web stack.

---

## Self-review

- **Spec coverage:** §3.1 signature → Task 1 Step 6. §3.2 composite loader (always-return, exact-ref key, parse-fail→ComposeError) → Task 1 Step 5. §3.3 outcome mapping (parse/`_ParentNotFound`/compose/resolve) → Task 1 Step 6. §3.4 `unresolved_parents` + `meta.extends` → Task 1 Steps 4/6. §4 HTTP → Task 2. §5 CLI → Task 3. §6 testing → Tasks 1–3 + Task 4. §7 scope (no bundled library) honored — server passes no `library_dirs`. §8 premise noted (PEA-256).
- **Placeholder scan:** none — every code step is complete.
- **Type consistency:** `_ParentNotFound.ref`, `_build_loader -> ParentLoader`, `_located(stage, exc)`, `compile_to_ir_json(..., parents, library_dirs)`, `CompileResult.unresolved_parents` are defined in Task 1 and consumed unchanged in Tasks 2–3. `meta["extends"]` set in Task 1, read in Tasks 2–3.

## Known limitations (out of scope — PEA-256)
- No in-service bundled standard `library/` (server passes no `library_dirs`); only request-supplied + CLI `--library`.
- `unresolved_parents` surfaces one ref at a time (single inheritance is linear); deep chains negotiate iteratively.
- Version-constraint enforcement stays the composer's existing major-only check; no `parents`-map size/DoS limits.
