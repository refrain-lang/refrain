# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain evaluator.

Walks an `IRProtocol` against an input `Source`, producing a stream of
events. Two phases:

  1. **Setup** — instantiate one `PrimitiveImpl` per `IRCall`,
     pre-resolve static arguments (numbers, strings, tuples), and
     classify which arguments are dynamic stream inputs that must be
     evaluated per-chunk. Pre-compute control values (Phase 0d controls
     are static at session start; the `--set control=value` ergonomic
     for runtime tuning is a Phase 0e concern).
  2. **Run** — per chunk pulled from the source, walk each IR stream's
     expression with primitive `.step()` calls, evaluate `reward.*`,
     apply output bindings, apply inhibit gating at the output stage,
     emit events.

Output is an iterator of `Event` records that the caller can print to
JSON-lines, aggregate, or stream to a downstream consumer.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import primitive_impls as impls
from .ir import (
    IRArray,
    IRBinaryOp,
    IRBlockExpr,
    IRBoolLit,
    IRCall,
    IRConditional,
    IRControlRef,
    IRDerive,
    IRExpr,
    IRInhibit,
    IRNumberLit,
    IRProtocol,
    IRRewardField,
    IRStreamRef,
    IRStringLit,
    IRThreshold,
    IRThresholdRef,
    IRTuple,
)
from .sources import Source


# ---------------------------------------------------------------------------
# Event records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Event:
    """One unit of evaluator output.

    `timestamp_s`  — start-of-recording-relative time in seconds
    `channel`      — the output-binding name (`audio_gain`, `audio_chime`, ...)
    `kind`         — `"value"` for analog channels, `"event"` for discrete
    `value`        — float in [0, 1] for analog; None for events
    """

    timestamp_s: float
    channel: str
    kind: str
    value: float | None


# ---------------------------------------------------------------------------
# Static-argument classification per primitive
# ---------------------------------------------------------------------------


# Maps primitive name -> tuple of `dynamic` arg keys (in order). All other
# args extracted from the IRCall become the static-args dict that the
# impl's __init__ receives.
#
# "_pos_0" / "_pos_1" mean "the Nth positional arg." Pipeline staging
# threads the previous stage's chunk as the first positional arg, so most
# transform primitives consume it that way.
_DYNAMIC_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "bipolar": ("_RAW",),       # special: receives the raw multi-channel chunk
    "referential": ("_RAW",),
    "bandpass": ("_pos_0",),
    "hilbert": ("_pos_0",),
    "magnitude": ("_pos_0",),
    "rectify": ("_pos_0",),
    "smooth": ("_pos_0",),
    "differentiate": ("_pos_0",),
    "percentile": ("signal",),   # threshold-type form
    "auto_range": ("_pos_0",),
    "absolute": (),
    "above": ("_pos_0", "_pos_1"),
    "below": ("_pos_0", "_pos_1"),
    "inside": ("_pos_0",),
    "all_of": ("_array",),       # special: array of conditions
    "any_of": ("_array",),
    "dwell": ("condition",),
    "sigmoid": ("_pos_0",),
    "linear": ("_pos_0",),
    "bandpower": ("input",),
    # threshold/inhibit constructors with no dynamic inputs
    "mute": (),
    "freeze": (),
    "flag": (),
    "ratio": (),
}


