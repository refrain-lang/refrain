"""Tooling: pytest discovers bench tests; bench meets the project lint bar (F,E9)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def test_pytest_discovers_bench_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/bench/"],
        capture_output=True, text=True, timeout=120, cwd=str(REPO),
    )
    assert proc.returncode == 0, f"collect failed:\n{proc.stdout}\n{proc.stderr}"
    assert "test_equivalence" in proc.stdout
    assert "test_runner" in proc.stdout


def test_ruff_real_errors_clean():
    """Match the project's CI lint bar: ruff --select F,E9 (real errors only)."""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "bench", "tests/bench", "--select", "F,E9"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO),
    )
    assert proc.returncode == 0, f"ruff F,E9:\n{proc.stdout}\n{proc.stderr}"
