# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
# tests/_inhibit_fixtures.py — the inhibit protocols the `kind` tests share.
# Compile-verified against the real surface syntax; do not hand-edit the
# syntax, re-compile and confirm no errors instead.
"""Protocol sources with one labelled `inhibit`, in two shapes.

`inhibit_protocol()` is the compile-side document: a single `inhibit "emg"`
whose action and `kind` label the caller chooses. Shared by
`tests/test_resolver.py` and `tests/test_ir_json.py` so the two agree on one
document — the resolve tests say what the label means and the emit tests say
what reaches the wire, and a divergence between the two documents would hide
exactly the drift those tests exist to catch.

It deliberately uses `bandpass`/`hilbert`/`magnitude` rather than
`band_envelope`: an unlabelled copy must emit IR-JSON 0.1, which it cannot do
if the derive pipeline pushes it to 0.5 on complex FIR taps.

`reward_event_protocol()` is the run-side document for the reward-event hold
path, used by `tests/test_eval_artifact_mask_reward.py`. It lives beside the
compile-side one rather than as a third inline copy in a test file.
"""

from __future__ import annotations

_BASE = '''protocol "inhibit_kind_demo" {
  meta { version = "1.0.0"; evidence = "demo"; description = "inhibit kind" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz", "A1"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "thr" { signal = "env"; type = absolute(2 uV) }
  inhibit "emg" {
    metric    = bandpower(input: "raw", band: (20 Hz, 50 Hz), window: 100 ms)
    threshold = absolute(55 uV2)
    action    = __ACTION____KIND__
  }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
}'''


def inhibit_protocol(
    *, action: str = "mute(release: 200 ms)", kind: str | None = None
) -> str:
    """Protocol source with one inhibit. `kind=None` writes no `kind` field."""
    label = "" if kind is None else f'\n    kind      = "{kind}"'
    return _BASE.replace("__ACTION__", action).replace("__KIND__", label)


# ---------------------------------------------------------------------------
# The reward-event path. `reward { event = dwell(...) }` is what 44 of the 45
# live protocols drive feedback from, so this is the document the observability
# mask has to be measured on.
#
# Two inputs reach `level` and `emg_level` through `rectify()` alone, which is
# `abs()`: no filter stands between a written sample and the comparison, so the
# dwell's condition is exactly "this sample's trained column was written above
# 0.5" and the gate is exactly "this sample's muscle column was written above
# 0.5". Anything with a group delay would smear the sample indices the tests
# assert and leave no way to tell a one-sample filter lag from a mask bug.
#
# `gain` is a constant 0.5 and the output stage zeroes a muted channel, so a
# 0.0 in `output/gain` is the mute and nothing else — the witness that the gate
# really fired, and over exactly which samples. `chime` is the patient-facing
# event channel, gated by `& ~muted`, so the difference between "the dwell
# fired" and "the trainee heard it" is readable as `reward.event` against
# `output/chime`.
# ---------------------------------------------------------------------------

_REWARD_EVENT_BASE = '''protocol "artifact gate on the reward path" {
  meta { version = "1.0"; evidence = "experimental"; description = "reward-event artifact mask" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz", "A1"] }
  input "trained" { montage = referential(active: "Cz", reference: "device") }
  input "muscle"  { montage = referential(active: "A1", reference: "device") }
  derive "level"     { from = "trained"; pipeline = [rectify()] }
  derive "emg_level" { from = "muscle";  pipeline = [rectify()] }
  threshold "t" { signal = "level"; type = absolute(0.5 uV) }
  inhibit "emg" {
    metric    = rectify("emg_level")
    threshold = absolute(0.5 uV)
    action    = __RACTION____KIND__
  }
__REWARD____OUTPUT____SCOPING__}'''

_OUTPUT = '''  output {
    chime = reward.event
    gain  = 0.5
  }
'''

# A protocol with no feedback channels at all. Legal, and the resolver accepts
# it — an analysis-only document that computes a reward and records it without
# playing anything. It matters here because it is the one shape in which the
# per-channel output loop never runs, so nothing after the reward stage tidies
# up behind it.
_NO_OUTPUT = '''  output { }
'''

