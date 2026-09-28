# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""The autopilot advisor state machine (SPEC §7.10), driven by scripted facts.
1 chunk = 256 samples = 1 s. The ap() fixture has watch 20 s, between_moves
30 s, settle 5 s, target 10-35%, tighten_first [crossover, theta]."""

import numpy as np
import pytest

from refrain import parse
from refrain.resolver import resolve
from refrain.advisor import (
    AdviceError, Advisor, ChunkFacts, facts_from_json, fmt_value, mmss, pct,
    percentile_of, r6,
)
from refrain.ir_json import ir_to_json_obj
from tests._autopilot_fixtures import AUTOPILOT_BLOCK, ap, plain

SR = 256


def make(src=None, **bindings):
    ir = resolve(parse(src or ap()), bindings=bindings or None)
    return Advisor(ir_to_json_obj(ir), SR)


def facts(*, checks=(True, True), muted=False, emg=None, phase=1, name="train1",
          running=True, output_muted=False, frozen=False, events=0, t_env=5.0, n=SR):
    b = lambda v: np.full(n, bool(v))  # noqa: E731
    ev = np.zeros(n, dtype=bool)
    ev[:events] = True
    return ChunkFacts(
        n=n, running=running, phase_index=phase, phase_name=name,
        output_muted=output_muted, clock_frozen=frozen, bundle=None,
        muted=b(muted), inhibits={"emg": b(muted if emg is None else emg)},
        checks=[b(c) for c in checks], events=ev,
        derive_samples={"derive/t_env": np.full(n, t_env)},
    )


def run(adv, pattern, **kw):
    for checks in pattern:
        adv.feed(facts(checks=checks, **kw))
    return adv.advice()


def strict(k=20, hits=1):
    """k chunks, theta always passes, crossover passes in `hits` chunks."""
    return [(True, i < hits) for i in range(k)]


EASY = [(True, True)] * 20


# --- helpers --------------------------------------------------------------

def test_helpers():
    assert r6(0.1234566) == 0.123457 and r6(-0.1234566) == -0.123457
    assert pct(0.125) == 13 and pct(0.05) == 5
    assert mmss(65.9) == "1:05" and mmss(20) == "0:20"
    assert fmt_value(0.55, 2, "") == "0.55"
    assert fmt_value(15.0, 0, "%") == "15%"
    assert fmt_value(7.2, 1, "uV") == "7.2 uV"
    assert percentile_of([5, 1, 3, 2, 4], 60) == pytest.approx(3.4)


# --- observation steps (§4.4 steps 1-4, 6) ---------------------------------

def test_before_training_and_in_muted_phase():
    adv = make()
    assert adv.advice()["reason"] == "not_training_phase"
    adv.feed(facts(phase=0, name="settle", output_muted=True))
    a = adv.advice()
    assert (a["state"], a["reason"]) == ("hold", "not_training_phase")
    assert a["message"] == "Advice paused: not a training phase."


def test_clock_frozen_is_not_training():
    adv = make()
    adv.feed(facts(frozen=True))
    assert adv.advice()["reason"] == "not_training_phase"


def test_collecting_shows_progress():
    a = run(make(), [(True, True)] * 5)
    assert (a["state"], a["reason"]) == ("collecting", "collecting")
    assert a["message"] == "Collecting clean signal (0:05 of 0:20)."
    assert a["evidence"]["clean_s"] == 5.0 and a["evidence"]["required_s"] == 20.0


def test_on_track():
    a = run(make(), [(True, i < 4) for i in range(20)])
    assert (a["state"], a["reason"]) == ("hold", "on_track")
    assert a["message"] == "On track. Reward met 20% of clean time (target 10-35%)."
    assert a["evidence"]["checks"] == {"theta": 1.0, "crossover": 0.2}
    assert a["id"] is None


def test_guard_holds_with_its_message():
    adv = make()
    run(adv, [(True, True)] * 16)
    a = run(adv, [(True, True)] * 4, muted=True)
    assert (a["state"], a["reason"]) == ("hold", "guard")
    assert a["message"] == "Muscle artifact. (emg guard active 20% of training time)."


def test_guard_with_no_say_uses_the_default_message():
    """A guard with no `say` skips the generic lead-in sentence entirely —
    the message is just the fact, not "Frequent X guard activity. (...)"."""
    adv = make(ap(autopilot=AUTOPILOT_BLOCK.replace(' say = "Muscle artifact."', '')))
    run(adv, [(True, True)] * 16)
    a = run(adv, [(True, True)] * 4, muted=True)
    assert (a["state"], a["reason"]) == ("hold", "guard")
    assert a["message"] == "emg guard active 20% of training time."


def test_stale_clean_time_drops_out():
    """Review Focus #2: 1 clean chunk in every 8 never adds up to 20 s of
    evidence, because clean time older than 2 x watch (40 s) is dropped."""
    adv = make(ap(autopilot=AUTOPILOT_BLOCK.replace("max = 15%", "max = 99%")))
    for _ in range(25):
        adv.feed(facts())
        for _ in range(7):
            adv.feed(facts(muted=True, emg=False))
    a = adv.advice()
    assert a["reason"] == "collecting"
    assert a["evidence"]["clean_s"] <= 5.0


def test_equipment_change_settles_then_restarts():
    adv = make()
    run(adv, strict())
    adv.mark_equipment_change()
    a = adv.advice()
    assert (a["reason"], a["message"]) == (
        "equipment_settling", "Settling after equipment change (5 s left).")
    run(adv, [(True, True)] * 5)                       # the settle window: not counted
    a = run(adv, [(True, True)])
    assert a["reason"] == "collecting" and a["evidence"]["clean_s"] == 1.0
    assert [e["kind"] for e in adv.drain_events()][-2:] == ["equipment_change", "blocked"]


def test_new_training_phase_restarts_window():
    adv = make()
    run(adv, [(True, True)] * 20)
    adv.feed(facts(phase=2, name="rest", output_muted=True))
    a = run(adv, [(True, True)], phase=3, name="train2")
    assert a["reason"] == "collecting" and a["evidence"]["clean_s"] == 1.0


def test_observing_when_protocol_has_no_reward_condition():
    from tests._seed_fixtures import NON_SEEDING
    adv = make(NON_SEEDING)
    for _ in range(120):
        adv.feed(facts(checks=(), name="run"))
    a = adv.advice()
    assert a["reason"] == "observing"
    assert a["message"] == "Observing: this protocol has no reward condition to judge."


# --- decisions (§4.4 steps 7-11, §4.5) -------------------------------------

def test_too_strict_lowers_the_crossover_target():
    adv = make()
    a = run(adv, strict())
    assert (a["state"], a["reason"], a["level"], a["id"]) == ("adjust", "too_strict", "policy", "adv-0001")
    assert a["message"] == ("Reward met 5% of clean time (target 10-35%). "
                            "The limiter is crossover. Lower Crossover target 0.60 -> 0.55.")
    assert a["limiter"] == {"check": "crossover", "pass_rate": 0.05}
    c = a["control"]
    assert (c["name"], c["current"], c["proposed"], c["direction"], c["auto_allowed"]) == (
        "xover", 0.6, 0.55, "easier", True)
    assert a["eligible_at_s"] == a["t_s"] == 20.0
    assert adv.drain_events() == [{"advisor_version": "1", "t_s": 20.0, "kind": "suggested",
                                   "id": "adv-0001", "reason": "too_strict", "control": "xover",
                                   "from": 0.6, "to": 0.55}]


def test_same_suggestion_keeps_its_id():
    adv = make()
    run(adv, strict())
    a = run(adv, [(True, False)])
    assert a["id"] == "adv-0001" and adv.drain_events()[-1]["kind"] == "suggested"


def test_too_easy_tightens_the_first_listed_check():
    a = run(make(), EASY)
    assert (a["reason"], a["control"]["name"], a["control"]["proposed"]) == ("too_easy", "xover", 0.65)
    assert a["message"].endswith("Raise Crossover target 0.60 -> 0.65.")


def test_too_easy_falls_back_when_first_is_at_limit():
    adv = make()
    adv.note_control("xover", 1.0, "seed")
    a = run(adv, EASY)
    assert (a["control"]["name"], a["control"]["proposed"], a["control"]["auto_allowed"]) == (
        "t_pct", 20.0, False)


def test_limiter_without_policy_holds():
    a = run(make(ap(xover_ap="")), strict())
    assert (a["state"], a["reason"]) == ("hold", "no_knob")
    assert a["message"].endswith("The limiter is crossover. No adjustable setting addresses it; holding.")


def test_limiter_message_is_used():
    block = AUTOPILOT_BLOCK.replace(
        'emg = guard { max = 15%; say = "Muscle artifact." }',
        'emg = guard { max = 15%; say = "Muscle artifact." }\n    crossover = limiter { say = "Coach." }')
    a = run(make(ap(xover_ap="", autopilot=block)), strict())
    assert a["message"].endswith("The limiter is crossover. Coach.")


def test_manual_value_outside_limits_never_proposes_wrong_direction():
    """Review Focus #1: the clinician set 0.45 (below the 0.5 limit). Easing
    means lowering; clamping would raise it to 0.5, the wrong way — hold."""
    adv = make()
    adv.note_control("xover", 0.45, "manual")
    a = run(adv, strict())
    assert (a["state"], a["reason"]) == ("hold", "at_limit")
    assert a["message"].endswith("Crossover target is already at its easiest allowed value (0.45).")


def test_at_limit_hardest_when_all_tighten_first_knobs_are_maxed():
    """Both tighten_first knobs (crossover -> xover, theta -> t_pct) are
    already at their hardest value, so the fallback picks the best-passing
    check and reports it stuck at the top of its range."""
    adv = make()
    adv.note_control("xover", 1.0, "seed")
    adv.note_control("t_pct", 40.0, "seed")
    a = run(adv, EASY)
    assert (a["state"], a["reason"]) == ("hold", "at_limit")
    assert (a["control"]["name"], a["control"]["proposed"], a["control"]["strategy"]) == (
        "t_pct", None, "fixed_step")
    assert a["message"] == ("Reward met 100% of clean time (target 10-35%). "
                            "t_pct is already at its hardest allowed value (40.00%).")


def test_proportional_step_in_baseline_mode():
    a = run(make(threshold_style="baseline"), [(i < 1, True) for i in range(20)])
    c = a["control"]
    assert (c["name"], c["current"], c["proposed"], c["auto_allowed"]) == ("t_uv", 8.0, 7.2, False)
    assert a["message"].endswith("Lower t_uv 8.0 uV -> 7.2 uV.")


def test_rebaseline_proposes_signal_percentile():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    adv = make(ap(t_uv_ap=pol), threshold_style="baseline")
    a = run(adv, [(i < 1, True) for i in range(20)], t_env=5.0)
    assert (a["control"]["strategy"], a["control"]["proposed"]) == ("rebaseline", 5.0)


def test_rebaseline_wrong_direction_holds_without_a_proposal():
    """The signal sits above the current threshold, so re-baselining to its
    percentile would raise the threshold — but the reward is too strict and
    needs it lowered. There is no direction that helps, so hold at_limit
    instead of proposing a value that moves the wrong way."""
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    adv = make(ap(t_uv_ap=pol), threshold_style="baseline")
    a = run(adv, [(i < 1, True) for i in range(20)], t_env=20.0)
    assert (a["state"], a["reason"]) == ("hold", "at_limit")
    assert (a["control"]["name"], a["control"]["proposed"], a["control"]["strategy"]) == (
        "t_uv", None, "rebaseline")
    assert a["message"] == ("Reward met 5% of clean time (target 10-35%). "
                            "Re-baselining t_uv would not make reward easier right now.")


def test_hint_when_protocol_has_no_policies():
    adv = make(plain(theta_as=' as "theta"', xover_as=' as "crossover"'))
    a = run(adv, strict(k=120, hits=6))
    assert (a["state"], a["level"], a["id"]) == ("hint", "hint", "adv-0001")
    assert a["message"] == ("Reward met 5% of clean time (target 50-75%). The limiter is crossover. "
                            "Consider easing Crossover target (lower is easier).")
    assert a["control"]["proposed"] is None and a["control"]["auto_allowed"] is False


def test_hint_dismissed_then_holds_for_cooldown():
    adv = make(plain(theta_as=' as "theta"', xover_as=' as "crossover"'))
    a = run(adv, strict(k=120, hits=6))
    ev = adv.dismiss(a["id"])
    assert ev["kind"] == "dismissed" and ev["control"] == "xover"
    b = run(adv, [(True, False)])
    assert (b["state"], b["level"], b["reason"], b["id"]) == ("hold", "hint", "cooldown", None)
    assert b["message"] == ("Reward met 4% of clean time (target 50-75%). The limiter is crossover. "
                            "Hint dismissed; holding for 2:59.")
    assert b["eligible_at_s"] == 300.0


def test_no_hint_for_a_knob_that_feeds_a_guard():
    adv = make(plain(emg_thr="xover"))
    a = run(adv, strict(k=120, hits=6))
    assert a["reason"] == "no_knob" and a["message"].endswith("The limiter is check 1. "
                                                              "No adjustable setting addresses it; holding.")


# --- lifecycle (§4.7) -------------------------------------------------------

def test_apply_then_cooldown_then_next_step():
    adv = make()
    a = run(adv, strict())
    control, value, ev = adv.apply(a["id"], "autopilot")
    assert (control, value) == ("xover", 0.55)
    assert ev == {"advisor_version": "1", "t_s": 20.0, "kind": "applied", "id": "adv-0001",
                  "control": "xover", "from": 0.6, "to": 0.55, "by": "autopilot"}
    assert adv.advice()["reason"] == "collecting"
    a = run(adv, strict())
    assert (a["reason"], a["eligible_at_s"]) == ("cooldown", 50.0)
    assert a["message"].endswith("Crossover target can change again in 0:10.")
    a = run(adv, strict(k=10, hits=1))
    assert (a["reason"], a["id"], a["control"]["proposed"]) == ("too_strict", "adv-0002", 0.5)


def test_reversal_after_a_bad_tightening_bypasses_cooldown():
    adv = make()
    a = run(adv, EASY)
    adv.apply(a["id"], "clinician")                  # 0.60 -> 0.65
    a = run(adv, strict(hits=0))                      # reward collapses to 0%
    assert (a["state"], a["reason"], a["control"]["proposed"]) == ("adjust", "reversal", 0.6)
    assert a["message"] == ("The last change made reward worse (0% of clean time). "
                            "Return Crossover target 0.65 -> 0.60.")


def test_reversal_clears_once_a_later_window_is_back_in_band():
    """A fired reversal is not permanent: when a later full evidence window
    lands inside the target band, the reversal is dropped and the normal
    decision (here: on track) takes over."""
    adv = make()
    a = run(adv, EASY)
    adv.apply(a["id"], "clinician")                  # 0.60 -> 0.65
    a = run(adv, strict(hits=0))
    assert a["reason"] == "reversal"
    adv.drain_events()
    a = run(adv, [(True, True)] * 2)                  # window 2/20 = 10%: in band
    assert (a["state"], a["reason"], a["id"]) == ("hold", "on_track", None)
    assert adv.reversal is None
    assert [e["kind"] for e in adv.drain_events()] == ["superseded"]
    a = run(adv, strict(k=20, hits=0))               # worse again: no reversal left
    assert a["reason"] == "too_strict"


def test_apply_stale_id_is_refused_and_value_unchanged():
    """Review Focus #3."""
    adv = make()
    a = run(adv, strict())
    with pytest.raises(AdviceError):
        adv.apply("adv-9999", "autopilot")
    adv.dismiss(a["id"])
    with pytest.raises(AdviceError):
        adv.apply(a["id"], "clinician")
    assert adv.values["xover"] == 0.6


