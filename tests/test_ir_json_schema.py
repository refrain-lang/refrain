# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The committed IR-JSON golden vectors must validate against the published
JSON Schema. Version-aware: v0.1 goldens validate against ir-json-v0.1.schema.json
and v0.2 goldens validate against ir-json-v0.2.schema.json. This keeps the
schemas honest: they cannot drift from the wire format the emitter actually
produces, because every fixture is checked against it (and this test is wired
into the check_equivalence drift gate)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse
from refrain.resolver import resolve
from tests.conftest_staged import HET

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO / "src" / "refrain" / "schema"
SCHEMA_BY_VERSION = {
    "0.1": SCHEMA_DIR / "ir-json-v0.1.schema.json",
    "0.2": SCHEMA_DIR / "ir-json-v0.2.schema.json",
    "0.3": SCHEMA_DIR / "ir-json-v0.3.schema.json",
}
FIXTURES = REPO / "refrain-core" / "tests" / "fixtures"
IR_JSON_FILES = sorted(FIXTURES.glob("*.ir.json"))


@pytest.fixture(scope="module")
def validators():
    """One validator per published schema version, keyed by version string."""
    out = {}
    for version, path in SCHEMA_BY_VERSION.items():
        schema = json.loads(path.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        out[version] = jsonschema.Draft202012Validator(schema)
    return out


def test_schema_files_exist():
    for version, path in SCHEMA_BY_VERSION.items():
        assert path.exists(), f"missing v{version} schema: {path}"


def test_corpus_is_nonempty():
    assert IR_JSON_FILES, "no *.ir.json fixtures found — corpus path wrong?"


def test_corpus_covers_both_versions():
    # The corpus must exercise both wire versions, or the v0.2 schema is never
    # validated by the gate. (A v0.1 fixture and the composite_smr_theta v0.2
    # fixture both exist after Stage 2.)
    versions = {json.loads(p.read_text()).get("refrain_ir_version") for p in IR_JSON_FILES}
    assert "0.1" in versions, "no v0.1 golden vectors found"
    assert "0.2" in versions, "no v0.2 golden vectors found (composite fixture missing?)"


@pytest.mark.parametrize("ir_path", IR_JSON_FILES, ids=lambda p: p.stem)
def test_golden_ir_json_validates(validators, ir_path):
    doc = json.loads(ir_path.read_text())
    version = doc.get("refrain_ir_version")
    assert version in validators, f"{ir_path.name}: unknown refrain_ir_version {version!r}"
    validator = validators[version]
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, f"v{version} schema rejected {ir_path.name}:\n" + "\n".join(
        f"  {list(e.path)}: {e.message}" for e in errors
    )


def _valid_envelope():
    """Minimal structurally-valid IR-JSON (all required top-level fields present).
    Lets the rejection tests below isolate a single downstream defect, so a
    failure is attributable to the mutation rather than a missing envelope field."""
    return {
        "refrain_ir_version": "0.1",
        "sample_rate_hz": 256,
        "channels": ["Cz"],
        "inputs": {},
        "derives": {},
        "output": {"x": {"node": "number", "value": 1.0}},
        "topological_order": [],
    }


def test_valid_envelope_is_accepted(validators):
    assert validators["0.1"].is_valid(_valid_envelope())


def test_unknown_expr_node_is_rejected(validators):
    doc = _valid_envelope()
    doc["output"]["x"] = {"node": "not_a_real_node"}
    assert not validators["0.1"].is_valid(doc)


def test_missing_required_top_level_field_is_rejected(validators):
    doc = _valid_envelope()
    del doc["sample_rate_hz"]
    assert not validators["0.1"].is_valid(doc)


def _het_obj():
    """Resolve + emit the HET (staged, two-block) protocol IR-JSON."""
    return ir_to_json_obj(resolve(parse(HET)))


def _v02_schema():
    return json.loads(SCHEMA_BY_VERSION["0.2"].read_text())


def test_staged_protocol_emits_v02():
    # A protocol using blocks / reward bundles must be tagged v0.2 (not v0.1),
    # since those fields live in the v0.2 schema.
    assert _het_obj()["refrain_ir_version"] == "0.2"


def test_staged_ir_validates_against_schema():
    jsonschema.validate(_het_obj(), _v02_schema())   # raises on failure


def test_schema_rejects_bad_phase_mode():
    obj = copy.deepcopy(_het_obj())
    obj["session"]["phases"][1]["mode"] = "bogus_mode"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(obj, _v02_schema())
