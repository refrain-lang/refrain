# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Unit / dimension algebra and stream-type construction."""

from __future__ import annotations

import pytest

from refrain.types_ import (
    BOOLEAN_STREAM,
    DIMENSIONLESS,
    EVENT_STREAM,
    FREQUENCY,
    TEMPERATURE,
    TIME,
    VOLTAGE,
    VOLTAGE_PER_TIME,
    VOLTAGE_SQ,
    Dimensions,
    StreamType,
    complex_stream,
    dims_compatible_for_add,
    dims_compatible_for_div,
    dims_compatible_for_mul,
    dims_sqrt,
    scalar_stream,
    unit_dims,
    vector_stream,
)


# ---------------------------------------------------------------------------
# unit_dims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unit, expected",
    [
        (None, DIMENSIONLESS),
        ("Hz", FREQUENCY),
        ("ms", TIME),
        ("s", TIME),
        ("min", TIME),
        ("uV", VOLTAGE),
        ("uV2", VOLTAGE_SQ),
        ("C", TEMPERATURE),
        ("%", DIMENSIONLESS),
    ],
)
def test_unit_dims_maps_surface_units_to_dimensions(unit, expected):
    assert unit_dims(unit) == expected


def test_fahrenheit_is_deliberately_not_a_unit():
    """`ms`/`s`/`min` interconvert by a scale factor. Celsius and Fahrenheit
    need an offset as well, which an exponent vector cannot carry — so there
    is one surface spelling for temperature and conversion is the host's job.
    """
    with pytest.raises(ValueError, match="unknown unit"):
        unit_dims("F")


def test_unknown_unit_raises():
    with pytest.raises(ValueError, match="unknown unit"):
        unit_dims("dB")


# ---------------------------------------------------------------------------
# Dimensions arithmetic
# ---------------------------------------------------------------------------


def test_voltage_squared():
    assert VOLTAGE * VOLTAGE == VOLTAGE_SQ


def test_voltage_per_time():
    assert VOLTAGE / TIME == VOLTAGE_PER_TIME


def test_dimensionless_division():
    # uV / uV is dimensionless.
    assert VOLTAGE / VOLTAGE == DIMENSIONLESS


def test_inverse_time_is_frequency():
    assert (DIMENSIONLESS / TIME) == FREQUENCY


def test_pow_doubles_exponents():
    assert VOLTAGE ** 2 == VOLTAGE_SQ
    assert (VOLTAGE / TIME) ** 2 == Dimensions(time=-2, voltage=2)


# ---------------------------------------------------------------------------
# Temperature
# ---------------------------------------------------------------------------


def test_temperature_is_not_reachable_from_voltage_and_time():
    """A thermopile reading is a different physical quantity from an electrode
    reading, so no product or quotient of the other two bases produces it."""
    assert TEMPERATURE != VOLTAGE
    assert TEMPERATURE != DIMENSIONLESS
    assert TEMPERATURE / VOLTAGE != DIMENSIONLESS
    assert str(TEMPERATURE) == "C"
    assert str(scalar_stream(TEMPERATURE)) == "stream<scalar C>"


def test_temperature_takes_part_in_the_ordinary_algebra():
    assert TEMPERATURE * TEMPERATURE == Dimensions(temperature=2)
    assert TEMPERATURE / TIME == Dimensions(time=-1, temperature=1)
    assert str(TEMPERATURE ** 2) == "C^2"
    assert str(TEMPERATURE / TIME) == "C^1*s^-1"


def test_a_voltage_is_still_printed_the_old_way():
    """Guard against the new exponent leaking into every existing diagnostic."""
    assert str(VOLTAGE) == "uV"
    assert str(VOLTAGE_SQ) == "uV2"
    assert str(VOLTAGE_PER_TIME) == "uV/s"
    assert str(DIMENSIONLESS) == "dimensionless"


# ---------------------------------------------------------------------------
# dims_sqrt — the rule behind `sqrt()`
# ---------------------------------------------------------------------------


def test_dims_sqrt_halves_even_exponents():
    assert dims_sqrt(VOLTAGE_SQ) == VOLTAGE
    assert dims_sqrt(DIMENSIONLESS) == DIMENSIONLESS
    assert dims_sqrt(TEMPERATURE ** 2) == TEMPERATURE
    assert dims_sqrt(Dimensions(time=-2, voltage=2)) == VOLTAGE_PER_TIME


def test_dims_sqrt_refuses_an_odd_exponent():
    """There is no half-power of a volt to return, in either direction."""
    assert dims_sqrt(VOLTAGE) is None
    assert dims_sqrt(VOLTAGE_PER_TIME) is None
    assert dims_sqrt(TEMPERATURE) is None
    assert dims_sqrt(Dimensions(time=-2, voltage=1)) is None


# ---------------------------------------------------------------------------
# Compatibility helpers
# ---------------------------------------------------------------------------


def test_add_requires_matching_dims():
    assert dims_compatible_for_add(VOLTAGE, VOLTAGE)
    assert not dims_compatible_for_add(VOLTAGE, TIME)


def test_mul_always_succeeds():
    ok, result = dims_compatible_for_mul(VOLTAGE, TIME)
    assert ok
    assert result == Dimensions(time=1, voltage=1)


def test_div_always_succeeds():
    ok, result = dims_compatible_for_div(VOLTAGE_SQ, VOLTAGE)
    assert ok
    assert result == VOLTAGE


# ---------------------------------------------------------------------------
# StreamType
# ---------------------------------------------------------------------------


def test_scalar_stream_str():
    assert str(scalar_stream(VOLTAGE)) == "stream<scalar uV>"
    assert str(scalar_stream(DIMENSIONLESS)) == "stream<scalar dimensionless>"


def test_vector_stream_requires_size():
    with pytest.raises(ValueError, match="vector_size"):
        StreamType("vector", dimensions=VOLTAGE)


def test_vector_size_only_for_vector():
    with pytest.raises(ValueError, match="only meaningful"):
        StreamType("scalar", dimensions=VOLTAGE, vector_size=19)


def test_vector_stream_str():
    assert str(vector_stream(19, VOLTAGE_SQ)) == "stream<vector<19> uV2>"


def test_event_and_boolean_streams():
    assert str(EVENT_STREAM) == "event_stream"
    assert str(BOOLEAN_STREAM) == "stream<boolean>"


def test_complex_stream():
    assert str(complex_stream(VOLTAGE)) == "stream<complex uV>"
