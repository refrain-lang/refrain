# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Increment 0 detectors: unsupported shapes raise UnsupportedProtocol."""
from __future__ import annotations

from pathlib import Path

import pytest

from refrain.fuzz.errors import UnsupportedProtocol
from refrain.fuzz.surface import build_surface
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ir(rel: str, *, library: str | None = None):
    from refrain.compose import filesystem_loader
    loader = filesystem_loader([REPO_ROOT / library]) if library else None
    return resolve(parse_file(REPO_ROOT / rel), None, parent_loader=loader)


def test_single_condition_reward_raises_typed_skip():
    # composite_smr_theta has a bare dwell(above(...)) reward -> ConditionLeaf.
    # Inc 1: the leaf classifier gives the specific "composite-signal" reason
    # (the condition signal is reward.composite, not a derive).
    ir = _ir("bench/protocols/composite_smr_theta.refrain")
    with pytest.raises(UnsupportedProtocol) as exc:
        build_surface(ir)
    assert exc.value.reason == "composite-signal reward condition"


def test_center_bandwidth_bandpass_falls_through_to_a_typed_reward_skip():
    """Was `test_center_bandwidth_bandpass_no_longer_raises_typed_skip`, which
    asserted a plain ValueError and `not isinstance(..., UnsupportedProtocol)`.

    othmer_ilf_t3t4 gets past bandpass parsing and then has no `reward.event`
    to fuzz at all. That is a shape the fuzzer does not model, which is what
    `UnsupportedProtocol` is for — the bare ValueError put it in the same
    bucket as a crash, and the backstop now treats that bucket as a build
    failure.
    """
    ir = _ir("examples/othmer_ilf_t3t4.refrain")
    with pytest.raises(UnsupportedProtocol) as exc:
        build_surface(ir)
    assert exc.value.reason == "no reward.event to fuzz"


def test_supported_protocol_still_builds():
    ir = _ir("bench/protocols/realistic_smr.refrain")
    surface = build_surface(ir)  # no raise
    assert surface.protocol_name


def test_unmodelled_condition_is_a_typed_skip_that_names_the_primitive():
    """Was `test_unrecognized_condition_stays_valueerror_not_typed`, which
    pinned the plain-ValueError behaviour as an Increment-0 state.

    micro_08_bandpower reaches `_condition_from_ir` with an `inside(...)` call.
    "We don't model `inside()`" is a declinable shape, and the reason now says
    which primitive — the old message was `unrecognized condition expr IRCall`,
    which does not tell a reader what to add support for.
    """
    ir = _ir("bench/protocols/micro_08_bandpower.refrain")
    with pytest.raises(UnsupportedProtocol) as exc:
        build_surface(ir)
    assert exc.value.reason == "condition shape not modelled (inside())"


# ---------------------------------------------------------------------------
# Increment 1: single-leaf supportability detector
# ---------------------------------------------------------------------------


def test_single_above_absolute_builds():
    ir = _ir("bench/protocols/micro_single_above.refrain")
    from refrain.fuzz.surface import ConditionLeaf
    surf = build_surface(ir)          # no raise
    assert isinstance(surf.reward_condition, ConditionLeaf)
    assert surf.reward_condition.op == "above"


def test_single_below_absolute_builds():
    ir = _ir("bench/protocols/micro_single_below.refrain")
    from refrain.fuzz.surface import ConditionLeaf
    surf = build_surface(ir)
    assert isinstance(surf.reward_condition, ConditionLeaf)
    assert surf.reward_condition.op == "below"



def test_coherence_single_leaf_reason():
    with pytest.raises(UnsupportedProtocol) as e:
        build_surface(_ir("examples/dyadic_alpha_coherence_pz.refrain"))
    assert e.value.reason == "non-bandpass (coherence) reward signal"


def test_no_threshold_single_leaf_reason():
    with pytest.raises(UnsupportedProtocol) as e:
        build_surface(_ir("examples/alpha_theta.refrain"))
    assert e.value.reason == "reward condition without a resolvable threshold"


def test_dynamic_threshold_single_leaf_raises():
    """_classify_single_leaf must reject non-absolute, non-percentile thresholds."""
    from refrain.fuzz.surface import (
        ConditionLeaf,
        DeriveSurface,
        ThresholdSurface,
        _classify_single_leaf,
    )
    thr = ThresholdSurface(name="t", signal="s", kind="dynamic")
    derive = DeriveSurface(
        name="s",
        band=(4.0, 8.0),
        sos=[[1, 0, 0, 1, 0, 0]],
        smooth_tau_ms=None,
        hilbert_group_delay_samples=0,
        channel="Cz",
    )
    leaf = ConditionLeaf(op="above", signal="s", threshold="t")
    with pytest.raises(UnsupportedProtocol) as e:
        _classify_single_leaf(leaf, (derive,), (thr,))
    assert e.value.reason == "single dynamic-threshold reward (unsupported)"


