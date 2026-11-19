import pytest

import refrain
from refrain.resolver import resolve
from refrain.editor import render_protocol
from refrain.editor.render import _fill

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
                         "cont_num": "env", "cont_den": "env_t",
                         "dwell": "250 ms", "midpoint": 1.0, "steepness": 3}},
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


# ---------------------------------------------------------------------------
# Optional catalog slots (`_fill`).
#
# An optional slot's clause is NOT a `{placeholder}` in the block's own
# `template`: it is spliced in only when the model supplies a value. That is
# deliberate and load-bearing — a renderer that has never heard of optional
# slots (refrain-editor's vendored TypeScript port) still renders every model
# that omits the slot byte-identically, which is what keeps its 41-fixture
# parity corpus green without a change in that repository.

_BASE = "event = dwell(condition: above(\"env\", \"env_t\"), duration: {dwell})"
_SLOTS = [{"name": "dwell", "type": "raw"}]
_OPTIONAL = [{"name": "tolerance", "type": "raw",
              "after": "duration: {dwell}", "clause": ", tolerance: {tolerance}"}]


def test_an_optional_slot_the_model_supplies_is_spliced_in():
    out = _fill(_BASE, _SLOTS, {"dwell": "250 ms", "tolerance": "100 ms"}, _OPTIONAL)
    assert out.endswith("duration: 250 ms, tolerance: 100 ms)")


def test_an_optional_slot_the_model_omits_renders_exactly_as_before():
    assert (_fill(_BASE, _SLOTS, {"dwell": "250 ms"}, _OPTIONAL)
            == _fill(_BASE, _SLOTS, {"dwell": "250 ms"}))


def test_a_missing_non_optional_slot_still_raises():
    # The non-optional case must keep failing loudly: a template placeholder
    # with no slot value is a bug in the model, not an omission to paper over.
    with pytest.raises(KeyError):
        _fill(_BASE, _SLOTS, {}, _OPTIONAL)


def test_an_optional_slot_whose_anchor_is_not_in_the_template_raises():
    # Without this the splice would silently do nothing and the declaration
    # would be dropped — the exact failure optional slots exist to prevent.
    bad = [dict(_OPTIONAL[0], after="duration: {nope}")]
    with pytest.raises(ValueError, match="tolerance"):
        _fill(_BASE, _SLOTS, {"dwell": "250 ms", "tolerance": "100 ms"}, bad)


def test_an_unknown_slot_name_in_the_model_still_raises():
    with pytest.raises(KeyError):
        _fill(_BASE, _SLOTS, {"dwell": "250 ms", "toleranc": "100 ms"}, _OPTIONAL)


def test_an_optional_slot_the_model_carries_empty_raises():
    """Presence is not a value. `""` renders the clause with a hole in it
    (`tolerance: )`) and `None` renders Python's own repr into the source
    (`tolerance: None`); neither parses, and both would reach a practitioner
    as a corrupt protocol rather than an error. This is the one input that
    walks straight past the `optional` marker, which is otherwise what keeps
    the splice honest."""
    for empty in ("", None):
        with pytest.raises(ValueError, match="renders empty"):
            _fill(_BASE, _SLOTS, {"dwell": "250 ms", "tolerance": empty}, _OPTIONAL)
