# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Scripted advisor scenarios for the Python<->Rust parity fixtures. Each is
(protocol source, resolve bindings, ops). Keep them covering every reason
code, every event kind, both strategies' arithmetic and the compact-facts
decoder's list forms."""

from tests._autopilot_fixtures import AUTOPILOT_BLOCK, ap, plain

REBASE = ('autopilot = rebaseline { fixes = "theta"; from = "t_env"; window = 10 s; '
          'percentile = 60; higher_is = "harder"; apply = "suggest"; '
          'only_when = threshold_style == "baseline" }')


def feed(checks=(True, True), *, muted=False, emg=None, phase=1, name="train1", running=True,
         output_muted=False, frozen=False, events=0, t_env=5.0, n=256, k=1):
    op = {"op": "feed", "n": n, "running": running, "phase_index": phase, "phase_name": name,
          "output_muted": output_muted, "clock_frozen": frozen, "bundle": None,
          "muted": muted, "inhibits": {"emg": muted if emg is None else emg},
          "checks": list(checks), "events": events, "derive_samples": {"derive/t_env": t_env}}
    return [op] * k


def strict(k=20, hits=1):
    return [feed((True, i < hits))[0] for i in range(k)]


EASY = feed(k=20)


def op(kind, **kw):
    return [{"op": kind, **kw}]


SCENARIOS = {
    "too_strict_apply_cooldown": (ap(), {}, strict() + op("apply", id="adv-0001", by="autopilot")
                                  + strict() + strict(10, 1)),
    "too_easy_reversal": (ap(), {}, EASY + op("apply", id="adv-0001", by="practitioner")
                          + strict(hits=0)),
    "fallback_and_refusals": (ap(), {}, op("note", control="xover", value=1.0, source="seed")
                              + EASY + op("apply", id="adv-0001", by="autopilot")
                              + op("apply", id="adv-0001", by="practitioner")
                              + op("apply", id="adv-0009", by="practitioner")),
    "manual_outside_limits": (ap(), {}, op("note", control="xover", value=0.45, source="manual")
                              + strict()),
    "guard_block_and_equipment": (ap(), {}, strict() + feed((True, False), muted=True, k=5)
                                  + op("equipment") + feed(k=7)),
    "dismiss": (ap(), {}, strict() + op("dismiss", id="adv-0001") + strict(k=12)),
    "phases": (ap(), {}, feed(phase=0, name="settle", output_muted=True, k=3) + feed(k=20)
               + feed(phase=2, name="rest", output_muted=True) + feed(phase=3, name="train2", k=2)
               + feed(phase=3, name="train2", frozen=True)),
    "baseline_proportional": (ap(), {"threshold_style": "baseline"},
                              [feed((i < 1, True))[0] for i in range(20)]),
    "rebaseline": (ap(t_uv_ap=REBASE), {"threshold_style": "baseline"},
                   [feed((i < 1, True), t_env=5.0 + 0.1 * i)[0] for i in range(20)]),
    "hint": (plain(theta_as=' as "theta"', xover_as=' as "crossover"'), {},
             strict(k=120, hits=6) + op("dismiss", id="adv-0001") + strict(k=3, hits=0)),
    "no_hint_guard_knob": (plain(emg_thr="xover"), {}, strict(k=120, hits=6)),
    "stale": (ap(autopilot=AUTOPILOT_BLOCK.replace("max = 15%", "max = 99%")), {},
              (feed() + feed(muted=True, emg=False, k=7)) * 25),
    "mixed_lists": (ap(), {}, [
        feed((True, i % 3 == 0), muted=[j % 5 == 0 for j in range(256)],
             emg=[j % 7 == 0 for j in range(256)], events=i % 4)[0] for i in range(40)]),
    # A zero-length chunk fed at time 0 followed by normal chunks. Regression
    # coverage for the horizon-underflow bug fixed in the Rust port's
    # ingest()/evidence() (see advisor.rs's `zero_length_chunk_at_time_zero_
    # still_has_evidence` unit test): a naive `saturating_sub` horizon drops
    # the zero-length bucket that Python's signed subtraction keeps.
    "empty_chunk": (ap(), {}, feed(n=0) + feed(k=3)),
    # A standing too_strict suggestion whose evidence window then slides into
    # the reward-target band (on_track, no key) without ever being applied or
    # dismissed: the only way to reach the "superseded" event kind, which
    # none of the other scripted scenarios produce.
    "superseded_by_ontrack": (ap(), {}, strict(hits=1) + strict(hits=4)),
    # Writing a control's current value is a no-op for both sources: no
    # event, no window restart, no cooldown, the standing id survives; and
    # after an apply, re-writing the applied value leaves the pending
    # reversal check armed.
    "noop_write": (ap(), {}, strict() + op("note", control="xover", value=0.6, source="manual")
                   + op("note", control="xover", value=0.6, source="seed") + strict(k=2)
                   + op("apply", id="adv-0001", by="practitioner")
                   + op("note", control="xover", value=0.55, source="manual")
                   + feed(k=3) + EASY),
    # A fired reversal clears once a later full window is back in band.
    "reversal_clears": (ap(), {}, EASY + op("apply", id="adv-0001", by="practitioner")
                        + strict(hits=0) + feed(k=2) + strict(hits=0)),
}
