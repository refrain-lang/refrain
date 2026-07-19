# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Amp profile loading + schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from refrain.amp_profile import (
    AMP_PROFILE_SCHEMA,
    AmpProfile,
    AmpProfileError,
    load_amp_profile,
)


PROFILES_DIR = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles"


def test_shipped_q21_loads():
    p = load_amp_profile(PROFILES_DIR / "q21.json")
    assert isinstance(p, AmpProfile)
    assert p.model == "neurofield-q21"
    assert p.vendor == "Neurofield"
    assert "dc" in p.coupling and "ac" in p.coupling
    # The Q21 streams at a single rate: CANbus bandwidth caps it at 256 Hz
    # (21 ch x 24 bit at higher rates would exceed the bus), so the profile
    # declares only 256 -- it must not advertise rates the hardware can't reach.
    assert p.sample_rates_hz == (256,)
    assert p.has_channel("T3") and p.has_channel("Cz")
    assert p.supports_impedance_check is True


def test_shipped_openbci_cyton_loads():
    p = load_amp_profile(PROFILES_DIR / "openbci_cyton.json")
    assert p.model == "openbci-cyton"
    assert p.coupling == ("ac",)
    assert p.supports_coupling("ac")
    assert not p.supports_coupling("dc")


def test_best_sample_rate_at_least(tmp_path):
    # Rate-selection logic (pick the highest supported rate >= the minimum) is
    # exercised against a synthetic multi-rate profile: no *shipped* amp offers
    # several rates -- the Q21 is CANbus-capped at 256 Hz -- but the selection
    # code stays live for any future multi-rate hardware.
    prof = tmp_path / "multi.json"
    prof.write_text(json.dumps({
        "schema": AMP_PROFILE_SCHEMA, "model": "m", "vendor": "v",
        "coupling": ["dc"], "sample_rates_hz": [256, 512, 1024, 2048],
        "channels": [],
    }))
    p = load_amp_profile(prof)
    assert p.best_sample_rate_at_least(256) == 2048
    assert p.best_sample_rate_at_least(513) == 2048
    assert p.best_sample_rate_at_least(2049) is None


def test_missing_schema_field(tmp_path):
    bad = tmp_path / "no_schema.json"
    bad.write_text(json.dumps({"model": "x", "vendor": "y", "coupling": ["dc"],
                                "sample_rates_hz": [256], "channels": []}))
    with pytest.raises(AmpProfileError, match="unknown profile schema"):
        load_amp_profile(bad)


def test_wrong_schema_version(tmp_path):
    bad = tmp_path / "wrong_schema.json"
    bad.write_text(json.dumps({"schema": "refrain-amp-profile/v99",
                                "model": "x", "vendor": "y",
                                "coupling": ["dc"], "sample_rates_hz": [256],
                                "channels": []}))
    with pytest.raises(AmpProfileError, match="unknown profile schema"):
        load_amp_profile(bad)


def test_missing_required_field(tmp_path):
    bad = tmp_path / "missing.json"
    bad.write_text(json.dumps({"schema": AMP_PROFILE_SCHEMA, "model": "x"}))
    with pytest.raises(AmpProfileError, match="missing required field"):
        load_amp_profile(bad)


def test_invalid_coupling(tmp_path):
    bad = tmp_path / "bad_coupling.json"
    bad.write_text(json.dumps({
        "schema": AMP_PROFILE_SCHEMA, "model": "x", "vendor": "y",
        "coupling": ["dc", "weird"], "sample_rates_hz": [256], "channels": [],
    }))
    with pytest.raises(AmpProfileError, match="coupling must be subset"):
        load_amp_profile(bad)


def test_empty_sample_rates(tmp_path):
    bad = tmp_path / "no_rates.json"
    bad.write_text(json.dumps({
        "schema": AMP_PROFILE_SCHEMA, "model": "x", "vendor": "y",
        "coupling": ["dc"], "sample_rates_hz": [], "channels": [],
    }))
    with pytest.raises(AmpProfileError, match="sample_rates_hz must not be empty"):
        load_amp_profile(bad)


def test_channel_string_form(tmp_path):
    # Channels may be plain strings instead of {name, kind} objects.
    good = tmp_path / "string_chans.json"
    good.write_text(json.dumps({
        "schema": AMP_PROFILE_SCHEMA, "model": "x", "vendor": "y",
        "coupling": ["dc"], "sample_rates_hz": [256],
        "channels": ["Cz", "Pz", "Fz"],
    }))
    p = load_amp_profile(good)
    assert p.has_channel("Cz")
    assert all(c.kind == "eeg" for c in p.channels)


def test_reference_attribute_exists():
    p = load_amp_profile(PROFILES_DIR / "openbci_cyton.json")
    assert hasattr(p, "reference")


def test_reference_keyword_accepted(tmp_path):
    data = {
        "schema": AMP_PROFILE_SCHEMA, "model": "m", "vendor": "v",
        "coupling": ["ac"], "sample_rates_hz": [250],
        "channels": ["Cz"], "supports_impedance_check": False,
        "supports_markers": False, "max_simultaneous_channels": 4,
        "adc_bits": 24, "input_range_uv": 300000.0,
        "reference": "device",
    }
    f = tmp_path / "amp.json"; f.write_text(json.dumps(data))
    assert load_amp_profile(f).reference == "device"


def test_reference_channel_name_accepted(tmp_path):
    data = {
        "schema": AMP_PROFILE_SCHEMA, "model": "m", "vendor": "v",
        "coupling": ["ac"], "sample_rates_hz": [250],
        "channels": ["Cz", "A1"], "supports_impedance_check": False,
        "supports_markers": False, "max_simultaneous_channels": 4,
        "adc_bits": 24, "input_range_uv": 300000.0,
        "reference": "A1",
    }
    f = tmp_path / "amp.json"; f.write_text(json.dumps(data))
    assert load_amp_profile(f).reference == "A1"


def test_reference_invalid_raises(tmp_path):
    data = {
        "schema": AMP_PROFILE_SCHEMA, "model": "m", "vendor": "v",
        "coupling": ["ac"], "sample_rates_hz": [250],
        "channels": ["Cz"], "supports_impedance_check": False,
        "supports_markers": False, "max_simultaneous_channels": 4,
        "adc_bits": 24, "input_range_uv": 300000.0,
        "reference": "bogus",
    }
    f = tmp_path / "amp.json"; f.write_text(json.dumps(data))
    with pytest.raises(AmpProfileError, match="reference"):
        load_amp_profile(f)


def test_shipped_profiles_declare_reference():
    assert load_amp_profile(PROFILES_DIR / "brainbit_flex.json").reference == "device"
    assert load_amp_profile(PROFILES_DIR / "q21.json").reference == "linked_ears"
    assert load_amp_profile(PROFILES_DIR / "openbci_cyton.json").reference == "linked_ears"
