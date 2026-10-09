"""Authored signal checks for independent output events (not EEG evidence)."""

from pathlib import Path

import numpy as np

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve


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


def test_direct_dwell_output_emits_once_per_hold(backend):
    ir = resolve(parse(PROTOCOL), AMP)
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
