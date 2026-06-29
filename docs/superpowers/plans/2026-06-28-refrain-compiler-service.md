# refrain-compiler Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the canonical Python compiler as an in-repo HTTP service (`POST /compile` → IR-JSON) plus a `refrain compile-json` CLI producer, so the coherence-portal can author/compile protocols (Option A sub-project #2).

**Architecture:** One shared compile path — `refrain.compile_json.compile_to_ir_json()` (`parse → resolve(amp=None) → ir_to_json_obj(sr) → validate → hash`) — is wrapped by two thin faces: the `refrain compile-json` CLI subcommand and a FastAPI app (`refrain.server`). The IR-JSON schema is relocated into the package so it ships in the wheel for runtime validation. Web deps live behind a `[server]` optional-dependencies extra; core installs are unchanged.

**Tech Stack:** Python ≥3.10, FastAPI + uvicorn (server extra), jsonschema (validation), hatchling (build), pytest (tests). Reuses `refrain.parser`, `refrain.resolver`, `refrain.ir_json`.

## Global Constraints

- Python floor: `requires-python = ">=3.10"`. Use `from __future__ import annotations` in new modules.
- Lint/type bar (every new/edited file): strict mypy (`[tool.mypy] strict = true`); ruff rules `E,F,W,I,N,B,UP,PL`, `line-length = 100`, `target-version = "py310"`.
- **Canonical compiler, no fork:** the service imports the same `parse`/`resolve`/`ir_to_json_obj` the package ships. Never copy or reimplement compile logic.
- **Compile errors are data, not HTTP failures:** a `.refrain` with parse/resolve errors yields HTTP **200** with `ir_json: null` + populated `errors[]`. Reserve **422** for malformed requests (Pydantic) and **500** for a compiler bug (emitted IR fails its own schema).
- **content_hash** is `"sha256:" + sha256(canonical)` where `canonical` is the exact `ir_to_json(...)` serialization (`json.dumps(obj, indent=2)`, UTF-8). Same bytes everyone stores/serves.
- **jsonschema placement:** declared in the `[server]` extra and `dev` (NOT core). `compile_to_ir_json` imports it lazily and *gracefully skips validation* when absent (core CLI users without `[server]` still get IR-JSON). The server always has it installed, so it always validates.
- `fastapi` + `uvicorn[standard]` live only in `[server]`. Importing `refrain.server` without the extra is expected to fail; never import it from core modules.

## File Structure

- Create `src/refrain/schema/ir-json-v0.1.schema.json`, `src/refrain/schema/ir-json-v0.2.schema.json` — relocated wire schemas (single source of truth; ship in wheel). *(Task 1, `git mv` from `refrain-core/schema/`.)*
- Create `src/refrain/compile_json.py` — `Diagnostic`, `CompileResult`, `compile_to_ir_json`, schema load+validate, content hash. The one compile path. *(Tasks 2–4.)*
- Modify `src/refrain/cli.py` — add `_cmd_compile_json` + the `compile-json` subparser. *(Task 5.)*
- Create `src/refrain/server.py` — FastAPI app: `POST /compile`, `GET /healthz`, `GET /version`. *(Task 6.)*
- Modify `pyproject.toml` — add `[project.optional-dependencies] server`; move `jsonschema` into it (keep in `dev`). *(Task 6.)*
- Create `Dockerfile`, `docs/refrain-compiler.md` — deploy + usage. *(Task 7.)*
- Modify `tests/test_ir_json_schema.py:26`, `tests/test_ir_json.py:356`, `refrain-core/tools/check_equivalence.py:17`, and `docs/{IR-JSON,CONFORMANCE,REPRODUCIBILITY,MIGRATING-TO-RUST-BACKEND}.md` — repoint to the new schema path. *(Task 1.)*
- Create `tests/test_compile_json.py`, `tests/test_compile_json_cli.py`, `tests/test_server.py` — TDD suites. *(Tasks 2–6.)*

## Shared test fixtures (used across tasks)

These `.refrain` sources are structurally identical to the verified-valid `bench/protocols/micro_01_passthrough.refrain` and existing resolver tests. Paste them verbatim where a task references them by name.

