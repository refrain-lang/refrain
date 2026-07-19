# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""HTTP face over the canonical compiler (Option A sub-project #2).

Stateless: `compile in -> IR out`. Trusted-network sidecar — must not be
exposed publicly (no auth). Requires the `[server]` extra (fastapi/uvicorn).
Run with: `uvicorn refrain.server:app`.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import __version__
from .compile_json import compile_to_ir_json

app = FastAPI(title="refrain-compiler", version=__version__)


class CompileRequest(BaseModel):
    refrain: str
    sample_rate_hz: float | None = None
    parents: dict[str, str] | None = None
    # Resolve-time control bindings (mode/placement), passed verbatim to the
    # compiler and echoed back in `meta.bindings`. The echo is a capability
    # gate for callers: pydantic drops unknown fields, so a caller talking to
    # an older image would otherwise get default-variant IR with no signal
    # that its bindings were ignored.
    bindings: dict[str, Any] | None = None
    # Bundled amp-profile NAME (e.g. "brainbit_flex", "q21", "openbci_cyton").
    # When set, the protocol resolves against that profile so
    # `reference: amp.reference` folds to the device's real reference and the
    # site is validated against the amp's channels. Echoed back as `meta.amp`
    # (a capability probe, like `meta.bindings`): an older image drops this
    # field, so only a positive echo proves the amp was applied. An unknown
    # name is a typed 400 (bad request parameter), never a 500.
    amp: str | None = None


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, Any]:
    return {
        "refrain_version": __version__,
        "ir_versions_supported": ["0.1", "0.2", "0.3"],
        "schema_versions": ["0.1", "0.2", "0.3"],
    }


@app.post("/compile")
def compile_endpoint(req: CompileRequest) -> dict[str, Any]:
    result = compile_to_ir_json(
        req.refrain,
        sample_rate_hz=req.sample_rate_hz,
        parents=req.parents,
        bindings=req.bindings,
        amp=req.amp,
    )
    # A bad request parameter (an unbundled amp name) is a typed 4xx, distinct
    # from a protocol that fails to compile (200 + diagnostics, below) and from
    # a compiler bug (500).
    if result.request_error is not None:
        raise HTTPException(status_code=400, detail=result.request_error)
    if result.schema_error is not None:
        raise HTTPException(status_code=500, detail=result.schema_error)
    return {
        "ir_json": result.ir_json,
        "ir_json_text": result.ir_json_text,
        "meta": result.meta,
        "errors": [asdict(d) for d in result.errors],
        "unresolved_parents": result.unresolved_parents,
    }
