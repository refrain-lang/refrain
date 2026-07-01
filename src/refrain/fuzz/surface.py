# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Extracts a protocol's testable structure from the resolved IR into a
plain data model. Reads IR-JSON via `ir_to_json_obj` so baked filter
coefficients are available without re-instantiating impls here. No DSP.

This is the single source of "knowledge of the protocol" that both the
scenario generator and the analytic oracle read.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..ir import (
    IRCall,
    IRConditional,
    IRControl,
    IRControlRef,
    IRDerive,
    IRExpr,
    IRInput,
    IRNumberLit,
    IRProtocol,
    IRRewardField,
    IRStreamRef,
    IRStringLit,
    IRThreshold,
    IRThresholdRef,
)
from ..ir_json import ir_to_json_obj
from .errors import UnsupportedProtocol

# Default Hilbert FIR length used by the runtime impl when `taps` is not given
# (the resolver/impl bakes 65 taps -> group delay (65 - 1) // 2 == 32).
_DEFAULT_HILBERT_TAPS = 65


@dataclass(frozen=True, slots=True)
class DeriveSurface:
    name: str
    band: tuple[float, float]
    sos: list[list[float]] | None
    smooth_tau_ms: float | None
    hilbert_group_delay_samples: int
    channel: str


@dataclass(frozen=True, slots=True)
class ThresholdSurface:
    name: str
    signal: str
    kind: str                                  # "absolute" | "percentile"
    absolute_uv: float | None = None
    percentile_target: float | None = None
    percentile_window_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ConditionLeaf:
    op: str
    signal: str
    threshold: str
    children: tuple = ()


@dataclass(frozen=True, slots=True)
class ConditionNode:
    op: str
    children: tuple


@dataclass(frozen=True, slots=True)
class PhaseSurface:
    name: str
    duration_s: float
    output_muted: bool


@dataclass(frozen=True, slots=True)
class OutputBindingSurface:
    name: str
    binds_to: str
    gated_by_holds: bool


@dataclass(frozen=True, slots=True)
class ControlSurface:
    name: str
    default: float
    min_value: float | None
    max_value: float | None


@dataclass(frozen=True, slots=True)
class LogicalSurface:
    protocol_name: str
    sample_rate_hz: int
    required_channels: tuple[str, ...]
    derives: tuple[DeriveSurface, ...]
    thresholds: tuple[ThresholdSurface, ...]
    reward_condition: ConditionNode | ConditionLeaf
    dwell_ms: float
    phases: tuple[PhaseSurface, ...]
    outputs: tuple[OutputBindingSurface, ...]
    controls: tuple[ControlSurface, ...]


# ---------------------------------------------------------------------------
# Small expression helpers
# ---------------------------------------------------------------------------


def _strip_prefix(canonical: str) -> str:
    """`derive/smr_envelope` -> `smr_envelope`; `threshold/smr_t` -> `smr_t`."""
    return canonical.split("/", 1)[1] if "/" in canonical else canonical


def _ms(lit: IRNumberLit) -> float:
    """Convert a time-valued number literal to milliseconds using its
    original surface unit (`ms`/`s`/`min`). Bare/`ms` values pass through."""
    unit = lit.unit
    value = float(lit.value)
    if unit in (None, "ms"):
        return value
    if unit == "s":
        return value * 1_000.0
    if unit == "min":
        return value * 60_000.0
    raise ValueError(f"surface: unexpected time unit {unit!r}")


def _arg(call: IRCall, name: str) -> IRExpr | None:
    """Return the value of the named argument of `call`, or None."""
    for a in call.args:
        if a.name == name:
            return a.value
    return None


def _first_positional(call: IRCall) -> IRExpr | None:
    for a in call.args:
        if a.name is None:
            return a.value
    return None


# ---------------------------------------------------------------------------
# Derive extraction
# ---------------------------------------------------------------------------