`SMOKE_SRC` — compiles cleanly, emits IR-JSON v0.1:
```python
SMOKE_SRC = '''protocol "svc_smoke" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "compile-service smoke protocol"
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

`BAD_RESOLVE_SRC` — parses, fails to resolve with a located error (`unknown identifier 'nonexistent'`):
```python
BAD_RESOLVE_SRC = '''protocol "svc_bad" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "bad"
  }
  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  derive "filtered" {
    from = "raw"
    pipeline = [bandpass(center: nonexistent, bandwidth: ratio(0.5))]
  }
  output {
    audio_gain = 0
  }
}'''
```

`BAD_PARSE_SRC` — malformed syntax (unclosed brace) → `ParseError`:
```python
BAD_PARSE_SRC = 'protocol "oops" {'
```

---

### Task 1: Relocate the IR-JSON schema into the package (single source of truth)

**Files:**
- Move: `refrain-core/schema/ir-json-v0.1.schema.json` → `src/refrain/schema/ir-json-v0.1.schema.json`
- Move: `refrain-core/schema/ir-json-v0.2.schema.json` → `src/refrain/schema/ir-json-v0.2.schema.json`
- Modify: `tests/test_ir_json_schema.py:26`
- Modify: `tests/test_ir_json.py:356`
- Modify: `refrain-core/tools/check_equivalence.py:17` (docstring)
- Modify: `docs/IR-JSON.md`, `docs/CONFORMANCE.md`, `docs/REPRODUCIBILITY.md`, `docs/MIGRATING-TO-RUST-BACKEND.md` (prose path references)

**Interfaces:**
- Produces: schema files importable at runtime via `importlib.resources.files("refrain") / "schema" / "ir-json-v{version}.schema.json"`.

**Note:** Do NOT edit `CHANGELOG.md` or files under `docs/superpowers/{plans,specs}/` — those are historical records; leave their old paths intact.

- [ ] **Step 1: Move the schema files with git**

```bash
mkdir -p src/refrain/schema
git mv refrain-core/schema/ir-json-v0.1.schema.json src/refrain/schema/ir-json-v0.1.schema.json
git mv refrain-core/schema/ir-json-v0.2.schema.json src/refrain/schema/ir-json-v0.2.schema.json
rmdir refrain-core/schema 2>/dev/null || true
```

- [ ] **Step 2: Repoint the two test path-constants**

In `tests/test_ir_json_schema.py`, change line 26 from:
```python
SCHEMA_DIR = REPO / "refrain-core" / "schema"
```
to:
```python
SCHEMA_DIR = REPO / "src" / "refrain" / "schema"
```

In `tests/test_ir_json.py`, change line 356 from:
```python
    schema_path = REPO / "refrain-core" / "schema" / "ir-json-v0.1.schema.json"
```
to:
```python
    schema_path = REPO / "src" / "refrain" / "schema" / "ir-json-v0.1.schema.json"
```

(If `REPO` in either file is not the repo root, adjust the relative segments so the path resolves to `<repo>/src/refrain/schema`. Confirm with `python -c "import pathlib,sys; print((pathlib.Path('tests/test_ir_json_schema.py')))"` then read the `REPO =` line.)

- [ ] **Step 3: Run the moved-schema tests to verify they still pass**

Run: `python -m pytest tests/test_ir_json_schema.py tests/test_ir_json.py -q`
Expected: PASS (same counts as before the move). If `jsonschema`-gated tests skip, that's fine.

- [ ] **Step 4: Tidy the prose references**

In `refrain-core/tools/check_equivalence.py` line 17, replace `refrain-core/schema/ir-json-v0.1.schema.json or ir-json-v0.2.schema.json` with `src/refrain/schema/ir-json-v0.1.schema.json or ir-json-v0.2.schema.json`.

In each of `docs/IR-JSON.md`, `docs/CONFORMANCE.md`, `docs/REPRODUCIBILITY.md`, `docs/MIGRATING-TO-RUST-BACKEND.md`, replace every occurrence of the substring `refrain-core/schema/` with `src/refrain/schema/`. Verify none remain:
```bash
grep -rn "refrain-core/schema/" docs/IR-JSON.md docs/CONFORMANCE.md docs/REPRODUCIBILITY.md docs/MIGRATING-TO-RUST-BACKEND.md refrain-core/tools/check_equivalence.py
```
Expected: no output.

- [ ] **Step 5: Verify the schema ships in the built wheel**

```bash
rm -rf dist && python -m pip wheel --no-deps -w dist . >/dev/null && unzip -l dist/refrain-*.whl | grep "refrain/schema/"
```
Expected: two lines listing `refrain/schema/ir-json-v0.1.schema.json` and `refrain/schema/ir-json-v0.2.schema.json`. Then `rm -rf dist`.

- [ ] **Step 6: Commit**

```bash
git add -A src/refrain/schema tests/test_ir_json_schema.py tests/test_ir_json.py refrain-core/tools/check_equivalence.py docs/
git commit -m "refactor(schema): relocate ir-json schema into src/refrain/schema (single source of truth)"
```

---

### Task 2: `compile_to_ir_json` — happy path, meta, content_hash

**Files:**
- Create: `src/refrain/compile_json.py`
- Test: `tests/test_compile_json.py`

**Interfaces:**
- Consumes: `refrain.parser.parse(source: str) -> A.File`; `refrain.resolver.resolve(file_ast, amp=None) -> IRProtocol`; `refrain.ir_json.ir_to_json_obj(ir, *, sample_rate_hz=None) -> dict`; `refrain.__version__`.
- Produces:
  - `Diagnostic(stage: str, message: str, severity: str = "error", line: int|None = None, col: int|None = None, end_line: int|None = None, end_col: int|None = None)` (frozen dataclass).
  - `CompileResult(ir_json: dict|None, meta: dict, errors: list[Diagnostic], schema_error: str|None = None)` (frozen dataclass).
  - `compile_to_ir_json(source: str, *, sample_rate_hz: float|None = None, validate: bool = True) -> CompileResult`.
  - `meta` keys on success: `refrain_version`, `ir_version`, `sample_rate_hz`, `content_hash`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compile_json.py` with the shared fixtures (paste `SMOKE_SRC` from the fixtures section above) and:
