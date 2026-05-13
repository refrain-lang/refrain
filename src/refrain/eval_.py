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
    "coherence": ("input_a", "input_b"),
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
    if callee == "coherence" and "window" in out:
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
    """Runs a resolved `IRProtocol` against incoming sample chunks.

    Two construction modes:

      - `Evaluator(ir, source)` — *pull-mode*. Wraps a `Source` (file or
        synthetic) and yields events from `run()`. Best for offline
        replay and validation.

      - `Evaluator.live(ir, sample_rate_hz, channel_names)` — *push-mode*.
        Host applications (a recording app, an LSL bridge) hand chunks
        in via `step_chunk(chunk)` and receive the events that fired
        during that chunk. Best for live operation.

    Lifecycle (SPEC §7.1):

        ready  -- start() -->  warmup  -- (auto, after first muted phase)
                                                 \\
                                                  V
                                                  run  -- stop() --> stopped

    During `warmup`, primitive state (filter delay lines, percentile
    windows) continues to update, but output bindings are muted so the
    patient never hears artifacts from filter settling. The protocol
    declares the warmup window via `session.phases[0].output_muted`.

    Mid-session control tuning (SPEC §7.7) goes through `set_control`.
    Filter coefficients recompute warm-restart-style; primitive state
    is preserved across the change.
    """

    def __init__(
        self,
        ir: IRProtocol,
        source: Source | None = None,
        *,
        sample_rate_hz: float | None = None,
        channel_names: tuple[str, ...] | None = None,
    ):
        if source is not None:
            if sample_rate_hz is not None or channel_names is not None:
                raise ValueError(
                    "Evaluator: pass either a Source OR explicit "
                    "sample_rate_hz + channel_names, not both"
                )
            self.source = source
            self.sample_rate_hz = source.sample_rate_hz
            self.channel_names = source.channel_names
        else:
            if sample_rate_hz is None or channel_names is None:
                raise ValueError(
                    "Evaluator: in push-mode (no Source), both "
                    "sample_rate_hz and channel_names are required"
                )
            self.source = None
            self.sample_rate_hz = float(sample_rate_hz)
            self.channel_names = tuple(channel_names)
        self.ir = ir

        # impls keyed by id(IRCall). One instance per CALL SITE so state
        # is per-call-site, not shared.
        self._impls: dict[int, impls.PrimitiveImpl] = {}
        # Static control values, resolved once at construction.
        self._controls: dict[str, float] = self._resolve_controls()
        # `control_target -> [impls that consumed it at construction]`
        # so set_control(...) can forward updates.
        self._control_deps: dict[str, list[impls.PrimitiveImpl]] = {}
        # Pre-instantiate input/derive/threshold/inhibit primitives.
        self._build_pipeline()
        # Inhibit actions are at the output stage.
        self._inhibit_actions: dict[str, Any] = self._build_inhibit_actions()
        # Track output-binding canonical names for event emission order.
        self._output_channels = list(ir.output.keys())

        # Lifecycle state.
        self._state: str = "ready"
        self._samples_pushed: int = 0
        self._warmup_samples: int = self._compute_warmup_samples()

        # Per-chunk last-sample values for introspection (the tap API).
        # Repopulated every step_chunk call by `_capture_taps`. Empty
        # before the first step_chunk. See `last_taps()` for the schema.
        self._last_taps: dict[str, float | bool] = {}

    # -- Live-mode factory --------------------------------------------------

    @classmethod
    def live(
        cls,
        ir: IRProtocol,
        *,
        sample_rate_hz: float,
        channel_names: tuple[str, ...],
    ) -> Evaluator:
        """Construct a push-mode evaluator. The host calls `start()`,
        then `step_chunk(chunk)` per arriving sample chunk, then `stop()`."""
        return cls(ir, sample_rate_hz=sample_rate_hz, channel_names=channel_names)

    # -- Lifecycle ----------------------------------------------------------

    @property
    def state(self) -> str:
        """`"ready"` | `"warmup"` | `"run"` | `"stopped"`."""
        return self._state

    @property
    def warmup_remaining_s(self) -> float:
        """Seconds of warmup left, or 0 if not in warmup."""
        if self._state != "warmup":
            return 0.0
        remaining = max(0, self._warmup_samples - self._samples_pushed)
        return remaining / self.sample_rate_hz

    def start(self, *, skip_warmup: bool = False) -> None:
        """Enter `warmup` (or directly `run` if the protocol has no
        warmup-muted phase). Call before the first `step_chunk`.

        `skip_warmup=True` jumps straight to `run`, useful for offline
        analysis and tests where the warmup-output-muting behaviour is
        not the thing under test. Real clinical sessions should NEVER
        skip warmup — filter state and percentile windows need the
        warmup window to populate, and the patient would hear settling
        transients without it.
        """
        if self._state != "ready":
            raise RuntimeError(f"Evaluator.start() called in state {self._state!r}")
        self._samples_pushed = 0
        if skip_warmup or self._warmup_samples == 0:
            self._state = "run"
        else:
            self._state = "warmup"

    def stop(self) -> None:
        """End the session. Subsequent `step_chunk` calls raise."""
        self._state = "stopped"

    def _compute_warmup_samples(self) -> int:
        """Look at the first session phase; if `output_muted = true`, its
        duration becomes the warmup window. Otherwise the protocol enters
        `run` immediately."""
        if not self.ir.session.phases:
            return 0
        first = self.ir.session.phases[0]
        if not first.output_muted:
            return 0
        return int(round(first.duration_ms / 1000.0 * self.sample_rate_hz))

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
        # Note any IRControlRefs in the static args *before* substitution
        # so we can later forward set_control updates to this impl.
        control_targets = _collect_control_targets(static)
        # Substitute IRControlRefs with their resolved Python values
        # (SPEC §5.4 / §7.7). `bandpass(center: orf, ...)` picks up
        # `orf`'s default here; live retuning is wired via `set_control`.
        static = _substitute_controls(static, self._controls)
        if call.callee in ("mute", "freeze", "flag"):
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
        for target in control_targets:
            self._control_deps.setdefault(target, []).append(impl)

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

    # -- Run / step_chunk --------------------------------------------------

    def run(
        self,
        *,
        chunk_size: int = 64,
        skip_warmup: bool = False,
    ) -> Iterator[Event]:
        """Pull-mode runner: iterate over the configured Source, yielding
        events as they fire.

        Equivalent to repeatedly calling `step_chunk(chunk)`. The first
        chunk transitions the lifecycle from `ready` → `warmup` (or
        directly to `run` if the protocol has no warmup phase or
        `skip_warmup=True` was passed). See `start()` for the warmup
        rationale.
        """
        if self.source is None:
            raise RuntimeError(
                "Evaluator.run() requires a Source. For push-mode (without "
                "a Source), use Evaluator.live(...) and step_chunk()."
            )
        if self._state == "ready":
            self.start(skip_warmup=skip_warmup)
        for raw_chunk in self.source.iter_chunks(chunk_size):
            yield from self.step_chunk(raw_chunk)

    def step_chunk(self, raw_chunk: np.ndarray) -> list[Event]:
        """Push-mode chunk processor: consume one (n_samples, n_channels)
        chunk, return the events that fired during it.

        The first call after construction implicitly transitions from
        `ready` to `warmup`/`run`. During `warmup`, primitive state
        still updates (filters settle, percentile windows populate),
        but output events are suppressed so the patient doesn't hear
        artifacts. After enough samples have been pushed to satisfy the
        protocol's first muted phase, the evaluator transitions to `run`
        and starts emitting events.

        Re-shapes a 1-D chunk to (n, 1) for single-channel sources.
        """
        if self._state == "ready":
            self.start()
        if self._state == "stopped":
            raise RuntimeError("Evaluator.step_chunk() called after stop()")

        if raw_chunk.ndim == 1:
            raw_chunk = raw_chunk[:, None]
        if raw_chunk.shape[1] != len(self.channel_names):
            raise ValueError(
                f"step_chunk: chunk has {raw_chunk.shape[1]} channels "
                f"but evaluator was configured for {len(self.channel_names)} "
                f"({self.channel_names!r})"
            )

        actual_chunk_size = raw_chunk.shape[0]
        t0_s = self._samples_pushed / self.sample_rate_hz

        # Build per-chunk control broadcasts.
        control_chunks_cache: dict[str, np.ndarray] = {
            name: np.full(actual_chunk_size, val, dtype=np.float64)
            for name, val in self._controls.items()
        }

        events = list(self._process_chunk(raw_chunk, t0_s, control_chunks_cache))

        # Advance cursor and possibly transition warmup -> run.
        self._samples_pushed += actual_chunk_size
        if self._state == "warmup" and self._samples_pushed >= self._warmup_samples:
            self._state = "run"

        return events

    def _process_chunk(
        self,
        raw_chunk: np.ndarray,
        t0_s: float,
        control_chunks_cache: dict[str, np.ndarray],
    ) -> Iterator[Event]:
        """Walk the IR for one chunk. Always updates state; only emits
        events when state == 'run'."""
        actual_chunk_size = raw_chunk.shape[0]
        suppress_output = (self._state == "warmup")

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

        # Thresholds: per-threshold call, fed the signal it tracks.
        for t in self.ir.thresholds.values():
            impl = self._impls[id(t.threshold_call)]
            if isinstance(impl, impls.AbsoluteThresholdImpl):
                stream_values[t.canonical_name] = impl.step(np.zeros(actual_chunk_size))
            else:
                signal_chunk = stream_values[t.signal]
                stream_values[t.canonical_name] = impl.step(signal_chunk)

        # Inhibits: metric → threshold → boolean active stream.
        inhibit_active: dict[str, np.ndarray] = {}
        for ih in self.ir.inhibits.values():
            metric_chunk = self._eval_expr(
                ih.metric, stream_values, control_chunks_cache, actual_chunk_size
            )
            thresh_impl = self._impls[id(ih.threshold)]
            if isinstance(thresh_impl, impls.AbsoluteThresholdImpl):
                thresh_chunk = thresh_impl.step(np.zeros(actual_chunk_size))
            else:
                thresh_chunk = thresh_impl.step(metric_chunk)
            inhibit_active[ih.canonical_name] = metric_chunk > thresh_chunk

        # Reward
        reward_continuous: np.ndarray | None = None
        reward_event: impls.DwellResult | None = None
        reward_sub_chunks: list[np.ndarray] = []
        if self.ir.reward.continuous is not None:
            reward_continuous = self._eval_expr(
                self.ir.reward.continuous,
                stream_values, control_chunks_cache, actual_chunk_size,
            )
        if self.ir.reward.event is not None:
            reward_event, reward_sub_chunks = self._eval_reward_event(
                self.ir.reward.event,
                stream_values, control_chunks_cache, actual_chunk_size,
            )

        # Combined inhibit gate — also exposed as the `muted` tap.
        muted = self._compute_muted(inhibit_active, actual_chunk_size)

        # Pre-compute each output binding's gated/clamped values now so
        # we can capture them as `output/<channel>` taps *and* emit
        # them as events below. Tap capture lands BEFORE the warmup
        # early-return so a host clinician observation window can plot
        # envelopes / thresholds / output traces during warmup.
        per_channel_output: dict[str, tuple[np.ndarray, bool]] = {}
        for channel, expr in self.ir.output.items():
            values = self._eval_expr(
                expr,
                stream_values, control_chunks_cache, actual_chunk_size,
                reward_continuous=reward_continuous,
                reward_event=reward_event,
            )
            is_event = self._is_event_channel(expr)
            if is_event:
                # Event channels: gate by inhibits, keep as boolean.
                gated_bool = values.astype(bool) & ~muted
                per_channel_output[channel] = (gated_bool, True)
            else:
                clamped = np.clip(values, 0.0, 1.0)
                gated = np.where(muted, 0.0, clamped)
                per_channel_output[channel] = (gated, False)

        # Capture taps for introspection (SPEC §7.8 / EMBEDDING.md).
        # This populates `self._last_taps` and runs in BOTH warmup and
        # run states — the host wants warmup visibility.
        self._capture_taps(
            stream_values=stream_values,
            inhibit_active=inhibit_active,
            muted=muted,
            reward_continuous=reward_continuous,
            reward_event=reward_event,
            reward_sub_chunks=reward_sub_chunks,
            per_channel_output=per_channel_output,
        )

        # Output emission: during warmup we still computed everything
        # (so primitive state stays current and taps populate), but we
        # suppress events so the patient hears nothing while filters
        # settle.
        if suppress_output:
            return

        for channel, (out_chunk, is_event) in per_channel_output.items():
            if is_event:
                for i in range(actual_chunk_size):
                    if out_chunk[i]:
                        yield Event(
                            timestamp_s=t0_s + i / self.sample_rate_hz,
                            channel=channel,
                            kind="event",
                            value=None,
                        )
            else:
                yield Event(
                    timestamp_s=t0_s,
                    channel=channel,
                    kind="value",
                    value=float(np.mean(out_chunk)),
                )

    # -- Introspection: live taps (SPEC §7.8) ------------------------------

    def _capture_taps(
        self,
        *,
        stream_values: dict[str, np.ndarray],
        inhibit_active: dict[str, np.ndarray],
        muted: np.ndarray,
        reward_continuous: np.ndarray | None,
        reward_event: impls.DwellResult | None,
        reward_sub_chunks: list[np.ndarray],
        per_channel_output: dict[str, tuple[np.ndarray, bool]],
    ) -> None:
        """Repopulate `self._last_taps` from the current chunk's
        intermediate values. Called once per `_process_chunk` *before*
        the warmup output-suppression early-return so the host can
        plot envelopes / thresholds during warmup.

        Mutates the existing dict in place to avoid per-chunk
        allocation. The dict's keyset is stable for a given protocol,
        so re-creating the dict every step would be wasted work.
        """
        taps = self._last_taps

        # Inputs and derives — last sample of each canonical stream.
        for inp in self.ir.inputs.values():
            chunk = stream_values.get(inp.canonical_name)
            if chunk is not None and chunk.size:
                taps[inp.canonical_name] = float(chunk[-1])
        for d in self.ir.derives.values():
            chunk = stream_values.get(d.canonical_name)
            if chunk is not None and chunk.size:
                taps[d.canonical_name] = float(chunk[-1])

        # Thresholds — current adaptive value (last sample).
        for t in self.ir.thresholds.values():
            chunk = stream_values.get(t.canonical_name)
            if chunk is not None and chunk.size:
                taps[t.canonical_name] = float(chunk[-1])

        # Inhibits — last-sample boolean for each.
        for ih in self.ir.inhibits.values():
            chunk = inhibit_active.get(ih.canonical_name)
            if chunk is not None and chunk.size:
                taps[ih.canonical_name] = bool(chunk[-1])

        # Combined gate.
        if muted.size:
            taps["muted"] = bool(muted[-1])
        else:
            taps["muted"] = False

        # Reward.
        if reward_continuous is not None and reward_continuous.size:
            taps["reward/continuous"] = float(reward_continuous[-1])
        if reward_event is not None:
            # `reward/event` semantics: did the dwell fire ANY sample
            # in this chunk. The regular Event stream from step_chunk
            # is the source of truth for edge timing; this tap is a
            # quick "anything fired" indicator for the observation
            # window.
            taps["reward/event"] = bool(reward_event.events.any())
            if reward_event.holds.size:
                taps["reward/event.holds"] = bool(reward_event.holds[-1])
        # Sub-condition taps. `reward_sub_chunks` is empty when reward
        # has no event; populated with at least one element otherwise
        # (single-condition dwells are still exposed as
        # `reward/condition[0]` for uniform host iteration).
        for i, sub in enumerate(reward_sub_chunks):
            if sub.size:
                taps[f"reward/condition[{i}]"] = bool(sub[-1])

        # Output taps — what the patient-facing value actually was,
        # post-gating and post-clamp. For event channels we expose
        # "did the channel fire any sample this chunk."
        for channel, (out_chunk, is_event) in per_channel_output.items():
            key = f"output/{channel}"
            if out_chunk.size == 0:
                continue
            if is_event:
                taps[key] = bool(out_chunk.any())
            else:
                taps[key] = float(out_chunk[-1])

    def last_taps(self) -> dict[str, float | bool]:
        """Return last-sample values for internal taps from the most
        recent `step_chunk` call. Repopulated on every step_chunk;
        empty before the first one.

        A host application uses this to populate a clinician observation
        window — envelope traces per derive, threshold lines, dwell
        sub-condition tape, pre-gating reward.continuous overlay.

        Keys included when the corresponding entity exists in the
        resolved protocol:

          - "input/<name>"           last sample of post-montage input
          - "derive/<name>"          last sample of the derive's output
          - "threshold/<name>"       current threshold value (last sample)
          - "inhibit/<name>"         boolean: inhibit active this sample
          - "muted"                  boolean: combined inhibit gate
          - "reward/continuous"      pre-gating sigmoid value
          - "reward/event"           boolean: did dwell event fire any
                                     sample in this chunk (np.any)
          - "reward/event.holds"     boolean: dwell condition currently held
          - "reward/condition[i]"    boolean: i-th sub-condition. Uniform
                                     indexed form — single-condition
                                     dwells emit `reward/condition[0]`.
          - "output/<channel>"       post-gating, post-clamp value of
                                     the patient-facing channel

        Returns a copy, so callers can persist or aggregate without
        worrying about subsequent step_chunk mutating the snapshot.

        Populated identically during `warmup` and `run` states — hosts
        legitimately want to plot warmup progress.
        """
        return dict(self._last_taps)

    # -- Mid-session control tuning (SPEC §7.7) ----------------------------

    def set_control(self, name: str, value: float) -> None:
        """Update a control's runtime value and forward the change to any
        primitive impls that depend on it (warm-restart per SPEC §7.7 —
        impl state is preserved across the update).

        Looks up `name` against the protocol's `controls.<name>`
        declarations. Raises if the name doesn't match a declared
        control.

        Phase 0e-a scope: PercentileImpl (target_pct), SigmoidImpl
        (midpoint/steepness), SmoothImpl (tau). Other impls receiving a
        control change silently ignore — Phase 0e-c extends this.
        """
        target = f"control/{name}"
        if target not in self._controls:
            raise KeyError(f"no control named {name!r}")
        self._controls[target] = float(value)
        for impl in self._control_deps.get(target, []):
            updater = getattr(impl, "update_control", None)
            if updater is not None:
                updater(target, float(value))

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
    ) -> tuple[impls.DwellResult, list[np.ndarray]]:
        """The reward.event expression's top-level call is `dwell` (or
        another event-producing primitive). Special-cased so we keep the
        DwellResult around for `.events` and `.holds` view access.

        Returns (dwell_result, sub_condition_chunks) where
        sub_condition_chunks is a list of per-sub-condition boolean
        arrays — one element per `all_of` / `any_of` sub-expression,
        or one element wrapping the whole condition when it's not a
        compound. The host's `last_taps()` exposes these as
        `reward/condition[i]`.
        """
        if not isinstance(expr, IRCall) or expr.callee != "dwell":
            # For Phase 0d, only `dwell` is supported as a reward event source.
            raise NotImplementedError(
                f"reward.event must be a `dwell(...)` call; got {type(expr).__name__}"
            )
        impl = self._impls[id(expr)]
        # `dwell`'s dynamic input is the `condition` expression.
        _static, dynamic_exprs = _classify_call(expr)
        condition_expr = dynamic_exprs[0]

        # Sub-condition expansion for the tap API. When the condition
        # is `all_of([c0, c1, ...])` or `any_of([...])`, evaluate each
        # element individually so the host can see which sub-condition
        # is blocking reward. Then recombine for the dwell's input —
        # cheaper than re-evaluating, semantically identical to the
        # AllOfImpl / AnyOfImpl reduction (np.all / np.any).
        sub_chunks: list[np.ndarray]
        if (
            isinstance(condition_expr, IRCall)
            and condition_expr.callee in ("all_of", "any_of")
            and condition_expr.args
            and isinstance(condition_expr.args[0].value, IRArray)
        ):
            arr = condition_expr.args[0].value
            sub_chunks = [
                self._eval_expr(elt, stream_values, control_chunks, chunk_size)
                for elt in arr.elements
            ]
            stacked = np.stack(sub_chunks, axis=0)
            if condition_expr.callee == "all_of":
                condition_chunk = np.all(stacked, axis=0)
            else:
                condition_chunk = np.any(stacked, axis=0)
        else:
            # Single condition — expose it as `reward/condition[0]`
            # so the host iteration loop is uniform.
            condition_chunk = self._eval_expr(
                condition_expr, stream_values, control_chunks, chunk_size,
            )
            sub_chunks = [condition_chunk]

        return impl.step(condition_chunk), sub_chunks

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


