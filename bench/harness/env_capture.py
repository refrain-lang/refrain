"""Captures the host environment (versions, CPU, git SHA) for a bench run.

Every field is either a string or None; never raises — a missing tool
(e.g. git not on PATH) yields None rather than an error.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path


def _safe_run(*cmd: str) -> str | None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return None


def _module_version(name: str) -> str | None:
    try:
        mod = __import__(name)
        return getattr(mod, "__version__", None)
    except ImportError:
        return None


def _cpu_model() -> str | None:
    if sys.platform == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            return None
    if sys.platform == "darwin":
        return _safe_run("sysctl", "-n", "machdep.cpu.brand_string")
    return platform.processor() or None


def _cpu_governor() -> str | None:
    if sys.platform == "linux":
        try:
            return Path(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
            ).read_text().strip()
        except OSError:
            return None
    return None


def _cpu_freq_khz() -> str | None:
    if sys.platform == "linux":
        try:
            return Path(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq"
            ).read_text().strip()
        except OSError:
            return None
    return None


def _cpu_count() -> str | None:
    count = os.cpu_count()
    return str(count) if count is not None else None


def capture_env() -> dict[str, str | None]:
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu": _cpu_model(),
        "cpu_governor": _cpu_governor(),
        "cpu_max_freq_khz": _cpu_freq_khz(),
        "cpu_count_logical": _cpu_count(),
        "numpy_version": _module_version("numpy"),
        "scipy_version": _module_version("scipy"),
        "refrain_version": _module_version("refrain"),
        "git_sha": _safe_run("git", "rev-parse", "HEAD"),
        "git_dirty": "true" if _safe_run("git", "status", "--porcelain") else "false",
    }
