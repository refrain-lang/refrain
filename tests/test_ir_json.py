# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the IR -> JSON emitter (the wire format for a non-Python runtime).

The emitter serializes a fully-resolved `IRProtocol` into a JSON-compatible
object tree that an out-of-process runtime (e.g. a Rust core) can deserialize
and evaluate without re-parsing `.refrain` source. These tests pin the
contract: the structure a consumer relies on, and numerical fidelity of any
baked DSP coefficients against the live evaluator.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.ir_json import ir_to_json, ir_to_json_obj
from refrain.parser import parse, parse_file
from refrain.primitive_impls import BandpassImpl, HilbertFirImpl, SmoothImpl
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
BENCH_PROTOCOLS = REPO / "bench" / "protocols"
AMP_PATH = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

_AMP = load_amp_profile(AMP_PATH)


@pytest.fixture(scope="module")
def smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"), load_amp_profile(AMP_PATH))


def test_top_level_carries_name_rate_and_channels(smr_ir):
    obj = ir_to_json_obj(smr_ir)
    assert obj["name"] == smr_ir.name
    assert obj["sample_rate_hz"] == smr_ir.requires.sample_rate_chosen_hz
    assert obj["channels"] == list(smr_ir.requires.channels)


def test_output_is_json_serializable(smr_ir):
    # ir_to_json must produce a string that round-trips through json,
    # and the parsed object must equal the structured form.
    text = ir_to_json(smr_ir)
    assert isinstance(text, str)
    assert json.loads(text) == ir_to_json_obj(smr_ir)


def _walk(node):
    """Yield every expression node dict in a serialized expression tree."""
    if isinstance(node, dict) and "node" in node:
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)


def _callees(expr):
    return [n["callee"] for n in _walk(expr) if n.get("node") == "call"]


def test_derive_serializes_discriminated_expression_tree(smr_ir):
    obj = ir_to_json_obj(smr_ir)
    smr = obj["derives"]["smr_envelope"]
    assert smr["canonical_name"] == "derive/smr_envelope"
    assert "stream_type" in smr
    assert "input/raw" in smr["upstream"]

    # The pipeline desugars to smooth(magnitude(hilbert(bandpass(raw)))).
    callees = _callees(smr["expression"])
    assert set(callees) == {"bandpass", "hilbert", "magnitude", "smooth"}

    # The innermost input is a stream reference, tagged so a consumer can
    # distinguish it from a literal.
    stream_refs = [n for n in _walk(smr["expression"]) if n.get("node") == "stream_ref"]
    assert any(n["target"] == "input/raw" for n in stream_refs)


def _find_call(expr, callee):
    return next(n for n in _walk(expr) if n.get("node") == "call" and n["callee"] == callee)


def test_bandpass_bakes_sos_matching_live_evaluator(smr_ir):
    # The de-risk move: Python owns scipy filter *design*; the baked SOS in
    # the IR-JSON must equal what the live evaluator computes, bit-for-bit,
    # because both go through the same design code path.
    obj = ir_to_json_obj(smr_ir)
    fs = smr_ir.requires.sample_rate_chosen_hz
    bp = _find_call(obj["derives"]["smr_envelope"]["expression"], "bandpass")
    baked = np.asarray(bp["coeffs"]["sos"])
    ref = BandpassImpl(band=(12.0, 15.0), order=4, kind="butterworth", sample_rate_hz=fs).sos
    assert baked.shape == ref.shape
    np.testing.assert_array_equal(baked, ref)


def test_sample_rate_override_bakes_at_target_rate(smr_ir):
    # The runtime rate is a host choice (the amp may support several rates
    # >= the protocol minimum). The emitter must bake coefficients at the
    # rate the runtime will actually use, not the resolver's default.
    target = 256.0
    assert smr_ir.requires.sample_rate_chosen_hz != target  # guard: they differ here
    obj = ir_to_json_obj(smr_ir, sample_rate_hz=target)
    assert obj["sample_rate_hz"] == target
    bp = _find_call(obj["derives"]["smr_envelope"]["expression"], "bandpass")
    ref = BandpassImpl(band=(12.0, 15.0), order=4, kind="butterworth", sample_rate_hz=target).sos
    np.testing.assert_array_equal(np.asarray(bp["coeffs"]["sos"]), ref)


