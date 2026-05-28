# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the `refrain fuzz` CLI subcommand."""
from __future__ import annotations

from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SMR = str(REPO_ROOT / "bench" / "protocols" / "realistic_smr.refrain")


def test_refrain_fuzz_runs_and_emits_balanced_report(capsys):
    rc = main(["fuzz", SMR, "--max-scenarios", "3"])
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "What your protocol does" in combined
    assert "Engine check" in combined
    assert rc in (0, 1)   # 0 = PASS, 1 = FAIL — both valid for a smoke test


def test_refrain_fuzz_missing_file_returns_nonzero(capsys):
    rc = main(["fuzz", "/nonexistent/path.refrain"])
    out = capsys.readouterr()
    assert rc != 0
    assert "no such file" in out.err.lower()
