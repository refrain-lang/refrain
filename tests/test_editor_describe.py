from refrain.editor import describe_protocol

_IN_SUBSET_MINIMAL = '''
protocol "minimal_declared_cz" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["sensorimotor_sleep"] }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"
    pipeline = [ band_envelope(band: (12 Hz, 15 Hz), latency: 360 ms) ] }
  threshold "env_t" { signal = "env"; type = percentile(target_pct: 70, window: 2 min) }
  reward { event = dwell(condition: above("env", "env_t"), duration: 250 ms)
           continuous = sigmoid("env" / "env_t", midpoint: 1.0, steepness: 3) }
  output { audio_chime = reward.event; audio_gain = reward.event.holds ? reward.continuous : 0 }
}
'''

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


def test_in_subset_protocol_reports_no_reason():
    from refrain.editor import describe_protocol as dp
    d = dp(_IN_SUBSET_MINIMAL)
    assert d["in_subset"] is True
    assert d["reason"] is None


def test_out_of_subset_reports_the_decision():
    from refrain.editor import describe_protocol as dp
    # Resolves fine, but bandpass -> smooth is not a shape the editor models
    # (this is scp_cz's pipeline). A resolve failure would exercise a
    # different path, so it has to be a protocol the language accepts.
    d = dp(_IN_SUBSET_MINIMAL.replace(
        "pipeline = [ band_envelope(band: (12 Hz, 15 Hz), latency: 360 ms) ]",
        "pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), smooth(tau: 250 ms) ]"))
    assert d["ok"] is True
    assert d["in_subset"] is False
    assert "not in subset" in d["reason"]


def test_a_matcher_crash_is_reported_as_a_crash_not_a_decision():
    """The broad except at describe.py:653 cannot tell a deliberate
    _NotInSubset from an AttributeError in a matcher, and that is what hid two
    of the six editor fixes. `reason` names the exception type for anything
    that is not a _NotInSubset, so a bug reads as a bug."""
    import refrain.editor.describe as D
    from refrain.editor import describe_protocol as dp
    orig = D._match_derive
    D._match_derive = lambda decl: (_ for _ in ()).throw(AttributeError("boom"))
    try:
        d = dp(_IN_SUBSET_MINIMAL)
    finally:
        D._match_derive = orig
    assert d["in_subset"] is False
    assert d["reason"].startswith("AttributeError")


def test_an_input_with_an_explicit_unit_is_out_of_subset():
    """An `input` round-trips as its montage alone, so a `unit` declaration
    would be dropped on render and the signal would come back as microvolts.

    Measured before the guard existed: a Celsius protocol went through
    describe -> render and the rendered input read
    `input "skin" { montage = bipolar(plus: "TH", minus: "T1") }`, with the
    input's dimensions C -> uV and the derive's C -> uV behind it. The
    numbers were unchanged, so nothing in the file looked wrong.

    Refusing the protocol is visible; retyping its signal is not.
    """
    from refrain.editor import describe_protocol as dp
    src = _IN_SUBSET_MINIMAL.replace(
        'input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }',
        'input "raw" { montage = referential(active: "Cz", reference: "linked_ears")\n'
        '               unit = "C" }')
    d = dp(src)
    assert d["ok"] is True, d["reason"]          # the language accepts it
    assert d["in_subset"] is False
    assert "unit" in d["reason"]


def test_the_unit_guard_does_not_touch_an_ordinary_input():
    """The same protocol without the declaration stays in the subset, so the
    refusal is the declaration and not the montage shape."""
    from refrain.editor import describe_protocol as dp
    d = dp(_IN_SUBSET_MINIMAL)
    assert d["in_subset"] is True
    assert d["reason"] is None