def _iter_pipeline_calls(d: IRDerive):
    """Yield the IRCalls of a desugared pipeline derive, outermost-first.

    A pipeline `[bandpass, hilbert, magnitude, smooth]` desugars to a nested
    call tree whose OUTERMOST call is the last stage (`smooth`) and whose
    innermost positional argument chains down to `bandpass(<input stream>)`.
    """
    expr = d.expression
    while isinstance(expr, IRCall):
        yield expr
        inner = None
        for a in expr.args:
            if isinstance(a.value, IRCall):
                inner = a.value
                break
        expr = inner


def _band_from_call(call: IRCall, ir: IRProtocol) -> tuple[float, float]:
    """Read `band: (lo Hz, hi Hz)`, or derive edges from the `center:`/`bandwidth:`
    form. Formula mirrors primitive_impls._resolve_band (the resolver's source of
    truth): band = (center / sqrt(ratio), center * sqrt(ratio))."""
    band = _arg(call, "band")
    if band is not None:
        lo, hi = band.elements
        return (float(lo.value), float(hi.value))
    center_expr = _arg(call, "center")
    bw_expr = _arg(call, "bandwidth")
    if center_expr is None or bw_expr is None:
        raise UnsupportedProtocol("center/bandwidth bandpass")
    if isinstance(center_expr, IRNumberLit):
        center = float(center_expr.value)
    elif isinstance(center_expr, IRControlRef):
        center = _resolve_control_default(ir, center_expr)
    else:
        raise UnsupportedProtocol("center/bandwidth bandpass")
    # bandwidth is `ratio(R)` — an IRCall wrapping one number.
    if not (isinstance(bw_expr, IRCall) and bw_expr.callee == "ratio" and bw_expr.args):
        raise UnsupportedProtocol("center/bandwidth bandpass")
    ratio = float(bw_expr.args[0].value.value)
    if center is None or center <= 0 or ratio <= 0:
        raise UnsupportedProtocol("center/bandwidth bandpass")
    sqrt_r = math.sqrt(ratio)
    return (center / sqrt_r, center * sqrt_r)


def _sos_for_derive_step(j: dict, derive_name: str, callee: str) -> list[list[float]] | None:
    """Walk the IR-JSON derive's nested `expression` tree for the `callee`
    step and return its baked `coeffs["sos"]` (a list of 6-tuples), or None."""
    dj = j["derives"].get(derive_name)
    if dj is None:
        return None

    def _find(node) -> dict | None:
        if isinstance(node, dict):
            if node.get("node") == "call" and node.get("callee") == callee:
                return node
            for v in node.values():
                found = _find(v)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for v in node:
                found = _find(v)
                if found is not None:
                    return found
        return None

    step = _find(dj["expression"])
    if step is None:
        return None
    coeffs = step.get("coeffs")
    if not coeffs:
        return None
    sos = coeffs.get("sos")
    return [list(map(float, row)) for row in sos] if sos is not None else None


def _upstream_channel(ir: IRProtocol, d: IRDerive) -> str:
    """Resolve the active EEG channel feeding this derive via its upstream
    input's montage (`referential`/`bipolar`/`source_project`).

    Falls back to the first required channel if the montage shape is unknown.
    """
    for up in d.upstream:
        if up.startswith("input/"):
            inp = ir.inputs.get(_strip_prefix(up))
            if inp is not None:
                ch = _montage_active_channel(inp)
                if ch is not None:
                    return ch
    return ir.requires.channels[0] if ir.requires.channels else ""


def _montage_active_channel(inp: IRInput) -> str | None:
    montage = inp.montage
    if not isinstance(montage, IRCall):
        return None
    active = _arg(montage, "active")
    if isinstance(active, IRStringLit):
        return active.value
    # bipolar(...) uses `anode`; fall back to the first string-literal arg.
    for a in montage.args:
        if isinstance(a.value, IRStringLit):
            return a.value.value
    return None


