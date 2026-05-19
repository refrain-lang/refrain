"""Smoke test for the `python -m bench equivalence` CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _env_with_pythonpath() -> dict[str, str]:
    repo = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ)
    src = str(repo / "src")
    root = str(repo)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, root, existing) if p)
    return env


def test_equivalence_cli_smoke():
    repo = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "bench", "equivalence", "--only", "micro_01_passthrough"],
        capture_output=True, text=True, timeout=180,
        env=_env_with_pythonpath(), cwd=str(repo),
    )
    assert proc.returncode == 0, f"CLI failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "PASS" in proc.stdout
    assert "micro_01_passthrough" in proc.stdout


def test_equivalence_cli_help():
    repo = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.run(
        [sys.executable, "-m", "bench", "--help"],
        capture_output=True, text=True, timeout=30,
        env=_env_with_pythonpath(), cwd=str(repo),
    )
    assert proc.returncode == 0
    assert "equivalence" in proc.stdout
