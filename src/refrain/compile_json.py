# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Compile `.refrain` source to IR-JSON — the one path the CLI and the
HTTP service both call. Wraps `parse -> resolve(amp=...) -> ir_to_json_obj`,
attaches compile metadata (version, ir version, content hash), and (later)
validates the emitted IR-JSON against the bundled schema.

`amp` names a bundled amplifier profile (e.g. `"brainbit_flex"`); when set, the
protocol resolves against it so `reference: amp.reference` folds to the device's
real reference and site availability is checked. Omitted, resolution runs with
`amp=None` exactly as before, so every literal-reference protocol is unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import __version__
from .amp_profile import AmpProfile, load_amp_profile
from .ast import File
from .compose import ComposeError, ParentLoader, compose, parse_ref
from .ir_json import effective_channels, ir_to_json_obj
from .parser import ParseError, parse, parse_file
from .resolver import ResolveError, resolve

# Bundled amp profiles ship beside this module (src/refrain/amp_profiles/*.json).
_AMP_PROFILE_DIR = Path(__file__).resolve().parent / "amp_profiles"


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

    `request_error` is set only when a *request parameter* is invalid — today,
    an `amp` name the service does not bundle. That is a caller/version-skew
    error, not a protocol that fails to compile, so the HTTP layer maps it to a
    typed 4xx (distinct from the 200 + `errors` diagnostics a bad protocol gets,
    and from the 500 a compiler bug gets). It carries the typed detail body.

    `ir_json_text` is the verbatim canonical serialization that `content_hash`
    covers (`json.dumps(ir_json, indent=2)`). Non-Python consumers (the Go
    portal, the browser editor) cannot reproduce Python's serialization
    byte-for-byte, so they must forward *these* bytes unchanged for the
    `sha256(bytes) == content_hash` integrity check to hold. None on error.
    """

    ir_json: dict[str, Any] | None
    meta: dict[str, Any]
    errors: list[Diagnostic]
    schema_error: str | None = None
    request_error: dict[str, Any] | None = None
    ir_json_text: str | None = None
    unresolved_parents: list[str] = field(default_factory=list)


def _content_hash(canonical: str) -> str:
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@cache
def _bundled_amp_names() -> frozenset[str]:
    """Names of the amp profiles bundled with the package (files, sans `.json`).

    The membership set for the `amp` request field: a name is accepted only if
    it is one of these. Because the name is checked against this set and never
    joined onto a path, `..`, path separators, and absolute paths are rejected
    as unknown names — there is no traversal surface.
    """
    return frozenset(p.stem for p in _AMP_PROFILE_DIR.glob("*.json"))


class _ParentNotFoundError(Exception):
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
    supplied then yields `_ParentNotFoundError` rather than the composer's
    "no loader configured" error. Parent parse failures become ComposeError.
    """
    pmap = parents or {}
    dirs = [Path(d) for d in (library_dirs or [])]

    def loader(ref: str) -> File:
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
        raise _ParentNotFoundError(ref)

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


def compile_to_ir_json(  # noqa: PLR0911 — linear compile pipeline: one guard-clause return per outcome
    source: str,
    *,
    sample_rate_hz: float | None = None,
    validate: bool = True,
    parents: dict[str, str] | None = None,
    library_dirs: list[str] | None = None,
    bindings: dict[str, object] | None = None,
    amp: str | None = None,
) -> CompileResult:
    # `meta["bindings"]` echoes the applied bindings verbatim ({} when
    # omitted). Consumers use it as a capability gate: an older service that
    # ignored `bindings` would return default-variant IR, and only a positive
    # echo proves the requested variant is the one that was baked.
    #
    # `meta["amp"]` is the same kind of probe for the amp profile: the resolved
    # profile name (or None when none was requested). An older image drops the
    # request field and returns fail-closed / literal-only IR with no echo, so a
    # caller reads the echo to confirm its `amp.reference` protocol was resolved
    # against the device it asked for.
    applied_bindings = dict(bindings or {})
    base_meta: dict[str, Any] = {
        "refrain_version": __version__,
        "ir_version": None,
        "sample_rate_hz": sample_rate_hz,
        "content_hash": None,
        "extends": None,
        "bindings": applied_bindings,
        "amp": amp,
        "seeds": [],
    }

    # Resolve the amp *name* to a bundled profile up front. An amp the service
    # does not bundle is a bad request parameter (a caller or version-skew
    # error), surfaced as a typed 4xx — not a 200 protocol diagnostic and not a
    # 500. The name is validated against the bundled set (see `_bundled_amp_names`);
    # a valid one is loaded and passed to `resolve()`.
    amp_profile: AmpProfile | None = None
    if amp is not None:
        if amp not in _bundled_amp_names():
            return CompileResult(
                None,
                base_meta,
                [],
                request_error={
                    "error": "unknown_amp",
                    "amp": amp,
                    "allowed": sorted(_bundled_amp_names()),
                },
            )
        amp_profile = load_amp_profile(_AMP_PROFILE_DIR / f"{amp}.json")

    try:
        file_ast = parse(source)
    except ParseError as exc:
        return CompileResult(None, base_meta, [Diagnostic("parse", str(exc))])

    base_meta["extends"] = file_ast.protocol.extends
    loader = _build_loader(parents, library_dirs)

    # Compose explicitly so a composition failure stays a `compose` diagnostic:
    # `resolve()` re-raises ComposeError as ResolveError to unify error types.
    try:
        composed = compose(file_ast, loader)
    except _ParentNotFoundError as exc:
        return CompileResult(None, base_meta, [], unresolved_parents=[exc.ref])
    except ComposeError as exc:
        return CompileResult(None, base_meta, [_located("compose", exc)])

    try:
        ir = resolve(composed, amp=amp_profile, bindings=bindings)
    except ResolveError as exc:
        return CompileResult(None, base_meta, [_located("resolve", exc)])

    # A protocol that names no electrode — neither in `requires.channels` nor
    # in any montage — gives the runtime nothing to acquire, and the emitted
    # `channels` would trip the schema's minItems. That is an authoring gap,
    # not a compiler bug, so report it as a diagnostic rather than letting it
    # reach `schema_error` (which the HTTP layer maps to an opaque 500).
    if not effective_channels(ir):
        return CompileResult(
            None,
            base_meta,
            [
                Diagnostic(
                    stage="resolve",
                    message=(
                        "protocol names no channels: declare `requires.channels` "
                        "or name an electrode in an input montage"
                    ),
                )
            ],
        )

    obj = ir_to_json_obj(ir, sample_rate_hz=sample_rate_hz)
    canonical = json.dumps(obj, indent=2)
    seeds = sorted(
        c.canonical_name.removeprefix("control/")
        for c in ir.controls.values()
        if c.seed is not None
    )
    meta = {
        "refrain_version": __version__,
        "ir_version": obj["refrain_ir_version"],
        "sample_rate_hz": obj["sample_rate_hz"],
        "content_hash": _content_hash(canonical),
        "extends": file_ast.protocol.extends,
        "bindings": applied_bindings,
        "amp": amp,
        "seeds": seeds,
    }
    schema_error = _validate(obj) if validate else None
    return CompileResult(
        ir_json=obj,
        meta=meta,
        errors=[],
        schema_error=schema_error,
        ir_json_text=canonical,
    )
