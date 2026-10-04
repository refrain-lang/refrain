# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Reproductions for the nine defects in the Coherence Recorder's external
report against v0.22.0 (`refrain-biosignal-modality-gaps.md` §3, D1-D9).

Each test asserts the behaviour Refrain *should* have and is marked
``xfail(strict=True)``. So today a reproduced defect shows as XFAIL — green,
but on the record — and the moment someone fixes it the test XPASSes, which
``strict=True`` turns into a failure. That forces the marker off in the same
commit as the fix, which is the ratchet the reporter asked for.

(The brief said "assert the current, broken behaviour, marked
xfail(strict=True)". Those two halves contradict each other: a test that
asserts today's broken behaviour passes today, and a strict xfail on a passing
test is a failure. Asserting the *desired* behaviour is what makes the ratchet
work, so that is what these do.)

A rig note, learned the hard way and worth keeping: `referential(active: "Cz",
reference: "linked_ears")` subtracts the mean of A1/A2. Driving every synthetic
channel with the same waveform therefore cancels it to exactly zero and makes
every filter look dead. `drive()` below writes the active channel only.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import numpy as np
import pytest

from refrain import parse
from refrain.eval_ import Evaluator
from refrain.ir_json import ir_to_json_obj
from refrain.resolver import ResolveError, resolve
from refrain.synthetic import channels_for_synthetic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def resolve_src(src: str, amp=None):
    return resolve(parse(src), amp)


def drive(ir, *, fs: float, active: str = "Cz", signal=None, n=None,
          const: float | None = None, seed: int = 0):
    """Build a chunk that drives `active` only (see the rig note above)."""
    chans = channels_for_synthetic(ir)
    n = n if n is not None else int(round(fs))
    block = np.zeros((n, len(chans)), dtype=np.float64)
    idx = chans.index(active)
    if signal is not None:
        block[:, idx] = signal
    elif const is not None:
        block[:, idx] = const
    else:
        block[:, idx] = 0.1 * np.random.default_rng(seed).standard_normal(n)
    return chans, block


def step_once(ir, *, fs: float, active: str = "Cz", **kw):
    """Resolve-then-run: construct an Evaluator and push one chunk."""
    chans, block = drive(ir, fs=fs, active=active, **kw)
    ev = Evaluator(ir, sample_rate_hz=fs, channel_names=chans)
    return ev, ev.step_chunk(block)


def run_recorded(ir, *, fs: float, block, chans, chunk: int, set_control=None):
    """Push `block` in `chunk`-sized pieces, returning the concatenated
    recorded streams."""
    ev = Evaluator(ir, sample_rate_hz=fs, channel_names=chans, record_streams=True)
    if set_control is not None:
        ev.set_control(*set_control)
    out: dict[str, list[np.ndarray]] = {}
    for i in range(0, block.shape[0], chunk):
        ev.step_chunk(block[i:i + chunk])
        for k, v in ev.last_streams().items():
            out.setdefault(k, []).append(np.asarray(v))
    return {k: np.concatenate(v) for k, v in out.items()}


_ENV_PIPELINE = """
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(),
                 smooth(tau: 250 ms) ]
"""


def env_protocol(body: str, *, rate: str = ">= 256 Hz") -> str:
    """A minimal one-input, one-envelope-derive protocol plus `body`."""
    return f"""
protocol "probe" {{
  requires {{ sample_rate = "{rate}"; channels = ["Cz"] }}
  input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
  derive "env" {{
    from = "raw"
    {_ENV_PIPELINE.strip()}
  }}
  {body}
}}
"""


# ---------------------------------------------------------------------------
# D1 — a live frequency control is accepted and does nothing
# ---------------------------------------------------------------------------

D1_SRC = """
protocol "d1" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "band" {
    from = "raw"
    pipeline = [ bandpass(center: orf, bandwidth: 2 Hz, order: 4), hilbert(),
                 magnitude(), smooth(tau: 250 ms) ]
  }
  controls {
    orf = frequency {
      default = 10 Hz
      range = (1 Hz, 40 Hz)
      label = "Reinforcement frequency"
      live_tunable = true
    }
  }
  output { audio_gain = 0 }
}
"""