```python
import json

import refrain
from refrain.compile_json import compile_to_ir_json

SMOKE_SRC = '''protocol "svc_smoke" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "compile-service smoke protocol"
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


def test_happy_path_emits_ir_and_meta():
    result = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0)
    assert result.errors == []
    assert result.ir_json is not None
    assert result.ir_json["name"] == "svc_smoke"
    assert result.meta["refrain_version"] == refrain.__version__
    assert result.meta["ir_version"] == "0.1"
    assert result.meta["sample_rate_hz"] == 256.0
    assert result.meta["content_hash"].startswith("sha256:")


def test_content_hash_is_deterministic_and_rate_sensitive():
    h1 = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0).meta["content_hash"]
    h1b = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0).meta["content_hash"]
    h2 = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=512.0).meta["content_hash"]
    assert h1 == h1b
    assert h1 != h2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_compile_json.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'refrain.compile_json'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/refrain/compile_json.py`:
```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Compile `.refrain` source to IR-JSON — the one path the CLI and the
HTTP service both call. Wraps `parse -> resolve(amp=None) -> ir_to_json_obj`,
attaches compile metadata (version, ir version, content hash), and (later)
validates the emitted IR-JSON against the bundled schema.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import __version__
from .ir_json import ir_to_json_obj
from .parser import parse
from .resolver import resolve


@dataclass(frozen=True)
class Diagnostic:
    """A compile diagnostic with an optional 1-based source span."""

    stage: str
    message: str
    severity: str = "error"
    line: int | None = None
    col: int | None = None
    end_line: int | None = None
    end_col: int | None = None


@dataclass(frozen=True)
class CompileResult:
    """Outcome of one compile. `ir_json`/`content_hash` are None on error.

    `schema_error` is set only when the emitted IR-JSON fails its own schema
    (a compiler bug); the HTTP layer maps it to 500. It is never a user error.
    """

    ir_json: dict | None
    meta: dict
    errors: list[Diagnostic]
    schema_error: str | None = None


def _content_hash(canonical: str) -> str:
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_to_ir_json(
    source: str, *, sample_rate_hz: float | None = None, validate: bool = True
) -> CompileResult:
    ir = resolve(parse(source))
    obj = ir_to_json_obj(ir, sample_rate_hz=sample_rate_hz)
    canonical = json.dumps(obj, indent=2)
    meta = {
        "refrain_version": __version__,
        "ir_version": obj["refrain_ir_version"],
        "sample_rate_hz": obj["sample_rate_hz"],
        "content_hash": _content_hash(canonical),
    }
    return CompileResult(ir_json=obj, meta=meta, errors=[])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_compile_json.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Lint & type-check the new module**

Run: `python -m ruff check src/refrain/compile_json.py && python -m mypy src/refrain/compile_json.py`
Expected: no errors. (Fix any before committing.)

- [ ] **Step 6: Commit**

```bash
git add src/refrain/compile_json.py tests/test_compile_json.py
git commit -m "feat(compile): add compile_to_ir_json happy path with meta + content_hash"
```

---

### Task 2 note — `canonical` equals `ir_to_json` output

`json.dumps(obj, indent=2)` here is byte-identical to `refrain.ir_json.ir_to_json(ir, sample_rate_hz=...)` (which is defined as exactly that). So `content_hash` is the hash of the canonical `ir_to_json` serialization, as the spec requires — without importing `ir_to_json` separately.

---

### Task 3: `compile_to_ir_json` — map parse/resolve errors to Diagnostics

**Files:**
- Modify: `src/refrain/compile_json.py`
- Test: `tests/test_compile_json.py` (add cases)

**Interfaces:**
- Consumes: `refrain.parser.ParseError` (no location attrs); `refrain.resolver.ResolveError` (has `.loc: Loc | None` with `.line/.col/.end_line/.end_col`).
- Produces: on error, `CompileResult(ir_json=None, meta={...content_hash:None, ir_version:None...}, errors=[Diagnostic(...)])`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compile_json.py` (paste `BAD_RESOLVE_SRC` and `BAD_PARSE_SRC` from the fixtures section above, then):
```python
def test_resolve_error_returns_located_diagnostic():
    result = compile_to_ir_json(BAD_RESOLVE_SRC, sample_rate_hz=256.0)
    assert result.ir_json is None
    assert result.meta["content_hash"] is None
    assert result.meta["ir_version"] is None
    assert len(result.errors) == 1
    diag = result.errors[0]
    assert diag.stage == "resolve"
    assert diag.severity == "error"
    assert diag.line is not None and diag.col is not None


def test_parse_error_returns_diagnostic_without_location():
    result = compile_to_ir_json(BAD_PARSE_SRC)
    assert result.ir_json is None
    assert len(result.errors) == 1
    assert result.errors[0].stage == "parse"
    assert result.errors[0].message  # non-empty (Lark's message)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_compile_json.py -q`
