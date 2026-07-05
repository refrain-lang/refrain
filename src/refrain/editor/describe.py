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
    view = {
        "name": name, "kind": ctl.kind,
        "allowed": [list(a) if isinstance(a, tuple) else a for a in ctl.allowed],
        "default": list(ctl.default_placement),
        "label": ctl.label or name, "final": bool(ctl.final),
    }
    if ctl.kind == "set":
        view["set_min"], view["set_max"] = ctl.set_min, ctl.set_max
    return view


def _mode_view(name: str, ctl: Any) -> dict:
    return {
        "name": name,
        "kind": "mode",
        "choices": list(ctl.choices),
        "default": ctl.default_mode,
        "label": ctl.label or name,
        "final": bool(ctl.final),
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
    if isinstance(expr, A.MemberAccess):           # pair placement member, e.g. coh.a
        return {"bind": f"{expr.target.name}.{expr.member}"}
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
    if isinstance(montage, A.Call) and montage.callee == "referential":
        return {"name": decl.name, "block": "montage.referential",
                "slots": {"active": _slot_from_expr(_arg(montage, "active")),
                          "reference": _slot_from_expr(_arg(montage, "reference"))}}
    if isinstance(montage, A.Call) and montage.callee == "passthrough":
        return {"name": decl.name, "block": "montage.passthrough", "slots": {}}
    if isinstance(montage, A.Call) and montage.callee == "bipolar":
        return {"name": decl.name, "block": "montage.bipolar",
                "slots": {"pair": _slot_from_expr(_arg(montage, "pair"))}}
    raise _NotInSubset("montage not referential/passthrough/bipolar")


def _match_derive(decl: A.NamedDecl) -> dict:
    bm = _body_map(decl)
    if "formula" in bm:
        f = bm["formula"]
        if isinstance(f, A.BinaryOp) and f.op in ("/", "-") \
           and isinstance(f.left, A.StringLit) and isinstance(f.right, A.StringLit):
            block = "derive.ratio" if f.op == "/" else "derive.difference"
            return {"name": decl.name, "block": block,
                    "slots": {"a": f.left.value, "b": f.right.value}}
        if isinstance(f, A.Call) and f.callee == "coherence":
            ia = _arg(f, "input_a") or (f.args[0].value if len(f.args) > 0 else None)
            ib = _arg(f, "input_b") or (f.args[1].value if len(f.args) > 1 else None)
            win = _arg(f, "window")
            if win is None:                       # positional, window-less form
                return {"name": decl.name, "block": "derive.coherence_band",
                        "slots": {"input_a": ia.value, "input_b": ib.value,
                                  "band": _expr_to_str(_arg(f, "band"))}}
            return {"name": decl.name, "block": "derive.coherence",
                    "slots": {"input_a": ia.value, "input_b": ib.value,
                              "band": _expr_to_str(_arg(f, "band")),
                              "window_ms": _to_ms(win)}}
        raise _NotInSubset(f"formula derive '{decl.name}' not in subset")
    pipe = bm.get("pipeline")
    if not isinstance(pipe, A.Array):
        raise _NotInSubset(f"derive '{decl.name}' is not pipeline-form")
    calls = pipe.elements
    names = [c.callee for c in calls if isinstance(c, A.Call)]
    if names == ["rectify"] and len(calls) == 1:  # e.g. |theta - alpha| asymmetry
        return {"name": decl.name, "block": "derive.rectify", "from": bm["from"].value, "slots": {}}
    if names == ["bandpass", "hilbert", "magnitude", "smooth"] and len(calls) == 4:
        bp, sm = calls[0], calls[3]
        center, bw, band = _arg(bp, "center"), _arg(bp, "bandwidth"), _arg(bp, "band")
        if center is not None:                              # center + ratio form
            if not (isinstance(bw, A.Call) and bw.callee == "ratio"):
                raise _NotInSubset("envelope needs center + bandwidth: ratio(R)")
            return {"name": decl.name, "block": "derive.envelope", "from": bm["from"].value,
                    "slots": {"center": _slot_from_expr(center),
                              "ratio": bw.args[0].value.value,
                              "smooth_tau_ms": _to_ms(_arg(sm, "tau"))}}
        if isinstance(band, (A.Tuple, A.Array)) and len(band.elements) == 2 \
                and all(isinstance(e, A.NumberLit) for e in band.elements):  # explicit-edge form
            order = _arg(bp, "order")
            return {"name": decl.name, "block": "derive.envelope_band", "from": bm["from"].value,
                    "slots": {"band_low_hz": band.elements[0].value,
                              "band_high_hz": band.elements[1].value,
                              "order": order.value if order is not None else 4,
                              "smooth_tau_ms": _to_ms(_arg(sm, "tau"))}}
        raise _NotInSubset("envelope needs center + bandwidth: ratio(R), or band: (lo, hi)")
    if names == ["bandpass", "rectify", "smooth"] and len(calls) == 3:  # low-Fs envelope (HRV)
        bp, sm = calls[0], calls[2]
        band = _arg(bp, "band")
        if band is None:
            raise _NotInSubset("lf_envelope bandpass needs a literal band")
        return {"name": decl.name, "block": "derive.lf_envelope", "from": bm["from"].value,
                "slots": {"band": _expr_to_str(band), "smooth_tau": _to_ms(_arg(sm, "tau"))}}
    if names == ["auto_range"] and len(calls) == 1:
        ar = calls[0]
        return {"name": decl.name, "block": "derive.auto_range", "from": bm["from"].value,
                "slots": {"window_ms": _to_ms(_arg(ar, "window")),
                          "percentile": _expr_to_str(_arg(ar, "percentile"))}}
    raise _NotInSubset(f"derive '{decl.name}' pipeline {names} not in subset")


def _match_reward_component(decl: A.NamedDecl) -> dict:
    bm = _body_map(decl)
    if "metric" in bm and "action" in bm:  # artifact-rejection inhibit (bandpower + mute)
        metric, thr, action = bm["metric"], bm["threshold"], bm["action"]
        band = _arg(metric, "band")
        return {"name": decl.name, "kind": decl.keyword, "block": "inhibit.artifact",
                "slots": {"input": _arg(metric, "input").value,
                          "band_low_hz": band.elements[0].value,
                          "band_high_hz": band.elements[1].value,
                          "metric_window_ms": _to_ms(_arg(metric, "window")),
                          "target_pct": _arg(thr, "target_pct").value,
                          "thr_window_ms": _to_ms(_arg(thr, "window")),
                          "release_ms": _to_ms(_arg(action, "release"))}}
    sig = bm.get("signal")
    if not isinstance(sig, A.Call) or sig.callee != "sigmoid":
        raise _NotInSubset("reward component signal not a sigmoid()")
    ref = sig.args[0].value
    midpoint = _arg(sig, "midpoint")
    if (isinstance(ref, A.BinaryOp) and ref.op == "/"  # graded: sigmoid(<a>/<b>, midpoint: <num>)
            and isinstance(ref.left, A.StringLit) and isinstance(ref.right, A.StringLit)):
        return {"name": decl.name, "kind": decl.keyword, "block": "reward.component_ratio",
                "slots": {"num": ref.left.value, "den": ref.right.value,
                          "midpoint": midpoint.value, "steepness": _arg(sig, "steepness").value,
                          "weight": _slot_from_expr(bm.get("weight"))}}
    if not isinstance(ref, A.StringLit) or midpoint is None or midpoint.unit != "uV":
        raise _NotInSubset("reward component needs sigmoid(<ref>, midpoint: <uV>, steepness:)")
    return {"name": decl.name, "kind": decl.keyword, "block": "reward.component",
            "slots": {"signal": ref.value, "midpoint": midpoint.value,
                      "steepness": _arg(sig, "steepness").value,
                      "weight": _slot_from_expr(bm.get("weight"))}}


def _match_threshold(decl: A.NamedDecl) -> dict:
    bm = _body_map(decl)
    t = bm["type"]
    if isinstance(t, A.Call) and t.callee == "percentile":
        node = {"name": decl.name, "block": "threshold.percentile", "signal": bm["signal"].value,
                "slots": {"target_pct": _slot_from_expr(_arg(t, "target_pct")),
                          "window_ms": _to_ms(_arg(t, "window"))}}
    elif isinstance(t, A.Call) and t.callee == "absolute":
        named = _arg(t, "value")
        if named is not None:                     # absolute(value: <control|literal>)
            node = {"name": decl.name, "block": "threshold.absolute", "signal": bm["signal"].value,
                    "slots": {"value": _slot_from_expr(named)}}
        else:                                     # positional: absolute(8 uV)
            node = {"name": decl.name, "block": "threshold.absolute_lit", "signal": bm["signal"].value,
                    "slots": {"value": _slot_from_expr(t.args[0].value)}}
    else:
        raise _NotInSubset(f"threshold {getattr(t, 'callee', '?')} not in subset")
    if bool(getattr(bm.get("live_tunable"), "value", False)):  # threshold-level live flag
        node["live_tunable"] = True
    return node


def _match_reward(block: A.SectionBlock) -> dict:
    bm = _body_map(block)
    if getattr(bm.get("combine"), "value", None) == "all":   # Mode 2b: set placement, all sites
        ev = bm.get("event")
        cond = _arg(ev, "condition") if isinstance(ev, A.Call) else None
        if not (isinstance(ev, A.Call) and ev.callee == "dwell"
                and isinstance(cond, A.Call) and cond.callee in ("above", "below")):
            raise _NotInSubset("set-all reward event not dwell(above/below)")
        return {"name": None, "block": "reward.set_all",
                "slots": {"direction": cond.callee, "signal": cond.args[0].value.value,
                          "threshold": cond.args[1].value.value,
                          "dwell": _expr_to_str(_arg(ev, "duration"))}}
    if "combine" in bm:                                   # weighted composite
        cont = bm.get("continuous")
        ev = bm.get("event")
        cond = _arg(ev, "condition") if isinstance(ev, A.Call) else None
        if not (isinstance(cont, A.MemberAccess) and cont.member == "composite"):
            raise _NotInSubset("weighted reward continuous not reward.composite")
        if not (isinstance(ev, A.Call) and ev.callee == "dwell"
                and isinstance(cond, A.Call) and cond.callee == "above"
                and isinstance(cond.args[0].value, A.MemberAccess)
                and cond.args[0].value.member == "composite"):
            raise _NotInSubset("weighted reward event not dwell(above(reward.composite, ..))")
        return {"name": None, "block": "reward.weighted",
                "slots": {"threshold": cond.args[1].value.value,
                          "dwell": _expr_to_str(_arg(ev, "duration"))}}
    ev, cont = bm.get("event"), bm.get("continuous")
    if not isinstance(ev, A.Call) or ev.callee != "dwell":
        raise _NotInSubset("reward.event not a dwell()")
    cond = _arg(ev, "condition")
    if isinstance(cond, A.Call) and cond.callee == "all_of":  # compound operant: target + inhibits
        arr = cond.args[0].value
        if not isinstance(arr, (A.Array, A.Tuple)):
            raise _NotInSubset("all_of arg not a list")
        parts = []
        for c in arr.elements:
            if not (isinstance(c, A.Call) and c.callee in ("above", "below")):
                raise _NotInSubset("all_of condition not above/below")
            parts.append(f'{c.callee}("{c.args[0].value.value}", "{c.args[1].value.value}")')
        if not (isinstance(cont, A.Call) and cont.callee == "sigmoid"):
            raise _NotInSubset("compound reward continuous not a sigmoid()")
        carg = cont.args[0].value
        dwell = _expr_to_str(_arg(ev, "duration"))
        if (isinstance(carg, A.BinaryOp) and carg.op == "/"
                and isinstance(carg.left, A.StringLit) and isinstance(carg.right, A.StringLit)):
            return {"name": None, "block": "reward.operant_compound",
                    "slots": {"conditions": ", ".join(parts),
                              "cont_num": carg.left.value, "cont_den": carg.right.value,
                              "midpoint": _arg(cont, "midpoint").value,
                              "steepness": _arg(cont, "steepness").value, "dwell": dwell}}
        if isinstance(carg, A.StringLit):         # bare-ref continuous (e.g. coherence)
            return {"name": None, "block": "reward.operant_compound_abs",
                    "slots": {"conditions": ", ".join(parts), "cont_signal": carg.value,
                              "midpoint": _arg(cont, "midpoint").value,
                              "steepness": _arg(cont, "steepness").value, "dwell": dwell}}
        raise _NotInSubset("compound sigmoid arg not a ref/ref ratio or bare ref")
    if not isinstance(cond, A.Call) or cond.callee not in ("above", "below"):
        raise _NotInSubset("reward condition not above/below")
    base = {"direction": cond.callee, "signal": cond.args[0].value.value,
            "threshold": cond.args[1].value.value, "dwell": _expr_to_str(_arg(ev, "duration"))}
    if isinstance(cont, A.Call) and cont.callee == "sigmoid":
        arg = cont.args[0].value  # first positional arg of sigmoid
        mid = _arg(cont, "midpoint")
        if (isinstance(arg, A.BinaryOp) and arg.op == "/"
                and isinstance(arg.left, A.StringLit) and isinstance(arg.right, A.StringLit)):
            return {"name": None, "block": "reward.operant",
                    "slots": {**base, "cont_num": arg.left.value, "cont_den": arg.right.value,
                              "midpoint": mid.value, "steepness": _arg(cont, "steepness").value}}
        if isinstance(arg, A.StringLit) and mid is not None and mid.unit == "uV":  # sigmoid(<ref>, <uV>)
            return {"name": None, "block": "reward.operant_abs",
                    "slots": {**base, "cont_signal": arg.value, "midpoint_uv": mid.value,
                              "steepness": _arg(cont, "steepness").value}}
        raise _NotInSubset("sigmoid arg not a ref/ref ratio or bare ref with uV midpoint")
    if isinstance(cont, A.StringLit):                     # bare-ref continuous (HRV)
        return {"name": None, "block": "reward.passthrough",
                "slots": {**base, "cont_ref": cont.value}}
    raise _NotInSubset("reward.continuous not a sigmoid() or a bare ref")


def _match_outputs(block: A.SectionBlock) -> list:
    return [{"channel": a.target, "route": _expr_to_str(a.value)}
            for a in block.body if isinstance(a, A.Assignment)]


def _match_requires(block: A.SectionBlock) -> dict:
    # Preserve every requires field in source order (coupling/impedance/markers,
    # not just sample_rate/channels) so the clone round-trips losslessly.
    out: dict = {}
    for a in block.body:
        if not isinstance(a, A.Assignment):
            continue
        v = a.value
        out[a.target] = ([e.value for e in v.elements] if isinstance(v, (A.Array, A.Tuple))
                         else getattr(v, "value", None))
    out.setdefault("sample_rate", "")
    out.setdefault("channels", [])  # placement protocols declare sites, not a channels list
    return out


def _match_block(decl: A.NamedDecl) -> dict:
    thr = _body_map(decl).get("threshold")  # staged block activates a set of thresholds
    if not isinstance(thr, (A.Array, A.Tuple)):
        raise _NotInSubset("block needs threshold = [...]")
    return {"name": decl.name, "thresholds": [e.value for e in thr.elements]}


def _match_session(block: A.SectionBlock) -> dict:
    phases = []
    for ph in _body_map(block)["phases"].elements:
        f = _body_map(ph)
        phase = {"name": f["name"].value,
                 "output_muted": bool(getattr(f.get("output_muted"), "value", False))}
        if "duration" in f:                       # absent for open-ended phases
            phase["duration_ms"] = _to_ms(f["duration"])
        if "block" in f:                          # staged: activates a named block
            phase["block"] = f["block"].value
        if "mode" in f:                           # `mode = timed_with_floor | open | ...`
            m = f["mode"]
            phase["mode"] = getattr(m, "name", None) or getattr(m, "value", None)
        phases.append(phase)
    return {"phases": phases}


def _build_model(ast, controls, placements=()) -> dict:
    p = ast.protocol
    if p.extends is not None:                          # inheritance is not modelled
        raise _NotInSubset("extends not in subset")
    for c in controls:                                 # only kinds render can emit
        if c["kind"] not in RENDERABLE_CONTROL_KINDS:
            raise _NotInSubset(f"control kind '{c['kind']}' not renderable")
    inputs, derives, thresholds, outputs, reward_components, blocks = [], [], [], [], [], []
    reward, requires, session = None, {"sample_rate": "", "channels": []}, {"phases": []}
    for stmt in p.body:
        if isinstance(stmt, A.NamedDecl):
            if stmt.keyword == "input":
                inputs.append(_match_input(stmt))
            elif stmt.keyword == "derive":
                derives.append(_match_derive(stmt))
            elif stmt.keyword == "threshold":
                thresholds.append(_match_threshold(stmt))
            elif stmt.keyword == "block":                # staged: per-phase threshold set
                blocks.append(_match_block(stmt))
            elif stmt.keyword in ("reward", "inhibit"):  # named weighted-composite components
                reward_components.append(_match_reward_component(stmt))
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
            elif stmt.keyword in ("meta", "controls", "groups"):
                pass  # meta/controls handled by describe_protocol; groups inlined into
                      # placement `allowed` at render time, so the section is dropped
            else:
                raise _NotInSubset(f"section '{stmt.keyword}' not in subset")
    return {"name": p.name, "meta": _meta_from_ast(ast), "requires": requires,
            "inputs": inputs, "derives": derives, "thresholds": thresholds,
            "inhibits": [], "reward_components": reward_components, "reward": reward,
            "outputs": outputs, "controls": controls, "placements": list(placements),
            "blocks": blocks, "session": session}


def describe_protocol(source: str, *, amp: Any = None) -> dict:
    try:
        ast = refrain.parse(source)
    except Exception as e:
        return {"ok": False, "diagnostics": [_diag(e)], "meta": {},
                "in_subset": False, "controls": [], "placements": [], "modes": [], "model": None}
    try:
        ir = resolve(ast, amp)
    except Exception as e:
        return {"ok": False, "diagnostics": [_diag(e)], "meta": _meta_from_ast(ast),
                "in_subset": False, "controls": [], "placements": [], "modes": [], "model": None}

    controls, placements, modes = [], [], []
    for name, ctl in ir.controls.items():
        if ctl.type_kind == "placement":
            placements.append(_placement_view(name, ctl))
        elif ctl.type_kind == "mode":
            modes.append(_mode_view(name, ctl))
        else:
            controls.append(_control_view(name, ctl))

    try:
        model = _build_model(ast, controls, placements)
        in_subset = True
    except (_NotInSubset, KeyError, AttributeError, TypeError, IndexError):
        # Intentional out-of-subset, or a matcher hit an unexpected AST shape:
        # either way degrade gracefully — never crash on a resolvable protocol.
        model, in_subset = None, False
    return {"ok": True, "diagnostics": [], "meta": _meta_from_ast(ast),
            "in_subset": in_subset, "controls": controls,
            "placements": placements, "modes": modes, "model": model}
