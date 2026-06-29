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
from functools import cache
from importlib.resources import files
from typing import Any

from . import __version__
from .ir_json import ir_to_json_obj
from .parser import ParseError, parse
from .resolver import ResolveError, resolve


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

    ir_json: dict[str, Any] | None
    meta: dict[str, Any]
    errors: list[Diagnostic]
    schema_error: str | None = None


def _content_hash(canonical: str) -> str:
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@cache
def _load_schema(version: str) -> dict[str, Any]:
    """Load the bundled IR-JSON schema for `version` (e.g. "0.1").

    Raises FileNotFoundError if no schema ships for that version.
    """
    resource = files("refrain") / "schema" / f"ir-json-v{version}.schema.json"
    schema: dict[str, Any] = json.loads(resource.read_text())
    return schema


def _validate(obj: dict[str, Any]) -> str | None:
    """Validate emitted IR-JSON against its bundled schema.

    Returns an error string when the compiler produced non-conformant IR
    (a compiler bug), else None. Returns None (skips) when `jsonschema` is
    not installed — the core install carries the schema files but not the
    validator; the [server] extra adds it.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]  # noqa: PLC0415
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


def compile_to_ir_json(
    source: str, *, sample_rate_hz: float | None = None, validate: bool = True
) -> CompileResult:
    base_meta: dict[str, Any] = {
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
    schema_error = _validate(obj) if validate else None
    return CompileResult(ir_json=obj, meta=meta, errors=[], schema_error=schema_error)
