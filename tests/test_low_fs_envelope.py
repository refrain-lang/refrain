# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Ask 1 (sanctioned low-Fs path): rectify()+smooth() as the envelope on a
low-sample-rate signal (the 4 Hz HRV tachogram).

At 4 Hz the FIR Hilbert's group delay is prohibitive (32 samples = 8 s), and a
low-latency IIR analytic signal is hard near DC (see test_hilbert_iir.py). For
the recorder's `bandpass(0.04-0.15 Hz) -> envelope -> smooth(4 s)` pipeline,
`rectify() + smooth(tau)` is the sanctioned substitute: it adds ~no latency
beyond the smooth the protocol already budgets, and recovers the rhythm's
amplitude envelope faithfully.
"""

from __future__ import annotations

import numpy as np

from refrain.primitive_impls import RectifyImpl, SmoothImpl


def test_rectify_smooth_tracks_lf_envelope():
    """A 0.1 Hz rhythm with a slow (100 s) amplitude modulation: rectify+smooth
    recovers the modulation. Validated correlation ~0.955 at tau=4 s (the
    recorder's smoothing); we assert a comfortable >0.9 floor."""
    fs = 4.0
    t = np.arange(int(400 * fs)) / fs
    carrier = np.sin(2 * np.pi * 0.1 * t)            # in-band LF rhythm
    env = 1.0 + 0.8 * np.sin(2 * np.pi * 0.01 * t)   # slow AM envelope
    x = env * carrier

    y = SmoothImpl(tau_ms=4000.0, sample_rate_hz=fs).step(RectifyImpl().step(x))

    settle = int(50 * fs)  # discard the smoother's warm-up transient
    corr = np.corrcoef(y[settle:], np.abs(env[settle:]))[0, 1]
    assert corr > 0.9
