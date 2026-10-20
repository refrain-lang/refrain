# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The golden-vector gate must be able to fail (refrain#100).

`check_equivalence.py` ran `gen_fixtures.py` over the committed fixtures and
then pointed `cargo test` at the result, so step 1 overwrote the baseline that
step 2 was supposed to compare against. The committed contents were never read
by anything: they could drift arbitrarily and both the local gate and the
`rust-equivalence` CI job stayed green. They did — 15 fixtures carried
`sample_rate_chosen_hz: 2048` from before the Q21 was capped to 256 Hz, across
several releases, and nothing noticed. (That field was corrected in passing by
#103; the hole that hid it was not.)

Regeneration and comparison are now separate operations. `gen_fixtures.py
--out-dir DIR` is the regenerate mode; `verify_fixtures.py` is the verify mode,
and it regenerates into a throwaway directory so the committed fixtures stay on
disk for `cargo test` to be held to.

What is tested here is the comparison itself — the part that has to notice the
2048 — plus the one structural property that cannot be asserted by reading the
code: regenerating leaves the committed tree untouched. The end-to-end run
belongs to the gate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "refrain-core" / "tools"
FIXTURES = REPO / "refrain-core" / "tests" / "fixtures"

# A floor on how many committed fixtures the emitter must rewrite during a
# verify run, so adding one does not break the build but a `--out-dir` that
# stopped working does. Measured: 52 of the 60 committed files are regenerated;
# the other 8 (`seed_run.ir.json` and the seven `output_dwell*`) have no
# generator in the tree at all.
MIN_REGENERATED = 20

# The tools/ directory is a scripts directory, not an installed package —
# `gen_fixtures.py` reaches its sibling `advisor_scenarios` the same way. A hard
# import rather than `importorskip`: the tool is in this repo, so an absent one
# is a deleted gate, and a file of skips is how a gate stops being a gate.
sys.path.insert(0, str(TOOLS))

import verify_fixtures  # noqa: E402 — needs the sys.path line above


def _dirs(tmp_path: Path, committed: dict, fresh: dict) -> tuple[Path, Path]:
    """Write two fixture directories from `{filename: json-object}` maps."""
    a, b = tmp_path / "committed", tmp_path / "fresh"
    for d, files in ((a, committed), (b, fresh)):
        d.mkdir()
        for name, obj in files.items():
            (d / name).write_text(json.dumps(obj, indent=2) + "\n")
    return a, b


# ---------------------------------------------------------------------------
# The drift the gate was supposed to catch and did not
# ---------------------------------------------------------------------------


def test_the_2048_is_reported(tmp_path):
    """The exact shape of the drift that survived several releases: one integer
    metadata field, same way in every fixture."""
    stale = {"requires": {"sample_rate_min_hz": 256, "sample_rate_chosen_hz": 2048}}
    current = {"requires": {"sample_rate_min_hz": 256, "sample_rate_chosen_hz": 256}}
    a, b = _dirs(tmp_path, {"micro_01.ir.json": stale}, {"micro_01.ir.json": current})

    drift = verify_fixtures.compare_dirs(a, b)

    assert drift, "the 2048 went unreported — this is the whole defect"
    joined = "\n".join(drift)
    assert "micro_01.ir.json" in joined
    assert "sample_rate_chosen_hz" in joined
    assert "2048" in joined and "256" in joined


def test_identical_fixtures_report_nothing(tmp_path):
    obj = {"requires": {"sample_rate_chosen_hz": 256}, "taps": [1, 2, 3]}
    a, b = _dirs(tmp_path, {"x.ir.json": obj}, {"x.ir.json": obj})
    assert verify_fixtures.compare_dirs(a, b) == []


# ---------------------------------------------------------------------------
# Float noise must not be drift, and a behavioural change must be
# ---------------------------------------------------------------------------


def test_a_float_inside_the_parity_bar_is_not_drift(tmp_path):
    """The fixtures are scipy output committed from one machine and regenerated
    on another, so a last-bit difference in a filter coefficient is noise. The
    bar is the one `equivalence.rs` already holds the Rust core to, so "the
    committed fixture is current" means the same thing in both halves of the
    gate."""
    a, b = _dirs(
        tmp_path,
        {"x.io.json": {"out": [1.0, 2.0, 3.0]}},
        {"x.io.json": {"out": [1.0, 2.0 + 1e-12, 3.0]}},
    )
    assert verify_fixtures.compare_dirs(a, b) == []


def test_a_float_outside_the_parity_bar_is_drift(tmp_path):
    a, b = _dirs(
        tmp_path,
        {"x.io.json": {"out": [1.0, 2.0, 3.0]}},
        {"x.io.json": {"out": [1.0, 2.5, 3.0]}},
    )
    drift = verify_fixtures.compare_dirs(a, b)
    assert drift
    assert "out[1]" in "\n".join(drift), drift


def test_a_small_absolute_change_near_zero_is_not_drift(tmp_path):
    """A relative tolerance alone would call 1e-20 vs 2e-20 a 100% change. Every
    stream has a quiet stretch, so an absolute floor is what keeps the gate from
    being noise."""
    a, b = _dirs(
        tmp_path,
        {"x.io.json": {"out": [0.0, 1e-20]}},
        {"x.io.json": {"out": [1e-18, 2e-20]}},
    )
    assert verify_fixtures.compare_dirs(a, b) == []


def test_an_int_that_became_a_float_is_drift(tmp_path):
    """`256` and `256.0` are the same number and different wire formats; the
    IR-JSON schema distinguishes them."""
    a, b = _dirs(
        tmp_path,
        {"x.ir.json": {"sample_rate_chosen_hz": 256}},
        {"x.ir.json": {"sample_rate_chosen_hz": 256.0}},
    )
    assert verify_fixtures.compare_dirs(a, b)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_a_dropped_key_is_drift(tmp_path):
    a, b = _dirs(
        tmp_path,
        {"x.ir.json": {"requires": {"markers": "not_required", "impedance": "x"}}},
        {"x.ir.json": {"requires": {"markers": "not_required"}}},
    )
    drift = verify_fixtures.compare_dirs(a, b)
    assert drift and "impedance" in "\n".join(drift), drift


def test_a_changed_list_length_is_drift(tmp_path):
    a, b = _dirs(tmp_path, {"x.io.json": {"out": [1.0, 2.0]}},
                 {"x.io.json": {"out": [1.0, 2.0, 3.0]}})
    assert verify_fixtures.compare_dirs(a, b)


def test_a_fixture_the_emitter_no_longer_produces_is_drift(tmp_path):
    """The emitter stopped writing a committed fixture: the Rust test reading it
    is now reading history nobody regenerates."""
    a, b = _dirs(tmp_path, {"gone.ir.json": {"a": 1}}, {})
    drift = verify_fixtures.compare_dirs(a, b)
    assert drift and "gone.ir.json" in "\n".join(drift), drift


def test_a_fixture_nobody_committed_is_drift(tmp_path):
    """The emitter produces a fixture that is not in the tree — the usual cause
    being a new fixture added to `gen_fixtures.py` and never committed."""
    a, b = _dirs(tmp_path, {}, {"new.ir.json": {"a": 1}})
    drift = verify_fixtures.compare_dirs(a, b)
    assert drift and "new.ir.json" in "\n".join(drift), drift


def test_a_non_json_file_is_compared_byte_for_byte(tmp_path):
    a, b = tmp_path / "committed", tmp_path / "fresh"
    a.mkdir()
    b.mkdir()
    (a / "notes.txt").write_text("one\n")
    (b / "notes.txt").write_text("two\n")
    assert verify_fixtures.compare_dirs(a, b)


# ---------------------------------------------------------------------------
# The structural property: verifying does not overwrite the baseline
# ---------------------------------------------------------------------------


def _git_dirty(path: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--", str(path)],
        cwd=REPO, capture_output=True, text=True, check=False,
    ).stdout.strip()


def test_regenerating_for_the_verify_step_leaves_the_committed_fixtures_alone(tmp_path):
    """The defect in one assertion. `gen_fixtures.py` must be able to write
    somewhere other than the tree, because a gate that regenerates the baseline
    it is about to read has nothing to compare against — and a dirty fixture
    directory after every local gate run is what trained people to ignore it."""
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    if _git_dirty(FIXTURES):
        pytest.skip("committed fixtures are already modified locally")

    before = {p.name: p.read_bytes() for p in sorted(FIXTURES.iterdir()) if p.is_file()}

    fresh = tmp_path / "fresh"
    verify_fixtures.regenerate_into(fresh)

    after = {p.name: p.read_bytes() for p in sorted(FIXTURES.iterdir()) if p.is_file()}
    assert after == before, "regenerating for the verify step rewrote the committed tree"
    assert not _git_dirty(FIXTURES), _git_dirty(FIXTURES)

    emitted = {p.name for p in fresh.iterdir() if p.is_file()}
    assert emitted >= set(before), sorted(set(before) - emitted)

    # And it really did regenerate. This is the vacuity guard that matters: the
    # throwaway directory starts as a copy of the committed one, so a
    # `--out-dir` that was quietly ignored would leave `compare_dirs` comparing
    # a copy against its own source — always green, which is the unfailable
    # gate all over again. `shutil.copytree` preserves mtimes, so a fixture the
    # emitter wrote is strictly newer than the committed file it came from.
    rewritten = [
        name for name in sorted(emitted & set(before))
        if (fresh / name).stat().st_mtime > (FIXTURES / name).stat().st_mtime
    ]
    assert len(rewritten) >= MIN_REGENERATED, (
        f"only {len(rewritten)} fixtures were actually written by the emitter "
        f"({sorted(rewritten)}) — the rest are the seeded copy, so the verify "
        f"step would be comparing a copy against its own source"
    )


def test_the_regenerate_mode_is_reachable_from_the_command_line():
    """If `--out-dir` ever disappears, `verify_fixtures.py` would quietly
    regenerate in place again and the gate would go back to being unfailable."""
    out = subprocess.run(
        [sys.executable, str(TOOLS / "gen_fixtures.py"), "--help"],
        cwd=REPO, capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    ).stdout
    assert "--out-dir" in out, out
