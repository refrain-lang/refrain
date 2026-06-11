from __future__ import annotations

from typing import Any

import refrain
from refrain import ast as A
from refrain.editor.catalog import _fmt_num
from refrain.editor.render import RENDERABLE_CONTROL_KINDS
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


class _NotInSubset(Exception):
    pass


def _body_map(stmt) -> dict:
    return {a.target: a.value for a in stmt.body if isinstance(a, A.Assignment)}


def _arg(call: A.Call, name: str):
    for a in call.args:
        if a.name == name:
            return a.value
    return None


def _slot_from_expr(expr):
    if isinstance(expr, A.NumberLit):
        return expr.value
    if isinstance(expr, A.StringLit):
        return expr.value
    if isinstance(expr, A.NameRef):
        return {"bind": expr.name}
    raise _NotInSubset(f"unsupported slot expr {type(expr).__name__}")


def _to_ms(num: A.NumberLit) -> float:
    return num.value * {"ms": 1, "s": 1000, "min": 60000}[num.unit]


def _expr_to_str(e) -> str:
    if isinstance(e, A.NumberLit):
        return _fmt_num(e.value) + (f" {e.unit}" if e.unit else "")
    if isinstance(e, A.StringLit):
        return f'"{e.value}"'
    if isinstance(e, A.BoolLit):
        return "true" if e.value else "false"
    if isinstance(e, A.NameRef):
        return e.name
    if isinstance(e, A.MemberAccess):
        return f"{_expr_to_str(e.target)}.{e.member}"
    if isinstance(e, A.Conditional):
        return f"{_expr_to_str(e.cond)} ? {_expr_to_str(e.then_branch)} : {_expr_to_str(e.else_branch)}"
    if isinstance(e, A.BinaryOp):
        return f"{_expr_to_str(e.left)} {e.op} {_expr_to_str(e.right)}"
    if isinstance(e, (A.Tuple, A.Array)):
        open_, close = ("(", ")") if isinstance(e, A.Tuple) else ("[", "]")
        return open_ + ", ".join(_expr_to_str(x) for x in e.elements) + close
    raise _NotInSubset(f"expr {type(e).__name__}")


def _match_input(decl: A.NamedDecl) -> dict:
    montage = _body_map(decl).get("montage")
    if not isinstance(montage, A.Call) or montage.callee != "referential":
        raise _NotInSubset("montage not referential")
    return {"name": decl.name, "block": "montage.referential",
            "slots": {"active": _slot_from_expr(_arg(montage, "active")),
                      "reference": _slot_from_expr(_arg(montage, "reference"))}}


def _match_derive(decl: A.NamedDecl) -> dict:
    bm = _body_map(decl)
    if "formula" in bm:
        f = bm["formula"]
        if isinstance(f, A.BinaryOp) and f.op == "/" \
           and isinstance(f.left, A.StringLit) and isinstance(f.right, A.StringLit):
            return {"name": decl.name, "block": "derive.ratio",
                    "slots": {"a": f.left.value, "b": f.right.value}}
        if isinstance(f, A.Call) and f.callee == "coherence":
            return {"name": decl.name, "block": "derive.coherence",
                    "slots": {"input_a": _arg(f, "input_a").value,
                              "input_b": _arg(f, "input_b").value,
                              "band": _expr_to_str(_arg(f, "band")),
                              "window_ms": _to_ms(_arg(f, "window"))}}
        raise _NotInSubset(f"formula derive '{decl.name}' not in subset")
    pipe = bm.get("pipeline")
    if not isinstance(pipe, A.Array):
        raise _NotInSubset(f"derive '{decl.name}' is not pipeline-form")
    calls = pipe.elements
    names = [c.callee for c in calls if isinstance(c, A.Call)]
    if names != ["bandpass", "hilbert", "magnitude", "smooth"] or len(calls) != 4:
        raise _NotInSubset(f"derive '{decl.name}' pipeline {names} not the envelope pattern")
    bp, sm = calls[0], calls[3]
    center, bw = _arg(bp, "center"), _arg(bp, "bandwidth")
    if center is None or not (isinstance(bw, A.Call) and bw.callee == "ratio"):
        raise _NotInSubset("envelope needs center + bandwidth: ratio(R)")
    return {"name": decl.name, "block": "derive.envelope", "from": bm["from"].value,
            "slots": {"center": _slot_from_expr(center),
                      "ratio": bw.args[0].value.value,
                      "smooth_tau_ms": _to_ms(_arg(sm, "tau"))}}


def _match_threshold(decl: A.NamedDecl) -> dict:
    bm = _body_map(decl)
    t = bm["type"]
    if isinstance(t, A.Call) and t.callee == "percentile":
        node = {"name": decl.name, "block": "threshold.percentile", "signal": bm["signal"].value,
                "slots": {"target_pct": _slot_from_expr(_arg(t, "target_pct")),
                          "window_ms": _to_ms(_arg(t, "window"))}}
    elif isinstance(t, A.Call) and t.callee == "absolute":
        node = {"name": decl.name, "block": "threshold.absolute", "signal": bm["signal"].value,
                "slots": {"value": _slot_from_expr(_arg(t, "value"))}}
    else:
        raise _NotInSubset(f"threshold {getattr(t, 'callee', '?')} not in subset")
    if bool(getattr(bm.get("live_tunable"), "value", False)):  # threshold-level live flag
        node["live_tunable"] = True
    return node


