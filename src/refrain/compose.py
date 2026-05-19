# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Protocol composition: SPEC §11.

Composition runs as an AST pass *before* resolution. Given a child
protocol that `extends` a parent reference, the composer:

  1. Loads the parent via a `ParentLoader` callable.
  2. Recursively composes the parent (it may also extend something).
  3. Merges the parent's body with the child's, applying SPEC §11.1's
     per-section semantics.
  4. Honors `final = true` body fields on parent named decls — children
     cannot amend or remove them (SPEC §11.4).
  5. Emits a merged `File` AST with `extends = None`. The existing
     resolver then runs on this merged AST unchanged.

The composer is loader-agnostic: tests pass an in-memory map; the CLI
passes a filesystem loader. This keeps the composer pure and testable.

Library-path resolution conventions are documented in
`docs/DESIGN-NOTES.md` §7.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol as TypingProtocol

from . import ast as A
from .parser import ParseError, parse_file


# ---------------------------------------------------------------------------
# Errors and types
# ---------------------------------------------------------------------------


class ComposeError(Exception):
    """Raised on any composition failure (missing parent, cycle, illegal
    amend/remove, version mismatch)."""

    def __init__(self, message: str, loc: A.Loc | None = None):
        self.loc = loc
        if loc is not None:
            super().__init__(f"line {loc.line}:{loc.col}: {message}")
        else:
            super().__init__(message)


class ParentLoader(TypingProtocol):
    """Resolves a protocol reference (e.g. `"library/foo@1.2"`) to a `File`.

    Implementations must raise `ComposeError` on a missing or unparseable
    parent.
    """

    def __call__(self, ref: str) -> A.File: ...


# ---------------------------------------------------------------------------
# Filesystem-backed loader
# ---------------------------------------------------------------------------


def parse_ref(ref: str) -> tuple[str, str | None]:
    """Split `"library/foo/bar@1.2"` into (path="library/foo/bar", version="1.2").

    A ref with no `@` returns version=None.
    """
    if "@" in ref:
        path, version = ref.rsplit("@", 1)
        return path, version
    return ref, None


def filesystem_loader(library_dirs: list[Path | str]) -> ParentLoader:
    """Build a loader that resolves protocol refs against a search path.

    Convention: a ref like `"library/foo/bar"` resolves to the first
    existing `<dir>/library/foo/bar.refrain` across `library_dirs`. The
    "library/" prefix in the ref is treated as a literal path component
    so that protocol packs live under a `library/` subdirectory in the
    search root by convention.

    The version constraint (the `@1.2` part of the ref) is NOT enforced
    at load time; the composer applies the SPEC §11.5 compatibility
    check after parsing.
    """
    dirs = [Path(d) for d in library_dirs]

    def loader(ref: str) -> A.File:
        path, _ = parse_ref(ref)
        for d in dirs:
            candidate = d / f"{path}.refrain"
            if candidate.exists():
                try:
                    return parse_file(candidate)
                except ParseError as exc:
                    raise ComposeError(
                        f"parent {ref!r} at {candidate}: parse failed: {exc}"
                    ) from exc
        searched = ", ".join(str(d) for d in dirs) or "(no search directories)"
        raise ComposeError(
            f"cannot resolve parent {ref!r}: searched {searched}"
        )

    return loader


def default_library_dirs() -> list[Path]:
    """Library search path from the `REFRAIN_LIBRARY_PATH` env var.

    Format: `:`-separated list of directories, like `PATH`. Empty
    components are dropped.
    """
    raw = os.environ.get("REFRAIN_LIBRARY_PATH", "")
    return [Path(p) for p in raw.split(os.pathsep) if p]


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def compose(file: A.File, loader: ParentLoader | None) -> A.File:
    """Apply `extends` / `amend` / `remove` / `final` semantics.

    If the protocol has no `extends`, returns the input unchanged. If it
    does and `loader` is None, raises `ComposeError` with a hint pointing
    at the library-path configuration. Recursively composes parents
    along the inheritance chain, detecting cycles.
    """
    return _compose_with_stack(file, loader, stack=())


