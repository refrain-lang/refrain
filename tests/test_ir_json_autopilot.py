# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
import json
from pathlib import Path

import jsonschema

from refrain.compile_json import compile_to_ir_json
from tests._autopilot_fixtures import ap, plain

REPO = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((REPO / "src/refrain/schema/ir-json-v0.4.schema.json").read_text())


def test_policy_protocol_is_v04_and_schema_valid():
    obj = compile_to_ir_json(ap()).ir_json
    assert obj["refrain_ir_version"] == "0.4"
    assert obj["reward"]["check_names"] == ["theta", "crossover"]
    a = obj["autopilot"]
    assert a["reward_target"] == [0.10, 0.35]
    assert a["watch_samples"] == 20 * 256 and a["equipment_settle_samples"] == 5 * 256
    assert a["guards"] == {"emg": {"max": 0.15, "say": "Muscle artifact."}}
    assert a["citation"] == ["Test policy"]
    x = obj["controls"]["xover"]["autopilot"]
    assert x == {
        "strategy": "fixed_step", "fixes": "crossover", "higher_is": "harder", "apply": "auto",
        "limits": [0.5, 1.0], "round_to": 0.01, "decimals": 2, "say": None,
        "between_moves_samples": None, "step": 0.05, "from": None, "window_samples": None,
        "percentile": None, "citation": [],
    }
    assert "autopilot" not in obj["controls"]["t_uv"]            # dropped by only_when
    jsonschema.Draft202012Validator(SCHEMA).validate(obj)


def test_names_alone_make_v04():
    obj = compile_to_ir_json(plain(theta_as=' as "theta"')).ir_json
    assert obj["refrain_ir_version"] == "0.4" and "autopilot" not in obj


def test_plain_protocol_is_unchanged():
    obj = compile_to_ir_json(plain()).ir_json
    assert obj["refrain_ir_version"] == "0.1"
    assert "autopilot" not in obj and "check_names" not in obj["reward"]


def test_decimals_follow_round_to():
    from refrain.ir_json import _decimals
    assert [_decimals(x) for x in (None, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)] == [2, 2, 2, 1, 1, 0, 0]
