import pytest

import refrain
from refrain.resolver import resolve
from refrain.ir_json import ir_to_json_obj
from refrain.editor import describe_protocol, render_protocol

SMR = '''
protocol "smr_up_c4" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["C4"] }
  input "raw" { montage = referential(active: "C4", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(center: env_center, bandwidth: ratio(1.25), order: 4),
                 hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
  reward { event = dwell(condition: above("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
  controls {
    env_center = frequency { default = 13.4164 Hz; range = (10.73 Hz, 16.1 Hz); label = "SMR band center" }
    reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %"; live_tunable = true }
  }
  session { phases = [ phase { name = "warmup"; duration = 90 s; output_muted = true },
                       phase { name = "training"; duration = 30 min; mode = timed_with_floor } ] }
}
'''


def _ir(src):
    return ir_to_json_obj(resolve(refrain.parse(src)))


def test_smr_is_in_subset_and_round_trips():
    d = describe_protocol(SMR)
    assert d["in_subset"] is True and d["model"] is not None
    assert _ir(SMR) == _ir(render_protocol(d["model"]))


BASELINE = '''
protocol "smr_up_c4_baseline" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["C4"] }
  input "raw" { montage = referential(active: "C4", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(center: env_center, bandwidth: ratio(1.25), order: 4),
                 hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "env_t" { signal = "env"; type = absolute(value: thr_uv) }
  reward { event = dwell(condition: above("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
  controls {
    env_center = frequency { default = 13.4164 Hz; range = (10.73 Hz, 16.1 Hz); label = "SMR band center" }
    thr_uv = voltage { default = 2.0 uV; range = (0.5 uV, 30.0 uV); label = "Threshold"; live_tunable = true }
  }
}
'''


def test_baseline_absolute_round_trips():
    d = describe_protocol(BASELINE)
    assert d["in_subset"] is True
    assert _ir(BASELINE) == _ir(render_protocol(d["model"]))


# Explicit-edge envelope (`bandpass(band: (lo, hi))`) — the clinical fixed-band
# form, distinct from the center+ratio envelope above.
BAND = '''
protocol "smr_band_cz" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4),
                 hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
  reward { event = dwell(condition: above("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
  controls {
    reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %"; live_tunable = true }
  }
}
'''


def test_band_envelope_in_subset_and_round_trips():
    d = describe_protocol(BAND)
    assert d["in_subset"] is True and d["model"] is not None
    env = next(x for x in d["model"]["derives"] if x["block"] == "derive.envelope_band")
    assert env["slots"]["band_low_hz"] == 12 and env["slots"]["band_high_hz"] == 15
    assert env["slots"]["order"] == 4 and env["slots"]["smooth_tau_ms"] == 250
    assert _ir(BAND) == _ir(render_protocol(d["model"]))


def test_band_edges_edit_changes_the_band():
    """Editing band edges in the model renders a new band that resolves."""
    d = describe_protocol(BAND)
    model = d["model"]
    env = next(x for x in model["derives"] if x["block"] == "derive.envelope_band")
    env["slots"]["band_low_hz"], env["slots"]["band_high_hz"] = 11, 14
    out = render_protocol(model)
    assert "band: (11 Hz, 14 Hz)" in out
    resolve(refrain.parse(out))  # still resolves with the edited band


FAA = '''
protocol "faa_f3f4" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["mood_regulation"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["F3", "F4"] }
  input "left"  { montage = referential(active: "F3", reference: "linked_ears") }
  input "right" { montage = referential(active: "F4", reference: "linked_ears") }
  derive "alpha_l" { from = "left"
    pipeline = [ bandpass(center: alpha_center, bandwidth: ratio(1.5), order: 4), hilbert(), magnitude(), smooth(tau: 500 ms) ] }
  derive "alpha_r" { from = "right"
    pipeline = [ bandpass(center: alpha_center, bandwidth: ratio(1.5), order: 4), hilbert(), magnitude(), smooth(tau: 500 ms) ] }
  derive "faa" { formula = "alpha_l" / "alpha_r" }
  threshold "faa_t" { signal = "faa"; type = percentile(target_pct: reward_pct, window: 2 min) }
  reward { event = dwell(condition: below("faa", "faa_t"), duration: 500 ms)
           continuous = sigmoid("faa_t" / "faa", midpoint: 1.0, steepness: 3) }
  output { audio_gain = reward.continuous; audio_chime = reward.event }
  controls {
    alpha_center = frequency { default = 9.79796 Hz; range = (7.84 Hz, 11.76 Hz); label = "Alpha band center" }
    reward_pct = percent { default = 50; range = (30, 70); label = "Target reward %"; live_tunable = true }
  }
}
'''

COH = '''
protocol "alpha_coherence_c3c4" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["flow_connectivity"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["C3", "C4"] }
  input "left"  { montage = referential(active: "C3", reference: "linked_ears") }
  input "right" { montage = referential(active: "C4", reference: "linked_ears") }
  derive "alpha_coh" { formula = coherence(input_a: "left", input_b: "right", band: (8 Hz, 12 Hz), window: 2 s) }
  threshold "coh_t" { signal = "alpha_coh"; type = percentile(target_pct: reward_pct, window: 2 min) }
  reward { event = dwell(condition: above("alpha_coh", "coh_t"), duration: 500 ms)
           continuous = sigmoid("alpha_coh" / "coh_t", midpoint: 1.0, steepness: 3) }
  output { audio_gain = reward.continuous; audio_chime = reward.event }
  controls { reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %"; live_tunable = true } }
}
'''


def test_faa_round_trips():
    d = describe_protocol(FAA)
    assert d["in_subset"] is True
    assert _ir(FAA) == _ir(render_protocol(d["model"]))


def test_coherence_round_trips():
    d = describe_protocol(COH)
    assert d["in_subset"] is True
    assert _ir(COH) == _ir(render_protocol(d["model"]))


def test_public_api_exports():
    assert hasattr(refrain, "describe_protocol")
    assert hasattr(refrain, "render_protocol")


@pytest.mark.parametrize("src", [SMR, BASELINE, FAA, COH])
def test_all_in_subset_round_trip(src):
    d = describe_protocol(src)
    assert d["in_subset"] is True
    assert _ir(src) == _ir(render_protocol(d["model"]))