def test_autopilot_cannot_apply_a_suggest_only_change():
    adv = make()
    adv.note_control("xover", 1.0, "seed")
    a = run(adv, EASY)                                   # t_pct, suggest-only
    with pytest.raises(AdviceError, match="suggestion"):
        adv.apply(a["id"], "autopilot")
    assert adv.apply(a["id"], "clinician")[1] == 20.0
    with pytest.raises(ValueError):
        adv.apply("adv-0001", "robot")


def test_dismiss_suppresses_the_same_move():
    adv = make()
    a = run(adv, strict())
    ev = adv.dismiss(a["id"])
    assert ev["kind"] == "dismissed" and ev["control"] == "xover"
    b = adv.advice()
    assert (b["reason"], b["eligible_at_s"]) == ("cooldown", 50.0)


def test_manual_change_supersedes_and_restarts():
    adv = make()
    run(adv, strict())
    adv.drain_events()
    adv.note_control("xover", 0.7, "manual")
    assert [e["kind"] for e in adv.drain_events()] == ["changed_manually", "superseded"]
    assert adv.advice()["reason"] == "collecting"


def test_note_control_seed_restarts_window_silently():
    """A seeded value (the evaluator applying a control's own `seed` policy)
    changes the value and restarts the evidence window like a manual change,
    but — unlike "manual" — raises no `changed_manually` audit event."""
    adv = make()
    run(adv, [(True, True)] * 10)
    assert adv.advice()["evidence"]["clean_s"] == 10.0
    adv.drain_events()
    adv.note_control("xover", 0.7, "seed")
    assert adv.drain_events() == []
    assert adv.values["xover"] == 0.7
    a = adv.advice()
    assert a["reason"] == "collecting" and a["evidence"] is None


