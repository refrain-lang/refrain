"""Edge-case coverage for describe/render — the gate-leak bugs found in review.

Each test pins one fix: phase mode/duration fidelity (C1/C3), graceful handling
of valid-but-out-of-subset input (C2), control-kind gating (I1), number-format
round-trip fidelity (I2), boolean meta (I3), and `extends`/unknown-section
gating (I4).
"""
import refrain
from refrain.resolver import resolve
from refrain.ir_json import ir_to_json_obj

from refrain.editor import describe_protocol, render_protocol
from refrain.editor.catalog import _fmt_num
from refrain.editor.describe import _NotInSubset, _build_model
from refrain.editor.render import _quote


def _ir(src):
    return ir_to_json_obj(resolve(refrain.parse(src)))


# A valid adaptive protocol with a {SESSION} / {META_EXTRA} / {CENTER_DEFAULT} hole.
_BASE = '''
protocol "p" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"]{META_EXTRA} }
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
    env_center = frequency { default = {CENTER_DEFAULT} Hz; range = (10.73 Hz, 16.1 Hz); label = "c" }
    reward_pct = percent { default = 70; range = (50, 90); label = "r"; live_tunable = true }
  }{SESSION}
}
'''


def _proto(*, session="", meta_extra="", center_default="13.4164"):
    return (_BASE.replace("{SESSION}", session)
                 .replace("{META_EXTRA}", meta_extra)
                 .replace("{CENTER_DEFAULT}", center_default))


# --- C1 + C3: phase mode and non-round durations round-trip faithfully -------

def test_phase_modes_and_durations_round_trip():
    session = '''
  session { phases = [
      phase { name = "warmup"; duration = 90 s; output_muted = true },
      phase { name = "train"; duration = 150 s },
      phase { name = "rest"; mode = open; output_muted = true } ] }'''
    src = _proto(session=session)
    d = describe_protocol(src)
    assert d["in_subset"] is True
    # exact IR equality: 150 s must NOT become "2 min", and `mode = open` must survive
    assert _ir(src) == _ir(render_protocol(d["model"]))
    modes = {p["name"]: p.get("mode") for p in d["model"]["session"]["phases"]}
    assert modes["rest"] == "open"


# --- C2: a valid protocol just outside the fixtures never crashes describe ----

def test_open_phase_does_not_crash_describe():
    # The exact review repro: a phase with `mode = open` and no `duration`.
    src = _proto(session='\n  session { phases = [ phase { name = "x"; mode = open } ] }')
    d = describe_protocol(src)            # must not raise
    assert d["ok"] is True


# --- I1: control kinds render cannot emit are out-of-subset, not a crash ------

def test_unrenderable_control_kind_is_out_of_subset():
    ast = refrain.parse(_proto())
    # frequency/percent are renderable; a `duration` control is not.
    _build_model(ast, [{"name": "c", "kind": "frequency"}])              # no raise
    try:
        _build_model(ast, [{"name": "w", "kind": "duration"}])
        raised = False
    except _NotInSubset:
        raised = True
    assert raised


# --- I2: numbers round-trip without precision loss ---------------------------

def test_fmt_num_is_round_trip_exact():
    assert _fmt_num(70.0) == "70"
    assert _fmt_num(1.0) == "1"
    assert _fmt_num(1.25) == "1.25"
    assert _fmt_num(13.4164078) == "13.4164078"   # would be "13.4164" under :.6g


def test_high_precision_default_round_trips():
    src = _proto(center_default="13.4164078")
    d = describe_protocol(src)
    assert d["in_subset"] is True
    assert _ir(src) == _ir(render_protocol(d["model"]))


# --- I3: boolean meta renders as true/false (not Python's True) --------------

def test_quote_renders_bools_lowercase():
    assert _quote(True) == "true"
    assert _quote(False) == "false"


def test_bool_meta_round_trips():
    src = _proto(meta_extra="; sham = true")
    d = describe_protocol(src)
    assert d["in_subset"] is True
    assert _ir(src) == _ir(render_protocol(d["model"]))


# --- I4: `extends` and unknown sections are out-of-subset --------------------

def test_extends_is_out_of_subset():
    ast = refrain.parse('protocol "c" extends "lib/base@1" '
                        '{ meta { description = "d"; status = "draft"; goals = [] } }')
    try:
        _build_model(ast, [])
        raised = False
    except _NotInSubset:
        raised = True
    assert raised


def test_unknown_section_is_out_of_subset():
    ast = refrain.parse('protocol "g" { meta { description = "d"; status = "draft"; goals = [] } '
                        'groups { frontal = ["F3", "F4"] } }')
    try:
        _build_model(ast, [])
        raised = False
    except _NotInSubset:
        raised = True
    assert raised
