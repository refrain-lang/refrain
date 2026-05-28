# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""End-to-end test for the coherence() primitive: a synthetic two-
channel signal switches between coherent and incoherent halves; a tiny
protocol binds coherence directly to audio_gain; we assert the gain
trace correctly tracks the input's coherence state.

This complements the per-primitive numerical tests in
test_primitive_impls.py — those verify CoherenceImpl in isolation;
this one verifies the language → registry → evaluator → output chain.
"""

from __future__ import annotations

import numpy as np

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

# (backend fixture provided by tests/conftest.py)


SR = 250


def _coherent_then_incoherent(
    *, sample_rate_hz: int, n_samples: int, channels: tuple[str, ...], seed: int = 0
) -> np.ndarray:
    """Build a 2-channel signal: first half shares an alpha (10 Hz)
    component (high coherence), second half is independent broadband
    noise on each channel (low coherence).

    Returns a (n_samples, n_channels) float64 array in evaluator
    convention.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) / sample_rate_hz
    half = n_samples // 2
    out = np.zeros((n_samples, len(channels)), dtype=np.float64)

    # Each channel has its own independent noise component throughout.
    for ch_idx in range(len(channels)):
        out[:, ch_idx] = 0.3 * rng.standard_normal(n_samples)

    # First half: add a shared 10 Hz alpha component to the two channels
    # we want to be coherent (channels 0 and 1).
    shared_alpha = np.sin(2 * np.pi * 10 * t[:half])
    out[:half, 0] += shared_alpha
    out[:half, 1] += shared_alpha
    # Second half: nothing shared — just the independent noise.

    return out


def test_coherence_runs_end_to_end_and_tracks_input_coherence(backend):
    """The protocol declares `coherence(input_a, input_b, band, window)`
    as a derive; the evaluator wires it correctly; audio_gain output
    reflects the actual coherence state of the input."""
    src = """
        protocol "alpha_coherence_demo" {
          meta {
            version = "1.0"
            evidence = "demo"
            description = "Alpha-band coherence test"
          }
          requires {
            sample_rate = ">= 250 Hz"
            channels    = ["C3", "C4"]
          }
          input "raw_c3" {
            montage = referential(active: "C3", reference: "device")
          }
          input "raw_c4" {
            montage = referential(active: "C4", reference: "device")
          }
          derive "alpha_coh" {
            formula = coherence(
              input_a: "raw_c3",
              input_b: "raw_c4",
              band:    (8 Hz, 12 Hz),
              window:  2 s
            )
          }
          reward {
            continuous = "alpha_coh"
          }
          output {
            audio_gain = reward.continuous
          }
        }
    """
    ir = resolve(parse(src))

    # 30 s of synthetic data — first 15 s coherent, second 15 s incoherent.
    duration_s = 30
    n_samples = duration_s * SR
    data = _coherent_then_incoherent(
        sample_rate_hz=SR, n_samples=n_samples, channels=("C3", "C4"), seed=42,
    )

    ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("C3", "C4"), backend=backend)
    ev.start(skip_warmup=True)

    # Push 64-sample chunks. Track audio_gain values per chunk.
    chunk_size = 64
    gain_events: list = []
    for i in range(0, n_samples, chunk_size):
        chunk = data[i:i + chunk_size]
        if chunk.shape[0] == 0:
            break
        events = ev.step_chunk(chunk)
        for ev_record in events:
            if ev_record.channel == "audio_gain" and ev_record.kind == "value":
                gain_events.append((ev_record.timestamp_s, ev_record.value))

    assert len(gain_events) > 0, "no audio_gain events emitted"

    # Skip the first 4 seconds for the impl's coherence buffer to fill
    # (window = 2 s; let it run another window for the rolling MSC to
    # converge to steady state).
    times = np.array([t for t, _ in gain_events])
    gains = np.array([v for _, v in gain_events])
    coherent_mask = (times > 4.0) & (times < 14.0)
    incoherent_mask = (times > 19.0) & (times < 29.0)
    coherent_gain = gains[coherent_mask].mean()
    incoherent_gain = gains[incoherent_mask].mean()

    # During the coherent phase, MSC ≫ during the incoherent phase.
    assert coherent_gain > 0.6, (
        f"coherent-phase mean audio_gain should be > 0.6 (high MSC), "
        f"got {coherent_gain:.3f}"
    )
    assert incoherent_gain < 0.3, (
        f"incoherent-phase mean audio_gain should be < 0.3 (low MSC), "
        f"got {incoherent_gain:.3f}"
    )
    assert coherent_gain > incoherent_gain + 0.3


def test_coherence_in_tap_api(backend):
    """The introspection tap API should expose the coherence derive's
    last-sample value under `derive/<name>`."""
    src = """
        protocol "p" {
          meta { version = "1.0" }
          input "raw_a" { montage = referential(active: "C3", reference: "device") }
          input "raw_b" { montage = referential(active: "C4", reference: "device") }
          derive "coh" {
            formula = coherence(
              input_a: "raw_a", input_b: "raw_b",
              band: (8 Hz, 12 Hz), window: 2 s
            )
          }
          reward { continuous = "coh" }
          output { audio_gain = reward.continuous }
        }
    """
    ir = resolve(parse(src))
    ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("C3", "C4"), backend=backend)
    ev.start(skip_warmup=True)

    # Push enough data for the buffer to fill (2 s window = 500 samples).
    rng = np.random.default_rng(0)
    n = SR * 4
    data = rng.standard_normal((n, 2))
    for i in range(0, n, 64):
        ev.step_chunk(data[i:i + 64])


