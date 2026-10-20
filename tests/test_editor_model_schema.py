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


def test_mode_and_boolean_control_shapes_validate():
    m = dict(_GOOD)
    m["controls"] = [
        {"name": "threshold_style", "kind": "mode",
         "choices": ["baseline", "adaptive"], "default": "adaptive",
         "label": "Threshold style", "final": False},
        {"name": "artifact_guard", "kind": "boolean", "default": True,
         "label": "Artifact guard", "live_tunable": True},
    ]
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


def test_carried_through_expression_slot_validates():
    """`{"expr": "<source text>"}` is a slot the editor preserves rather than
    models — a mode-gated conditional, or arithmetic over controls.

    #109 introduced the shape and made the four flagship EEG protocols
    describable, but did not widen `$defs.slotValue`, and nothing validated a
    flagship model. The result shipped: every one of the four describes to a
    model that `validate_model` rejects, so a host that validates before
    rendering refuses exactly the protocols #109 existed to admit.
    """
    m = dict(_GOOD)
    m["inhibits"] = [{
        "name": "artifact", "block": "inhibit.bandpower_relative", "from": "raw",
        "slots": {
            "band": {"expr": "(hbeta_lo_hz, hbeta_hi_hz)"},
            "target_pct": {"expr": 'artifact_guard == "on" ? artifact_strictness : 100'},
        },
    }]
    validate_model(m)


def test_an_unknown_object_slot_shape_is_still_rejected():
    """The companion: widening for `expr` must not turn the slot into `any`.
    `additionalProperties: false` on each branch is what keeps a typo from
    validating."""
    m = dict(_GOOD)
    m["inhibits"] = [{
        "name": "artifact", "block": "inhibit.bandpower_relative", "from": "raw",
        "slots": {"target_pct": {"expresion": "typo"}},
    }]
    with pytest.raises(jsonschema.ValidationError):
        validate_model(m)
    m["inhibits"][0]["slots"] = {"target_pct": {"expr": "ok", "bind": "also"}}
    with pytest.raises(jsonschema.ValidationError):
        validate_model(m)


def test_every_flagship_protocol_describes_to_a_valid_model():
    """The gap that let the above ship: no test validated a model produced by
    `describe_protocol` from a real protocol, only hand-written ones.

    Inline rather than read from the corpus, because the protocol library is a
    separate repository and is not available here. This is the artifact-inhibit
    shape all four flagships share, which is the one that was rejected.
    """
    from refrain.editor import describe_protocol

    src = '''
protocol "flagship_shape" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ band_envelope(band: (12 Hz, 15 Hz), latency: 360 ms) ] }
  threshold "env_t" { signal = "env"; type = percentile(target_pct: 70, window: 2 min) }
  inhibit "emg" {
    metric    = bandpower(input: "raw", band: (hb_lo_hz, hb_hi_hz), window: 100 ms)
    threshold = percentile(
      target_pct: guard == "on" ? strictness : 100,
      window: 2 min
    )
    action    = mute(release: 200 ms)
  }
  reward { event = dwell(condition: above("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
  controls {
    hb_lo_hz   = frequency { default = 20 Hz; range = (18 Hz, 24 Hz); label = "HB low" }
    hb_hi_hz   = frequency { default = 30 Hz; range = (26 Hz, 34 Hz); label = "HB high" }
    strictness = number { default = 95; range = (80, 99); label = "Strictness" }
    guard      = mode { choices = ["on", "off"]; default = "on"; label = "Guard" }
  }
}
'''
    d = describe_protocol(src)
    assert d["in_subset"] is True, d.get("reason")
    # The shape that was rejected must actually be present, or this passes
    # vacuously the next time a matcher stops producing it. Walked rather than
    # read from a known key: in this subset the artifact inhibit is modelled
    # as a reward component, not under `inhibits`, and hard-coding the key is
    # how the assertion would quietly stop checking anything.
    def _exprs(node):
        if isinstance(node, dict):
            if "expr" in node and len(node) == 1:
                yield node
            for v in node.values():
                yield from _exprs(v)
        elif isinstance(node, list):
            for v in node:
                yield from _exprs(v)

    found = list(_exprs(d["model"]))
    assert found, "no carried-through expression in the model — test is vacuous"
    validate_model(d["model"])
