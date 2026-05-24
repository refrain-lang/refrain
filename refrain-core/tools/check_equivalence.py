"""Drift gate: regenerate golden-vector fixtures, run Rust equivalence tests,
build the refrain_core wheel, and run the behavioral evaluator suite through
the Rust backend.

Usage (from worktree root):
    PYTHONPATH="$PWD" ./.venv/bin/python refrain-core/tools/check_equivalence.py

Exits 0 only when ALL FOUR steps succeed:
  1. gen_fixtures.py regenerates all fixtures from the current Python evaluator.
  2. `cargo test` (equivalence + events + taps + ir_deser) passes in refrain-core/.
  3. The refrain_core wheel is built and installed from current source.
  4. The behavioral evaluator suite (tests/test_eval_*.py) passes under
     REFRAIN_EVAL_BACKEND=rust — proving Python↔Rust behavioral parity.

REUSE: calls the existing gen_fixtures.py as a subprocess; does not duplicate
       fixture-generation logic.  The Rust tests already exist in
       refrain-core/tests/equivalence.rs — this script just drives them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]   # repo / worktree root
REFRAIN_CORE = WORKTREE / "refrain-core"
GEN_FIXTURES = WORKTREE / "refrain-core" / "tools" / "gen_fixtures.py"


def _run(label: str, cmd: list[str], *, cwd: Path, extra_env: dict[str, str] | None = None) -> bool:
    """Run *cmd* in *cwd*, stream output, return True on success."""
    import os
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    print(f"\n{'='*60}")
    print(f"STEP: {label}")
    print(f"  cmd : {' '.join(cmd)}")
    print(f"  cwd : {cwd}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, cwd=cwd, env=env)
    ok = result.returncode == 0
    status = "PASS" if ok else f"FAIL (exit {result.returncode})"
    print(f"  --> {status}")
    return ok


def main() -> int:
    results: dict[str, bool] = {}

    # Step 1 — regenerate fixtures from the current Python evaluator.
    # REUSE: delegates entirely to the existing gen_fixtures.py; no logic here.
    results["gen_fixtures"] = _run(
        "Regenerate golden-vector fixtures (gen_fixtures.py)",
        [sys.executable, str(GEN_FIXTURES)],
        cwd=WORKTREE,
        extra_env={"PYTHONPATH": str(WORKTREE)},
    )

    # Step 2 — all Rust tests against the freshly regenerated fixtures
    # (equivalence, events, taps, ir_deser). Running the whole suite makes
    # the gate catch drift in every golden-vector family, not just streams.
    results["cargo_test"] = _run(
        "Rust tests vs. regenerated golden vectors (cargo test)",
        ["cargo", "test"],
        cwd=REFRAIN_CORE,
    )

    # Step 3 — build and install the refrain_core wheel from current source.
    # This is the SAME install path locally and in CI: PEP517 build isolation
    # auto-provisions maturin per refrain-core/pyproject's build-system; the
    # Rust `target/` cache makes the compile incremental.  --force-reinstall
    # guarantees the wheel reflects current Rust source; --no-deps avoids
    # disturbing numpy/etc. (already installed).
    results["build_wheel"] = _run(
        "Build + install refrain_core wheel from current source (pip install)",
        [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(REFRAIN_CORE)],
        cwd=WORKTREE,
        extra_env={"PYO3_USE_ABI3_FORWARD_COMPATIBILITY": "1"},
    )

    # Step 4 — run the behavioral evaluator suite through the Rust backend.
    # This makes the gate fail on ANY Python↔Rust behavioral drift, not just
    # golden-vector drift detected by the Rust tests above.
    behavioral_files = [str(p) for p in sorted((WORKTREE / "tests").glob("test_eval_*.py"))]
    results["dual_backend_pytest"] = _run(
        "Behavioral evaluator suite under REFRAIN_EVAL_BACKEND=rust (pytest)",
        [sys.executable, "-m", "pytest", *behavioral_files, "-q"],
        cwd=WORKTREE,
        extra_env={"REFRAIN_EVAL_BACKEND": "rust", "PYTHONPATH": str(WORKTREE)},
    )

    # Summary
    print(f"\n{'='*60}")
    print("EQUIVALENCE + DUAL-BACKEND DRIFT GATE — SUMMARY")
    print(f"{'='*60}")
    all_ok = True
    for name, ok in results.items():
        mark = "PASS" if ok else "FAIL"
        print(f"  {mark}  {name}")
        if not ok:
            all_ok = False

    print(f"{'='*60}")
    if all_ok:
        print("RESULT: PASS — fixtures current, Rust core equivalent, wheel built, dual-backend parity confirmed.")
    else:
        print("RESULT: FAIL — see step output above for details.")
    print(f"{'='*60}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
