# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Mode 2a set-replication fan-out: an AST-level pre-pass (design §3).

When a protocol binds a ``placement { kind = "set" }`` control into an input
montage, the single-site author-facing protocol is *replicated* per bound site:
N per-site inputs, N copies of every dependent derive/threshold (named
``<name>@<site>``), and one combined reward whose ``event`` dwell condition is
``all_of``/``any_of`` (per ``reward.combine``) over the N per-site conditions.

This runs in :func:`refrain.resolver.resolve` **after** ``compose`` and **before**
``_Resolver`` — exactly the pre-pass slot ``compose`` occupies. It only rewrites
the AST (reusing :mod:`refrain.ast` node constructors); it duplicates no resolver
logic. The set ``controls`` declaration is left intact so the resolver's eager
set-binding validation (allowed ∩ device, min/max) and the IR-JSON emitter's
placement-omission still run on the rewritten AST.

The emitted AST is a flat multi-site graph using only existing node types, so the
IR-JSON schema and the Rust core are unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from . import ast as A
from .resolver import ResolveError

# Decl keywords that participate in per-site replication.
_PER_SITE_KEYWORDS = ("input", "derive", "threshold")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fan_out(file_ast: A.File, bindings: dict[str, Any], *, amp: Any = None) -> A.File:
    """Replicate a bound ``set`` placement into a flat per-site protocol AST.

    Returns ``file_ast`` unchanged when the protocol declares no ``set``
    placement (the common single-site / non-set case). When exactly one ``set``
    placement is declared, the protocol is rewritten per bound site. More than
    one ``set`` placement is unsupported in v1 and raises ``ResolveError``.

    ``amp`` is accepted for API symmetry with the resolver but full
    allowed/device validation is deferred to the later resolve (which sees the
    retained set ``controls`` decl); fan-out only needs the bound site *list*.
    """
    proto = file_ast.protocol

    # --- Step 1: light-scan the controls block for kind = "set" placements ---
    set_names = _find_set_placements(proto)
    if not set_names:
        return file_ast  # no set placement → single-site path, unchanged
    if len(set_names) > 1:
        raise ResolveError(
            "more than one set placement is not supported (v1 replicates a single set): "
            f"{sorted(set_names)}",
            loc=proto.loc,
        )
    set_name = next(iter(set_names))

    # --- Step 2: resolve the bound site list -------------------------------
    sites = _bound_sites(proto, set_name, bindings)

    # --- Step 3: find the set-bound input ----------------------------------
    decls = _index_decls(proto)
    set_input_names = [
        name
        for (kw, name), decl in decls.items()
        if kw == "input" and _input_references_set(decl, set_name)
    ]
    if not set_input_names:
        # The set is declared but never wired into a montage. Nothing to
        # replicate; leave the AST alone (the resolver validates the binding).
        return file_ast
    if len(set_input_names) > 1:
        raise ResolveError(
            "a set placement may feed at most one input montage in v1; "
            f"found {sorted(set_input_names)}",
            loc=proto.loc,
        )

    # --- Step 4: compute the per-site subgraph (transitive closure) --------
    entity_names = {name for (kw, name) in decls.keys() if kw in _PER_SITE_KEYWORDS}
    # refs[name] = the set of other entity names this decl references.
    refs: dict[str, set[str]] = {}
    for (kw, name), decl in decls.items():
        if kw in _PER_SITE_KEYWORDS:
            refs[name] = _referenced_entities(decl, entity_names) - {name}

    per_site = _transitive_per_site(set_input_names[0], refs)

    # --- Step 5: scoping guards -------------------------------------------
    _check_scoping(proto, decls, refs, per_site, entity_names)

    # --- Steps 6-8: rewrite ------------------------------------------------
    return _rewrite(file_ast, proto, sites, decls, per_site)


# ---------------------------------------------------------------------------
# Step 1: discover set placements
# ---------------------------------------------------------------------------


def _controls_block(proto: A.Protocol) -> A.SectionBlock | None:
    for stmt in proto.body:
        if isinstance(stmt, A.SectionBlock) and stmt.keyword == "controls":
            return stmt
    return None


def _placement_kind(block_expr: A.BlockExpr) -> str | None:
    """Return the ``kind`` string of a ``placement { ... }`` control, or None."""
    if block_expr.name != "placement":
        return None
    for stmt in block_expr.body:
        if (
            isinstance(stmt, A.Assignment)
            and stmt.target == "kind"
            and isinstance(stmt.value, A.StringLit)
        ):
            return stmt.value.value
    return None


