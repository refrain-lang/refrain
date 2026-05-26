# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Resolver pipeline tests.

Three layers:
  1. Happy-path: bits of expected structure in the IR.
  2. Error paths: each kind of static failure must raise with a useful loc.
  3. End-to-end on the three example files against the Q21 profile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refrain import ast as A
from refrain.amp_profile import AmpProfile, load_amp_profile
from refrain.ir import (
    IRBinaryOp,
    IRCall,
    IRConditional,
    IRControlRef,
    IRMeta,
    IRNumberLit,
    IRProtocol,
    IRRequires,
    IRRewardField,
    IRStreamRef,
    IRStringLit,
    IRThresholdRef,
)
from refrain.parser import parse, parse_file
from refrain.resolver import ResolveError, resolve
from refrain.types_ import BOOLEAN_STREAM, EVENT_STREAM, FREQUENCY, TIME, VOLTAGE


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
AMP_PATH = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json"


@pytest.fixture(scope="module")
def amp() -> AmpProfile:
    return load_amp_profile(AMP_PATH)


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def _minimal(src: str):
    return resolve(parse(src))


def test_minimal_protocol_resolves():
    src = '''
        protocol "minimal" {
          meta {
            version = "1.0"
            evidence = "demo"
            description = "tiny"
          }
        }
    '''
    ir = _minimal(src)
    assert isinstance(ir, IRProtocol)
    assert ir.name == "minimal"
    assert ir.meta.fields["version"].value == "1.0"


# ---------------------------------------------------------------------------
# String classification (SPEC §5.4)
# ---------------------------------------------------------------------------


def test_string_referring_to_input_becomes_stream_ref():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "a" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
        }
    '''
    ir = _minimal(src)
    a = ir.derives["a"]
    # The pipeline lowers to smooth(@input/raw, tau: 100 ms).
    assert isinstance(a.expression, IRCall) and a.expression.callee == "smooth"
    first_arg = a.expression.args[0]
    assert isinstance(first_arg.value, IRStreamRef)
    assert first_arg.value.target == "input/raw"


def test_string_referring_to_threshold_becomes_threshold_ref():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
          threshold "t1" {
            signal = "env"
            type = absolute(8 uV)
          }
          reward {
            event = dwell(condition: above("env", "t1"), duration: 100 ms)
          }
        }
    '''
    ir = _minimal(src)
    event = ir.reward.event
    # event = dwell(condition: above("env", "t1"), ...)
    cond = next(a for a in event.args if a.name == "condition").value
    assert isinstance(cond, IRCall) and cond.callee == "above"
    sig_arg, thr_arg = cond.args
    assert isinstance(sig_arg.value, IRStreamRef)
    assert sig_arg.value.target == "derive/env"
    assert isinstance(thr_arg.value, IRThresholdRef)
    assert thr_arg.value.target == "threshold/t1"


def test_string_with_no_referent_stays_a_string():
    src = '''
        protocol "P" {
          meta {
            description = "this is just text"
          }
        }
    '''
    ir = _minimal(src)
    desc = ir.meta.fields["description"]
    assert isinstance(desc, IRStringLit)
    assert desc.value == "this is just text"


# ---------------------------------------------------------------------------
# NameRef resolution (for controls)
# ---------------------------------------------------------------------------


def test_control_reference_in_derive():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "band" {
            from = "raw"
            pipeline = [bandpass(center: orf, bandwidth: ratio(0.5))]
          }
          controls {
            orf = frequency {
              default = 0.01 Hz
              range = (0.001 Hz, 1 Hz)
              live_tunable = true
            }
          }
        }
    '''
    ir = _minimal(src)
    band = ir.derives["band"]
    assert "control/orf" in band.upstream


def test_undefined_name_ref_raises():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "band" {
            from = "raw"
            pipeline = [bandpass(center: nonexistent, bandwidth: ratio(0.5))]
          }
        }
    '''
    with pytest.raises(ResolveError, match="unknown identifier 'nonexistent'"):
        _minimal(src)