def _collect_control_targets(value: Any) -> list[str]:
    """Walk a static-args structure looking for IRControlRefs; return
    the list of canonical control names they reference. Used by the
    evaluator to register control->impl dependencies for `set_control`."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, IRControlRef):
            out.append(v.target)
        elif isinstance(v, dict):
            for val in v.values():
                walk(val)
        elif isinstance(v, (tuple, list)):
            for item in v:
                walk(item)

    walk(value)
    return out


def _substitute_controls(value: Any, controls: dict[str, float]) -> Any:
    """Walk a static-args structure, replacing IRControlRefs with the
    corresponding control's resolved Python value.

    Handles nested dicts / tuples / lists so that constructs like
    `bandpass(center: orf, bandwidth: ratio(2.5))` see a float for
    `center` even though the IR carried an IRControlRef.
    """
    if isinstance(value, IRControlRef):
        if value.target not in controls:
            raise KeyError(
                f"control reference {value.target!r} has no resolved value"
            )
        return controls[value.target]
    if isinstance(value, dict):
        return {k: _substitute_controls(v, controls) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_substitute_controls(v, controls) for v in value)
    if isinstance(value, list):
        return [_substitute_controls(v, controls) for v in value]
    return value


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
    skip_warmup: bool = False,
) -> Iterator[Event]:
    """Run the IR against the source, yielding events.

    `skip_warmup=True` jumps the evaluator straight to `run` state,
    bypassing the protocol's session.phases[0] output-mute window.
    Useful for tests and offline analysis; never appropriate for live
    clinical use.
    """
    yield from Evaluator(ir, source).run(chunk_size=chunk_size, skip_warmup=skip_warmup)


__all__ = ["Evaluator", "Event", "eval_protocol"]
