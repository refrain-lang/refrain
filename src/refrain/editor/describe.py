from __future__ import annotations

from typing import Any

import refrain
from refrain import ast as A
from refrain.resolver import resolve


def _num(expr: Any):
    return getattr(expr, "value", None)


def _control_view(name: str, ctl: Any) -> dict:
    return {
        "name": name,
        "kind": ctl.type_kind,
        "default": _num(ctl.default),
        "unit": getattr(ctl.default, "unit", None),
        "range": [_num(ctl.range_low), _num(ctl.range_high)] if ctl.range_low is not None else None,
        "label": ctl.label or name,
        "live_tunable": bool(ctl.live_tunable),
    }


def _placement_view(name: str, ctl: Any) -> dict:
    return {
        "name": name, "kind": ctl.kind,
        "allowed": [list(a) if isinstance(a, tuple) else a for a in ctl.allowed],
        "default": list(ctl.default_placement),
        "label": ctl.label or name, "final": bool(ctl.final),
    }


def _diag(exc: Exception) -> dict:
    loc = getattr(exc, "loc", None)
    return {"severity": "error", "message": str(exc),
            "line": getattr(loc, "line", None), "col": getattr(loc, "col", None)}


def _meta_from_ast(ast: Any) -> dict:
    out: dict = {}
    for stmt in ast.protocol.body:
        if isinstance(stmt, A.SectionBlock) and stmt.keyword == "meta":
            for a in stmt.body:
                v = a.value
                out[a.target] = ([getattr(e, "value", None) for e in v.elements]
                                 if isinstance(v, (A.Array, A.Tuple)) else getattr(v, "value", None))
    return out


def describe_protocol(source: str, *, amp: Any = None) -> dict:
    try:
        ast = refrain.parse(source)
    except Exception as e:
        return {"ok": False, "diagnostics": [_diag(e)], "meta": {},
                "in_subset": False, "controls": [], "placements": [], "model": None}
    try:
        ir = resolve(ast, amp)
    except Exception as e:
        return {"ok": False, "diagnostics": [_diag(e)], "meta": _meta_from_ast(ast),
                "in_subset": False, "controls": [], "placements": [], "model": None}

    controls, placements = [], []
    for name, ctl in ir.controls.items():
        (placements if ctl.type_kind == "placement" else controls).append(
            _placement_view(name, ctl) if ctl.type_kind == "placement" else _control_view(name, ctl))

    return {"ok": True, "diagnostics": [], "meta": _meta_from_ast(ast),
            "in_subset": False, "controls": controls, "placements": placements, "model": None}
