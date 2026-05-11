# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""End-to-end evaluator validation.

Two layers:

  1. **Synthetic gating bar** (the deterministic CI test). Synthesise a
     60s signal with three scheduled SMR-band bursts at known times,
     run the SMR Cz protocol against it, verify reward events fire
     during bursts and audio_gain is higher during bursts than during
     quiet intervals.

  2. **Real-data smoke test** against `data/CRJA_20240228_EO.xdf` (a
     5-minute eyes-open Q21 recording shipped at the repo root, one
     level up from this package). Verifies the evaluator runs without
     NaN / crash and produces a reasonable reward-event rate on a
     resting baseline.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean

import numpy as np
import pytest

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import eval_protocol
from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.sources import SyntheticSource, open_source
from refrain.synthetic import SignalGenerator, SMRBurst


REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_PATH = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

# The XDF lives one directory up from the refrain package source root.
XDF_PATH = REPO.parent / "data" / "CRJA_20240228_EO.xdf"


# ---------------------------------------------------------------------------
# Synthetic gating bar
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"), load_amp_profile(AMP_PATH))


def _run_synthetic_smr(smr_ir, *, duration_s, bursts):
    """Helper: run SMR Cz against a synthetic signal with the given bursts."""
    gen = SignalGenerator(
        sample_rate_hz=256,
        channels=("Cz", "A1", "A2"),
        bursts=bursts,
        noise_uv_rms=10.0,
        seed=1,
    )
    src = SyntheticSource(gen, duration_s=duration_s)
    return list(eval_protocol(smr_ir, src, chunk_size=64))


def test_evaluator_runs_to_completion_on_synthetic(smr_ir):
    """Bedrock: 60s of synthetic input doesn't crash and produces some output."""
    events = _run_synthetic_smr(smr_ir, duration_s=60.0, bursts=())
    assert len(events) > 0
    # No NaNs.
    for e in events:
        if e.kind == "value":
            assert not np.isnan(e.value)


def test_audio_gain_clamped_to_zero_one(smr_ir):
    """Per SPEC §7.6, analog channels are clamped to [0, 1]."""
    events = _run_synthetic_smr(smr_ir, duration_s=30.0, bursts=())
    gains = [e.value for e in events if e.channel == "audio_gain" and e.kind == "value"]
    assert all(0.0 <= v <= 1.0 for v in gains), f"gain out of range: min={min(gains)}, max={max(gains)}"


def test_audio_gain_higher_during_smr_bursts(smr_ir):
    """The whole point. Bursts inject 13.5 Hz at 25 µV peak; baseline
    is pink noise at 10 µV RMS. Burst-window gain should exceed quiet."""
    bursts = (
        SMRBurst(start_s=15.0, end_s=18.0, center_hz=13.5, amplitude_uv=25.0, channel="Cz"),
        SMRBurst(start_s=30.0, end_s=33.0, center_hz=13.5, amplitude_uv=25.0, channel="Cz"),
        SMRBurst(start_s=45.0, end_s=48.0, center_hz=13.5, amplitude_uv=25.0, channel="Cz"),
    )
    events = _run_synthetic_smr(smr_ir, duration_s=60.0, bursts=bursts)
    gains = [(e.timestamp_s, e.value) for e in events
             if e.channel == "audio_gain" and e.kind == "value"]
    burst_windows = [(15.0, 18.0), (30.0, 33.0), (45.0, 48.0)]
    burst_gains = [v for t, v in gains
                   if any(s <= t < e for s, e in burst_windows)]
    quiet_gains = [v for t, v in gains
                   if 5.0 <= t < 14.0 or 22.0 <= t < 29.0 or 52.0 <= t < 59.0]
    assert mean(burst_gains) > mean(quiet_gains), (
        f"burst-window mean gain {mean(burst_gains):.3f} should exceed "
        f"quiet-window mean gain {mean(quiet_gains):.3f}"
    )


