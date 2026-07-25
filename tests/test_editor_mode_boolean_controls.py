"""General mode-control and boolean-control rendering (Task A/B on top of
6293868's mode-conditional threshold work).

Before this, `_build_model` never accepted a `modes` argument at all, so a
mode control NOT referenced by a conditional threshold — a `feedback_style`
selector here, alongside `threshold_style` which the conditional threshold
does reference — was silently dropped on render. And `boolean` had no
`_CONTROL_BODY` template, so any protocol using one (e.g. an on/off
artifact-guard control) fell out of subset outright.

This fixture exercises both in one protocol, which is also the proof that a
mode referenced by a conditional threshold and an unrelated mode control are
each rendered exactly once — not zero times (dropped) and not twice
(duplicated, which would desync `topological_order` and fail the round trip).
"""
import re

import refrain
from refrain.resolver import resolve
from refrain.ir_json import ir_to_json_obj

from refrain.editor import describe_protocol, render_protocol


def _declared_once(rendered: str, name: str, kind: str) -> bool:
    """True iff `name` appears as a `<name> = <kind> {` *declaration* exactly
    once in `rendered`. Matched on the declaration form (start-of-line name,
    `=`, the kind keyword) rather than a bare substring count: a mode's name
    also appears inside a conditional threshold's condition (`threshold_style
    == "baseline"`), so counting occurrences of e.g. `"threshold_style"` alone
    over-counts and would mask a real double-emission bug behind a passing
    assertion for the wrong reason."""
    pattern = rf"^\s*{re.escape(name)}\s*=\s*{re.escape(kind)}\b"
    return len(re.findall(pattern, rendered, re.MULTILINE)) == 1

MODES_AND_BOOLEAN = '''
protocol "smr_up_c4_styled_ext" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["C4"] }
  input "raw" { montage = referential(active: "C4", reference: "linked_ears") }
  derive "smr_envelope" { from = "raw"
    pipeline = [ bandpass(center: env_center, bandwidth: ratio(1.25), order: 4),
                 hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "smr_t" {
    signal       = "smr_envelope"
    type         = threshold_style == "baseline"
                     ? absolute(value: smr_threshold_uv)
                     : percentile(target_pct: smr_reward_pct, window: 2 min)
    live_tunable = true
  }
  reward { event = dwell(condition: above("smr_envelope", "smr_t"), duration: 250 ms)
           continuous = sigmoid("smr_envelope" / "smr_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
  controls {
    env_center = frequency { default = 13.4164 Hz; range = (10.73 Hz, 16.1 Hz); label = "SMR band center" }
    threshold_style = mode { choices = ["baseline", "adaptive"]; default = "adaptive"; label = "Threshold style" }
    smr_threshold_uv = voltage { default = 5 uV; range = (1 uV, 20 uV); label = "Threshold"; live_tunable = true }
    smr_reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %"; live_tunable = true }
    feedback_style = mode { choices = ["chime", "continuous"]; default = "chime"; label = "Feedback style" }
    artifact_guard = boolean { default = true; label = "Artifact guard"; live_tunable = true }
  }
}
'''


def _ir(src):
    return ir_to_json_obj(resolve(refrain.parse(src)))


def test_modes_and_boolean_are_in_subset():
    d = describe_protocol(MODES_AND_BOOLEAN)
    assert d["ok"] is True
    assert d["in_subset"] is True, "unrelated mode + boolean control must be admitted"
    assert d["model"] is not None


def test_model_controls_include_both_modes_and_the_boolean_exactly_once():
    d = describe_protocol(MODES_AND_BOOLEAN)
    names = [c["name"] for c in d["model"]["controls"]]
    assert names.count("threshold_style") == 1   # conditional-referenced mode
    assert names.count("feedback_style") == 1     # unrelated mode
    assert names.count("artifact_guard") == 1     # boolean
    # Source order is preserved: threshold_style sits between env_center and
    # smr_threshold_uv (its conditional's insert position), feedback_style and
    # artifact_guard trail after smr_reward_pct, in declaration order.
    assert names == ["env_center", "threshold_style", "smr_threshold_uv",
                      "smr_reward_pct", "feedback_style", "artifact_guard"]


def test_boolean_default_is_a_real_bool_not_an_int():
    d = describe_protocol(MODES_AND_BOOLEAN)
    guard = next(c for c in d["model"]["controls"] if c["name"] == "artifact_guard")
    assert guard["kind"] == "boolean"
    assert guard["default"] is True
    assert type(guard["default"]) is bool  # not the int 1 that a naive numeric coercion would give
    # top-level desc["controls"]/desc["modes"] split is unaffected by the model change
    assert "artifact_guard" in {c["name"] for c in d["controls"]}
    assert "threshold_style" not in {c["name"] for c in d["controls"]}
    assert "feedback_style" not in {c["name"] for c in d["controls"]}
    assert {"threshold_style", "feedback_style"} == {m["name"] for m in d["modes"]}


def test_modes_and_boolean_round_trip_exactly():
    d = describe_protocol(MODES_AND_BOOLEAN)
    assert d["in_subset"] is True
    rendered = render_protocol(d["model"])
    assert _ir(MODES_AND_BOOLEAN) == _ir(rendered)
    # The regression guard for double emission: each control is declared
    # exactly once, matched on declaration form (not a bare substring — see
    # `_declared_once`'s docstring for why that would be a false negative
    # test here).
    assert _declared_once(rendered, "threshold_style", "mode")
    assert _declared_once(rendered, "feedback_style", "mode")
    assert _declared_once(rendered, "artifact_guard", "boolean")
    assert "true" in rendered  # boolean default rendered as `true`, not `1`


def test_boolean_renders_true_false_not_1_0():
    src = MODES_AND_BOOLEAN.replace(
        'artifact_guard = boolean { default = true; label = "Artifact guard"; live_tunable = true }',
        'artifact_guard = boolean { default = false; label = "Artifact guard"; live_tunable = true }',
    )
    d = describe_protocol(src)
    assert d["in_subset"] is True
    rendered = render_protocol(d["model"])
    assert "default = false" in rendered
    assert "default = 0" not in rendered
    assert _ir(src) == _ir(rendered)