def test_hilbert_and_smooth_bake_matching_live_evaluator(smr_ir):
    obj = ir_to_json_obj(smr_ir)
    fs = smr_ir.requires.sample_rate_chosen_hz
    expr = obj["derives"]["smr_envelope"]["expression"]

    hil = _find_call(expr, "hilbert")
    ref_hil = HilbertFirImpl(taps=65, sample_rate_hz=fs)
    np.testing.assert_array_equal(np.asarray(hil["coeffs"]["fir_taps"]), ref_hil.h)
    assert hil["coeffs"]["group_delay"] == ref_hil.group_delay

    sm = _find_call(expr, "smooth")
    ref_sm = SmoothImpl(tau_ms=250.0, sample_rate_hz=fs)
    assert sm["coeffs"]["alpha"] == ref_sm.alpha


def test_full_protocol_shapes_present(smr_ir):
    obj = ir_to_json_obj(smr_ir)

    # Input montage.
    assert obj["inputs"]["raw"]["montage"]["callee"] == "referential"

    # Thresholds: signal binding + tunability + constructor.
    smr_t = obj["thresholds"]["smr_t"]
    assert smr_t["signal"] == "derive/smr_envelope"
    assert smr_t["live_tunable"] is True
    assert smr_t["threshold_call"]["callee"] == "percentile"

    # Reward: both arms present, with the expected outermost calls.
    assert obj["reward"]["continuous"]["callee"] == "sigmoid"
    assert obj["reward"]["event"]["callee"] == "dwell"

    # Output channels.
    assert set(obj["output"]) == {"audio_chime", "audio_gain", "game_speed"}

    # Controls with their declared default.
    assert obj["controls"]["smr_target_pct"]["default"]["value"] == 70

    # Session phases preserve order, names, and the muted warmup.
    phases = obj["session"]["phases"]
    assert [p["name"] for p in phases] == ["warmup", "training", "cooldown"]
    assert phases[0]["output_muted"] is True

    # Topological order drives evaluation; it must be present and non-empty.
    assert isinstance(obj["topological_order"], list)
    assert "input/raw" in obj["topological_order"]


@pytest.mark.parametrize(
    "protocol_path",
    sorted(BENCH_PROTOCOLS.glob("*.refrain")),
    ids=lambda p: p.stem,
)
def test_bench_corpus_emits_and_round_trips(protocol_path):
    # Every protocol in the validation corpus must serialize end-to-end
    # (no unhandled IR node) and survive a JSON round-trip.
    ir = resolve(parse_file(protocol_path), load_amp_profile(AMP_PATH))
    obj = ir_to_json_obj(ir)
    assert json.loads(ir_to_json(ir)) == obj
    assert obj["name"] == ir.name
    assert obj["sample_rate_hz"] == ir.requires.sample_rate_chosen_hz


# ---------------------------------------------------------------------------
# Task 6: placement control omission — no-wire-change invariant
# ---------------------------------------------------------------------------


