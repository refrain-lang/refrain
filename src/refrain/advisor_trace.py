# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Reward-check tracing over IR-JSON (SPEC §7.10.6).

Answers, from the compiled protocol alone: which reward checks exist, which
controls feed each one (and each inhibit), and — where unambiguous — which
single control sets a check's threshold and whether raising it makes the check
harder or easier. Used by the resolver's compile-time checks and by the
advisor's direction hints. Mirrored by the `trace` section of
`refrain-core/src/advisor.rs`; parity-gated. Change both or neither.
"""

from __future__ import annotations

from typing import Any

ELIGIBLE_KINDS = ("number", "percent", "voltage", "frequency")


def _bare(canonical: str) -> str:
    return canonical.split("/", 1)[1] if "/" in canonical else canonical


def _arg(call: dict, name: str, position: int) -> Any:
    for i, a in enumerate(call.get("args") or []):
        if a.get("name") == name or (a.get("name") is None and i == position):
            return a.get("value")
    return None


def reward_checks(event: dict | None) -> tuple[list[dict], str]:
    """The check expressions of a `dwell(...)` reward event, and how they combine."""
    if not event or event.get("node") != "call" or event.get("callee") != "dwell":
        return [], "all"
    cond = _arg(event, "condition", 0)
    if cond is None:
        return [], "all"
    if (
        cond.get("node") == "call"
        and cond.get("callee") in ("all_of", "any_of")
        and cond.get("args")
        and (cond["args"][0].get("value") or {}).get("node") == "array"
    ):
        combine = "all" if cond["callee"] == "all_of" else "any"
        return list(cond["args"][0]["value"].get("elements") or []), combine
    return [cond], "all"


def controls_in(expr: Any, ir: dict) -> set[str]:
    """Bare control names reachable from `expr`, following derive and threshold refs."""
    out: set[str] = set()
    seen: set[str] = set()

    def walk(e: Any) -> None:
        if isinstance(e, list):
            for x in e:
                walk(x)
            return
        if not isinstance(e, dict):
            return
        node = e.get("node")
        if node == "control_ref":
            out.add(_bare(e["target"]))
        elif node == "stream_ref":
            t = e.get("target", "")
            if t.startswith("derive/") and t not in seen:
                seen.add(t)
                d = (ir.get("derives") or {}).get(_bare(t))
                if d is not None:
                    walk(d.get("expression"))
        elif node == "threshold_ref":
            t = e.get("target", "")
            if t not in seen:
                seen.add(t)
                th = (ir.get("thresholds") or {}).get(_bare(t))
                if th is not None:
                    walk(th.get("threshold_call"))
        elif node == "call":
            for a in e.get("args") or []:
                walk(a.get("value"))
        elif node in ("array", "tuple"):
            walk(e.get("elements") or [])
        elif node == "binop":
            walk(e.get("left"))
            walk(e.get("right"))
        elif node == "conditional":
            walk(e.get("cond"))
            walk(e.get("then"))
            walk(e.get("else"))
        elif node == "block":
            for v in (e.get("fields") or {}).values():
                walk(v)

    walk(expr)
    return out


def trace_check(check: dict, ir: dict) -> tuple[str, str] | None:
    """(control, "harder"|"easier") when exactly one live-tunable, eligible
    control sets this check's threshold; None when ambiguous."""
    if check.get("node") != "call" or check.get("callee") not in ("above", "below"):
        return None
    thr = _arg(check, "threshold", 1)
    if not isinstance(thr, dict):
        return None
    base = "harder" if check["callee"] == "above" else "easier"
    node = thr.get("node")
    if node == "control_ref":
        cand = _bare(thr["target"])
        side = {cand}
    elif node == "threshold_ref":
        th = (ir.get("thresholds") or {}).get(_bare(thr.get("target", "")))
        if th is None:
            return None
        call = th.get("threshold_call") or {}
        key = {"absolute": "value", "percentile": "target_pct"}.get(call.get("callee"))
        if key is None:
            return None
        v = _arg(call, key, 0)
        if not isinstance(v, dict) or v.get("node") != "control_ref":
            return None
        cand = _bare(v["target"])
        side = controls_in(call, ir)
    else:
        return None
    controls = ir.get("controls") or {}
    live = {c for c in side if (controls.get(c) or {}).get("live_tunable")}
    decl = controls.get(cand)
    if live != {cand} or decl is None or decl.get("type_kind") not in ELIGIBLE_KINDS:
        return None
    return cand, base


def trace_protocol(ir: dict) -> dict:
    """Every reward's checks with their traced knob/direction and feeding controls."""
    rewards = [("", ir.get("reward"))] + sorted((ir.get("reward_bundles") or {}).items())
    bundles: dict[str, dict] = {}
    check_controls: set[str] = set()
    for key, r in rewards:
        if not r:
            continue
        checks, combine = reward_checks(r.get("event"))
        if not checks:
            continue
        names = r.get("check_names") or []
        entries = []
        for i, c in enumerate(checks):
            traced = trace_check(c, ir)
            feeds = sorted(controls_in(c, ir))
            check_controls.update(feeds)
            entries.append({
                "name": names[i] if i < len(names) else None,
                "knob": traced[0] if traced else None,
                "higher_is": traced[1] if traced else None,
                "controls": feeds,
            })
        bundles[key] = {"combine": combine, "checks": entries}
    inhibit_controls: set[str] = set()
    for ih in (ir.get("inhibits") or {}).values():
        inhibit_controls |= controls_in(ih.get("metric"), ir)
        inhibit_controls |= controls_in(ih.get("threshold"), ir)
    return {
        "bundles": bundles,
        "check_controls": sorted(check_controls),
        "inhibit_controls": sorted(inhibit_controls),
    }