def test_reward_events_fire_during_bursts(smr_ir):
    """At least one reward event should fire during the burst windows.

    We don't assert a tight count because dwell + adaptive thresholds
    make per-burst event counts noisy. The bar: events do appear during
    burst windows, and they're rare without bursts."""
    burst_windows = [(15.0, 18.0), (30.0, 33.0), (45.0, 48.0)]
    bursts = tuple(
        SMRBurst(start_s=s, end_s=e, center_hz=13.5, amplitude_uv=30.0, channel="Cz")
        for s, e in burst_windows
    )
    events = _run_synthetic_smr(smr_ir, duration_s=60.0, bursts=bursts)
    chimes = [e.timestamp_s for e in events
              if e.channel == "audio_chime" and e.kind == "event"]
    # Inside any burst window OR within 1s after (dwell+filter latency).
    in_or_near_burst = [
        t for t in chimes
        if any(s <= t < e + 1.0 for s, e in burst_windows)
    ]
    assert len(chimes) >= 1, f"expected ≥1 chime event over 3 bursts, got {len(chimes)}"
    # Most events should be in/near bursts.
    if len(chimes) > 0:
        ratio = len(in_or_near_burst) / len(chimes)
        assert ratio >= 0.5, (
            f"only {ratio:.0%} of chime events near burst windows "
            f"(in/near={len(in_or_near_burst)}, total={len(chimes)})"
        )


def test_no_chimes_without_bursts(smr_ir):
    """Baseline pink noise should produce very few chime events.

    Not zero: the SMR Cz protocol's adaptive threshold means there's
    always a 30%-of-time floor where the protocol's conditions can be
    met. But over 60s of pink noise we shouldn't see runaway firing.
    """
    events = _run_synthetic_smr(smr_ir, duration_s=60.0, bursts=())
    chimes = [e for e in events if e.channel == "audio_chime" and e.kind == "event"]
    # 60s baseline EO should produce maybe a handful of false positives,
    # not 100. Loose bound — the math is reasonable, not micro-tuned.
    assert len(chimes) <= 30, (
        f"baseline produced {len(chimes)} chime events in 60s — too many"
    )


# ---------------------------------------------------------------------------
# Filter-family variants run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["butterworth", "bessel", "chebyshev2"])
def test_evaluator_runs_with_each_filter_kind(kind):
    """SMR-like protocol parameterized by filter kind, end-to-end."""
    src_text = f'''
        protocol "smr_kind_{kind}" {{
          meta {{ version = "1.0" }}
          requires {{ sample_rate = ">= 256 Hz"; channels = ["Cz"] }}
          input "raw" {{
            montage = referential(active: "Cz", reference: "linked_ears")
          }}
          derive "env" {{
            from = "raw"
            pipeline = [
              bandpass(band: (12 Hz, 15 Hz), order: 4, kind: "{kind}"),
              hilbert(),
              magnitude(),
              smooth(tau: 250 ms),
            ]
          }}
          threshold "t" {{
            signal = "env"
            type = absolute(5 uV)
          }}
          reward {{
            continuous = sigmoid("env", midpoint: 5 uV, steepness: 1)
            event = dwell(condition: above("env", "t"), duration: 250 ms)
          }}
          output {{
            audio_gain = reward.continuous
            audio_chime = reward.event
          }}
        }}
    '''
    from refrain.parser import parse
    ir = resolve(parse(src_text), load_amp_profile(AMP_PATH))
    gen = SignalGenerator(
        sample_rate_hz=256,
        channels=("Cz", "A1", "A2"),
        bursts=(SMRBurst(start_s=5.0, end_s=8.0, center_hz=13.5, amplitude_uv=25.0, channel="Cz"),),
        seed=1,
    )
    src = SyntheticSource(gen, duration_s=15.0)
    events = list(eval_protocol(ir, src, chunk_size=64))
    gains = [e.value for e in events if e.channel == "audio_gain" and e.kind == "value"]
    assert all(0.0 <= v <= 1.0 for v in gains)
    assert any(v > 0.5 for v in gains), f"{kind}: expected some gain > 0.5 during burst"


# ---------------------------------------------------------------------------
# Real-data smoke test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not XDF_PATH.exists(), reason="XDF fixture not available")
def test_smr_runs_on_real_xdf(smr_ir):
    """Run SMR Cz on the shipped 5-min eyes-open Q21 recording. The bar:
    no NaN, finite events, gain values in [0, 1]."""
    source = open_source(XDF_PATH)
    events = list(eval_protocol(smr_ir, source, chunk_size=64))
    assert len(events) > 0
    chimes = [e for e in events if e.channel == "audio_chime" and e.kind == "event"]
    gains = [e.value for e in events if e.channel == "audio_gain" and e.kind == "value"]
    # No NaN anywhere.
    assert not any(np.isnan(v) for v in gains)
    # Gain in [0, 1].
    assert all(0.0 <= v <= 1.0 for v in gains)
    # Chime rate is *some* positive but low value (EO is SMR-suppressed
    # compared to EC; ~0.1 events/sec is sane).
    duration_s = source.n_samples / source.sample_rate_hz
    rate = len(chimes) / duration_s
    assert 0 <= rate <= 5.0, f"chime rate {rate:.2f}/s on EO baseline is out of plausible range"
