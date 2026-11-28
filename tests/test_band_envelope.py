# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""`band_envelope(band:, latency:)` — one stage, one declared delay.

The shipped EEG envelope is `bandpass(order: 4) -> hilbert() -> magnitude()
-> smooth(tau)`: four stages, each contributing its own lag, so the feedback
delay is an emergent sum nobody chose. `band_envelope` replaces the four with
a single least-squares complex FIR whose delay is a *design input* (Smetanin
et al. 2020, J Neural Eng 17(4) 046022).

What these tests pin:
  - the declared latency is the delay the output actually realizes
  - the filter streams: chunked output is bit-identical to one-shot
  - a filter too short for its declared delay is refused, not silently run
    (at 250 Hz / 360 ms, 193 taps measures r=0.907 and 129 taps r=0.491 —
    an accuracy collapse with no symptom other than the number)
  - against real EEG, at the published band width and delay, we land inside
    the published interval (opt-in; see REFRAIN_EEG_CORPUS below)

This is the EEG case. The low-sample-rate HRV case is settled separately in
docs/DESIGN-NOTES.md §7a and tested in `test_low_fs_envelope.py`; nothing here
reopens it.
"""

from __future__ import annotations

import os

import numpy as np
import pytest
from scipy import signal as scisig

from refrain.cost import _iter_calls, estimate_cost
from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.primitive_impls import (
    BandEnvelopeImpl,
    HilbertFirImpl,
    MagnitudeImpl,
    SmoothImpl,
)
from refrain.resolver import resolve
from refrain.types_ import VOLTAGE, scalar_stream
from tests.conftest import RUST_BACKEND_ACTIVE

SR = 256.0


# ---------------------------------------------------------------------------
# Reference envelope + lag measurement
#
# The zero-phase reference is not realizable live (it reads the future); it is
# the accuracy target. Correlation against it at the lag that maximises that
# correlation is the metric both DESIGN-NOTES §7a and the paper use, which is
# what makes the numbers comparable.
# ---------------------------------------------------------------------------


def _zero_phase_envelope(x: np.ndarray, sr: float, band: tuple[float, float]) -> np.ndarray:
    sos = scisig.butter(4, [band[0] / (sr / 2), band[1] / (sr / 2)], btype="band", output="sos")
    return np.abs(scisig.hilbert(scisig.sosfiltfilt(sos, x)))


def _lag_and_corr(
    est: np.ndarray, truth: np.ndarray, sr: float, max_ms: float = 1400.0
) -> tuple[float, float]:
    """Lag (ms) maximising correlation with `truth`, and that correlation."""
    est = est[: len(truth)]
    truth = truth[: len(est)]
    a = (est - est.mean()) / (est.std() + 1e-12)
    b = (truth - truth.mean()) / (truth.std() + 1e-12)
    best_lag, best_corr = -1, -1.0
    for lag in range(int(max_ms / 1000 * sr)):
        n = len(a) - lag
        if n < sr:
            break
        corr = float(np.dot(a[lag : lag + n], b[:n]) / n)
        if corr > best_corr:
            best_lag, best_corr = lag, corr
    return best_lag / sr * 1000.0, best_corr


def _am_rhythm(
    band: tuple[float, float], seconds: float = 120.0, sr: float = SR, seed: int = 7
) -> np.ndarray:
    """An in-band rhythm with a slow, non-periodic amplitude envelope.

    A sinusoidal AM would make the lag search ambiguous (the envelope repeats),
    so the modulator is low-passed noise: one unique correlation peak.
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    carrier_hz = (band[0] + band[1]) / 2.0
    carrier = np.sin(2 * np.pi * carrier_hz * np.arange(n) / sr)
    slow = scisig.sosfilt(
        scisig.butter(2, 0.3 / (sr / 2), btype="low", output="sos"), rng.standard_normal(n)
    )
    env = 10.0 + 8.0 * slow / (np.std(slow) + 1e-12)
    return np.clip(env, 1.0, None) * carrier


def _edges(x: np.ndarray, sr: float, seconds: float = 6.0) -> slice:
    """Drop both ends: the filter's cold start and sosfiltfilt's edge effects."""
    return slice(int(seconds * sr), -int(seconds * sr))