@pytest.mark.xfail(
    strict=True,
    reason="D1: 'Declare a frequency control live_tunable, bind it to "
           "bandpass(center:), call set_control — the write succeeds, "
           "coefficients never change.' Only smooth/percentile/sigmoid/absolute "
           "implement update_control. Deferred in EMBEDDING.md; nothing warns.",
)
def test_d1_live_frequency_control_retunes_the_filter():
    ir = resolve_src(D1_SRC)
    fs = 256.0
    secs = 16
    n = int(fs * secs)
    t = np.arange(n) / fs
    chans, block = drive(ir, fs=fs, n=n, signal=10.0 * np.sin(2 * np.pi * 25.0 * t))

    # A 2 Hz-wide filter centred on 10 Hz rejects a pure 25 Hz tone.
    at_default = run_recorded(ir, fs=fs, block=block, chans=chans, chunk=256)["band"]
    # Retuned to 25 Hz, the same tone should now sit in the passband.
    retuned = run_recorded(ir, fs=fs, block=block, chans=chans, chunk=256,
                           set_control=("orf", 25.0))["band"]

    rejected = float(at_default[-1024:].mean())
    passed = float(retuned[-1024:].mean())
    assert rejected < 1.0, f"sanity: 25 Hz should be rejected at orf=10, got {rejected}"
    assert passed > 5.0, (
        f"set_control('orf', 25.0) did not retune: 25 Hz tone still reads "
        f"{passed} (identical to the un-retuned {rejected})"
    )


# ---------------------------------------------------------------------------
# D2 — coherence() silently returns 0.0 when the band contains no Welch bin
# ---------------------------------------------------------------------------

COH_SRC = """
protocol "coh" {
  requires { sample_rate = ">= %(fs)d Hz"; channels = ["Cz", "Pz"] }
  input "a" { montage = referential(active: "Cz", reference: "linked_ears") }
  input "b" { montage = referential(active: "Pz", reference: "linked_ears") }
  derive "co" { formula = coherence("a", "b", band: (%(lo)s Hz, %(hi)s Hz), window: 60 s) }
  output { audio_gain = 0 }
}
"""


def _coherence_of_phase_locked_pair(lo, hi, fs, f_sig, secs=120):
    """Max MSC over a run of two near-identical `f_sig` sinusoids."""
    ir = resolve_src(COH_SRC % dict(fs=int(fs), lo=lo, hi=hi))
    chans = channels_for_synthetic(ir)
    n = int(fs * secs)
    t = np.arange(n) / fs
    rng = np.random.default_rng(1)
    s = np.sin(2 * np.pi * f_sig * t)
    block = np.zeros((n, len(chans)))
    block[:, chans.index("Cz")] = s + 0.01 * rng.standard_normal(n)
    block[:, chans.index("Pz")] = s + 0.01 * rng.standard_normal(n)
    return float(run_recorded(ir, fs=fs, block=block, chans=chans,
                              chunk=int(fs))["co"].max())


@pytest.mark.xfail(
    strict=True,
    reason="D2: 'A tachogram perfectly phase-locked to a 0.1 Hz breath returns "
           "exactly zero, byte-identical to unrelated signals.' nperseg = "
           "max(8, int(fs // 4)), so the Welch bins are ~4 Hz apart and a band "
           "containing no bin centre falls through to msc = 0.0. NOTE: this is "
           "not confined to sub-1 Hz bands — see "
           "test_d2_narrow_eeg_bands_also_collapse.",
)
def test_d2_coherence_of_phase_locked_hrv_band_is_not_zero():
    msc = _coherence_of_phase_locked_pair(0.05, 0.3, fs=4.0, f_sig=0.1, secs=300)
    assert msc > 0.5, (
        f"two identical 0.1 Hz signals report MSC {msc} over an HRV band at 4 Hz "
        f"(bin spacing {4.0 / max(8, int(4.0 // 4))} Hz — no bin lands in the band)"
    )


