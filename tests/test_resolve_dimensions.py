# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Three resolve-time dimension behaviours, decided 2026-10-19.

1. **A bare number in a dimensioned slot is kept, and warned about.**
   `sigmoid("<uV stream>", midpoint: 5.0)` means 5 uV. Twenty-three of the
   twenty-six `midpoint:` values in the shipped corpus are bare, so requiring
   the unit is an edit to every protocol file for no new capability. The
   warning gives the author the nudge without the migration.

2. **Temperature is a base dimension, spelled `C`**, with a `temperature`
   control kind and an `input { unit = "C" }` declaration — because a
   dimension no stream can carry is a decoration. Every montage returns
   microvolts, so before this a thermal protocol wrote its targets in `uV`
   and meant degrees Celsius.

3. **`sqrt()` halves the exponents, or refuses.** `sqrt(uV2)` is `uV`;
   `sqrt(uV)` has no unit to return and is an error naming the stream.
"""

from __future__ import annotations

import warnings
from contextlib import contextmanager

import pytest

from refrain.ir import IRCall, IRNumberLit
from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse
from refrain.resolver import ResolveError, ResolveWarning, resolve
from refrain.types_ import TEMPERATURE, VOLTAGE, VOLTAGE_SQ


def resolve_src(src: str):
    return resolve(parse(src))


@contextmanager
def warnings_as_errors():
    """Assert the body emits no `ResolveWarning`.

    `pytest.warns` asserts a warning *happened*; several tests here need the
    opposite, and "no warning" is the half that keeps the new one from being
    noise on the existing corpus.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResolveWarning)
        yield


def env_protocol(body: str) -> str:
    """One input, one microvolt envelope derive, plus `body`."""
    return f"""
protocol "probe" {{
  requires {{ sample_rate = ">= 256 Hz"; channels = ["Cz"] }}
  input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
  derive "env" {{
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(),
                 smooth(tau: 250 ms) ]
  }}
  {body}
}}
"""


# ---------------------------------------------------------------------------
# 1 — the bare-number warning
# ---------------------------------------------------------------------------


BARE_MIDPOINT = env_protocol("""
  reward { continuous = sigmoid("env", midpoint: 5.0, steepness: 0.5) }
  output { audio_gain = reward.continuous }
""")


def test_a_bare_midpoint_still_resolves_and_warns():
    with pytest.warns(ResolveWarning, match="read as uV") as caught:
        ir = resolve_src(BARE_MIDPOINT)
    assert ir.reward is not None
    message = str(caught[0].message)
    assert "sigmoid(midpoint: ...)" in message
    assert "bare number" in message
    # The author needs the position to find it in a 200-line protocol.
    assert "line " in message


def test_the_same_midpoint_with_its_unit_does_not_warn():
    """The warning has to be silenceable by doing what it asks, or it is noise."""
    with pytest.warns(ResolveWarning):
        resolve_src(BARE_MIDPOINT)
    with warnings_as_errors():
        resolve_src(env_protocol("""
  reward { continuous = sigmoid("env", midpoint: 5.0 uV, steepness: 0.5) }
  output { audio_gain = reward.continuous }
"""))


def test_a_dimensionless_signal_does_not_warn_about_a_bare_number():
    """`midpoint: 1.0` on a ratio derive is not understating anything — the
    stream really is dimensionless, which is most of the bare corpus values."""
    with warnings_as_errors():
        resolve_src(env_protocol("""
  derive "ratio" { formula = "env" / "env" }
  reward { continuous = sigmoid("ratio", midpoint: 1.0, steepness: 3) }
  output { audio_gain = reward.continuous }
"""))


@pytest.mark.parametrize(
    "body, slot",
    [
        ("""
  reward { event = dwell(condition: above("env", 5.0), duration: 250 ms) }
  output { audio_chime = reward.event }
""", "above(threshold: ...)"),
        ("""
  reward { event = dwell(condition: inside("env", low: 2.0, high: 9.0), duration: 250 ms) }
  output { audio_chime = reward.event }
""", "inside(low: ...)"),
        ("""
  threshold "t" { signal = "env"; type = absolute(value: 8.0) }
  reward { event = dwell(condition: above("env", "t"), duration: 250 ms) }
  output { audio_chime = reward.event }
""", "absolute(value: ...)"),
    ],
)
def test_every_dimension_checked_slot_warns(body, slot):
    """The same slots the dimension *check* covers, so the two stay in step."""
    with pytest.warns(ResolveWarning) as caught:
        resolve_src(env_protocol(body))
    assert any(slot in str(w.message) for w in caught), [str(w.message) for w in caught]


