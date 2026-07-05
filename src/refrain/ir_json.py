# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Emit a resolved `IRProtocol` as a JSON-compatible object tree.

This is the wire format that lets a non-Python runtime (e.g. a portable
Rust core embedded on mobile or in a game engine) execute a Refrain
protocol without depending on the Python parser/resolver. The Python
front-end (`parse_file` -> `resolve`) stays the single source of truth for
*authoring*; this module serializes the resolved IR so the runtime is just
"deserialize IR-JSON -> evaluate chunks".

See `docs/IR-JSON.md` for the schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from .eval_ import _classify_call, _substitute_controls, control_defaults
from .ir import (
    IRArray,
    IRBinaryOp,
    IRBlockExpr,
    IRBoolLit,
    IRCall,
    IRConditional,
    IRControl,
    IRControlRef,
    IRDerive,
    IRExpr,
    IRInhibit,
    IRInput,
    IRMeta,
    IRNumberLit,
    IRPhase,
    IRProtocol,
    IRRequires,
    IRReward,
    IRRewardField,
    IRSession,
    IRStreamRef,
    IRStringLit,
    IRThreshold,
    IRThresholdRef,
    IRTuple,
)
from .primitive_impls import make_filter_impl
from .types_ import Dimensions, StreamType

IR_JSON_VERSION = "0.1"


def _protocol_ir_version(ir: IRProtocol) -> str:
    """Lowest IR-JSON version that represents this protocol.

    A protocol that uses no named components, no weighted combine, and no
    staged-protocol features (blocks / named reward bundles) emits v0.1
    (byte-identical to the pre-v0.2 emitter); anything using the v0.2
    composite or staging features emits v0.2.
    """
    if (
        ir.reward.components
        or ir.reward.combine == "weighted"
        or ir.blocks
        or ir.reward_bundles
    ):
        return "0.2"
    return IR_JSON_VERSION


@dataclass(frozen=True)
class _EmitCtx:
    """Carries the runtime parameters needed to bake DSP coefficients."""

    sample_rate_hz: float | None
    channel_names: tuple[str, ...]
    controls: dict[str, float]


# ---------------------------------------------------------------------------
# Type-layer serialization
# ---------------------------------------------------------------------------


def _emit_dims(d: Dimensions) -> dict:
    return {"time": d.time, "voltage": d.voltage}


def _emit_stream_type(st: StreamType) -> dict:
    return {
        "value_kind": st.value_kind,
        "dimensions": _emit_dims(st.dimensions),
        "vector_size": st.vector_size,
    }


# ---------------------------------------------------------------------------
# Coefficient baking
# ---------------------------------------------------------------------------


def _extract_coeffs(impl: object) -> dict | None:
    """Read whatever DSP coefficients an instantiated impl exposes.

    Generic on purpose: it reads the attribute set each impl already
    computes (`sos`, FIR taps, `alpha`, derived window sizes), so the
    baked values are produced by the *exact same code path* the live
    evaluator uses — bit-for-bit, no reimplementation.
    """
    c: dict = {}
    if hasattr(impl, "sos"):
        c["sos"] = np.asarray(impl.sos).tolist()
    if hasattr(impl, "h"):
        c["fir_taps"] = np.asarray(impl.h).tolist()
        c["group_delay"] = int(impl.group_delay)
    if hasattr(impl, "alpha"):
        c["alpha"] = float(impl.alpha)
    if hasattr(impl, "dt"):
        c["dt"] = float(impl.dt)
    if hasattr(impl, "window_samples"):
        c["window_samples"] = int(impl.window_samples)
    if hasattr(impl, "lag"):
        c["lag_samples"] = int(impl.lag)
    if hasattr(impl, "dwell_samples"):
        c["dwell_samples"] = int(impl.dwell_samples)
    if hasattr(impl, "nperseg"):
        c["nperseg"] = int(impl.nperseg)
    if hasattr(impl, "noverlap"):
        c["noverlap"] = int(impl.noverlap)
    return c or None


