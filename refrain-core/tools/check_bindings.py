"""Drift gate: cross-compile mobile static libs then check Swift/Kotlin bindings are current.

Usage (from worktree root):
    . "$HOME/.cargo/env"
    PYTHONPATH="$PWD" ./.venv/bin/python refrain-core/tools/check_bindings.py

Exits 0 only when ALL steps succeed:
  1. Cross-compile the staticlib for aarch64-apple-ios  (--features uniffi).
  2. Cross-compile the staticlib for aarch64-linux-android (--features uniffi).
  3. Regenerate Swift + Kotlin bindings into a temp dir from the host cdylib.
  4. Diff the temp output against committed bindings/swift + bindings/kotlin.
     Fail if any difference is found (i.e. committed bindings are stale).

Prints a clear PASS/FAIL summary; exits non-zero on any failure.

REUSE: mirrors check_equivalence.py structure — same _run() helper, same
       summary block.  Cross-compile commands are verbatim from
       bindings/README.md (the README is the single source of truth; no logic
       is duplicated here beyond calling the exact commands).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]   # repo / worktree root
REFRAIN_CORE = WORKTREE / "refrain-core"
BINDINGS_SWIFT_COMMITTED = REFRAIN_CORE / "bindings" / "swift"
BINDINGS_KOTLIN_COMMITTED = REFRAIN_CORE / "bindings" / "kotlin"

# On Linux CI:  librefrain_core.so
# On macOS dev: librefrain_core.dylib
_HOST_LIB = (
    "librefrain_core.dylib"
    if sys.platform == "darwin"
    else "librefrain_core.so"
)
HOST_LIB_PATH = REFRAIN_CORE / "target" / "release" / _HOST_LIB


def _run(label: str, cmd: list[str], *, cwd: Path, extra_env: dict[str, str] | None = None) -> bool:
    """Run *cmd* in *cwd*, stream output, return True on success."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    print(f"\n{'='*60}")
    print(f"STEP: {label}")
    print(f"  cmd : {' '.join(str(c) for c in cmd)}")
    print(f"  cwd : {cwd}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, cwd=cwd, env=env)
    ok = result.returncode == 0
    status = "PASS" if ok else f"FAIL (exit {result.returncode})"
    print(f"  --> {status}")
    return ok


def _diff_dirs(label: str, generated: Path, committed: Path) -> bool:
    """Return True if dirs are identical, False (with a diff print) if not."""
    print(f"\n{'='*60}")
    print(f"STEP: {label}")
    print(f"  generated : {generated}")
    print(f"  committed : {committed}")
    print(f"{'='*60}")

    # Use diff -r (stdlib-free, available everywhere) for a portable comparison.
    result = subprocess.run(
        ["diff", "-r", "--unified=3", str(committed), str(generated)],
        capture_output=False,
    )
    ok = result.returncode == 0
    status = "PASS — no diff (bindings are current)" if ok else f"FAIL (exit {result.returncode}) — committed bindings are STALE; regenerate and commit"
    print(f"  --> {status}")
    return ok


def main() -> int:
    results: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Step 1 — Cross-compile staticlib: aarch64-apple-ios
    # Command verbatim from bindings/README.md § "Cross-compile the static
    # library for mobile".
    # ------------------------------------------------------------------
    results["cross_ios"] = _run(
        "Cross-compile staticlib: aarch64-apple-ios",
        [
            "cargo", "rustc", "--release", "--lib",
            "--target", "aarch64-apple-ios",
            "--crate-type", "staticlib",
            "--features", "uniffi",
        ],
        cwd=REFRAIN_CORE,
    )

    # ------------------------------------------------------------------
    # Step 2 — Cross-compile staticlib: aarch64-linux-android
    # Command verbatim from bindings/README.md.
    # ------------------------------------------------------------------
    results["cross_android"] = _run(
        "Cross-compile staticlib: aarch64-linux-android",
        [
            "cargo", "rustc", "--release", "--lib",
            "--target", "aarch64-linux-android",
            "--crate-type", "staticlib",
            "--features", "uniffi",
        ],
        cwd=REFRAIN_CORE,
    )

    # ------------------------------------------------------------------
    # Step 3a — Build host cdylib (needed by uniffi-bindgen --library mode).
    # Command verbatim from bindings/README.md § "Regenerate the bindings".
    # ------------------------------------------------------------------
    results["host_cdylib"] = _run(
        "Build host cdylib (uniffi-bindgen --library mode input)",
        ["cargo", "build", "--release", "--features", "uniffi"],
        cwd=REFRAIN_CORE,
    )

    # Bail early on host build failure — bindgen steps depend on it.
    if not results["host_cdylib"]:
        print("\nHost cdylib build failed — skipping bindgen diff steps.")
        _print_summary(results)
        return 1

    # ------------------------------------------------------------------
    # Step 3b + 3c — Regenerate Swift + Kotlin into a temp dir, then diff
    # against committed bindings.
    # Commands verbatim from bindings/README.md § "Regenerate the bindings".
    # On Linux the library extension is .so; on macOS it is .dylib.
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="refrain_bindings_check_") as tmpdir:
        tmp_swift = Path(tmpdir) / "swift"
        tmp_kotlin = Path(tmpdir) / "kotlin"
        tmp_swift.mkdir()
        tmp_kotlin.mkdir()

        results["bindgen_swift"] = _run(
            "Regenerate Swift bindings into temp dir",
            [
                "cargo", "run", "--release", "--features", "uniffi",
                "--bin", "uniffi-bindgen", "--",
                "generate", "--library", str(HOST_LIB_PATH),
                "--language", "swift",
                "--out-dir", str(tmp_swift),
            ],
            cwd=REFRAIN_CORE,
        )

        results["bindgen_kotlin"] = _run(
            "Regenerate Kotlin bindings into temp dir (--no-format to avoid ktlint dep)",
            [
                "cargo", "run", "--release", "--features", "uniffi",
                "--bin", "uniffi-bindgen", "--",
                "generate", "--library", str(HOST_LIB_PATH),
                "--language", "kotlin",
                "--no-format",
                "--out-dir", str(tmp_kotlin),
            ],
            cwd=REFRAIN_CORE,
        )

        # Only diff if generation succeeded.
        if results["bindgen_swift"]:
            results["diff_swift"] = _diff_dirs(
                "Diff: temp Swift vs committed bindings/swift",
                tmp_swift,
                BINDINGS_SWIFT_COMMITTED,
            )
        else:
            results["diff_swift"] = False

        if results["bindgen_kotlin"]:
            results["diff_kotlin"] = _diff_dirs(
                "Diff: temp Kotlin vs committed bindings/kotlin",
                tmp_kotlin,
                BINDINGS_KOTLIN_COMMITTED,
            )
        else:
            results["diff_kotlin"] = False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    return _print_summary(results)


def _print_summary(results: dict[str, bool]) -> int:
    print(f"\n{'='*60}")
    print("BINDINGS DRIFT GATE — SUMMARY")
    print(f"{'='*60}")
    all_ok = True
    for name, ok in results.items():
        mark = "PASS" if ok else "FAIL"
        print(f"  {mark}  {name}")
        if not ok:
            all_ok = False

    print(f"{'='*60}")
    if all_ok:
        print("RESULT: PASS — mobile static libs cross-compile; bindings are current.")
    else:
        print("RESULT: FAIL — see step output above for details.")
        print("  If bindings are stale, run the regen commands from bindings/README.md")
        print("  and commit the updated files.")
    print(f"{'='*60}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
