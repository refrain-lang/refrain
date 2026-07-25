from __future__ import annotations

from refrain.editor.catalog import Catalog, _fmt_duration, _fmt_num, load_catalog, render_slot

_CONTROL_BODY = {
    "frequency": '{name} = frequency {{\n      default = {default} Hz\n      range   = ({lo} Hz, {hi} Hz)\n      label   = "{label}"{live}{seed}\n    }}',
    "percent":   '{name} = percent {{\n      default = {default}\n      range   = ({lo}, {hi})\n      label   = "{label}"{live}{seed}\n    }}',
    "voltage":   '{name} = voltage {{\n      default = {default} uV\n      range   = ({lo} uV, {hi} uV)\n      label   = "{label}"{live}{seed}\n    }}',
    "number":    '{name} = number {{\n      default = {default}\n      range   = ({lo}, {hi})\n      label   = "{label}"{live}{seed}\n    }}',
}

# The control kinds render can emit. `describe` gates `in_subset` on this so a
# protocol with a kind we cannot render degrades gracefully instead of crashing.
RENDERABLE_CONTROL_KINDS = frozenset(_CONTROL_BODY)


def _fmtnum(v):
    return None if v is None else _fmt_num(v)


def _fill(template: str, slots_def: list[dict], slot_values: dict) -> str:
    typed = {s["name"]: s["type"] for s in slots_def}
    return template.format(**{n: render_slot(v, typed[n]) for n, v in slot_values.items()})


def _render_seed(s: dict) -> str:
    """Render a control's `seed = percentile { ... }` block (leading newline so
    it slots after `live_tunable` inside the control body). window_ms -> the
    most natural duration unit; target_pct is a bound control's bare name or a
    literal number."""
    tp = s["target_pct"]
    tp_txt = tp["bind"] if isinstance(tp, dict) else _fmt_num(tp)
    return ("\n      seed = percentile {\n"
            f'        from       = "{s["from"]}"\n'
            f"        window     = {_fmt_duration(s['window_ms'])}\n"
            f"        target_pct = {tp_txt}\n"
            "      }")


def _render_control(c: dict) -> str:
    live = "\n      live_tunable = true" if c.get("live_tunable") else ""
    seed = _render_seed(c["seed"]) if c.get("seed") else ""
    lo, hi = (c.get("range") or [None, None])
    return _CONTROL_BODY[c["kind"]].format(
        name=c["name"], default=_fmtnum(c["default"]),
        lo=_fmtnum(lo), hi=_fmtnum(hi), label=c.get("label", c["name"]), live=live, seed=seed)


def _render_placement(p: dict) -> str:
    """Render a placement control (active/set/bipolar/pair) inside `controls`."""
    def site(s):
        return f'"{s}"'

    def pair(t):
        return f"({site(t[0])}, {site(t[1])})"

    kind, default, allowed = p["kind"], p["default"], p["allowed"]
    if kind == "active":
        d, a = site(default[0]), "[" + ", ".join(site(s) for s in allowed) + "]"
    elif kind == "set":
        d = "[" + ", ".join(site(s) for s in default) + "]"
        a = "[" + ", ".join(site(s) for s in allowed) + "]"
    else:  # bipolar | pair — site pairs
        d, a = pair(default), "[" + ", ".join(pair(t) for t in allowed) + "]"
    parts = [f'kind = "{kind}"', f"default = {d}", f"allowed = {a}"]
    if kind == "set":
        parts += [f"min = {_fmt_num(p['set_min'])}", f"max = {_fmt_num(p['set_max'])}"]
    parts.append(f'label = "{p["label"]}"')
    return f'{p["name"]} = placement {{ {"; ".join(parts)} }}'


def _render_phase(p: dict) -> str:
    parts = [f'name = "{p["name"]}"']
    if p.get("duration_ms") is not None:
        parts.append(f"duration = {_fmt_duration(p['duration_ms'])}")
    if p.get("block"):
        parts.append(f'block = "{p["block"]}"')
    if p.get("mode"):
        parts.append(f"mode = {p['mode']}")
    if p.get("output_muted"):
        parts.append("output_muted = true")
    return "      phase { " + "; ".join(parts) + " }"


def _render_threshold_call(cat: Catalog, branch: dict) -> str:
    """Render just the constructor-call side of a threshold branch (e.g.
    `percentile(target_pct: reward_pct, window: 2 min)`), stripping the shared
    `type = ` prefix baked into the catalog template so it can be embedded on
    either side of a mode-conditional ternary."""
    b = cat.block(branch["block"])
    filled = _fill(b["template"], b["slots"], branch["slots"])
    return filled.removeprefix("type = ")


def _render_mode_decl(name: str, m: dict) -> str:
    """Render the mode control a conditional threshold's condition names.

    Not general mode-control rendering (there is no `RENDERABLE_CONTROL_KINDS`
    entry for "mode") — this exists only so a threshold.conditional node's
    referenced control still resolves after render (the resolver folds
    `mode == "x" ? a : b` by looking up the *declared* control, so omitting it
    breaks resolution outright rather than merely losing editability)."""
    choices = ", ".join(f'"{c}"' for c in m["choices"])
    parts = [f"choices = [{choices}]", f'default = "{m["default"]}"']
    if m.get("label"):
        parts.append(f'label = "{m["label"]}"')
    if m.get("final"):
        parts.append("final = true")
    return f'{name} = mode {{ {"; ".join(parts)} }}'


