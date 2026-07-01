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
    ir = _ir("bench/protocols/composite_smr_theta.refrain")
    with pytest.raises(UnsupportedProtocol) as exc:
        build_surface(ir)
    assert exc.value.reason == "single-condition reward"


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