# ---------------------------------------------------------------------------
# Unit / dimension consistency
# ---------------------------------------------------------------------------


def test_voltage_division_yields_dimensionless():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
          threshold "t" {
            signal = "env"
            type = absolute(8 uV)
          }
          reward {
            continuous = sigmoid("env" / "t", midpoint: 1.0, steepness: 3)
          }
        }
    '''
    ir = _minimal(src)
    sigmoid = ir.reward.continuous
    division = sigmoid.args[0].value
    assert isinstance(division, IRBinaryOp) and division.op == "/"
    assert division.stream_type.dimensions.is_dimensionless


def test_unit_mismatch_in_addition_is_an_error():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
          reward {
            continuous = sigmoid("env" + 1 Hz, midpoint: 1.0, steepness: 3)
          }
        }
    '''
    with pytest.raises(ResolveError, match="matching dimensions"):
        _minimal(src)


def test_differentiate_produces_voltage_per_time():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "d" {
            from = "raw"
            pipeline = [differentiate()]
          }
        }
    '''
    ir = _minimal(src)
    assert ir.derives["d"].stream_type.dimensions.time == -1
    assert ir.derives["d"].stream_type.dimensions.voltage == 1


# ---------------------------------------------------------------------------
# Reward / reward.event member access
# ---------------------------------------------------------------------------


def test_reward_member_access_resolves():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
          reward {
            event = dwell(condition: above("env", 5 uV), duration: 100 ms)
            continuous = sigmoid("env", midpoint: 5 uV, steepness: 1)
          }
          output {
            audio_chime = reward.event
            audio_gain = reward.event.holds ? reward.continuous : 0
          }
        }
    '''
    ir = _minimal(src)
    chime = ir.output["audio_chime"]
    assert isinstance(chime, IRRewardField) and chime.field_path == "event"
    assert chime.stream_type == EVENT_STREAM

    gain = ir.output["audio_gain"]
    assert isinstance(gain, IRConditional)
    cond = gain.cond
    assert isinstance(cond, IRRewardField) and cond.field_path == "event.holds"
    assert cond.stream_type == BOOLEAN_STREAM


def test_reward_event_without_dwell_raises():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          reward {
            event = above("raw", 5 uV)
          }
        }
    '''
    # above() returns a boolean stream, not an event_stream. Reward event
    # must be event_stream.
    with pytest.raises(ResolveError, match="event_stream"):
        _minimal(src)


# ---------------------------------------------------------------------------
# Hardware validation
# ---------------------------------------------------------------------------


def test_missing_channel_against_amp_raises(amp):
    src = '''
        protocol "P" {
          requires {
            channels = ["NotAChannel"]
          }
          meta { version = "1.0" }
        }
    '''
    with pytest.raises(ResolveError, match="missing required channels"):
        resolve(parse(src), amp)


def test_unsupported_coupling_against_amp_raises():
    cyton = load_amp_profile(
        AMP_PATH.parent / "openbci_cyton.json"
    )
    src = '''
        protocol "P" {
          requires {
            coupling = "dc"
          }
          meta { version = "1.0" }
        }
    '''
    with pytest.raises(ResolveError, match="does not support 'dc' coupling"):
        resolve(parse(src), cyton)


def test_sample_rate_above_amp_capability_raises():
    cyton = load_amp_profile(AMP_PATH.parent / "openbci_cyton.json")
    src = '''
        protocol "P" {
          requires {
            sample_rate = ">= 1024 Hz"
          }
          meta { version = "1.0" }
        }
    '''
    with pytest.raises(ResolveError, match="no sample rate >= 1024 Hz"):
        resolve(parse(src), cyton)


def test_resolver_picks_highest_rate_at_least_requested(amp):
    src = '''
        protocol "P" {
          requires {
            sample_rate = ">= 256 Hz"
            channels = ["Cz"]
          }
          meta { version = "1.0" }
        }
    '''
    ir = resolve(parse(src), amp)
    assert ir.requires.sample_rate_chosen_hz == 2048
    assert ir.requires.sample_rate_min_hz == 256


def test_no_amp_profile_skips_hardware_checks():
    src = '''
        protocol "P" {
          requires {
            channels = ["NotARealChannel"]
            coupling = "dc"
          }
          meta { version = "1.0" }
        }
    '''
    ir = resolve(parse(src), amp=None)
    assert "NotARealChannel" in ir.requires.channels


# ---------------------------------------------------------------------------
# Acyclicity / source-order enforcement
# ---------------------------------------------------------------------------


def test_forward_reference_raises():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "a" {
            formula = "later" + 1 uV
          }
          derive "later" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
        }
    '''
    # "later" is forward-referenced from "a"; per SPEC §5.4 it falls back
    # to a string literal value, and then `+ 1 uV` fails because string
    # has dimensionless dims that don't match uV.
    with pytest.raises(ResolveError):
        _minimal(src)