Expected: FAIL — `BAD_RESOLVE_SRC`/`BAD_PARSE_SRC` currently raise uncaught `ResolveError`/`ParseError` (not returned as `CompileResult`).

- [ ] **Step 3: Implement error handling**

In `src/refrain/compile_json.py`, update the imports and rewrite `compile_to_ir_json` to catch and map errors:
```python
from .parser import ParseError, parse
from .resolver import ResolveError, resolve
```
```python
def compile_to_ir_json(
    source: str, *, sample_rate_hz: float | None = None, validate: bool = True
) -> CompileResult:
    base_meta = {
        "refrain_version": __version__,
        "ir_version": None,
        "sample_rate_hz": sample_rate_hz,
        "content_hash": None,
    }

    try:
        file_ast = parse(source)
    except ParseError as exc:
        return CompileResult(None, base_meta, [Diagnostic("parse", str(exc))])

    try:
        ir = resolve(file_ast)
    except ResolveError as exc:
        loc = exc.loc
        diag = Diagnostic(
            stage="resolve",
            message=str(exc),
            line=loc.line if loc is not None else None,
            col=loc.col if loc is not None else None,
            end_line=loc.end_line if loc is not None else None,
            end_col=loc.end_col if loc is not None else None,
        )
        return CompileResult(None, base_meta, [diag])

    obj = ir_to_json_obj(ir, sample_rate_hz=sample_rate_hz)
    canonical = json.dumps(obj, indent=2)
    meta = {
        "refrain_version": __version__,
        "ir_version": obj["refrain_ir_version"],
        "sample_rate_hz": obj["sample_rate_hz"],
        "content_hash": _content_hash(canonical),
    }
    return CompileResult(ir_json=obj, meta=meta, errors=[])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_compile_json.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint & type-check**

Run: `python -m ruff check src/refrain/compile_json.py && python -m mypy src/refrain/compile_json.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/compile_json.py tests/test_compile_json.py
git commit -m "feat(compile): map parse/resolve errors to located Diagnostics"
```

---

### Task 4: `compile_to_ir_json` — validate emitted IR-JSON against the bundled schema

**Files:**
- Modify: `src/refrain/compile_json.py`
- Test: `tests/test_compile_json.py` (add cases)

**Interfaces:**
- Consumes: bundled schema via `importlib.resources.files("refrain") / "schema" / "ir-json-v{version}.schema.json"`; `jsonschema.Draft202012Validator` (lazy import; optional).
- Produces: `_load_schema(version: str) -> dict`; `_validate(obj: dict) -> str | None`. `compile_to_ir_json` sets `CompileResult.schema_error` from `_validate` when `validate=True` and `jsonschema` is importable.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compile_json.py`:
```python
from refrain.compile_json import _load_schema, _validate


def test_bundled_schema_loads_for_both_versions():
    assert _load_schema("0.1")["$id"].endswith("ir-json-v0.1.schema.json")
    assert _load_schema("0.2")["$id"].endswith("ir-json-v0.2.schema.json")


def test_valid_compile_has_no_schema_error():
    result = compile_to_ir_json(SMOKE_SRC, sample_rate_hz=256.0)
    assert result.schema_error is None


def test_validate_flags_nonconformant_ir():
    # An object that claims v0.1 but is missing every required field.
    err = _validate({"refrain_ir_version": "0.1"})
    assert err is not None
    assert "0.1" in err


def test_validate_unknown_version_is_reported():
    err = _validate({"refrain_ir_version": "9.9"})
    assert err is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_compile_json.py -q`
