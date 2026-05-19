"""Every bench protocol must parse and resolve cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest

from refrain.amp_profile import load_amp_profile
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent.parent
PROTOCOLS = REPO / "bench" / "protocols"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"

MICRO_PROTOCOLS = [
    "micro_01_passthrough.refrain",
    "micro_02_bandpass.refrain",
    "micro_03_envelope.refrain",
    "micro_04_threshold.refrain",
    "micro_05_reward.refrain",
]


@pytest.mark.parametrize("filename", MICRO_PROTOCOLS)
def test_microbench_protocol_parses_and_resolves(filename: str):
    path = PROTOCOLS / filename
    assert path.exists(), f"missing protocol file: {path}"
    ir = resolve(parse_file(path), load_amp_profile(AMP_Q21))
    assert ir is not None
    assert ir.name is not None
