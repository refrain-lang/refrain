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


def test_describe_parse_error_still_has_modes_key():
    # A host that reads desc["modes"] unconditionally must not KeyError just
    # because the source failed to parse. Before the fix, the ok: False
    # early-return omitted "modes" entirely and this raised KeyError.
    d = describe_protocol("not a valid protocol")
    assert d["ok"] is False
    assert d["modes"] == []


def test_describe_resolve_error_still_has_modes_key():
    # Same guarantee on the other ok: False path: a source that parses but
    # fails to resolve (e.g. a derive referencing an unknown stream) must
    # still return "modes": [] rather than omitting the key.
    src = ('protocol "x" { input "raw" { montage = referential('
           'active: "C4", reference: "linked_ears") } '
           'derive "d" { from = "missing"; pipeline = [rectify()] } }')
    d = describe_protocol(src)
    assert d["ok"] is False
    assert d["modes"] == []


def test_extra_pipeline_stage_is_out_of_subset_but_tunable():
    src = SMR.replace("smooth(tau: 250 ms) ]", "smooth(tau: 250 ms), differentiate() ]")
    d = describe_protocol(src)
    assert d["ok"] is True
    assert d["in_subset"] is False
    assert d["model"] is None
    assert any(c["name"] == "env_center" for c in d["controls"])  # still tunable


_STYLED = '''
    protocol "styled" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        threshold_style = mode { choices = ["adaptive", "baseline"]; default = "adaptive"; label = "Threshold style" }
        reward_pct = percent { default = 70 }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "env" { from = "raw"; pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ] }
      threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
      reward { continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
      output { audio_gain = reward.continuous }
    }
'''


def test_describe_lists_mode_control():
    desc = describe_protocol(_STYLED)
    assert desc["ok"] is True
    modes = {m["name"]: m for m in desc["modes"]}
    assert modes["threshold_style"]["choices"] == ["adaptive", "baseline"]
    assert modes["threshold_style"]["default"] == "adaptive"
    assert modes["threshold_style"]["label"] == "Threshold style"
    # A mode control must not leak into the numeric controls list.
    assert "threshold_style" not in {c["name"] for c in desc["controls"]}