# ---------------------------------------------------------------------------
# The declared latency is the realized latency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("latency_ms", [150.0, 250.0, 350.0])
def test_declared_latency_is_the_delay_the_output_realizes(latency_ms):
    """The whole point of the primitive: the author names the delay and gets it.

    In the shipped four-stage chain the delay is whatever the four stages sum
    to. Here the output's lag against the zero-phase reference must be the
    number in the source, within a couple of samples.
    """
    band = (12.0, 15.0)
    x = _am_rhythm(band)
    impl = BandEnvelopeImpl(band=band, latency_ms=latency_ms, sample_rate_hz=SR)

    y = impl.step(x)

    edge = _edges(x, SR)
    lag_ms, _ = _lag_and_corr(y[edge], _zero_phase_envelope(x, SR, band)[edge], SR)
    assert abs(lag_ms - latency_ms) <= 20.0, (
        f"declared {latency_ms} ms but the output lags by {lag_ms:.0f} ms"
    )


def test_tracks_the_amplitude_envelope_of_its_band():
    band = (12.0, 15.0)
    x = _am_rhythm(band)

    y = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR).step(x)

    edge = _edges(x, SR)
    _, corr = _lag_and_corr(y[edge], _zero_phase_envelope(x, SR, band)[edge], SR)
    assert corr > 0.8, f"envelope correlation {corr:.3f} against the zero-phase reference"


def test_envelope_is_in_the_input_s_units():
    """A 10 uV sinusoid at band centre must read 10 uV, the same as
    `bandpass -> hilbert -> magnitude` gives.

    Correlation against a reference envelope is scale-invariant, so it cannot
    see a calibration error. The ideal response, windowed, has a passband gain
    of about 2/3 of the analytic convention — left uncorrected the envelope
    reads low by a constant factor and every `absolute(uV)` threshold in a
    protocol converted to this primitive fires at the wrong amplitude.
    """
    band = (12.0, 15.0)
    n = int(60 * SR)
    x = 10.0 * np.sin(2 * np.pi * 13.5 * np.arange(n) / SR)

    y = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR).step(x)

    settled = y[int(6 * SR) :]
    assert settled.mean() == pytest.approx(10.0, rel=0.02), (
        f"a 10 uV rhythm reads {settled.mean():.2f} uV"
    )


def test_envelope_scale_agrees_with_the_four_stage_chain():
    """So that converting a protocol keeps its uV thresholds meaningful."""
    band = (12.0, 15.0)
    n = int(60 * SR)
    x = 10.0 * np.sin(2 * np.pi * 13.5 * np.arange(n) / SR)
    settled = slice(int(6 * SR), None)

    one_stage = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR).step(x)
    sos = scisig.butter(4, [band[0] / (SR / 2), band[1] / (SR / 2)], btype="band", output="sos")
    four_stage = MagnitudeImpl().step(
        HilbertFirImpl(taps=65, sample_rate_hz=SR).step(scisig.sosfilt(sos, x))
    )

    ratio = one_stage[settled].mean() / four_stage[settled].mean()
    assert ratio == pytest.approx(1.0, rel=0.05), f"envelope scale differs by {ratio:.3f}x"


def test_accuracy_depends_on_the_declared_latency_not_the_tap_count():
    """The primitive's whole proposition is that latency is the knob. If
    accuracy also swung with `taps`, the author would be tuning two things.

    Designing the filter by sampling an N-point DFT grid does swing: a 3 Hz
    band at 250 Hz covers two or three grid points, whose positions jitter
    with N (measured r ran 0.80-0.94 across tap counts at one fixed delay).
    The closed-form design does not.
    """
    band = (12.0, 15.0)
    x = _am_rhythm(band)
    truth = _zero_phase_envelope(x, SR, band)
    edge = _edges(x, SR)

    correlations = []
    for taps in (185, 203, 257, 385, 513):
        y = BandEnvelopeImpl(
            band=band, latency_ms=360.0, taps=taps, sample_rate_hz=SR
        ).step(x)
        correlations.append(_lag_and_corr(y[edge], truth[edge], SR)[1])

    spread = max(correlations) - min(correlations)
    assert spread < 0.02, f"r varies by {spread:.3f} across tap counts: {correlations}"


def test_output_is_a_real_nonnegative_envelope():
    band = (12.0, 15.0)
    y = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR).step(_am_rhythm(band))

    assert y.dtype == np.float64, "an envelope is a magnitude: real, not complex"
    assert np.all(y >= 0.0)
    assert np.all(np.isfinite(y))


def test_rejects_out_of_band_energy():
    """A 6 Hz theta rhythm must not show up in a 12-15 Hz envelope."""
    band = (12.0, 15.0)
    impl = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR)
    n = int(60 * SR)
    theta = 10.0 * np.sin(2 * np.pi * 6.0 * np.arange(n) / SR)
    in_band = 10.0 * np.sin(2 * np.pi * 13.5 * np.arange(n) / SR)

    edge = _edges(theta, SR)
    out_of_band = impl.step(theta)[edge].mean()
    passed = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR).step(in_band)[
        edge
    ].mean()

    assert out_of_band < 0.1 * passed


