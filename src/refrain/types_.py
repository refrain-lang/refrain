# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Refrain type system: dimensional units and stream types.

Two compactly-defined layers:

  1. `Dimensions` — exponent tuple over the base dimensions Refrain
     uses (time, voltage and temperature). Surface units (`Hz`, `ms`,
     `min`, `s`, `uV`, `uV2`, `C`, `%`, dimensionless) map onto Dimensions
     instances via `unit_dims()`. Arithmetic on Dimensions (`__mul__`,
     `__truediv__`, `__pow__`) is exponent addition; `dims_sqrt()` is
     exponent halving, which only some dimensions admit.

  2. `StreamType` — a stream's value-kind (`scalar` / `vector` / `boolean`
     / `event` / `complex`) plus its dimensions and (optional) vector size.

Rate tracking (Hz at which each stream emits samples) is deliberately
NOT in this version. SPEC §6.6 mandates rate-alignment checks, but the
three Phase-0 examples never produce a rate mismatch — every primitive
they use preserves the input rate or operates on a windowed metric
internal to an inhibit. Rate analysis lands in Phase 0c (see
`docs/DESIGN-NOTES.md`).

`%` is dimensionally identical to `dimensionless`, and the type checker
treats them as the same kind.

It is NOT rescaled here. An earlier version of this docstring said "the
runtime knows `95 %` means `0.95`"; that is true of exactly one place and
false everywhere else, so it is worth stating precisely:

  * In an **expression**, a `%` literal keeps its face value. `30 %` is the
    number 30.0, and `30 % * reward.continuous` is thirty times the reward,
    not three tenths of it. The surface unit is preserved on `IRNumberLit`
    and reaches IR-JSON as `{"value": 30.0, "unit": "%"}`.
  * A **`percent` control** is likewise a 0-100 number used raw:
    `default = 75` reaches the wire as `75.0`, and
    `percentile(target_pct:)` requires `0 <= target_pct <= 100`.
  * **Autopilot fields only** divide by 100. `reward_target = (20%, 40%)`
    resolves to `(0.2, 0.4)` and a guard's `max = 30%` to `0.3`, via
    `_ap_percent` in the resolver — which never consults `unit_dims`.

So the same token means two different things depending on where it is
written. That is a real inconsistency rather than a documentation gap;
reconciling it changes emitted values either way, so it is tracked as an
issue rather than quietly fixed. Until then, this is the rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# Dimensional algebra
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dimensions:
    """Exponent vector over (time, voltage, temperature). Two Dimensions
    are equal iff every exponent matches. Hz = Dimensions(time=-1).

    Temperature is a base dimension of its own rather than a unit of
    voltage, because a thermopile reading and an electrode reading are not
    the same physical quantity and nothing converts between them. Before it
    existed, a skin-temperature protocol wrote every target in `uV` and
    meant degrees Celsius — it type-checked while lying.
    """

    time: int = 0
    voltage: int = 0
    temperature: int = 0

    def __mul__(self, other: Dimensions) -> Dimensions:
        return Dimensions(
            self.time + other.time,
            self.voltage + other.voltage,
            self.temperature + other.temperature,
        )

    def __truediv__(self, other: Dimensions) -> Dimensions:
        return Dimensions(
            self.time - other.time,
            self.voltage - other.voltage,
            self.temperature - other.temperature,
        )

    def __pow__(self, n: int) -> Dimensions:
        return Dimensions(self.time * n, self.voltage * n, self.temperature * n)

    def __str__(self) -> str:
        return _dims_str(self)

    @property
    def is_dimensionless(self) -> bool:
        return self.time == 0 and self.voltage == 0 and self.temperature == 0


# Canonical dimensions.
DIMENSIONLESS = Dimensions()
TIME = Dimensions(time=1)
FREQUENCY = Dimensions(time=-1)  # Hz
VOLTAGE = Dimensions(voltage=1)
VOLTAGE_SQ = Dimensions(voltage=2)
VOLTAGE_PER_TIME = Dimensions(time=-1, voltage=1)
TEMPERATURE = Dimensions(temperature=1)


def _dims_str(d: Dimensions) -> str:
    """Human-readable dimension string for diagnostics."""
    if d == DIMENSIONLESS:
        return "dimensionless"
    if d == TIME:
        return "time"
    if d == FREQUENCY:
        return "Hz"
    if d == VOLTAGE:
        return "uV"
    if d == VOLTAGE_SQ:
        return "uV2"
    if d == VOLTAGE_PER_TIME:
        return "uV/s"
    if d == TEMPERATURE:
        return "C"
    parts: list[str] = []
    if d.voltage:
        parts.append(f"uV^{d.voltage}")
    if d.temperature:
        parts.append(f"C^{d.temperature}")
    if d.time:
        parts.append(f"s^{d.time}")
    return "*".join(parts) or "dimensionless"