Expected: FAIL with `ImportError: cannot import name '_load_schema'`.

- [ ] **Step 3: Implement schema loading + validation**

In `src/refrain/compile_json.py`, add imports near the top:
```python
from functools import lru_cache
from importlib.resources import files
```
Add these functions (place above `compile_to_ir_json`):
```python
@lru_cache(maxsize=None)
def _load_schema(version: str) -> dict:
    """Load the bundled IR-JSON schema for `version` (e.g. "0.1").

    Raises FileNotFoundError if no schema ships for that version.
    """
    resource = files("refrain") / "schema" / f"ir-json-v{version}.schema.json"
    return json.loads(resource.read_text())


def _validate(obj: dict) -> str | None:
    """Validate emitted IR-JSON against its bundled schema.

    Returns an error string when the compiler produced non-conformant IR
    (a compiler bug), else None. Returns None (skips) when `jsonschema` is
    not installed — the core install carries the schema files but not the
    validator; the [server] extra adds it.
    """
    try:
        import jsonschema
    except ModuleNotFoundError:
        return None

    version = obj.get("refrain_ir_version")
    if not isinstance(version, str):
        return "emitted IR-JSON has no refrain_ir_version"
    try:
        schema = _load_schema(version)
    except FileNotFoundError:
        return f"no bundled schema for ir version {version!r}"

    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(obj),
        key=lambda e: list(e.path),
    )
    if errors:
        return f"emitted IR-JSON failed schema v{version}: {errors[0].message}"
    return None
```
Then in `compile_to_ir_json`, replace the success `return` with:
```python
    schema_error = _validate(obj) if validate else None
    return CompileResult(ir_json=obj, meta=meta, errors=[], schema_error=schema_error)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_compile_json.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Lint & type-check**

Run: `python -m ruff check src/refrain/compile_json.py && python -m mypy src/refrain/compile_json.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/compile_json.py tests/test_compile_json.py
git commit -m "feat(compile): validate emitted IR-JSON against bundled schema (lazy jsonschema)"
```

---

### Task 5: `refrain compile-json` CLI subcommand

**Files:**
- Modify: `src/refrain/cli.py` (add import, `_cmd_compile_json`, subparser in `_build_argparser`)
- Test: `tests/test_compile_json_cli.py`

**Interfaces:**
- Consumes: `refrain.compile_json.compile_to_ir_json`; argparse dispatch via `main(argv)`.
- Produces: `refrain compile-json FILE [--sample-rate HZ] [--meta]`. Exit 0 (prints IR-JSON, or meta with `--meta`), exit 1 (compile errors or schema error → diagnostics to stderr), exit 2 (no such file).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compile_json_cli.py`:
```python
import json

from refrain.cli import main

SMOKE_SRC = '''protocol "svc_smoke" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "compile-service smoke protocol"
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

BAD_RESOLVE_SRC = SMOKE_SRC.replace(
    '  output {\n    audio_gain = 0\n  }\n}',
    '  derive "filtered" {\n'
    '    from = "raw"\n'
    '    pipeline = [bandpass(center: nonexistent, bandwidth: ratio(0.5))]\n'
    '  }\n'
    '  output {\n    audio_gain = 0\n  }\n}',
)


def _write(tmp_path, src):
    p = tmp_path / "p.refrain"
    p.write_text(src)
    return str(p)


def test_compile_json_prints_ir(tmp_path, capsys):
    code = main(["compile-json", _write(tmp_path, SMOKE_SRC), "--sample-rate", "256"])
    assert code == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["name"] == "svc_smoke"
    assert obj["sample_rate_hz"] == 256.0


def test_compile_json_meta_flag(tmp_path, capsys):
    code = main(["compile-json", _write(tmp_path, SMOKE_SRC), "--meta"])
    assert code == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["content_hash"].startswith("sha256:")


def test_compile_json_resolve_error_exits_1(tmp_path, capsys):
    code = main(["compile-json", _write(tmp_path, BAD_RESOLVE_SRC)])
    assert code == 1
    assert "resolve" in capsys.readouterr().err


def test_compile_json_missing_file_exits_2(tmp_path, capsys):
    code = main(["compile-json", str(tmp_path / "nope.refrain")])
    assert code == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_compile_json_cli.py -q`
