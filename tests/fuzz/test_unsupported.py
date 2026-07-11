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


def test_center_bandwidth_bandpass_no_longer_raises_typed_skip():
    # Since center/bandwidth support was added, othmer_ilf_t3t4 now gets past
    # bandpass parsing. It then fails at the reward stage (no dwell event) with
    # a plain ValueError backstop — NOT an UnsupportedProtocol typed skip.
    ir = _ir("examples/othmer_ilf_t3t4.refrain")
    with pytest.raises(ValueError) as exc:
        build_surface(ir)
    assert not isinstance(exc.value, UnsupportedProtocol)


def test_supported_protocol_still_builds():
    ir = _ir("bench/protocols/realistic_smr.refrain")
    surface = build_surface(ir)  # no raise
    assert surface.protocol_name


def test_unrecognized_condition_stays_valueerror_not_typed():
    # micro_08_bandpower hits `_condition_from_ir` with a non-leaf IRCall;
    # Increment 0 leaves this as a plain ValueError (-> backstop "unclassified"),
    # NOT one of our typed skips.
    ir = _ir("bench/protocols/micro_08_bandpower.refrain")
    with pytest.raises(ValueError) as exc:
        build_surface(ir)
    assert not isinstance(exc.value, UnsupportedProtocol)


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