def _render_conditional_threshold(n: dict, cat: Catalog) -> str:
    keys = ["signal", "type"] + (["live_tunable"] if n.get("live_tunable") else [])
    kw = max(len(k) for k in keys)
    cond = f'{n["mode_control"]} == "{n["equals"]}"'
    cont_indent = " " * (kw + 9)
    lines = [
        f'  threshold "{n["name"]}" {{',
        f'    {"signal".ljust(kw)} = "{n["signal"]}"',
        f'    {"type".ljust(kw)} = {cond}',
        f'{cont_indent}? {_render_threshold_call(cat, n["when_true"])}',
        f'{cont_indent}: {_render_threshold_call(cat, n["when_false"])}',
    ]
    if n.get("live_tunable"):
        lines.append(f'    {"live_tunable".ljust(kw)} = true')
    lines.append("  }")
    return "\n".join(lines)


def _quote(v):
    if isinstance(v, bool):                       # bool before int (bool is an int subclass)
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, (int, float)):
        return _fmt_num(v)
    return str(v)


def render_protocol(model: dict, catalog: Catalog | None = None) -> str:
    cat = catalog or load_catalog()
    L: list[str] = [f'protocol "{model["name"]}" {{', "  meta {"]
    for k, v in model["meta"].items():
        L.append(f'    {k} = [{", ".join(_quote(x) for x in v)}]' if isinstance(v, list)
                 else f"    {k} = {_quote(v)}")
    L.append("  }")

    req = model["requires"]
    req_parts = [
        f"{k} = [{', '.join(_quote(x) for x in v)}]" if isinstance(v, list) else f"{k} = {_quote(v)}"
        for k, v in req.items()
    ]
    L.append(f"  requires {{ {'; '.join(req_parts)} }}")

    for n in model["inputs"]:
        b = cat.block(n["block"])
        L.append(f'  input "{n["name"]}" {{ montage = {_fill(b["template"], b["slots"], n["slots"])} }}')

    for n in model["derives"]:
        b = cat.block(n["block"])
        body = _fill(b["template"], b["slots"], n["slots"])
        if b.get("form") == "formula":
            L.append(f'  derive "{n["name"]}" {{ formula = {body} }}')
        else:
            L.append(f'  derive "{n["name"]}" {{\n    from = "{n["from"]}"\n    pipeline = [ {body} ]\n  }}')

    mode_decls: dict[str, dict] = {}  # mode controls referenced by conditional thresholds
    for n in model["thresholds"]:
        if n["block"] == "threshold.conditional":
            mode_decls.setdefault(n["mode_control"], n["mode_decl"])
            L.append(_render_conditional_threshold(n, cat))
            continue
        b = cat.block(n["block"])
        lt = "; live_tunable = true" if n.get("live_tunable") else ""
        L.append(f'  threshold "{n["name"]}" {{ signal = "{n["signal"]}"; {_fill(b["template"], b["slots"], n["slots"])}{lt} }}')

    for n in model.get("reward_components", []):  # named weighted-composite reward/inhibit decls
        b = cat.block(n["block"])
        L.append(f'  {n["kind"]} "{n["name"]}" {{ {_fill(b["template"], b["slots"], n["slots"])} }}')

    r = model["reward"]
    if r:
        b = cat.block(r["block"])
        L.append(f'  reward {{\n    {_fill(b["template"], b["slots"], r["slots"])}\n  }}')

    L.append("  output {")
    for o in model["outputs"]:
        L.append(f'    {o["channel"]} = {o["route"]}')
    L.append("  }")

    if model["controls"] or model.get("placements") or mode_decls:
        L.append("  controls {")
        L.extend("    " + _render_placement(pl) for pl in model.get("placements", []))
        pending_modes = dict(mode_decls)          # consumed as each anchor control is emitted
        for c in model["controls"]:
            for name, m in list(pending_modes.items()):
                if m.get("insert_before") == c["name"]:
                    L.append("    " + _render_mode_decl(name, m))
                    del pending_modes[name]
            L.append("    " + _render_control(c))
        L.extend("    " + _render_mode_decl(name, m) for name, m in pending_modes.items())
        L.append("  }")

    for blk in model.get("blocks", []):           # staged: per-phase threshold sets
        thr = ", ".join(_quote(t) for t in blk["thresholds"])
        L.append(f'  block "{blk["name"]}" {{ threshold = [{thr}] }}')

    phases = model.get("session", {}).get("phases") or []
    if phases:
        L.append("  session {\n    phases = [")
        L.extend(_render_phase(p) + "," for p in phases)
        L.append("    ]\n  }")

    L.append("}")
    return "\n".join(L) + "\n"
