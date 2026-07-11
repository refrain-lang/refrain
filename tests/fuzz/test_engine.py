# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""engine.py — scenario execution, per-sample streams, quiet-envelope probe.

The load-bearing test here is `test_noise_is_bit_identical_across_amplitudes`:
the entire metamorphic tier rests on the sweep being a controlled A/B on ONE
noise realization. If that ever breaks, every sweep assertion silently becomes
a comparison across independent noisy runs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.fuzz.engine import (
    REWARD_HOLDS,
    measure_quiet_envelopes,
    run_scenario,
    time_in_reward,
)
from refrain.fuzz.scenario import BandSegment, PhaseOverride, Scenario, Tone
from refrain.fuzz.surface import build_surface
from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.synthetic import channels_for_synthetic, render_scenario
from refrain.sources import SyntheticSource

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ir(rel: str):
    return resolve(parse_file(REPO_ROOT / rel), None)


def _scenario(amp: float, *, total_s: float = 10.0) -> Scenario:
    segs = (
        (BandSegment(band=(12.0, 15.0), channel="Cz", start_s=2.0, end_s=8.0,
                     content=Tone(amplitude_uv=amp)),)
        if amp > 0 else ()
    )
    return Scenario(
        label=f"amp_{amp:g}", duration_s=total_s, sample_rate_hz=256,
        segments=segs, controls={}, coverage_tags=frozenset(),
        phase_override=PhaseOverride(1.0, total_s - 1.5, 0.5), seed=42,
    )


def _render(scenario, channels) -> np.ndarray:
    src = SyntheticSource(render_scenario(scenario, channels=channels),
                          duration_s=scenario.duration_s)
    return np.concatenate([c.copy() for c in src.iter_chunks(64)], axis=0)


def test_noise_is_bit_identical_across_amplitudes():
    """Same seed + same segments-except-tone-amplitude => the noise realization
    is byte-identical, and the difference is exactly the injected tone."""
    ir = _ir("bench/protocols/micro_single_pct.refrain")
    channels = channels_for_synthetic(ir)
    quiet = _render(_scenario(0.0), channels)
    driven = _render(_scenario(20.0), channels)

    # Outside the tone segment [2 s, 8 s): bit-identical, not merely close.
    assert np.array_equal(quiet[: 2 * 256], driven[: 2 * 256])
    assert np.array_equal(quiet[8 * 256 :], driven[8 * 256 :])

    # Inside: a pure 20 uV sinusoid on Cz only (channel 0), zero elsewhere.
    diff = driven[2 * 256 : 8 * 256] - quiet[2 * 256 : 8 * 256]
    assert np.abs(diff[:, 0]).max() == pytest.approx(20.0, rel=0.02)
    assert np.abs(diff[:, 1:]).max() == 0.0


def test_run_scenario_returns_per_sample_reward_holds():
    ir = _ir("bench/protocols/micro_single_pct.refrain")
    channels = channels_for_synthetic(ir)
    res = run_scenario(_scenario(20.0), ir=ir, channels=channels, chunk_size=64)

    holds = res.streams[REWARD_HOLDS]
    assert holds.shape == (10 * 256,)
    assert holds.dtype == np.bool_
    # The derive's envelope stream is exposed under its bare name.
    assert res.streams["up_env"].shape == (10 * 256,)
    assert holds.any(), "a 20 uV tone must drive the reward at some point"


def test_time_in_reward_is_the_fraction_of_the_window_holding():
    streams = {REWARD_HOLDS: np.array([0, 0, 1, 1, 1, 1, 0, 0], dtype=bool)}
    # window [2/8 s, 6/8 s) at fs=8 -> samples 2..6 -> all four are True.
    assert time_in_reward(streams, window_s=(0.25, 0.75), fs=8) == 1.0
    assert time_in_reward(streams, window_s=(0.0, 1.0), fs=8) == 0.5


def test_time_in_reward_rejects_an_empty_window():
    streams = {REWARD_HOLDS: np.zeros(8, dtype=bool)}
    with pytest.raises(ValueError, match="empty"):
        time_in_reward(streams, window_s=(0.5, 0.5), fs=8)


def test_time_in_reward_rejects_a_window_that_overruns_the_array():
    """A window partially past the end of the array must never be silently
    truncated: that would report on far fewer samples than requested and
    make a partial slice look like a complete measurement."""
    holds = np.zeros(2560, dtype=bool)  # 10 s @ 256 Hz
    holds[2432:] = True                 # held for the trailing 0.5 s only
    streams = {REWARD_HOLDS: holds}
    with pytest.raises(ValueError, match="2560"):
        # Requests window_s=(9.5, 12.0) -> samples [2432:3072), but the
        # array only has 2560 samples; must raise, not silently return 1.0
        # off the 128 in-range samples.
        time_in_reward(streams, window_s=(9.5, 12.0), fs=256)


def test_time_in_reward_rejects_a_negative_start():
    streams = {REWARD_HOLDS: np.zeros(8, dtype=bool)}
    with pytest.raises(ValueError, match="8"):
        time_in_reward(streams, window_s=(-1.0, 1.0), fs=8)


def test_measure_quiet_envelopes_returns_nonempty_positive_arrays_seed_stable():
    ir = _ir("bench/protocols/realistic_smr.refrain")
    surface = build_surface(ir)
    channels = channels_for_synthetic(ir)
    a = measure_quiet_envelopes(ir=ir, surface=surface, channels=channels,
                                chunk_size=64, fill_s=20.0, seed=42)
    b = measure_quiet_envelopes(ir=ir, surface=surface, channels=channels,
                                chunk_size=64, fill_s=20.0, seed=43)
    assert set(a) == {d.name for d in surface.derives}
    for name, arr in a.items():
        assert arr.size > 0
        assert np.all(arr > 0.0)
        med = float(np.median(arr))
        other_med = float(np.median(b[name]))
        # A median over ~16 s of quiet noise is stable across realizations.
        assert abs(med - other_med) / med < 0.25, (name, med, other_med)


def test_run_scenario_raises_when_stream_keys_vary_across_chunks(monkeypatch):
    """`run_scenario` concatenates each stream across chunks with
    `np.concatenate`. If `last_streams()` ever drops a key partway through a
    run (it captures `reward.event*` only `if <var> is not None` — see
    eval_.py), that key's array silently ends up SHORTER than its siblings,
    and every later index-aligned comparison (e.g. time_in_reward against the
    derive envelope) is quietly measuring misaligned samples. The invariant
    must be enforced loudly, not papered over."""
    ir = _ir("bench/protocols/micro_single_pct.refrain")
    channels = channels_for_synthetic(ir)
    scenario = _scenario(20.0, total_s=1.0)  # several chunks at chunk_size=64

    original_last_streams = Evaluator.last_streams
    calls = {"n": 0}

    def flaky_last_streams(self):
        streams = original_last_streams(self)
        calls["n"] += 1
        if calls["n"] > 1:
            streams = dict(streams)
            streams.pop(REWARD_HOLDS, None)
        return streams

    monkeypatch.setattr(Evaluator, "last_streams", flaky_last_streams)

    with pytest.raises(RuntimeError, match=REWARD_HOLDS):
        run_scenario(scenario, ir=ir, channels=channels, chunk_size=64)