def _find_set_placements(proto: A.Protocol) -> set[str]:
    block = _controls_block(proto)
    if block is None:
        return set()
    out: set[str] = set()
    for stmt in block.body:
        if (
            isinstance(stmt, A.Assignment)
            and isinstance(stmt.value, A.BlockExpr)
            and _placement_kind(stmt.value) == "set"
        ):
            out.add(stmt.target)
    return out


def _set_default_sites(proto: A.Protocol, set_name: str) -> list[str]:
    """Read the declared ``default = [...]`` site list for a set placement."""
    block = _controls_block(proto)
    assert block is not None  # caller only invokes after _find_set_placements
    for stmt in block.body:
        if (
            isinstance(stmt, A.Assignment)
            and stmt.target == set_name
            and isinstance(stmt.value, A.BlockExpr)
        ):
            for inner in stmt.value.body:
                if (
                    isinstance(inner, A.Assignment)
                    and inner.target == "default"
                    and isinstance(inner.value, A.Array)
                ):
                    return [
                        e.value for e in inner.value.elements if isinstance(e, A.StringLit)
                    ]
    return []


# ---------------------------------------------------------------------------
# Step 2: resolve the bound site list
# ---------------------------------------------------------------------------


def _bound_sites(proto: A.Protocol, set_name: str, bindings: dict[str, Any]) -> list[str]:
    if set_name in bindings:
        value = bindings[set_name]
        if isinstance(value, (list, tuple)):
            sites = list(value)
        else:
            raise ResolveError(
                f"placement {set_name!r} (set): binding must be a list of channel "
                f"strings, got {value!r}",
            )
    else:
        sites = _set_default_sites(proto, set_name)

    if not all(isinstance(s, str) for s in sites):
        raise ResolveError(
            f"placement {set_name!r} (set): site list must be channel-name strings, "
            f"got {sites!r}",
        )
    if not sites:
        raise ResolveError(
            f"placement {set_name!r} (set): no sites to replicate (empty binding/default)",
        )
    # Light count sanity only; allowed ∩ device + min/max validation is run by
    # the resolver against the retained set controls decl.
    return sites


# ---------------------------------------------------------------------------
# Steps 3-4: decl indexing, reference scanning, transitive closure
# ---------------------------------------------------------------------------


def _index_decls(proto: A.Protocol) -> dict[tuple[str, str], A.NamedDecl]:
    out: dict[tuple[str, str], A.NamedDecl] = {}
    for stmt in proto.body:
        if isinstance(stmt, A.NamedDecl) and stmt.keyword in _PER_SITE_KEYWORDS:
            out[(stmt.keyword, stmt.name)] = stmt
    return out


def _input_references_set(decl: A.NamedDecl, set_name: str) -> bool:
    """True if the input's montage call has a channel-slot NameRef to the set."""
    for stmt in decl.body:
        if isinstance(stmt, A.Assignment) and stmt.target == "montage":
            for ref in _iter_namerefs(stmt.value):
                if ref.name == set_name:
                    return True
    return False


def _string_lits(expr: A.Expr) -> list[str]:
    """Collect every StringLit *value* anywhere in an expression tree."""
    out: list[str] = []
    _walk_string_lits(expr, out)
    return out


def _walk_string_lits(node: A.Node, out: list[str]) -> None:
    if isinstance(node, A.StringLit):
        out.append(node.value)
        return
    for child in _expr_children(node):
        _walk_string_lits(child, out)


def _iter_namerefs(node: A.Node) -> Iterator[A.NameRef]:
    """Yield every NameRef anywhere in an expression tree."""
    if isinstance(node, A.NameRef):
        yield node
        return
    for child in _expr_children(node):
        yield from _iter_namerefs(child)


def _expr_children(node: A.Node) -> Iterator[A.Expr]:
    """Yield the immediate Expr/Arg children of an AST expression node."""
    if isinstance(node, A.Call):
        for arg in node.args:
            yield arg.value
    elif isinstance(node, A.Arg):
        yield node.value
    elif isinstance(node, (A.Array, A.Tuple)):
        yield from node.elements
    elif isinstance(node, A.BinaryOp):
        yield node.left
        yield node.right
    elif isinstance(node, A.Conditional):
        yield node.cond
        yield node.then_branch
        yield node.else_branch
    elif isinstance(node, A.MemberAccess):
        yield node.target
    elif isinstance(node, A.BlockExpr):
        for stmt in node.body:
            if isinstance(stmt, A.Assignment):
                yield stmt.value