Expected: FAIL — argparse exits with an error because `compile-json` is not a registered subcommand.

- [ ] **Step 3: Add the import**

In `src/refrain/cli.py`, after the existing `from .resolver import ResolveError, resolve` line, add:
```python
from .compile_json import compile_to_ir_json
```

- [ ] **Step 4: Add the handler**

In `src/refrain/cli.py`, add this function (next to `_cmd_resolve`):
```python
def _cmd_compile_json(args: argparse.Namespace) -> int:
    """Parse + resolve + emit IR-JSON (the portal's phase-1 producer)."""
    path = Path(args.file)
    if not path.exists():
        print(f"error: {path}: no such file", file=sys.stderr)
        return 2

    result = compile_to_ir_json(path.read_text(), sample_rate_hz=args.sample_rate)

    if result.errors:
        for d in result.errors:
            loc = f"{d.line}:{d.col}: " if d.line is not None else ""
            print(f"error: {path}: {d.stage}: {loc}{d.message}", file=sys.stderr)
        return 1
    if result.schema_error is not None:
        print(f"error: {path}: schema: {result.schema_error}", file=sys.stderr)
        return 1

    payload = result.meta if args.meta else result.ir_json
    print(json.dumps(payload, indent=2))
    return 0
```

- [ ] **Step 5: Register the subparser**

In `_build_argparser`, after the `resolve_cmd.set_defaults(func=_cmd_resolve)` block, add:
```python
    compile_json_cmd = sub.add_parser(
        "compile-json",
        help="Parse + resolve + emit IR-JSON for a target sample rate.",
    )
    compile_json_cmd.add_argument("file", help="Path to the .refrain protocol file.")
    compile_json_cmd.add_argument(
        "--sample-rate", type=float, default=None, metavar="HZ",
        help="Sample rate to bake coefficients for (default: the protocol's chosen rate).",
    )
    compile_json_cmd.add_argument(
        "--meta", action="store_true",
        help="Print compile metadata (versions, content_hash) instead of the IR-JSON.",
    )
    compile_json_cmd.set_defaults(func=_cmd_compile_json)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_compile_json_cli.py -q`
Expected: PASS (4 passed).

- [ ] **Step 7: Lint & type-check**

Run: `python -m ruff check src/refrain/cli.py && python -m mypy src/refrain/cli.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/cli.py tests/test_compile_json_cli.py
git commit -m "feat(cli): add 'refrain compile-json' subcommand"
```

---

### Task 6: FastAPI service + `[server]` extra

