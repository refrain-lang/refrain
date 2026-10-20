# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Mechanism M2 from the Coherence Recorder's testing brief: resolving implies
running.

> "Property: *any protocol that passes `resolve()` must survive stepping a
> chunk.* No third outcome. Run it over every protocol in the corpus plus every
> fixture."

This is the mechanism that closes five of the nine reported defects —
threshold-in-formula, `sigmoid(steepness: 0 - 3)`, `absolute(value: <derive>)`,
`percentile` as a pipeline stage, and a bandpass above Nyquist — and the one
that would have caught the prototype the reporter committed on the strength of
`refrain check` plus `compile-json`.

Three guards against the harness going quietly vacuous, taken from the
reporter's account of building the same thing on their side (their first run
produced 14 failures and all 14 were the rig):

1. **Discovery must find protocols**, from each corpus root separately. A glob
   that silently matches nothing would make this file pass by testing nothing.
2. **`KNOWN_UNRUNNABLE` fails in both directions** — an unlisted failure is a
   finding, and a listed entry that starts passing is a stale entry that must
   be removed.
3. **A canary** rebuilds a defect the harness is supposed to catch and asserts
   it still catches it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from refrain import parse_file
from refrain.amp_profile import load_amp_profile
from refrain.compose import filesystem_loader
from refrain.eval_ import Evaluator
from refrain.resolver import ResolveError, resolve
from refrain.synthetic import channels_for_smoke, smoke_step

REPO = Path(__file__).resolve().parents[1]

# Which engine to step protocols through. Matches tests/conftest.py's
# convention so one env var drives the whole suite.
EVAL_BACKEND = os.environ.get("REFRAIN_EVAL_BACKEND", "python")

# Corpus roots, kept separate so discovery can be asserted per root.
CORPUS_ROOTS: dict[str, Path] = {
    "examples": REPO / "examples",
    "bench": REPO / "bench" / "protocols",
    "external": REPO / "tests" / "fixtures" / "external",
}

# Minimum protocols expected per root. A floor, not an exact count, so adding
# a protocol does not break the build — but losing a whole root does.
MIN_PER_ROOT = {"examples": 8, "bench": 20, "external": 1}

# Amp profile per protocol, where the bundled default will not do. A protocol
# with no entry resolves against no amp, which takes its declared minimum rate.
AMP_FOR: dict[str, Path] = {
    "smr_cz_brainbit.refrain": REPO / "src" / "refrain" / "amp_profiles" / "brainbit_flex.json",
    "skin_temp_warming.refrain": REPO / "tests" / "fixtures" / "external" / "emotibit_md_ancillary.json",
}

# Protocols that legitimately do not resolve standalone, with the reason. These
# are not failures of the property — the property is about protocols that DO
# resolve — but they are listed so that a protocol silently dropping out of the
# corpus is visible.
KNOWN_UNRESOLVABLE: dict[str, str] = {}

# Protocols that resolve but cannot yet be stepped, with the reason. Empty is
# the goal. An entry here is a known gap, not a licence: `test_known_unrunnable
# _entries_are_still_unrunnable` fails when one starts working, which forces
# the entry out.
KNOWN_UNRUNNABLE: dict[str, str] = {}