def _referenced_entities(decl: A.NamedDecl, entity_names: set[str]) -> set[str]:
    """Names of input/derive/threshold entities this decl refers to (via StringLit).

    SPEC §5.4: a string literal naming a declared block is semantically a
    reference. We classify at the AST level by matching StringLit values against
    the protocol's declared entity names — a sound over-approximation for the
    dependency edges we need (``derive.from``/pipeline refs, ``threshold.signal``).
    """
    found: set[str] = set()
    for stmt in decl.body:
        if isinstance(stmt, A.Assignment):
            for s in _string_lits(stmt.value):
                if s in entity_names:
                    found.add(s)
    return found


def _transitive_per_site(seed: str, refs: dict[str, set[str]]) -> set[str]:
    """Forward closure: entities reachable *from* the set-bound input.

    An entity is per-site iff it (transitively) consumes the set-bound input.
    Iterate to a fixpoint: add any entity that references an already-per-site
    entity.
    """
    per_site = {seed}
    changed = True
    while changed:
        changed = False
        for name, deps in refs.items():
            if name in per_site:
                continue
            if deps & per_site:
                per_site.add(name)
                changed = True
    return per_site


# ---------------------------------------------------------------------------
# Step 5: scoping guards
# ---------------------------------------------------------------------------


def _reward_block(proto: A.Protocol) -> A.SectionBlock | None:
    for stmt in proto.body:
        if isinstance(stmt, A.SectionBlock) and stmt.keyword == "reward":
            return stmt
    return None


def _check_scoping(
    proto: A.Protocol,
    decls: dict[tuple[str, str], A.NamedDecl],
    refs: dict[str, set[str]],
    per_site: set[str],
    entity_names: set[str],
) -> None:
    # (a) Continuous reward over a replicated set is rejected (design §3a).
    reward = _reward_block(proto)
    if reward is not None:
        for stmt in reward.body:
            if isinstance(stmt, A.Assignment) and stmt.target == "continuous":
                touched = {s for s in _string_lits(stmt.value) if s in entity_names}
                if touched & per_site:
                    raise ResolveError(
                        "a continuous reward over a replicated `set` needs "
                        "aggregation — see Mode 2b",
                        loc=stmt.loc,
                    )

    # (b) Ambiguous boundary: a per-site entity that mixes a replicated stream
    # with a non-replicated one cannot be unambiguously assigned (design §3b).
    for (kw, name), decl in decls.items():
        if name not in per_site or name not in refs:
            continue
        deps = refs[name]
        non_replicated = deps - per_site
        if deps & per_site and non_replicated:
            raise ResolveError(
                f"{kw} {name!r} mixes a replicated `set` stream with non-replicated "
                f"stream(s) {sorted(non_replicated)} — the replication boundary is "
                "ambiguous; split it so per-site and shared streams don't mix",
                loc=decl.loc,
            )


# ---------------------------------------------------------------------------
# Steps 6-8: rewrite the AST
# ---------------------------------------------------------------------------


def _suffix(name: str, site: str) -> str:
    return f"{name}@{site}"


def _rename_refs_expr(node: A.Expr, per_site: set[str], site: str) -> A.Expr:
    """Return a copy of ``node`` with every StringLit naming a per-site entity
    rewritten to ``<name>@<site>``. Non-matching StringLits (literals, fixed
    channel names, non-replicated refs) are left untouched."""
    if isinstance(node, A.StringLit):
        if node.value in per_site:
            return A.StringLit(value=_suffix(node.value, site), loc=node.loc)
        return node
    if isinstance(node, A.Call):
        return A.Call(
            callee=node.callee,
            args=tuple(
                A.Arg(name=a.name, value=_rename_refs_expr(a.value, per_site, site), loc=a.loc)
                for a in node.args
            ),
            loc=node.loc,
        )
    if isinstance(node, A.Array):
        return A.Array(
            elements=tuple(_rename_refs_expr(e, per_site, site) for e in node.elements),
            loc=node.loc,
        )
    if isinstance(node, A.Tuple):
        return A.Tuple(
            elements=tuple(_rename_refs_expr(e, per_site, site) for e in node.elements),
            loc=node.loc,
        )
    if isinstance(node, A.BinaryOp):
        return A.BinaryOp(
            op=node.op,
            left=_rename_refs_expr(node.left, per_site, site),
            right=_rename_refs_expr(node.right, per_site, site),
            loc=node.loc,
        )
    if isinstance(node, A.Conditional):
        return A.Conditional(
            cond=_rename_refs_expr(node.cond, per_site, site),
            then_branch=_rename_refs_expr(node.then_branch, per_site, site),
            else_branch=_rename_refs_expr(node.else_branch, per_site, site),
            loc=node.loc,
        )
    if isinstance(node, A.MemberAccess):
        return A.MemberAccess(
            target=_rename_refs_expr(node.target, per_site, site),
            member=node.member,
            loc=node.loc,
        )
    if isinstance(node, A.BlockExpr):
        return A.BlockExpr(
            name=node.name,
            body=tuple(_rename_stmt(s, per_site, site) for s in node.body),
            loc=node.loc,
        )
    # NumberLit, BoolLit, NameRef and anything else: returned as-is.
    return node