@pytest.mark.xfail(
    strict=True,
    reason="D2 (wider than reported): at 256 Hz the bin spacing is exactly 4 Hz, "
           "so any band narrower than 4 Hz that falls between multiples of 4 — "
           "e.g. SMR (13, 15) Hz or narrow alpha (9, 11) Hz — also returns "
           "exactly 0.0. The report scopes this to 'below about 1 Hz'; it is "
           "really 'no Welch bin centre inside the band', which bites ordinary "
           "EEG coherence training too.",
)
@pytest.mark.parametrize(
    "lo,hi,f_sig",
    [
        pytest.param(13, 15, 14.0, id="smr-13-15"),
        pytest.param(9, 11, 10.0, id="narrow-alpha-9-11"),
    ],
)
def test_d2_narrow_eeg_bands_also_collapse(lo, hi, f_sig):
    msc = _coherence_of_phase_locked_pair(lo, hi, fs=256.0, f_sig=f_sig)
    assert msc > 0.5, (
        f"two identical {f_sig} Hz signals report MSC {msc} over ({lo}, {hi}) Hz "
        f"at 256 Hz; the 4 Hz Welch grid puts no bin inside the band"
    )


# ---------------------------------------------------------------------------
# D3 — unknown fields in phase{} / session{} are silently dropped
# ---------------------------------------------------------------------------

D3_PHASE = """
protocol "d3" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "e" { from = "raw"; pipeline = [ smooth(tau: 250 ms) ] }
  output { audio_gain = 0 }
  session { phases = [
    phase { name = "run"; duration = 10 s; pace = 0.108 Hz; totally_bogus = "xyz" }
  ] }
}
"""

D3_SESSION = """
protocol "d3s" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "e" { from = "raw"; pipeline = [ smooth(tau: 250 ms) ] }
  output { audio_gain = 0 }
  session { totally_bogus = "xyz"; phases = [ phase { name = "run"; duration = 10 s } ] }
}
"""

D3_BLOCK = """
protocol "d3b" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "e" { from = "raw"; pipeline = [ smooth(tau: 250 ms) ] }
  block "b" { totally_bogus = "xyz" }
  output { audio_gain = 0 }
  session { phases = [ phase { name = "run"; duration = 10 s } ] }
}
"""


def test_d3_block_rejects_unknown_fields_today():
    """The control case, and the one-line fix the sibling resolver already has.

    Not xfail: this is correct behaviour today and must stay correct. It is what
    makes D3 an inconsistency rather than an across-the-board omission.
    """
    with pytest.raises(ResolveError, match=r"unexpected field\(s\) \['totally_bogus'\]"):
        resolve_src(D3_BLOCK)


def test_d3_phase_rejects_unknown_fields():
    """FIXED. Was: 'Resolves with no error. The resulting phase carries neither
    field.' _resolve_phase now rejects, as _resolve_block already did.

    Both offending fields are named, so an author who wrote a rate schedule in
    the obvious place is told about `pace` and not only about the typo.
    """
    with pytest.raises(ResolveError) as exc:
        resolve_src(D3_PHASE)
    msg = str(exc.value)
    assert "unexpected field" in msg
    assert "pace" in msg and "totally_bogus" in msg
    assert 'phase "run"' in msg, f"the message should name the phase: {msg}"


def test_d3_session_rejects_unknown_fields():
    """FIXED. The same silent drop in `session { }` itself."""
    with pytest.raises(ResolveError, match=r"session: unexpected field"):
        resolve_src(D3_SESSION)


# ---------------------------------------------------------------------------
# D4 — dimensions are not checked where protocols compare quantities
# ---------------------------------------------------------------------------


def test_d4_binary_gt_rejects_mismatched_dimensions_today():
    """The control case: binary `>` does check. Not xfail — must stay correct."""
    with pytest.raises(ResolveError, match="requires matching dimensions"):
        resolve_src(env_protocol("""
  reward { continuous = sigmoid("env" > 5 Hz, midpoint: 0.5, steepness: 1) }
  output { audio_gain = reward.continuous }
"""))