_PLAIN_REWARD = '''  reward { event = dwell(condition: __CONDITION__, duration: 250 ms) }
'''

# A second inhibit, and a block that names only that one. `"emg"` is then
# outside the active block's inhibit set, so it gates nothing — and must
# therefore make nothing unobservable either, label or no label. `"idle"` has a
# limit no sample in these fixtures reaches, so the only inhibit that ever
# fires is the one the block has excluded.
_BLOCK_SCOPING = '''  inhibit "idle" {
    metric    = rectify("emg_level")
    threshold = absolute(1000 uV)
    action    = mute(release: 125 ms)
  }
  block "narrow" { inhibit = ["idle"]; output = ["chime", "gain"] }
  session { phases = [ phase { name = "train"; duration = 60 s; block = "narrow" } ] }
'''

# Two named reward bundles and a session that switches from one to the other
# part-way through. Every bundle is stepped on every chunk so its dwell timer
# stays warm, and the mask has to reach the one that is NOT live too — an
# artifact is an artifact whichever block is running. That is only readable
# from outside once the warm bundle becomes the live one, which is what the
# phase boundary below is for: it lands at 3.5 s, inside the mute, between the
# sample a blind clock would have risen on and the sample a paused one does.
#
# `"first"` holds for 10 s, longer than any fixture run, so it contributes
# nothing of its own to the published streams. Its only job is to be the live
# bundle while `"warm"` accumulates out of sight — otherwise the two bundles
# would fire together and the published streams would say nothing about which
# one they came from.
_STAGED_REWARD = '''  reward "live" { event = dwell(condition: __CONDITION__, duration: 10 s) }
  reward "warm" { event = dwell(condition: __CONDITION__, duration: 250 ms) }
'''

_STAGED_SCOPING = '''  block "first"  { reward = "live"; output = ["chime", "gain"] }
  block "second" { reward = "warm"; output = ["chime", "gain"] }
  session {
    phases = [
      phase { name = "p1"; duration = 3500 ms; block = "first" },
      phase { name = "p2"; duration = 60 s;    block = "second" },
    ]
  }
'''

#: The reward dwell's condition when the caller wants a hold nested inside it.
#: 125 ms is 32 samples at 256 Hz exactly, so no rounding stands between the
#: written source and the asserted sample index.
NESTED_HOLD_CONDITION = 'hold_for(condition: above("level", "t"), duration: 125 ms)'


def reward_event_protocol(
    *,
    kind: str | None = None,
    release: str = "125 ms",
    condition: str = 'above("level", "t")',
    mode: str = "plain",
    with_outputs: bool = True,
    action: str = "mute",
) -> str:
    """Protocol source whose reward is a 250 ms `dwell` over `condition`.

    `kind=None` writes no `kind` field. `mode` picks the shape:

    - `"plain"`: one top-level reward, no blocks and no session.
    - `"block_scoped"`: adds a second inhibit and a single-phase session whose
      block names only that one, so the `"emg"` gate is live but outside the
      active block's set.
    - `"staged"`: replaces the top-level reward with two named bundles and a
      session that makes the second one live at 3.5 s.

    `with_outputs=False` writes an empty `output { }`: a legal protocol that
    computes a reward and plays nothing, and the only shape in which the
    per-channel output loop never runs.
    """
    label = "" if kind is None else f'\n    kind      = "{kind}"'
    # `flag()` takes no arguments, so it swallows `release` rather than
    # carrying it: an artifact flag has no audio gate and so no hangover.
    raction = "flag()" if action == "flag" else f"{action}(release: {release})"
    reward, scoping = {
        "plain": (_PLAIN_REWARD, ""),
        "block_scoped": (_PLAIN_REWARD, _BLOCK_SCOPING),
        "staged": (_STAGED_REWARD, _STAGED_SCOPING),
    }[mode]
    return (
        _REWARD_EVENT_BASE
        .replace("__RACTION__", raction)
        .replace("__KIND__", label)
        .replace("__REWARD__", reward)
        .replace("__OUTPUT__", _OUTPUT if with_outputs else _NO_OUTPUT)
        .replace("__SCOPING__", scoping)
        .replace("__CONDITION__", condition)
    )