def _classify_call(call: IRCall) -> tuple[dict[str, Any], list[IRExpr]]:
    """Split an IRCall's args into (static_kwargs, dynamic_arg_exprs).

    `static_kwargs` ends up at the impl's __init__; `dynamic_arg_exprs`
    are IR expressions evaluated per chunk and passed to `.step()`.
    """
    dynamic_keys = _DYNAMIC_ARG_KEYS.get(call.callee, ())
    static: dict[str, Any] = {}
    dynamic: list[IRExpr] = []

    # Positional args by index.
    positional = [a for a in call.args if a.name is None]
    named = {a.name: a.value for a in call.args if a.name is not None}

    # Pull out dynamic args (by name or position).
    used_positional = set()
    for key in dynamic_keys:
        if key.startswith("_pos_"):
            idx = int(key.split("_")[-1])
            if idx < len(positional):
                dynamic.append(positional[idx].value)
                used_positional.add(idx)
        elif key == "_RAW":
            # Marked separately; the evaluator threads the raw chunk in.
            pass
        elif key == "_array":
            # all_of/any_of: the single positional arg is an array of
            # conditions; we expand it to a list of dynamic args.
            if not positional:
                raise ValueError(f"{call.callee}: expected an array of conditions")
            arr = positional[0].value
            used_positional.add(0)
            if not isinstance(arr, IRArray):
                raise ValueError(f"{call.callee}: expected an IRArray, got {type(arr).__name__}")
            dynamic.extend(arr.elements)
        else:
            if key in named:
                dynamic.append(named.pop(key))

    # Remaining positional args become static (used as `_pos_N` keys at
    # impl construction time if needed; for now only `bandpass` uses
    # positional `band` etc., which the resolver already named).
    for i, parg in enumerate(positional):
        if i in used_positional:
            continue
        # Some primitives take a value as a positional arg (e.g.
        # absolute(value)). Use _pos_N naming convention.
        static[f"_pos_{i}"] = _to_python_value(parg.value)

    # Named static args.
    for name, value in named.items():
        static[name] = _to_python_value(value)

    # Per-primitive static-arg massaging (rename numeric durations to *_ms etc.)
    static = _massage_static_args(call.callee, static)

    return static, dynamic


def _to_python_value(expr: IRExpr) -> Any:
    """Convert an IR literal-ish expression to a plain Python value
    usable as a primitive constructor argument."""
    if isinstance(expr, IRNumberLit):
        # Convert duration units to ms for uniform constructor handling.
        if expr.unit in ("ms",):
            return float(expr.value)
        if expr.unit == "s":
            return float(expr.value * 1000.0)
        if expr.unit == "min":
            return float(expr.value * 60_000.0)
        return float(expr.value)
    if isinstance(expr, IRStringLit):
        return expr.value
    if isinstance(expr, IRBoolLit):
        return expr.value
    if isinstance(expr, IRTuple):
        return tuple(_to_python_value(e) for e in expr.elements)
    if isinstance(expr, IRArray):
        return [_to_python_value(e) for e in expr.elements]
    if isinstance(expr, IRCall) and expr.callee == "ratio":
        # `ratio(R)` is a bandwidth constructor: extract the bare number.
        return _to_python_value(expr.args[0].value)
    if isinstance(expr, IRBlockExpr):
        return {k: _to_python_value(v) for k, v in expr.fields.items()}
    # Stream refs and other non-static expressions get returned as-is so
    # the caller can detect and reject them.
    return expr


