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


def test_seed_statistic_must_be_percentile():
    m = dict(_GOOD)
    m["controls"] = [{"name": "thr_uv", "kind": "voltage", "default": 2.0, "unit": "uV",
                      "range": [0.5, 30.0], "label": "Threshold", "live_tunable": True,
                      "seed": {"statistic": "percentile", "from": "env",
                               "window_ms": 60000, "target_pct": 40}}]
    validate_model(m)  # the only engine-supported statistic
    m["controls"][0]["seed"] = dict(m["controls"][0]["seed"], statistic="mean")
    with pytest.raises(jsonschema.ValidationError):
        validate_model(m)


def test_conditional_threshold_shape_validates():
    m = dict(_GOOD)
    m["thresholds"] = [{
        "name": "smr_t", "block": "threshold.conditional", "signal": "smr_envelope",
        "mode_control": "threshold_style", "equals": "baseline",
        "mode_decl": {"choices": ["baseline", "adaptive"], "default": "adaptive",
                      "label": "Threshold style"},
        "when_true": {"block": "threshold.absolute", "slots": {"value": {"bind": "smr_threshold_uv"}}},
        "when_false": {"block": "threshold.percentile",
                        "slots": {"target_pct": {"bind": "smr_reward_pct"}, "window_ms": 120000}},
        "live_tunable": True,
    }]
    validate_model(m)


def test_conditional_threshold_missing_branch_fails():
    m = dict(_GOOD)
    m["thresholds"] = [{
        "name": "smr_t", "block": "threshold.conditional", "signal": "smr_envelope",
        "mode_control": "threshold_style", "equals": "baseline",
        "when_true": {"block": "threshold.absolute", "slots": {"value": {"bind": "smr_threshold_uv"}}},
        # when_false omitted — a conditional threshold with only one branch is malformed
    }]
    with pytest.raises(jsonschema.ValidationError):
        validate_model(m)
