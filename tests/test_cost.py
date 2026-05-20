# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the static cost-estimate / complexity-hint model (refrain.cost)."""

from __future__ import annotations

from pathlib import Path

from refrain.amp_profile import load_amp_profile
from refrain.cli import main
from refrain.cost import TIER_SLOWDOWN, estimate_cost
from refrain.parser import parse, parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"


def _resolve(src: str):
    return resolve(parse(src), load_amp_profile(AMP_Q21))


def _resolve_file(name: str):
    return resolve(parse_file(EXAMPLES / name), load_amp_profile(AMP_Q21))


_HEADER = '''protocol "{name}" {{
  meta {{ version = "0.1.0" evidence = "demo" description = "x" }}
  requires {{ sample_rate = ">= 256 Hz" channels = ["Cz"] }}
  input "raw" {{ montage = referential(active: "Cz", reference: "linked_ears") }}
  derive "e" {{
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ]
  }}
'''

ENVELOPE_ONLY = _HEADER.format(name="env_only") + '  output { audio_gain = 0 }\n}\n'

PERCENTILE_ONE = _HEADER.format(name="pctl_one") + '''  threshold "t" {
    signal = "e"
    type   = percentile(target_pct: 70, window: 2 min)
  }
  output { audio_gain = 0 }
}
'''


def test_envelope_only_is_cheap_and_unwarned():
    rep = estimate_cost(_resolve(ENVELOPE_ONLY), sample_rate_hz=256.0)
    # envelope (1 band) + dispatch only; comfortably real-time on every tier.
    assert rep.rtf_by_tier["embedded(Pi4)"] < 0.5
    assert not any("NOT keep up" in w for w in rep.warnings)
    assert rep.dominant.name.startswith("envelope")
    assert rep.any_uncalibrated is False


def test_percentile_dominates():
    rep = estimate_cost(_resolve(PERCENTILE_ONE), sample_rate_hz=256.0)
    pctl = next(d for d in rep.drivers if d.name.startswith("percentile"))
    env = next(d for d in rep.drivers if d.name.startswith("envelope"))
    assert pctl.calibrated is True
    assert pctl.us_per_sample > 50 * env.us_per_sample  # dominates by orders of magnitude
    assert rep.dominant is pctl


def test_rtf_scales_superlinearly_with_sample_rate():
    ir = _resolve(PERCENTILE_ONE)
    rtf_256 = estimate_cost(ir, sample_rate_hz=256.0).rtf_by_tier["workstation"]
    rtf_512 = estimate_cost(ir, sample_rate_hz=512.0).rtf_by_tier["workstation"]
    # percentile cost ~ window_samples (∝ sr) and RTF ~ cost*sr → ∝ sr^2.
    assert rtf_512 / rtf_256 > 3.0


def test_report_structure_is_consistent():
    rep = estimate_cost(_resolve(PERCENTILE_ONE), sample_rate_hz=256.0)
    assert abs(rep.total_us_per_sample - sum(d.us_per_sample for d in rep.drivers)) < 1e-9
    assert set(rep.rtf_by_tier) == set(TIER_SLOWDOWN)
    assert rep.n_channels == 1
    assert rep.sample_rate_hz == 256.0


def test_default_sample_rate_from_protocol():
    ir = _resolve(PERCENTILE_ONE)
    rep = estimate_cost(ir)  # no explicit rate
    assert rep.sample_rate_hz == float(ir.requires.sample_rate_chosen_hz)


def test_realistic_smr_warns_on_embedded():
    rep = estimate_cost(_resolve_file("smr_cz.refrain"), sample_rate_hz=256.0)
    # Two 2-min percentile windows: fine on a workstation, over budget on Pi-class.
    assert rep.rtf_by_tier["workstation"] < 1.0
    assert rep.rtf_by_tier["embedded(Pi4)"] >= 1.0
    assert any("embedded" in w and "NOT keep up" in w for w in rep.warnings)


def test_bandpower_marked_uncalibrated():
    # alpha_theta has an EMG inhibit whose metric is bandpower(...).
    rep = estimate_cost(_resolve_file("alpha_theta.refrain"), sample_rate_hz=256.0)
    bp = next((d for d in rep.drivers if d.name.startswith("bandpower")), None)
    assert bp is not None
    assert bp.calibrated is False
    assert rep.any_uncalibrated is True


def test_cli_cost_smoke(capsys):
    rc = main(["cost", str(EXAMPLES / "smr_cz.refrain"), "--sample-rate", "256"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "percentile windows" in out
    assert "EXCEEDS" in out
    assert "PROVISIONAL" in out


def test_cli_cost_missing_file():
    assert main(["cost", "/no/such/file.refrain"]) == 2