# ---------------------------------------------------------------------------
# Streaming with carried state
#
# The issue's reference code calls lfilter over a whole array. A primitive runs
# on 50 ms chunks, so the delay line has to persist — the `zi` property that
# BandpassImpl._zi provides for the IIR path.
# ---------------------------------------------------------------------------


def test_chunked_output_is_identical_to_one_shot():
    band = (12.0, 15.0)
    x = _am_rhythm(band, seconds=30.0)

    one_shot = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR).step(x)

    streamed_impl = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR)
    chunk = int(0.05 * SR)  # the evaluator's 50 ms chunk
    streamed = np.concatenate(
        [streamed_impl.step(x[i : i + chunk]) for i in range(0, len(x), chunk)]
    )

    assert np.allclose(streamed, one_shot, atol=1e-12), (
        "the delay line does not carry across chunks"
    )


def test_ragged_chunks_also_stream_correctly():
    """Chunk sizes vary in practice (BLE delivery is not on a tidy boundary)."""
    band = (8.0, 12.0)
    x = _am_rhythm(band, seconds=20.0, seed=11)

    one_shot = BandEnvelopeImpl(band=band, latency_ms=200.0, sample_rate_hz=SR).step(x)

    impl = BandEnvelopeImpl(band=band, latency_ms=200.0, sample_rate_hz=SR)
    out, i, rng = [], 0, np.random.default_rng(3)
    while i < len(x):
        n = int(rng.integers(1, 97))
        out.append(impl.step(x[i : i + n]))
        i += n

    assert np.allclose(np.concatenate(out), one_shot, atol=1e-12)


def test_cold_start_does_not_produce_garbage():
    """A 193-tap filter needs ~0.8 s of history before its output means
    anything (and makes #93 slightly worse). It must still be finite and
    non-negative from the first sample — protocols absorb the warm-up in
    their warmup phase, they do not get NaNs to render."""
    band = (12.0, 15.0)
    impl = BandEnvelopeImpl(band=band, latency_ms=250.0, sample_rate_hz=SR)

    first = impl.step(_am_rhythm(band, seconds=1.0)[: int(0.05 * SR)])

    assert np.all(np.isfinite(first))
    assert np.all(first >= 0.0)


# ---------------------------------------------------------------------------
# The sizing rule, enforced
# ---------------------------------------------------------------------------


def test_filter_must_span_about_twice_the_declared_delay():
    """193 taps carries 360 ms at 250 Hz; 129 taps does not, and the only
    symptom is r falling from 0.907 to 0.491. Refuse it at construction."""
    with pytest.raises(ValueError, match="taps"):
        BandEnvelopeImpl(band=(12.0, 15.0), latency_ms=360.0, taps=129, sample_rate_hz=250.0)


def test_sizing_error_names_the_tap_count_that_would_work():
    with pytest.raises(ValueError) as excinfo:
        BandEnvelopeImpl(band=(12.0, 15.0), latency_ms=360.0, taps=129, sample_rate_hz=250.0)

    assert "181" in str(excinfo.value), (
        f"the error should say how many taps 360 ms needs: {excinfo.value}"
    )


def test_a_sufficient_filter_is_accepted():
    impl = BandEnvelopeImpl(band=(12.0, 15.0), latency_ms=360.0, taps=193, sample_rate_hz=250.0)

    assert impl.taps == 193
    assert impl.group_delay == 90  # 360 ms at 250 Hz


@pytest.mark.parametrize("sr", [250.0, 256.0, 500.0])
@pytest.mark.parametrize("latency_ms", [250.0, 300.0, 350.0])
def test_default_sizing_works_at_every_eeg_rate(sr, latency_ms):
    """The declared latency is wall-clock, so one protocol source has to work
    on a 250 Hz and a 500 Hz amp. A fixed tap count would not: 193 taps
    carries 384 ms at 250 Hz but only 192 ms at 500 Hz, so `latency: 350 ms`
    would be accepted on one amp and refused on the other. The default is
    derived from the delay for exactly this reason."""
    impl = BandEnvelopeImpl(band=(12.0, 15.0), latency_ms=latency_ms, sample_rate_hz=sr)

    assert impl.taps >= 2 * impl.group_delay + 1
    assert impl.taps % 2 == 1