def test_absolute_threshold_unresolved_value_skips_cleanly():
    """An `absolute(value: <control>)` threshold leaves absolute_uv=None; skip it
    cleanly rather than crash downstream in scenario generation."""
    from refrain.fuzz.surface import (
        ConditionLeaf,
        DeriveSurface,
        ThresholdSurface,
        _classify_single_leaf,
    )
    thr = ThresholdSurface(name="t", signal="s", kind="absolute", absolute_uv=None)
    derive = DeriveSurface(
        name="s",
        band=(4.0, 8.0),
        sos=[[1, 0, 0, 1, 0, 0]],
        smooth_tau_ms=None,
        hilbert_group_delay_samples=0,
        channel="Cz",
    )
    leaf = ConditionLeaf(op="above", signal="s", threshold="t")
    with pytest.raises(UnsupportedProtocol) as e:
        _classify_single_leaf(leaf, (derive,), (thr,))
    assert e.value.reason == "absolute threshold value did not resolve to a literal"


# ---------------------------------------------------------------------------
# Multi-leaf reward conditions must validate EVERY leaf, not just single ones
# ---------------------------------------------------------------------------


def test_multi_leaf_condition_with_unresolvable_absolute_leaf_skips_cleanly():
    """Regression for the metamorphic-tier gate result, Cause A. A multi-leaf
    `all_of([above(smr, smr_t), below(hbeta, hbeta_t)])` reward whose first leaf
    uses `absolute(value: <control>)` — a control-valued threshold whose
    `absolute_uv` never resolves to a literal. `_classify_single_leaf` was never
    reached for such a leaf because it only ran on single-leaf conditions; the
    unsupported leaf shape reached the sweeps unvalidated, could hold no anchor,
    and produced false NO_CONTRAST violations on every sweep, on all 5 fuzz-gate
    seeds. It must now skip cleanly with the same reason a single-leaf absolute
    control threshold gets.

    The fixture `bench/protocols/micro_multi_leaf_control_absolute.refrain`
    reproduces the shape repo-locally (the original was
    refrain-protocols/.../smr_up_c4_baseline_brainbit.refrain, which is not
    available in this repo's CI)."""
    ir = _ir("bench/protocols/micro_multi_leaf_control_absolute.refrain")
    with pytest.raises(UnsupportedProtocol) as e:
        build_surface(ir)
    assert e.value.reason == "absolute threshold value did not resolve to a literal"


# ---------------------------------------------------------------------------
# A band edge may be a control, not only a literal
# ---------------------------------------------------------------------------


def test_band_edges_given_as_controls_resolve_to_their_defaults():
    """`_band_from_call`'s `band:` branch assumed two number literals and did
    `float(lo.value)`, so the paired `_lo_hz`/`_hi_hz` form in
    alpha_theta_autopilot raised

        AttributeError: 'IRControlRef' object has no attribute 'value'

    which the runner reported as `skipped: unclassified (...)` — printed
    alongside the declared skips and read like one. The `center:`/`bandwidth:`
    branch had resolved a control to its default all along; the `band:` branch
    now does too.
    """
    ir = _ir("examples/alpha_theta_autopilot.refrain")
    # It is still unfuzzable, but for an honest declared reason rather than a
    # crash: its reward signal is a ratio derive, not a bandpass envelope.
    with pytest.raises(UnsupportedProtocol) as exc:
        build_surface(ir)
    assert "non-bandpass" in exc.value.reason

    # The band itself now reads cleanly. Pin it directly so a regression in
    # `_band_from_call` cannot hide behind the reward-stage skip above.
    from refrain.fuzz.surface import _band_from_call

    delta = ir.derives["delta_envelope"].expression
    call = delta
    while call is not None and getattr(call, "callee", None) != "bandpass":
        args = getattr(call, "args", ())
        call = next((a.value for a in args if getattr(a.value, "callee", None)), None)
    assert call is not None, "could not find the bandpass call"
    lo, hi = _band_from_call(call, ir)
    # delta_lo_hz defaults to 2 Hz (range 1-4), delta_hi_hz to 4 Hz (range 2-8).
    assert (lo, hi) == (2.0, 4.0), f"delta band read as ({lo}, {hi})"
