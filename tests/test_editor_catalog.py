import json

import pytest
from refrain.editor import load_catalog
from refrain.editor.catalog import _CATALOG_PATH, render_slot


def test_catalog_loads_and_indexes_by_id():
    cat = load_catalog()
    # "3" since refrain 0.25.0, which added optional slots. The bump is the
    # only signal a vendored consumer gets that the catalog now holds blocks a
    # renderer without optional-slot support cannot fully render.
    assert cat.version == "3"
    env = cat.block("derive.envelope")
    assert env["kind"] == "derive"
    assert [s["name"] for s in env["slots"]] == ["center", "ratio", "smooth_tau_ms"]


def test_unknown_block_raises():
    with pytest.raises(KeyError):
        load_catalog().block("derive.nope")


def test_catalog_has_operant_family():
    cat = load_catalog()
    for bid in ("montage.referential", "threshold.percentile", "reward.operant"):
        assert cat.has(bid), bid


def test_render_slot_formats_by_type():
    assert render_slot({"bind": "env_center"}, "frequency") == "env_center"
    assert render_slot(13.4164, "frequency") == "13.4164 Hz"
    assert render_slot(1.25, "number") == "1.25"
    assert render_slot(250, "duration_ms") == "250"
    assert render_slot("C4", "site") == '"C4"'
    assert render_slot("env", "ref") == "env"
    assert render_slot("above", "enum") == "above"
    assert render_slot("(8 Hz, 12 Hz)", "raw") == "(8 Hz, 12 Hz)"


# --- optional slots ------------------------------------------------------
# A block's optional slot carries its own clause rather than a placeholder in
# the block's `template`. The two tests below pin the halves of that: the
# clause must be splice-able, and the template must stay free of it.

def _blocks():
    return json.loads(_CATALOG_PATH.read_text())["blocks"]


def test_no_optional_slot_appears_as_a_placeholder_in_its_block_template():
    """The invariant that keeps a renderer with no optional-slot support
    correct. refrain-editor's vendored TypeScript `fill` substitutes every
    `{name}` it finds in `template` and renders an absent raw slot as the
    literal string "undefined"; its parity suite re-renders 41 vendored models
    byte-exact and every one of them has a `reward.*` block. So an optional
    slot's placeholder belongs in its own `clause`, never in `template` — put
    it in the template and all 41 go red in a repository this one does not
    touch."""
    for b in _blocks():
        for s in b.get("optional_slots", []):
            assert "{%s}" % s["name"] not in b["template"], (b["id"], s["name"])


def test_every_optional_slot_can_be_spliced_into_its_block_template():
    for b in _blocks():
        for s in b.get("optional_slots", []):
            assert b["template"].count(s["after"]) == 1, (b["id"], s["name"])
            assert "{%s}" % s["name"] in s["clause"], (b["id"], s["name"])
            assert s["type"] in ("raw", "ref", "enum"), (b["id"], s["type"])
