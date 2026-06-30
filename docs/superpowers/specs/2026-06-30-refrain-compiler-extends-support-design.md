# refrain-compiler `extends`/parent resolution — design spec (lean)

**Date:** 2026-06-30
**Status:** Approved design, ready for implementation plan
**Owner:** refrain team
**Builds on:** the merged `refrain-compiler` service (#52/#53)
**Deferred work:** Linear **PEA-256** (in-service standard `library/`, version hardening, DoS limits, + cross-team portal concerns)

## 1. Problem

The compile service resolves with no `parent_loader`, so a protocol that uses `extends`
fails to compile — it can only compile self-contained protocols. To compile *arbitrary*
clinician-authored protocols (the point of in-portal authoring), the service must resolve
parents. The composition machinery already exists (`src/refrain/compose.py`): a `ParentLoader`
callable feeds `compose()`, which runs as an AST pass before resolution; `resolve(file_ast,
parent_loader=…)` threads it in (`resolver.py:2287`). We are wiring that in, not building it.

**Scope decision (lean):** support only **caller-supplied parents** (a `parents` map on the
request) plus a filesystem `--library` for the CLI. The in-service bundled standard `library/`
is **deferred** (PEA-256) — no standard `library/` base set exists yet (0/58 catalog protocols
use `extends`), so bundling one now is a blind interface for a non-existent consumer.

## 2. Approach (Approach 1 — composite loader, caller-supplied)

`compile_to_ir_json` builds a **composite `ParentLoader`** and passes it to `resolve(…,
parent_loader=…)`. The loader resolves a ref by trying the **in-memory map first**
(request-supplied parents), then the **filesystem library dirs** (used by the CLI). A ref that
resolves in neither raises a distinct *not-found* signal, surfaced as recoverable data
(`unresolved_parents`) rather than an error.

## 3. Core — `src/refrain/compile_json.py`

### 3.1 Signature
```python
def compile_to_ir_json(
    source: str, *, sample_rate_hz: float | None = None, validate: bool = True,
    parents: dict[str, str] | None = None,      # ref -> parent source (in-memory)
    library_dirs: list[str] | None = None,      # filesystem search dirs (CLI)
) -> CompileResult
```

### 3.2 Composite loader
A small loader (built in this module) — *not* the stock `filesystem_loader`, because we must
distinguish **not-found** (recoverable) from **malformed parent** (an error), which the stock
loader collapses into one `ComposeError`:
```python
class _ParentNotFound(Exception):
    def __init__(self, ref: str): self.ref = ref; super().__init__(ref)

def _build_loader(parents, library_dirs) -> ParentLoader:
    pmap = parents or {}
    dirs = [Path(d) for d in (library_dirs or [])]
    def loader(ref: str) -> A.File:
        path, _ = parse_ref(ref)                 # strip @version, like filesystem_loader
        if path in pmap:
            return parse(pmap[path])             # ParseError -> wrapped (see 3.3)
        for d in dirs:
            cand = d / f"{path}.refrain"
            if cand.exists():
                return parse_file(cand)          # ParseError -> wrapped
        raise _ParentNotFound(ref)
    return loader
```
In-memory takes precedence over filesystem. `parse_ref` (from `compose.py`) strips the
`@version` so map keys/file names are version-agnostic (the portal keys `parents` by the ref
*path*, not `name@ver`); the composer applies its own version check.

**Always return a loader — never `None`** (even when `parents` and `library_dirs` are both
empty). A standalone protocol never invokes it (the composer early-returns when `extends` is
`None`), and a child with `extends` and nothing supplied then cleanly raises `_ParentNotFound`
→ `unresolved_parents` — instead of hitting the composer's "no loader configured" `ComposeError`
(which would wrongly read as a compose *error* on the portal's first, parents-less call).

### 3.3 Outcome mapping (`compile_to_ir_json` body)
Parse the child, **capture `file_ast.protocol.extends`** (the original ref, before composition
flattens it), then `resolve(file_ast, parent_loader=loader)` and map exceptions:

| Raised | Meaning | Result |
|---|---|---|
| `ParseError` | child won't parse | `Diagnostic(stage="parse")`, `ir_json=None` |
| `_ParentNotFound(ref)` | a parent isn't supplied / on the path | `unresolved_parents=[ref]`, `ir_json=None`, **no error** |
| `ComposeError` | cycle / illegal `amend`/`remove` / `final` violation / version mismatch / parent parse-fail | `Diagnostic(stage="compose", line/col from `.loc`)`, `ir_json=None` |
| `ResolveError` | child (post-merge) fails resolution | `Diagnostic(stage="resolve")`, `ir_json=None` |
| (none) | success | `ir_json` populated, `unresolved_parents=[]` |

Parent parse failures are wrapped as `ComposeError` so they read as a `compose` diagnostic, not
a silent miss.

### 3.4 New result fields
```python
@dataclass(frozen=True)
class CompileResult:
    ir_json: dict | None
    ir_json_text: str | None
    meta: dict                       # gains "extends": <ref> | None
    errors: list[Diagnostic]
    schema_error: str | None = None
    unresolved_parents: list[str] = field(default_factory=list)
```
- `meta["extends"]` — the protocol's original parent ref (or `None`). Lets the portal build its
  dependency graph from stored metadata and pre-assemble closures. Present on every outcome
  where the child parsed (i.e. `None` only on parse failure or genuinely standalone).
- `unresolved_parents` — refs the loader couldn't resolve. **Single inheritance is linear**, so
  this surfaces the *next* missing ref (length ≤ 1 in practice); kept a list for forward-compat.

## 4. HTTP — `src/refrain/server.py`

Request model gains `parents`:
```python
class CompileRequest(BaseModel):
    refrain: str
    sample_rate_hz: float | None = None
    parents: dict[str, str] | None = None
```
The endpoint passes `parents=req.parents` (and **no** `library_dirs` — the bundled library is
deferred). Response gains `unresolved_parents`; `meta` already carries `extends`:
```json
{ "ir_json": …|null, "ir_json_text": …|null,
  "meta": { …, "extends": "my_custom_base"|null },
  "unresolved_parents": ["<ref>"], "errors": [] }
```
**All three outcomes are HTTP 200** (resolved / unresolved-parent / compile-or-compose
diagnostic). 422 (malformed request) and 500 (schema-validation bug) are unchanged. A
composition error is *not* recoverable by supplying parents; an `unresolved_parents` response
*is* — the portal supplies the named parent(s) and retries (or pre-supplies the closure from its
`meta.extends` graph, avoiding round-trips).

## 5. CLI — `src/refrain/cli.py`

`refrain compile-json` gains `--library DIR` (repeatable), mirroring `refrain resolve`/`cost`:
```
refrain compile-json child.refrain --sample-rate 250 --library ./lib
```
It builds `library_dirs = args.library + default_library_dirs()` and passes them (no `parents`
map). The CLI is filesystem-only and has no negotiation: a `unresolved_parents` result is a
**hard error** (print `error: <path>: unresolved parent: <ref>`, exit 1), consistent with the
existing commands.

## 6. Testing (TDD)

Unit (`compile_to_ir_json`):
- child `extends` a **request-supplied** parent → success; merged IR reflects parent+child (e.g.
  a parent-defined derive survives, a child `amend` overrides).
- child `extends` a **filesystem** parent (fixture lib dir) → success.
- child `extends` a **missing** parent → `unresolved_parents=[ref]`, `ir_json=None`, `errors=[]`.
- 2-level chain, middle supplied, grandparent missing → reports the grandparent ref.
- **composition error** (child `amend`s a decl absent in parent, or a cycle) → `compose`
  Diagnostic, `unresolved_parents=[]`.
- supplied parent that won't parse → `compose` Diagnostic (not a silent miss).
- `meta["extends"]` carries the original ref; `None` for a standalone protocol.
- a composed protocol's `content_hash` / `ir_json_text` are deterministic and self-consistent.

HTTP (`TestClient`):
- `/compile` with `parents` resolving the child → 200, `ir_json` populated, `unresolved_parents=[]`.
- `/compile` with a missing parent → 200, `ir_json=null`, `unresolved_parents=[ref]`.
- `meta.extends` present in the body.

CLI:
- `compile-json --library <fixture dir>` resolves a filesystem parent → exit 0, IR printed.
- missing parent → exit 1, stderr names the unresolved ref.

All new code: strict mypy, ruff `E,F,W,I,N,B,UP,PL`, line-length 100.

## 7. Scope

**In:** composite caller-supplied/filesystem loader in `compile_to_ir_json`; `parents`,
`unresolved_parents`, `meta.extends` contract; CLI `--library`; the tests above.

**Out (→ PEA-256):** in-service bundled standard `library/` (image bundling + `REFRAIN_LIBRARY_PATH`
wiring + `/version` surfacing); tighter version-constraint enforcement; `parents`-map DoS limits.

**Out (→ portal team, flagged on PEA-256):** closure-aware IR caching (a parent edit changes
every child's `content_hash`); ref→protocol resolution + tenant scoping; "supply the correct
parent" as a clinical-correctness responsibility (the service cannot verify a parent is the
right one).

## 8. Premise (assumed, not yet confirmed)

This is only exercised if the authoring/editor path **emits** `extends` (vs rendering
self-contained `.refrain`). We proceed on the assumption that protocol inheritance will be in
play; if the editor always flattens, this path is unused. Tracked on PEA-256.
