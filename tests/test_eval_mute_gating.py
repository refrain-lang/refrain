# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""A mute inhibit silences every output channel, on both backends (SPEC §7.4).

These tests document behaviour both engines already have: while a `mute`
inhibit is active, each continuous `output/<channel>` reads 0.0 and each
event channel reads false. They also pin that `suppress_on_mute` on a
`feedback` layer is a declaration for the downstream player and changes
nothing in either engine.

`action = freeze(...)` is here for the same reason: it gates output exactly as
`mute` does and holds no value, which is what SPEC §7.4 says and what both
engines do — `FreezeAction.gate` is the same hangover state machine as
`MuteAction.gate` and the Rust core maps `"mute" | "freeze"` to one
`InhibitGate`. Until 0.25.0 that claim had no behavioural test on either
backend, which is what the parametrized case at the bottom of this file is.

The file name matches `tests/test_eval_*.py` on purpose: that glob is what the
dual-backend gate runs under `REFRAIN_EVAL_BACKEND=rust`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse
from refrain.resolver import resolve

AMP = load_amp_profile(Path(__file__).resolve().parents[1] / "src/refrain/amp_profiles/q21.json")

RATE = 256
CHUNK = 64  # 4 chunks per second; the burst edges below fall on chunk edges

# `audio_gain` is a constant 0.5, so any 0.0 reading is the mute and nothing
# else. `audio_chime` is true whenever the signal is above 0.5, which includes
# the burst, so a false reading during the mute is the gate and not an
# absent event. The mute trips only above ~1.5, so the middle second of the
# signal (level 1.0 after the montage halves it) has the chime true and the output unmuted.
PROTOCOL = '''
protocol "mute gating" {
  meta { version = "1.0"; evidence = "experimental"; description = "mute gating" }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "level" { from = "raw"; pipeline = [rectify()] }
  inhibit "emg" {
    metric = sigmoid("level", midpoint: 1.5 uV, steepness: 10)
    threshold = absolute(0.5)
    action = ACTION(release: 100 ms)
  }
  output {
    audio_gain = 0.5
    audio_chime = above("level", 0.5 uV)
  }
  FEEDBACK
}
'''

FEEDBACK = '''feedback {
  version = 1
  external_background = false
  approach = layer {
    output = "audio_gain"; role = "approach"; label = "Approach"; priority = 10
    asset_role = "approach"; attack = 4 s; release = 8 s; preview = true
    suppress_on_inhibit = true; suppress_on_mute = SUPPRESS; reset_on_gap = true
  }
}'''


def _ir(suppress_on_mute: bool | None = None, action: str = "mute"):
    feedback = "" if suppress_on_mute is None else FEEDBACK.replace(
        "SUPPRESS", "true" if suppress_on_mute else "false")
    return resolve(parse(
        PROTOCOL.replace("FEEDBACK", feedback).replace("ACTION", action)), AMP)


def _signal() -> np.ndarray:
    """6 s: quiet, a moderate second (1-2 s), an EMG burst (2-3 s), quiet."""
    level = np.full(6 * RATE, 0.2)
    level[1 * RATE:2 * RATE] = 2.0
    level[2 * RATE:3 * RATE] = 4.0
    return np.column_stack((level, np.zeros_like(level)))


def _run(backend: str, suppress_on_mute: bool | None = None,
         action: str = "mute") -> list[dict]:
    """Feed the signal chunk by chunk; return the taps after each chunk."""
    ev = Evaluator.live(_ir(suppress_on_mute, action), sample_rate_hz=RATE,
                        channel_names=("Cz", "linked_ears"), backend=backend)
    ev.start(skip_warmup=True)
    signal = _signal()
    taps = []
    for start in range(0, len(signal), CHUNK):
        ev.step_chunk(signal[start:start + CHUNK])
        taps.append(ev.last_taps())
    return taps


