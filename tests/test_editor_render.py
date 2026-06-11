import refrain
from refrain.resolver import resolve
from refrain.editor import render_protocol

MODEL = {
    "name": "smr_up_c4",
    "meta": {"version": "0.1.0", "description": "SMR up at C4", "status": "draft",
             "goals": ["sensorimotor_sleep"]},
    "requires": {"sample_rate": ">= 256 Hz", "channels": ["C4"]},
    "inputs": [{"name": "raw", "block": "montage.referential",
                "slots": {"active": "C4", "reference": "linked_ears"}}],
    "derives": [{"name": "env", "block": "derive.envelope", "from": "raw",
                 "slots": {"center": {"bind": "env_center"}, "ratio": 1.25, "smooth_tau_ms": 250}}],
    "thresholds": [{"name": "env_t", "block": "threshold.percentile", "signal": "env",
                    "slots": {"target_pct": {"bind": "reward_pct"}, "window_ms": 120000}}],
    "inhibits": [],
    "reward": {"block": "reward.operant",
               "slots": {"direction": "above", "signal": "env", "threshold": "env_t",
                         "dwell_ms": 250, "midpoint": 1.0, "steepness": 3}},
    "outputs": [{"channel": "audio_chime", "route": "reward.event"},
                {"channel": "audio_gain", "route": "reward.event.holds ? reward.continuous : 0"}],
    "controls": [
        {"name": "env_center", "kind": "frequency", "default": 13.4164, "unit": "Hz",
         "range": [10.73, 16.1], "label": "SMR band center", "live_tunable": False},
        {"name": "reward_pct", "kind": "percent", "default": 70, "range": [50, 90],
         "label": "Target reward %", "live_tunable": True}],
    "session": {"phases": [{"name": "warmup", "duration_ms": 90000, "output_muted": True},
                           {"name": "training", "duration_ms": 1800000, "output_muted": False}]},
}


def test_render_produces_resolvable_refrain():
    src = render_protocol(MODEL)
    ir = resolve(refrain.parse(src))          # must not raise
    assert "env_center" in ir.controls and "reward_pct" in ir.controls
    assert [str(c) for c in ir.requires.channels] == ["C4"]
