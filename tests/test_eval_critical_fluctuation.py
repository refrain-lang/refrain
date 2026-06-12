# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""End-to-end behavioral test for the critical-fluctuation cue mechanism.

A single-band, single-site early-warning detector (the same env -> variance +
autocorr -> fused EWS score -> adaptive-percentile inhibit -> mute chain as
examples/critical_fluctuation_cue.refrain, minus the band/site fan-out for
speed) is run over synthetic EEG. We assert the **cue** engages: tonic
`audio_gain` (1.0) that the inhibit briefly mutes to 0.0 once the adaptive
baseline is warm.

This is the integration check that the constant `reward = 1.0` is actually muted
by an inhibit (the "no operant target, only an informational cue" design) —
verified on both the Python and Rust backends via the `backend` fixture.
"""

from __future__ import annotations

import numpy as np

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

SR = 256

# Minimal early-warning detector: one band (alpha), one site, short windows so
# the adaptive baseline warms quickly. rectify("score") is an identity on the
# [0,1] score (an inhibit metric must be a primitive call).
_CRIT_FLUCT_MIN = """
    protocol "crit_fluct_min" {
      meta { version = "1.0"; evidence = "demo"; description = "single-band critical-fluctuation detector" }
      requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
      input "raw" { montage = referential(active: "Cz", reference: "device") }
      derive "env"       { from = "raw"; pipeline = [bandpass(band: (8 Hz, 12 Hz), order: 4), hilbert(), magnitude()] }
      derive "esq"       { formula = "env" * "env" }
      derive "mean_esq"  { from = "esq"; pipeline = [smooth(tau: 200 ms)] }
      derive "mean_e"    { from = "env"; pipeline = [smooth(tau: 200 ms)] }
      derive "mean_e_sq" { formula = "mean_e" * "mean_e" }
      derive "var"       { formula = "mean_esq" - "mean_e_sq" }
      derive "varN"      { from = "var"; pipeline = [auto_range(window: 5 s)] }
      derive "ac1"       { from = "env"; pipeline = [autocorr(lag: 60 ms, window: 1 s)] }
      derive "acN"       { from = "ac1"; pipeline = [auto_range(window: 5 s)] }
      derive "score"     { formula = "varN" + "acN" - "varN" * "acN" }
      inhibit "critical_fluctuation" { metric = rectify("score"); threshold = percentile(target_pct: 80, window: 5 s); action = mute(release: 300 ms) }
      reward { continuous = 1.0 }
      output { audio_gain = reward.continuous }
    }
"""


def _amplitude_modulated_alpha(*, n_samples: int, seed: int = 0) -> np.ndarray:
    """One channel: a 10 Hz alpha rhythm whose amplitude swells and fades
    (0.3 Hz modulation) plus noise — a non-stationary envelope that drives both
    rising variance and autocorrelation (the early-warning signature)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) / SR
    amp = 3.0 + 2.5 * np.sin(2 * np.pi * 0.3 * t)
    sig = amp * np.sin(2 * np.pi * 10 * t) + 0.5 * rng.standard_normal(n_samples)
    return sig.reshape(n_samples, 1)


def test_critical_fluctuation_cue_engages(backend):
    ir = resolve(parse(_CRIT_FLUCT_MIN))
    n_samples = 40 * SR
    data = _amplitude_modulated_alpha(n_samples=n_samples, seed=42)

    ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("Cz",), backend=backend)
    ev.start(skip_warmup=True)

    times: list[float] = []
    gains: list[float] = []
    for i in range(0, n_samples, 64):
        chunk = data[i:i + 64]
        if chunk.shape[0] == 0:
            break
        for e in ev.step_chunk(chunk):
            if e.channel == "audio_gain" and e.kind == "value":
                times.append(e.timestamp_s)
                gains.append(e.value)

    times = np.array(times)
    gains = np.array(gains)
    assert len(gains) > 0, "no audio_gain events emitted"

    # Tonic audio: where not muted, audio_gain is the constant reward 1.0.
    assert np.isclose(gains.max(), 1.0), f"audio should be tonic 1.0, max={gains.max():.3f}"

    # Warm region (after the 5 s auto_range / percentile windows fill).
    warm = gains[times > 10.0]
    assert len(warm) > 0
    muted_frac = float(np.mean(warm < 0.5))

    # The cue engages: the constant reward IS muted by the inhibit (proves mute
    # gates a contingency-free output), at a sane self-calibrated rate — neither
    # never nor always.
    assert muted_frac > 0.02, f"cue never fired (muted_frac={muted_frac:.3f})"
    assert muted_frac < 0.6, f"audio muted almost constantly (muted_frac={muted_frac:.3f})"
    # And it is a genuine interruption: muted samples drop to ~0.
    assert float(warm.min()) < 0.01, f"muted audio_gain should reach 0, min={warm.min():.3f}"
