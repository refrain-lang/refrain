# refrain-compiler service — design spec

**Date:** 2026-06-28
**Status:** Approved design, ready for implementation plan
**Owner:** refrain team
**Worktree/branch:** `refrain-compiler-service`

## 1. Context & problem

The coherence-portal "Option A" architecture brief
(`coherence-portal/.../docs/integration/2026-06-28-in-portal-protocol-authoring-architecture.md`)
decomposes in-portal protocol authoring into three sub-projects. **Sub-project #2 — a
packaged `refrain-compiler` service — is the refrain team's deliverable and the
critical-path cross-team dependency.** Without it there is no IR-JSON for arbitrary
protocols, so the portal cannot serve compiled protocols to the phone and the editor
has nothing to validate against.

The portal is Go and per ADR-3 never compiles `.refrain`; it stores and serves opaque
artifacts. The phone runs compiled IR-JSON via the Rust `refrain-core` runtime. The
**only** thing that compiles `.refrain` → IR-JSON is the Python `refrain` compiler.
This spec covers wrapping that canonical compiler as a service the portal can call.

A Go/WASM reimplementation of the compiler was rejected by the brief: a second
filter-design implementation is a numerical-parity hazard on the highest-risk part of
the system. Staying the single canonical compiler is the entire point — so this service
is a thin face over the existing `parse → resolve → ir_to_json_obj` pipeline, never a
fork.

## 2. Decision: in the `refrain` repo, as an optional `[server]` extra

The service lives **inside this repo**, not a separate one. It imports the exact
`parse`/`resolve`/`ir_to_json_obj` it ships, so it is the canonical compiler by
construction (no fork, one version, one CI, one parity fuzzer). All web dependencies
live behind a `[server]` optional-dependencies group, so `pip install refrain` is
unchanged and only `pip install refrain[server]` (and the Docker image) pull the web
stack.

The condition that would justify a separate repo — an *operational* need for an
independent deploy cadence/owner/access-control — does not currently apply; the brief
leaves packaging to the refrain team.

## 3. Architecture & layering

Three layers, **one** compile path:

```
refrain compile-json (CLI)  ─┐
                             ├─►  refrain.compile_json.compile_to_ir_json()   ← the ONE compile path
POST /compile (FastAPI)     ─┘         parse → resolve(amp=None) → ir_to_json_obj(sr) → validate → hash
```

### 3.1 `src/refrain/compile_json.py` — the shared compile path (no web deps)

Pure, importable, dependency-light (lark/numpy/scipy only — already core deps).

```python
@dataclass(frozen=True)
class Diagnostic:
    stage: str        # "parse" | "resolve"
    message: str
    severity: str     # "error" (warnings reserved for future)
    line: int | None
    col: int | None
    end_line: int | None
    end_col: int | None

@dataclass(frozen=True)
class CompileResult:
    ir_json: dict | None          # ir_to_json_obj output, or None if errors
    meta: dict                    # compile metadata (see §4.2)
    errors: list[Diagnostic]      # empty on success

def compile_to_ir_json(source: str, *, sample_rate_hz: float | None = None) -> CompileResult:
    ...
```

Behavior:
- `parse(source)` → on `ParseError`, return a `CompileResult` with `ir_json=None` and one
  `Diagnostic(stage="parse", ...)`. Pull `Loc` from the error where available.
- `resolve(ast, amp=None)` → on `ResolveError`, same shape with `stage="resolve"`,
  populating `line/col/end_line/end_col` from `ResolveError.loc`.
- `ir_to_json_obj(ir, sample_rate_hz=...)` → the IR-JSON dict (already stamps
  `refrain_ir_version` and `sample_rate_hz`).
- Validate the emitted IR-JSON against the bundled schema (§5). Validation failure is a
  **compiler bug**, surfaced distinctly (the HTTP layer maps it to 500), never a normal
  `Diagnostic`.
- Compute `content_hash` and assemble `meta` (§4.2).

This is also the phase-1 "manual producer" the brief assumes exists (it referred to a
non-existent `compile-nf-protocol.py`).

### 3.2 `src/refrain/server.py` — FastAPI app (behind `[server]`)

Thin I/O glue over `compile_json`. No compile logic. Pydantic models for request/response
give wire-contract validation + OpenAPI for free; `TestClient` makes HTTP tests trivial.

### 3.3 CLI — `refrain compile-json`

