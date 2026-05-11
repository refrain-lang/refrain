# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Typed signature registry for the Refrain v0.0r1 standard library.

Each primitive in `PRIMITIVES.md` that the three Phase-0 examples use
has an entry here. Sessions 2+ will grow the registry to full PRIMITIVES.md
coverage as more protocols come online.

A primitive entry carries:
  - the parameter signature (positional vs named, types, defaults)
  - the output stream type (in terms of the input stream type)
  - the resource budget (state_kb, worst_case_us)
  - the category (signal-stream primitive, threshold-type constructor,
    inhibit-action constructor)

The resolver looks up a `Call`'s callee in this registry; if the callee
isn't found, it's either a `custom` primitive (declared in the protocol)
or an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .types_ import (
    BOOLEAN_STREAM,
    DIMENSIONLESS,
    EVENT_STREAM,
    FREQUENCY,
    TIME,
    VOLTAGE,
    VOLTAGE_PER_TIME,
    VOLTAGE_SQ,
    Dimensions,
    StreamType,
    complex_stream,
    scalar_stream,
)


# ---------------------------------------------------------------------------
# Parameter / signature descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    state_kb: int
    worst_case_us: int


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One named parameter in a primitive's signature."""

    name: str
    # `kind` is a coarse "what is this thing?" tag rather than a full
    # type expression. Session 2's resolver matches against these tags;
    # finer typing (e.g. specific Dimensions on a Number) is handled in
    # the resolver's per-primitive checks.
    kind: str
    # Possible kinds:
    #   "number"          — a numeric literal (any unit)
    #   "number_dim"      — number with specific dimensions (see `dims`)
    #   "duration"        — number with TIME dims
    #   "frequency"       — number with FREQUENCY dims
    #   "voltage"         — number with VOLTAGE dims
    #   "ratio"           — dimensionless number
    #   "string"          — string literal
    #   "stream_ref"      — a string-lit that names a derive/input/threshold
    #   "threshold_ref"   — a string-lit that names a threshold
    #   "channel_name"    — a string-lit naming a physical channel
    #   "stream"          — an expression producing a stream
    #   "condition"       — a boolean stream (or `all_of`/`any_of` call)
    #   "array_of_strings" — array of string literals
    #   "array_of_conditions" — array of boolean-stream expressions
    #   "tuple_2_numbers" — a 2-tuple of numbers (band edges, percentile pair)
    #   "ratio_call"      — a `ratio(R)` constructor call (for bandpass center form)
    dims: Dimensions | None = None
    default: object | None = None
    required: bool = True


@dataclass(frozen=True, slots=True)
class Signature:
    """One (possibly overloaded) shape of a primitive."""

    params: tuple[ParamSpec, ...]
    # Computed output type. Callable so it can depend on the input
    # (e.g. `differentiate` returns input-units / s).
    output: Callable[[dict[str, "ResolvedArg"]], StreamType]


@dataclass(frozen=True, slots=True)
class ResolvedArg:
    """A type-resolved argument as the registry sees it. Used by output
    factories to pick up the input stream's type for unit propagation."""

    name: str | None
    stream_type: StreamType | None  # for stream inputs
    dims: Dimensions | None         # for numeric inputs


@dataclass(frozen=True, slots=True)
class PrimitiveSpec:
    """The full registry entry for one primitive name."""

    name: str
    category: str   # "signal" | "threshold_ctor" | "inhibit_action" | "ratio_ctor"
    signatures: tuple[Signature, ...]
    budget: ResourceBudget
    doc: str = field(default="")


# ---------------------------------------------------------------------------
# Output-type factories (closures over the resolved arg dict)
# ---------------------------------------------------------------------------


def _identity_input(args: dict[str, ResolvedArg]) -> StreamType:
    """Output type equals the input stream's type."""
    inp = args.get("__input__") or args.get("signal") or args.get("input")
    if inp is None or inp.stream_type is None:
        return scalar_stream(VOLTAGE)
    return inp.stream_type


def _const_scalar(dims: Dimensions) -> Callable[..., StreamType]:
    def f(args: dict[str, ResolvedArg]) -> StreamType:
        return scalar_stream(dims)
    return f


def _const_complex(dims: Dimensions) -> Callable[..., StreamType]:
    def f(args: dict[str, ResolvedArg]) -> StreamType:
        return complex_stream(dims)
    return f


