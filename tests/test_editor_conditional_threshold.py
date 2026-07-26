"""Mode-conditional threshold (`type = <mode> == "<choice>" ? absolute(...) :
percentile(...)`) — the adaptive/baseline collapse the protocol library
restructured around. Before this, `_match_threshold` only accepted a bare
`percentile(...)`/`absolute(...)` Call and raised `_NotInSubset` on the
ternary (`Conditional`), which is the single largest cause of protocols
falling out of the editor's catalog-v1 subset.
"""
import refrain
from refrain.resolver import resolve
from refrain.ir_json import ir_to_json_obj

from refrain.editor import describe_protocol, render_protocol

CONDITIONAL = '''
protocol "smr_up_c4_styled" {
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
  }
}
'''


def _ir(src):
    return ir_to_json_obj(resolve(refrain.parse(src)))


def test_conditional_threshold_is_in_subset():
    d = describe_protocol(CONDITIONAL)
    assert d["ok"] is True
    assert d["in_subset"] is True, "ternary threshold must be admitted to the editor subset"
    assert d["model"] is not None


def test_conditional_threshold_node_shape():
    d = describe_protocol(CONDITIONAL)
    thr = next(n for n in d["model"]["thresholds"] if n["name"] == "smr_t")
    assert thr["block"] == "threshold.conditional"
    assert thr["signal"] == "smr_envelope"
    assert thr["mode_control"] == "threshold_style"
    assert thr["equals"] == "baseline"
    assert thr["live_tunable"] is True
    assert thr["when_true"] == {"block": "threshold.absolute",
                                 "slots": {"value": {"bind": "smr_threshold_uv"}}}
    assert thr["when_false"] == {"block": "threshold.percentile",
                                  "slots": {"target_pct": {"bind": "smr_reward_pct"},
                                            "window_ms": 120000}}


def test_conditional_threshold_round_trips():
    d = describe_protocol(CONDITIONAL)
    assert d["in_subset"] is True
    assert _ir(CONDITIONAL) == _ir(render_protocol(d["model"]))


def test_conditional_threshold_not_equality_stays_out_of_subset():
    # `!=` still folds fine at resolve time (the resolver handles both == and
    # !=), but it is not the `<mode-control> == "<choice>"` shape the model
    # represents — must not be admitted rather than silently reinterpreted.
    src = CONDITIONAL.replace('threshold_style == "baseline"', 'threshold_style != "baseline"')
    d = describe_protocol(src)
    assert d["ok"] is True  # resolves fine; still not in our narrow subset
    assert d["in_subset"] is False


def test_conditional_threshold_unsupported_branch_stays_out_of_subset():
    # A branch that isn't itself a plain percentile/absolute call (e.g. a bare
    # numeric literal) must not be admitted — no lossy fallback representation.
    src = CONDITIONAL.replace(
        "? absolute(value: smr_threshold_uv)",
        "? 5",
    )
    d = describe_protocol(src)
    assert d["in_subset"] is False