def _discover(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.refrain"))


def _all_protocols() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for name, root in CORPUS_ROOTS.items():
        for p in _discover(root):
            out.append((name, p))
    # An optional larger corpus. The reporter offered theirs and the
    # `refrain-protocols` package is the obvious one; neither is vendored, so
    # point REFRAIN_EXTRA_CORPUS at a directory to fold it in.
    extra = os.environ.get("REFRAIN_EXTRA_CORPUS")
    if extra:
        for p in _discover(Path(extra)):
            out.append(("extra", p))
    return out


PROTOCOLS = _all_protocols()
PROTOCOL_IDS = [str(p.relative_to(REPO)) if REPO in p.parents or p.is_relative_to(REPO)
                else str(p) for _root, p in PROTOCOLS]


def _resolve_one(path: Path):
    """Resolve `path` the way a host would: with its amp profile if it needs
    one, and with a parent loader so `extends` works."""
    amp = None
    if path.name in AMP_FOR:
        amp = load_amp_profile(AMP_FOR[path.name])
    loader = filesystem_loader([path.parent, REPO / "examples"])
    return resolve(parse_file(path), amp, parent_loader=loader)


# The channel selection and the step itself now live in `refrain.synthetic`,
# because `refrain check` makes the same step per invocation (refrain#97) and
# two copies of "which channels does a passthrough montage want" is how one of
# them goes wrong. The reasoning that was written here is in their docstrings:
# padding with A1/A2 for `linked_ears` is right for a referential montage and
# fatal for `passthrough()`, and getting it wrong made this file's first run
# report five of its own bugs as the corpus's.
_channels_for = channels_for_smoke


def _step_one(ir, *, chunks: int = 3, backend: str | None = None) -> None:
    """Push a few chunks of noise through a fresh Evaluator."""
    smoke_step(ir, chunks=chunks, backend=backend or EVAL_BACKEND)


# ---------------------------------------------------------------------------
# Guard 1 — discovery is not vacuous
# ---------------------------------------------------------------------------


def test_the_backend_under_test_is_the_one_requested():
    """Guard against the whole file going quietly Python-only.

    `Evaluator(...)` sets `_backend = "python"` unconditionally; only
    `Evaluator.live(backend=...)` attaches the Rust core. If this file ever
    goes back to the bare constructor, a `REFRAIN_EVAL_BACKEND=rust` run would
    still exercise Python and report it as parity — so assert the engine that
    came back is the one asked for.
    """
    ir = _resolve_one(CORPUS_ROOTS["bench"] / "micro_single_above.refrain")
    chans = _channels_for(ir)
    ev = Evaluator.live(
        ir, sample_rate_hz=256.0, channel_names=chans, backend=EVAL_BACKEND
    )
    assert ev._backend == EVAL_BACKEND, (
        f"asked for {EVAL_BACKEND!r}, got {ev._backend!r}"
    )
    if EVAL_BACKEND == "rust":
        assert ev._rust is not None, "rust backend requested but no core attached"


@pytest.mark.parametrize("root_name", sorted(MIN_PER_ROOT))
def test_discovery_finds_protocols_in_every_corpus_root(root_name):
    found = _discover(CORPUS_ROOTS[root_name])
    assert len(found) >= MIN_PER_ROOT[root_name], (
        f"{root_name} ({CORPUS_ROOTS[root_name]}) yielded {len(found)} "
        f"protocols, expected at least {MIN_PER_ROOT[root_name]}. Either the "
        f"corpus moved or this harness is testing nothing."
    )


def test_the_corpus_is_big_enough_to_be_worth_running():
    assert len(PROTOCOLS) >= 30, f"only {len(PROTOCOLS)} protocols discovered"


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("root,path", PROTOCOLS, ids=PROTOCOL_IDS)
def test_resolves_implies_runs(root, path):
    """If it resolves, it must survive `step_chunk`. No third outcome."""
    name = path.name
    try:
        ir = _resolve_one(path)
    except ResolveError as exc:
        if name in KNOWN_UNRESOLVABLE:
            pytest.skip(f"known unresolvable: {KNOWN_UNRESOLVABLE[name]}")
        pytest.fail(
            f"{path} does not resolve: {exc}\n"
            f"If that is expected, add it to KNOWN_UNRESOLVABLE with a reason."
        )

    if name in KNOWN_UNRUNNABLE:
        pytest.skip(f"known unrunnable: {KNOWN_UNRUNNABLE[name]}")

    try:
        _step_one(ir)
    except Exception as exc:  # noqa: BLE001 — any exception is the finding
        pytest.fail(
            f"{path} RESOLVES BUT DOES NOT RUN: "
            f"{type(exc).__name__}: {exc}\n"
            f"This is the M2 property failing — `resolve()` accepted a protocol "
            f"the evaluator cannot step. Fix the resolver to reject it or the "
            f"evaluator to run it; do not add it to KNOWN_UNRUNNABLE without a "
            f"reason that says which."
        )


# ---------------------------------------------------------------------------
# Guard 2 — the allowlists cannot go stale
# ---------------------------------------------------------------------------


def test_allowlist_entries_name_real_protocols():
    discovered = {p.name for _root, p in PROTOCOLS}
    for listing, label in ((KNOWN_UNRESOLVABLE, "KNOWN_UNRESOLVABLE"),
                           (KNOWN_UNRUNNABLE, "KNOWN_UNRUNNABLE")):
        stale = sorted(set(listing) - discovered)
        assert not stale, f"{label} names protocols that no longer exist: {stale}"


@pytest.mark.parametrize("name", sorted(KNOWN_UNRUNNABLE))
def test_known_unrunnable_entries_are_still_unrunnable(name):
    """A listed protocol that starts working must be delisted, or the allowlist
    quietly becomes a place where regressions hide."""
    path = next(p for _root, p in PROTOCOLS if p.name == name)
    ir = _resolve_one(path)
    with pytest.raises(Exception):  # noqa: B017,PT011 — any exception
        _step_one(ir)


@pytest.mark.parametrize("name", sorted(KNOWN_UNRESOLVABLE))
def test_known_unresolvable_entries_are_still_unresolvable(name):
    path = next(p for _root, p in PROTOCOLS if p.name == name)
    with pytest.raises(ResolveError):
        _resolve_one(path)


# ---------------------------------------------------------------------------
# Guard 3 — the canary
# ---------------------------------------------------------------------------

# The defect that reached the reporter's repository in a committed protocol
# that had never run. It is now rejected at resolve (D6), so the canary asserts
# the harness still notices it one way or the other: either resolve refuses it,
# or stepping it raises. What must never happen is both succeeding.
CANARY_THRESHOLD_IN_FORMULA = """
protocol "canary_threshold_in_formula" {
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

# A second canary for the other half of the property: a shape that resolves and
# then dies inside the evaluator. `percentile` as a pipeline stage (D9.3) used
# to be this canary and is now implemented, which is exactly the signal the
# canary exists to give — it failed, loudly, in the commit that fixed it.
#
# Its replacement is the nearest shape still unimplemented: a threshold whose
# level tracks *another threshold*. `absolute(value: "<a derive>")` now works
# (D9.2), but only for an input or derive — the evaluator reads the level as a
# constant unless the slot holds an `IRStreamRef`, and a threshold name
# resolves to an `IRThresholdRef`, so this still dies on `float()`.
#
# It was left that way deliberately rather than by oversight. Thresholds are
# evaluated in declaration order and nothing topologically sorts them, so
# accepting a threshold here would run for a backward reference and raise
# KeyError for a forward one — trading a clear crash for an order-dependent
# one. If this canary ever resolves AND runs, either that ordering question
# was answered — delete the canary — or the harness stopped looking.
CANARY_THRESHOLD_TRACKING_A_THRESHOLD = """
protocol "canary_threshold_tracks_threshold" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" { from = "raw"; pipeline = [ smooth(tau: 250 ms) ] }
  threshold "floor" { signal = "env"; type = absolute(value: 8 uV) }
  threshold "ceiling" { signal = "env"; type = absolute(value: "floor") }
  reward { event = dwell(condition: above("env", "ceiling"), duration: 250 ms) }
  output { audio_chime = reward.event }
}
"""


@pytest.mark.parametrize(
    "src,label",
    [
        (CANARY_THRESHOLD_IN_FORMULA, "threshold referenced from a formula derive"),
        (CANARY_THRESHOLD_TRACKING_A_THRESHOLD, "a threshold tracking another threshold"),
    ],
)
def test_canary_the_harness_still_catches_a_known_bad_shape(src, label, tmp_path):
    """Rebuild a shape that must not resolve-and-run, and assert this file's own
    machinery notices. If a canary passes silently, every green run above means
    nothing."""
    p = tmp_path / "canary.refrain"
    p.write_text(src)

    try:
        ir = _resolve_one(p)
    except ResolveError:
        return  # caught at resolve — the property holds

    with pytest.raises(Exception):  # noqa: B017,PT011
        _step_one(ir)
        pytest.fail(
            f"canary failed: a protocol with {label} both resolved and ran. "
            f"Either it is now genuinely supported (delete this canary) or the "
            f"harness has stopped detecting the class."
        )