def _bandpower_output(args: dict[str, ResolvedArg]) -> StreamType:
    return scalar_stream(VOLTAGE_SQ)


def _differentiate_output(args: dict[str, ResolvedArg]) -> StreamType:
    inp = args.get("__input__")
    if inp is None or inp.stream_type is None:
        return scalar_stream(VOLTAGE_PER_TIME)
    # Unit / s — only meaningful for scalar streams.
    new_dims = inp.stream_type.dimensions / TIME
    return scalar_stream(new_dims)


def _magnitude_output(args: dict[str, ResolvedArg]) -> StreamType:
    """`magnitude(complex T)` -> scalar T."""
    inp = args.get("__input__")
    if inp is None or inp.stream_type is None:
        return scalar_stream(VOLTAGE)
    return scalar_stream(inp.stream_type.dimensions)


def _rectify_output(args: dict[str, ResolvedArg]) -> StreamType:
    return _magnitude_output(args)


def _smooth_output(args: dict[str, ResolvedArg]) -> StreamType:
    return _identity_input(args)


def _auto_range_output(args: dict[str, ResolvedArg]) -> StreamType:
    return scalar_stream(DIMENSIONLESS)


def _percentile_output(args: dict[str, ResolvedArg]) -> StreamType:
    """`percentile` over a stream tracks that stream's unit."""
    inp = args.get("signal") or args.get("__input__")
    if inp is None or inp.stream_type is None:
        return scalar_stream(VOLTAGE)
    return scalar_stream(inp.stream_type.dimensions)


def _sigmoid_output(args: dict[str, ResolvedArg]) -> StreamType:
    return scalar_stream(DIMENSIONLESS)


def _linear_output(args: dict[str, ResolvedArg]) -> StreamType:
    return scalar_stream(DIMENSIONLESS)


def _boolean_stream_output(args: dict[str, ResolvedArg]) -> StreamType:
    return BOOLEAN_STREAM


def _event_stream_output(args: dict[str, ResolvedArg]) -> StreamType:
    return EVENT_STREAM


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


# Acquisition --------------------------------------------------------------

_BIPOLAR = PrimitiveSpec(
    name="bipolar",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("plus", "channel_name"),
                ParamSpec("minus", "channel_name"),
            ),
            output=_const_scalar(VOLTAGE),
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=5),
    doc="Differential signal between two physical electrodes.",
)

_REFERENTIAL = PrimitiveSpec(
    name="referential",
    category="signal",
    signatures=(
        # Scalar form: active + reference channel
        Signature(
            params=(
                ParamSpec("active", "channel_name"),
                ParamSpec("reference", "channel_name"),
            ),
            output=_const_scalar(VOLTAGE),
        ),
        # Vector form: array of channels + reference. Output is vector.
        Signature(
            params=(
                ParamSpec("channels", "array_of_strings"),
                ParamSpec("reference", "channel_name"),
            ),
            output=lambda args: StreamType(
                "vector",
                dimensions=VOLTAGE,
                vector_size=len(args["channels"].dims) if False else _vector_size_from(args, "channels"),
            ),
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=10),
    doc="Single-active or multi-channel referential signal.",
)


def _vector_size_from(args: dict[str, ResolvedArg], key: str) -> int:
    """Extract vector size hint from an array-of-strings argument."""
    arg = args.get(key)
    if arg is None or arg.stream_type is None:
        return 1
    return arg.stream_type.vector_size or 1


# Spectral -----------------------------------------------------------------

_BANDPASS = PrimitiveSpec(
    name="bandpass",
    category="signal",
    signatures=(
        # Edge-frequency form
        Signature(
            params=(
                ParamSpec("band", "tuple_2_numbers", dims=FREQUENCY),
                ParamSpec("order", "ratio", default=4, required=False),
            ),
            output=_identity_input,
        ),
        # Center-bandwidth form
        Signature(
            params=(
                ParamSpec("center", "frequency"),
                ParamSpec("bandwidth", "ratio_call_or_tuple"),
                ParamSpec("order", "ratio", default=4, required=False),
            ),
            output=_identity_input,
        ),
    ),
    budget=ResourceBudget(state_kb=2, worst_case_us=20),
    doc="Butterworth biquad bandpass.",
)

_HILBERT = PrimitiveSpec(
    name="hilbert",
    category="signal",
    signatures=(
        Signature(
            params=(),
            output=lambda args: complex_stream(
                _identity_input(args).dimensions
            ),
        ),
    ),
    budget=ResourceBudget(state_kb=4, worst_case_us=40),
    doc="Analytic signal via Hilbert transform.",
)

_BANDPOWER = PrimitiveSpec(
    name="bandpower",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("input", "stream_ref"),
                ParamSpec("band", "tuple_2_numbers", dims=FREQUENCY),
                ParamSpec("window", "duration"),
            ),
            output=_bandpower_output,
        ),
    ),
    budget=ResourceBudget(state_kb=8, worst_case_us=60),
    doc="Sliding-window total power in a frequency band.",
)