# ---------------------------------------------------------------------------
# Composition wiring (full coverage in test_compose.py)
# ---------------------------------------------------------------------------


def test_extends_without_loader_raises_with_helpful_message():
    src = '''
        protocol "child" extends "library/parent@1.0" {
          meta { version = "1.0" }
        }
    '''
    with pytest.raises(ResolveError, match="no parent loader"):
        _minimal(src)


def test_amend_outside_extends_raises():
    src = '''
        protocol "orphan_amend" {
          amend reward { continuous = sigmoid("x", midpoint: 0, steepness: 1) }
        }
    '''
    with pytest.raises(ResolveError, match="requires an `extends`"):
        _minimal(src)


def test_remove_outside_extends_raises():
    src = '''
        protocol "orphan_remove" {
          remove inhibit "emg"
        }
    '''
    with pytest.raises(ResolveError, match="requires an `extends`"):
        _minimal(src)


# ---------------------------------------------------------------------------
# Filter `kind:` validation (Phase 0d)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["butterworth", "bessel", "chebyshev2"])
def test_bandpass_kind_butterworth_bessel_chebyshev2_accepted(kind):
    src = f'''
        protocol "P" {{
          input "raw" {{ montage = bipolar(plus: "T3", minus: "T4") }}
          derive "env" {{
            from = "raw"
            pipeline = [bandpass(band: (12 Hz, 15 Hz), order: 4, kind: "{kind}")]
          }}
        }}
    '''
    _minimal(src)


def test_bandpass_kind_elliptic_rejected_with_clear_message():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [bandpass(band: (12 Hz, 15 Hz), kind: "elliptic")]
          }
        }
    '''
    with pytest.raises(ResolveError, match="not a supported filter family"):
        _minimal(src)


def test_bandpass_kind_typo_rejected():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [bandpass(band: (12 Hz, 15 Hz), kind: "butterwroth")]
          }
        }
    '''
    with pytest.raises(ResolveError, match="not a supported filter family"):
        _minimal(src)


def test_hilbert_kind_fir_accepted():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [hilbert(kind: "fir", taps: 65), magnitude()]
          }
        }
    '''
    _minimal(src)


def test_hilbert_kind_unknown_rejected():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [hilbert(kind: "wavelet"), magnitude()]
          }
        }
    '''
    with pytest.raises(ResolveError, match="not a supported filter family"):
        _minimal(src)


# ---------------------------------------------------------------------------
# Reward shape rules
# ---------------------------------------------------------------------------


def test_reward_must_declare_continuous_or_event():
    src = '''
        protocol "P" {
          reward { }
          meta { version = "1.0" }
        }
    '''
    with pytest.raises(ResolveError, match="continuous`, `event`"):
        _minimal(src)


# ---------------------------------------------------------------------------
# Threshold + inhibit
# ---------------------------------------------------------------------------


def test_threshold_signal_must_be_a_stream():
    src = '''
        protocol "P" {
          threshold "t" {
            signal = "no_such_thing"
            type = absolute(8 uV)
          }
          meta { version = "1.0" }
        }
    '''
    with pytest.raises(ResolveError, match="not a stream"):
        _minimal(src)


