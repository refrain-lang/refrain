import pytest

jsonschema = pytest.importorskip("jsonschema")
from refrain.editor import validate_model

_GOOD = {
    "name": "p", "meta": {"description": "d", "status": "draft", "goals": []},
    "requires": {"sample_rate": ">= 256 Hz", "channels": ["C4"]},
    "inputs": [], "derives": [], "thresholds": [], "inhibits": [],
    "reward": None, "outputs": [], "controls": [], "session": {"phases": []},
}


def test_good_model_validates():
    validate_model(_GOOD)  # must not raise


def test_missing_name_fails():
    bad = {k: v for k, v in _GOOD.items() if k != "name"}
    with pytest.raises(jsonschema.ValidationError):
        validate_model(bad)


def test_control_binding_shape_validates():
    m = dict(_GOOD)
    m["derives"] = [{"name": "env", "block": "derive.envelope", "from": "raw",
                     "slots": {"center": {"bind": "env_center"}, "ratio": 1.25, "smooth_tau_ms": 250}}]
    validate_model(m)