def _bake_coeffs(call: IRCall, ctx: _EmitCtx) -> dict | None:
    """Instantiate the call's impl and serialize its baked coefficients.

    Returns None when baking isn't statically possible (e.g. a
    control-parameterized argument that's only known at runtime, or a
    primitive with no precomputable coefficients)."""
    if ctx.sample_rate_hz is None:
        return None
    try:
        static, _dynamic = _classify_call(call)
        # Resolve control references to their default value (the value the
        # runtime uses by default) so control-parameterized coefficients —
        # e.g. percentile `window_samples` alongside a control-ref
        # `target_pct` — bake. REUSE the evaluator's substitution walker.
        static = _substitute_controls(static, ctx.controls)
        # A remaining non-literal argument (an unresolvable dynamic expr)
        # can't be baked ahead of time; leave it for the runtime to resolve.
        if any(isinstance(v, IRExpr) for v in static.values()):
            return None
        impl = make_filter_impl(
            call.callee,
            static,
            sample_rate_hz=ctx.sample_rate_hz,
            channel_names=ctx.channel_names,
        )
    except (ValueError, NotImplementedError, KeyError, TypeError):
        return None
    return _extract_coeffs(impl)


# ---------------------------------------------------------------------------
# Expression-layer serialization
# ---------------------------------------------------------------------------


def _emit_expr(expr: IRExpr, ctx: _EmitCtx) -> dict:
    """Serialize an IR expression into a discriminated (`"node"`-tagged) dict.

    The discriminator lets a consumer (serde on the Rust side) deserialize
    into a tagged enum without guessing.
    """
    if isinstance(expr, IRNumberLit):
        return {
            "node": "number",
            "value": expr.value,
            "dims": _emit_dims(expr.dims),
            "unit": expr.unit,
        }
    if isinstance(expr, IRStringLit):
        return {"node": "string", "value": expr.value}
    if isinstance(expr, IRBoolLit):
        return {"node": "bool", "value": expr.value}
    if isinstance(expr, IRStreamRef):
        return {
            "node": "stream_ref",
            "target": expr.target,
            "stream_type": _emit_stream_type(expr.stream_type),
        }
    if isinstance(expr, IRThresholdRef):
        return {
            "node": "threshold_ref",
            "target": expr.target,
            "stream_type": _emit_stream_type(expr.stream_type),
        }
    if isinstance(expr, IRControlRef):
        # Emit a `control_ref` node carrying BOTH the canonical control target
        # AND the resolved default value. The default is the value the runtime
        # uses until a `set_control(...)` arrives (so behaviour with no live
        # retuning is identical to baking the literal number, as before), while
        # the `target` preserves the binding "control X feeds this param" so a
        # non-Python runtime can route `set_control` to the right impl. Missing
        # default → 0.0, matching `control_defaults`. NB: coefficient *baking*
        # (`_bake_coeffs`) substitutes controls to literals on its own internal
        # path, so this change does not perturb any baked coefficient.
        return {
            "node": "control_ref",
            "target": expr.target,
            "default": ctx.controls.get(expr.target, 0.0),
            "dims": _emit_dims(expr.dims),
        }
    if isinstance(expr, IRRewardField):
        return {
            "node": "reward_field",
            "field_path": expr.field_path,
            "stream_type": _emit_stream_type(expr.stream_type),
        }
    if isinstance(expr, IRCall):
        node = {
            "node": "call",
            "callee": expr.callee,
            "args": [{"name": a.name, "value": _emit_expr(a.value, ctx)} for a in expr.args],
            "stream_type": _emit_stream_type(expr.stream_type),
        }
        coeffs = _bake_coeffs(expr, ctx)
        if coeffs is not None:
            node["coeffs"] = coeffs
        return node
    if isinstance(expr, IRArray):
        return {"node": "array", "elements": [_emit_expr(e, ctx) for e in expr.elements]}
    if isinstance(expr, IRTuple):
        return {"node": "tuple", "elements": [_emit_expr(e, ctx) for e in expr.elements]}
    if isinstance(expr, IRBinaryOp):
        return {
            "node": "binop",
            "op": expr.op,
            "left": _emit_expr(expr.left, ctx),
            "right": _emit_expr(expr.right, ctx),
            "stream_type": _emit_stream_type(expr.stream_type),
        }
    if isinstance(expr, IRConditional):
        return {
            "node": "conditional",
            "cond": _emit_expr(expr.cond, ctx),
            "then": _emit_expr(expr.then_branch, ctx),
            "else": _emit_expr(expr.else_branch, ctx),
            "stream_type": _emit_stream_type(expr.stream_type),
        }
    if isinstance(expr, IRBlockExpr):
        return {
            "node": "block",
            "kind": expr.kind,
            "fields": {k: _emit_expr(v, ctx) for k, v in expr.fields.items()},
        }
    raise TypeError(f"ir_json: don't know how to serialize {type(expr).__name__}")


