import pytest
from refrain.editor import load_catalog


def test_catalog_loads_and_indexes_by_id():
    cat = load_catalog()
    assert cat.version == "1"
    env = cat.block("derive.envelope")
    assert env["kind"] == "derive"
    assert [s["name"] for s in env["slots"]] == ["center", "ratio", "smooth_tau_ms"]


def test_unknown_block_raises():
    with pytest.raises(KeyError):
        load_catalog().block("derive.nope")
