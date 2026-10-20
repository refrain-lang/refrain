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


ANYOF = COMPOUND.replace('"smr_compound_cz"', '"smr_anyof_cz"').replace("all_of", "any_of")


def test_anyof_compound_reward_round_trips():
    d = describe_protocol(ANYOF)
    assert d["in_subset"] is True
    m = d["model"]
    assert m["reward"]["block"] == "reward.operant_compound_anyof"
    assert 'above("smr", "smr_t")' in m["reward"]["slots"]["conditions"]
    out = render_protocol(m)
    assert "any_of([" in out and "all_of" not in out
    assert _ir(ANYOF) == _ir(out)


def test_anyof_bare_ref_continuous_not_in_subset():
    src = ANYOF.replace('sigmoid("smr" / "smr_t"', 'sigmoid("smr"')
    assert describe_protocol(src)["in_subset"] is False


def test_compound_numeric_condition_arg_not_in_subset():
    assert describe_protocol(ANYOF.replace('above("smr", "smr_t")', 'above("smr", 2.5)'))["in_subset"] is False
    assert describe_protocol(COMPOUND.replace('above("smr", "smr_t")', 'above("smr", 2.5)'))["in_subset"] is False


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


# Baseline-seeded control (`seed = percentile { ... }`), first-class as of the
# language feature that shipped in v0.15.0. The editor must round-trip the seed
# rather than silently drop it — otherwise editing a seeded protocol in the
# catalog UI strips the patient's baseline threshold. Two target_pct forms:
# a `percent` control bind (the generic operant cores) and a literal (BrainBit).
SEEDED_BIND = '''
protocol "smr_up_c4_seeded" {
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
    reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %"; live_tunable = true }
    thr_uv = voltage { default = 2.0 uV; range = (0.5 uV, 30.0 uV); label = "Threshold"; live_tunable = true
      seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct } }
  }
}
'''

SEEDED_LIT = '''
protocol "smr_up_c4_seeded_lit" {
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
    thr_uv = voltage { default = 2.0 uV; range = (0.5 uV, 30.0 uV); label = "Threshold"; live_tunable = true
      seed = percentile { from = "env"; window = 60 s; target_pct = 40 } }
  }
}
'''


def test_seeded_control_bind_round_trips():
    d = describe_protocol(SEEDED_BIND)
    assert d["in_subset"] is True
    thr = next(c for c in d["model"]["controls"] if c["name"] == "thr_uv")
    assert thr.get("seed"), "describe dropped the control seed"
    assert _ir(SEEDED_BIND) == _ir(render_protocol(d["model"]))


def test_seeded_control_literal_round_trips():
    d = describe_protocol(SEEDED_LIT)
    assert d["in_subset"] is True
    thr = next(c for c in d["model"]["controls"] if c["name"] == "thr_uv")
    assert thr.get("seed"), "describe dropped the control seed"
    assert _ir(SEEDED_LIT) == _ir(render_protocol(d["model"]))


BIPOLAR_LIT = '''
protocol "theta_down_t3t4" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["adhd_attention"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["T3","T4"] }
  input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(band: (4 Hz, 8 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "env_t" { signal = "env"; type = percentile(target_pct: 30, window: 2 min) }
  reward { event = dwell(condition: below("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event }
  session { phases = [ phase { name = "training"; duration = 5 min } ] }
}
'''


def test_bipolar_lit_is_in_subset_and_round_trips():
    d = describe_protocol(BIPOLAR_LIT)
    assert d["in_subset"] is True and d["model"] is not None
    montage = d["model"]["inputs"][0]
    assert montage["block"] == "montage.bipolar_lit"
    assert montage["slots"] == {"plus": "T3", "minus": "T4"}
    assert _ir(BIPOLAR_LIT) == _ir(render_protocol(d["model"]))


# A mode-conditional protocol whose BASELINE branch uses an absolute threshold
# bound to a seeded control. Under the default `adaptive` mode the resolver
# folds that branch away, so `thr_uv` is unreferenced and the runtime IR
# correctly carries no seed for it -- there is nothing to compute. The EDITOR
# view is a different question: it still shows `thr_uv` as a control, so
# dropping only its seed loses authored intent on save.
SEEDED_MODE = '''
protocol "seeded_mode" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["focus_attention"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4),
                 hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "env_t" {
    signal = "env"
    type   = style == "baseline"
             ? absolute(value: thr_uv)
             : percentile(target_pct: reward_pct, window: 2 min)
  }
  reward { event = dwell(condition: above("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.continuous }
  controls {
    style = mode { choices = ["adaptive", "baseline"]; default = "adaptive"; label = "Threshold style" }
    reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %"; live_tunable = true }
    thr_uv = voltage {
      default      = 2.0 uV
      range        = (0.5 uV, 10.0 uV)
      label        = "Threshold"
      live_tunable = true
      seed = percentile {
        from       = "env"
        window     = 60 s
        target_pct = 40
      }
    }
  }
  session { phases = [ phase { name = "warmup"; duration = 90 s; output_muted = true },
                       phase { name = "training"; duration = 30 min } ] }
}
'''


