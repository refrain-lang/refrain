"""Idiomatic baseline for micro_05_reward.

Pipeline (per the protocol): two envelopes (SMR 12-15 Hz, theta 4-8 Hz), each
with a windowed-percentile threshold; a dwell event machine over
all_of([above(smr_env, smr_t), below(theta_env, theta_t)]); a sigmoid over the
ratio smr_env / smr_t; and the two output streams (audio_chime event channel,
audio_gain value channel). Every stage is a shared primitive from `_dsp.py`,
each of which mirrors the corresponding refrain primitive exactly.
"""

from __future__ import annotations

import numpy as np

from bench.baselines._dsp import (
    DwellMachine,
    Envelope,
    PercentileThreshold,
    linked_ears_montage,
    safe_ratio,
    sigmoid,
)


class Baseline:
    def __init__(self, *, sample_rate_hz: float, channel_names: tuple[str, ...]):
        self.channel_names = channel_names

        self.smr_env = Envelope(band=(12.0, 15.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.theta_env = Envelope(band=(4.0, 8.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.smr_t = PercentileThreshold(
            target_pct=70.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz
        )
        self.theta_t = PercentileThreshold(
            target_pct=30.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz
        )
        self.dwell = DwellMachine(duration_ms=250.0, sample_rate_hz=sample_rate_hz)

    def step(self, raw_chunk: np.ndarray) -> dict[str, np.ndarray]:
        raw = linked_ears_montage(raw_chunk, self.channel_names)

        smr_e = self.smr_env.step(raw)
        theta_e = self.theta_env.step(raw)
        smr_t = self.smr_t.step(smr_e)
        theta_t = self.theta_t.step(theta_e)

        # all_of([above(smr_env, smr_t), below(theta_env, theta_t)])
        condition = (smr_e > smr_t) & (theta_e < theta_t)

        # dwell rising-edge / holds state machine, persisted across chunks
        events, holds = self.dwell.step(condition)

        # ratio smr_env / smr_t, then sigmoid (midpoint 1.0, steepness 3)
        ratio = safe_ratio(smr_e, smr_t)
        continuous = sigmoid(ratio, midpoint=1.0, steepness=3.0)

        return {
            "raw": raw,
            "smr_envelope": smr_e,
            "theta_envelope": theta_e,
            "smr_t": smr_t,
            "theta_t": theta_t,
            "reward.continuous": continuous,
            "reward.event": events,
            "reward.event.holds": holds,
            "output/audio_chime": events,
            "output/audio_gain": np.clip(continuous, 0.0, 1.0),
        }