def test_inhibit_action_kind_captured():
    src = '''
        protocol "P" {
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          inhibit "emg" {
            metric = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = absolute(8 uV2)
            action = mute(release: 200 ms)
          }
          meta { version = "1.0" }
        }
    '''
    ir = _minimal(src)
    inh = ir.inhibits["emg"]
    assert inh.action_kind == "mute"
    assert inh.action_release_ms == 200.0


# ---------------------------------------------------------------------------
# End-to-end on real examples
# ---------------------------------------------------------------------------


EXAMPLE_FILES = ["smr_cz.refrain", "othmer_ilf_t3t4.refrain", "alpha_theta.refrain"]


@pytest.mark.parametrize("name", EXAMPLE_FILES)
def test_example_resolves_against_q21(name, amp):
    f = parse_file(EXAMPLES / name)
    ir = resolve(f, amp)
    assert isinstance(ir, IRProtocol)
    # Basic invariants:
    assert ir.requires.sample_rate_chosen_hz in amp.sample_rates_hz
    for ch in ir.requires.channels:
        assert amp.has_channel(ch), f"{name}: channel {ch} should be on the Q21"
    # Topological order has at least the inputs.
    for inp_name in ir.inputs:
        assert f"input/{inp_name}" in ir.topological_order


def test_smr_dwells_on_three_conditions(amp):
    ir = resolve(parse_file(EXAMPLES / "smr_cz.refrain"), amp)
    event = ir.reward.event
    cond_arg = next(a for a in event.args if a.name == "condition").value
    assert cond_arg.callee == "all_of"
    conditions_array = cond_arg.args[0].value
    assert len(conditions_array.elements) == 3


def test_othmer_ilf_has_log_scale_frequency_control(amp):
    ir = resolve(parse_file(EXAMPLES / "othmer_ilf_t3t4.refrain"), amp)
    orf = ir.controls["orf"]
    assert orf.type_kind == "frequency"
    assert orf.log_scale is True
    assert orf.live_tunable is True
    assert orf.dims == FREQUENCY


def test_othmer_ilf_derive_band_depends_on_orf_control(amp):
    ir = resolve(parse_file(EXAMPLES / "othmer_ilf_t3t4.refrain"), amp)
    band = ir.derives["band"]
    assert "control/orf" in band.upstream
    assert "input/ilf" in band.upstream


def test_alpha_theta_uses_formula_form(amp):
    ir = resolve(parse_file(EXAMPLES / "alpha_theta.refrain"), amp)
    crossover = ir.derives["theta_minus_alpha"]
    assert isinstance(crossover.expression, IRBinaryOp)
    assert crossover.expression.op == "-"


def test_resource_budgets_summed(amp):
    ir = resolve(parse_file(EXAMPLES / "smr_cz.refrain"), amp)
    # Budgets should be positive and under amp limits.
    assert ir.budget.state_kb > 0
    assert ir.budget.worst_case_us > 0
    assert ir.budget.state_kb <= amp.resource_limits.max_state_kb
    assert ir.budget.worst_case_us <= amp.resource_limits.max_worst_case_us_per_step


# ---------------------------------------------------------------------------
# Placement control type (Task 1)
# ---------------------------------------------------------------------------


def test_placement_control_active_resolves():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3","C4"]; label = "Training site" } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    c = ir.controls["site"]
    assert c.type_kind == "placement"
    assert c.kind == "active"
    assert c.allowed == ("Cz", "C3", "C4")
    assert c.final is False
    assert c.default_placement == ("Cz",)


def test_placement_empty_allowed_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = [] } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    with pytest.raises(ResolveError, match="non-empty|any"):
        resolve(parse(src))


def test_placement_default_must_be_in_allowed():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Fz"; allowed = ["Cz","C3"] } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    with pytest.raises(ResolveError, match="not in allowed"):
        resolve(parse(src))


def test_placement_rejects_live_tunable():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = "any"; live_tunable = true } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    with pytest.raises(ResolveError, match="live_tunable|frozen"):
        resolve(parse(src))