def _massage_static_args(callee: str, static: dict[str, Any]) -> dict[str, Any]:
    """Rename a few primitive args so the impl constructors stay uniform.

    The IR keeps the original surface name (`tau` for smooth, `window`
    for percentile, etc.) which carries unit info via the float value's
    pre-conversion. Here we normalize to `*_ms` / `*_hz` for impls that
    expect those.
    """
    out = dict(static)
    if callee == "smooth" and "tau" in out:
        out["tau_ms"] = out.pop("tau")
    if callee == "percentile" and "window" in out:
        out["window_ms"] = out.pop("window")
    if callee == "auto_range" and "window" in out:
        out["window_ms"] = out.pop("window")
    if callee == "dwell" and "duration" in out:
        out["duration_ms"] = out.pop("duration")
    if callee == "bandpower" and "window" in out:
        out["window_ms"] = out.pop("window")
    if callee == "absolute" and "_pos_0" in out:
        out["value"] = out.pop("_pos_0")
    if callee == "above" or callee == "below":
        # signal/threshold passed dynamically; nothing to massage.
        pass
    if callee in ("mute", "freeze") and "release" in out:
        out["release_ms"] = out.pop("release")
    return out


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class Evaluator:
    """One-shot evaluator for an IRProtocol + Source.

    Construction does the setup phase: instantiate impls, resolve controls,
    cache the IR walk plan. `run()` is a generator of `Event` records.
    """

    def __init__(self, ir: IRProtocol, source: Source):
        self.ir = ir
        self.source = source
        self.sample_rate_hz = source.sample_rate_hz
        self.channel_names = source.channel_names

        # impls keyed by id(IRCall). One instance per CALL SITE so state
        # is per-call-site, not shared.
        self._impls: dict[int, impls.PrimitiveImpl] = {}
        # Static control values, resolved once.
        self._controls: dict[str, float] = self._resolve_controls()
        # Pre-instantiate input/derive/threshold/inhibit primitives.
        self._build_pipeline()
        # Inhibit actions are at the output stage.
        self._inhibit_actions: dict[str, Any] = self._build_inhibit_actions()
        # Track output-binding canonical names for event emission order.
        self._output_channels = list(ir.output.keys())

    # -- Setup -------------------------------------------------------------

    def _resolve_controls(self) -> dict[str, float]:
        """Read each control's `default` value at session start. Phase 0d
        keeps controls static; runtime tuning is Phase 0e."""
        out: dict[str, float] = {}
        for control in self.ir.controls.values():
            if control.default is not None and isinstance(control.default, IRNumberLit):
                # Default is in surface units; convert duration→ms when
                # relevant. For frequency / voltage / percent, raw value
                # is fine.
                val = float(control.default.value)
                if control.default.unit == "ms":
                    pass
                elif control.default.unit == "s":
                    val *= 1000.0
                elif control.default.unit == "min":
                    val *= 60_000.0
                out[control.canonical_name] = val
            else:
                out[control.canonical_name] = 0.0
        return out

    def _build_pipeline(self) -> None:
        # Inputs: each has a single montage call.
        for inp in self.ir.inputs.values():
            self._instantiate_call(inp.montage)
        # Derives, thresholds, inhibits: walk each expression looking for
        # IRCalls to instantiate. We instantiate them depth-first so
        # nested calls are constructed in their dependency order.
        for d in self.ir.derives.values():
            self._instantiate_expr(d.expression)
        for t in self.ir.thresholds.values():
            self._instantiate_call(t.threshold_call)
        for ih in self.ir.inhibits.values():
            self._instantiate_expr(ih.metric)
            self._instantiate_call(ih.threshold)
        # Reward expressions may contain calls.
        if self.ir.reward.continuous is not None:
            self._instantiate_expr(self.ir.reward.continuous)
        if self.ir.reward.event is not None:
            self._instantiate_expr(self.ir.reward.event)
        # Output bindings.
        for expr in self.ir.output.values():
            self._instantiate_expr(expr)

    def _instantiate_expr(self, expr: IRExpr) -> None:
        if isinstance(expr, IRCall):
            self._instantiate_call(expr)
        elif isinstance(expr, IRBinaryOp):
            self._instantiate_expr(expr.left)
            self._instantiate_expr(expr.right)
        elif isinstance(expr, IRConditional):
            self._instantiate_expr(expr.cond)
            self._instantiate_expr(expr.then_branch)
            self._instantiate_expr(expr.else_branch)
        elif isinstance(expr, IRArray):
            for elt in expr.elements:
                self._instantiate_expr(elt)
        # Other leaves (refs, literals, member access) don't need an impl.

    def _instantiate_call(self, call: IRCall) -> None:
        if id(call) in self._impls:
            return
        # First instantiate any nested call args.
        for arg in call.args:
            self._instantiate_expr(arg.value)
        static, _dynamic = _classify_call(call)
        if call.callee in ("mute", "freeze", "flag"):
            # These are inhibit-action constructors; the action object
            # lives in _inhibit_actions, not in _impls. Skip here.
            return
        if call.callee == "ratio":
            return  # bandwidth constructor; consumed by bandpass
        impl = impls.make_filter_impl(
            call.callee,
            static,
            sample_rate_hz=self.sample_rate_hz,
            channel_names=self.channel_names,
        )
        self._impls[id(call)] = impl

    def _build_inhibit_actions(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for ih in self.ir.inhibits.values():
            # The IRInhibit's action_kind tells us the family; we also
            # need the release_ms from the action call's args (if any).
            # The resolver already extracted action_release_ms.
            release_ms = ih.action_release_ms or 200.0
            static = {"release_ms": release_ms}
            try:
                act = impls.make_action(
                    ih.action_kind, static, sample_rate_hz=self.sample_rate_hz
                )
            except NotImplementedError:
                act = impls.FlagAction()
            out[ih.canonical_name] = act
        return out

    # -- Run ---------------------------------------------------------------

    def run(self, *, chunk_size: int = 64) -> Iterator[Event]:
        """Yield events one chunk at a time."""
        # Stream values keyed by canonical name (input/derive/threshold/inhibit).
        # Each chunk we recompute them in topological order.
        cursor_samples = 0
        # Cache static control-value chunks.
        control_chunks_cache: dict[str, np.ndarray] = {}

        for raw_chunk in self.source.iter_chunks(chunk_size):
            actual_chunk_size = raw_chunk.shape[0]
            t0_s = cursor_samples / self.sample_rate_hz

            # Refresh control chunks if size changed.
            if not control_chunks_cache or next(iter(control_chunks_cache.values())).shape[0] != actual_chunk_size:
                control_chunks_cache = {
                    name: np.full(actual_chunk_size, val, dtype=np.float64)
                    for name, val in self._controls.items()
                }

            stream_values: dict[str, np.ndarray] = {}

            # Inputs
            for inp in self.ir.inputs.values():
                impl = self._impls[id(inp.montage)]
                stream_values[inp.canonical_name] = impl.step(raw_chunk)

            # Derives
            for d in self.ir.derives.values():
                stream_values[d.canonical_name] = self._eval_expr(
                    d.expression, stream_values, control_chunks_cache, actual_chunk_size
                )

            # Thresholds: the threshold's value stream = call(threshold_call) on the
            # source signal (referenced as IRStreamRef.signal — already in stream_values).
            for t in self.ir.thresholds.values():
                impl = self._impls[id(t.threshold_call)]
                # PercentileImpl is fed the signal it tracks; absolute is constant.
                if isinstance(impl, impls.AbsoluteThresholdImpl):
                    stream_values[t.canonical_name] = impl.step(np.zeros(actual_chunk_size))
                else:
                    signal_chunk = stream_values[t.signal]
                    stream_values[t.canonical_name] = impl.step(signal_chunk)

            # Inhibits: metric → threshold → boolean active stream.
            # Stored under canonical_name as the BOOLEAN active stream
            # (not the metric value itself).
            inhibit_active: dict[str, np.ndarray] = {}
            for ih in self.ir.inhibits.values():
                metric_chunk = self._eval_expr(
                    ih.metric, stream_values, control_chunks_cache, actual_chunk_size
                )
                # Threshold call: a percentile/absolute over the metric.
                thresh_impl = self._impls[id(ih.threshold)]
                if isinstance(thresh_impl, impls.AbsoluteThresholdImpl):
                    thresh_chunk = thresh_impl.step(np.zeros(actual_chunk_size))
                else:
                    thresh_chunk = thresh_impl.step(metric_chunk)
                inhibit_active[ih.canonical_name] = metric_chunk > thresh_chunk

            # Reward
            reward_continuous: np.ndarray | None = None
            reward_event: impls.DwellResult | None = None
            if self.ir.reward.continuous is not None:
                reward_continuous = self._eval_expr(
                    self.ir.reward.continuous,
                    stream_values,
                    control_chunks_cache,
                    actual_chunk_size,
                )
            if self.ir.reward.event is not None:
                # The event expression's top-level call should be `dwell`.
                reward_event = self._eval_reward_event(
                    self.ir.reward.event,
                    stream_values,
                    control_chunks_cache,
                    actual_chunk_size,
                )

            # Output bindings + inhibit gating
            for channel, expr in self.ir.output.items():
                values = self._eval_expr(
                    expr,
                    stream_values,
                    control_chunks_cache,
                    actual_chunk_size,
                    reward_continuous=reward_continuous,
                    reward_event=reward_event,
                )
                # Apply inhibits at the output stage.
                muted = self._compute_muted(inhibit_active, actual_chunk_size)
                # Detect event channels by their reward.event binding.
                if self._is_event_channel(expr):
                    # Emit a discrete event per True sample.
                    for i in range(actual_chunk_size):
                        if values[i] and not muted[i]:
                            yield Event(
                                timestamp_s=t0_s + i / self.sample_rate_hz,
                                channel=channel,
                                kind="event",
                                value=None,
                            )
                else:
                    # Analog channel: emit chunked summary (one Event
                    # carrying chunk-mean) plus implicit clamping to [0, 1].
                    clamped = np.clip(values, 0.0, 1.0)
                    gated = np.where(muted, 0.0, clamped)
                    yield Event(
                        timestamp_s=t0_s,
                        channel=channel,
                        kind="value",
                        value=float(np.mean(gated)),
                    )

            cursor_samples += actual_chunk_size

    # -- Helpers ----------------------------------------------------------

    def _eval_expr(
        self,
        expr: IRExpr,
        stream_values: dict[str, np.ndarray],
        control_chunks: dict[str, np.ndarray],
        chunk_size: int,
        *,
        reward_continuous: np.ndarray | None = None,
        reward_event: impls.DwellResult | None = None,
    ) -> np.ndarray:
        if isinstance(expr, IRNumberLit):
            return np.full(chunk_size, _scale_to_ms_if_duration(expr), dtype=np.float64)
        if isinstance(expr, IRBoolLit):
            return np.full(chunk_size, expr.value, dtype=bool)
        if isinstance(expr, IRStringLit):
            # A string in an arithmetic context is a value (rare); use 0
            # as a safe fallback. The resolver's type check should have
            # rejected this if it would matter.
            return np.zeros(chunk_size, dtype=np.float64)
        if isinstance(expr, IRStreamRef):
            return stream_values[expr.target]
        if isinstance(expr, IRThresholdRef):
            return stream_values[expr.target]
        if isinstance(expr, IRControlRef):
            return control_chunks[expr.target]
        if isinstance(expr, IRRewardField):
            if expr.field_path == "continuous":
                if reward_continuous is None:
                    return np.zeros(chunk_size, dtype=np.float64)
                return reward_continuous
            if expr.field_path == "event":
                if reward_event is None:
                    return np.zeros(chunk_size, dtype=bool)
                return reward_event.events
            if expr.field_path == "event.holds":
                if reward_event is None:
                    return np.zeros(chunk_size, dtype=bool)
                return reward_event.holds
            raise ValueError(f"unknown reward field {expr.field_path!r}")
        if isinstance(expr, IRBinaryOp):
            left = self._eval_expr(
                expr.left, stream_values, control_chunks, chunk_size,
                reward_continuous=reward_continuous, reward_event=reward_event,
            )
            right = self._eval_expr(
                expr.right, stream_values, control_chunks, chunk_size,
                reward_continuous=reward_continuous, reward_event=reward_event,
            )
            return _apply_binop(expr.op, left, right)
        if isinstance(expr, IRConditional):
            cond = self._eval_expr(
                expr.cond, stream_values, control_chunks, chunk_size,
                reward_continuous=reward_continuous, reward_event=reward_event,
            )
            t = self._eval_expr(
                expr.then_branch, stream_values, control_chunks, chunk_size,
                reward_continuous=reward_continuous, reward_event=reward_event,
            )
            e = self._eval_expr(
                expr.else_branch, stream_values, control_chunks, chunk_size,
                reward_continuous=reward_continuous, reward_event=reward_event,
            )
            return np.where(cond, t, e)
        if isinstance(expr, IRCall):
            return self._eval_call(
                expr, stream_values, control_chunks, chunk_size,
                reward_continuous=reward_continuous, reward_event=reward_event,
            )
        if isinstance(expr, IRArray):
            # An array used as a value (rare outside primitive args).
            raise ValueError("IRArray not directly evaluable as a stream")
        raise ValueError(f"can't evaluate {type(expr).__name__} as a stream expression")

    def _eval_call(
        self,
        call: IRCall,
        stream_values: dict[str, np.ndarray],
        control_chunks: dict[str, np.ndarray],
        chunk_size: int,
        *,
        reward_continuous: np.ndarray | None = None,
        reward_event: impls.DwellResult | None = None,
    ) -> np.ndarray:
        if call.callee == "ratio":
            # Used only inside bandpass; not a stream-producing call.
            raise ValueError("ratio() should not appear as a stream-producing call")
        impl = self._impls[id(call)]
        _static, dynamic_exprs = _classify_call(call)
        # Evaluate each dynamic arg to a chunk.
        dynamic_chunks: list[np.ndarray] = []
        for e in dynamic_exprs:
            dynamic_chunks.append(
                self._eval_expr(
                    e, stream_values, control_chunks, chunk_size,
                    reward_continuous=reward_continuous, reward_event=reward_event,
                )
            )
        return impl.step(*dynamic_chunks)

    def _eval_reward_event(
        self,
        expr: IRExpr,
        stream_values: dict[str, np.ndarray],
        control_chunks: dict[str, np.ndarray],
        chunk_size: int,
    ) -> impls.DwellResult:
        """The reward.event expression's top-level call is `dwell` (or
        another event-producing primitive). Special-cased so we keep the
        DwellResult around for `.events` and `.holds` view access."""
        if not isinstance(expr, IRCall) or expr.callee != "dwell":
            # For Phase 0d, only `dwell` is supported as a reward event source.
            raise NotImplementedError(
                f"reward.event must be a `dwell(...)` call; got {type(expr).__name__}"
            )
        impl = self._impls[id(expr)]
        # `dwell`'s dynamic input is the `condition` expression.
        _static, dynamic_exprs = _classify_call(expr)
        condition_chunk = self._eval_expr(
            dynamic_exprs[0], stream_values, control_chunks, chunk_size,
        )
        return impl.step(condition_chunk)

    def _compute_muted(
        self, inhibit_active: dict[str, np.ndarray], chunk_size: int
    ) -> np.ndarray:
        """Combine all inhibits' output gates into a single boolean
        `output is muted this sample` stream."""
        if not inhibit_active:
            return np.zeros(chunk_size, dtype=bool)
        muted = np.zeros(chunk_size, dtype=bool)
        for canonical, active in inhibit_active.items():
            action = self._inhibit_actions.get(canonical)
            if action is None or isinstance(action, impls.FlagAction):
                continue
            muted |= action.gate(active)
        return muted

    @staticmethod
    def _is_event_channel(expr: IRExpr) -> bool:
        """Heuristic: a binding to `reward.event` (directly) drives an
        event-channel output. Bindings to `reward.event.holds` or other
        expressions produce analog values."""
        return isinstance(expr, IRRewardField) and expr.field_path == "event"


# ---------------------------------------------------------------------------
# Free-standing helpers
# ---------------------------------------------------------------------------


def _apply_binop(op: str, left: np.ndarray, right: np.ndarray) -> np.ndarray:
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        # Avoid divide-by-zero noise. NF protocols dividing by a tracker
        # that hasn't warmed up will see large values briefly; that's OK.
        with np.errstate(divide="ignore", invalid="ignore"):
            out = left / right
        return np.where(np.isfinite(out), out, 0.0)
    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op == "<=":
        return left <= right
    if op == ">=":
        return left >= right
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    raise ValueError(f"unsupported binary op: {op}")


def _scale_to_ms_if_duration(n: IRNumberLit) -> float:
    """For inline arithmetic, return the bare numeric value with no
    unit conversion. (Static-arg conversion lives in `_to_python_value`.)"""
    return float(n.value)


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def eval_protocol(
    ir: IRProtocol,
    source: Source,
    *,
    chunk_size: int = 64,
) -> Iterator[Event]:
    """Run the IR against the source, yielding events."""
    yield from Evaluator(ir, source).run(chunk_size=chunk_size)


__all__ = ["Evaluator", "Event", "eval_protocol"]
