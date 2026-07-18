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
    # total 31 = bench/protocols 22 (13 Inc0 + 4 Inc1 fixtures + the metamorphic
    # tier's micro_multi_leaf_control_absolute skip fixture + the expression-
    # position control_ref regression fixture micro_11_control_expr (skipped as
    # "reward.event has no all_of/any_of condition" since it only declares
    # `continuous`) + Task 3's param-slot control_ref regression
    # fixture micro_12_control_param_slots, which now also wires a control_ref
    # into `inside(low:/high:)` inside a `dwell(...)` reward.event and so is
    # skipped instead as "unrecognized condition expr IRCall" — the same
    # reason micro_07_ilf/micro_08_bandpower are skipped, since the fuzzer's
    # condition surface only understands all_of/any_of/above/below, not a bare
    # `inside(...)` — plus the two baseline-seeding fixtures seed_smr_baseline
    # and seed_exprpos, both skipped as "reward.event has no all_of/any_of
    # condition" since they declare only a `continuous` sigmoid reward)
    # + examples 9.
    # fuzzed 8 = Inc0's 4 + Inc1's micro_single_above/below/center_bandwidth +
    # Task 6's micro_single_pct (percentile single-leaf no longer skips — it
    # now fuzzes under the metamorphic tier instead of the old calibrated-
    # oracle skip).
    rc = main(["fuzz", "bench/protocols", "examples",
               "--library", "examples", "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "coverage: fuzzed 8 / total 31" in out
    # Inc1 splits the old generic "single-condition reward" skip into specific,
    # feature-mapped reasons (so the breakdown maps to later increments); the
    # multi-leaf control-absolute fixture adds its own feature-mapped skip.
    assert "composite-signal reward condition" in out
    assert "absolute threshold value did not resolve to a literal" in out
    assert rc == 0  # only skips/known-passes across the real corpus


def test_single_file_path_unchanged(capsys):
    rc = main(["fuzz", str(SUPPORTED), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "Engine check" in out  # single-file report, not the batch summary
    assert rc in (0, 1)


def test_batch_eval_error_becomes_errored_not_crash(tmp_path, capsys):
    """An evaluator-setup error on one protocol must be classified ERRORED, not
    abort the batch.

    The trigger here is `passthrough()`, which demands a single-channel source
    while the synthetic source always carries the ear channels — a real, still
    open gap (fuzzing the HRV/passthrough protocols needs its own increment).
    A montage naming a channel outside `requires.channels` no longer errors:
    `channels_for_synthetic` synthesizes the montage's electrodes."""
    d = tmp_path / "c"
    d.mkdir()
    shutil.copy(SUPPORTED, d / "ok.refrain")
    (d / "passthrough_multichannel.refrain").write_text(
        'protocol "passthrough_multichannel" {\n'
        '  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }\n'
        '  input "raw" { montage = passthrough() }\n'
        '  derive "env" { from = "raw"\n'
        "    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), "
        "magnitude(), smooth(tau: 250 ms) ] }\n"
        '  threshold "t" { signal = "env"; type = absolute(8 uV) }\n'
        '  reward { event = dwell(condition: above("env", "t"), duration: 250 ms) }\n'
        "  output { audio_chime = reward.event }\n"
        '  session { phases = [ phase { name = "training"; duration = 30 min } ] }\n'
        "}\n"
    )
    rc = main(["fuzz", str(d), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "errored 1" in out   # the batch completed and reported it
    assert "fuzzed 1" in out    # the good protocol still fuzzed
    assert rc == 1              # errors keep the build red


def test_montage_channels_outside_requires_fuzz_not_error(tmp_path, capsys):
    """A montage naming an electrode absent from `requires.channels` — what the
    BrainBit `placement_*` protocols do, since a placement control substitutes
    its channels into the montage at resolve time — must FUZZ, not ERROR."""
    d = tmp_path / "c"
    d.mkdir()
    (d / "bipolar_c3_c4.refrain").write_text(
        'protocol "bipolar_c3_c4" {\n'
        '  requires { sample_rate = ">= 256 Hz" }\n'
        '  input "raw" { montage = bipolar(plus: "C3", minus: "C4") }\n'
        '  derive "env" { from = "raw"\n'
        "    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), "
        "magnitude(), smooth(tau: 250 ms) ] }\n"
        '  threshold "t" { signal = "env"; type = absolute(8 uV) }\n'
        '  reward { event = dwell(condition: above("env", "t"), duration: 250 ms) }\n'
        "  output { audio_chime = reward.event }\n"
        '  session { phases = [ phase { name = "training"; duration = 30 min } ] }\n'
        "}\n"
    )
    rc = main(["fuzz", str(d), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "errored 0" in out
    assert "fuzzed 1" in out
    assert rc == 0