def test_a_wrong_unit_is_still_an_error_not_a_warning():
    """Keeping the bare number is not the same as giving up on the check."""
    with pytest.raises(ResolveError, match="must share dimensions"):
        resolve_src(env_protocol("""
  reward { continuous = sigmoid("env", midpoint: 5 Hz, steepness: 1) }
  output { audio_gain = reward.continuous }
"""))


# ---------------------------------------------------------------------------
# 2 — the temperature dimension
# ---------------------------------------------------------------------------


# The shape of the shipped skin-temperature protocol, written honestly: a
# thermopile differential declared in Celsius, a baseline control in Celsius,
# and a target rise compared against it.
THERMAL = """
protocol "hand_warming" {
  meta { version = "1.0"; evidence = "research"; description = "thermal" }
  requires { sample_rate = ">= 7 Hz"; channels = ["TH", "T1"] }
  input "skin" { montage = bipolar(plus: "TH", minus: "T1"); unit = "C" }
  derive "skin_temp" { from = "skin"; pipeline = [ smooth(tau: 10 s) ] }
  derive "rise" { formula = "skin_temp" - baseline_c }
  threshold "target" { signal = "rise"; type = absolute(value: 1.5 C) }
  reward { event = dwell(condition: above("rise", "target"), duration: 30 s) }
  output { audio_chime = reward.event }
  controls {
    baseline_c = temperature { default = 31 C; range = (20 C, 38 C); label = "Baseline" }
  }
}
"""


def test_a_thermal_protocol_types_in_celsius_end_to_end():
    with warnings_as_errors():
        ir = resolve_src(THERMAL)
    assert ir.inputs["skin"].stream_type.dimensions == TEMPERATURE
    assert ir.derives["skin_temp"].stream_type.dimensions == TEMPERATURE
    assert ir.derives["rise"].stream_type.dimensions == TEMPERATURE
    assert ir.controls["baseline_c"].dims == TEMPERATURE


def test_the_same_protocol_in_microvolts_is_now_a_dimension_error():
    """The lie the thermal fixture had to tell is no longer type-correct.

    Drop the `unit` declaration and the thermopile types as microvolts again,
    so subtracting a Celsius baseline from it is the mismatch `-` already
    catches — which is the point: once a Celsius quantity exists, mixing it
    with a voltage is caught by the checks Refrain already had.
    """
    mixed = THERMAL.replace('; unit = "C"', "")
    with pytest.raises(ResolveError) as exc:
        resolve_src(mixed)
    assert "requires matching dimensions" in str(exc.value)
    assert "stream<scalar uV> vs stream<scalar C>" in str(exc.value)


def test_an_input_without_a_unit_is_microvolts_as_before():
    ir = resolve_src(THERMAL.replace('; unit = "C"', "").replace("1.5 C", "1.5 uV")
                     .replace("31 C", "31 uV").replace("(20 C, 38 C)", "(20 uV, 38 uV)")
                     .replace("temperature {", "voltage {"))
    assert ir.inputs["skin"].stream_type.dimensions == VOLTAGE


def test_an_unknown_input_unit_is_refused_by_name():
    with pytest.raises(ResolveError, match="unknown unit 'F'"):
        resolve_src(THERMAL.replace('unit = "C"', 'unit = "F"'))


def test_an_input_unit_must_be_a_string():
    with pytest.raises(ResolveError, match="must be a string naming a surface unit"):
        resolve_src(THERMAL.replace('unit = "C"', "unit = 3 Hz"))


def test_temperature_reaches_ir_json_only_when_it_is_used():
    """A new exponent emitted unconditionally would move every golden vector
    and every deployed consumer's wire form for nothing."""
    thermal = ir_to_json_obj(resolve_src(THERMAL), sample_rate_hz=16.0)
    assert thermal["inputs"]["skin"]["stream_type"]["dimensions"] == {
        "time": 0, "voltage": 0, "temperature": 1,
    }
    with pytest.warns(ResolveWarning):
        electrical = ir_to_json_obj(resolve_src(BARE_MIDPOINT), sample_rate_hz=256.0)
    assert electrical["inputs"]["raw"]["stream_type"]["dimensions"] == {
        "time": 0, "voltage": 1,
    }


def test_an_unknown_control_kind_is_still_refused():
    """Guard against the new kind arriving as a catch-all."""
    with pytest.raises(ResolveError, match="unknown control type 'kelvin'"):
        resolve_src(THERMAL.replace("temperature {", "kelvin {"))


# ---------------------------------------------------------------------------
# 3 — sqrt()'s resolve rule
# ---------------------------------------------------------------------------


RMS = env_protocol("""
  derive "square" { formula = "env" * "env" }
  derive "rms" { from = "square"; pipeline = [ sqrt() ] }
  output { audio_gain = "rms" }
""")


