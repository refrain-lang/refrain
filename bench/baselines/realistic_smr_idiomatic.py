"""Idiomatic baseline for realistic_smr (= examples/smr_cz.refrain).

Three band envelopes + two percentile thresholds + one absolute threshold,
dwell over a 3-way condition, sigmoid reward, and ternary output gating
(reward.event.holds ? reward.continuous : 0). Reuses _Envelope and
_PercentileThreshold from the micro_05 baseline.
"""

from __future__ import annotations

import numpy as np

from bench.baselines.micro_05_reward_idiomatic import _Envelope, _PercentileThreshold


class Baseline:
    def __init__(self, *, sample_rate_hz, channel_names):
        self.cz = channel_names.index("Cz")
        self.a1 = channel_names.index("A1")
        self.a2 = channel_names.index("A2")

        self.smr_env = _Envelope(band=(12.0, 15.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.theta_env = _Envelope(band=(4.0, 8.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)
        self.hbeta_env = _Envelope(band=(22.0, 30.0), tau_ms=250.0, sample_rate_hz=sample_rate_hz)

        self.smr_t = _PercentileThreshold(target_pct=70.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz)
        self.theta_t = _PercentileThreshold(target_pct=30.0, window_ms=120000.0, sample_rate_hz=sample_rate_hz)
        self.hbeta_value = 8.0

        # dwell state machine (DwellImpl): 250 ms @ rate
        self.dwell_samples = max(1, int(round(0.250 * sample_rate_hz)))
        self.streak = 0
        self.was_holding = False

    def step(self, raw_chunk):
        raw = raw_chunk[:, self.cz] - 0.5 * (raw_chunk[:, self.a1] + raw_chunk[:, self.a2])

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
        events = np.zeros(n, dtype=bool)
        holds = np.zeros(n, dtype=bool)
        for i, c in enumerate(condition):
            self.streak = self.streak + 1 if c else 0
            is_h = self.streak >= self.dwell_samples
            holds[i] = is_h
            if is_h and not self.was_holding:
                events[i] = True
            self.was_holding = is_h

        # ratio smr_env / smr_t. Mirror refrain's `/` binop: replace
        # non-finite results (e.g. 0/0 during warmup) with 0.0.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = smr_e / smr_t
        ratio = np.where(np.isfinite(ratio), ratio, 0.0)

        # sigmoid (midpoint 1.0, steepness 3)
        continuous = 1.0 / (1.0 + np.exp(-3.0 * (ratio - 1.0)))

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
