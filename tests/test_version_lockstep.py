# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""refrain and refrain-core are versioned in lockstep.

Both packages are built from the same commit by release.yml on every ``v*``
tag and are Rust<->Python equivalence-gated in CI, so one shared version
number names that guarantee (and kills the compatibility matrix a consumer
would otherwise have to track).  This guard fails the build if the two
pyproject.toml versions ever drift.  See CONTRIBUTING.md, "Cutting a
release": bump BOTH files together in the release PR.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _project_version(pyproject: Path) -> str:
    text = pyproject.read_text(encoding="utf-8")
    project = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[|\Z)", text)
    assert project, f"no [project] table in {pyproject}"
    version = re.search(r'(?m)^version\s*=\s*"([^"]+)"', project.group(1))
    assert version, f"no version key in the [project] table of {pyproject}"
    return version.group(1)


def test_refrain_and_refrain_core_versions_match():
    root = _project_version(REPO_ROOT / "pyproject.toml")
    core = _project_version(REPO_ROOT / "refrain-core" / "pyproject.toml")
    assert root == core, (
        f"refrain is {root} but refrain-core is {core}. The two packages "
        "are released in lockstep: bump BOTH pyproject.toml versions "
        "together in the release PR (CONTRIBUTING.md, 'Cutting a release')."
    )