def _rename_stmt(stmt: A.Statement, per_site: set[str], site: str) -> A.Statement:
    """Rewrite per-site refs inside an assignment's value; pass others through."""
    if isinstance(stmt, A.Assignment):
        return A.Assignment(
            target=stmt.target,
            value=_rename_refs_expr(stmt.value, per_site, site),
            loc=stmt.loc,
        )
    return stmt


def _replicate_input(decl: A.NamedDecl, set_name: str, site: str) -> A.NamedDecl:
    """Per-site copy of the set-bound input: rewrite the set NameRef channel
    slot to the concrete site string, and rename the decl to ``<name>@<site>``."""
    new_body: list[A.Statement] = []
    for stmt in decl.body:
        if isinstance(stmt, A.Assignment) and stmt.target == "montage":
            new_body.append(
                A.Assignment(
                    target="montage",
                    value=_bind_set_nameref(stmt.value, set_name, site),
                    loc=stmt.loc,
                )
            )
        else:
            new_body.append(stmt)
    return A.NamedDecl(
        keyword="input",
        name=_suffix(decl.name, site),
        body=tuple(new_body),
        loc=decl.loc,
    )


def _bind_set_nameref(node: A.Expr, set_name: str, site: str) -> A.Expr:
    """Rewrite a ``NameRef(set_name)`` channel slot to ``StringLit(site)``."""
    if isinstance(node, A.NameRef) and node.name == set_name:
        return A.StringLit(value=site, loc=node.loc)
    if isinstance(node, A.Call):
        return A.Call(
            callee=node.callee,
            args=tuple(
                A.Arg(name=a.name, value=_bind_set_nameref(a.value, set_name, site), loc=a.loc)
                for a in node.args
            ),
            loc=node.loc,
        )
    return node


def _replicate_dependent(decl: A.NamedDecl, per_site: set[str], site: str) -> A.NamedDecl:
    """Per-site copy of a derive/threshold: rename the decl and rewrite every
    per-site stream/threshold ref inside it to ``@site``."""
    new_body = tuple(_rename_stmt(s, per_site, site) for s in decl.body)
    return A.NamedDecl(
        keyword=decl.keyword,
        name=_suffix(decl.name, site),
        body=new_body,
        loc=decl.loc,
    )


def _combine_callee(proto: A.Protocol) -> str:
    """``reward.combine`` → ``all_of`` (default) / ``any_of``."""
    reward = _reward_block(proto)
    if reward is not None:
        for stmt in reward.body:
            if (
                isinstance(stmt, A.Assignment)
                and stmt.target == "combine"
                and isinstance(stmt.value, A.StringLit)
            ):
                if stmt.value.value == "any":
                    return "any_of"
                # "all" (and any other value, which the resolver validates) → all_of
                return "all_of"
    return "all_of"


def _rewrite_reward(
    reward: A.SectionBlock, proto: A.Protocol, sites: list[str], per_site: set[str]
) -> A.SectionBlock:
    """Replace the dwell ``condition`` with all_of/any_of over per-site conditions."""
    combine = _combine_callee(proto)
    new_body: list[A.Statement] = []
    for stmt in reward.body:
        if (
            isinstance(stmt, A.Assignment)
            and stmt.target == "event"
            and isinstance(stmt.value, A.Call)
            and stmt.value.callee == "dwell"
        ):
            new_body.append(
                A.Assignment(
                    target="event",
                    value=_rewrite_dwell(stmt.value, combine, sites, per_site),
                    loc=stmt.loc,
                )
            )
        else:
            new_body.append(stmt)
    return A.SectionBlock(keyword="reward", body=tuple(new_body), loc=reward.loc)