def test_rejects_a_latency_too_short_to_taper():
    """Below a few samples the ideal two-sided response cannot be tapered
    around the declared delay, and the filter's energy wraps to the far end
    of the window — so the output's real lag is the filter length, not the
    declared latency. Refuse rather than realize a delay nobody asked for."""
    with pytest.raises(ValueError, match="latency"):
        BandEnvelopeImpl(band=(12.0, 15.0), latency_ms=4.0, sample_rate_hz=SR)


def test_rejects_nonpositive_latency():
    with pytest.raises(ValueError, match="latency"):
        BandEnvelopeImpl(band=(12.0, 15.0), latency_ms=0.0, sample_rate_hz=SR)


def test_rejects_band_outside_nyquist():
    with pytest.raises(ValueError, match="nyquist"):
        BandEnvelopeImpl(band=(12.0, 200.0), latency_ms=250.0, sample_rate_hz=SR)


def test_rejects_inverted_band():
    with pytest.raises(ValueError, match="band"):
        BandEnvelopeImpl(band=(15.0, 12.0), latency_ms=250.0, sample_rate_hz=SR)


# ---------------------------------------------------------------------------
# Language surface
# ---------------------------------------------------------------------------

PROTOCOL = """
protocol "smr_one_stage" {
  meta { version = "0.1.0"; description = "d"; status = "draft"; goals = ["adhd_attention"] }
  requires { sample_rate = ">= 250 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "smr_envelope" { from = "raw"
    pipeline = [ band_envelope(band: (12 Hz, 15 Hz), latency: 250 ms) ] }
  threshold "smr_hi" { signal = "smr_envelope"; type = absolute(6 uV) }
  reward { continuous = sigmoid("smr_envelope", midpoint: 6 uV, steepness: 1)
           event = dwell(condition: above("smr_envelope", "smr_hi"), duration: 250 ms) }
  output { audio_gain = reward.continuous; audio_chime = reward.event }
}
"""


def test_resolves_as_a_one_stage_pipeline():
    ir = resolve(parse(PROTOCOL))

    derive = ir.derives["smr_envelope"]
    assert derive.stream_type == scalar_stream(VOLTAGE), (
        f"an envelope is a real uV stream like magnitude(), got {derive.stream_type}"
    )


def test_replaces_four_stages_with_one():
    """What the issue actually asks for: one stage carrying one delay."""
    ir = resolve(parse(PROTOCOL))

    callees = [c.callee for c in _iter_calls(ir.derives["smr_envelope"].expression)]
    assert callees.count("band_envelope") == 1
    for superseded in ("bandpass", "hilbert", "magnitude", "smooth"):
        assert superseded not in callees


