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
    # NOT be reclassified as a skip (that would hide engine bugs). `predict` is
    # only invoked for oracle (sample-exact) scenarios, so this needs a
    # sample-exact-tier fixture — realistic_smr is metamorphic-tier now and
    # has none, so patching `predict` there would never even be exercised.
    monkeypatch.setattr(runner, "predict", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        _run("bench/protocols/micro_single_above.refrain")


def test_single_above_absolute_fuzzes_clean_full_depth():
    out = _run("bench/protocols/micro_single_above.refrain", max_scenarios=40)
    assert out.status == FUZZED
    assert out.passed is True


def test_center_bandwidth_single_above_fuzzes_clean_full_depth():
    out = _run("bench/protocols/micro_center_bandwidth.refrain", max_scenarios=40)
    assert out.status == FUZZED
    assert out.passed is True


def test_single_below_absolute_still_fuzzes_clean_full_depth():
    out = _run("bench/protocols/micro_single_below.refrain", max_scenarios=40)
    assert out.status == FUZZED
    assert out.passed is True


def test_percentile_single_leaf_now_fuzzes_clean_under_the_metamorphic_tier():
    out = _run("bench/protocols/micro_single_pct.refrain", max_scenarios=0)
    assert out.status == FUZZED
    assert out.passed is True
    assert "Metamorphic" in out.report


def test_realistic_smr_moves_to_the_metamorphic_tier_and_passes():
    # It fires 7 events on the quiet negative control; its old sample-exact pass
    # was hollow (the oracle marked the percentile regions DON'T-CARE).
    out = _run("bench/protocols/realistic_smr.refrain", max_scenarios=0)
    assert out.status == FUZZED
    assert out.passed is True
    assert "tier: metamorphic" in out.report


def test_absolute_only_protocols_keep_the_sample_exact_tier():
    for rel in ("bench/protocols/micro_single_above.refrain",
                "bench/protocols/micro_single_below.refrain"):
        out = _run(rel, max_scenarios=0)
        assert out.status == FUZZED, rel
        assert out.passed is True, rel
        assert "tier: sample_exact" in out.report, rel


def test_max_scenarios_never_truncates_a_sweep_group():
    """Truncating a sweep silently drops rungs and breaks the monotonicity
    comparison. The cap applies to oracle scenarios only.

    Uses a SAMPLE_EXACT protocol on purpose: a metamorphic-tier protocol has an
    empty oracle corpus, so `max_scenarios` would be a no-op there and the test
    would pass even if the cap were (wrongly) applied to sweep groups too.
    Here the cap really does bite the oracle corpus, and the sweeps must survive
    it intact — every rung of every group still reported.
    """
    capped = _run("bench/protocols/micro_single_above.refrain", max_scenarios=1)
    full = _run("bench/protocols/micro_single_above.refrain", max_scenarios=0)
    assert capped.status == FUZZED
    assert capped.passed is True

    def sweep_lines(report: str) -> list[str]:
        return [ln.strip() for ln in report.splitlines()
                if ln.strip().startswith(("[UP", "[DOWN", "[NO ASSERTION]"))]

    # The cap dropped oracle scenarios (fewer of them ran)...
    assert "scenarios:  1" in capped.report
    assert "scenarios:  1" not in full.report
    # ...but every sweep group survived, with the same series in each.
    assert sweep_lines(capped.report) == sweep_lines(full.report)
    assert sweep_lines(capped.report), "expected at least one sweep group"


def test_seed_is_threaded_into_every_scenario():
    a = _run_seeded("bench/protocols/micro_single_pct.refrain", seed=42)
    b = _run_seeded("bench/protocols/micro_single_pct.refrain", seed=43)
    assert a.passed is True and b.passed is True
    assert a.report != b.report  # different realization => different metrics


def _run_seeded(rel: str, *, seed: int):
    return fuzz_protocol(_ir(rel), path=rel, max_scenarios=0, chunk_size=64, seed=seed)
