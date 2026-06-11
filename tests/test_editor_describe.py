from refrain.editor import describe_protocol

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
}
'''


def test_describe_extracts_meta_and_controls():
    d = describe_protocol(SMR)
    assert d["ok"] is True
    assert d["meta"]["description"] == "d"
    by_name = {c["name"]: c for c in d["controls"]}
    assert by_name["env_center"]["kind"] == "frequency"
    assert by_name["env_center"]["live_tunable"] is False
    assert by_name["reward_pct"]["default"] == 70
    assert d["placements"] == []


def test_describe_reports_parse_error():
    d = describe_protocol('protocol "x" { this is not valid')
    assert d["ok"] is False
    assert d["diagnostics"]
    assert d["model"] is None


def test_extra_pipeline_stage_is_out_of_subset_but_tunable():
    src = SMR.replace("smooth(tau: 250 ms) ]", "smooth(tau: 250 ms), differentiate() ]")
    d = describe_protocol(src)
    assert d["ok"] is True
    assert d["in_subset"] is False
    assert d["model"] is None
    assert any(c["name"] == "env_center" for c in d["controls"])  # still tunable