# --- Regression: positional coherence inputs (live/push mode) ---------------
# `coherence("a","b", …)` (positional inputs) previously resolved but was
# unrunnable at step_chunk on BOTH backends — python: CoherenceImpl.step missing
# x_a/x_b; rust: "missing baked coeffs" (an uncatchable panic). Every downstream
# consumer keys the two explicit stream inputs by name, so the resolver now
# canonicalizes positional coherence inputs to named (input_a/input_b).


def test_coherence_positional_inputs_canonicalized_to_named():
    def src(coh):
        return f"""
            protocol "p" {{
              meta {{ version = "1.0"; evidence = "demo"; description = "x" }}
              input "a" {{ montage = referential(active: "C3", reference: "device") }}
              input "b" {{ montage = referential(active: "C4", reference: "device") }}
              derive "coh" {{ formula = {coh} }}
              reward {{ continuous = "coh" }}
              output {{ audio_gain = reward.continuous }}
            }}
        """
    pos = resolve(parse(src('coherence("a", "b", band: (8 Hz, 12 Hz), window: 2 s)')))
    named = resolve(parse(src('coherence(input_a: "a", input_b: "b", band: (8 Hz, 12 Hz), window: 2 s)')))
    arg_names = lambda ir: [a.name for a in ir.derives["coh"].expression.args]
    assert arg_names(pos) == arg_names(named) == ["input_a", "input_b", "band", "window"]


def test_coherence_positional_inputs_run_end_to_end(backend):
    src = """
        protocol "p" {
          meta { version = "1.0"; evidence = "demo"; description = "x" }
          requires { sample_rate = ">= 250 Hz"; channels = ["C3", "C4"] }
          input "a" { montage = referential(active: "C3", reference: "device") }
          input "b" { montage = referential(active: "C4", reference: "device") }
          derive "coh" { formula = coherence("a", "b", band: (8 Hz, 12 Hz), window: 2 s) }
          reward { continuous = "coh" }
          output { audio_gain = reward.continuous }
        }
    """
    ir = resolve(parse(src))
    n_samples = 30 * SR
    data = _coherent_then_incoherent(sample_rate_hz=SR, n_samples=n_samples, channels=("C3", "C4"), seed=42)
    ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("C3", "C4"), backend=backend)
    ev.start(skip_warmup=True)
    times, gains = [], []
    for i in range(0, n_samples, 64):
        chunk = data[i:i + 64]
        if chunk.shape[0] == 0:
            break
        for e in ev.step_chunk(chunk):
            if e.channel == "audio_gain" and e.kind == "value":
                times.append(e.timestamp_s)
                gains.append(e.value)
    times, gains = np.array(times), np.array(gains)
    assert len(gains) > 0
    coherent = gains[(times > 4.0) & (times < 14.0)].mean()
    incoherent = gains[(times > 19.0) & (times < 29.0)].mean()
    assert coherent > 0.6, f"coherent-phase gain should be high, got {coherent:.3f}"
    assert incoherent < 0.3, f"incoherent-phase gain should be low, got {incoherent:.3f}"
    assert coherent > incoherent + 0.3

    taps = ev.last_taps()
    assert "derive/coh" in taps, f"missing tap; saw keys: {sorted(taps)}"
    assert isinstance(taps["derive/coh"], float)
    assert 0.0 <= taps["derive/coh"] <= 1.0


def test_dyadic_coherence_example_tracks_inter_brain_alpha(backend):
    """The shipped two-brain coherence example resolves and runs on both
    backends, and its audio_gain (a sigmoid over the inter-brain alpha MSC)
    rises when the two Pz alpha rhythms are coherent. End-to-end REAL check of
    examples/dyadic_alpha_coherence_pz.refrain."""
    from pathlib import Path

    from refrain.parser import parse_file

    example = (
        Path(__file__).resolve().parent.parent / "examples" / "dyadic_alpha_coherence_pz.refrain"
    )
    # No amp: Pz_A/Pz_B are a two-participant layout, not on the bundled profiles.
    ir = resolve(parse_file(example))
    rate = 256  # the example requires >= 256 Hz
    n_samples = 30 * rate
    data = _coherent_then_incoherent(
        sample_rate_hz=rate, n_samples=n_samples, channels=("Pz_A", "Pz_B"), seed=1,
    )
    ev = Evaluator.live(ir, sample_rate_hz=rate, channel_names=("Pz_A", "Pz_B"), backend=backend)
    ev.start(skip_warmup=True)
    times, gains = [], []
    for i in range(0, n_samples, 64):
        chunk = data[i:i + 64]
        if chunk.shape[0] == 0:
            break
        for e in ev.step_chunk(chunk):
            if e.channel == "audio_gain" and e.kind == "value":
                times.append(e.timestamp_s)
                gains.append(e.value)
    times, gains = np.array(times), np.array(gains)
    coherent = gains[(times > 4.0) & (times < 14.0)].mean()
    incoherent = gains[(times > 19.0) & (times < 29.0)].mean()
    assert coherent > 0.6, f"coherent-phase audio_gain should be high, got {coherent:.3f}"
    assert incoherent < 0.3, f"incoherent-phase audio_gain should be low, got {incoherent:.3f}"
