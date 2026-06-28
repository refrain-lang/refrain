# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
from __future__ import annotations

from pathlib import Path

from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.synthetic import channels_for_synthetic

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_channels_include_requires_and_ears():
    ir = resolve(parse_file(REPO_ROOT / "bench/protocols/realistic_smr.refrain"), None)
    chans = channels_for_synthetic(ir)
    assert "A1" in chans and "A2" in chans
    for c in ir.requires.channels:
        assert c in chans