def test_ir_json_omits_placement_controls():
    src = '''
        protocol "poise" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls {
            site = placement { kind = "active"; default = "Cz"; allowed = ["Cz","C3"] }
            gain_pct = percent { default = 65 %; range = (50%, 90%); live_tunable = true }
          }
          input "raw" { montage = referential(active: site, reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    ir = resolve(parse(src), _AMP, bindings={"site": "C3"})
    obj = ir_to_json_obj(ir)
    assert "site" not in obj["controls"]      # placement omitted (resolve-time only)
    assert "gain_pct" in obj["controls"]      # numeric/live control still emitted


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

_REPL = """
    protocol "poise_ms" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls { sites = placement { kind = "set"; default = ["Cz"]; allowed = ["C3","Cz","C4"]; min = 1; max = 3 } }
      input "raw" { montage = referential(active: sites, reference: "linked_ears") }
      derive "smr" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
      threshold "smr_t" { signal = "smr"; type = absolute(8 uV) }
      reward { combine = "all"; event = dwell(condition: above("smr","smr_t"), duration: 100 ms) }
      output { audio_chime = reward.event }
    }
"""


def test_pair_and_set_controls_omitted_from_ir_json():
    # A pair-bound protocol emits NO placement control in ir_to_json_obj(ir)["controls"].
    ir = resolve(parse(_PAIR_PROTO), _AMP)
    obj = ir_to_json_obj(ir)
    assert "coh" not in obj["controls"]


def test_set_bound_ir_json_is_flat_multisite():
    # A set-bound protocol's IR-JSON has per-site inputs and no "sites" placement control.
    ir = resolve(parse(_REPL), _AMP, bindings={"sites": ["C3", "Cz", "C4"]})
    obj = ir_to_json_obj(ir)
    assert {"raw@C3", "raw@Cz", "raw@C4"} <= set(obj["inputs"])
    assert "sites" not in obj["controls"]   # set placement omitted


def test_placement_bound_ir_json_matches_literal_site():
    site_src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "C3"; allowed = ["C3"] } }
          input "raw" { montage = referential(active: site, reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    literal_src = site_src.replace(
        'controls { site = placement { kind = "active"; default = "C3"; allowed = ["C3"] } }', ''
    ).replace('referential(active: site,', 'referential(active: "C3",')
    a = ir_to_json_obj(resolve(parse(site_src), _AMP, bindings={"site": "C3"}))
    b = ir_to_json_obj(resolve(parse(literal_src), _AMP))
    # Same montage/input/output shape; placement control absent from wire in both cases.
    assert a["inputs"] == b["inputs"]
    assert a["output"] == b["output"]


# ---------------------------------------------------------------------------
# Named groups — Task 6: wire-invariant test
# ---------------------------------------------------------------------------


def test_groups_form_emits_identical_ir_json():
    """A protocol using a group emits IR-JSON byte-identical to the inline-list form."""
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
    assert ir_to_json_obj(grouped) == ir_to_json_obj(inline)
    # placement controls are omitted from IR-JSON either way; groups never appear
    assert "groups" not in ir_to_json_obj(grouped)


# ---------------------------------------------------------------------------
# Task 8 (Stage 1): IR-JSON — version-aware v0.1/v0.2 emission
# ---------------------------------------------------------------------------

_V01_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
      output { audio_gain = reward.continuous }
    }
'''

_V02_PROTO = '''
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


def test_single_reward_protocol_emits_v01_unchanged():
    ir = resolve(parse(_V01_PROTO), _AMP)
    obj = ir_to_json_obj(ir)
    assert obj["refrain_ir_version"] == "0.1"
    # v0.1 reward shape: exactly continuous + event keys, no components/combine.
    assert set(obj["reward"]) == {"continuous", "event"}


def test_weighted_protocol_emits_v02_with_components():
    ir = resolve(parse(_V02_PROTO), _AMP)
    obj = ir_to_json_obj(ir)
    assert obj["refrain_ir_version"] == "0.2"
    assert obj["reward"]["combine"] == "weighted"
    comps = {c["name"]: c for c in obj["reward"]["components"]}
    assert set(comps) == {"smr", "theta"}
    assert comps["smr"]["role"] == "reward"
    assert comps["theta"]["role"] == "suppress"
    # Weight is emitted as a control_ref node (weights are controls).
    assert comps["smr"]["weight"]["node"] == "control_ref"
    assert comps["smr"]["signal"]["callee"] == "sigmoid"
    # The composite is reachable via the continuous binding as a reward_field.
    assert obj["reward"]["continuous"]["node"] == "reward_field"
    assert obj["reward"]["continuous"]["field_path"] == "composite"