# ---------------------------------------------------------------------------
# Protocol-shape serialization
# ---------------------------------------------------------------------------


def _emit_input(inp: IRInput, ctx: _EmitCtx) -> dict:
    return {
        "canonical_name": inp.canonical_name,
        "stream_type": _emit_stream_type(inp.stream_type),
        "montage": _emit_expr(inp.montage, ctx),
    }


def _emit_derive(d: IRDerive, ctx: _EmitCtx) -> dict:
    return {
        "canonical_name": d.canonical_name,
        "stream_type": _emit_stream_type(d.stream_type),
        "expression": _emit_expr(d.expression, ctx),
        # Sorted for a deterministic wire format (upstream derives from a set
        # upstream; order is irrelevant to evaluation but matters for
        # reproducible, diff-stable IR-JSON).
        "upstream": sorted(d.upstream),
    }


def _emit_threshold(t: IRThreshold, ctx: _EmitCtx) -> dict:
    return {
        "canonical_name": t.canonical_name,
        "signal": t.signal,
        "threshold_call": _emit_expr(t.threshold_call, ctx),
        "stream_type": _emit_stream_type(t.stream_type),
        "live_tunable": t.live_tunable,
    }


def _emit_inhibit(ih: IRInhibit, ctx: _EmitCtx) -> dict:
    return {
        "canonical_name": ih.canonical_name,
        "metric": _emit_expr(ih.metric, ctx),
        "threshold": _emit_expr(ih.threshold, ctx),
        "action_kind": ih.action_kind,
        "action_release_ms": ih.action_release_ms,
        "final": ih.final,
    }


def _emit_reward(r: IRReward, ctx: _EmitCtx, version: str) -> dict:
    base = {
        "continuous": _emit_expr(r.continuous, ctx) if r.continuous is not None else None,
        "event": _emit_expr(r.event, ctx) if r.event is not None else None,
    }
    if version == "0.1":
        # Byte-identical to the pre-v0.2 emitter: no components/combine keys.
        return base
    base["combine"] = r.combine
    base["components"] = [
        {
            "name": c.name,
            "canonical_name": c.canonical_name,
            "role": c.role,
            "signal": _emit_expr(c.signal, ctx),
            "weight": _emit_expr(c.weight, ctx) if c.weight is not None else None,
        }
        for c in r.components
    ]
    return base


def _emit_control(c: IRControl, ctx: _EmitCtx) -> dict:
    return {
        "canonical_name": c.canonical_name,
        "type_kind": c.type_kind,
        "dims": _emit_dims(c.dims),
        "default": _emit_expr(c.default, ctx) if c.default is not None else None,
        "range_low": _emit_expr(c.range_low, ctx) if c.range_low is not None else None,
        "range_high": _emit_expr(c.range_high, ctx) if c.range_high is not None else None,
        "log_scale": c.log_scale,
        "label": c.label,
        "live_tunable": c.live_tunable,
        "tune_strategy": c.tune_strategy,
    }