# Time-series math ---------------------------------------------------------

_DIFFERENTIATE = PrimitiveSpec(
    name="differentiate",
    category="signal",
    signatures=(
        Signature(params=(), output=_differentiate_output),
    ),
    budget=ResourceBudget(state_kb=1, worst_case_us=5),
    doc="First-order time derivative.",
)

_MAGNITUDE = PrimitiveSpec(
    name="magnitude",
    category="signal",
    signatures=(
        Signature(params=(), output=_magnitude_output),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=5),
    doc="Absolute value of a complex stream.",
)

_RECTIFY = PrimitiveSpec(
    name="rectify",
    category="signal",
    signatures=(
        Signature(params=(), output=_rectify_output),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=5),
    doc="Absolute value of a real-valued stream.",
)

_SMOOTH = PrimitiveSpec(
    name="smooth",
    category="signal",
    signatures=(
        Signature(
            params=(ParamSpec("tau", "duration"),),
            output=_smooth_output,
        ),
    ),
    budget=ResourceBudget(state_kb=1, worst_case_us=5),
    doc="Exponential-moving-average smoother.",
)


# Statistics ---------------------------------------------------------------

_AUTO_RANGE = PrimitiveSpec(
    name="auto_range",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("window", "duration"),
                ParamSpec("percentile", "tuple_2_numbers", default=None, required=False),
            ),
            output=_auto_range_output,
        ),
    ),
    budget=ResourceBudget(state_kb=16, worst_case_us=40),
    doc="Roll a stream to [0, 1] via P^2 percentile tracker.",
)


# Mappings -----------------------------------------------------------------

_SIGMOID = PrimitiveSpec(
    name="sigmoid",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("__input__", "stream_ref_or_expr"),
                ParamSpec("midpoint", "number"),
                ParamSpec("steepness", "ratio"),
            ),
            output=_sigmoid_output,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=5),
    doc="Logistic mapping to [0, 1].",
)

_LINEAR = PrimitiveSpec(
    name="linear",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("__input__", "stream_ref_or_expr"),
                ParamSpec("midpoint", "number"),
                ParamSpec("slope", "ratio"),
            ),
            output=_linear_output,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=5),
    doc="Linear mapping (downstream clamps to [0, 1] for analog outputs).",
)


# Conditions ---------------------------------------------------------------

_ABOVE = PrimitiveSpec(
    name="above",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("signal", "stream_ref"),
                ParamSpec("threshold", "threshold_ref_or_number"),
            ),
            output=_boolean_stream_output,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=2),
    doc="True when signal > threshold.",
)

_BELOW = PrimitiveSpec(
    name="below",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("signal", "stream_ref"),
                ParamSpec("threshold", "threshold_ref_or_number"),
            ),
            output=_boolean_stream_output,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=2),
    doc="True when signal < threshold.",
)

_INSIDE = PrimitiveSpec(
    name="inside",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("signal", "stream_ref"),
                ParamSpec("low", "number"),
                ParamSpec("high", "number"),
            ),
            output=_boolean_stream_output,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=2),
    doc="True when low <= signal <= high.",
)

