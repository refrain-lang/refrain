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
    with pytest.raises(ResolveError, match="default.*not in allowed|allowed"):
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
