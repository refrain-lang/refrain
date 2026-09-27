# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Evaluator host API for autopilot advice (SPEC §5)."""

import numpy as np
import pytest

from refrain import parse
from refrain.resolver import resolve
from refrain.advisor import AdviceError
from refrain.eval_ import Evaluator
from tests._autopilot_fixtures import ap, plain

SR = 256.0


def _live(src, backend="python", **bindings):
    ir = resolve(parse(src), bindings=bindings or None)
    ev = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("Cz",), backend=backend)
    ev.start(skip_warmup=False)
    return ev


def _chunks(ev, n, value=1.0):
    for _ in range(n):
        ev.step_chunk(np.full((256, 1), value, dtype=np.float64))


def test_advice_before_and_during_settle():
    ev = _live(ap(emg_thr="100"))
    assert ev.advice()["reason"] == "not_training_phase"
    _chunks(ev, 5)                                   # inside the 10 s muted settle
    assert ev.advice()["reason"] == "not_training_phase"


def test_training_phase_collects_then_judges():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 12)                                  # 10 s settle + 2 s training
    a = ev.advice()
    assert a["reason"] == "collecting" and a["evidence"]["clean_s"] == 2.0
    _chunks(ev, 20)
    # Constant input: ratio == 1.0 > xover 0.6 always; t_env == its own
    # percentile, so theta (strictly above) rarely passes -> too strict on theta.
    a = ev.advice()
    assert a["state"] in ("adjust", "hold") and a["evidence"]["clean_s"] >= 20.0


def test_set_control_is_a_manual_change():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 15)
    ev.drain_advice_events()
    ev.set_control("xover", 0.7)
    kinds = [e["kind"] for e in ev.drain_advice_events()]
    assert kinds[0] == "changed_manually"
    assert ev.advice()["reason"] == "collecting"


def test_apply_advice_changes_the_control():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 12)
    adv = ev._advisor
    adv.values["xover"] = 0.6
    # Force a known adjust through the advisor, then apply via the Evaluator.
    from tests.test_advisor import facts, strict
    for checks in strict():
        adv.feed(facts(checks=checks))
    a = ev.advice()
    assert a["state"] == "adjust" and a["control"]["name"] == "xover"
    event = ev.apply_advice(a["id"], by="autopilot")
    assert event["kind"] == "applied" and ev._controls["control/xover"] == 0.55
    with pytest.raises(AdviceError):
        ev.apply_advice(a["id"])


def test_equipment_change_and_policy():
    ev = _live(ap(emg_thr="100"))
    _chunks(ev, 12)
    ev.mark_equipment_change()
    assert ev.advice()["reason"] == "equipment_settling"
    assert ev.autopilot_policy()["controls"]["xover"]["apply"] == "auto"


def test_plain_protocol_gets_observations():
    ev = _live(plain(emg_thr="100"))
    _chunks(ev, 12)
    a = ev.advice()
    assert a["reason"] == "collecting" and a["evidence"]["required_s"] == 120.0


def test_rust_backend_advice_matches_python():
    """Review Focus #5."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    src = ap(emg_thr="100")
    py, rs = _live(src, "python"), _live(src, "rust")
    rng = np.random.default_rng(3)
    for _ in range(40):
        chunk = rng.normal(0.0, 5.0, size=(256, 1))
        py.step_chunk(chunk)
        rs.step_chunk(chunk)
        assert rs.advice() == py.advice()
        assert rs.drain_advice_events() == py.drain_advice_events()
    assert rs.autopilot_policy() == py.autopilot_policy()
    with pytest.raises(AdviceError):
        rs.apply_advice("adv-9999")


# A second inhibit that fires almost all the time, left out of the block the
# training phases run: it does not mute output, so it must not hold advice
# as a guard either.
_BLOCKED_OUT = ap(emg_thr="100").replace(
    "output { audio_chime = reward.event }",
    '''inhibit "blink" {
    metric    = bandpower(input: "raw", band: (1 Hz, 4 Hz), window: 100 ms)
    threshold = percentile(target_pct: 1, window: 2 min)
    action    = mute(release: 200 ms)
  }
  output { audio_chime = reward.event }
  block "focus" { inhibit = ["emg"] }''').replace(
    'phase { name = "train1"; duration = 300 s }',
    'phase { name = "train1"; duration = 300 s; block = "focus" }').replace(
    'phase { name = "train2"; duration = 300 s }',
    'phase { name = "train2"; duration = 300 s; block = "focus" }')


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_guards_only_count_the_active_blocks_inhibits(backend):
    if backend == "rust":
        pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    ev = _live(_BLOCKED_OUT, backend)
    rng = np.random.default_rng(3)
    for _ in range(35):
        ev.step_chunk(rng.normal(0.0, 5.0, size=(256, 1)))
    a = ev.advice()
    assert a["reason"] != "guard"
    assert a["evidence"]["guards"] == {"blink": 0.0, "emg": 0.0}


def test_rust_backend_apply_dismiss_and_equipment_round_trip():
    """A successful apply_advice and dismiss_advice, then an equipment change,
    on both backends over the same real session: every return value, advice
    result, audit event and control value must match."""
    pytest.importorskip("refrain_core", reason="refrain_core wheel not installed")
    from pathlib import Path

    from refrain import parse_file
    ir = resolve(parse_file(Path(__file__).resolve().parents[1]
                            / "bench" / "protocols" / "autopilot_staged.refrain"))
    engines = {}
    for backend in ("python", "rust"):
        engines[backend] = Evaluator.live(ir, sample_rate_hz=SR, channel_names=("Cz",),
                                          backend=backend)
        engines[backend].start(skip_warmup=False)
    py, rs = engines["python"], engines["rust"]
    n = 256 * 50
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    x = (np.sin(2 * np.pi * 6 * t) * (1 + 0.8 * np.sin(2 * np.pi * t / 20))
         + 0.6 * np.sin(2 * np.pi * 10 * t) + 0.05 * rng.standard_normal(n))
    did = set()
    for i in range(0, n, 256):
        chunk = x[i:i + 256].reshape(-1, 1)
        py.step_chunk(chunk)
        rs.step_chunk(chunk)
        a = py.advice()
        assert rs.advice() == a
        if a["state"] == "adjust" and "apply" not in did:
            applied = py.apply_advice(a["id"], by="clinician")
            assert rs.apply_advice(a["id"], by="clinician") == applied
            assert applied["kind"] == "applied" and applied["to"] == a["control"]["proposed"]
            did.add("apply")
        elif a["state"] == "adjust" and "apply" in did and "dismiss" not in did:
            assert rs.dismiss_advice(a["id"]) == py.dismiss_advice(a["id"])
            did.add("dismiss")
            py.mark_equipment_change()
            rs.mark_equipment_change()
        assert rs.advice() == py.advice()
        assert rs.drain_advice_events() == py.drain_advice_events()
    assert did == {"apply", "dismiss"}
