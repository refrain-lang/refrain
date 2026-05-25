# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The committed IR-JSON golden vectors must validate against the published
JSON Schema (refrain-core/schema/ir-json-v0.1.schema.json). This keeps the
schema honest: it cannot drift from the wire format the emitter actually
produces, because every fixture is checked against it (and this test is wired
into the check_equivalence drift gate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "refrain-core" / "schema" / "ir-json-v0.1.schema.json"
FIXTURES = REPO / "refrain-core" / "tests" / "fixtures"
IR_JSON_FILES = sorted(FIXTURES.glob("*.ir.json"))


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), f"missing schema: {SCHEMA_PATH}"


def test_corpus_is_nonempty():
    assert IR_JSON_FILES, "no *.ir.json fixtures found — corpus path wrong?"


@pytest.mark.parametrize("ir_path", IR_JSON_FILES, ids=lambda p: p.stem)
def test_golden_ir_json_validates(validator, ir_path):
    doc = json.loads(ir_path.read_text())
    errors = sorted(validator.iter_errors(doc), key=lambda e: e.path)
    assert not errors, "schema rejected golden vector:\n" + "\n".join(
        f"  {list(e.path)}: {e.message}" for e in errors
    )


def test_malformed_ir_json_is_rejected(validator):
    bad = {"refrain_ir_version": "0.1", "output": {"x": {"node": "not_a_real_node"}}}
    assert not validator.is_valid(bad)