_ALL_OF = PrimitiveSpec(
    name="all_of",
    category="signal",
    signatures=(
        Signature(
            params=(ParamSpec("conditions", "array_of_conditions"),),
            output=_boolean_stream_output,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=2),
    doc="Boolean AND over an array of conditions.",
)

_ANY_OF = PrimitiveSpec(
    name="any_of",
    category="signal",
    signatures=(
        Signature(
            params=(ParamSpec("conditions", "array_of_conditions"),),
            output=_boolean_stream_output,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=2),
    doc="Boolean OR over an array of conditions.",
)


# Event-producing ----------------------------------------------------------

_DWELL = PrimitiveSpec(
    name="dwell",
    category="signal",
    signatures=(
        Signature(
            params=(
                ParamSpec("condition", "condition"),
                ParamSpec("duration", "duration"),
            ),
            output=_event_stream_output,
        ),
    ),
    budget=ResourceBudget(state_kb=2, worst_case_us=5),
    doc="Emit event when condition has been true for `duration`.",
)


# Threshold-type constructors ---------------------------------------------

_ABSOLUTE = PrimitiveSpec(
    name="absolute",
    category="threshold_ctor",
    signatures=(
        Signature(
            params=(ParamSpec("value", "number"),),
            output=lambda args: scalar_stream(VOLTAGE),  # placeholder; resolver handles
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=0),
    doc="Fixed threshold value.",
)

_PERCENTILE = PrimitiveSpec(
    name="percentile",
    category="threshold_ctor",
    signatures=(
        Signature(
            params=(
                ParamSpec("target_pct", "ratio"),
                ParamSpec("window", "duration"),
            ),
            output=_percentile_output,
        ),
    ),
    budget=ResourceBudget(state_kb=16, worst_case_us=20),
    doc="Adaptive percentile threshold.",
)


# Inhibit-action constructors ---------------------------------------------

_MUTE = PrimitiveSpec(
    name="mute",
    category="inhibit_action",
    signatures=(
        Signature(
            params=(ParamSpec("release", "duration"),),
            output=lambda args: BOOLEAN_STREAM,  # not a stream; placeholder
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=0),
    doc="Gate output to zero, hold for `release` after metric clears.",
)

_FREEZE = PrimitiveSpec(
    name="freeze",
    category="inhibit_action",
    signatures=(
        Signature(
            params=(ParamSpec("release", "duration"),),
            output=lambda args: BOOLEAN_STREAM,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=0),
    doc="Hold output at last value, release after `release`.",
)

_FLAG = PrimitiveSpec(
    name="flag",
    category="inhibit_action",
    signatures=(
        Signature(
            params=(),
            output=lambda args: BOOLEAN_STREAM,
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=0),
    doc="Emit telemetry only; do not modify output.",
)


# Helper / utility ---------------------------------------------------------

_RATIO = PrimitiveSpec(
    name="ratio",
    category="ratio_ctor",
    signatures=(
        Signature(
            params=(ParamSpec("value", "ratio"),),
            output=lambda args: scalar_stream(DIMENSIONLESS),
        ),
    ),
    budget=ResourceBudget(state_kb=0, worst_case_us=0),
    doc="Bandwidth-ratio constructor (used in bandpass center/bandwidth form).",
)


# Registry map -------------------------------------------------------------

_REGISTRY: dict[str, PrimitiveSpec] = {
    spec.name: spec
    for spec in [
        _BIPOLAR,
        _REFERENTIAL,
        _BANDPASS,
        _HILBERT,
        _BANDPOWER,
        _DIFFERENTIATE,
        _MAGNITUDE,
        _RECTIFY,
        _SMOOTH,
        _AUTO_RANGE,
        _SIGMOID,
        _LINEAR,
        _ABOVE,
        _BELOW,
        _INSIDE,
        _ALL_OF,
        _ANY_OF,
        _DWELL,
        _ABSOLUTE,
        _PERCENTILE,
        _MUTE,
        _FREEZE,
        _FLAG,
        _RATIO,
    ]
}


def lookup(name: str) -> PrimitiveSpec | None:
    """Return the registry entry for `name`, or None if absent."""
    return _REGISTRY.get(name)


def names() -> list[str]:
    """All registered primitive names (sorted for stability)."""
    return sorted(_REGISTRY)


def is_signal_primitive(name: str) -> bool:
    spec = _REGISTRY.get(name)
    return spec is not None and spec.category == "signal"


def is_threshold_ctor(name: str) -> bool:
    spec = _REGISTRY.get(name)
    return spec is not None and spec.category == "threshold_ctor"


def is_inhibit_action(name: str) -> bool:
    spec = _REGISTRY.get(name)
    return spec is not None and spec.category == "inhibit_action"


__all__ = [
    "ParamSpec",
    "PrimitiveSpec",
    "ResourceBudget",
    "ResolvedArg",
    "Signature",
    "lookup",
    "names",
    "is_signal_primitive",
    "is_threshold_ctor",
    "is_inhibit_action",
]
