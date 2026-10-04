# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Mechanism M6 from the Coherence Recorder's testing brief: `docs/PRIMITIVES.md`
and the primitive registry must agree, in both directions.

> "Parse the primitive headings out of `PRIMITIVES.md`, diff against the
> registry, fail on divergence in either direction. Entries legitimately not yet
> implemented carry an explicit marker... It misled two drafts of our own
> feature request, and it would mislead any protocol author reading the
> reference."

Two parsing details that a naive 30-line version gets wrong, both found by
writing it:

1. A heading may name several primitives at once — ``### `above`, `below`,
   `inside` `` — so matching one name per heading reports `below`, `inside`,
   `any_of`, `freeze` and `flag` as undocumented when they are not.
2. The "not implemented" marker is sometimes on the parent `##` section rather
   than on the entry: `pct_in_range` and `weighted_sum` sit under
   ``## Vector reductions *(sketches; ...)*`` with bare headings of their own.

And one scoping detail: ``## External providers`` documents `norms` and
`client`, which are runtime-supplied *namespaces* (`norms.power_db.lookup`,
`client.age`), not callable primitives, so that section is excluded.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from refrain.primitives import names

PRIMITIVES_MD = pathlib.Path(__file__).resolve().parents[1] / "docs" / "PRIMITIVES.md"

# A heading or section carrying any of these is declaring itself aspirational.
ABSENT_MARKER = re.compile(r"planned|stub|sketch|not yet|not implemented", re.IGNORECASE)

# `##` sections that do not document callable primitives.
NON_PRIMITIVE_SECTIONS = frozenset({
    "External providers",   # `norms.*` / `client.*` namespaces
})

_HEADING_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


def parse_documented() -> dict[str, tuple[str, str]]:
    """`name -> (own heading suffix, enclosing section heading)` for every
    primitive given a `###` heading in PRIMITIVES.md."""
    out: dict[str, tuple[str, str]] = {}
    section = ""
    for line in PRIMITIVES_MD.read_text().splitlines():
        if line.startswith("### "):
            rest = line[4:]
            if section.split(" *")[0].strip() in NON_PRIMITIVE_SECTIONS:
                continue
            suffix = _HEADING_NAME.sub("", rest).replace(",", "").strip()
            for name in _HEADING_NAME.findall(rest):
                out[name] = (suffix, section)
        elif line.startswith("## "):
            section = line[3:].strip()
    return out


def is_marked_absent(own: str, section: str) -> bool:
    return bool(ABSENT_MARKER.search(own) or ABSENT_MARKER.search(section))


def test_the_parser_finds_a_plausible_number_of_headings():
    """Guard against the diff going vacuous. A regex that silently matches
    nothing would make both assertions below pass for the wrong reason — which
    is the trap the reporter hit with their own harness, where 14 'failures'
    were all the rig."""
    documented = parse_documented()
    assert len(documented) >= 25, f"only parsed {len(documented)} headings"
    # Spot-check all three heading shapes the file actually uses.
    assert "smooth" in documented                      # plain
    assert "below" in documented and "inside" in documented   # combined
    assert "dead_zone" in documented                   # with a marker suffix


def test_every_documented_primitive_exists_or_is_marked_absent():
    documented = parse_documented()
    registry = set(names())
    unmarked_and_missing = {
        name: (own, section)
        for name, (own, section) in documented.items()
        if name not in registry and not is_marked_absent(own, section)
    }
    assert not unmarked_and_missing, (
        "PRIMITIVES.md documents these as ordinary primitives, but they are "
        "not in the registry and fail with 'unknown primitive'. Either "
        "implement them or mark the heading (as `dead_zone` and "
        f"`source_project` do): {unmarked_and_missing}"
    )


def test_every_registered_primitive_is_documented():
    documented = parse_documented()
    undocumented = sorted(set(names()) - set(documented))
    assert not undocumented, (
        "these primitives resolve but have no `###` heading in "
        f"docs/PRIMITIVES.md, so the reference is silent on them: {undocumented}"
    )


@pytest.mark.parametrize("name", ["align_to", "select_channel", "decimate"])
def test_the_three_primitives_the_report_named_are_marked(name):
    """D8 specifically. Kept as its own case so a regression names the
    primitive rather than printing a set."""
    documented = parse_documented()
    assert name in documented, f"{name} lost its heading"
    assert name not in set(names()), f"{name} is now implemented; drop this test"
    own, section = documented[name]
    assert is_marked_absent(own, section), f"{name} heading is bare: {own!r}"


def test_markers_on_a_parent_section_count():
    """`pct_in_range` and `weighted_sum` have bare headings under a section
    marked '*(sketches; ...)*'. The report describes `pct_in_range` as
    'correctly marked' without that distinction; a heading-only parser would
    report it as a D8-class divergence."""
    documented = parse_documented()
    for name in ("pct_in_range", "weighted_sum"):
        own, section = documented[name]
        assert not ABSENT_MARKER.search(own), f"{name} now has its own marker"
        assert ABSENT_MARKER.search(section), f"{name}'s section lost its marker"


def test_namespaces_are_not_treated_as_primitives():
    """`norms` and `client` are runtime-supplied namespaces, not callables. If
    the exclusion ever breaks they would show up as documented-but-absent."""
    documented = parse_documented()
    assert "norms" not in documented
    assert "client" not in documented
