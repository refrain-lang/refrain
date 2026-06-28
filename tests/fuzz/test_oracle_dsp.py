# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the oracle's pure-DSP analytic primitives."""
from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter

from refrain.fuzz.oracle import (
    bandpass_gain_at,
    settle_time_s,
    tone_envelope_steady_state,
)


def _sos_smr(fs=256):
    nyq = fs / 2.0
    return butter(4, [12.0 / nyq, 15.0 / nyq], btype="band", output="sos").tolist()


def test_bandpass_gain_at_center_is_near_unity():
    sos = _sos_smr()
    gain = bandpass_gain_at(sos, freq_hz=13.5, fs=256)
    assert 0.7 <= gain <= 1.0, f"order-4 butter center gain expected ~1; got {gain}"


def test_bandpass_gain_well_outside_passband_is_small():
    sos = _sos_smr()
    g_low = bandpass_gain_at(sos, freq_hz=2.0, fs=256)
    g_high = bandpass_gain_at(sos, freq_hz=60.0, fs=256)
    assert g_low < 0.01, f"out-of-band low gain too high: {g_low}"
    assert g_high < 0.01, f"out-of-band high gain too high: {g_high}"


def test_tone_envelope_steady_state_matches_amplitude_times_gain():
    sos = _sos_smr()
    A = 30.0
    env = tone_envelope_steady_state(sos, freq_hz=13.5, amplitude_uv=A, fs=256)
    gain = bandpass_gain_at(sos, freq_hz=13.5, fs=256)
    assert env == pytest.approx(A * gain, rel=1e-6)


def test_settle_time_s_is_at_least_3_tau_plus_filter_decay():
    sos = _sos_smr()
    tau_s = 0.250
    chunk_s = 64 / 256.0
    settle = settle_time_s(sos=sos, tau_s=tau_s, chunk_s=chunk_s, fs=256)
    assert settle >= 3.0 * tau_s + chunk_s
    assert settle < 2.0
