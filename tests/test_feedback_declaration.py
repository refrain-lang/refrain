"""A protocol declares feedback presentation without duplicating output logic."""

from pathlib import Path

import pytest

from refrain import parse, unparse
from refrain.amp_profile import load_amp_profile
from refrain.resolver import ResolveError, resolve


AMP = load_amp_profile(Path(__file__).resolve().parents[1] / "src/refrain/amp_profiles/q21.json")

BASE = '''protocol "feedback example" {
  meta { version = "1.0"; evidence = "experimental"; description = "feedback contract" }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "level" { from = "raw"; pipeline = [rectify()] }
  output {
    gain = 0.5
    chime = dwell(condition: above("level", 0.5), duration: 1 s)
  }
  FEEDBACK
}'''

DECL = '''feedback {
  version = 1
  external_background = false
  approach = layer {
    output = "gain"; role = "approach"; label = "Approach"; priority = 10
    asset_role = "approach"; attack = 4 s; release = 8 s; preview = true
    suppress_on_inhibit = true; suppress_on_mute = true; reset_on_gap = true
  }
  crossing = event {
    output = "chime"; role = "crossing"; label = "Crossing"; priority = 20
    asset_role = "crossing"; rearm = 3 s; min_interval = 20 s; preview = true
    suppress_on_inhibit = true; suppress_on_mute = true; reset_on_gap = true
  }
}'''


def resolved(decl=DECL):
    return resolve(parse(BASE.replace("FEEDBACK", decl)), AMP)


def test_feedback_entries_are_typed_and_ordered():
    feedback = resolved().feedback
    assert feedback.version == 1
    assert feedback.external_background is False
    assert [entry.name for entry in feedback.entries] == ["approach", "crossing"]
    assert feedback.entries[0].output == "gain"
    assert feedback.entries[0].attack_ms == 4000
    assert feedback.entries[1].kind == "event"
    assert feedback.entries[1].min_interval_ms == 20_000
    source = BASE.replace("FEEDBACK", DECL)
    assert parse(unparse(parse(source))) == parse(source)


def test_existing_protocol_without_feedback_stays_valid():
    assert resolved("").feedback is None


@pytest.mark.parametrize("decl, message", [
    (DECL.replace('output = "gain"', 'output = "missing"'), "missing"),
    (DECL.replace('role = "crossing"', 'role = "approach"'), "duplicate role"),
    (DECL.replace('priority = 20', 'priority = 10'), "duplicate priority"),
    (DECL.replace('output = "chime"', 'output = "gain"'), "event"),
    (DECL.replace('version = 1', 'version = 2'), "version"),
    (DECL.replace('attack = 4 s', 'attack = 61 s'), "attack"),
    (DECL.replace('asset_role = "approach"', 'asset_role = "/tmp/approach.wav"'), "asset_role"),
])
def test_bad_feedback_declarations_fail_with_location(decl, message):
    with pytest.raises(ResolveError, match=message) as error:
        resolved(decl)
    assert error.value.loc is not None
