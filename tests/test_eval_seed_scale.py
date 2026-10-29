# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""A seeded limit set from the person's own warm-up, times a multiplier.

`scale` is one number on a seed rule, and the thing it exists for is a muscle
limit a practitioner no longer has to guess: three times this trainee's own
warm-up median instead of a fixed voltage that is too tight for one person and
too loose for the next. These cases cover the multiplier itself, the clamp it
brings with it, and the whole composition the design wrote it for — a muscle
envelope feeding nothing but an artifact gate, a control seeded from that
envelope, and the gate's limit read off that control.

**The clamp is asymmetric on purpose, and one case below pins the other half.**
Nothing clamped a seeded value to its control's declared range before this
change: `_step_seeds` wrote the percentile straight through and `_apply_control`
still does. Clamping every seed would move the seeded value — and so the reward
behaviour — of a protocol that declared nothing new, which this release
forbids. So the clamp arrived attached to `scale`, the new declaration and the
one that can push a value a long way out, and
`test_an_unscaled_seed_outside_its_range_is_still_written_unclamped` states
today's answer for a seed with no multiplier so that the difference is on the
record rather than found later and read as a bug.

The file name matches `tests/test_eval_*.py` on purpose: that glob is what the
dual-backend drift gate runs under `REFRAIN_EVAL_BACKEND=rust`.
"""

from __future__ import annotations

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

RATE = 256.0
CHUNK = 256          # 1 s per chunk, so a 3 s warm-up is exactly 3 chunks

# `magnitude()` straight off a `passthrough()` input makes the derive equal the
# input sample for sample, so a constant-fill warm-up makes the percentile
# exact by construction: the median of 15360 copies of 5.0 is 5.0, and 5.0 * 3
# is 15.0 with no rounding to argue about across two engines.
#
# The run phase is `mode = open` so a full-length constant signal never runs
# the session to a stop mid-run (the same reason `seed_smr_baseline` is open).
SEED_PROTO = '''protocol "seed_scale_run" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "scaled seed" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "env" { from = "raw"; pipeline = [ magnitude() ] }
  threshold "thr" { signal = "env"; type = absolute(value: emg_limit_uv) }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { audio_gain = reward.continuous }
  controls {
    emg_limit_uv = voltage {
      default = 12 uV; RANGE live_tunable = true
      seed = percentile { from = "env"; window = 2 s; target_pct = PCTSCALE }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 3 s; output_muted = true },
    phase { name = "run";    mode = open }
  ] }
}'''


def _ir(
    *,
    target_pct: float,
    scale: float | None,
    control_range: tuple[float, float] | None,
):
    src = SEED_PROTO.replace("PCT", repr(float(target_pct)))
    src = src.replace("SCALE", "" if scale is None else f"; scale = {scale!r}")
    src = src.replace(
        "RANGE",
        ""
        if control_range is None
        else f"range = ({control_range[0]!r} uV, {control_range[1]!r} uV);",
    )
    return resolve(parse(src))


def _seeded_report(
    backend: str,
    *,
    fill: float,
    target_pct: float,
    scale: float | None = None,
    control_range: tuple[float, float] | None = None,
    skip_warmup: bool = False,
) -> tuple[dict, list[dict]]:
    """Run the warm-up plus two `run` chunks; return (seed report, chunk taps).

    `skip_warmup=True` starts the session straight in `run`, so the 2 s
    measurement never happens at all — the early-advance case below.
    """
    evaluator = Evaluator.live(
        _ir(target_pct=target_pct, scale=scale, control_range=control_range),
        sample_rate_hz=RATE, channel_names=("Cz",), backend=backend,
    )
    evaluator.start(skip_warmup=skip_warmup)
    taps: list[dict] = []
    for _ in range(5):
        evaluator.step_chunk(np.full((CHUNK, 1), fill, dtype=np.float64))
        taps.append(dict(evaluator.last_taps()))
    return evaluator.seed_report(), taps


def _seeded_value(backend: str, **kwargs) -> float:
    report, _ = _seeded_report(backend, **kwargs)
    return report["emg_limit_uv"]["value"]


# ---------------------------------------------------------------------------
# The multiplier
# ---------------------------------------------------------------------------


def test_the_seeded_value_is_the_statistic_times_the_scale(backend):
    """A constant-fill warm-up makes the percentile exact by construction:
    median 5.0 uV, scale 3.0, seeded 15.0 uV."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=3.0) == pytest.approx(15.0)


def test_an_absent_scale_seeds_the_bare_statistic(backend):
    """Absence reproduces what a seed did before `scale` existed — the same
    number the statistic measured, not a multiple of it."""
    assert _seeded_value(backend, fill=5.0, target_pct=50) == pytest.approx(5.0)


def test_an_explicit_scale_of_one_is_the_same_value_as_no_scale(backend):
    """Writing the default out longhand must change nothing: a multiplier of
    1.0 is exact in IEEE-754, so this is an equality, not an approximation."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=1.0) == (
        _seeded_value(backend, fill=5.0, target_pct=50)
    )


def test_a_scale_below_one_lowers_the_seeded_value(backend):
    """The multiplier is a multiplier in both directions, not a headroom
    factor that only ever raises a limit."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=0.4) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# The clamp, and the half of it that deliberately does not apply
