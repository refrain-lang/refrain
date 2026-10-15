"""Authored signal checks for independent output events (not EEG evidence)."""

import json
from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse
from refrain.resolver import ResolveError, resolve


AMP = load_amp_profile(Path(__file__).resolve().parents[1] / "src/refrain/amp_profiles/q21.json")
PROTOCOL = '''
protocol "output event" {
  meta { version = "1.0"; evidence = "experimental"; description = "output event" }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  output {
    cue = dwell(condition: above("raw", 0.5), duration: 30 ms)
  }
}
'''

REARM_PROTOCOL = PROTOCOL.replace(
    'dwell(condition: above("raw", 0.5), duration: 30 ms)',
    'dwell_rearm(condition: above("raw", 0.5), duration: 30 ms, rearm: 50 ms)',
)
REARM_TUNABLE_PROTOCOL = REARM_PROTOCOL.replace(
    "  output {",
    "  controls { cue_dwell = duration { default = 30 ms; "
    "range = (10 ms, 100 ms); live_tunable = true } }\n  output {",
).replace("duration: 30 ms, rearm: 50 ms", "duration: cue_dwell, rearm: 50 ms")


def test_direct_dwell_output_emits_once_per_hold(backend):
    ir = resolve(parse(PROTOCOL), AMP)
    assert ir_to_json_obj(ir, sample_rate_hz=100.0)["refrain_ir_version"] == "0.5"
    evaluator = Evaluator.live(
        ir, sample_rate_hz=100.0, channel_names=("Cz", "linked_ears"),
        record_streams=True, backend=backend,
    )
    evaluator.start(skip_warmup=True)

    def step(value: float, count: int):
        chunk = np.column_stack((np.full(count, value), np.zeros(count)))
        return evaluator.step_chunk(chunk)

    assert step(0.0, 3) == []
    assert step(2.0, 2) == []
    first = step(2.0, 6)
    assert [(event.channel, event.kind) for event in first] == [("cue", "event")]
    assert step(2.0, 10) == []
    assert step(0.0, 1) == []
    second = step(2.0, 3)
    assert [(event.channel, event.kind) for event in second] == [("cue", "event")]


def test_direct_dwell_output_resets_on_gap(backend):
    ir = resolve(parse(PROTOCOL), AMP)
    evaluator = Evaluator.live(
        ir, sample_rate_hz=100.0, channel_names=("Cz", "linked_ears"),
        backend=backend,
    )
    evaluator.start(skip_warmup=True)

    def step(count: int):
        return evaluator.step_chunk(np.column_stack((np.full(count, 2.0), np.zeros(count))))

    assert step(2) == []
    evaluator.reset_held_outputs()
    assert step(2) == []
    assert [(event.channel, event.kind) for event in step(1)] == [("cue", "event")]


def test_rearmed_output_needs_continuous_time_out_before_another_cue(backend):
    ir = resolve(parse(REARM_PROTOCOL), AMP)
    encoded = ir_to_json_obj(ir, sample_rate_hz=100.0)
    assert encoded["refrain_ir_version"] == "0.5"
    assert encoded["output"]["cue"]["coeffs"] == {
        "dwell_samples": 3, "rearm_samples": 5,
    }
    evaluator = Evaluator.live(
        ir, sample_rate_hz=100.0, channel_names=("Cz", "linked_ears"),
        backend=backend,
    )
    evaluator.start(skip_warmup=True)

    def count_events(value: float, count: int) -> int:
        chunk = np.column_stack((np.full(count, value), np.zeros(count)))
        return len(evaluator.step_chunk(chunk))

    assert count_events(2.0, 10) == 1
    assert count_events(0.0, 4) == 0
    assert count_events(2.0, 10) == 0
    assert count_events(0.0, 5) == 0
    assert count_events(2.0, 3) == 1
    evaluator.reset_held_outputs()
    assert count_events(2.0, 2) == 0
    assert count_events(2.0, 1) == 1


def test_rearmed_output_guard_breaks_hold_without_rearming(backend):
    """Authored sample levels test evaluator behavior, not clinical EEG evidence."""
    guarded = REARM_PROTOCOL.replace(
        "  output {",
        '  inhibit "spike" { metric = sigmoid("raw", midpoint: 1.5, steepness: 10); '
        'threshold = absolute(0.5); action = mute(release: 1 ms) }\n  output {',
    )
    ir = resolve(parse(guarded), AMP)
    evaluator = Evaluator.live(
        ir, sample_rate_hz=100.0, channel_names=("Cz", "linked_ears"), backend=backend,
    )
    evaluator.start(skip_warmup=True)

    def count_events(value: float, count: int) -> int:
        chunk = np.column_stack((np.full(count, value), np.zeros(count)))
        return len(evaluator.step_chunk(chunk))

    assert count_events(2.0, 3) == 1
    assert count_events(4.0, 1) == 0
    assert count_events(2.0, 10) == 0
    assert count_events(0.0, 5) == 0
    assert count_events(2.0, 3) == 1


def test_event_output_ir_validates_without_a_feedback_declaration():
    jsonschema = pytest.importorskip("jsonschema")
    ir = resolve(parse(REARM_PROTOCOL), AMP)
    schema_path = Path(__file__).resolve().parents[1] / "src/refrain/schema/ir-json-v0.5.schema.json"
    schema = json.loads(schema_path.read_text())
    jsonschema.validate(ir_to_json_obj(ir, sample_rate_hz=100.0), schema)


def test_rearmed_event_is_not_accepted_as_a_reward_event_source():
    source = REARM_PROTOCOL.replace(
        "  output {",
        '  reward { event = dwell_rearm(condition: above("raw", 0.5), '
        'duration: 30 ms, rearm: 50 ms) }\n  output {',
    )
    with pytest.raises(ResolveError, match="reward.event requires dwell"):
        resolve(parse(source), AMP)


def test_rearm_interval_is_fixed_and_positive():
    with pytest.raises(ResolveError, match="fixed duration"):
        resolve(parse(REARM_TUNABLE_PROTOCOL.replace(
            "rearm: 50 ms", "rearm: cue_dwell",
        )), AMP)
    with pytest.raises(ResolveError, match="positive"):
        resolve(parse(REARM_PROTOCOL.replace("rearm: 50 ms", "rearm: 0 ms")), AMP)


def test_rearmed_output_dwell_duration_is_live_tunable(backend):
    ir = resolve(parse(REARM_TUNABLE_PROTOCOL), AMP)
    evaluator = Evaluator.live(
        ir, sample_rate_hz=100.0, channel_names=("Cz", "linked_ears"),
        backend=backend,
    )
    evaluator.start(skip_warmup=True)
    chunk = np.column_stack((np.full(3, 2.0), np.zeros(3)))
    assert len(evaluator.step_chunk(chunk)) == 1
    evaluator.set_control("cue_dwell", 50.0)
    assert evaluator.step_chunk(np.column_stack((np.full(4, 2.0), np.zeros(4)))) == []
    assert len(evaluator.step_chunk(np.array([[2.0, 0.0]]))) == 1
