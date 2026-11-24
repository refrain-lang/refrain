# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""refrain and refrain-core are versioned in lockstep.

Both packages are built from the same commit by release.yml on every ``v*``
tag and are Rust<->Python equivalence-gated in CI, so one shared version
number names that guarantee (and kills the compatibility matrix a consumer
would otherwise have to track).  This guard fails the build if the two
pyproject.toml versions ever drift.  See CONTRIBUTING.md, "Cutting a
release": bump BOTH files together in the release PR.

The IR-JSON wire version is a second, independent lockstep: the Python
emitter decides which version a document claims, `refrain-core` decides which
it accepts, and a published JSON Schema has to exist for it. Those three live
in two languages and three directories, and v0.7 shipped with `cargo test`
red because only one of them moved. The guards at the bottom of this file
relate them from a plain `pytest`.
"""

import re
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# The IR-JSON wire version: emitter <-> Rust core <-> published schema
# ---------------------------------------------------------------------------

# The newest IR-JSON version the Python emitter can produce.
#
# Bumping a version means editing FOUR things in the same commit:
#   1. this pin
#   2. `_protocol_ir_version` in `src/refrain/ir_json.py`
#   3. `SUPPORTED_IR_VERSIONS` in `refrain-core/src/ir.rs`
#   4. the ratchet in `refrain-core/tests/ir_deser.rs` — BOTH of its lines:
#      the pin on the newest supported tag, AND the newer tag it uses to prove
#      a refusal. A refusal test aimed at a tag the build now accepts proves
#      nothing, and that is exactly how (4) was missed for v0.7: the whole
#      Python suite passed while `cargo test` sat at exit 101, because the only
#      alarm lived in a gate that is not part of a `pytest` run.
NEWEST_IR_VERSION = "0.8"

_SCHEMA_RE = re.compile(r"ir-json-v(\d+\.\d+)\.schema\.json\Z")


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.split("."))


def _schema_versions() -> list[str]:
    """Every IR-JSON version with a published JSON Schema, oldest first."""
    schema_dir = REPO_ROOT / "src" / "refrain" / "schema"
    tags = [
        m.group(1)
        for m in (_SCHEMA_RE.match(path.name) for path in schema_dir.iterdir())
        if m is not None
    ]
    return sorted(tags, key=_version_key)


def test_the_newest_published_ir_schema_matches_the_pin():
    """A new schema without moving the pin fails here, not three steps later."""
    versions = _schema_versions()
    assert versions, "no ir-json-v*.schema.json files found"
    assert versions[-1] == NEWEST_IR_VERSION, (
        f"the newest published schema is v{versions[-1]} but NEWEST_IR_VERSION "
        f"says {NEWEST_IR_VERSION}. See that constant's comment: four places "
        f"move together, and the Rust ratchet needs BOTH of its lines moved."
    )


def test_the_rust_core_accepts_every_ir_version_python_can_emit():
    """Cross-language drift in the wire version, catchable from a plain pytest.

    Reads the Rust constant as text on purpose: importing the compiled core
    would make this test depend on a built wheel, and the point is for it to
    fail in a bare checkout.
    """
    ir_rs = REPO_ROOT / "refrain-core" / "src" / "ir.rs"
    if not ir_rs.is_file():
        pytest.skip("refrain-core source not present (installed-wheel checkout)")
    block = re.search(
        r"pub const SUPPORTED_IR_VERSIONS:\s*&\[&str\]\s*=\s*&\[(.*?)\];",
        ir_rs.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert block is not None, (
        "could not find SUPPORTED_IR_VERSIONS in refrain-core/src/ir.rs — if it "
        "was renamed, update this guard rather than deleting it"
    )
    rust = sorted(re.findall(r'"(\d+\.\d+)"', block.group(1)), key=_version_key)
    schemas = _schema_versions()
    assert rust == schemas, (
        f"refrain-core accepts {rust} but the published schemas are {schemas}. "
        f"A version Python can emit that Rust refuses fails at session start; "
        f"a version Rust accepts with no schema skips validation silently."
    )
    assert rust[-1] == NEWEST_IR_VERSION
