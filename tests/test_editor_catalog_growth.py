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
