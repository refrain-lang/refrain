# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""fuzz_protocol() outcome classification + guarded backstop."""
from __future__ import annotations

from pathlib import Path

import pytest

from refrain.fuzz import runner
from refrain.fuzz.runner import FUZZED, SKIPPED, fuzz_protocol
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ir(rel: str):
    return resolve(parse_file(REPO_ROOT / rel), None)


def _run(rel: str, **kw):
    ir = _ir(rel)
    return fuzz_protocol(ir, path=rel, max_scenarios=kw.get("max_scenarios", 2),
                         chunk_size=kw.get("chunk_size", 64))


def test_supported_protocol_is_fuzzed_and_passes():
    out = _run("bench/protocols/realistic_smr.refrain")
    assert out.status == FUZZED
    assert out.passed is True
    assert out.report and "Engine check" in out.report


def test_single_condition_is_skipped_with_typed_reason():
    # composite_smr_theta has a bare dwell(above(reward.composite, ...)) reward;
    # Inc 1: the leaf classifier gives the specific "composite-signal" reason.
    out = _run("bench/protocols/composite_smr_theta.refrain")
    assert out.status == SKIPPED
    assert out.reason == "composite-signal reward condition"


def test_unrecognized_condition_is_skipped_unclassified():
    out = _run("bench/protocols/micro_08_bandpower.refrain")
    assert out.status == SKIPPED
    assert out.reason.startswith("unclassified (")


def test_backstop_does_not_swallow_scenario_loop_errors(monkeypatch):
    # An exception inside the evaluate/oracle/check loop must propagate,
    # NOT be reclassified as a skip (that would hide engine bugs).
    monkeypatch.setattr(runner, "predict", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        _run("bench/protocols/realistic_smr.refrain")