def test_d4_matching_dimensions_still_resolve():
    """Guard against a dimension check that is too strict to be useful."""
    ir = resolve_src(env_protocol("""
  threshold "t" { signal = "env"; type = absolute(value: 8 uV) }
  reward { event = dwell(condition: above("env", "t"), duration: 250 ms) }
  output { audio_chime = reward.event }
"""))
    assert "t" in ir.thresholds


@pytest.mark.parametrize("ctor", ["above", "below"])
def test_d4_condition_constructors_dimension_check(ctor):
    """FIXED. Was: 'above("<uV stream>", 5 Hz) — resolves.'"""
    with pytest.raises(ResolveError, match="dimension"):
        resolve_src(env_protocol(f"""
  reward {{ event = dwell(condition: {ctor}("env", 5 Hz), duration: 250 ms) }}
  output {{ audio_chime = reward.event }}
"""))


def test_d4_sigmoid_midpoint_dimension_check():
    """FIXED. Was: 'sigmoid("<uV stream>", midpoint: 5 Hz, steepness: 1) —
    resolves.' A midpoint is compared against the input, so it must share the
    input's dimensions."""
    with pytest.raises(ResolveError, match="dimension"):
        resolve_src(env_protocol("""
  reward { continuous = sigmoid("env", midpoint: 5 Hz, steepness: 1) }
  output { audio_gain = reward.continuous }
"""))


def test_d4_absolute_threshold_dimension_check():
    """FIXED. Was: 'absolute(value: 900 uV2) as a threshold on a uV signal —
    resolves, and reaches the wire with dims:{voltage:2} on the value against
    {voltage:1} on the stream.' It no longer reaches the wire at all, so the
    characterisation test that recorded that IR-JSON is gone with it."""
    with pytest.raises(ResolveError, match="dimension"):
        resolve_src(env_protocol("""
  threshold "t" { signal = "env"; type = absolute(value: 900 uV2) }
  reward { event = dwell(condition: above("env", "t"), duration: 250 ms) }
  output { audio_chime = reward.event }
"""))


def test_d4_dimensionless_literal_against_a_dimensioned_stream_still_resolves():
    """The deliberate limit of the fix.

    `sigmoid("smr_envelope", midpoint: 5.0, steepness: 0.5)` in
    bench/protocols/micro_09_inhibit.refrain means 5 uV, and bare unit-less
    numbers are how the whole corpus spells a threshold. Binary `>` *does*
    reject `"env" > 5`, so this is not yet full parity — requiring the unit is
    a language decision with corpus-wide churn, not a bug fix. Filed
    separately; this test pins what was deliberately left alone.
    """
    ir = resolve_src(env_protocol("""
  reward { continuous = sigmoid("env", midpoint: 5.0, steepness: 0.5) }
  output { audio_gain = reward.continuous }
"""))
    assert ir.reward is not None


# ---------------------------------------------------------------------------
# D5 — `%` literals reach the wire unscaled
# ---------------------------------------------------------------------------

D5_SRC = """
protocol "d5" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"; pipeline = [ smooth(tau: 10 ms) ] }
  output { audio_gain = %s }
}
"""


def _audio_gain(expr: str) -> float:
    ir = resolve_src(D5_SRC % expr)
    chans, block = drive(ir, fs=256.0, const=0.0)
    ev = Evaluator(ir, sample_rate_hz=256.0, channel_names=chans, record_streams=True)
    ev.step_chunk(block)
    return float(np.asarray(ev.last_streams()["output/audio_gain"])[-1])


def test_d5_percent_in_an_expression_keeps_its_face_value():
    """D5, as measured. `types_.py` claimed 'the runtime knows `95 %` means
    `0.95`'; in an expression it does not.

    The constants are chosen so the two readings land either side of the output
    clamp: 0.10 * 0.05 = 0.005 if scaled, 10 * 0.05 = 0.5 if raw. The report
    only showed the emitted IR-JSON value of 30.0 — the behaviour diverges too.
    """
    assert _audio_gain("10 % * 0.05") == pytest.approx(0.5)


