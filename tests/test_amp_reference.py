# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""`amp.reference` namespace: resolve-time fold + fail-closed behaviour."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import refrain
from refrain.amp_profile import load_amp_profile
from refrain.ir_json import ir_to_json_obj
from refrain.resolver import ResolveError, resolve

PROFILES = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles"
BRAINBIT = load_amp_profile(PROFILES / "brainbit_flex.json")
Q21 = load_amp_profile(PROFILES / "q21.json")


def _proto(reference: str) -> str:
    # Minimal SMR-at-Cz protocol; `reference` is spliced verbatim so both the
    # amp.reference form and a literal form come from one template. The reward
    # references the derive/threshold as string literals and via dwell(above(...)),
    # matching real-protocol idiom (validated against refrain v0.14.0).
    return f'''
protocol "t_v1" {{
  meta {{ title = "t"; status = "draft" }}
  requires {{ sample_rate = ">= 250 Hz"; channels = ["Cz"] }}
  input "raw" {{ montage = referential(active: "Cz", reference: {reference}) }}
  derive "env" {{
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz)), hilbert(), magnitude() ]
  }}
  threshold "env_t" {{ signal = "env"; type = absolute(value: 5 uV) }}
  reward {{ event = dwell(condition: above("env", "env_t"), duration: 250 ms) }}
  output {{ audio = reward.event }}
}}
'''


def _reference_arg(ir: dict) -> str:
    montage = ir["inputs"]["raw"]["montage"]
    arg = next(a for a in montage["args"] if a["name"] == "reference")
    return arg["value"]["value"]


def test_amp_reference_folds_to_device_on_brainbit():
    ir = ir_to_json_obj(resolve(refrain.parse(_proto("amp.reference")), amp=BRAINBIT))
    assert _reference_arg(ir) == "device"


def test_amp_reference_folds_to_linked_ears_on_q21():
    ir = ir_to_json_obj(resolve(refrain.parse(_proto("amp.reference")), amp=Q21))
    assert _reference_arg(ir) == "linked_ears"


def test_fold_is_byte_identical_to_literal_device_on_brainbit():
    got = ir_to_json_obj(resolve(refrain.parse(_proto("amp.reference")), amp=BRAINBIT))
    want = ir_to_json_obj(resolve(refrain.parse(_proto('"device"')), amp=BRAINBIT))
    assert got == want


def test_amp_reference_without_profile_fails_closed():
    with pytest.raises(ResolveError, match="requires an amp profile"):
        resolve(refrain.parse(_proto("amp.reference")), amp=None)


def test_amp_non_allowlisted_field_fails():
    with pytest.raises(ResolveError, match="not an exposed amp field"):
        resolve(refrain.parse(_proto("amp.adc_bits")), amp=BRAINBIT)


def test_amp_reference_missing_on_profile_fails(tmp_path):
    data = json.loads((PROFILES / "brainbit_flex.json").read_text())
    data.pop("reference", None)
    f = tmp_path / "no_ref.json"; f.write_text(json.dumps(data))
    amp = load_amp_profile(f)
    with pytest.raises(ResolveError, match="declares no 'reference'"):
        resolve(refrain.parse(_proto("amp.reference")), amp=amp)