def _derive_surface(d: IRDerive, ir: IRProtocol, j: dict) -> DeriveSurface:
    band = (0.0, 0.0)
    smooth_tau_ms: float | None = None
    hilbert_group_delay = 0
    for call in _iter_pipeline_calls(d):
        if call.callee == "bandpass":
            band = _band_from_call(call, ir)
        elif call.callee == "smooth":
            tau = _arg(call, "tau")
            if isinstance(tau, IRNumberLit):
                smooth_tau_ms = _ms(tau)
        elif call.callee == "hilbert":
            taps_arg = _arg(call, "taps")
            taps = (
                int(taps_arg.value)
                if isinstance(taps_arg, IRNumberLit)
                else _DEFAULT_HILBERT_TAPS
            )
            hilbert_group_delay = (taps - 1) // 2
    sos = _sos_for_derive_step(j, d.name, "bandpass")
    return DeriveSurface(
        name=d.name,
        band=band,
        sos=sos,
        smooth_tau_ms=smooth_tau_ms,
        hilbert_group_delay_samples=hilbert_group_delay,
        channel=_upstream_channel(ir, d),
    )


# ---------------------------------------------------------------------------
# Threshold extraction
# ---------------------------------------------------------------------------


def _resolve_control_default(ir: IRProtocol, ref: IRControlRef) -> float | None:
    ctrl = ir.controls.get(_strip_prefix(ref.target))
    if ctrl is None or not isinstance(ctrl.default, IRNumberLit):
        return None
    return float(ctrl.default.value)


def _threshold_surface(t: IRThreshold, ir: IRProtocol) -> ThresholdSurface:
    call = t.threshold_call
    kind = call.callee  # "absolute" | "percentile" | "dynamic"
    signal = _strip_prefix(t.signal)
    if kind == "absolute":
        val = _first_positional(call)
        absolute_uv = float(val.value) if isinstance(val, IRNumberLit) else None
        return ThresholdSurface(
            name=t.name, signal=signal, kind="absolute", absolute_uv=absolute_uv
        )
    if kind == "percentile":
        target_expr = _arg(call, "target_pct")
        if isinstance(target_expr, IRControlRef):
            target = _resolve_control_default(ir, target_expr)
        elif isinstance(target_expr, IRNumberLit):
            target = float(target_expr.value)
        else:
            target = None
        window_expr = _arg(call, "window")
        window_ms = _ms(window_expr) if isinstance(window_expr, IRNumberLit) else None
        return ThresholdSurface(
            name=t.name,
            signal=signal,
            kind="percentile",
            percentile_target=target,
            percentile_window_ms=window_ms,
        )
    return ThresholdSurface(name=t.name, signal=signal, kind=kind)


# ---------------------------------------------------------------------------
# Reward condition + dwell
# ---------------------------------------------------------------------------


def _condition_from_ir(expr: IRExpr) -> ConditionNode | ConditionLeaf:
    """Build the condition tree from an `all_of`/`any_of`/`above`/`below` call."""
    if isinstance(expr, IRCall):
        if expr.callee in ("all_of", "any_of"):
            arg = _first_positional(expr)
            children = tuple(
                _condition_from_ir(el) for el in getattr(arg, "elements", ())
            )
            return ConditionNode(op=expr.callee, children=children)
        if expr.callee in ("above", "below"):
            args = [a.value for a in expr.args]
            signal = ""
            threshold = ""
            for v in args:
                if isinstance(v, IRStreamRef):
                    signal = _strip_prefix(v.target)
                elif isinstance(v, IRThresholdRef):
                    threshold = _strip_prefix(v.target)
                elif isinstance(v, IRStringLit):
                    # Defensive: unresolved string refs.
                    if not signal:
                        signal = _strip_prefix(v.value)
                    else:
                        threshold = _strip_prefix(v.value)
            return ConditionLeaf(op=expr.callee, signal=signal, threshold=threshold)
    raise ValueError(f"surface: unrecognized condition expr {type(expr).__name__}")


def _dwell_ms_from_ir(ir: IRProtocol) -> float:
    """The reward event is a `dwell(condition: ..., duration: <time>)` call."""
    event = ir.reward.event
    if isinstance(event, IRCall) and event.callee == "dwell":
        dur = _arg(event, "duration")
        if isinstance(dur, IRNumberLit):
            return _ms(dur)
    raise ValueError("surface: reward.event is not a dwell(...) with a duration")


