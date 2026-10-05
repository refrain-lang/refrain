"""Independent held-condition outputs for continuous feedback states."""

from pathlib import Path

import numpy as np

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve


AMP = load_amp_profile(Path(__file__).resolve().parents[1] / "src/refrain/amp_profiles/q21.json")

PROTOCOL = '''
protocol "held outputs" {
  meta { version = "1.0"; evidence = "experimental"; description = "held condition" }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "level" { from = "raw"; pipeline = [rectify()] }
  controls {
    short_dwell = duration { default = 100 ms; range = (50 ms, 500 ms); live_tunable = true }
  }
  output {
    short = hold_for(condition: above("level", 0.5), duration: short_dwell)
    long = hold_for(condition: above("level", 0.5), duration: 300 ms)
  }
}
'''


def test_independent_held_outputs_reset_on_condition_break(backend):
    ir = resolve(parse(PROTOCOL), AMP)
    ev = Evaluator.live(ir, sample_rate_hz=100.0,
                        channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend=backend)
    ev.start(skip_warmup=True)

    def step(value, count):
        events = ev.step_chunk(np.column_stack((np.full(count, value), np.zeros(count))))
        assert all(event.kind == "value" for event in events)
        streams = ev.last_streams()
        return streams["output/short"], streams["output/long"]

    short, long = step(2, 20)
    assert not short[:9].any()
    assert short[9:].all()
    assert not long.any()
    short, long = step(2, 15)
    assert short.all()
    assert not long[:9].any()
    assert long[9:].all()
    short, long = step(0, 1)
    assert not short.any() and not long.any()
    short, long = step(2, 10)
    assert not short[:9].any() and short[9:].all()
    assert not long.any()


def test_held_output_resets_when_inhibit_mutes_feedback(backend):
    guarded = PROTOCOL.replace(
        "  output {",
        '  inhibit "spike" { metric = sigmoid("raw", midpoint: 1.5, steepness: 10); '
        'threshold = absolute(0.5); action = mute(release: 1 ms) }\n  output {',
    )
    ir = resolve(parse(guarded), AMP)
    ev = Evaluator.live(ir, sample_rate_hz=100.0,
                        channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend=backend)
    ev.start(skip_warmup=True)

    def step(value, count):
        ev.step_chunk(np.column_stack((np.full(count, value), np.zeros(count))))
        return ev.last_streams()["output/short"]

    assert step(2, 20)[-1] == 1
    assert step(4, 1)[-1] == 0
    clean = step(2, 10)
    assert not clean[:9].any()
    assert clean[-1] == 1


def test_live_duration_change_resets_held_output(backend):
    ir = resolve(parse(PROTOCOL), AMP)
    ev = Evaluator.live(ir, sample_rate_hz=100.0,
                        channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend=backend)
    ev.start(skip_warmup=True)
    chunk = np.column_stack((np.full(20, 2), np.zeros(20)))
    ev.step_chunk(chunk)
    assert ev.last_streams()["output/short"][-1] == 1
    ev.set_control("short_dwell", 200.0)
    ev.step_chunk(np.column_stack((np.full(10, 2), np.zeros(10))))
    assert not ev.last_streams()["output/short"].any()
    ev.step_chunk(np.column_stack((np.full(10, 2), np.zeros(10))))
    assert ev.last_streams()["output/short"][-1] == 1


def test_explicit_audio_mute_reset_requires_fresh_dwell(backend):
    ir = resolve(parse(PROTOCOL), AMP)
    ev = Evaluator.live(ir, sample_rate_hz=100.0,
                        channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend=backend)
    ev.start(skip_warmup=True)
    long_chunk = np.column_stack((np.full(20, 2), np.zeros(20)))
    ev.step_chunk(long_chunk)
    assert ev.last_streams()["output/short"][-1] == 1
    ev.reset_held_outputs()
    ev.step_chunk(np.column_stack((np.full(9, 2), np.zeros(9))))
    assert not ev.last_streams()["output/short"].any()
