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
