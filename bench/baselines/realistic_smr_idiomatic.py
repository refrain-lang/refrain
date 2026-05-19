"""Idiomatic baseline for realistic_smr (= examples/smr_cz.refrain).

Three band envelopes + two percentile thresholds + one absolute threshold,
dwell over a 3-way condition, sigmoid reward, and ternary output gating
(reward.event.holds ? reward.continuous : 0). Uses the shared primitives in
`_dsp.py`.
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
    def __init__(self, *, sample_rate_hz, channel_names):
        self.channel_names = channel_names

        self.smr_env = Envelope(band=(12.0, 15.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.theta_env = Envelope(band=(4.0, 8.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.hbeta_env = Envelope(band=(22.0, 30.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)

        self.smr_t = PercentileThreshold(
            target_pct=70.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz
        )
        self.theta_t = PercentileThreshold(
            target_pct=30.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz
        )
        self.hbeta_value = 8.0

        self.dwell = DwellMachine(duration_ms=250.0, sample_rate_hz=sample_rate_hz)

    def step(self, raw_chunk):
        raw = linked_ears_montage(raw_chunk, self.channel_names)

        smr_e = self.smr_env.step(raw)
        theta_e = self.theta_env.step(raw)
        hbeta_e = self.hbeta_env.step(raw)

        smr_t = self.smr_t.step(smr_e)
        theta_t = self.theta_t.step(theta_e)
        n = raw.shape[0]
        hbeta_t = np.full(n, self.hbeta_value)

        # all_of([above(smr, smr_t), below(theta, theta_t), below(hbeta, hbeta_t)])
        condition = (smr_e > smr_t) & (theta_e < theta_t) & (hbeta_e < hbeta_t)

        # dwell rising-edge / holds state machine, persisted across chunks
        events, holds = self.dwell.step(condition)

        # ratio smr_env / smr_t, then sigmoid (midpoint 1.0, steepness 3)
        ratio = safe_ratio(smr_e, smr_t)
        continuous = sigmoid(ratio, midpoint=1.0, steepness=3.0)

        # output gating: audio_gain/game_speed = holds ? continuous : 0, then clamp
        gated = np.clip(np.where(holds, continuous, 0.0), 0.0, 1.0)

        return {
            "raw": raw,
            "smr_envelope": smr_e,
            "theta_envelope": theta_e,
            "high_beta_envelope": hbeta_e,
            "smr_t": smr_t,
            "theta_t": theta_t,
            "hbeta_t": hbeta_t,
            "reward.continuous": continuous,
            "reward.event": events,
            "reward.event.holds": holds,
            "output/audio_chime": events,
            "output/audio_gain": gated,
            "output/game_speed": gated,
        }