New subcommand in `cli.py`: `refrain compile-json <file> [--sample-rate HZ] [--meta]`.
Prints IR-JSON to stdout; `--meta` emits the sidecar metadata; non-zero exit + diagnostics
to stderr on compile errors. Calls `compile_to_ir_json` — identical path to the server.

**Framework choice:** FastAPI + uvicorn. Considered Flask (lighter but hand-rolled
validation) and stdlib `http.server` (too bare for a cross-team contract). Rejected both
in favor of typed request/response models and OpenAPI.

## 4. HTTP contract

### 4.1 `POST /compile`

**Request:**
```json
{ "refrain": "<source text>", "sample_rate_hz": 250 }
```
`sample_rate_hz` is optional; omitted → the resolver's chosen rate. One rate per request
(the brief's contract; the portal loops over its target rates). A batch `sample_rates: []`
convenience is explicitly out of scope (YAGNI) and noted as a possible later add.

**Response 200 — success:**
```json
{
  "ir_json": { "refrain_ir_version": "0.1", "name": "...", "sample_rate_hz": 250, ... },
  "meta": {
    "refrain_version": "0.11.0",
    "ir_version": "0.1",
    "sample_rate_hz": 250,
    "content_hash": "sha256:<hex>"
  },
  "errors": []
}
```

**Response 200 — compile diagnostics (program has errors):**
```json
{
  "ir_json": null,
  "meta": { "refrain_version": "0.11.0", "ir_version": null, "sample_rate_hz": 250, "content_hash": null },
  "errors": [
    { "stage": "resolve", "message": "unknown stream 'thetaa'",
      "line": 12, "col": 5, "end_line": 12, "end_col": 11, "severity": "error" }
  ]
}
```

**Decision — compile errors are data, not HTTP failures.** A `.refrain` with a type/parse
error returns **200** with `ir_json: null` and populated `errors[]`. This is what makes
live editor validation clean: the HTTP request succeeded; the *program* has diagnostics.
Reserved status codes:
- **422** — malformed request (missing `refrain`, non-JSON body, wrong types). Handled by
  Pydantic/FastAPI.
- **500** — unexpected server fault, including the should-never-happen case of emitted
  IR-JSON failing schema validation (a compiler bug).

### 4.2 `meta` (compile metadata)

Distinct from the IR-JSON's internal `meta` (protocol metadata). Contains what the portal
needs to store an artifact row and bind the program signature:
- `refrain_version` — compiler package version (`0.11.0`).
- `ir_version` — echoed from `ir_json.refrain_ir_version` (auto-selected 0.1/0.2 by
  `_protocol_ir_version`); `null` when compilation failed.
- `sample_rate_hz` — the rate the IR was baked at.
- `content_hash` — `"sha256:<hex>"` over the **canonical IR byte form** the service
  defines: `ir_to_json(ir, sample_rate_hz=...)` output (`json.dumps(..., indent=2)`,
  insertion order preserved — deterministic for fixed input), UTF-8 encoded. `null` when
  compilation failed.

  **Integrity caveat (cross-team):** the brief (§4.1) binds this hash into the program
  signature so the phone verifies the exact IR it runs. That loop only closes if every hop
  hashes the *same* bytes. Because the portal is Go and will not re-run Python
  `ir_to_json`, the portal must **persist and serve the canonical bytes verbatim** (base64
  of exactly the form the service hashed) rather than re-serialize the parsed object. The
  response therefore treats this canonical serialization — not the convenience `ir_json`
  object — as the authoritative artifact. Flagged for cross-team confirmation (§9.4).

### 4.3 Aux endpoints
- `GET /healthz` → `200 {"status":"ok"}` — liveness for Compose/k8s.
- `GET /version` → `{ "refrain_version": "0.11.0", "ir_versions_supported": ["0.1","0.2"],
  "schema_versions": ["0.1","0.2"] }` — for ops and the editor.

### 4.4 Auth
None. Trusted-network sidecar (the brief's model). Documented: must not be exposed
publicly. Authentication is YAGNI for this increment.

## 5. Output validation & schema packaging

Validate every emitted IR-JSON against the bundled JSON-schema before returning, using
`jsonschema.Draft202012Validator`, selecting the schema file by the stamped
`refrain_ir_version` (0.1 or 0.2). A failure indicates the compiler produced
non-conformant IR → treated as a server fault (500), never a user `Diagnostic`. This is a
safety net that should never fire in normal operation.

**Packaging requirement:** the schema files currently live at
`refrain-core/schema/ir-json-v{0.1,0.2}.schema.json`, outside the importable package, so
they are not available at runtime today. The implementation must ship them as package data
(hatch `force-include`/`shared-data`, or copy into `src/refrain/schema/`) and load them via
`importlib.resources`. `jsonschema` moves into the `[server]` extra (and stays in `dev`).

## 6. Packaging & deployment

- `pyproject.toml`:
  `[project.optional-dependencies] server = ["fastapi", "uvicorn[standard]", "jsonschema >= 4.0"]`.
- `Dockerfile`: `pip install .[server]`; entrypoint runs `uvicorn refrain.server:app`.
  One service in the portal's Docker Compose, identical cloud and self-host (brief §5).
- Worker model (brief §6): stateless `compile in → IR out`, CPU-bound; scale with N
  uvicorn workers (scipy releases the GIL during DSP). Off the runtime hot path.

## 7. Testing (TDD)

- **Unit — `compile_to_ir_json`:** success path; parse error → located `Diagnostic`;
  resolve error → located `Diagnostic`; explicit vs default `sample_rate_hz`;
  `content_hash` determinism (same input → same hash; different rate → different hash); a
  v0.2-feature protocol stamps `ir_version == "0.2"`.
- **Parity / no-drift:** compile an existing golden `.refrain` and assert the service's
  `ir_json` equals the canonical `ir_to_json_obj` output / existing IR-JSON fixture —
  proves the service has not diverged from the compiler.
- **Schema validation:** emitted IR-JSON validates against the bundled schema for a
  representative protocol set.
- **HTTP (`TestClient`):** 200-with-ir; 200-with-errors (`ir_json: null`); 422 malformed;
  `GET /healthz`; `GET /version`.

All new code conforms to the repo bar: strict mypy, ruff (`E,F,W,I,N,B,UP,PL`),
line-length 100.

## 8. Scope

**In scope (this build):** the shared `compile_to_ir_json` function; the `refrain
compile-json` CLI subcommand; the FastAPI service (`POST /compile`, `/healthz`,
`/version`); schema packaging; the `[server]` extra; the Dockerfile; the test suite above.

**Out of scope (per brief §9 + ownership):**
- Sub-project #1 — portal IR store + delivery (portal team owns).
- Sub-project #3 — in-portal editor UI (WS-8 owns).
- A batch multi-rate `/compile` variant (YAGNI; possible later add).
- Authentication on `/compile`.
- A deploy/publish CI pipeline for the image (separate follow-up; the refrain
  `release.yml` already builds the wheel).
- Any change to the recorder's local compile+run path.

## 9. Discrepancies to flag back to the cross-team

These are stale points in the brief, surfaced so the cross-team contract stays accurate:
1. **IR version.** The brief pins IR-JSON **v0.1**; the repo already has a **v0.2** schema,
   and the emitter auto-selects 0.1/0.2 per protocol features. The contract must treat
   `ir_version` as a returned, per-artifact value (the brief's artifact row already has the
   field) — not a fixed "v0.1." Mobile/recorder should confirm the phone runtime accepts
   both.
2. **The sidecar does not need the `refrain_core` wheel for compilation.** Brief §5 lists it
   in the sidecar bundle; compilation needs only lark/numpy/scipy. `refrain_core` is the
   runtime/parity dependency, not a compile dependency. Dropping it shrinks the image.
3. **`compile-nf-protocol.py` does not exist.** The brief's phase-1 "manual producer" leans
   on a script that was never written; `refrain compile-json` (this spec) is that producer.
4. **Canonical-bytes integrity.** For the brief's signature-binding (§4.1) to verify the
   exact IR the phone runs, the portal must store/serve the service's canonical IR bytes
   verbatim (§4.2 integrity caveat), not a re-serialized form. Confirm the portal persists
   the bytes as received.

## 10. Cross-team asks this unblocks

- (brief §8a) refrain team owns + packages `refrain-compiler` — **this spec.**
- (brief §8b) confirm the IR artifact + delivery contract — the `meta` block here
  (`ir_version`, `content_hash`, `sample_rate_hz`) is the producer side of that contract.
- (brief §8c) mobile + recorder confirm the sample-rate variants to compile — the service
  compiles whatever rate it's asked for; the *set* of rates is the portal's to drive.
