import pytest
from refrain.editor import load_catalog
from refrain.editor.catalog import render_slot


def test_catalog_loads_and_indexes_by_id():
    cat = load_catalog()
    assert cat.version == "1"
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
