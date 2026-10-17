"""Round-trip coverage for catalog-growth shapes: weighted-composite reward and
the HRV low-Fs envelope / auto_range / passthrough / bare-ref-reward family."""
import refrain
from refrain.resolver import resolve
from refrain.ir_json import ir_to_json_obj

from refrain.editor import describe_protocol, render_protocol

COMPOSITE = '''
protocol "composite_smr_theta_cz" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["adhd_attention"];
         reward_style = "weighted" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "smr_envelope" { from = "raw"
    pipeline = [ bandpass(center: smr_center, bandwidth: ratio(1.25), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  derive "theta_envelope" { from = "raw"
    pipeline = [ bandpass(center: theta_center, bandwidth: ratio(2), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  reward  "smr"   { signal = sigmoid("smr_envelope",   midpoint: 6 uV, steepness: 1); weight = w_smr }
  inhibit "theta" { signal = sigmoid("theta_envelope", midpoint: 8 uV, steepness: 1); weight = w_theta }
  reward {
    combine    = "weighted"
    continuous = reward.composite
    event      = dwell(condition: above(reward.composite, 0.5), duration: 250 ms)
  }
  output { audio_gain = reward.composite; audio_chime = reward.event }
  controls {
    smr_center = frequency { default = 13.4164 Hz; range = (10.73 Hz, 16.1 Hz); label = "a" }
    theta_center = frequency { default = 5.65685 Hz; range = (4.53 Hz, 6.79 Hz); label = "b" }
    w_smr = number { default = 1.0; range = (0, 4); label = "c"; live_tunable = true }
    w_theta = number { default = 0.6; range = (0, 4); label = "d"; live_tunable = true }
  }
}
'''

HRV = '''
protocol "hrv_resonance" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["calm_anxiety"];
         modality = "hrv" }
  requires { sample_rate = ">= 4 Hz"; channels = ["tachogram"] }
  input "tach" { montage = passthrough() }
  derive "lf_env" { from = "tach"
    pipeline = [ bandpass(band: (0.04 Hz, 0.15 Hz), order: 4), rectify(), smooth(tau: 4 s) ] }
  derive "rhythm_strength" { from = "lf_env"
    pipeline = [ auto_range(window: 5 min, percentile: (1, 99)) ] }
  threshold "rs_t" { signal = "rhythm_strength"; type = percentile(target_pct: reward_pct, window: 5 min) }
  reward { event = dwell(condition: above("rhythm_strength", "rs_t"), duration: 5 s)
           continuous = "rhythm_strength" }
  output { audio_gain = reward.continuous; audio_chime = reward.event }
  controls { reward_pct = percent { default = 70; range = (50, 90); label = "r"; live_tunable = true } }
}
'''


# A tonic-level protocol: skin conductance read through passthrough() and
# trained on a smoothed level, with no band anywhere. This is the shape the
# editor needs for GSR/temperature authoring -- every other derive block is
# band-based or a formula, so without derive.level it is out of subset.
LEVEL = '''
protocol "gsr_calm" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["calm_stress"];
         modality = "gsr" }
  requires { sample_rate = ">= 5 Hz"; channels = ["gsr"] }
  input "skin" { montage = passthrough() }
  derive "level" { from = "skin"
    pipeline = [ smooth(tau: 4 s) ] }
  derive "arousal" { from = "level"
    pipeline = [ auto_range(window: 5 min, percentile: (1, 99)) ] }
  threshold "a_t" { signal = "arousal"; type = percentile(target_pct: reward_pct, window: 5 min) }
  reward { event = dwell(condition: below("arousal", "a_t"), duration: 5 s)
           continuous = "arousal" }
  output { audio_gain = reward.continuous; audio_chime = reward.event }
  controls { reward_pct = percent { default = 30; range = (10, 50); label = "r"; live_tunable = true } }
}
'''


def _ir(src):
    return ir_to_json_obj(resolve(refrain.parse(src)))