def test_d5_autopilot_percent_is_the_one_place_that_divides_by_100():
    """The sharper version of D5: `%` means two different things in v0.22.0.

    `_ap_percent` divides by 100; nothing else does. So `30 %` is 30.0 in an
    output expression and 0.3 in a guard, in the same file. Reconciling that
    changes emitted values either way, so it is filed rather than fixed here —
    this test pins both halves so a future fix has to move both.
    """
    ir = resolve_src(pathlib.Path(
        pathlib.Path(__file__).resolve().parents[1]
        / "bench" / "protocols" / "autopilot_alpha_theta.refrain"
    ).read_text())
    ap = ir_to_json_obj(ir)["autopilot"]
    assert ap["reward_target"] == [0.2, 0.4], "written as (20%, 40%)"
    assert ap["guards"]["emg"]["max"] == pytest.approx(0.3), "written as 30%"


def test_d5_percent_control_default_is_a_0_to_100_number_used_raw():
    """The third convention, and the reason scaling expression-`%` would be
    wrong rather than simply behaviour-changing: `percentile` requires 0-100."""
    from refrain.primitive_impls import PercentileImpl

    ir = resolve_src(pathlib.Path(
        pathlib.Path(__file__).resolve().parents[1]
        / "bench" / "protocols" / "autopilot_alpha_theta.refrain"
    ).read_text())
    controls = ir_to_json_obj(ir)["controls"]
    assert controls["t_pct"]["type_kind"] == "percent"
    assert controls["t_pct"]["default"]["value"] == 15.0  # not 0.15

    # 101 is out of range, which pins the scale as 0-100...
    with pytest.raises(ValueError, match=r"\[0, 100\]"):
        PercentileImpl(target_pct=101.0, window_ms=1000.0, sample_rate_hz=256.0)

    # ...and 0.95 is accepted, read as the 0.95th percentile rather than the
    # 95th. That is why rescaling `%` to a 0-1 fraction would be wrong here
    # and not merely behaviour-changing: it would silently move a clinical
    # threshold to the bottom of the distribution.
    data = np.arange(1000, dtype=np.float64)
    as_95th = PercentileImpl(target_pct=95.0, window_ms=4000.0, sample_rate_hz=250.0)
    as_fraction = PercentileImpl(target_pct=0.95, window_ms=4000.0, sample_rate_hz=250.0)
    hi = float(as_95th.step(data)[-1])
    lo = float(as_fraction.step(data)[-1])
    assert hi > 900 and lo < 100, f"95 -> {hi}, 0.95 -> {lo}"


def test_d5_docstring_states_the_actual_rule():
    """The docstring was the wrong side of the divergence; it now describes all
    three conventions. Guards against it drifting back to the false claim."""
    import refrain.types_ as types_

    doc = inspect.getdoc(types_)
    assert "It is NOT rescaled here." in doc
    assert "keeps its face value" in doc
    assert "Autopilot fields only" in doc
    # The old claim may still appear, but only as the thing being corrected.
    if "means `0.95`" in doc:
        assert "false everywhere else" in doc


# ---------------------------------------------------------------------------
# D6 — a threshold reference inside a `formula` derive crashes at runtime
# ---------------------------------------------------------------------------

D6_SRC = """
protocol "d6" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "skin_temp" { from = "raw"; pipeline = [ smooth(tau: 250 ms) ] }
  threshold "baseline_t" { signal = "skin_temp"; type = absolute(value: 8 uV) }
  derive "rise" { formula = "skin_temp" - "baseline_t" }
  reward { continuous = sigmoid("rise", midpoint: 0.5, steepness: 3) }
  output { audio_gain = reward.continuous }
  session { phases = [ phase { name = "run"; duration = 10 s } ] }
}
"""


def test_d6_threshold_in_formula_derive_either_rejects_or_runs():
    """FIXED, by rejecting at resolve. Was: 'Resolves, emits IR, then dies on
    the first chunk: stream "baseline_t" not bound (Rust) / KeyError:
    "threshold/baseline_t" (Python).'

    Rejecting is the honest option because the ordering is not incidental: the
    evaluator runs every input, then every derive, then every threshold, so no
    derive can ever bind a threshold. The companion test that recorded the
    misleading topological order is gone with it — this shape no longer gets
    far enough to print one.
    """
    try:
        ir = resolve_src(D6_SRC)
    except ResolveError:
        return  # rejected at resolve — the cheaper of the two fixes
    step_once(ir, fs=256.0)  # or it must survive a chunk