def test_editor_keeps_a_seed_on_a_mode_folded_control():
    """A seed on a control the active mode folds out must survive describe.

    The resolver deliberately does not attach a `dead` seed to the runtime IR
    (resolver.py: "dead seed: control folded out -- do not attach"), which is
    right: nothing should compute it. But `describe_protocol` keeps the control
    itself in the model, so reading seeds off the resolved IR drops the seed
    while keeping its control -- and re-rendering then emits a protocol whose
    baseline mode has silently lost its seeding rule.
    """
    d = describe_protocol(SEEDED_MODE)
    assert d["in_subset"] is True
    ctl = next(c for c in d["model"]["controls"] if c["name"] == "thr_uv")
    assert ctl.get("seed") == {
        "statistic": "percentile",
        "from": "env",
        "window_ms": 60000,
        "target_pct": 40,
    }


def test_rendering_a_mode_folded_control_keeps_its_seed_block():
    """The round trip must not drop the seed from the emitted source."""
    d = describe_protocol(SEEDED_MODE)
    out = render_protocol(d["model"])
    assert "seed = percentile" in out
    assert 'from       = "env"' in out
    # And the re-rendered protocol still resolves and still round-trips.
    again = describe_protocol(out)
    assert again["in_subset"] is True
    assert again["model"] == d["model"]


# Band edges as PAIRED frequency controls — `bandpass(band: (lo_hz, hi_hz))`.
# This is how the flagship protocols are written (smr.refrain:113), and it is
# why five of them sat outside the editor subset: the four-stage matcher
# demanded A.NumberLit for both edges.
BOUND_EDGES = '''
protocol "smr_bound_edges_cz" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ bandpass(band: (smr_lo_hz, smr_hi_hz), order: 4),
                 hilbert(), magnitude(), smooth(tau: 250 ms) ] }
  threshold "env_t" { signal = "env"; type = percentile(target_pct: reward_pct, window: 2 min) }
  reward { event = dwell(condition: above("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
  controls {
    smr_lo_hz = frequency { default = 12 Hz; range = (10 Hz, 14 Hz); label = "SMR low edge" }
    smr_hi_hz = frequency { default = 15 Hz; range = (13 Hz, 18 Hz); label = "SMR high edge" }
    reward_pct = percent { default = 70; range = (50, 90); label = "Target reward %"; live_tunable = true }
  }
  session { phases = [ phase { name = "training"; duration = 30 min; mode = timed_with_floor } ] }
}
'''


def test_control_bound_band_edges_are_in_subset():
    d = describe_protocol(BOUND_EDGES)
    assert d["ok"], d.get("diagnostics")
    assert d["in_subset"] is True


def test_control_bound_band_edges_describe_as_bindings():
    d = describe_protocol(BOUND_EDGES)
    env = next(b for b in d["model"]["derives"] if b["name"] == "env")
    assert env["block"] == "derive.envelope_band"
    assert env["slots"]["band_low_hz"] == {"bind": "smr_lo_hz"}
    assert env["slots"]["band_high_hz"] == {"bind": "smr_hi_hz"}


def test_control_bound_band_edges_round_trip():
    d = describe_protocol(BOUND_EDGES)
    assert _ir(BOUND_EDGES) == _ir(render_protocol(d["model"]))


# The artifact-rejection inhibit as the flagship protocols write it
# (smr.refrain:181): a mode-gated conditional for target_pct, and — once the
# band edges above are bindable — the same literal-only assumption one
# function away in _match_reward_component.
ARTIFACT_INHIBIT = BOUND_EDGES.replace(
    '  reward {',
    '''  inhibit "emg" {
    metric    = bandpower(input: "raw", band: (emg_lo_hz, emg_hi_hz), window: 100 ms)
    threshold = percentile(
      target_pct: artifact_guard == "on" ? artifact_strictness : 100,
      window: 2 min
    )
    action    = mute(release: 200 ms)
  }
  reward {''').replace(
    '    reward_pct = percent',
    '''    emg_lo_hz = frequency { default = 50 Hz; range = (30 Hz, 70 Hz); label = "EMG low edge" }
    emg_hi_hz = frequency { default = 100 Hz; range = (80 Hz, 120 Hz); label = "EMG high edge" }
    artifact_guard = mode { choices = ["on", "off"]; default = "on"; label = "Artifact guard (EMG)" }
    artifact_strictness = percent { default = 95; range = (50, 99); label = "Guard strictness"; live_tunable = true }
    reward_pct = percent''')


def _component(d, name):
    return next(c for c in d["model"]["reward_components"] if c["name"] == name)


def test_artifact_inhibit_with_conditional_target_is_in_subset():
    d = describe_protocol(ARTIFACT_INHIBIT)
    assert d["ok"], d.get("diagnostics")
    assert d["in_subset"] is True