def _classify_single_leaf(leaf, derives, thresholds):
    derive = next((d for d in derives if d.name == leaf.signal), None)
    thr = next((t for t in thresholds if t.name == leaf.threshold), None)
    if derive is None:
        raise UnsupportedProtocol("composite-signal reward condition")
    if derive.sos is None:
        raise UnsupportedProtocol("non-bandpass (coherence) reward signal")
    if thr is None:
        raise UnsupportedProtocol("reward condition without a resolvable threshold")
    if thr.kind == "percentile":
        raise UnsupportedProtocol("single percentile-leaf reward (needs calibrated oracle)")
    return leaf


def _reward_condition_from_ir(ir, derives, thresholds) -> ConditionNode | ConditionLeaf:
    event = ir.reward.event
    if isinstance(event, IRCall) and event.callee == "dwell":
        cond = _arg(event, "condition")
        node = _condition_from_ir(cond)
        if isinstance(node, ConditionNode):
            return node
        if isinstance(node, ConditionLeaf):
            return _classify_single_leaf(node, derives, thresholds)
    raise ValueError("surface: reward.event has no all_of/any_of condition")


# ---------------------------------------------------------------------------
# Phases / outputs / controls
# ---------------------------------------------------------------------------


def _output_binding(name: str, expr: IRExpr) -> OutputBindingSurface:
    """Detect a `holds ? continuous : 0` gating conditional, or a plain
    `reward.<field>` binding."""
    if isinstance(expr, IRConditional):
        # `reward.event.holds ? reward.continuous : 0`
        binds = "reward.continuous"
        then = expr.then_branch
        if isinstance(then, IRRewardField):
            binds = f"reward.{then.field_path}"
        return OutputBindingSurface(name=name, binds_to=binds, gated_by_holds=True)
    if isinstance(expr, IRRewardField):
        return OutputBindingSurface(
            name=name, binds_to=f"reward.{expr.field_path}", gated_by_holds=False
        )
    return OutputBindingSurface(name=name, binds_to="", gated_by_holds=False)


def _control_surface(c: IRControl) -> ControlSurface:
    default = float(c.default.value) if isinstance(c.default, IRNumberLit) else 0.0
    lo = float(c.range_low.value) if isinstance(c.range_low, IRNumberLit) else None
    hi = float(c.range_high.value) if isinstance(c.range_high, IRNumberLit) else None
    return ControlSurface(name=c.name, default=default, min_value=lo, max_value=hi)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_surface(ir: IRProtocol) -> LogicalSurface:
    j = ir_to_json_obj(ir)
    derives = tuple(_derive_surface(d, ir, j) for d in ir.derives.values())
    thresholds = tuple(_threshold_surface(t, ir) for t in ir.thresholds.values())
    reward_condition = _reward_condition_from_ir(ir, derives, thresholds)
    dwell_ms = _dwell_ms_from_ir(ir)
    phases = tuple(
        PhaseSurface(
            name=p.name,
            duration_s=p.duration_ms / 1_000.0,
            output_muted=p.output_muted,
        )
        for p in ir.session.phases
    )
    outputs = tuple(_output_binding(name, expr) for name, expr in ir.output.items())
    controls = tuple(
        _control_surface(c)
        for c in ir.controls.values()
        if c.type_kind != "placement"
    )
    return LogicalSurface(
        protocol_name=ir.name,
        sample_rate_hz=int(j["sample_rate_hz"]),
        required_channels=tuple(ir.requires.channels),
        derives=derives,
        thresholds=thresholds,
        reward_condition=reward_condition,
        dwell_ms=dwell_ms,
        phases=phases,
        outputs=outputs,
        controls=controls,
    )


__all__ = [
    "ConditionLeaf",
    "ConditionNode",
    "ControlSurface",
    "DeriveSurface",
    "LogicalSurface",
    "OutputBindingSurface",
    "PhaseSurface",
    "ThresholdSurface",
    "build_surface",
]