def test_d6_error_names_the_threshold_and_the_way_out():
    """The reporter committed a prototype on the strength of `resolve` plus
    `compile-json`. The error has to be actionable enough that the next person
    does not have to rediscover the workaround their own file ended up using
    (subtract the seeded control instead)."""
    with pytest.raises(ResolveError) as exc:
        resolve_src(D6_SRC)
    msg = str(exc.value)
    assert "baseline_t" in msg
    assert "control" in msg
    assert "reward" in msg or "inhibit" in msg


def test_d6_threshold_refs_still_work_where_they_are_evaluated_in_order():
    """The limit of the rejection: `reward` and `inhibit` run after thresholds,
    so a threshold reference there is fine and must stay fine. This is the
    idiom every shipped protocol uses."""
    ir = resolve_src(env_protocol("""
  threshold "t" { signal = "env"; type = absolute(value: 8 uV) }
  reward {
    event = dwell(condition: above("env", "t"), duration: 250 ms)
    continuous = sigmoid("env" / "t", midpoint: 1.0, steepness: 3)
  }
  output { audio_chime = reward.event; audio_gain = reward.continuous }
"""))
    step_once(ir, fs=256.0)


# ---------------------------------------------------------------------------
# D7 — filters cold-start at zero
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="D7: 'SmoothImpl sets _state = 0.0; BandpassImpl zeroes _zi. "
           "Harmless for zero-mean EEG. For any DC-offset modality the filter "
           "must settle through the entire standing level.' Measured here at "
           "15 Hz (the rate the reporter's own EmotiBit profile declares) the "
           "transient is real but the report's figures do not reproduce — see "
           "test_d7_cold_start_magnitudes_as_measured.",
)
def test_d7_smooth_warm_starts_from_the_first_sample():
    from refrain.primitive_impls import SmoothImpl

    fs = 15.0
    const = np.full(int(fs * 10), 8.0)
    tonic = SmoothImpl(tau_ms=3180.0, sample_rate_hz=fs).step(const)
    phasic = const - tonic
    at_1s = abs(float(phasic[int(fs)]))
    assert at_1s < 0.02, (
        f"a phasic derive on a constant 8.0 input reads {at_1s} at t=1 s; "
        f"warm-starting _state from the first sample would make it ~0"
    )


def test_d7_cold_start_magnitudes_as_measured():
    """Characterisation of the actual transient at 15 Hz, recorded because the
    report's numbers do not reproduce and any resolver-side settling check would
    be calibrated against them.

    Report: 'a phasic derive reads 6.95 uS at t = 1 s and takes ~70 s to fall
    below 0.01 uS. At bandpass order 4 the settling is 57 s; at the
    polygraph-standard 0.0159 Hz it is 176 s.'
    """
    from refrain.primitive_impls import BandpassImpl, SmoothImpl

    fs = 15.0
    n = int(fs * 400)
    const = np.full(n, 8.0)

    for tau_s, expected_at_1s in ((3.18, 5.72), (60.0, 7.86)):
        phasic = const - SmoothImpl(tau_ms=tau_s * 1000.0, sample_rate_hz=fs).step(const)
        assert float(phasic[int(fs)]) == pytest.approx(expected_at_1s, abs=0.05), (
            f"tau={tau_s} s: phasic at t=1 s is {phasic[int(fs)]}, not the "
            f"report's 6.95"
        )

    # Settling of a 4th-order bandpass to within 1% of the standing level:
    # 1.8 s at 0.05 Hz and 5.5 s at 0.0159 Hz, not 57 s and 176 s.
    for f_hp, expected_s in ((0.05, 1.8), (0.0159, 5.5)):
        y = BandpassImpl(band=(f_hp, 5.0), order=4, sample_rate_hz=fs).step(const)
        settled = np.where(np.abs(y) < 0.08)[0]
        assert settled.size, f"bandpass({f_hp} Hz) never settles within 400 s"
        assert settled[0] / fs == pytest.approx(expected_s, abs=0.3)