def _match_reward(block: A.SectionBlock) -> dict:
    bm = _body_map(block)
    ev, cont = bm.get("event"), bm.get("continuous")
    if not isinstance(ev, A.Call) or ev.callee != "dwell":
        raise _NotInSubset("reward.event not a dwell()")
    cond = _arg(ev, "condition")
    if not isinstance(cond, A.Call) or cond.callee not in ("above", "below"):
        raise _NotInSubset("reward condition not above/below")
    if not isinstance(cont, A.Call) or cont.callee != "sigmoid":
        raise _NotInSubset("reward.continuous not a sigmoid()")
    ratio = cont.args[0].value  # first positional arg of sigmoid = the BinaryOp ratio
    if not (isinstance(ratio, A.BinaryOp) and ratio.op == "/"
            and isinstance(ratio.left, A.StringLit) and isinstance(ratio.right, A.StringLit)):
        raise _NotInSubset("sigmoid arg not a ref/ref ratio")
    return {"name": None, "block": "reward.operant",
            "slots": {"direction": cond.callee,
                      "signal": cond.args[0].value.value,
                      "threshold": cond.args[1].value.value,
                      "cont_num": ratio.left.value,
                      "cont_den": ratio.right.value,
                      "dwell_ms": _to_ms(_arg(ev, "duration")),
                      "midpoint": _arg(cont, "midpoint").value,
                      "steepness": _arg(cont, "steepness").value}}


def _match_outputs(block: A.SectionBlock) -> list:
    return [{"channel": a.target, "route": _expr_to_str(a.value)}
            for a in block.body if isinstance(a, A.Assignment)]


def _match_requires(block: A.SectionBlock) -> dict:
    bm = _body_map(block)
    return {"sample_rate": bm["sample_rate"].value,
            "channels": [e.value for e in bm["channels"].elements]}


def _match_session(block: A.SectionBlock) -> dict:
    phases = []
    for ph in _body_map(block)["phases"].elements:
        f = _body_map(ph)
        phase = {"name": f["name"].value,
                 "output_muted": bool(getattr(f.get("output_muted"), "value", False))}
        if "duration" in f:                       # absent for open-ended phases
            phase["duration_ms"] = _to_ms(f["duration"])
        if "mode" in f:                           # `mode = timed_with_floor | open | ...`
            m = f["mode"]
            phase["mode"] = getattr(m, "name", None) or getattr(m, "value", None)
        phases.append(phase)
    return {"phases": phases}


def _build_model(ast, controls) -> dict:
    p = ast.protocol
    if p.extends is not None:                          # inheritance is not modelled
        raise _NotInSubset("extends not in subset")
    for c in controls:                                 # only kinds render can emit
        if c["kind"] not in RENDERABLE_CONTROL_KINDS:
            raise _NotInSubset(f"control kind '{c['kind']}' not renderable")
    inputs, derives, thresholds, outputs = [], [], [], []
    reward, requires, session = None, {"sample_rate": "", "channels": []}, {"phases": []}
    for stmt in p.body:
        if isinstance(stmt, A.NamedDecl):
            if stmt.keyword == "input":
                inputs.append(_match_input(stmt))
            elif stmt.keyword == "derive":
                derives.append(_match_derive(stmt))
            elif stmt.keyword == "threshold":
                thresholds.append(_match_threshold(stmt))
            else:
                raise _NotInSubset(f"named decl '{stmt.keyword}' not in subset")
        elif isinstance(stmt, A.SectionBlock):
            if stmt.keyword == "reward":
                reward = _match_reward(stmt)
            elif stmt.keyword == "output":
                outputs = _match_outputs(stmt)
            elif stmt.keyword == "requires":
                requires = _match_requires(stmt)
            elif stmt.keyword == "session":
                session = _match_session(stmt)
            elif stmt.keyword in ("meta", "controls"):
                pass  # handled by describe_protocol
            else:
                raise _NotInSubset(f"section '{stmt.keyword}' not in subset")
    return {"name": p.name, "meta": _meta_from_ast(ast), "requires": requires,
            "inputs": inputs, "derives": derives, "thresholds": thresholds,
            "inhibits": [], "reward": reward, "outputs": outputs,
            "controls": controls, "session": session}


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

    try:
        model = _build_model(ast, controls)
        in_subset = True
    except (_NotInSubset, KeyError, AttributeError, TypeError, IndexError):
        # Intentional out-of-subset, or a matcher hit an unexpected AST shape:
        # either way degrade gracefully — never crash on a resolvable protocol.
        model, in_subset = None, False
    return {"ok": True, "diagnostics": [], "meta": _meta_from_ast(ast),
            "in_subset": in_subset, "controls": controls, "placements": placements,
            "model": model}
