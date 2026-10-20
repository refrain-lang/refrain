"""Drift gate: verify the committed golden-vector fixtures are current, run the
Rust equivalence tests against them, build the refrain_core wheel, run the
behavioral evaluator suite through the Rust backend, and validate IR-JSON
golden vectors against the published JSON Schema.

Usage (from worktree root):
    PYTHONPATH="$PWD" ./.venv/bin/python refrain-core/tools/check_equivalence.py

Exits 0 only when ALL FIVE steps succeed:
  1. verify_fixtures.py confirms the COMMITTED fixtures are what the current
     Python evaluator emits — regenerating into a throwaway directory, so the
     committed vectors survive into step 2.
  2. `cargo test` (equivalence + events + taps + ir_deser) passes in
     refrain-core/ against those committed vectors.
  3. The refrain_core wheel is built and installed from current source.
  4. The behavioral evaluator suite (tests/test_eval_*.py) passes under
     REFRAIN_EVAL_BACKEND=rust — proving Python↔Rust behavioral parity.
  5. IR-JSON golden vectors (tests/fixtures/*.ir.json) validate against the
     published JSON Schema matching each vector's `refrain_ir_version`
     (src/refrain/schema/ir-json-v0.1.schema.json or ir-json-v0.2.schema.json).

Step 1 used to BE `gen_fixtures.py`, run in place, which overwrote the
committed fixtures before step 2 read them. Python↔Rust parity was still
proven; the golden property — today matches a known-good past — was not, since
the expected values were regenerated in the same run meant to check them
(refrain#100). Regeneration is now a separate, deliberate act:
`gen_fixtures.py`, read the diff, commit.

REUSE: drives the existing gen_fixtures.py (via verify_fixtures.py) as a
       subprocess; does not duplicate fixture-generation or comparison logic.
       The Rust tests already exist in refrain-core/tests/equivalence.rs —
       this script just drives them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]   # repo / worktree root
REFRAIN_CORE = WORKTREE / "refrain-core"
VERIFY_FIXTURES = WORKTREE / "refrain-core" / "tools" / "verify_fixtures.py"


def _run(label: str, cmd: list[str], *, cwd: Path, extra_env: dict[str, str] | None = None) -> bool:
    """Run *cmd* in *cwd*, stream output, return True on success."""
    import os
    env = os.environ.copy()
    # Ensure ~/.cargo/bin is on PATH so `cargo` is found even when the gate is
    # launched from Python without the Cargo shims on the shell PATH.
    cargo_bin = str(Path.home() / ".cargo" / "bin")
    current_path = env.get("PATH", "")
    if cargo_bin not in current_path:
        env["PATH"] = cargo_bin + os.pathsep + current_path
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

    # Step 1 — the committed fixtures must be what the emitter produces now.
    # REUSE: delegates to verify_fixtures.py, which drives gen_fixtures.py into
    # a throwaway directory; no generation or comparison logic here. The
    # committed tree is not written to, which is what makes step 2 a comparison
    # against a baseline rather than against its own output.
    results["verify_fixtures"] = _run(
        "Verify committed golden vectors are current (verify_fixtures.py)",
        [sys.executable, str(VERIFY_FIXTURES)],
        cwd=WORKTREE,
        extra_env={"PYTHONPATH": str(WORKTREE)},
    )

    # Step 2 — all Rust tests against the COMMITTED fixtures (equivalence,
    # events, taps, ir_deser). Running the whole suite makes the gate catch
    # drift in every golden-vector family, not just streams.
    results["cargo_test"] = _run(
        "Rust tests vs. committed golden vectors (cargo test)",
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

    # Step 5 — IR-JSON golden vectors validate against the published JSON Schema.
    results["schema_validation"] = _run(
        "IR-JSON golden vectors vs. JSON Schema (pytest)",
        [sys.executable, "-m", "pytest", str(WORKTREE / "tests" / "test_ir_json_schema.py"), "-q"],
        cwd=WORKTREE,
        extra_env={"PYTHONPATH": str(WORKTREE)},
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
        print("RESULT: PASS — committed fixtures current, Rust core equivalent to them, wheel built, dual-backend parity confirmed, IR-JSON schema valid.")
    else:
        print("RESULT: FAIL — see step output above for details.")
    print(f"{'='*60}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