def _emit_phase(p: IRPhase) -> dict:
    return {
        "name": p.name,
        "duration_ms": p.duration_ms,
        "output_muted": p.output_muted,
        "mode": p.mode,
        "block": p.block,
    }


def _emit_block(b) -> dict:
    return {
        "name": b.name,
        "thresholds": list(b.thresholds),
        "reward": b.reward,
        "output": list(b.outputs),
        "inhibits": list(b.inhibits),
    }


def _emit_session(s: IRSession) -> dict:
    return {"phases": [_emit_phase(p) for p in s.phases]}


def _emit_requires(req: IRRequires) -> dict:
    return {
        "coupling": req.coupling,
        "sample_rate_min_hz": req.sample_rate_min_hz,
        "sample_rate_chosen_hz": req.sample_rate_chosen_hz,
        "channels": list(req.channels),
        "impedance": req.impedance,
        "markers": req.markers,
    }


def _emit_meta(meta: IRMeta, ctx: _EmitCtx) -> dict:
    return {k: _emit_expr(v, ctx) for k, v in meta.fields.items()}


def ir_to_json_obj(ir: IRProtocol, *, sample_rate_hz: float | None = None) -> dict:
    """Serialize a resolved protocol into a JSON-compatible dict.

    `sample_rate_hz` overrides the runtime sample rate used to bake
    coefficients and emitted as `sample_rate_hz`. The amp may support
    several rates at or above the protocol's minimum; the host chooses one,
    and coefficients must be baked at *that* rate. Defaults to the
    resolver's `sample_rate_chosen_hz`.
    """
    rate = float(sample_rate_hz) if sample_rate_hz is not None else ir.requires.sample_rate_chosen_hz
    ctx = _EmitCtx(
        sample_rate_hz=rate,
        channel_names=tuple(ir.requires.channels),
        controls=control_defaults(ir),
    )
    version = _protocol_ir_version(ir)
    return {
        "refrain_ir_version": version,
        "name": ir.name,
        "extends": ir.extends,
        "sample_rate_hz": rate,
        "channels": list(ir.requires.channels),
        "requires": _emit_requires(ir.requires),
        "meta": _emit_meta(ir.meta, ctx),
        "inputs": {name: _emit_input(i, ctx) for name, i in ir.inputs.items()},
        "derives": {name: _emit_derive(d, ctx) for name, d in ir.derives.items()},
        "thresholds": {name: _emit_threshold(t, ctx) for name, t in ir.thresholds.items()},
        "inhibits": {name: _emit_inhibit(ih, ctx) for name, ih in ir.inhibits.items()},
        "reward": _emit_reward(ir.reward, ctx, version),
        "output": {name: _emit_expr(expr, ctx) for name, expr in ir.output.items()},
        "controls": {
            name: _emit_control(c, ctx)
            for name, c in ir.controls.items()
            if c.type_kind not in ("placement", "mode")
        },
        "session": _emit_session(ir.session),
        "blocks": {name: _emit_block(b) for name, b in ir.blocks.items()},
        "reward_bundles": {
            name: _emit_reward(rb, ctx, version) for name, rb in ir.reward_bundles.items()
        },
        "topological_order": list(ir.topological_order),
    }


def ir_to_json(ir: IRProtocol, *, sample_rate_hz: float | None = None) -> str:
    """Serialize a resolved protocol into a JSON string.

    ``sort_keys`` is intentionally left ``False`` so the ``output`` section
    preserves the IR declaration order of output channels. The Rust runtime
    must emit events in that declaration order, matching Python's
    ``_process_chunk`` which iterates ``per_channel_output.items()`` in
    insertion order.  All other top-level dicts (inputs, derives, …) have
    no order-sensitive semantics and are sorted by their BTreeMap key order
    in the Rust deserialiser anyway.
    """
    return json.dumps(ir_to_json_obj(ir, sample_rate_hz=sample_rate_hz), indent=2)


__all__ = ["ir_to_json", "ir_to_json_obj", "IR_JSON_VERSION"]