# ---------------------------------------------------------------------------


def test_a_scaled_seed_is_still_clamped_to_the_controls_range(backend):
    """A scale cannot push a limit outside what the protocol declared as sane:
    5.0 * 100 is 500 uV, and the control says 30 uV is the top."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=100.0,
                         control_range=(0.5, 30.0)) == pytest.approx(30.0)


def test_a_scaled_seed_inside_its_range_is_untouched_by_the_clamp(backend):
    """The clamp is a bound, not a rounding step — a value already inside the
    declared range comes through as the plain product."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=3.0,
                         control_range=(0.5, 30.0)) == pytest.approx(15.0)


def test_an_unscaled_seed_outside_its_range_is_still_written_unclamped(backend):
    """Today's behaviour, pinned. The clamp attaches to `scale` so that adding
    it moves no existing protocol's seeded value: this seed declares no
    multiplier, measures 50 uV, is told 30 uV is the top, and is written at
    50 uV exactly as it was before `scale` existed."""
    assert _seeded_value(backend, fill=50.0, target_pct=50,
                         control_range=(0.5, 30.0)) == pytest.approx(50.0)


def test_a_range_the_protocol_left_open_does_not_clamp_a_scaled_seed(backend):
    """There is nothing to clamp against when the control declares no range,
    so the product stands — the same 15.0 either way."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=3.0,
                         control_range=None) == pytest.approx(15.0)


# A range bound may name another control: `range = (0.5 uV, cap_uv)` compiles,
# and `range_high` reaches the IR as a `control_ref` rather than a number. That
# is not a bound either engine can clamp against — Python's IR carries no live
# value on the ref at all, while the Rust core sees only a baked `default`, so
# reading it on one side and not the other is exactly the divergence this
# release forbids. Both therefore decline to clamp that side.
BOUND_RANGE_PROTO = SEED_PROTO.replace(
    "RANGE", "range = (0.5 uV, cap_uv);"
).replace(
    "  controls {",
    "  controls {\n    cap_uv = voltage { default = 30 uV; range = (1 uV, 60 uV) }",
)


def test_a_range_bound_that_names_another_control_does_not_clamp(backend):
    """Pinned because the two answers differ and nothing else distinguishes
    them: declining to clamp writes 500 uV, while reading `cap_uv`'s baked
    default as the ceiling would write 30 uV.

    Whether a seeded value should be bounded by a control-bound range is a
    design question with live-retuning consequences; until it is answered, both
    engines read a non-literal bound as "no bound" and say so in SPEC §4.9.4.
    """
    src = BOUND_RANGE_PROTO.replace("PCT", "50.0").replace("SCALE", "; scale = 100.0")
    evaluator = Evaluator.live(
        resolve(parse(src)), sample_rate_hz=RATE, channel_names=("Cz",), backend=backend,
    )
    evaluator.start(skip_warmup=False)
    for _ in range(5):
        evaluator.step_chunk(np.full((CHUNK, 1), 5.0, dtype=np.float64))
    report = evaluator.seed_report()["emg_limit_uv"]
    assert report["status"] == "seeded"
    assert report["value"] == pytest.approx(500.0)   # not 30.0, the ref's default


# ---------------------------------------------------------------------------
# What the host is told
# ---------------------------------------------------------------------------


def test_the_scale_appears_in_the_seed_report(backend):
    """A practitioner reading the report sees 15 uV and needs to know whether
    that was measured or multiplied."""
    scaled, _ = _seeded_report(backend, fill=5.0, target_pct=50, scale=3.0)
    unscaled, _ = _seeded_report(backend, fill=5.0, target_pct=50)
    assert scaled["emg_limit_uv"]["scale"] == 3.0
    assert scaled["emg_limit_uv"]["value"] == pytest.approx(15.0)
    assert unscaled["emg_limit_uv"]["scale"] == 1.0


# ---------------------------------------------------------------------------
# Gate item 10 — the fail-closed rule a seeded muscle limit inherits
# ---------------------------------------------------------------------------


def test_a_warm_up_seed_that_cannot_fill_its_window_still_fails_closed(backend):
    """Pinned so the asymmetry with a re-seed's keep-the-old-value rule is
    deliberate on the record rather than discovered in a clinic: a trainee
    whose warm-up got cut short hears nothing all session.

    Starting straight in `run` leaves the 2 s window with 0 of its 512
    samples, so the measurement never completes and the output is muted for
    the rest of the run. The control run is the same signal with the warm-up
    intact, where the seed fires and the audio is audible — without it this
    case passes just as happily when the output is silent for some other
    reason.
    """
    short, short_taps = _seeded_report(backend, fill=5.0, target_pct=50,
                                       scale=3.0, skip_warmup=True)
    whole, whole_taps = _seeded_report(backend, fill=5.0, target_pct=50, scale=3.0)
    assert short["emg_limit_uv"]["status"] == "insufficient_samples"
    assert short["emg_limit_uv"]["value"] is None
    assert all(t["output/audio_gain"] == 0.0 for t in short_taps)
    assert whole["emg_limit_uv"]["status"] == "seeded"
    assert whole_taps[-1]["output/audio_gain"] > 0.0


# ---------------------------------------------------------------------------
# The worked composition, end to end (design §4)
# ---------------------------------------------------------------------------

# A muscle envelope that feeds nothing but the artifact gate, a control seeded
# from that envelope, and `threshold = absolute(<control>)` on the gate. The
# risk the design flagged was that a derive consumed only by an inhibit might
# be folded out before the seed could ingest it; these cases run the whole
# thing and read the gate's behaviour off the output.
#
# Broadband noise is the signal on purpose: it gives the envelope a real
# spread, so three times its median lands in the upper tail and the gate trips
# sometimes rather than always or never. The seed is deterministic because the
# generator is seeded.
WORKED = '''protocol "worked_muscle_limit" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "per-person muscle limit" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = passthrough() }
  derive "emg_envelope" {
    formula = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
  }
  derive "smr" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "smr_t" { signal = "smr"; type = absolute(2 uV) }
  controls {
    emg_limit_uv = voltage {
      default = 12 uV
      label   = "Muscle limit (seeded at warm-up)"
      seed = percentile { from = "emg_envelope"; window = 2 s; target_pct = 50SCALE }
    }
  }
  inhibit "emg" {
    metric    = rectify("emg_envelope")
    threshold = absolute(emg_limit_uv)
    action    = mute(release: 350 ms)
    kind      = "artifact"
  }
  reward { continuous = sigmoid("smr" / "smr_t", midpoint: 1.0, steepness: 3) }
  output {
    audio_gain = reward.continuous
    witness    = 0.5
  }
  session { phases = [
    phase { name = "warmup"; duration = 2 s; output_muted = true },
    phase { name = "run";    mode = open }
  ] }
}'''

WORKED_CHUNKS = 8          # 2 s warm-up + 6 s of run at 1 s per chunk


def _worked_run(backend: str, scale: str) -> tuple[dict, np.ndarray]:
    """Run the worked composition; return (seed report, the muted samples).

    `witness` is a constant 0.5 and the output stage zeroes a muted channel,
    so a 0.0 in `output/witness` is the gate and nothing else. `muted` itself
    is only a per-chunk tap, which would hide a gate that trips for a fraction
    of a second; the stream reads per sample.
    """
    rng = np.random.default_rng(0)
    signal = rng.standard_normal((CHUNK * WORKED_CHUNKS, 1)) * 10.0
    evaluator = Evaluator.live(
        resolve(parse(WORKED.replace("SCALE", scale))),
        sample_rate_hz=RATE, channel_names=("Cz",), record_streams=True,
        backend=backend,
    )
    evaluator.start(skip_warmup=False)
    witness: list[float] = []
    for start in range(0, len(signal), CHUNK):
        evaluator.step_chunk(signal[start:start + CHUNK])
        witness.extend(
            np.asarray(evaluator.last_streams()["output/witness"], dtype=float).tolist()
        )
    return evaluator.seed_report(), np.asarray(witness) == 0.0


def test_the_worked_muscle_limit_composition_runs_end_to_end(backend):
    """A derive feeding ONLY an inhibit, a control seeded from it, and
    `threshold = absolute(<control>)` on the inhibit — design §4.

    The seed has to ingest a derive nothing else consumes, survive dead-seed
    elimination because the inhibit's threshold references the control, and
    reach the gate as its limit. The gate then has to trip on some samples and
    not others: a limit that muted nothing, or everything, would pass a test
    that only asked whether the protocol ran.
    """
    report, muted = _worked_run(backend, "; scale = 3.0")
    entry = report["emg_limit_uv"]
    assert entry["status"] == "seeded"
    assert entry["source"] == "derive/emg_envelope"
    assert entry["scale"] == 3.0
    assert entry["value"] > 0.0
    run_muted = muted[2 * CHUNK:]           # the warm-up is muted by its phase
    assert 0 < run_muted.sum() < len(run_muted)


def test_the_scale_is_what_moves_the_worked_limit_and_what_it_gates(backend):
    """Same signal, same statistic, one number different.

    The measured median is identical in both runs, so the seeded limit must be
    exactly three times the unscaled one — and a limit three times higher has
    to let more of the session through. This is the case that fails if `scale`
    reaches the report but not the control the gate reads.
    """
    scaled, scaled_muted = _worked_run(backend, "; scale = 3.0")
    plain, plain_muted = _worked_run(backend, "")
    assert scaled["emg_limit_uv"]["value"] == pytest.approx(
        3.0 * plain["emg_limit_uv"]["value"]
    )
    assert scaled_muted[2 * CHUNK:].sum() < plain_muted[2 * CHUNK:].sum()
