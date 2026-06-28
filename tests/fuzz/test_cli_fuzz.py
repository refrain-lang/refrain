# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the single-file `refrain fuzz` CLI path + exit-code contract."""
from __future__ import annotations

from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SMR = str(REPO_ROOT / "bench" / "protocols" / "realistic_smr.refrain")
# composite_smr_theta has a bare dwell(above(...)) reward block — triggers
# UnsupportedProtocol("single-condition reward"), so the CLI emits SKIPPED.
SINGLE_COND = str(REPO_ROOT / "bench" / "protocols" / "composite_smr_theta.refrain")
OTHMER_LIB = str(REPO_ROOT / "examples" / "othmer_ilf_cz_pz.refrain")


def test_supported_protocol_runs_and_reports(capsys):
    rc = main(["fuzz", SMR, "--max-scenarios", "3"])
    combined = "".join(capsys.readouterr())
    assert "What your protocol does" in combined
    assert "Engine check" in combined
    assert rc in (0, 1)


def test_unsupported_protocol_skips_with_exit_zero(capsys):
    rc = main(["fuzz", SINGLE_COND, "--max-scenarios", "3"])
    combined = "".join(capsys.readouterr())
    assert rc == 0
    assert "SKIPPED (unsupported: single-condition reward)" in combined


def test_resolve_error_returns_exit_two(capsys):
    # othmer_ilf_cz_pz `extends` a library with no loader -> ResolveError.
    rc = main(["fuzz", OTHMER_LIB])
    combined = "".join(capsys.readouterr())
    assert rc == 2
    assert "resolve failed" in combined.lower()


def test_missing_file_returns_exit_two(capsys):
    rc = main(["fuzz", "/nonexistent/path.refrain"])
    combined = "".join(capsys.readouterr())
    assert rc == 2
    assert "no such file" in combined.lower()