# ---------------------------------------------------------------------------
# Placement control type — Task 2: resolve-time binding (active site, montage)
# ---------------------------------------------------------------------------

from refrain.amp_profile import load_amp_profile  # noqa: E402 (re-import OK; same object)
_AMP = load_amp_profile(Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json")

_SITE_PROTO = '''
    protocol "poise" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3","C4"] } }
      input "raw" { montage = referential(active: site, reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''


def _active_channel(ir):
    """Read the bound channel string from the resolved montage IRCall."""
    call = ir.inputs["raw"].montage
    return next(a.value.value for a in call.args if a.name == "active")


def test_placement_binds_default_site():
    ir = resolve(parse(_SITE_PROTO), _AMP)
    assert _active_channel(ir) == "Cz"


def test_placement_binds_override_site():
    ir = resolve(parse(_SITE_PROTO), _AMP, bindings={"site": "C3"})
    assert _active_channel(ir) == "C3"


def test_placement_binding_not_in_allowed_fails():
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_SITE_PROTO), _AMP, bindings={"site": "Fz"})


def test_placement_binding_not_device_capable_fails():
    src = _SITE_PROTO.replace('allowed = ["Cz","C3","C4"]', 'allowed = "any"')
    with pytest.raises(ResolveError, match="missing|capable|channel"):
        resolve(parse(src), _AMP, bindings={"site": "ZZ9"})


def test_placement_binding_non_string_rejected():
    # A non-string active binding (e.g. an int from a JSON deserializer) must
    # fail with a clear error, not silently flow into the channel name.
    with pytest.raises(ResolveError, match="must be a channel-name string|string"):
        resolve(parse(_SITE_PROTO), _AMP, bindings={"site": 42})


def test_final_placement_rejects_override():
    src = _SITE_PROTO.replace('allowed = ["Cz","C3","C4"]', 'allowed = ["Cz"]; final = true')
    with pytest.raises(ResolveError, match="final|locked|cannot override"):
        resolve(parse(src), _AMP, bindings={"site": "C3"})


def test_coherence_two_active_placements_bind():
    # Two inputs, each with its own active placement — exercises coherence of
    # independent bindings without requiring Task 3 (requires.channels).
    src = '''
        protocol "coh" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls {
            site_a = placement { kind = "active"; default = "C3"; allowed = ["C3","F3"] }
            site_b = placement { kind = "active"; default = "C4"; allowed = ["C4","F4"] }
          }
          input "a" { montage = referential(active: site_a, reference: "linked_ears") }
          input "b" { montage = referential(active: site_b, reference: "linked_ears") }
          derive "coh" { formula = coherence("a", "b", band: (8 Hz, 12 Hz)) }
          reward { continuous = sigmoid("coh", midpoint: 0.5, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src), _AMP, bindings={"site_a": "F3", "site_b": "F4"})
    a_call = ir.inputs["a"].montage
    b_call = ir.inputs["b"].montage
    assert next(x.value.value for x in a_call.args if x.name == "active") == "F3"
    assert next(x.value.value for x in b_call.args if x.name == "active") == "F4"


# ---------------------------------------------------------------------------
# Task 3: requires.channels derives from bound placement
# ---------------------------------------------------------------------------

_SITE_PROTO_REQ = '''
    protocol "poise" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3"] } }
      requires { channels = [site] }
      input "raw" { montage = referential(active: site, reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''


def test_requires_channels_from_placement():
    ir = resolve(parse(_SITE_PROTO_REQ), _AMP, bindings={"site": "C3"})
    assert ir.requires.channels == ("C3",)


# ---------------------------------------------------------------------------
# Task 4: bipolar placement + bipolar(pair: site) montage form
# ---------------------------------------------------------------------------

_BIPOLAR_PROTO = '''
    protocol "ilf" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { site = placement { kind = "bipolar"; default = ("T3","T4"); allowed = [("T3","T4"),("C3","C4")] } }
      requires { channels = [site] }
      input "raw" { montage = bipolar(pair: site) }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''


def _bipolar_legs(ir):
    call = ir.inputs["raw"].montage
    args = {a.name: a.value.value for a in call.args}
    return (args["plus"], args["minus"])


def test_bipolar_placement_binds_default():
    ir = resolve(parse(_BIPOLAR_PROTO), _AMP)
    assert _bipolar_legs(ir) == ("T3", "T4")
    assert ir.requires.channels == ("T3", "T4")


def test_bipolar_placement_binds_override():
    ir = resolve(parse(_BIPOLAR_PROTO), _AMP, bindings={"site": ("C3", "C4")})
    assert _bipolar_legs(ir) == ("C3", "C4")


def test_bipolar_pair_not_in_allowed_fails():
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_BIPOLAR_PROTO), _AMP, bindings={"site": ("F3", "F4")})