# Surface unit names (SPEC §2.5) -> dimensions.
_UNIT_TO_DIMS: dict[str, Dimensions] = {
    "Hz": FREQUENCY,
    "ms": TIME,
    "s": TIME,
    "min": TIME,
    "uV": VOLTAGE,
    "uV2": VOLTAGE_SQ,
    # Degrees Celsius. The one surface spelling for temperature, deliberately:
    # `ms`/`s`/`min` interconvert by a scale factor, and Fahrenheit does not —
    # it needs an offset as well, which an exponent vector cannot express. A
    # host that wants to show Fahrenheit converts for display.
    "C": TEMPERATURE,
    "%": DIMENSIONLESS,
}


def unit_dims(unit: str | None) -> Dimensions:
    """Map a SPEC §2.5 surface unit token to its `Dimensions`.

    `None` -> DIMENSIONLESS (a bare number). An unknown unit raises
    `ValueError`; the parser's UNIT terminal already restricts to the
    spec set, so this is a defence-in-depth check.
    """
    if unit is None:
        return DIMENSIONLESS
    try:
        return _UNIT_TO_DIMS[unit]
    except KeyError as exc:
        raise ValueError(f"unknown unit {unit!r}") from exc


# ---------------------------------------------------------------------------
# Stream types
# ---------------------------------------------------------------------------


ValueKind = Literal["scalar", "vector", "boolean", "event", "complex"]


@dataclass(frozen=True, slots=True)
class StreamType:
    """A time series of values.

    Examples:
        `stream<scalar uV>`        -> StreamType("scalar", dims=VOLTAGE)
        `stream<vector<19> uV2>`   -> StreamType("vector", vector_size=19, dims=VOLTAGE_SQ)
        `stream<boolean>`          -> StreamType("boolean")
        `event_stream`             -> StreamType("event")
        `stream<complex uV>`       -> StreamType("complex", dims=VOLTAGE)
    """

    value_kind: ValueKind
    dimensions: Dimensions = DIMENSIONLESS
    vector_size: int | None = None

    def __post_init__(self) -> None:
        if self.value_kind == "vector" and self.vector_size is None:
            raise ValueError("vector stream type requires vector_size")
        if self.value_kind != "vector" and self.vector_size is not None:
            raise ValueError(f"vector_size is only meaningful for vector streams (got {self.value_kind})")

    def __str__(self) -> str:
        if self.value_kind == "event":
            return "event_stream"
        if self.value_kind == "boolean":
            return "stream<boolean>"
        if self.value_kind == "vector":
            return f"stream<vector<{self.vector_size}> {_dims_str(self.dimensions)}>"
        return f"stream<{self.value_kind} {_dims_str(self.dimensions)}>"


# Common stream types for the registry.
BOOLEAN_STREAM = StreamType("boolean")
EVENT_STREAM = StreamType("event")


def scalar_stream(dims: Dimensions = DIMENSIONLESS) -> StreamType:
    return StreamType("scalar", dimensions=dims)


def complex_stream(dims: Dimensions = DIMENSIONLESS) -> StreamType:
    return StreamType("complex", dimensions=dims)


def vector_stream(size: int, dims: Dimensions = DIMENSIONLESS) -> StreamType:
    return StreamType("vector", dimensions=dims, vector_size=size)


# ---------------------------------------------------------------------------
# Type-checking helpers
# ---------------------------------------------------------------------------


def dims_compatible_for_add(a: Dimensions, b: Dimensions) -> bool:
    """`+` / `-` / comparison operands must share dimensions exactly."""
    return a == b


def dims_compatible_for_mul(a: Dimensions, b: Dimensions) -> tuple[bool, Dimensions]:
    """`*` of dimensions always succeeds; returns the product."""
    return True, a * b


def dims_compatible_for_div(a: Dimensions, b: Dimensions) -> tuple[bool, Dimensions]:
    """`/` of dimensions always succeeds; returns the quotient."""
    return True, a / b


def dims_sqrt(d: Dimensions) -> Dimensions | None:
    """Halve every exponent, or `None` when one of them is odd.

    `sqrt(uV2)` is `uV` and `sqrt(dimensionless)` is dimensionless, but
    `sqrt(uV)` has no representable dimension — these exponents are integers,
    so there is no half-power of a volt to return. `None` says "this root has
    no unit"; the resolver turns that into an error naming the stream, rather
    than this layer inventing a fractional exponent nothing else can read.
    """
    if d.time % 2 or d.voltage % 2 or d.temperature % 2:
        return None
    return Dimensions(d.time // 2, d.voltage // 2, d.temperature // 2)


__all__ = [
    "Dimensions",
    "StreamType",
    "ValueKind",
    "DIMENSIONLESS",
    "TIME",
    "FREQUENCY",
    "VOLTAGE",
    "VOLTAGE_SQ",
    "VOLTAGE_PER_TIME",
    "TEMPERATURE",
    "BOOLEAN_STREAM",
    "EVENT_STREAM",
    "scalar_stream",
    "complex_stream",
    "vector_stream",
    "unit_dims",
    "dims_compatible_for_add",
    "dims_compatible_for_mul",
    "dims_compatible_for_div",
    "dims_sqrt",
]
