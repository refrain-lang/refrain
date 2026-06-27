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


# Compound operant reward (all_of([above, below])) + EMG artifact inhibit +
# rich requires (coupling/impedance) — the standard BrainBit SMR/theta design.
COMPOUND = '''
protocol "smr_compound_cz" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { coupling = "ac"; sample_rate = ">= 256 Hz"; channels = ["Cz"]; impedance = "not_required" }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "smr" { from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  derive "theta" { from = "raw"
    pipeline = [ bandpass(band: (4 Hz, 8 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "smr_t" { signal = "smr"; type = percentile(target_pct: smr_pct, window: 2 min) }
  threshold "theta_t" { signal = "theta"; type = percentile(target_pct: theta_pct, window: 2 min) }
  inhibit "emg" {
    metric = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
    threshold = percentile(target_pct: 95, window: 2 min)
    action = mute(release: 200 ms)
  }
  reward {
    event = dwell(condition: all_of([above("smr", "smr_t"), below("theta", "theta_t")]), duration: 250 ms)
    continuous = sigmoid("smr" / "smr_t", midpoint: 1.0, steepness: 3)
  }
  output { audio_chime = reward.event }
  controls {
    smr_pct = percent { default = 40; range = (15, 70); label = "SMR reward %"; live_tunable = true }
    theta_pct = percent { default = 80; range = (50, 95); label = "Theta inhibit %"; live_tunable = true }
  }
}
'''


def test_compound_reward_and_artifact_inhibit_round_trip():
    d = describe_protocol(COMPOUND)
    assert d["in_subset"] is True
    m = d["model"]
    assert m["reward"]["block"] == "reward.operant_compound"
    assert 'above("smr", "smr_t")' in m["reward"]["slots"]["conditions"]
    assert 'below("theta", "theta_t")' in m["reward"]["slots"]["conditions"]
    inh = [c for c in m["reward_components"] if c["block"] == "inhibit.artifact"]
    assert inh and inh[0]["slots"]["band_low_hz"] == 50 and inh[0]["slots"]["release_ms"] == 200
    assert m["requires"]["coupling"] == "ac"  # rich requires preserved losslessly
    assert _ir(COMPOUND) == _ir(render_protocol(m))


# Staged / N-phase: a `block` decl activated by a phase's `block = "..."` field.
STAGED = '''
protocol "staged_smr_cz" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["adhd_attention"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "smr" { from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "smr_t" { signal = "smr"; type = percentile(target_pct: smr_pct, window: 2 min) }
  reward { event = dwell(condition: above("smr", "smr_t"), duration: 250 ms)
           continuous = sigmoid("smr" / "smr_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event }
  controls { smr_pct = percent { default = 40; range = (15, 70); label = "SMR %"; live_tunable = true } }
  block "focus" { threshold = ["smr_t"] }
  session { phases = [
    phase { name = "warmup"; duration = 60 s; output_muted = true },
    phase { name = "block1"; duration = 5 min; block = "focus"; mode = timed_with_floor }
  ] }
}
'''


def test_staged_block_round_trips():
    d = describe_protocol(STAGED)
    assert d["in_subset"] is True
    m = d["model"]
    assert m["blocks"] == [{"name": "focus", "thresholds": ["smr_t"]}]
    assert any(p.get("block") == "focus" for p in m["session"]["phases"])
    assert _ir(STAGED) == _ir(render_protocol(m))


# Alpha asymmetry: difference derive + rectify pipeline + sigmoid(<ref>, midpoint: <uV>).
ALPHA = '''
protocol "alpha_asym_c3c4" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["mood_regulation"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["C3", "C4"] }
  input "l" { montage = referential(active: "C3", reference: "linked_ears") }
  input "r" { montage = referential(active: "C4", reference: "linked_ears") }
  derive "al" { from = "l"
    pipeline = [ bandpass(band: (8 Hz, 12 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 500 ms) ] }
  derive "ar" { from = "r"
    pipeline = [ bandpass(band: (8 Hz, 12 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 500 ms) ] }
  derive "diff" { formula = "al" - "ar" }
  derive "asym" { from = "diff"; pipeline = [rectify()] }
  threshold "asym_t" { signal = "asym"; type = percentile(target_pct: rate, window: 2 min) }
  reward { event = dwell(condition: below("asym", "asym_t"), duration: 500 ms)
           continuous = sigmoid("asym", midpoint: 0 uV, steepness: 0.5) }
  output { audio_gain = reward.continuous }
  controls { rate = percent { default = 50; range = (30, 70); label = "Rate"; live_tunable = true } }
}
'''


def test_alpha_difference_rectify_abs_round_trips():
    d = describe_protocol(ALPHA)
    assert d["in_subset"] is True
    m = d["model"]
    assert any(x["block"] == "derive.difference" for x in m["derives"])
    assert any(x["block"] == "derive.rectify" for x in m["derives"])
    assert m["reward"]["block"] == "reward.operant_abs"
    assert _ir(ALPHA) == _ir(render_protocol(m))


# Placement: an `active` site picker (groups inlined into allowed).
PLACEMENT = '''
protocol "place_smr" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["adhd_attention"] }
  requires { sample_rate = ">= 256 Hz" }
  groups { sensorimotor = ["C3", "Cz", "C4"] }
  controls {
    site = placement { kind = "active"; default = "Cz"; allowed = sensorimotor; label = "Site" }
    pct = percent { default = 70; range = (50, 90); label = "Pct"; live_tunable = true }
  }
  input "raw" { montage = referential(active: site, reference: "device") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "t" { signal = "env"; type = percentile(target_pct: pct, window: 2 min) }
  reward { event = dwell(condition: all_of([above("env", "t")]), duration: 250 ms)
           continuous = sigmoid("env" / "t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event }
}
'''


def test_active_placement_round_trips():
    d = describe_protocol(PLACEMENT)
    assert d["in_subset"] is True
    pl = d["model"]["placements"]
    assert pl and pl[0]["kind"] == "active" and pl[0]["default"] == ["Cz"]
    assert pl[0]["allowed"] == ["C3", "Cz", "C4"]
    assert _ir(PLACEMENT) == _ir(render_protocol(d["model"]))