# ---------------------------------------------------------------------------
# Task 1 (Mode 2): kind="pair" — coherence pairs with .a/.b leg member access
# ---------------------------------------------------------------------------

_PAIR_PROTO = '''
    protocol "coh" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { coh = placement { kind = "pair"; default = ("C3","C4"); allowed = [("C3","C4"),("F3","F4")] } }
      requires { channels = [coh] }
      input "a" { montage = referential(active: coh.a, reference: "linked_ears") }
      input "b" { montage = referential(active: coh.b, reference: "linked_ears") }
      derive "c" { from = "a"; pipeline = [smooth(tau: 100 ms)] }
      reward { continuous = sigmoid("c", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''


def _active_of(ir, input_name):
    call = ir.inputs[input_name].montage
    return next(x.value.value for x in call.args if x.name == "active")


def test_pair_legs_bind_default(amp):
    ir = resolve(parse(_PAIR_PROTO), amp)
    assert _active_of(ir, "a") == "C3"
    assert _active_of(ir, "b") == "C4"
    assert set(ir.requires.channels) == {"C3", "C4"}


def test_pair_legs_bind_override(amp):
    ir = resolve(parse(_PAIR_PROTO), amp, bindings={"coh": ("F3", "F4")})
    assert _active_of(ir, "a") == "F3"
    assert _active_of(ir, "b") == "F4"


def test_pair_not_in_allowed_fails(amp):
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_PAIR_PROTO), amp, bindings={"coh": ("Cz", "Pz")})


# ---------------------------------------------------------------------------
# Task 2 (Mode 2): kind="set" — multi-site list declaration + binding
# ---------------------------------------------------------------------------

_SET_DECL = '''
    protocol "ms" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4","Pz"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''


def test_set_control_resolves(amp):
    ir = resolve(parse(_SET_DECL), amp)
    c = ir.controls["sites"]
    assert c.kind == "set"
    assert c.allowed == ("C3", "Cz", "C4", "Pz")
    assert c.set_min == 1 and c.set_max == 3
    assert c.default_placement == ("Cz",)


def test_set_count_below_min_fails(amp):
    src = _SET_DECL.replace("min = 1", "min = 2")
    with pytest.raises(ResolveError, match="at least|min"):
        resolve(parse(src), amp, bindings={"sites": ["Cz"]})


def test_set_count_above_max_fails(amp):
    with pytest.raises(ResolveError, match="at most|max"):
        resolve(parse(_SET_DECL), amp, bindings={"sites": ["C3", "Cz", "C4", "Pz"]})


def test_set_member_not_in_allowed_fails(amp):
    with pytest.raises(ResolveError, match="not in allowed|allowed"):
        resolve(parse(_SET_DECL), amp, bindings={"sites": ["C3", "Fz"]})


# ---------------------------------------------------------------------------
# Task 3 (Mode 2): reward.combine field ("all" | "any")
# ---------------------------------------------------------------------------