def test_sqrt_of_a_squared_stream_halves_the_dimension():
    with warnings_as_errors():
        ir = resolve_src(RMS)
    assert ir.derives["square"].stream_type.dimensions == VOLTAGE_SQ
    assert ir.derives["rms"].stream_type.dimensions == VOLTAGE


def test_the_halved_dimension_reaches_the_wire():
    """A primitive with no dimension handling of its own would pass the input
    type straight through, so `sqrt(uV2)` would type as uV2 and every consumer
    downstream would read a power where an amplitude is. Pinned on the wire,
    because that type is what a host actually reads."""
    with warnings_as_errors():
        obj = ir_to_json_obj(resolve_src(RMS), sample_rate_hz=256.0)
    assert obj["derives"]["square"]["stream_type"]["dimensions"] == {"time": 0, "voltage": 2}
    assert obj["derives"]["rms"]["stream_type"]["dimensions"] == {"time": 0, "voltage": 1}


def test_sqrt_of_a_dimensionless_stream_stays_dimensionless():
    with warnings_as_errors():
        ir = resolve_src(env_protocol("""
  derive "ratio" { formula = "env" / "env" }
  derive "root" { from = "ratio"; pipeline = [ sqrt() ] }
  output { audio_gain = "root" }
"""))
    assert ir.derives["root"].stream_type.dimensions.is_dimensionless


def test_sqrt_of_bandpower_is_an_amplitude():
    """The composition the request was filed for: a power measure rooted back
    into the units a practitioner reads."""
    with warnings_as_errors():
        ir = resolve_src(env_protocol("""
  derive "power" { formula = bandpower(input: "raw", band: (12 Hz, 15 Hz), window: 1 s) }
  derive "amplitude" { from = "power"; pipeline = [ sqrt() ] }
  output { audio_gain = "amplitude" }
"""))
    assert ir.derives["amplitude"].stream_type.dimensions == VOLTAGE


def test_sqrt_of_an_odd_powered_stream_is_refused_with_its_type():
    with pytest.raises(ResolveError) as exc:
        resolve_src(env_protocol("""
  derive "root" { from = "env"; pipeline = [ sqrt() ] }
  output { audio_gain = "root" }
"""))
    message = str(exc.value)
    assert "even powers" in message
    assert "stream<scalar uV>" in message, message
    # A resolve error has to say where, like every other one.
    assert message.startswith("line ")


@pytest.mark.parametrize(
    "formula, id_",
    [
        ('sqrt()', "no-input-at-all"),
        ('sqrt("env")', "stream-passed-positionally"),
    ],
)
def test_sqrt_refuses_a_formula_call_it_cannot_type(formula, id_):
    """The silent-units trap, closed.

    A zero-parameter primitive called in `formula` position gets its
    positional argument dropped — the signature has no slot to put it in — so
    the output factory sees no input. Every other factory then falls back to
    `uV`, which is harmless for `rectify` (same units in and out) and wrong
    for a root. `sqrt("env")` on a uV stream would type as uV and read as an
    amplitude when it is the root of one, and bare `sqrt()` used to reach the
    evaluator and die at the first chunk. Both are refused at resolve, naming
    the pipeline spelling that does work.
    """
    with pytest.raises(ResolveError) as exc:
        resolve_src(env_protocol(f"""
  derive "root" {{ formula = {formula} }}
  output {{ audio_gain = "root" }}
"""))
    assert "cannot see the stream it is rooting" in str(exc.value)
    assert "pipeline = [ sqrt() ]" in str(exc.value)


def test_a_name_that_is_not_a_primitive_is_still_refused():
    """`sqrt` arriving in the registry must not be a hole through which any
    unregistered pipeline stage now resolves."""
    with pytest.raises(ResolveError, match="unknown primitive 'cbrt' in pipeline"):
        resolve_src(env_protocol("""
  derive "root" { from = "env"; pipeline = [ cbrt() ] }
  output { audio_gain = "root" }
"""))


def test_sqrt_refuses_a_rate_too():
    """`uV/s` has two odd exponents, not one — the check is per-exponent."""
    with pytest.raises(ResolveError, match="even powers"):
        resolve_src(env_protocol("""
  derive "slope" { from = "env"; pipeline = [ differentiate() ] }
  derive "root" { from = "slope"; pipeline = [ sqrt() ] }
  output { audio_gain = "root" }
"""))


def test_sqrt_is_in_the_ir_as_a_pipeline_stage():
    with warnings_as_errors():
        ir = resolve_src(RMS)
    stages = ir.derives["rms"].expression
    assert isinstance(stages, IRCall) and stages.callee == "sqrt"
    assert not any(isinstance(a.value, IRNumberLit) for a in stages.args)
