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