_REWARD_COMBINE_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "t" { signal = "env"; type = absolute(8 uV) }
      reward { combine = "any"; event = dwell(condition: above("env","t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
'''

_REWARD_COMBINE_NO_COMBINE_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "t" { signal = "env"; type = absolute(8 uV) }
      reward { event = dwell(condition: above("env","t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
'''

_REWARD_COMBINE_INVALID_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "t" { signal = "env"; type = absolute(8 uV) }
      reward { combine = "most"; event = dwell(condition: above("env","t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
'''


def test_reward_combine_parsed(amp):
    ir = resolve(parse(_REWARD_COMBINE_PROTO), amp)
    assert ir.reward.combine == "any"


def test_reward_combine_defaults_all(amp):
    ir = resolve(parse(_REWARD_COMBINE_NO_COMBINE_PROTO), amp)
    assert ir.reward.combine == "all"


def test_reward_combine_invalid_fails(amp):
    with pytest.raises(ResolveError):
        resolve(parse(_REWARD_COMBINE_INVALID_PROTO), amp)


# ---------------------------------------------------------------------------
# Named groups — Task 2: resolver builds + validates group table
# ---------------------------------------------------------------------------


def test_groups_table_built():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))           # resolving succeeds; group declared but unused is fine
    assert ir is not None


def test_groups_empty_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = [] }
        }
    '''
    with pytest.raises(ResolveError, match="empty"):
        resolve(parse(src))


def test_groups_duplicate_channel_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","C3"] }
        }
    '''
    with pytest.raises(ResolveError, match="more than once"):
        resolve(parse(src))


def test_group_name_collides_with_control_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { site = ["C3","C4"] }
          controls { site = placement { kind = "active"; default = "C3"; allowed = ["C3","C4"] } }
        }
    '''
    with pytest.raises(ResolveError, match="collides"):
        resolve(parse(src))


# ---------------------------------------------------------------------------
# Named groups — Task 3: expand group refs in placement allowed
# ---------------------------------------------------------------------------


def test_group_expands_in_active_allowed():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = smr } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    assert ir.controls["site"].allowed == ("C3", "Cz", "C4")


def test_group_allowed_matches_inline_form():
    base = '''
        protocol "p" {{
          meta {{ version = "1.0"; evidence = "clinical"; description = "x" }}
          {groups}controls {{ site = placement {{ kind = "active"; default = "Cz"; allowed = {allowed} }} }}
          input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
          reward {{ continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }}
          output {{ audio_gain = reward.continuous }}
        }}
    '''
    grouped = resolve(parse(base.format(groups='groups { smr = ["C3","Cz","C4"] } ', allowed="smr")))
    inline = resolve(parse(base.format(groups="", allowed='["C3","Cz","C4"]')))
    assert grouped.controls["site"].allowed == inline.controls["site"].allowed


def test_unknown_group_in_allowed_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "Cz"; allowed = nosuch } }
        }
    '''
    with pytest.raises(ResolveError, match="unknown group 'nosuch'"):
        resolve(parse(src))


# ---------------------------------------------------------------------------
# Named groups — Task 4: expand group refs in set default
# ---------------------------------------------------------------------------


def test_group_expands_in_set_default():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          controls { sites = placement { kind = "set"; default = smr; allowed = smr; min = 1; max = 3 } }
          input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src))
    assert ir.controls["sites"].default_placement == ("C3", "Cz", "C4")
    assert ir.controls["sites"].allowed == ("C3", "Cz", "C4")


def test_group_default_exceeding_max_rejected():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { smr = ["C3","Cz","C4"] }
          controls { sites = placement { kind = "set"; default = smr; allowed = smr; min = 1; max = 2 } }
        }
    '''
    with pytest.raises(ResolveError):       # existing min/max count check fires on the expanded default
        resolve(parse(src))


# ---------------------------------------------------------------------------
# Task 2 (Stage 1): IR — IRRewardComponent + IRReward.components dataclass shape
# ---------------------------------------------------------------------------

_COMPONENTS_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        w_smr   = percent { default = 1; range = (0, 4) }
        w_theta = percent { default = 0.6; range = (0, 4) }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "smr_env"   { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      derive "theta_env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      reward  "smr"   { signal = sigmoid("smr_env",   midpoint: 6 uV, steepness: 1); weight = w_smr }
      inhibit "theta" { signal = sigmoid("theta_env", midpoint: 8 uV, steepness: 1); weight = w_theta }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_reward_components_resolve_with_roles_and_weights(amp):
    ir = resolve(parse(_COMPONENTS_PROTO), amp)
    comps = {c.name: c for c in ir.reward.components}
    assert set(comps) == {"smr", "theta"}
    assert comps["smr"].role == "reward"
    assert comps["theta"].role == "suppress"
    assert comps["smr"].canonical_name == "reward/smr"
    # Weight resolves to a control ref (weights are ordinary controls).
    assert comps["smr"].weight.target == "control/w_smr"
    assert ir.reward.combine == "weighted"
    # The suppress-band inhibit is NOT a hard-gate IRInhibit.
    assert "theta" not in ir.inhibits


def test_ir_reward_component_dataclass_shape():
    from refrain.ir import IRRewardComponent, IRReward, IRNumberLit, IRControlRef
    from refrain.types_ import DIMENSIONLESS
    comp = IRRewardComponent(
        name="smr",
        canonical_name="reward/smr",
        role="reward",
        signal=IRNumberLit(value=0.5, dims=DIMENSIONLESS),
        weight=IRControlRef(target="control/w_smr", dims=DIMENSIONLESS),
    )
    assert comp.role == "reward"
    assert comp.canonical_name == "reward/smr"
    r = IRReward(continuous=None, event=None, combine="weighted", components=(comp,))
    assert r.components[0].name == "smr"
    assert r.combine == "weighted"
    # Back-compat default: empty components tuple, combine "all".
    r0 = IRReward(continuous=None, event=None)
    assert r0.components == ()
    assert r0.combine == "all"


# ---------------------------------------------------------------------------
# Task 4 (Stage 1): Resolver — wire components, accept combine=weighted,
#                    require ≥1 positive weight
# ---------------------------------------------------------------------------


def test_reward_combine_weighted_accepted(amp):
    ir = resolve(parse(_COMPONENTS_PROTO), amp)
    assert ir.reward.combine == "weighted"
    assert len(ir.reward.components) == 2


_WEIGHTED_NO_COMPONENTS_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_reward_weighted_requires_at_least_one_component(amp):
    with pytest.raises(ResolveError):
        resolve(parse(_WEIGHTED_NO_COMPONENTS_PROTO), amp)


_ALL_ZERO_WEIGHTS_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { w0 = percent { default = 0; range = (0, 0) } }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      reward  "a" { signal = sigmoid("env", midpoint: 6 uV, steepness: 1); weight = w0 }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_reward_weighted_all_zero_weights_rejected(amp):
    with pytest.raises(ResolveError):
        resolve(parse(_ALL_ZERO_WEIGHTS_PROTO), amp)


# ---------------------------------------------------------------------------
# Task 5 (Stage 1): Resolver — reward.composite and reward.<name>.signal access
# ---------------------------------------------------------------------------


def test_reward_composite_member_access_resolves(amp):
    ir = resolve(parse(_COMPONENTS_PROTO), amp)
    # output.audio_gain = reward.composite
    field = ir.output["audio_gain"]
    assert isinstance(field, IRRewardField)
    assert field.field_path == "composite"
    assert field.stream_type.value_kind == "scalar"


_COMPONENT_NAME_ACCESS_PROTO = _COMPONENTS_PROTO.replace(
    "output { audio_gain = reward.composite }",
    "output { audio_gain = reward.composite; video_clarity = reward.smr.signal }",
)


def test_reward_component_signal_access_resolves(amp):
    ir = resolve(parse(_COMPONENT_NAME_ACCESS_PROTO), amp)
    field = ir.output["video_clarity"]
    assert isinstance(field, IRRewardField)
    assert field.field_path == "smr.signal"


def test_reward_unknown_component_access_rejected(amp):
    bad = _COMPONENTS_PROTO.replace("reward.composite }", "reward.nope.signal }")
    with pytest.raises(ResolveError):
        resolve(parse(bad), amp)
