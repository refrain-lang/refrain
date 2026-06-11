# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Numerical correctness checks for each primitive implementation.

Each test uses a known signal (sinusoid at a specific frequency, step
input, etc.) and asserts an expected property of the output (band
attenuation, group delay, percentile value, dwell timing, etc.). These
are unit-level numerical sanity checks; end-to-end behaviour is in
`test_eval_validation.py`.
"""

from __future__ import annotations

import numpy as np
import pytest

from refrain.primitive_impls import (
    AboveImpl,
    AbsoluteThresholdImpl,
    AllOfImpl,
    AnyOfImpl,
    AutocorrImpl,
    BandpassImpl,
    BandpowerImpl,
    BelowImpl,
    BipolarImpl,
    CoherenceImpl,
    DifferentiateImpl,
    DwellImpl,
    HilbertFirImpl,
    HilbertIirAllpassImpl,
    InsideImpl,
    LinearImpl,
    MagnitudeImpl,
    MuteAction,
    PercentileImpl,
    RectifyImpl,
    ReferentialImpl,
    SigmoidImpl,
    SmoothImpl,
)


SR = 256.0


def _sine(freq_hz: float, n: int, amplitude: float = 10.0) -> np.ndarray:
    t = np.arange(n) / SR
    return amplitude * np.sin(2 * np.pi * freq_hz * t)


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


def test_bipolar_subtracts_channels():
    impl = BipolarImpl(plus="A", minus="B", channel_names=("A", "B", "C"))
    chunk = np.array([[1.0, 2.0, 99.0], [3.0, 4.0, 99.0]])
    out = impl.step(chunk)
    assert np.allclose(out, np.array([-1.0, -1.0]))


def test_bipolar_unknown_channel_raises():
    with pytest.raises(ValueError, match="not in source"):
        BipolarImpl(plus="X", minus="A", channel_names=("A", "B"))


def test_referential_single_active_reference():
    impl = ReferentialImpl(active="A", reference="B", channel_names=("A", "B"))
    chunk = np.array([[10.0, 5.0], [20.0, 8.0]])
    assert np.allclose(impl.step(chunk), np.array([5.0, 12.0]))


def test_referential_linked_ears_averages_a1_a2():
    impl = ReferentialImpl(active="Cz", reference="linked_ears",
                            channel_names=("Cz", "A1", "A2"))
    chunk = np.array([[10.0, 1.0, 3.0]])  # mean(A1, A2) = 2.0
    assert impl.step(chunk)[0] == pytest.approx(10.0 - 2.0)


def test_referential_linked_ears_fallback_to_common_average():
    """When no A1/A2/etc. in the source, falls back to common-average."""
    impl = ReferentialImpl(active="Cz", reference="linked_ears",
                            channel_names=("Cz", "Pz", "Fz"))
    chunk = np.array([[10.0, 4.0, 4.0]])  # common-avg = 6
    assert impl.step(chunk)[0] == pytest.approx(10.0 - 6.0)


def test_referential_common_average_explicit():
    impl = ReferentialImpl(active="A", reference="common_average",
                            channel_names=("A", "B", "C"))
    chunk = np.array([[3.0, 6.0, 9.0]])  # avg = 6
    assert impl.step(chunk)[0] == pytest.approx(3.0 - 6.0)


# ---------------------------------------------------------------------------
# Bandpass — each filter family
# ---------------------------------------------------------------------------


def test_butterworth_passes_in_band_attenuates_out_of_band():
    in_band = _sine(13.0, n=2048)
    out_of_band = _sine(50.0, n=2048)
    bp = BandpassImpl(band=(12.0, 15.0), order=4, kind="butterworth", sample_rate_hz=SR)
    # Warm up filter state.
    bp.step(np.zeros(512))
    y_in = bp.step(in_band)
    bp2 = BandpassImpl(band=(12.0, 15.0), order=4, kind="butterworth", sample_rate_hz=SR)
    bp2.step(np.zeros(512))
    y_out = bp2.step(out_of_band)
    in_rms = np.sqrt(np.mean(y_in[-1024:] ** 2))
    out_rms = np.sqrt(np.mean(y_out[-1024:] ** 2))
    # Use last 1024 samples to skip the filter's settling transient.
    assert in_rms > 5.0, f"in-band attenuated too hard: {in_rms:.2f}"
    assert out_rms < 0.5, f"out-of-band leaked through: {out_rms:.2f}"


def test_bessel_constant_group_delay():
    """Bessel's defining feature: roughly constant group delay in the
    passband. We test the weaker property that the steady-state output
    on a passband sinusoid has the expected envelope amplitude."""
    bp = BandpassImpl(band=(12.0, 15.0), order=4, kind="bessel", sample_rate_hz=SR)
    bp.step(np.zeros(1024))
    sig = _sine(13.5, n=2048, amplitude=10.0)
    y = bp.step(sig)
    steady_rms = np.sqrt(np.mean(y[-512:] ** 2))
    # Sine RMS is amplitude / sqrt(2) ≈ 7.07. Should pass through.
    assert 4.0 < steady_rms < 8.5, f"bessel attenuated unexpectedly: rms={steady_rms:.2f}"


def test_chebyshev2_meets_stopband_attenuation():
    """Cheby II's attenuation_db param is the stopband floor. A signal
    far outside the band should be attenuated by ~attenuation_db dB."""
    bp = BandpassImpl(
        band=(12.0, 15.0), order=4, kind="chebyshev2", attenuation_db=40,
        sample_rate_hz=SR,
    )
    bp.step(np.zeros(2048))
    far_out = _sine(60.0, n=4096, amplitude=10.0)
    y = bp.step(far_out)
    in_rms = np.sqrt(np.mean(far_out[-2048:] ** 2))
    out_rms = np.sqrt(np.mean(y[-2048:] ** 2))
    attenuation_db = 20 * np.log10(in_rms / max(out_rms, 1e-9))
    # 40 dB target; cheby2 hits this in the stopband at moderate orders.
    # Conservative test: attenuation >= 30 dB.
    assert attenuation_db > 30, f"cheby2 stopband attenuation only {attenuation_db:.1f} dB"


def test_unknown_bandpass_kind_raises():
    with pytest.raises(ValueError, match="unknown kind"):
        BandpassImpl(band=(12.0, 15.0), kind="quincy", sample_rate_hz=SR)


def test_bandpass_center_bandwidth_form():
    """center=13.5, bandwidth=ratio(2.5) gives edges at (13.5/sqrt(2.5), 13.5*sqrt(2.5))."""
    bp = BandpassImpl(
        center_hz=13.5, bandwidth_ratio=2.5, order=4, kind="butterworth",
        sample_rate_hz=SR,
    )
    bp.step(np.zeros(1024))
    sig = _sine(13.5, n=2048, amplitude=10.0)
    y = bp.step(sig)
    steady_rms = np.sqrt(np.mean(y[-512:] ** 2))
    # Should pass at center.
    assert steady_rms > 3.0, f"center-bandwidth form attenuated at its own center: {steady_rms:.2f}"


# ---------------------------------------------------------------------------
# Hilbert
# ---------------------------------------------------------------------------


def test_hilbert_fir_envelope_of_am_modulated_signal():
    """Hilbert + magnitude recovers the envelope of an AM-modulated
    sinusoid. Carrier at 13.5 Hz, envelope a slow ramp."""
    n = 4096
    t = np.arange(n) / SR
    envelope = 5.0 + 0.5 * np.sin(2 * np.pi * 0.5 * t)  # slow 0.5 Hz envelope
    carrier = envelope * np.sin(2 * np.pi * 13.5 * t)
    hil = HilbertFirImpl(taps=65, sample_rate_hz=SR)
    mag = MagnitudeImpl()
    # Skip initial transient (group delay + a bit).
    skip = 200
    z = hil.step(carrier)
    env_est = mag.step(z)
    # The estimated envelope (after transient) should track the true
    # envelope within ~20%.
    true_env_skipped = envelope[skip:]
    est_env_skipped = env_est[skip:]
    ratio = est_env_skipped / true_env_skipped
    assert 0.8 < np.median(ratio) < 1.2, (
        f"hilbert envelope tracking is off: median ratio {np.median(ratio):.2f}"
    )


def test_hilbert_iir_allpass_not_implemented():
    """The Phase 0d evaluator deliberately defers iir_allpass; the
    language accepts the kind but the impl is NotImplemented."""
    with pytest.raises(NotImplementedError, match="iir_allpass"):
        HilbertIirAllpassImpl()


# ---------------------------------------------------------------------------
# Time-series math
# ---------------------------------------------------------------------------


def test_magnitude_of_complex():
    impl = MagnitudeImpl()
    x = np.array([3.0 + 4.0j, 1.0 + 0.0j, 0.0 + 1.0j])
    assert np.allclose(impl.step(x), [5.0, 1.0, 1.0])


def test_rectify_of_real():
    impl = RectifyImpl()
    assert np.allclose(impl.step(np.array([-3.0, 2.0, -0.5])), [3.0, 2.0, 0.5])


def test_smooth_step_response_settles_to_input_value():
    impl = SmoothImpl(tau_ms=100.0, sample_rate_hz=SR)
    # 5 seconds of constant 1.0 input — output should reach ~1.0.
    y = impl.step(np.ones(1280))
    assert y[-1] == pytest.approx(1.0, rel=1e-3)


def test_smooth_at_one_tau_reaches_63pct():
    """One time constant → output reaches (1 - 1/e) ≈ 0.632 of input."""
    tau_ms = 100.0
    impl = SmoothImpl(tau_ms=tau_ms, sample_rate_hz=SR)
    n_samples_one_tau = int(tau_ms / 1000.0 * SR)
    y = impl.step(np.ones(n_samples_one_tau))
    # Expected value at exactly one tau, for one-pole IIR with α = 1−exp(−1/Nτ),
    # is ~0.632. Tolerance for discretisation noise.
    assert 0.55 < y[-1] < 0.68, f"one-tau response = {y[-1]:.3f}, expected ~0.632"


def test_differentiate_constant_is_zero():
    impl = DifferentiateImpl(sample_rate_hz=SR)
    y = impl.step(np.full(64, 7.0))
    # After initial transient, derivative of a constant is zero.
    assert np.all(np.abs(y[5:]) < 1e-9)


def test_differentiate_linear_ramp_is_constant():
    impl = DifferentiateImpl(sample_rate_hz=SR)
    t = np.arange(128) / SR
    ramp = 3.0 * t  # slope 3 in input-units per second
    y = impl.step(ramp)
    # Settled derivative should be ≈ 3.0 (centered finite difference).
    assert np.allclose(y[5:], 3.0, atol=0.1)


# ---------------------------------------------------------------------------
# Percentile
# ---------------------------------------------------------------------------


def test_percentile_tracks_window_distribution():
    impl = PercentileImpl(target_pct=50.0, window_ms=1000.0, sample_rate_hz=SR)
    # Feed a uniform distribution; 50th percentile should approach 0.5.
    rng = np.random.default_rng(42)
    chunk = rng.uniform(0, 1, size=512)
    y = impl.step(chunk)
    # After warm-up, percentile estimate should be near 0.5.
    assert 0.4 < y[-1] < 0.6


def test_percentile_with_constant_input_returns_constant():
    impl = PercentileImpl(target_pct=70.0, window_ms=500.0, sample_rate_hz=SR)
    y = impl.step(np.full(256, 7.0))
    assert y[-1] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# Thresholds + conditions
# ---------------------------------------------------------------------------


def test_absolute_threshold_emits_constant():
    impl = AbsoluteThresholdImpl(value=8.0)
    y = impl.step(np.zeros(10))
    assert np.all(y == 8.0)


def test_above_below_inside():
    above = AboveImpl()
    below = BelowImpl()
    inside = InsideImpl(low=2.0, high=5.0)
    signal = np.array([1.0, 3.0, 6.0])
    threshold = np.array([2.0, 2.0, 5.0])
    assert list(above.step(signal, threshold)) == [False, True, True]
    assert list(below.step(signal, threshold)) == [True, False, False]
    assert list(inside.step(signal)) == [False, True, False]


def test_all_of_any_of():
    a = np.array([True, True, False, False])
    b = np.array([True, False, True, False])
    c = np.array([True, False, False, False])
    assert list(AllOfImpl().step(a, b, c)) == [True, False, False, False]
    assert list(AnyOfImpl().step(a, b, c)) == [True, True, True, False]


# ---------------------------------------------------------------------------
# Dwell
# ---------------------------------------------------------------------------


def test_dwell_fires_event_on_rising_edge_of_sustained_condition():
    impl = DwellImpl(duration_ms=100.0, sample_rate_hz=SR)
    # 100 ms × 256 Hz rounds to 26 samples; streak must reach 26 before
    # the rising-edge event fires.
    expected_dwell = impl.dwell_samples
    cond = np.array([True] * (expected_dwell + 25) + [False] * 10)
    result = impl.step(cond)
    assert result.events.sum() == 1, "expected exactly one rising-edge event"
    event_idx = int(np.argmax(result.events))
    assert event_idx == expected_dwell - 1, (
        f"event should fire when streak first reaches {expected_dwell}, "
        f"got idx={event_idx}"
    )
    assert result.holds[event_idx]
    # After condition goes false, holds returns to false.
    assert not result.holds[expected_dwell + 25 + 5]


def test_dwell_no_event_if_condition_resets_before_duration():
    """Condition flickers true/false; streak resets each time, no event."""
    impl = DwellImpl(duration_ms=100.0, sample_rate_hz=SR)
    short_run = impl.dwell_samples - 1
    cond = np.tile([True] * short_run + [False], 4)
    result = impl.step(cond)
    assert result.events.sum() == 0


def test_dwell_state_persists_across_chunks():
    """Streak counter carries from one chunk to the next."""
    impl = DwellImpl(duration_ms=100.0, sample_rate_hz=SR)
    # Split the streak across chunks: ceil(dwell/2) and ceil(dwell/2)+1
    # so the rising edge fires inside chunk 2.
    half = impl.dwell_samples // 2
    chunk1 = np.array([True] * half)
    chunk2 = np.array([True] * (impl.dwell_samples - half + 5))
    r1 = impl.step(chunk1)
    r2 = impl.step(chunk2)
    assert r1.events.sum() == 0
    assert r2.events.sum() == 1


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------


def test_sigmoid_at_midpoint_is_half():
    impl = SigmoidImpl(midpoint=5.0, steepness=2.0)
    assert impl.step(np.array([5.0]))[0] == pytest.approx(0.5)


def test_sigmoid_saturates_to_zero_one():
    impl = SigmoidImpl(midpoint=0.0, steepness=10.0)
    y = impl.step(np.array([-5.0, 5.0]))
    assert y[0] < 0.01
    assert y[1] > 0.99


def test_linear_slope_works():
    impl = LinearImpl(midpoint=1.0, slope=2.0)
    assert np.allclose(impl.step(np.array([0.0, 1.0, 2.0])), [-2.0, 0.0, 2.0])


# ---------------------------------------------------------------------------
# Inhibit action — mute
# ---------------------------------------------------------------------------


def test_mute_holds_for_release_after_active_clears():
    action = MuteAction(release_ms=100.0, sample_rate_hz=SR)
    # ~25 samples of release at 256 Hz, 100 ms.
    # Inhibit active for samples 0..9, then inactive.
    inhibit = np.array([True] * 10 + [False] * 50)
    muted = action.gate(inhibit)
    # While inhibit is active, definitely muted.
    assert all(muted[:10])
    # After inhibit clears, mute holds for `release_samples` more.
    # release_ms=100ms at 256Hz → 25 samples. After sample 10 there should
    # be 25 more samples of mute, then unmuted.
    assert muted[10 + 24]   # still within release
    assert not muted[10 + 26]  # past release


# ---------------------------------------------------------------------------
# Bandpower (light test — full Welch correctness is out of scope)
# ---------------------------------------------------------------------------


def test_bandpower_in_band_signal_has_positive_power():
    impl = BandpowerImpl(band=(50.0, 100.0), window_ms=100.0, sample_rate_hz=SR)
    # 60 Hz sine — squarely in band.
    sig = _sine(60.0, n=2048, amplitude=10.0)
    y = impl.step(sig)
    # After warm-up, power should be > 0.
    assert y[-1] > 0.0


def test_bandpower_out_of_band_has_low_power():
    impl = BandpowerImpl(band=(50.0, 100.0), window_ms=100.0, sample_rate_hz=SR)
    # 5 Hz — well outside the band.
    sig = _sine(5.0, n=2048, amplitude=10.0)
    y = impl.step(sig)
    impl2 = BandpowerImpl(band=(50.0, 100.0), window_ms=100.0, sample_rate_hz=SR)
    sig_in = _sine(75.0, n=2048, amplitude=10.0)
    y_in = impl2.step(sig_in)
    # Out-of-band power should be much lower than in-band.
    assert y[-1] < y_in[-1] * 0.1


# ---------------------------------------------------------------------------
# Coherence — magnitude-squared coherence between two streams
# ---------------------------------------------------------------------------


def test_coherence_identical_signals_yields_one():
    """Two identical streams have MSC = 1.0 (a signal is perfectly
    coherent with itself)."""
    impl = CoherenceImpl(band=(8.0, 12.0), window_ms=2000.0, sample_rate_hz=SR)
    sig = _sine(10.0, n=4096, amplitude=10.0)
    out = impl.step(sig, sig)
    assert out[-1] > 0.95, f"MSC for identical signals should be ~1.0, got {out[-1]:.3f}"


def test_coherence_independent_noise_is_low():
    """Independent white noise in both channels should give low MSC —
    the 7-segment averaging noise floor is approximately 1/n_segments."""
    rng = np.random.default_rng(0)
    impl = CoherenceImpl(band=(8.0, 12.0), window_ms=2000.0, sample_rate_hz=SR)
    a = rng.standard_normal(4096)
    b = rng.standard_normal(4096)
    out = impl.step(a, b)
    assert out[-1] < 0.4, (
        f"MSC for independent noise should be < 0.4 with 7-segment averaging, "
        f"got {out[-1]:.3f}"
    )


def test_coherence_band_selective():
    """Streams sharing a 10 Hz alpha component plus independent broadband
    noise have high MSC in the alpha band, low MSC everywhere else.

    Note: a fixed-phase sinusoid (even one with random initial phase)
    remains coherent over time because coherence measures phase
    *consistency*, not phase *value*. To make beta-band content
    incoherent, the contribution must be independent broadband noise,
    not synchronized tones with a fixed offset.
    """
    rng = np.random.default_rng(1)
    t = np.arange(4096) / SR
    shared_alpha = np.sin(2 * np.pi * 10 * t)
    # Independent broadband noise on each channel — incoherent at every
    # frequency including beta.
    noise_a = rng.standard_normal(4096)
    noise_b = rng.standard_normal(4096)
    a = shared_alpha + 0.5 * noise_a
    b = shared_alpha + 0.5 * noise_b

    impl_alpha = CoherenceImpl(band=(8.0, 12.0), window_ms=2000.0, sample_rate_hz=SR)
    impl_beta = CoherenceImpl(band=(20.0, 30.0), window_ms=2000.0, sample_rate_hz=SR)
    msc_alpha = impl_alpha.step(a, b)[-1]
    msc_beta = impl_beta.step(a, b)[-1]

    assert msc_alpha > 0.7, f"alpha MSC should be > 0.7, got {msc_alpha:.3f}"
    assert msc_beta < 0.4, f"beta MSC should be < 0.4, got {msc_beta:.3f}"
    assert msc_alpha > msc_beta + 0.3


def test_coherence_warmup_returns_zero():
    """Before the buffer accumulates enough samples for multi-segment
    Welch, MSC should be 0.0 (matches BandpowerImpl's warmup convention,
    not NaN — downstream comparisons would mishandle NaN)."""
    impl = CoherenceImpl(band=(8.0, 12.0), window_ms=2000.0, sample_rate_hz=SR)
    # 50 samples — well below the 96-sample minimum for 2-segment Welch.
    out = impl.step(np.ones(50), np.ones(50))
    assert np.all(out == 0.0)


def test_coherence_constant_input_returns_zero_not_nan():
    """Constant or all-zero inputs make Welch's denominators zero,
    producing NaN per bin. The impl should coerce to 0.0."""
    impl = CoherenceImpl(band=(8.0, 12.0), window_ms=2000.0, sample_rate_hz=SR)
    out = impl.step(np.zeros(4096), np.zeros(4096))
    assert np.all(np.isfinite(out))
    assert out[-1] == 0.0


def test_coherence_streaming_matches_single_shot():
    """Feeding the same data chunked vs. single-shot must produce the
    same MSC for the trailing window (the only window both have seen)."""
    rng = np.random.default_rng(42)
    a = rng.standard_normal(4096)
    b = 0.7 * a + 0.3 * rng.standard_normal(4096)  # partially coherent

    impl1 = CoherenceImpl(band=(0.0, 50.0), window_ms=2000.0, sample_rate_hz=SR)
    out_single = impl1.step(a, b)[-1]

    impl2 = CoherenceImpl(band=(0.0, 50.0), window_ms=2000.0, sample_rate_hz=SR)
    out_chunked = None
    for i in range(0, 4096, 64):
        out_chunked = impl2.step(a[i:i + 64], b[i:i + 64])[-1]
    assert abs(out_single - out_chunked) < 1e-9, (
        f"streaming vs single-shot MSC drift: {out_single:.6f} vs {out_chunked:.6f}"
    )


def test_coherence_band_outside_nyquist_rejected():
    """Band edges above Nyquist must be rejected at construction."""
    with pytest.raises(ValueError, match="nyquist"):
        CoherenceImpl(band=(50.0, 150.0), window_ms=2000.0, sample_rate_hz=SR)


def test_coherence_window_too_short_rejected():
    """Window must accommodate ≥ 2 segments of multi-segment Welch.
    With nperseg = SR/4 (~62 samples at SR=250), the minimum is ~500 ms.
    A 100 ms window must be rejected with a clear diagnostic."""
    with pytest.raises(ValueError, match="window must be"):
        CoherenceImpl(band=(8.0, 12.0), window_ms=100.0, sample_rate_hz=SR)


# ---------------------------------------------------------------------------
# autocorr — rolling lag-k Pearson autocorrelation (critical slowing down)
# ---------------------------------------------------------------------------


def test_autocorr_warmup_returns_zero():
    """Before lag+2 samples accumulate, autocorr returns 0.0."""
    impl = AutocorrImpl(window_ms=1000.0, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(np.array([1.0, 2.0]))  # n=1 then n=2; both < lag+2 == 3
    assert out[0] == 0.0 and out[1] == 0.0


def test_autocorr_white_noise_near_zero():
    """Lag-1 autocorrelation of white noise is ~0."""
    rng = np.random.default_rng(0)
    x = rng.standard_normal(4000)
    impl = AutocorrImpl(window_ms=1000.0, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(x)
    assert abs(out[-1]) < 0.15  # last (fully warm) sample


def test_autocorr_smooth_signal_near_one():
    """A slowly-varying (highly autocorrelated) signal -> lag-1 ac near 1."""
    t = np.arange(4000) / SR
    x = np.sin(2 * np.pi * 0.5 * t)  # 0.5 Hz: adjacent samples nearly identical
    impl = AutocorrImpl(window_ms=1000.0, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(x)
    assert out[-1] > 0.9


def test_autocorr_constant_input_is_zero():
    """Constant input (zero variance, den=0) -> 0.0, not NaN; output bounded."""
    impl = AutocorrImpl(window_ms=1000.0, lag_samples=1, sample_rate_hz=SR)
    out = impl.step(np.full(1000, 3.0))
    assert np.all(out == 0.0)


def test_autocorr_streaming_matches_single_shot():
    """Chunked stepping == one-shot over the same samples (persistent buffer)."""
    rng = np.random.default_rng(1)
    x = rng.standard_normal(2000)
    a = AutocorrImpl(window_ms=500.0, lag_samples=2, sample_rate_hz=SR)
    b = AutocorrImpl(window_ms=500.0, lag_samples=2, sample_rate_hz=SR)
    one = a.step(x)
    chunked = np.concatenate([b.step(x[i:i + 64]) for i in range(0, len(x), 64)])
    np.testing.assert_allclose(one, chunked, atol=1e-12)