def _rewrite_dwell(
    dwell: A.Call, combine: str, sites: list[str], per_site: set[str]
) -> A.Call:
    new_args: list[A.Arg] = []
    for arg in dwell.args:
        if arg.name == "condition":
            base = arg.value
            per_site_conditions = tuple(
                _rename_refs_expr(base, per_site, site) for site in sites
            )
            conditions_arr = A.Array(elements=per_site_conditions, loc=base.loc)
            combined = A.Call(
                callee=combine,
                args=(A.Arg(name=None, value=conditions_arr, loc=arg.loc),),
                loc=base.loc,
            )
            new_args.append(A.Arg(name="condition", value=combined, loc=arg.loc))
        else:
            new_args.append(arg)
    return A.Call(callee=dwell.callee, args=tuple(new_args), loc=dwell.loc)


def _rewrite(
    file_ast: A.File,
    proto: A.Protocol,
    sites: list[str],
    decls: dict[tuple[str, str], A.NamedDecl],
    per_site: set[str],
) -> A.File:
    # Identify the single set-bound input name (the seed of `per_site`).
    set_input_name = next(
        name for (kw, name), _ in decls.items() if kw == "input" and name in per_site
    )
    # Recover the set placement name from the input montage NameRef.
    set_input_decl = decls[("input", set_input_name)]
    set_name = _input_set_nameref(set_input_decl)

    new_body: list[A.Statement] = []
    for stmt in proto.body:
        # Drop the original (un-suffixed) per-site decls; replicate later.
        if (
            isinstance(stmt, A.NamedDecl)
            and stmt.keyword in _PER_SITE_KEYWORDS
            and stmt.name in per_site
        ):
            continue
        # Rewrite the reward block's dwell condition into all_of/any_of.
        if isinstance(stmt, A.SectionBlock) and stmt.keyword == "reward":
            new_body.append(_rewrite_reward(stmt, proto, sites, per_site))
            continue
        new_body.append(stmt)

    # Insert the per-site copies, grouped by site, in declaration order so
    # inputs precede their derives precede their thresholds (a clean topo order).
    ordered_per_site_decls: list[A.NamedDecl] = [
        stmt
        for stmt in proto.body
        if isinstance(stmt, A.NamedDecl)
        and stmt.keyword in _PER_SITE_KEYWORDS
        and stmt.name in per_site
    ]
    replicas: list[A.Statement] = []
    for site in sites:
        for decl in ordered_per_site_decls:
            if decl.keyword == "input" and decl.name == set_input_name:
                replicas.append(_replicate_input(decl, set_name, site))
            else:
                replicas.append(_replicate_dependent(decl, per_site, site))

    # Place the replicas where the first original per-site decl was, preserving
    # the surrounding source order (meta/requires/controls before, reward/output
    # after) the resolver relies on.
    insert_at = _first_per_site_index(new_body, proto, per_site)
    final_body = tuple(new_body[:insert_at] + replicas + new_body[insert_at:])

    return A.File(
        imports=file_ast.imports,
        protocol=A.Protocol(
            name=proto.name,
            extends=proto.extends,
            body=final_body,
            loc=proto.loc,
        ),
        loc=file_ast.loc,
    )


def _input_set_nameref(decl: A.NamedDecl) -> str:
    """The set placement name referenced by the set-bound input's montage."""
    for stmt in decl.body:
        if isinstance(stmt, A.Assignment) and stmt.target == "montage":
            for ref in _iter_namerefs(stmt.value):
                return ref.name
    raise ResolveError(  # unreachable: caller verified the montage references the set
        f"internal: set-bound input {decl.name!r} lost its set reference",
        loc=decl.loc,
    )


def _first_per_site_index(
    new_body: list[A.Statement], proto: A.Protocol, per_site: set[str]
) -> int:
    """Index in ``new_body`` where the per-site decls should be inserted.

    Mirrors the position of the first original per-site decl in source order:
    after any decl that precedes it (meta/requires/controls/non-replicated
    inputs), before reward/output/session.
    """
    # Count how many statements in the original body came before the first
    # per-site decl, and translate that into an index in `new_body` (which has
    # the per-site decls removed and the reward block replaced in place).
    before: list[A.Statement] = []
    for stmt in proto.body:
        if (
            isinstance(stmt, A.NamedDecl)
            and stmt.keyword in _PER_SITE_KEYWORDS
            and stmt.name in per_site
        ):
            break
        before.append(stmt)
    # `before` statements are all retained (reward replacement keeps the slot,
    # and no preceding statement is a removed per-site decl), so the insert
    # index equals len(before).
    return len(before)


__all__ = ["fan_out"]
