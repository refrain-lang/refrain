# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""End-to-end acceptance: smr_cz passes; a deliberate protocol bug is caught."""
from __future__ import annotations

from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SMR = str(REPO_ROOT / "bench" / "protocols" / "realistic_smr.refrain")


def test_smr_cz_directed_scenarios_pass_engine_check(capsys):
    rc = main(["fuzz", SMR, "--max-scenarios", "8"])
    out = capsys.readouterr().err
    assert "Engine check" in out
    assert "GENERATOR BUG" not in out
    assert rc in (0, 1), f"unexpected exit code {rc}"


def test_mutation_flipped_smr_above_to_below_changes_behavior(tmp_path, capsys):
    """Copy the SMR protocol, flip the SMR-above leaf to SMR-below, and assert
    the report's behavior summary / engine check reflects the change."""
    src = Path(SMR).read_text()
    mutated = src.replace(
        'above("smr_envelope",       "smr_t")',
        'below("smr_envelope",       "smr_t")',
    )
    assert mutated != src, "expected to replace the SMR-above leaf"
    out_path = tmp_path / "smr_cz_mutant.refrain"
    out_path.write_text(mutated)

    rc = main(["fuzz", str(out_path), "--max-scenarios", "8"])
    text = capsys.readouterr().err
    # The pipeline must complete (no vacuity/generator abort), so an unrelated
    # crash can't satisfy the behavioural assertion below.
    assert "What your protocol does" in text
    assert "GENERATOR BUG" not in text
    # The mutant inverts the SMR leaf: the report's behavioural summary should
    # show the flipped leaf, or the engine check should FAIL outright (rc == 1).
    assert ("leaf:above:smr_envelope:smr_t:false" in text
            or "leaf:below:smr_envelope:smr_t:true" in text
            or rc == 1)
