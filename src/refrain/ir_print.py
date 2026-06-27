# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Pretty-printers for the resolved IR.

Two surfaces:

  `print_ir(ir)` -> a clinician-readable textual dump of the typed
  protocol — sections, declarations, type annotations, topological
  order, resource budget. Useful for `refrain resolve --print ir`.

  `print_cred_nf(ir)` -> a CRED-nf-compliant supplementary materials
  table per SPEC §8. Every checklist item maps to a row pulled from
  the IR; missing fields surface as `[NOT SPECIFIED]` so reviewers
  can see at a glance what the protocol omits.
"""

from __future__ import annotations

from .ir import (
    IRArray,
    IRBinaryOp,
    IRBlockExpr,
    IRBoolLit,
    IRCall,
    IRConditional,
    IRControlRef,
    IRExpr,
    IRMeta,
    IRNumberLit,
    IRProtocol,
    IRRewardField,
    IRStreamRef,
    IRStringLit,
    IRThresholdRef,
    IRTuple,
)


# ---------------------------------------------------------------------------
# IR dump
# ---------------------------------------------------------------------------


def print_ir(ir: IRProtocol) -> str:
    """Render the IR as a multi-section human-readable string."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"protocol  {ir.name}")
    if ir.extends:
        lines.append(f"extends   {ir.extends}")
    lines.append("=" * 72)
    lines.append("")

    # Meta
    lines.append("# meta")
    for name, expr in ir.meta.fields.items():
        lines.append(f"  {name:20} = {_render_expr(expr)}")
    lines.append("")

    # Requires
    r = ir.requires
    lines.append("# requires (validated against amp profile)")
    if ir.amp_profile is not None:
        lines.append(f"  amp                  = {ir.amp_profile.model} ({ir.amp_profile.vendor})")
    if r.coupling:
        lines.append(f"  coupling             = {r.coupling}")
    if r.sample_rate_min_hz is not None:
        lines.append(f"  sample_rate          >= {r.sample_rate_min_hz} Hz  (chosen: {r.sample_rate_chosen_hz} Hz)")
    else:
        lines.append(f"  sample_rate          = {r.sample_rate_chosen_hz} Hz")
    if r.channels:
        lines.append(f"  channels             = {list(r.channels)}")
    lines.append(f"  impedance            = {r.impedance}")
    lines.append(f"  markers              = {r.markers}")
    lines.append("")

    # Inputs
    if ir.inputs:
        lines.append("# inputs")
        for inp in ir.inputs.values():
            lines.append(f"  {inp.canonical_name:30}  :: {inp.stream_type}")
            lines.append(f"    montage = {_render_call(inp.montage)}")
        lines.append("")

    # Derives
    if ir.derives:
        lines.append("# derives")
        for d in ir.derives.values():
            lines.append(f"  {d.canonical_name:30}  :: {d.stream_type}")
            lines.append(f"    expression = {_render_expr(d.expression)}")
            if d.upstream:
                lines.append(f"    depends on = {list(d.upstream)}")
        lines.append("")

    # Thresholds
    if ir.thresholds:
        lines.append("# thresholds")
        for t in ir.thresholds.values():
            tunable = " (live-tunable)" if t.live_tunable else ""
            lines.append(f"  {t.canonical_name:30}  :: {t.stream_type}{tunable}")
            lines.append(f"    signal = {t.signal}")
            lines.append(f"    type   = {_render_call(t.threshold_call)}")
        lines.append("")

    # Inhibits
    if ir.inhibits:
        lines.append("# inhibits")
        for ih in ir.inhibits.values():
            tag = " [FINAL]" if ih.final else ""
            lines.append(f"  {ih.canonical_name:30}{tag}")
            lines.append(f"    metric    = {_render_expr(ih.metric)}")
            lines.append(f"    threshold = {_render_call(ih.threshold)}")
            release = f" (release {ih.action_release_ms:g} ms)" if ih.action_release_ms else ""
            lines.append(f"    action    = {ih.action_kind}{release}")
        lines.append("")

    # Customs
    if ir.customs:
        lines.append("# custom primitives")
        for c in ir.customs.values():
            lines.append(f"  {c.canonical_name:30}")
            lines.append(f"    module    = {c.module}")
            lines.append(f"    signature = {c.signature_str}")
            lines.append(f"    budget    = state {c.budget.state_kb} kB, {c.budget.worst_case_us} us/step")
        lines.append("")

    # Controls
    if ir.controls:
        lines.append("# controls (clinician-tunable)")
        for c in ir.controls.values():
            tunable = " (live-tunable)" if c.live_tunable else ""
            lines.append(f"  {c.canonical_name:30}  :: {c.type_kind}{tunable}")
            if c.label:
                lines.append(f"    label   = {c.label!r}")
            if c.default:
                lines.append(f"    default = {_render_expr(c.default)}")
            if c.range_low and c.range_high:
                lines.append(
                    f"    range   = ({_render_expr(c.range_low)}, {_render_expr(c.range_high)})"
                    + (" [log scale]" if c.log_scale else "")
                )
        lines.append("")

    # Reward
    lines.append("# reward")
    if ir.reward.continuous is not None:
        lines.append(f"  continuous = {_render_expr(ir.reward.continuous)}")
    if ir.reward.event is not None:
        lines.append(f"  event      = {_render_expr(ir.reward.event)}")
    lines.append("")

    # Output
    if ir.output:
        lines.append("# output bindings")
        for channel, expr in ir.output.items():
            lines.append(f"  {channel:20} = {_render_expr(expr)}")
        lines.append("")

    # Reward bundles
    if ir.reward_bundles:
        lines.append("# reward bundles")
        for bname, rb in ir.reward_bundles.items():
            parts = []
            if rb.continuous is not None:
                parts.append("continuous")
            if rb.event is not None:
                parts.append("event")
            lines.append(f"  {bname:30}  ({', '.join(parts)})")
        lines.append("")

    # Blocks
    if ir.blocks:
        lines.append("# blocks")
        for blk in ir.blocks.values():
            lines.append(f"  block {blk.name!r}")
            if blk.thresholds:
                lines.append(f"    thresholds = {list(blk.thresholds)}")
            if blk.reward is not None:
                lines.append(f"    reward     = {blk.reward!r}")
            if blk.outputs:
                lines.append(f"    outputs    = {list(blk.outputs)}")
            if blk.inhibits:
                lines.append(f"    inhibits   = {list(blk.inhibits)}")
        lines.append("")

    # Session
    if ir.session.phases:
        lines.append("# session")
        for p in ir.session.phases:
            muted = " (output muted)" if p.output_muted else ""
            dur = _render_duration(p.duration_ms) if p.duration_ms else "open-ended"
            lines.append(f"  phase {p.name!r:20} {dur:>10}{muted}")
            if p.mode != "timed":
                lines.append(f"    mode  = {p.mode}")
            if p.block is not None:
                lines.append(f"    block = {p.block!r}")
        total_ms = sum(p.duration_ms for p in ir.session.phases)
        lines.append(f"  total duration = {_render_duration(total_ms)}")
        lines.append("")

    # Budget
    lines.append("# resource budget")
    lines.append(f"  state            = {ir.budget.state_kb} kB")
    lines.append(f"  worst-case step  = {ir.budget.worst_case_us} us")
    if ir.amp_profile is not None:
        limits = ir.amp_profile.resource_limits
        state_pct = 100 * ir.budget.state_kb / limits.max_state_kb if limits.max_state_kb else 0
        step_pct = 100 * ir.budget.worst_case_us / limits.max_worst_case_us_per_step if limits.max_worst_case_us_per_step else 0
        lines.append(
            f"  vs. amp limits: state {state_pct:.1f}% of {limits.max_state_kb} kB, "
            f"step {step_pct:.1f}% of {limits.max_worst_case_us_per_step} us"
        )
    lines.append("")

    # Topological order
    lines.append("# dataflow topological order")
    for i, node in enumerate(ir.topological_order, 1):
        lines.append(f"  {i:2}. {node}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CRED-nf export (SPEC §8)
# ---------------------------------------------------------------------------


_CRED_ROWS = [
    # (CRED-nf checklist item, IR accessor lambda)
    ("Citation / prior literature",     lambda ir: _meta_value(ir.meta, "citation")),
    ("Indication / clinical population", lambda ir: _join_two(
        _meta_value(ir.meta, "indication"),
        _meta_value(ir.meta, "population"),
    )),
    ("Pre/post outcome measures",       lambda ir: _meta_value(ir.meta, "outcome_measures")),
    ("Control / sham condition",        lambda ir: _meta_value(ir.meta, "control_ref")),
    ("Adverse event monitoring",        lambda ir: _meta_value(ir.meta, "safety_monitoring")),
    ("Exact electrode locations",       lambda ir: list(ir.requires.channels) or None),
    ("Reference electrode",             lambda ir: _reference_electrode(ir)),
    ("Sampling rate",                   lambda ir: _sampling_rate(ir)),
    ("Filter design (band, order)",     lambda ir: _filter_design(ir)),
    ("Online artifact rejection",       lambda ir: _artifact_rejection(ir)),
    ("Threshold algorithm",             lambda ir: _threshold_summary(ir)),
    ("Reward modality",                 lambda ir: list(ir.output) or None),
    ("Reward contingency timing",       lambda ir: _reward_timing(ir)),
    ("Feedback signal computation",     lambda ir: _feedback_computation(ir)),
    ("Number of sessions",              lambda ir: _meta_value(ir.meta, "session_count")),
    ("Session duration",                lambda ir: _session_duration(ir)),
    ("Software / hardware used",        lambda ir: _amp_summary(ir)),
]


def print_cred_nf(ir: IRProtocol) -> str:
    """Render the CRED-nf supplement table."""
    rows: list[tuple[str, str]] = []
    for label, getter in _CRED_ROWS:
        try:
            value = getter(ir)
        except Exception:  # noqa: BLE001 — defence: a bad IR shouldn't kill the export
            value = None
        rows.append((label, _format_cred_value(value)))

    out: list[str] = []
    out.append(f"# CRED-nf supplement — {ir.name}")
    out.append("")
    out.append("| CRED-nf item | Value |")
    out.append("|---|---|")
    for label, value in rows:
        out.append(f"| {label} | {value} |")
    out.append("")
    out.append("---")
    out.append("")
    out.append(f"_Generated by Refrain from `{ir.name}` against amp "
               f"`{ir.amp_profile.model if ir.amp_profile else '(no amp profile)'}`._")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Expression rendering
# ---------------------------------------------------------------------------


def _render_expr(expr: IRExpr) -> str:
    if isinstance(expr, IRNumberLit):
        if expr.unit:
            return f"{_render_number(expr.value)} {expr.unit}"
        return _render_number(expr.value)
    if isinstance(expr, IRStringLit):
        return f'"{expr.value}"'
    if isinstance(expr, IRBoolLit):
        return "true" if expr.value else "false"
    if isinstance(expr, IRStreamRef):
        return f"@{expr.target}"
    if isinstance(expr, IRThresholdRef):
        return f"@{expr.target}"
    if isinstance(expr, IRControlRef):
        return f"@{expr.target}"
    if isinstance(expr, IRRewardField):
        return f"reward.{expr.field_path}"
    if isinstance(expr, IRCall):
        return _render_call(expr)
    if isinstance(expr, IRArray):
        return "[" + ", ".join(_render_expr(e) for e in expr.elements) + "]"
    if isinstance(expr, IRTuple):
        return "(" + ", ".join(_render_expr(e) for e in expr.elements) + ")"
    if isinstance(expr, IRBinaryOp):
        return f"({_render_expr(expr.left)} {expr.op} {_render_expr(expr.right)})"
    if isinstance(expr, IRConditional):
        return (
            f"({_render_expr(expr.cond)} ? "
            f"{_render_expr(expr.then_branch)} : "
            f"{_render_expr(expr.else_branch)})"
        )
    if isinstance(expr, IRBlockExpr):
        head = expr.kind or ""
        body = ", ".join(f"{k}={_render_expr(v)}" for k, v in expr.fields.items())
        return (head + " " if head else "") + "{ " + body + " }"
    return f"<{type(expr).__name__}>"


def _render_call(call: IRCall) -> str:
    parts = []
    for a in call.args:
        if a.name:
            parts.append(f"{a.name}: {_render_expr(a.value)}")
        else:
            parts.append(_render_expr(a.value))
    return f"{call.callee}({', '.join(parts)})"


def _render_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(value)


def _render_duration(ms: float) -> str:
    if ms >= 60_000:
        return f"{ms / 60_000:g} min"
    if ms >= 1000:
        return f"{ms / 1000:g} s"
    return f"{ms:g} ms"


# ---------------------------------------------------------------------------
# CRED-nf helpers
# ---------------------------------------------------------------------------


def _meta_value(meta: IRMeta, key: str):
    return meta.fields.get(key)


def _join_two(a, b):
    if a is None and b is None:
        return None
    parts = [v for v in (a, b) if v is not None]
    return parts if parts else None


def _reference_electrode(ir: IRProtocol):
    # Pull from the first referential input, if any.
    for inp in ir.inputs.values():
        if inp.montage.callee == "referential":
            for arg in inp.montage.args:
                if arg.name == "reference":
                    return arg.value
        if inp.montage.callee == "bipolar":
            # Bipolar inputs have an implicit reference (the minus pole).
            for arg in inp.montage.args:
                if arg.name == "minus":
                    return f"bipolar minus: {_render_expr(arg.value)}"
    return None


def _sampling_rate(ir: IRProtocol):
    r = ir.requires
    if r.sample_rate_min_hz is not None:
        return f">= {r.sample_rate_min_hz} Hz (runtime: {r.sample_rate_chosen_hz} Hz)"
    return f"{r.sample_rate_chosen_hz} Hz"


def _filter_design(ir: IRProtocol):
    """Surface every `bandpass(...)` call across all derives + inhibits."""
    designs: list[str] = []

    def walk(e: IRExpr) -> None:
        if isinstance(e, IRCall):
            if e.callee == "bandpass":
                designs.append(_render_call(e))
            for arg in e.args:
                walk(arg.value)
        elif isinstance(e, IRBinaryOp):
            walk(e.left)
            walk(e.right)
        elif isinstance(e, IRConditional):
            walk(e.cond)
            walk(e.then_branch)
            walk(e.else_branch)
        elif isinstance(e, (IRArray, IRTuple)):
            for elt in e.elements:
                walk(elt)

    for d in ir.derives.values():
        walk(d.expression)
    for ih in ir.inhibits.values():
        walk(ih.metric)
    return designs or None


def _artifact_rejection(ir: IRProtocol):
    if not ir.inhibits:
        return None
    rows = []
    for ih in ir.inhibits.values():
        rows.append(f"{ih.name}: metric={_render_expr(ih.metric)}, action={ih.action_kind}")
    return rows


def _threshold_summary(ir: IRProtocol):
    if not ir.thresholds:
        return None
    rows = []
    for t in ir.thresholds.values():
        rows.append(f"{t.name}: signal={t.signal}, type={_render_call(t.threshold_call)}")
    return rows


def _reward_timing(ir: IRProtocol):
    if ir.reward.event is None:
        return "continuous (no event dwell)"
    return _render_expr(ir.reward.event)


def _feedback_computation(ir: IRProtocol):
    rows = []
    for d in ir.derives.values():
        rows.append(f"{d.name} = {_render_expr(d.expression)}")
    return rows or None


def _session_duration(ir: IRProtocol):
    if not ir.session.phases:
        return None
    total = sum(p.duration_ms for p in ir.session.phases)
    parts = [f"{p.name} {_render_duration(p.duration_ms)}" for p in ir.session.phases]
    return f"{_render_duration(total)} total ({', '.join(parts)})"


def _amp_summary(ir: IRProtocol):
    if ir.amp_profile is None:
        return None
    return f"{ir.amp_profile.vendor} {ir.amp_profile.model}"


def _format_cred_value(value) -> str:
    if value is None:
        return "_[NOT SPECIFIED]_"
    if isinstance(value, IRExpr):
        return _render_expr(value)
    if isinstance(value, list):
        if not value:
            return "_[NOT SPECIFIED]_"
        return "<br>".join(_format_cred_value(v) for v in value)
    return str(value)


__all__ = ["print_ir", "print_cred_nf"]