# ---------------------------------------------------------------------------
# D8 — primitives documented but absent from the registry
# ---------------------------------------------------------------------------

_PRIMITIVES_MD = pathlib.Path(__file__).resolve().parents[1] / "docs" / "PRIMITIVES.md"
_ABSENT_MARKER = re.compile(r"planned|stub|sketch|not yet|internal", re.IGNORECASE)


def _documented_primitive_headings() -> dict[str, tuple[str, str]]:
    """`name -> (own heading suffix, enclosing ## section heading)`.

    A `###` heading may name several primitives at once (``### `above`,
    `below`, `inside```), and the "planned"/"sketches" disclaimer sometimes
    lives on the parent `##` heading rather than on the entry.
    """
    out: dict[str, tuple[str, str]] = {}
    section = ""
    for line in _PRIMITIVES_MD.read_text().splitlines():
        if line.startswith("### "):
            rest = line[4:]
            names = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", rest)
            suffix = re.sub(r"`[A-Za-z_][A-Za-z0-9_]*`|,", "", rest).strip()
            for nm in names:
                out[nm] = (suffix, section)
        elif line.startswith("## "):
            section = line[3:].strip()
    return out


@pytest.mark.parametrize("name", ["align_to", "select_channel", "decimate"])
def test_d8_documented_primitive_exists_or_is_marked(name):
    """FIXED, in the docs. Was: 'align_to, select_channel and decimate all
    appear in PRIMITIVES.md with signatures and worked examples, with no
    "planned" marker. None is in the v0.22.0 registry.'

    All three headings now say so, and each entry carries a note explaining
    what is actually absent. The general property — docs and registry must
    agree in both directions — lives in test_docs_registry_parity.py
    (mechanism M6); this stays as the record of the three the report named.
    """
    from refrain.primitives import lookup

    own, section = _documented_primitive_headings()[name]
    assert lookup(name) is None, f"{name} is now implemented; retire this test"
    assert _ABSENT_MARKER.search(own) or _ABSENT_MARKER.search(section), (
        f"PRIMITIVES.md documents `{name}` under heading suffix {own!r} in "
        f"section {section!r} with no planned/stub marker, but it is not in the "
        f"registry — it fails with 'unknown primitive'"
    )


def test_d8_decimate_was_marked_but_not_in_a_way_that_said_absent():
    """A correction to the report, kept because it changes what "marked" means.

    `decimate`'s heading was not bare: it read '*(internal, usually inserted by
    compiler)*'. That tells a reader the primitive is not theirs to call, which
    is not the same as telling them it does not exist — and nothing inserts it,
    because the evaluator runs at one rate throughout. So of the three the
    report named, two were genuinely unmarked and one was mis-marked.
    """
    body = _PRIMITIVES_MD.read_text()
    assert "*(internal, usually inserted by compiler)*" not in body
    assert "nothing inserts it" in body


def test_d8_correctly_marked_siblings_stay_marked():
    """`dead_zone` and `source_project` are the comparison the report draws.
    `pct_in_range` is also absent and also marked — but by its parent section
    ('Vector reductions *(sketches; ...)*'), not by its own heading, which the
    report describes as 'correctly marked' without that distinction."""
    headings = _documented_primitive_headings()
    assert _ABSENT_MARKER.search(headings["dead_zone"][0])
    assert _ABSENT_MARKER.search(headings["source_project"][0])
    own, section = headings["pct_in_range"]
    assert not _ABSENT_MARKER.search(own)
    assert _ABSENT_MARKER.search(section)


# ---------------------------------------------------------------------------
# D9 — four more shapes that resolve and then crash
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="D9.1: 'sigmoid(..., steepness: 0 - 3) -> TypeError. Matters because "
           "down-training wants a decreasing sigmoid and the grammar has no "
           "unary minus, so 0 - 3 is the only spelling available.' Fails as "
           "TypeError: float() argument must be ... not 'IRBinaryOp'.",
)
def test_d9_1_sigmoid_constant_folded_steepness():
    src = env_protocol("""
  reward { continuous = sigmoid("env", midpoint: 1.0, steepness: 0 - 3) }
  output { audio_gain = reward.continuous }
""")
    try:
        ir = resolve_src(src)
    except ResolveError:
        return
    step_once(ir, fs=256.0)