**Files:**
- Create: `src/refrain/server.py`
- Modify: `pyproject.toml` (add `[project.optional-dependencies] server`; keep `jsonschema` in `dev`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `refrain.compile_json.compile_to_ir_json`, `CompileResult`; `refrain.__version__`.
- Produces: `refrain.server.app` (FastAPI). `POST /compile {refrain, sample_rate_hz?}` → 200 `{ir_json, meta, errors}` | 422 (malformed) | 500 (schema_error). `GET /healthz` → `{"status":"ok"}`. `GET /version` → `{refrain_version, ir_versions_supported, schema_versions}`.

- [ ] **Step 1: Add the `[server]` extra to `pyproject.toml`**

In `pyproject.toml`, under `[project.optional-dependencies]`, add a `server` group (place it before `eval`):
```toml
server = [
  "fastapi >= 0.110",
  "uvicorn[standard] >= 0.29",
  "jsonschema >= 4.0",
]
```
(`jsonschema` stays listed in `dev` too — leave that line unchanged.)

- [ ] **Step 2: Install the extra into the working environment**

Run: `python -m pip install -e ".[server,dev]"`
Expected: installs fastapi, uvicorn, jsonschema (and dev tools). Needed so the tests below can import FastAPI.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_server.py`:
```python
from fastapi.testclient import TestClient

import refrain
from refrain.server import app

client = TestClient(app)

SMOKE_SRC = '''protocol "svc_smoke" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "compile-service smoke protocol"
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


def test_compile_success_returns_ir_and_meta():
    r = client.post("/compile", json={"refrain": SMOKE_SRC, "sample_rate_hz": 256.0})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"]["name"] == "svc_smoke"
    assert body["meta"]["content_hash"].startswith("sha256:")
    assert body["errors"] == []


def test_compile_error_is_200_with_diagnostics():
    bad = 'protocol "oops" {'
    r = client.post("/compile", json={"refrain": bad})
    assert r.status_code == 200
    body = r.json()
    assert body["ir_json"] is None
    assert body["errors"][0]["stage"] == "parse"


def test_malformed_request_is_422():
    r = client.post("/compile", json={"not_refrain": "x"})
    assert r.status_code == 422


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_version():
    body = client.get("/version").json()
    assert body["refrain_version"] == refrain.__version__
    assert body["ir_versions_supported"] == ["0.1", "0.2"]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_server.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'refrain.server'`.

- [ ] **Step 5: Implement the server**

Create `src/refrain/server.py`:
```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""HTTP face over the canonical compiler (Option A sub-project #2).

Stateless: `compile in -> IR out`. Trusted-network sidecar — must not be
exposed publicly (no auth). Requires the `[server]` extra (fastapi/uvicorn).
Run with: `uvicorn refrain.server:app`.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import __version__
from .compile_json import compile_to_ir_json

app = FastAPI(title="refrain-compiler", version=__version__)


class CompileRequest(BaseModel):
    refrain: str
    sample_rate_hz: float | None = None


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/version")
def version() -> dict:
    return {
        "refrain_version": __version__,
        "ir_versions_supported": ["0.1", "0.2"],
        "schema_versions": ["0.1", "0.2"],
    }


@app.post("/compile")
def compile_endpoint(req: CompileRequest) -> dict:
    result = compile_to_ir_json(req.refrain, sample_rate_hz=req.sample_rate_hz)
    if result.schema_error is not None:
        raise HTTPException(status_code=500, detail=result.schema_error)
    return {
        "ir_json": result.ir_json,
        "meta": result.meta,
        "errors": [asdict(d) for d in result.errors],
    }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_server.py -q`
Expected: PASS (5 passed).

- [ ] **Step 7: Lint & type-check**

Run: `python -m ruff check src/refrain/server.py && python -m mypy src/refrain/server.py`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/refrain/server.py tests/test_server.py
git commit -m "feat(server): FastAPI /compile service + [server] extra"
```

---

### Task 7: Dockerfile + usage docs

**Files:**
- Create: `Dockerfile`
- Create: `docs/refrain-compiler.md`

**Interfaces:**
- Consumes: the `[server]` extra; `refrain.server:app`.
- Produces: a container running `uvicorn refrain.server:app`.

- [ ] **Step 1: Write the Dockerfile**

Create `Dockerfile`:
```dockerfile
# refrain-compiler service (Option A sub-project #2).
# Stateless build-time compiler sidecar — author-time, off the runtime hot path.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[server]"

EXPOSE 8000
# Trusted-network sidecar: do NOT expose this port publicly.
CMD ["uvicorn", "refrain.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write the usage doc**

Create `docs/refrain-compiler.md`:
```markdown
# refrain-compiler service

Wraps the canonical Python compiler as an HTTP service for the coherence-portal
(Option A sub-project #2). Stateless `compile in → IR out`; author-time only,
never on the runtime hot path. Trusted-network sidecar — **no auth; do not expose
publicly.**

## Run

    pip install ".[server]"
    uvicorn refrain.server:app --host 0.0.0.0 --port 8000

Or build the image:

    docker build -t refrain-compiler .
    docker run --rm -p 8000:8000 refrain-compiler

## Endpoints

- `POST /compile` — body `{ "refrain": "<source>", "sample_rate_hz": 250 }` →
  `{ ir_json, meta, errors }`. Compile errors return **200** with `ir_json: null`
  and a populated `errors[]` (located for resolve errors). Malformed request →
  **422**; emitted-IR schema failure (compiler bug) → **500**.
- `GET /healthz` — liveness.
- `GET /version` — compiler + supported IR/schema versions.

`meta` carries `refrain_version`, `ir_version`, `sample_rate_hz`, and
`content_hash` (`sha256:` over the canonical IR bytes). The portal must store and
serve those exact bytes verbatim for the program-signature integrity check.

## CLI (same compile path)

    refrain compile-json protocol.refrain --sample-rate 250        # prints IR-JSON
    refrain compile-json protocol.refrain --meta                   # prints compile metadata
```

- [ ] **Step 3: Verify the container builds and serves (requires Docker)**

```bash
docker build -t refrain-compiler . && \
  cid=$(docker run --rm -d -p 8000:8000 refrain-compiler) && sleep 3 && \
  curl -fs localhost:8000/healthz && echo && docker stop "$cid"
```
Expected: `{"status":"ok"}`.
If Docker is unavailable in this environment, instead verify the entrypoint imports and serves in-process:
```bash
python -c "from fastapi.testclient import TestClient; from refrain.server import app; print(TestClient(app).get('/healthz').json())"
```
Expected: `{'status': 'ok'}`.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docs/refrain-compiler.md
git commit -m "feat(server): Dockerfile + usage docs for refrain-compiler"
```

---

### Task 8: Full-suite regression + final review

**Files:** none (verification only).

- [ ] **Step 1: Run the entire test suite**

Run: `python -m pytest -q`
Expected: PASS, with the new `tests/test_compile_json.py`, `tests/test_compile_json_cli.py`, `tests/test_server.py` included and the relocated-schema tests still green. No regressions.

- [ ] **Step 2: Lint & type-check the whole new surface**

Run: `python -m ruff check src/refrain/compile_json.py src/refrain/server.py src/refrain/cli.py && python -m mypy src/refrain/compile_json.py src/refrain/server.py src/refrain/cli.py`
Expected: no errors.

- [ ] **Step 3: Confirm core install is unaffected by the web stack**

Run: `python -c "import refrain, refrain.compile_json; print('core import OK', refrain.__version__)"`
Expected: imports succeed without fastapi/uvicorn needing to be present (compile_json must not import server or fastapi at module load).

---

## Known limitations / follow-ups (out of scope for this plan)

- **Structured parse-error locations.** `ParseError` discards Lark's line/column (keeps only the message string), so parse `Diagnostic`s have `line/col = None`. Enriching `ParseError` to carry a `Loc` (and surfacing it here) is a follow-up that improves editor diagnostics. Spec §4.1's implication that parse errors carry locations is aspirational until then.
- **`extends` / parent protocols.** `compile_to_ir_json` calls `resolve(file_ast)` with no `parent_loader`, so a protocol using `extends` fails to resolve and surfaces as a Diagnostic. Cross-protocol resolution against the portal's store is a separate concern.
- **Batch multi-rate `/compile`.** One rate per request (the brief's contract); the portal loops. A `sample_rates: []` convenience is deliberately deferred (YAGNI).
- **Deploy/publish CI** for the image is a separate follow-up (the existing `release.yml` builds the wheel).

## Self-review

- **Spec coverage:** §2 placement → in-repo (Tasks 2–7). §3.1 compile path → Task 2/3/4. §3.3 CLI → Task 5. §4.1 contract incl. 200/422/500 → Task 6. §4.2 meta + content_hash → Task 2; integrity note → docs (Task 7). §4.3 aux endpoints → Task 6. §4.4 no auth → server docstring/docs. §5 validation + Option-B relocation → Task 1 (relocate) + Task 4 (validate). §6 packaging/Docker → Tasks 6–7. §7 tests → every task TDD + Task 8. §8 scope honored; §9 discrepancies surfaced in docs + this plan's limitations.
- **Placeholder scan:** none — every code/test step has complete content.
- **Type consistency:** `Diagnostic`/`CompileResult`/`compile_to_ir_json` signatures defined in Task 2 are used unchanged in Tasks 3–6; `_load_schema`/`_validate` defined and consumed in Task 4; `app`/`CompileRequest` defined and tested in Task 6.