@pytest.mark.skipif(RUST_BACKEND_ACTIVE, reason="the Rust core has no band_envelope stage yet")
def test_runs_end_to_end_through_the_evaluator():
    """Covers the plumbing a unit test on the impl cannot: the resolver
    accepting the call, `latency` arriving at the constructor as `latency_ms`,
    and the factory building the right impl."""
    ev = Evaluator.live(
        resolve(parse(PROTOCOL)),
        sample_rate_hz=250,
        channel_names=("Cz", "A1", "A2"),
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    rng = np.random.default_rng(0)

    for _ in range(40):  # 40 x 50 ms = 2 s, past the filter's warm-up
        ev.step_chunk(rng.standard_normal((13, 3)) * 10.0)

    envelope = ev.last_streams()["smr_envelope"]
    assert envelope.shape == (13,)
    assert np.all(np.isfinite(envelope))
    assert np.all(envelope >= 0.0)
    assert envelope.std() > 0.0, "a live envelope should move, not sit at a constant"


@pytest.mark.skipif(RUST_BACKEND_ACTIVE, reason="the Rust core has no band_envelope stage yet")
def test_evaluator_rejects_a_latency_the_amp_rate_cannot_carry():
    """A declared latency is wall-clock; a 4 ms one is a sample or two at any
    EEG rate, and must fail loudly at build time rather than silently realize
    some other delay."""
    src = PROTOCOL.replace("latency: 250 ms", "latency: 4 ms")

    with pytest.raises(ValueError, match="latency"):
        Evaluator.live(
            resolve(parse(src)), sample_rate_hz=250, channel_names=("Cz", "A1", "A2")
        ).start(skip_warmup=True)


def test_cost_report_counts_a_band_envelope_derive():
    """A protocol that adopts the primitive must not read as having no
    envelope cost just because it no longer calls `bandpass`."""
    report = estimate_cost(resolve(parse(PROTOCOL)), sample_rate_hz=250.0)

    assert any("band_envelope" in d.name for d in report.drivers), (
        f"drivers: {[d.name for d in report.drivers]}"
    )


# ---------------------------------------------------------------------------
# Matched validation against the published result
#
# Smetanin et al. 2020 report r = 0.85 +/- 0.1 at a 100 ms delay on an
# individualized alpha band (peak +/-2 Hz, so 4 Hz wide). Matching band width
# AND delay is the only comparison that means anything: the same filter at the
# same 100 ms on SMR's 3 Hz band gives r = 0.35. The method transfers, the
# band width does not.
#
# Opt in with a corpus of EDF recordings:
#   REFRAIN_EEG_CORPUS=/path/to/corpus pytest tests/test_band_envelope.py
# Layout: <corpus>/cases/<case>/input.edf
# ---------------------------------------------------------------------------

PAPER_R, PAPER_TOLERANCE = 0.85, 0.1
CORPUS = os.environ.get("REFRAIN_EEG_CORPUS")


def _corpus_channel(case: str, want: str):
    mne = pytest.importorskip("mne", reason="real-EEG validation needs mne")
    mne.set_log_level("ERROR")
    path = os.path.join(CORPUS, "cases", case, "input.edf")
    if not os.path.exists(path):
        pytest.skip(f"no recording at {path}")
    raw = mne.io.read_raw_edf(path, preload=True, verbose=False)
    sr = raw.info["sfreq"]
    name = next((c for c in raw.ch_names if c.upper().startswith(want.upper())), None)
    if name is None:
        pytest.skip(f"{case} has no {want} channel")
    x = raw.get_data(picks=[name])[0] * 1e6
    return x[int(10 * sr) : int(min(190, raw.times[-1] - 5) * sr)], sr


@pytest.mark.skipif(not CORPUS, reason="set REFRAIN_EEG_CORPUS to run real-EEG validation")
def test_matches_the_published_correlation_at_matched_band_and_delay():
    band = (8.0, 12.0)  # 4 Hz wide, as the paper's individualized alpha
    x, sr = _corpus_channel("c002-edf-nominal-ec", "P4")

    y = BandEnvelopeImpl(band=band, latency_ms=100.0, sample_rate_hz=sr).step(x)

    edge = _edges(x, sr)
    _, corr = _lag_and_corr(y[edge], _zero_phase_envelope(x, sr, band)[edge], sr)
    assert abs(corr - PAPER_R) <= PAPER_TOLERANCE, (
        f"r = {corr:.3f}, outside the paper's {PAPER_R} +/- {PAPER_TOLERANCE}"
    )


def _four_stage_envelope(x: np.ndarray, sr: float, band: tuple[float, float]) -> np.ndarray:
    """What the EEG protocols ship today: four stages, delay by accident."""
    sos = scisig.butter(4, [band[0] / (sr / 2), band[1] / (sr / 2)], btype="band", output="sos")
    y = HilbertFirImpl(taps=65, sample_rate_hz=sr).step(scisig.sosfilt(sos, x))
    return SmoothImpl(tau_ms=250.0, sample_rate_hz=sr).step(MagnitudeImpl().step(y))


@pytest.mark.skipif(not CORPUS, reason="set REFRAIN_EEG_CORPUS to run real-EEG validation")
@pytest.mark.parametrize(
    "case,channel,band",
    [
        ("c002-edf-nominal-ec", "Cz", (12.0, 15.0)),
        ("c002-edf-nominal-ec", "P4", (8.0, 12.0)),
    ],
)
def test_matches_the_four_stage_chain_at_a_materially_lower_latency(case, channel, band):
    """The claim that makes the primitive worth having.

    Not "the curve is better" — at positive delay this filter family is
    equivalent to a windowed-DFT Hilbert. The gain is from collapsing four
    stages into one, so the delay stops being the sum of four independent
    choices. Measured across the corpus: median 206 ms saved at equal accuracy
    on 12-15 Hz (n=11, range 102-266 ms), 265 ms on 8-12 Hz (n=12).
    """
    x, sr = _corpus_channel(case, channel)
    truth = _zero_phase_envelope(x, sr, band)
    edge = _edges(x, sr)
    four_stage_lag, four_stage_r = _lag_and_corr(
        _four_stage_envelope(x, sr, band)[edge], truth[edge], sr
    )

    matched_at = None
    for latency_ms in range(60, int(four_stage_lag) + 1, 20):
        y = BandEnvelopeImpl(band=band, latency_ms=float(latency_ms), sample_rate_hz=sr).step(x)
        if _lag_and_corr(y[edge], truth[edge], sr)[1] >= four_stage_r:
            matched_at = latency_ms
            break

    assert matched_at is not None, (
        f"no declared latency below the four-stage chain's {four_stage_lag:.0f} ms "
        f"reached its r={four_stage_r:.3f}"
    )
    assert four_stage_lag - matched_at >= 100.0, (
        f"only {four_stage_lag - matched_at:.0f} ms saved at equal accuracy "
        f"({matched_at} ms vs {four_stage_lag:.0f} ms)"
    )


# ---------------------------------------------------------------------------
# kind: "weighted" — a sharper band at a short declared latency
#
# The tapered design above throws away the taps after the delay and smooths
# what is left, so at a short latency its skirts reach into the neighbouring
# rhythm. The weighted design keeps those taps and lets least squares spend the
# stopband where EEG has its power. The two are different trades, pinned here
# from both sides: what weighted buys, what it costs, and that a protocol which
# does not ask for it is not moved by it.
# ---------------------------------------------------------------------------


def _eeg_like(
    band: tuple[float, float], seconds: float = 120.0, sr: float = SR, seed: int = 11
) -> np.ndarray:
    """A weak in-band rhythm under what makes a short filter fail on real EEG:
    a 1/f background and a louder rhythm 2.5 Hz below the band."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    spectrum = np.fft.rfft(rng.standard_normal(n))
    spectrum /= np.maximum(np.fft.rfftfreq(n, 1 / sr), 0.5)  # amplitude ~ 1/f
    background = np.fft.irfft(spectrum, n)
    background *= 10.0 / np.std(background)
    slow = scisig.sosfilt(
        scisig.butter(2, 0.4 / (sr / 2), btype="low", output="sos"), rng.standard_normal(n)
    )
    neighbour = np.clip(8.0 + 5.0 * slow / np.std(slow), 1.0, None) * np.sin(
        2 * np.pi * (band[0] - 2.5) * t
    )
    return 0.15 * _am_rhythm(band, seconds=seconds, sr=sr, seed=seed) + neighbour + background


def _weighted(band, latency_ms, sr=SR, **kw) -> BandEnvelopeImpl:
    return BandEnvelopeImpl(
        band=band, latency_ms=latency_ms, kind="weighted", sample_rate_hz=sr, **kw
    )


def _r(impl: BandEnvelopeImpl, x: np.ndarray, band: tuple[float, float]) -> float:
    edge = _edges(x, SR)
    return _lag_and_corr(impl.step(x)[edge], _zero_phase_envelope(x, SR, band)[edge], SR)[1]


def test_weighted_tracks_the_band_at_a_latency_where_tapered_cannot():
    """The proposition. At 200 ms on a 4 Hz band the tapered skirt is 7 dB down
    at the neighbour and the envelope is mostly the neighbour's; the weighted
    design is still tracking the band. (On real EEG the margin is what moves
    the matched-accuracy latency from 320 ms to 160; a synthetic neighbour at
    one frequency is an easier case than a real alpha shoulder, so that figure
    is measured on recordings and not asserted here.)"""
    band = (12.0, 16.0)
    x = _eeg_like(band)

    tapered = _r(BandEnvelopeImpl(band=band, latency_ms=200.0, sample_rate_hz=SR), x, band)
    weighted = _r(_weighted(band, 200.0), x, band)

    assert weighted > 0.95, f"weighted at 200 ms r={weighted:.3f}"
    assert tapered < 0.85, f"the premise: tapered at 200 ms r={tapered:.3f}"
    later = _r(BandEnvelopeImpl(band=band, latency_ms=240.0, sample_rate_hz=SR), x, band)
    assert weighted > later, f"and 40 ms more does not buy it back: tapered at 240 ms r={later:.3f}"


def test_tapered_is_the_better_design_once_the_latency_is_generous():
    """Why weighted is a choice and not the default. Tapered keeps improving
    with latency; weighted plateaus, because its sharp edge stops resembling
    the band's own once the taper is long enough to draw a smooth one. They
    cross near 1.3-2 bandwidth-periods, and past that tapered also keeps its
    linear phase and its -60 dB stopband."""
    band = (22.0, 30.0)  # 8 Hz wide: 320 ms is 2.6 periods
    x = _eeg_like(band)

    tapered = _r(BandEnvelopeImpl(band=band, latency_ms=320.0, sample_rate_hz=SR), x, band)
    weighted = _r(_weighted(band, 320.0), x, band)

    assert tapered > weighted, f"tapered r={tapered:.3f}, weighted r={weighted:.3f}"
    assert tapered > 0.99


@pytest.mark.parametrize("band, latency_ms", [((12.0, 16.0), 200.0), ((12.0, 15.0), 320.0),
                                              ((8.0, 12.0), 240.0), ((22.0, 30.0), 240.0)])
def test_weighted_realizes_the_latency_it_declares(band, latency_ms):
    """Same contract as the tapered design, from 0.8 bandwidth-periods up."""
    x = _eeg_like(band)
    edge = _edges(x, SR)

    lag_ms, _ = _lag_and_corr(
        _weighted(band, latency_ms).step(x)[edge], _zero_phase_envelope(x, SR, band)[edge], SR
    )

    assert abs(lag_ms - latency_ms) <= 20.0, (
        f"declared {latency_ms} ms but the output lags by {lag_ms:.0f} ms"
    )


def test_weighted_refuses_a_latency_it_cannot_realize():
    """Below half a bandwidth-period the output lags by more than the number in
    the source (25-50 ms over, measured on real EEG). The tapered design at the
    same setting is merely inaccurate; this one would be untruthful."""
    with pytest.raises(ValueError, match="half a bandwidth-period") as err:
        _weighted((12.0, 15.0), 100.0)

    assert "167 ms" in str(err.value), "the message should name the latency that would work"
    _weighted((12.0, 15.0), 170.0)  # and that latency is accepted


def test_weighted_is_calibrated_like_tapered():
    """`absolute(6 uV)` must mean the same thing under either design."""
    band = (12.0, 15.0)
    n = int(30 * SR)
    tone = 7.0 * np.sin(2 * np.pi * 13.5 * np.arange(n) / SR)

    settled = _weighted(band, 250.0).step(tone)[int(5 * SR):]

    assert settled.mean() == pytest.approx(7.0, rel=0.01)


def test_weighted_rejects_the_neighbour_tapered_lets_through():
    """Where the accuracy comes from: 1.5 Hz below a 12-15 Hz band, at 200 ms,
    the tapered skirt is still most of the way up and the weighted one is not."""
    band = (12.0, 15.0)
    n = int(60 * SR)
    neighbour = 10.0 * np.sin(2 * np.pi * 10.5 * np.arange(n) / SR)
    edge = _edges(neighbour, SR)

    tapered = BandEnvelopeImpl(band=band, latency_ms=200.0, sample_rate_hz=SR).step(neighbour)
    weighted = _weighted(band, 200.0).step(neighbour)

    assert tapered[edge].mean() > 3.0, "the premise: tapered passes a third of it"
    assert weighted[edge].mean() < 1.5, f"weighted passed {weighted[edge].mean():.2f} of 10"


def test_weighted_still_rejects_mains_and_muscle():
    """What it gives up, bounded. The far stopband is shallower than the
    tapered design's and must still be a stopband: the white floor in the
    weights is what holds this, and without it 60 Hz came through at -30 dB."""
    band = (12.0, 16.0)
    n = int(30 * SR)
    t = np.arange(n) / SR
    edge = _edges(t, SR)
    impl_at = lambda hz: _weighted(band, 200.0).step(10.0 * np.sin(2 * np.pi * hz * t))[edge].mean()

    assert impl_at(60.0) < 0.2, "mains: at least 34 dB down"
    assert impl_at(35.0) < 0.3, "muscle band: at least 30 dB down"
    assert impl_at(4.0) < 0.06, "theta, where the weights put the stopband: 44 dB down"


def test_weighted_streams_identically_in_chunks():
    band = (12.0, 16.0)
    x = _eeg_like(band, seconds=30.0)

    one_shot = _weighted(band, 200.0).step(x)
    impl = _weighted(band, 200.0)
    chunk = int(0.05 * SR)
    streamed = np.concatenate([impl.step(x[i : i + chunk]) for i in range(0, len(x), chunk)])

    assert np.allclose(streamed, one_shot, atol=1e-12)


def test_weighted_taps_are_sized_from_the_bandwidth_not_the_delay():
    """The taps after the delay are memory, and how much is needed depends on
    how narrow the band is: three bandwidth-periods by default."""
    impl = _weighted((12.0, 16.0), 200.0, sr=250.0)

    assert impl.group_delay == 50
    assert impl.taps == 50 + int(np.ceil(3 * 250.0 / 4.0)) + 1  # 239, already odd


def test_weighted_refuses_too_little_memory():
    with pytest.raises(ValueError, match=r"taps >= 114"):
        _weighted((12.0, 16.0), 200.0, sr=250.0, taps=101)


def test_weighted_design_does_not_depend_on_a_frequency_grid():
    """Closed form, like the tapered design, and for the same reason. Checked
    against the same integrals by brute-force quadrature."""
    band, sr, delay = (12.0, 15.0), 250.0, 60
    impl = _weighted(band, 240.0, sr=sr)
    n = impl.taps
    k = np.arange(n)

    f = np.arange(0.0, sr / 2 + 0.001, 0.002)
    weight = lambda v: 1.0 / np.maximum(np.abs(v), 1.0) ** 2 + 1.0 / 20.0**2
    r = np.array([2.0 * np.trapezoid(weight(f) * np.cos(2 * np.pi * f * m / sr), f) for m in k])
    fb = np.linspace(band[0], band[1], 3001)
    p = np.array(
        [2.0 * np.trapezoid(weight(fb) * np.exp(2j * np.pi * fb * (m - delay) / sr), fb)
         for m in k]
    )
    b = np.linalg.solve(r[np.abs(k[:, None] - k[None, :])], p)
    b *= 2.0 / abs(np.sum(b * np.exp(-2j * np.pi * 13.5 * k / sr)))

    assert np.max(np.abs(impl.h_complex - b)) < 1e-4 * np.max(np.abs(b))


def test_unknown_kind_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown kind 'wcfir'.*'tapered', 'weighted'"):
        BandEnvelopeImpl(band=(12.0, 15.0), latency_ms=250.0, kind="wcfir", sample_rate_hz=SR)


def test_default_kind_is_the_design_that_shipped_before_kind_existed():
    """Nothing that does not write `kind` may move. The taps are the contract —
    both engines read them off the wire — so they are what is compared."""
    args = dict(band=(12.0, 15.0), latency_ms=250.0, sample_rate_hz=SR)

    default, tapered = BandEnvelopeImpl(**args), BandEnvelopeImpl(kind="tapered", **args)

    assert default.kind == "tapered"
    assert np.array_equal(default.h_complex, tapered.h_complex)
    assert default.taps == 141  # 2.2 x 64 samples, made odd: the pre-`kind` sizing


WEIGHTED_PROTOCOL = PROTOCOL.replace(
    "latency: 250 ms)", 'latency: 200 ms, kind: "weighted")'
)


def _band_envelope_call(ir_json: dict) -> dict:
    node = ir_json["derives"]["smr_envelope"]["expression"]
    while node["callee"] != "band_envelope":
        node = node["args"][0]["value"]
    return node


def test_kind_does_not_move_the_wire_version_and_absent_emits_nothing():
    """`kind` picks which taps get baked, and the taps are on the wire. An
    engine that has never heard of the argument runs the identical filter, so
    there is nothing for a version bump to protect — unlike `exempt` or a hold
    `tolerance`, where dropping the key runs a different protocol."""
    from refrain.ir_json import ir_to_json_obj

    plain = ir_to_json_obj(resolve(parse(PROTOCOL)), sample_rate_hz=250.0)
    weighted = ir_to_json_obj(resolve(parse(WEIGHTED_PROTOCOL)), sample_rate_hz=250.0)

    assert plain["refrain_ir_version"] == weighted["refrain_ir_version"] == "0.5"
    assert "kind" not in {a["name"] for a in _band_envelope_call(plain)["args"]}
    call = _band_envelope_call(weighted)
    assert {a["name"]: a["value"] for a in call["args"]}["kind"]["value"] == "weighted"
    assert len(call["coeffs"]["fir_taps_re"]) == 50 + 250 + 1  # 200 ms + 3 periods of 3 Hz


@pytest.mark.skipif(RUST_BACKEND_ACTIVE, reason="covered for the Rust core by micro_16's golden vector")
def test_weighted_runs_end_to_end_through_the_evaluator():
    ev = Evaluator.live(
        resolve(parse(WEIGHTED_PROTOCOL)),
        sample_rate_hz=250,
        channel_names=("Cz", "A1", "A2"),
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    rng = np.random.default_rng(0)

    for _ in range(60):
        ev.step_chunk(rng.standard_normal((13, 3)) * 10.0)

    envelope = ev.last_streams()["smr_envelope"]
    assert np.all(np.isfinite(envelope)) and np.all(envelope >= 0.0) and envelope.std() > 0.0


def test_editor_leaves_an_explicit_kind_out_of_its_subset():
    """The editor's envelope block has no slot for the design. Rendering it
    back would drop `kind` and hand the author a different filter."""
    from refrain.editor import describe_protocol

    described = describe_protocol(WEIGHTED_PROTOCOL)

    assert described["in_subset"] is False
    assert described["reason"] == "band_envelope with explicit kind not in subset"
    assert describe_protocol(PROTOCOL)["in_subset"] is True, "and without it, still in"
