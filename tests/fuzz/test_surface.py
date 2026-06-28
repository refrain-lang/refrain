"""Tests for the LogicalSurface extraction from a resolved Refrain IR."""
from __future__ import annotations

import pytest

from refrain.fuzz.surface import LogicalSurface, build_surface
from tests.fuzz._smr import resolved_smr_ir


@pytest.fixture(scope="module")
def smr_surface() -> LogicalSurface:
    return build_surface(resolved_smr_ir())


def test_surface_extracts_three_envelope_derives(smr_surface):
    names = {d.name for d in smr_surface.derives}
    assert names == {"smr_envelope", "theta_envelope", "high_beta_envelope"}


def test_surface_carries_baked_sos_per_derive(smr_surface):
    by_name = {d.name: d for d in smr_surface.derives}
    smr = by_name["smr_envelope"]
    assert smr.sos is not None
    assert len(smr.sos) >= 1
    assert all(len(s) == 6 for s in smr.sos)
    assert smr.band == pytest.approx((12.0, 15.0))


def test_surface_smoothing_tau_ms_present(smr_surface):
    by_name = {d.name: d for d in smr_surface.derives}
    assert by_name["smr_envelope"].smooth_tau_ms == pytest.approx(250.0)


def test_surface_three_thresholds_correct_kinds(smr_surface):
    by_name = {t.name: t for t in smr_surface.thresholds}
    assert set(by_name) == {"smr_t", "theta_t", "hbeta_t"}
    assert by_name["hbeta_t"].kind == "absolute"
    assert by_name["hbeta_t"].absolute_uv == pytest.approx(8.0)
    assert by_name["smr_t"].kind == "percentile"
    assert by_name["smr_t"].percentile_window_ms == pytest.approx(120_000.0)
    assert by_name["smr_t"].percentile_target == pytest.approx(70.0)  # default control


def test_surface_condition_tree_is_all_of_three_leaves(smr_surface):
    cond = smr_surface.reward_condition
    assert cond.op == "all_of"
    assert len(cond.children) == 3
    leaves = {(c.op, c.signal, c.threshold) for c in cond.children}
    assert leaves == {
        ("above", "smr_envelope",        "smr_t"),
        ("below", "theta_envelope",      "theta_t"),
        ("below", "high_beta_envelope",  "hbeta_t"),
    }


def test_surface_dwell_is_250ms(smr_surface):
    assert smr_surface.dwell_ms == pytest.approx(250.0)


def test_surface_phases_include_warmup_muted(smr_surface):
    phases = smr_surface.phases
    assert [p.name for p in phases] == ["warmup", "training", "cooldown"]
    assert phases[0].output_muted is True
    assert phases[1].output_muted is False
    assert phases[2].output_muted is True


def test_surface_sample_rate_resolved(smr_surface):
    assert smr_surface.sample_rate_hz > 0
    assert smr_surface.sample_rate_hz == 256


def test_surface_lists_relevant_channels(smr_surface):
    assert "Cz" in smr_surface.required_channels