@pytest.mark.xfail(
    strict=True,
    reason="D9.2: 'absolute(value: <derive ref>) -> TypeError.' Fails as "
           "TypeError: float() argument must be ... not 'IRStreamRef'.",
)
def test_d9_2_absolute_with_a_derive_reference():
    src = """
protocol "d92" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(),
                 smooth(tau: 250 ms) ]
  }
  derive "slow" { from = "raw"; pipeline = [ smooth(tau: 4 s) ] }
  threshold "t" { signal = "env"; type = absolute(value: "slow") }
  reward { event = dwell(condition: above("env", "t"), duration: 250 ms) }
  output { audio_chime = reward.event }
}
"""
    try:
        ir = resolve_src(src)
    except ResolveError:
        return
    step_once(ir, fs=256.0)


@pytest.mark.xfail(
    strict=True,
    reason="D9.3: 'percentile(target_pct:, window:) as a derive pipeline stage "
           "-> TypeError. It resolves to stream<scalar uV>; it would have given "
           "occupational EMG its 10th/50th/90th-percentile exposure measures as "
           "first-class signals.' Fails as PercentileImpl.step() missing 1 "
           "required positional argument: 'x'.",
)
def test_d9_3_percentile_as_a_pipeline_stage():
    src = """
protocol "d93" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(),
                 percentile(target_pct: 90, window: 60 s) ]
  }
  output { audio_gain = 0 }
}
"""
    try:
        ir = resolve_src(src)
    except ResolveError:
        return
    step_once(ir, fs=256.0)


D9_4_SRC = """
protocol "d94" {
  requires { sample_rate = ">= 250 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"; pipeline = [ bandpass(band: (20 Hz, 450 Hz), order: 4) ] }
  output { audio_gain = 0 }
}
"""


def test_d9_4_nyquist_violation_is_caught_at_resolve():
    """FIXED. Was: 'checked only at evaluator construction, not at resolve:
    bandpass(band: (20 Hz, 450 Hz)) compiles against a 250 Hz amp.'"""
    with pytest.raises(ResolveError, match="[Nn]yquist"):
        resolve_src(D9_4_SRC)


def test_d9_4_nyquist_check_covers_the_center_bandwidth_form():
    """`bandpass(center:, bandwidth:)` has no explicit band, so the implied
    high edge is what has to be checked."""
    with pytest.raises(ResolveError, match="[Nn]yquist"):
        resolve_src("""
protocol "cb" {
  requires { sample_rate = ">= 250 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"; pipeline = [ bandpass(center: 200 Hz, bandwidth: 4 Hz, order: 4) ] }
  output { audio_gain = 0 }
}
""")


def test_d9_4_nyquist_check_also_covers_bandpower_and_coherence():
    for prim in (
        'derive "d" { formula = bandpower(input: "raw", band: (20 Hz, 450 Hz), window: 100 ms) }',
        'derive "d" { formula = coherence("raw", "raw", band: (20 Hz, 450 Hz), window: 60 s) }',
    ):
        with pytest.raises(ResolveError, match="[Nn]yquist"):
            resolve_src(f"""
protocol "bp" {{
  requires {{ sample_rate = ">= 250 Hz"; channels = ["Cz"] }}
  input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
  {prim}
  output {{ audio_gain = 0 }}
}}
""")


def test_d9_4_legal_bands_are_unaffected():
    """Guard against an off-by-one that rejects ordinary EEG bands."""
    ir = resolve_src(env_protocol('output { audio_gain = 0 }'))
    assert "env" in ir.derives


def test_d9_4_nyquist_violation_is_caught_at_least_somewhere():
    """Guard the backstop: wherever the check lives, an above-Nyquist band must
    never reach a running evaluator."""
    try:
        ir = resolve_src(D9_4_SRC)
    except ResolveError:
        return
    with pytest.raises((ValueError, ResolveError), match="[Nn]yquist"):
        step_once(ir, fs=250.0)