def test_continuous_output_reads_zero_while_muted(backend):
    taps = _run(backend)
    assert any(t["muted"] for t in taps)
    assert any(not t["muted"] and t["output/audio_gain"] > 0.0 for t in taps)
    for t in taps:
        if t["muted"]:
            assert t["output/audio_gain"] == 0.0


def test_event_output_reads_false_while_muted(backend):
    taps = _run(backend)
    # Unmuted, the chime fires: so a false reading under the mute is the gate.
    assert any(not t["muted"] and t["output/audio_chime"] for t in taps)
    assert any(t["muted"] for t in taps)
    for t in taps:
        if t["muted"]:
            assert t["output/audio_chime"] == 0.0


def test_continuous_output_resumes_after_the_mute_clears(backend):
    taps = _run(backend)
    after = [t for t in taps if not t["muted"]][-5:]
    assert any(t["output/audio_gain"] > 0.0 for t in after)


def test_suppress_on_mute_on_a_feedback_layer_is_not_an_engine_behaviour(backend):
    """`suppress_on_mute` reaches the IR JSON and the Rust IR struct and is
    acted on by NEITHER engine: it is a declaration for the downstream
    player. Pinned so the next reader does not mistake it for a bug."""
    for flag in (True, False):
        entry = ir_to_json_obj(_ir(flag))["feedback"]["entries"][0]
        assert entry["suppress_on_mute"] is flag
    with_flag = _run(backend, suppress_on_mute=True)
    without = _run(backend, suppress_on_mute=False)
    assert any(t["muted"] for t in with_flag)
    for key in ("output/audio_gain", "output/audio_chime", "muted"):
        assert [t[key] for t in with_flag] == [t[key] for t in without]
    # Even with the flag false, the engine still silences the muted chunks.
    assert all(t["output/audio_gain"] == 0.0 for t in without if t["muted"])


@pytest.mark.parametrize("action", ["mute", "freeze"])
def test_a_freeze_gates_the_output_as_a_mute_does_and_holds_no_value(backend, action):
    """SPEC §7.4: a `freeze` inhibit zeroes the output, hangover and all.

    The name says otherwise and the spec used to as well, which is why this
    exists: through 0.24 §4.6 said a freeze held the output at its last value.
    Neither engine has ever done that — `FreezeAction.gate` is the same
    hangover state machine as `MuteAction.gate`, and the Rust core maps
    `"mute" | "freeze"` to a single `InhibitGate` — and 0.25.0 corrected the
    sentence. A protocol author who believed the old sentence would author a
    `freeze` gate expecting a held tone and get silence.

    "Zero" and "the last value" are different answers here, which is what
    makes this case able to tell them apart: the channel is a constant 0.5, so
    the sample before the gate shuts reads 0.5 and a held value would keep
    reading 0.5 for the whole mute. And the two actions are compared
    tap-for-tap, not merely both-nonzero: whatever the gate does, it has to do
    the same thing under either spelling, on either backend.
    """
    # The fixture is the thing most likely to make this vacuous: if the
    # action never reached the IR as a freeze, the case below would be a
    # second mute test passing for the wrong reason.
    assert _ir(action=action).inhibits["emg"].action_kind == action
    taps = _run(backend, action=action)
    first_muted = next(i for i, t in enumerate(taps) if t["muted"])
    # The last reading before the gate shut, i.e. the value a freeze would
    # have held. Non-zero, or the assertion below proves nothing.
    assert first_muted > 0
    assert taps[first_muted - 1]["output/audio_gain"] == 0.5
    assert any(t["muted"] for t in taps)
    for t in taps:
        if t["muted"]:
            assert t["output/audio_gain"] == 0.0
            assert t["output/audio_chime"] == 0.0
    # Identical to the `mute` run, tap for tap, including which chunks are
    # gated and the hangover's length.
    as_mute = _run(backend, action="mute")
    for key in ("muted", "output/audio_gain", "output/audio_chime"):
        assert [t[key] for t in taps] == [t[key] for t in as_mute], key