def test_note_control_with_an_unchanged_value_is_a_no_op():
    """Writing the value a control already has changes nothing: no audit
    event, no window restart, no cooldown, the standing suggestion survives."""
    adv = make()
    a = run(adv, strict())
    adv.drain_events()
    for source in ("manual", "seed"):
        adv.note_control("xover", 0.6, source)
        assert adv.drain_events() == []
        b = adv.advice()
        assert (b["id"], b["reason"]) == (a["id"], "too_strict")
        assert b["evidence"]["clean_s"] == 20.0
    assert adv.last_move_at is None


def test_note_control_unchanged_value_keeps_a_pending_reversal():
    adv = make()
    a = run(adv, EASY)
    adv.apply(a["id"], "clinician")                  # 0.60 -> 0.65
    adv.note_control("xover", 0.65, "manual")
    a = run(adv, strict(hits=0))
    assert a["reason"] == "reversal"


def test_note_control_rejects_a_bad_source():
    adv = make()
    with pytest.raises(ValueError, match="source must be 'manual' or 'seed'"):
        adv.note_control("xover", 0.7, "robot")
    assert adv.values["xover"] == 0.6


def test_guard_hold_blocks_a_standing_suggestion():
    adv = make()
    run(adv, strict())
    adv.drain_events()
    run(adv, [(True, False)] * 5, muted=True)
    assert [e["kind"] for e in adv.drain_events()] == ["blocked"]