def _compose_with_stack(
    file: A.File,
    loader: ParentLoader | None,
    *,
    stack: tuple[str, ...],
) -> A.File:
    proto = file.protocol
    if proto.extends is None:
        return file
    parent_ref = proto.extends
    if parent_ref in stack:
        chain = " -> ".join(list(stack) + [parent_ref])
        raise ComposeError(f"cycle in extends chain: {chain}", loc=proto.loc)
    if loader is None:
        raise ComposeError(
            f"protocol extends {parent_ref!r} but no parent loader is configured "
            f"(supply --library DIR or set REFRAIN_LIBRARY_PATH)",
            loc=proto.loc,
        )

    parent_file = loader(parent_ref)
    parent_composed = _compose_with_stack(parent_file, loader, stack=(*stack, parent_ref))

    _check_version_compat(parent_composed, parent_ref, child=file)

    return _merge(parent_composed, file)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def _merge(parent: A.File, child: A.File) -> A.File:
    """Merge child's body onto parent's. Returns a new `File` AST.

    Ordering: parent's body items stay in their original positions;
    child replacements / amends happen in place at the parent's
    position; new child decls append at the end. This preserves the
    SPEC §5.4 source-order invariant the resolver relies on.
    """
    parent_body = list(parent.protocol.body)

    # Index parent's named decls and section blocks.
    parent_named: dict[tuple[str, str], int] = {}
    parent_sections: dict[str, int] = {}
    final_set: set[tuple[str, str]] = set()

    for i, stmt in enumerate(parent_body):
        if isinstance(stmt, A.NamedDecl):
            parent_named[(stmt.keyword, stmt.name)] = i
            if _has_final_true(stmt):
                final_set.add((stmt.keyword, stmt.name))
        elif isinstance(stmt, A.SectionBlock):
            parent_sections[stmt.keyword] = i

    merged: list[A.Statement] = list(parent_body)
    to_remove_indices: set[int] = set()

    for stmt in child.protocol.body:
        if isinstance(stmt, A.NamedDecl):
            key = (stmt.keyword, stmt.name)
            if key in final_set:
                raise ComposeError(
                    f"cannot redeclare final {stmt.keyword} \"{stmt.name}\" from parent",
                    loc=stmt.loc,
                )
            if key in parent_named:
                merged[parent_named[key]] = stmt
            else:
                merged.append(stmt)
        elif isinstance(stmt, A.SectionBlock):
            if stmt.keyword in parent_sections:
                parent_section = merged[parent_sections[stmt.keyword]]
                if stmt.keyword in _FIELD_MERGE_SECTIONS:
                    merged[parent_sections[stmt.keyword]] = _merge_section_fields(
                        parent_section, stmt  # type: ignore[arg-type]
                    )
                else:
                    merged[parent_sections[stmt.keyword]] = stmt
            else:
                merged.append(stmt)
                parent_sections[stmt.keyword] = len(merged) - 1
        elif isinstance(stmt, A.AmendDecl):
            if stmt.target_name is None:
                # Section-block amend (e.g. `amend reward { continuous = ... }`).
                if stmt.target_kw not in parent_sections:
                    raise ComposeError(
                        f"amend target `{stmt.target_kw}` not in parent protocol",
                        loc=stmt.loc,
                    )
                idx = parent_sections[stmt.target_kw]
                parent_section = merged[idx]
                overlay = A.SectionBlock(
                    keyword=stmt.target_kw, body=stmt.body, loc=stmt.loc
                )
                merged[idx] = _merge_section_fields(parent_section, overlay)  # type: ignore[arg-type]
            else:
                key = (stmt.target_kw, stmt.target_name)
                if key in final_set:
                    raise ComposeError(
                        f"cannot amend final {stmt.target_kw} \"{stmt.target_name}\"",
                        loc=stmt.loc,
                    )
                if key not in parent_named:
                    raise ComposeError(
                        f"amend target {stmt.target_kw} \"{stmt.target_name}\" not in parent protocol",
                        loc=stmt.loc,
                    )
                idx = parent_named[key]
                merged[idx] = _merge_named_decl(merged[idx], stmt)  # type: ignore[arg-type]
        elif isinstance(stmt, A.RemoveDecl):
            key = (stmt.target_kw, stmt.target_name)
            if key in final_set:
                raise ComposeError(
                    f"cannot remove final {stmt.target_kw} \"{stmt.target_name}\"",
                    loc=stmt.loc,
                )
            if key not in parent_named:
                raise ComposeError(
                    f"remove target {stmt.target_kw} \"{stmt.target_name}\" not in parent protocol",
                    loc=stmt.loc,
                )
            to_remove_indices.add(parent_named[key])
        else:
            # Top-level Assignments inside a protocol body aren't legal
            # per SPEC §3, but the parser doesn't enforce that. Pass
            # through; the resolver will reject if it surfaces.
            merged.append(stmt)

    # Apply removes after iteration so indices stay stable.
    final_body = tuple(s for i, s in enumerate(merged) if i not in to_remove_indices)

    return A.File(
        imports=child.imports,
        protocol=A.Protocol(
            name=child.protocol.name,
            extends=None,  # merged result has no further parent
            body=final_body,
            loc=child.protocol.loc,
        ),
        loc=child.loc,
    )