def test_weighted_composite_round_trips():
    d = describe_protocol(COMPOSITE)
    assert d["in_subset"] is True
    # the named reward/inhibit components are captured
    comps = {c["name"]: c["kind"] for c in d["model"]["reward_components"]}
    assert comps == {"smr": "reward", "theta": "inhibit"}
    assert d["model"]["reward"]["block"] == "reward.weighted"
    assert _ir(COMPOSITE) == _ir(render_protocol(d["model"]))


def test_hrv_round_trips():
    d = describe_protocol(HRV)
    assert d["in_subset"] is True
    blocks = [n["block"] for n in d["model"]["derives"]]
    assert blocks == ["derive.lf_envelope", "derive.auto_range"]
    assert d["model"]["inputs"][0]["block"] == "montage.passthrough"
    assert d["model"]["reward"]["block"] == "reward.passthrough"
    assert _ir(HRV) == _ir(render_protocol(d["model"]))


def test_level_round_trips():
    d = describe_protocol(LEVEL)
    assert d["in_subset"] is True
    blocks = [n["block"] for n in d["model"]["derives"]]
    assert blocks == ["derive.level", "derive.auto_range"]
    assert d["model"]["inputs"][0]["block"] == "montage.passthrough"
    assert _ir(LEVEL) == _ir(render_protocol(d["model"]))


# Thermal biofeedback's shape: a tonic level, then the RISE of that level above
# a baseline the session seeds and freezes. The second stage subtracts a
# CONTROL, not another stream -- `derive.difference` quotes both operands and
# so cannot express it. Modelled on the recorder's shipped
# skin_temp_warming.refrain, minimally.
RISE = '''
protocol "skin_temp_rise" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["calm_stress"];
         modality = "temp" }
  requires { sample_rate = ">= 7 Hz"; channels = ["TH", "T1"] }
  input "skin" { montage = bipolar(plus: "TH", minus: "T1") }
  derive "skin_temp" { from = "skin"; pipeline = [ smooth(tau: 10 s) ] }
  derive "rise" { formula = "skin_temp" - baseline_c }
  threshold "target_t" { signal = "rise"; type = absolute(value: target_rise_c) }
  reward { event = dwell(condition: above("rise", "target_t"), duration: 60 s)
           continuous = "rise" }
  output { audio_gain = reward.continuous; audio_chime = reward.event }
  controls {
    baseline_pct = percent { default = 50; range = (10, 90); label = "Baseline percentile"; live_tunable = true }
    baseline_c = voltage {
      default = 1 uV; range = (0 uV, 20 uV); label = "Session baseline"; live_tunable = true
      seed = percentile { from = "skin_temp"; window = 120 s; target_pct = baseline_pct }
    }
    target_rise_c = voltage { default = 1.5 uV; range = (0.5 uV, 4 uV); label = "Target rise"; live_tunable = true }
  }
  session { phases = [ phase { name = "warmup"; duration = 300 s; output_muted = true },
                       phase { name = "training"; duration = 900 s } ] }
}
'''


def test_difference_against_a_control_round_trips():
    d = describe_protocol(RISE)
    assert d["in_subset"] is True
    blocks = [n["block"] for n in d["model"]["derives"]]
    assert blocks == ["derive.level", "derive.difference_control"]
    # `b` is the control's bare name -- the template renders it unquoted, which
    # is what makes it a control reference rather than a stream named
    # "baseline_c".
    rise = d["model"]["derives"][1]
    assert rise["slots"] == {"a": "skin_temp", "b": "baseline_c"}
    assert _ir(RISE) == _ir(render_protocol(d["model"]))


def test_difference_against_a_control_renders_the_operand_unquoted():
    d = describe_protocol(RISE)
    out = render_protocol(d["model"])
    assert 'formula = "skin_temp" - baseline_c' in out
    # And a stream-to-stream difference still quotes both sides.
    assert 'formula = "skin_temp" - "baseline_c"' not in out