def test_policy_description():
    p = make().policy()
    assert p["provenance"] == {"evidence": "exploratory", "citation": ["Test policy"],
                               "rationale": "Test rationale", "reviewed": None}
    assert p["controls"]["xover"]["apply"] == "auto" and p["watch_s"] == 20.0
    assert p["guards"]["emg"] == {"max": 0.15, "say": "Muscle artifact."}


def test_rebaseline_sources_lists_canonical_derive_names():
    pol = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
           'percentile = 60; higher_is = "harder"; apply = "suggest"; '
           'only_when = threshold_style == "baseline" }')
    adv = make(ap(t_uv_ap=pol), threshold_style="baseline")
    assert adv.rebaseline_sources() == ["derive/t_env"]
    assert make().rebaseline_sources() == []


def test_facts_from_json_expands_compact_fields():
    f = facts_from_json({"op": "feed", "n": 4, "running": True, "phase_index": 1,
                         "phase_name": "train1", "output_muted": False, "clock_frozen": False,
                         "bundle": None, "muted": [False, True, False, False],
                         "inhibits": {"emg": False}, "checks": [True, False], "events": 1,
                         "derive_samples": {"derive/t_env": 2.5}})
    assert f.muted.tolist() == [False, True, False, False]
    assert f.checks[1].tolist() == [False] * 4
    assert f.events.tolist() == [True, False, False, False]
    assert f.derive_samples["derive/t_env"].tolist() == [2.5] * 4
