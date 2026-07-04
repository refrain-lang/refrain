# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Batch/dir runner: aggregate report, coverage, exit codes."""
from __future__ import annotations

import shutil
from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED = REPO_ROOT / "bench/protocols/realistic_smr.refrain"
# Correction 1: composite_smr_theta skips as "single-condition reward"
UNSUPPORTED = REPO_ROOT / "bench/protocols/composite_smr_theta.refrain"


def _mixed_dir(tmp_path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    shutil.copy(SUPPORTED, d / "supported.refrain")
    shutil.copy(UNSUPPORTED, d / "skipme.refrain")
    (d / "broken.refrain").write_text("this is not a valid protocol {{{")
    return d


def test_batch_reports_counts_coverage_and_breakdown(tmp_path, capsys):
    rc = main(["fuzz", str(_mixed_dir(tmp_path)), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "fuzzed 1" in out and "skipped 1" in out and "errored 1" in out
    assert "coverage: fuzzed 1 / total 3" in out
    assert "composite-signal reward condition" in out
    assert rc == 1  # the broken/errored file makes the batch fail


def test_batch_exit_zero_when_only_skips(tmp_path, capsys):
    d = tmp_path / "c"
    d.mkdir()
    shutil.copy(SUPPORTED, d / "ok.refrain")
    shutil.copy(UNSUPPORTED, d / "skip.refrain")
    rc = main(["fuzz", str(d), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert rc == 0
    assert "errored 0" in out


def test_batch_aggregates_multiple_paths(tmp_path, capsys):
    # --library examples (not examples/library) for othmer_ilf_cz_pz resolution.
    # total 26 = bench/protocols 17 (13 Inc0 + 4 Inc1 fixtures) + examples 9.
    # fuzzed 7 = Inc0's 4 + Inc1's micro_single_above/below/center_bandwidth.
    rc = main(["fuzz", "bench/protocols", "examples",
               "--library", "examples", "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "coverage: fuzzed 7 / total 26" in out
    # Inc1 splits the old generic "single-condition reward" skip into specific,
    # feature-mapped reasons (so the breakdown maps to later increments).
    assert "single percentile-leaf reward (needs calibrated oracle)" in out
    assert "composite-signal reward condition" in out
    assert rc == 0  # only skips/known-passes across the real corpus


def test_single_file_path_unchanged(capsys):
    rc = main(["fuzz", str(SUPPORTED), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "Engine check" in out  # single-file report, not the batch summary
    assert rc in (0, 1)
