"""Verify gate: the committed golden-vector fixtures still match the emitter.

This is the VERIFY half of the golden-vector gate; `gen_fixtures.py` is the
REGENERATE half. Keeping them apart is the point. The gate used to run
`gen_fixtures.py` over the committed fixtures and then point `cargo test` at
the result, so step 1 overwrote the baseline step 2 was supposed to compare
against: the committed contents were never read by anything, and 15 fixtures
carried a `sample_rate_chosen_hz` of 2048 Hz — a rate no amp profile in the
tree supports — across several releases with both the local gate and the
`rust-equivalence` CI job green (refrain#100).

What it does:
  1. Copy the committed fixtures into a throwaway directory. The copy is not
     decoration — `_gen_advisor_fixtures()` globs `*.ir.json` to build the
     advisor traces, and eight committed files (`seed_run.ir.json` and the
     seven `output_dwell*`) have no generator in the tree, so a bare directory
     would emit a trace file missing those entries and report it as drift.
  2. Regenerate into that directory via `gen_fixtures.py --out-dir`.
  3. Compare, file by file, and report what moved.

The committed tree is never written to, so `cargo test` afterwards is held to
the committed vectors and `git status` stays clean after a gate run.

Usage (from worktree root):
    PYTHONPATH="$PWD" ./.venv/bin/python refrain-core/tools/verify_fixtures.py

Exit 0 when the committed fixtures are current, 1 when they are not. The remedy
for a red run is `gen_fixtures.py`, then read the diff and commit it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
FIXTURES = WORKTREE / "refrain-core" / "tests" / "fixtures"
GEN_FIXTURES = Path(__file__).resolve().parent / "gen_fixtures.py"

# The parity bar `equivalence.rs` already holds the Rust core to. Reusing it
# means "the committed fixture is current" is measured the same way in both
# halves of the gate. It has to be a tolerance rather than byte equality: the
# fixtures are scipy output committed from one machine and regenerated on
# another (CI is Linux/3.12), so a last-bit difference in a filter coefficient
# is the measuring instrument, not drift.
ATOL = 1e-6
RTOL = 1e-4

# Drift lines printed per file before truncating. A whole stream going wrong
# produces one line per sample otherwise, and the first few already say what
# happened.
MAX_PER_FILE = 6

# Longest rendered value kept in a drift line before eliding. A single `.io.json`
# stream is thousands of samples; printing one in full buries the next finding.
MAX_VALUE_CHARS = 60


def _close(got: float, want: float) -> bool:
    return abs(got - want) <= ATOL + RTOL * abs(want)


def _fmt(value: object) -> str:
    text = repr(value)
    if len(text) <= MAX_VALUE_CHARS:
        return text
    return text[: MAX_VALUE_CHARS - 3] + "..."


def _diff_scalar(committed: object, fresh: object, where: str) -> list[str]:
    """Compare two JSON leaves.

    Floats compare within the parity bar; everything else compares exactly,
    including the int/float distinction the IR-JSON schema cares about. The
    2048 was an integer metadata field, which is why integers get no tolerance.
    """
    # bool before int: `isinstance(True, int)` is True, and a flag flipping is
    # drift however small the numeric gap looks.
    if isinstance(committed, bool) or isinstance(fresh, bool):
        return [] if committed is fresh else [f"{where}: {committed} -> {fresh}"]
    if isinstance(committed, float) and isinstance(fresh, float):
        if _close(fresh, committed):
            return []
        return [f"{where}: {committed!r} -> {fresh!r}"]
    if committed == fresh and type(committed) is type(fresh):
        return []
    return [f"{where}: {_fmt(committed)} -> {_fmt(fresh)}"]


def diff_json(committed: object, fresh: object, path: str = "") -> list[str]:
    """Every way `fresh` differs from `committed`, as `<path>: a -> b` lines."""
    where = path or "<root>"

    if isinstance(committed, dict):
        if not isinstance(fresh, dict):
            return [f"{where}: object -> {type(fresh).__name__}"]
        out: list[str] = []
        for key in sorted(set(committed) - set(fresh)):
            out.append(f"{path}.{key}: present in the committed fixture, not emitted")
        for key in sorted(set(fresh) - set(committed)):
            out.append(f"{path}.{key}: emitted, not in the committed fixture")
        for key in committed:
            if key in fresh:
                out += diff_json(committed[key], fresh[key], f"{path}.{key}")
        return out

    if isinstance(committed, list):
        if not isinstance(fresh, list):
            return [f"{where}: array -> {type(fresh).__name__}"]
        if len(committed) != len(fresh):
            return [f"{where}: length {len(committed)} -> {len(fresh)}"]
        out = []
        for i, (c, f) in enumerate(zip(committed, fresh, strict=True)):
            out += diff_json(c, f, f"{path}[{i}]")
        return out

    return _diff_scalar(committed, fresh, where)


def compare_dirs(committed: Path, fresh: Path) -> list[str]:
    """Report every difference between two fixture directories.

    Byte-identical files are passed without parsing — the common case, and the
    only cheap one over 12 MB of JSON. A file that differs at byte level but
    agrees within the parity bar is reported as a note, not as drift, so the
    float noise that forces the tolerance stays visible.
    """
    have = {p.name for p in committed.iterdir() if p.is_file()}
    emitted = {p.name for p in fresh.iterdir() if p.is_file()}

    drift: list[str] = []
    for name in sorted(have - emitted):
        drift.append(
            f"{name}: committed but no longer emitted — the Rust test reading "
            f"it is reading history nothing regenerates"
        )
    for name in sorted(emitted - have):
        drift.append(f"{name}: emitted but never committed — add it to the tree")

    for name in sorted(have & emitted):
        a, b = committed / name, fresh / name
        raw_a, raw_b = a.read_bytes(), b.read_bytes()
        if raw_a == raw_b:
            continue
        if not name.endswith(".json"):
            drift.append(f"{name}: differs ({len(raw_a)} -> {len(raw_b)} bytes)")
            continue
        try:
            obj_a, obj_b = json.loads(raw_a), json.loads(raw_b)
        except json.JSONDecodeError as exc:
            drift.append(f"{name}: not valid JSON ({exc})")
            continue
        lines = diff_json(obj_a, obj_b)
        if not lines:
            print(f"  note: {name} differs byte-for-byte but is within the parity bar")
            continue
        shown = lines[:MAX_PER_FILE]
        drift += [f"{name}{line}" for line in shown]
        if len(lines) > len(shown):
            drift.append(f"{name}: ... and {len(lines) - len(shown)} more")
    return drift


def regenerate_into(dest: Path) -> None:
    """Seed `dest` with the committed fixtures, then regenerate over it.

    Seeding first makes this exactly what the old in-place run did, minus the
    mutation: `gen_fixtures.py` writes into a directory that already holds the
    committed set, so the advisor-trace glob sees the four fixtures no
    generator produces.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES, dest, dirs_exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKTREE)
    # Captured rather than streamed: a successful run is 30-odd lines of
    # "written", which would bury the comparison this step exists for. On a
    # failure it is the only diagnostic, so it is printed then.
    proc = subprocess.run(
        [sys.executable, str(GEN_FIXTURES), "--out-dir", str(dest)],
        cwd=WORKTREE, env=env, check=False, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise subprocess.CalledProcessError(proc.returncode, proc.args)


def main() -> int:
    print(f"{'='*60}")
    print("VERIFY: committed golden vectors vs. what the emitter produces now")
    print(f"  fixtures : {FIXTURES}")
    print(f"{'='*60}")

    with tempfile.TemporaryDirectory(prefix="refrain-fixtures-") as tmp:
        fresh = Path(tmp) / "fixtures"
        try:
            regenerate_into(fresh)
        except subprocess.CalledProcessError as exc:
            print(f"\n  --> FAIL: gen_fixtures.py exited {exc.returncode}")
            return 1
        drift = compare_dirs(FIXTURES, fresh)

    if not drift:
        print("\n  --> PASS: the committed fixtures are what the emitter produces.")
        return 0

    print(f"\n  --> FAIL: {len(drift)} difference(s) between the committed")
    print("      fixtures and the emitter:\n")
    for line in drift:
        print(f"      {line}")
    print(
        "\n      Remedy: run `gen_fixtures.py`, read the diff, and commit it if\n"
        "      the change is intended. If it is not, the emitter regressed —\n"
        "      the committed fixtures are the baseline, not the suspect."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