def test_artifact_inhibit_keeps_the_conditional_verbatim():
    slots = _component(describe_protocol(ARTIFACT_INHIBIT), "emg")["slots"]
    assert slots["target_pct"] == {
        "expr": 'artifact_guard == "on" ? artifact_strictness : 100'}


def test_artifact_inhibit_band_edges_are_bindable():
    # Same literal-only bug as the derive matcher, one function away.
    slots = _component(describe_protocol(ARTIFACT_INHIBIT), "emg")["slots"]
    assert slots["band_low_hz"] == {"bind": "emg_lo_hz"}
    assert slots["band_high_hz"] == {"bind": "emg_hi_hz"}


def test_artifact_inhibit_round_trips():
    d = describe_protocol(ARTIFACT_INHIBIT)
    assert _ir(ARTIFACT_INHIBIT) == _ir(render_protocol(d["model"]))


# A placement control that OMITS `allowed` (smr.refrain:223). The language
# treats three spellings differently — omitted is legal (resolver.py:2050),
# "any" is legal (:2051), `[]` RAISES (:2053) — and describe/render between
# them turned the legal omission into the illegal empty list.
OMITTED_ALLOWED = BOUND_EDGES.replace(
    '    reward_pct = percent',
    '''    site = placement { kind = "active"; default = "Cz"; label = "Training site" }
    reward_pct = percent''')


def test_placement_without_allowed_does_not_render_an_empty_list():
    out = render_protocol(describe_protocol(OMITTED_ALLOWED)["model"])
    assert "allowed = []" not in out


def test_placement_without_allowed_still_resolves_after_rendering():
    # The real gate: _ir() resolves. Comparing rendered text to rendered text
    # would have passed throughout, which is how this shipped.
    d = describe_protocol(OMITTED_ALLOWED)
    assert _ir(OMITTED_ALLOWED) == _ir(render_protocol(d["model"]))


# A placement declared AFTER the plain controls (smr.refrain:223, under an
# "Advanced: site" comment). render.py emitted every placement at the TOP of
# the controls block, so the authored position was lost — and the resolver's
# topological_order is source-position-sensitive, exactly as
# _mode_decls_by_name's docstring warns for modes.
TRAILING_PLACEMENT = BOUND_EDGES.replace(
    '  }\n  session {',
    '''    site = placement { kind = "active"; default = "Cz"; label = "Training site" }
  }
  session {''')


def test_trailing_placement_keeps_its_source_position():
    d = describe_protocol(TRAILING_PLACEMENT)
    assert d["in_subset"] is True
    assert _ir(TRAILING_PLACEMENT) == _ir(render_protocol(d["model"]))


def test_leading_placement_still_keeps_its_source_position():
    # The 37 protocols that already round-tripped declare placements first.
    # Fixing the trailing case must not break the leading one.
    d = describe_protocol(OMITTED_ALLOWED)
    assert _ir(OMITTED_ALLOWED) == _ir(render_protocol(d["model"]))


# The declared-latency envelope primitive (refrain v0.23.0). One stage in place
# of bandpass -> hilbert -> magnitude -> smooth. Band edges stay bindable
# because the flagship protocols drive them from paired frequency controls and
# migration must not take those sliders away; `latency` does not, because it
# sizes the filter whose coefficients are baked at resolve time.
DECLARED = BOUND_EDGES.replace(
    '''    pipeline = [ bandpass(band: (smr_lo_hz, smr_hi_hz), order: 4),
                 hilbert(), magnitude(), smooth(tau: 250 ms) ] }''',
    '''    pipeline = [ band_envelope(band: (smr_lo_hz, smr_hi_hz), latency: 360 ms) ] }''')


def test_declared_envelope_is_in_subset():
    d = describe_protocol(DECLARED)
    assert d["ok"], d.get("diagnostics")
    assert d["in_subset"] is True


def test_declared_envelope_block_and_slots():
    env = next(b for b in describe_protocol(DECLARED)["model"]["derives"]
               if b["name"] == "env")
    assert env["block"] == "derive.envelope_declared"
    assert env["slots"] == {
        "band_low_hz": {"bind": "smr_lo_hz"},
        "band_high_hz": {"bind": "smr_hi_hz"},
        "latency_ms": 360.0,
    }


def test_declared_envelope_round_trips():
    d = describe_protocol(DECLARED)
    assert _ir(DECLARED) == _ir(render_protocol(d["model"]))


def test_declared_envelope_with_literal_edges_round_trips():
    src = DECLARED.replace("band: (smr_lo_hz, smr_hi_hz)", "band: (12 Hz, 15 Hz)")
    d = describe_protocol(src)
    assert d["in_subset"] is True
    assert _ir(src) == _ir(render_protocol(d["model"]))


def test_declared_envelope_with_explicit_taps_is_not_in_subset():
    # `taps` sizes the filter and the block has no slot for it, so accepting
    # the protocol would silently drop it and change the filter. Refuse rather
    # than round-trip something that is not what was authored.
    src = DECLARED.replace("latency: 360 ms)", "latency: 360 ms, taps: ratio(2.5))")
    assert describe_protocol(src)["in_subset"] is False