# Sections that field-merge (SPEC §11.1). Others replace wholesale.
_FIELD_MERGE_SECTIONS = {"meta", "requires", "controls"}


def _merge_section_fields(parent: A.SectionBlock, overlay: A.SectionBlock) -> A.SectionBlock:
    """Field-level merge of two section blocks: child fields override
    parent same-named fields; unmentioned parent fields inherit."""
    fields: dict[str, A.Assignment] = {}
    for stmt in parent.body:
        if isinstance(stmt, A.Assignment):
            fields[stmt.target] = stmt
    for stmt in overlay.body:
        if isinstance(stmt, A.Assignment):
            fields[stmt.target] = stmt
    return A.SectionBlock(
        keyword=parent.keyword,
        body=tuple(fields.values()),
        loc=parent.loc,
    )


def _merge_named_decl(parent: A.NamedDecl, amend: A.AmendDecl) -> A.NamedDecl:
    """Field-level merge: amend's fields override parent's same-named fields."""
    fields: dict[str, A.Assignment] = {}
    for stmt in parent.body:
        if isinstance(stmt, A.Assignment):
            fields[stmt.target] = stmt
    for stmt in amend.body:
        if isinstance(stmt, A.Assignment):
            fields[stmt.target] = stmt
    return A.NamedDecl(
        keyword=parent.keyword,
        name=parent.name,
        body=tuple(fields.values()),
        loc=parent.loc,
    )


def _has_final_true(decl: A.NamedDecl) -> bool:
    """Check for `final = true` in a parent named decl's body (SPEC §11.4)."""
    for stmt in decl.body:
        if (
            isinstance(stmt, A.Assignment)
            and stmt.target == "final"
            and isinstance(stmt.value, A.BoolLit)
            and stmt.value.value is True
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Version compatibility (SPEC §11.5 — light-touch in Phase 0c)
# ---------------------------------------------------------------------------


def _check_version_compat(
    parent_composed: A.File,
    parent_ref: str,
    *,
    child: A.File,
) -> None:
    """Validate that parent's `meta.version` major is compatible with
    the `@<version>` constraint in the ref.

    SPEC §11.5 frames this in terms of *schema version* rather than
    protocol version. Schema version is underspecified (see
    `docs/DESIGN-NOTES.md` §1.7), so Phase 0c reads `meta.version` (the
    protocol version) and checks the major-number match. This is the
    pragmatic 80% rule; tighter semantics land when the spec resolves.
    """
    _, version_constraint = parse_ref(parent_ref)
    if version_constraint is None:
        return
    parent_version = _meta_string_field(parent_composed, "version")
    if parent_version is None:
        return  # parent didn't declare a version; nothing to check against
    if _major(parent_version) != _major(version_constraint):
        raise ComposeError(
            f"major version mismatch: extends {parent_ref!r} but parent "
            f"meta.version is {parent_version!r}",
            loc=child.protocol.loc,
        )


def _meta_string_field(file: A.File, name: str) -> str | None:
    for stmt in file.protocol.body:
        if isinstance(stmt, A.SectionBlock) and stmt.keyword == "meta":
            for inner in stmt.body:
                if (
                    isinstance(inner, A.Assignment)
                    and inner.target == name
                    and isinstance(inner.value, A.StringLit)
                ):
                    return inner.value.value
    return None


def _major(version: str) -> str:
    """Return the leading numeric component of a semver-ish string."""
    head = version.split(".", 1)[0]
    return head


__all__ = [
    "ComposeError",
    "ParentLoader",
    "compose",
    "default_library_dirs",
    "filesystem_loader",
    "parse_ref",
]
