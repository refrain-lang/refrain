# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Weighted-composite evaluation (Stage 1, backend='python')."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

AMP_PATH = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json"


@pytest.fixture(scope="module")
def amp():
    return load_amp_profile(AMP_PATH)


# One reward (smr, weight 1), one suppress (theta, weight 1). Identity-ish
# derives so the sigmoids are driven by the raw input directly.
_PROTO = '''
    protocol "p" {
      meta { version = "1.0"; evidence = "clinical"; description = "x" }
      controls {
        w_smr   = percent { default = 1; range = (0, 4); live_tunable = true }
        w_theta = percent { default = 1; range = (0, 4); live_tunable = true }
      }
      input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
      derive "smr_env"   { from = "raw"; pipeline = [rectify()] }
      derive "theta_env" { from = "raw"; pipeline = [rectify()] }
      reward  "smr"   { signal = sigmoid("smr_env",   midpoint: 0, steepness: 1000); weight = w_smr }
      inhibit "theta" { signal = sigmoid("theta_env", midpoint: 0, steepness: 1000); weight = w_theta }
      reward { combine = "weighted"; continuous = reward.composite }
      output { audio_gain = reward.composite }
    }
'''


def test_composite_is_weighted_average_of_success(amp):
    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    # Positive referential input (Cz=5, linked_ears=0) → raw = 5, rectified = 5
    # → smr sigmoid(5, midpoint=0, steepness=1000) ≈ 1; theta sigmoid ≈ 1
    # → suppress contributes (1 - 1) = 0. composite = (1*1 + 1*0)/(1+1) = 0.5.
    chunk = np.column_stack([np.full(64, 5.0), np.zeros(64)]).astype(np.float64)
    ev.step_chunk(chunk)
    comp = ev.last_streams()["reward.composite"]
    assert np.allclose(comp, 0.5, atol=1e-3)


def test_composite_reweight_via_set_control(amp):
    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    chunk = np.column_stack([np.full(64, 5.0), np.zeros(64)]).astype(np.float64)
    # Drop the suppress weight to 0 → composite = (1*1)/(1) = 1.0.
    ev.set_control("w_theta", 0.0)
    ev.step_chunk(chunk)
    comp = ev.last_streams()["reward.composite"]
    assert np.allclose(comp, 1.0, atol=1e-3)


def test_composite_exposed_in_taps_and_streams(amp):
    ir = resolve(parse(_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    ev.step_chunk(np.column_stack([np.full(64, 5.0), np.zeros(64)]).astype(np.float64))
    taps = ev.last_taps()
    assert "reward/composite" in taps
    assert abs(taps["reward/composite"] - 0.5) < 1e-3
    assert "reward/component[smr]" in taps
    assert "reward/component[theta]" in taps
    streams = ev.last_streams()
    assert "reward.composite" in streams


# Regression: reward.composite used as a dwell *condition* must reach the event
# path. The composite/component kwargs have to thread through _eval_reward_event
# (and its all_of/any_of + single-condition branches), or the condition silently
# evaluates to zeros and the event never fires.
_EVENT_PROTO = _PROTO.replace(
    'reward { combine = "weighted"; continuous = reward.composite }',
    'reward { combine = "weighted"; event = dwell(condition: above(reward.composite, 0.3), duration: 10 ms) }',
).replace(
    "output { audio_gain = reward.composite }",
    "output { audio_chime = reward.event }",
)


def test_composite_drives_dwell_event(amp):
    ir = resolve(parse(_EVENT_PROTO), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    # composite ≈ 0.5 > 0.3 held across the whole chunk → dwell (10 ms ≈ 3
    # samples) fires and holds.
    chunk = np.column_stack([np.full(64, 5.0), np.zeros(64)]).astype(np.float64)
    events = ev.step_chunk(chunk)
    assert ev.last_taps()["reward/event.holds"] is True
    assert any(e.channel == "audio_chime" and e.kind == "event" for e in events)


def test_composite_dwell_event_does_not_fire_below_threshold(amp):
    # composite ≈ 0.5 < 0.7 → never crosses → event must not fire (guards
    # against the condition being stuck "true"/ignored).
    proto = _EVENT_PROTO.replace("above(reward.composite, 0.3)", "above(reward.composite, 0.7)")
    ir = resolve(parse(proto), amp)
    ev = Evaluator.live(ir, sample_rate_hz=256.0, channel_names=("Cz", "linked_ears"),
                        record_streams=True, backend="python")
    ev.start(skip_warmup=True)
    chunk = np.column_stack([np.full(64, 5.0), np.zeros(64)]).astype(np.float64)
    events = ev.step_chunk(chunk)
    assert ev.last_taps()["reward/event.holds"] is False
    assert not any(e.channel == "audio_chime" for e in events)
