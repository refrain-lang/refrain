"""record_streams=True captures per-chunk stream_values for the bench harness."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from refrain.amp_profile import load_amp_profile
from refrain.eval_ import Evaluator
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_Q21 = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"


def _smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"),
                   load_amp_profile(AMP_Q21))


def test_record_streams_default_off():
    ev = Evaluator.live(_smr_ir(), sample_rate_hz=256, channel_names=("Cz",))
    ev.start(skip_warmup=True)
    ev.step_chunk(np.zeros((32, 1), dtype=np.float64))
    assert ev.last_streams() == {}, "default mode must not record"


def test_record_streams_captures_chunk():
    ev = Evaluator.live(
        _smr_ir(), sample_rate_hz=256, channel_names=("Cz",),
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    chunk = np.random.default_rng(0).standard_normal((32, 1))
    ev.step_chunk(chunk)
    streams = ev.last_streams()
    assert "raw" in streams
    assert streams["raw"].shape == (32,)
    assert "smr_envelope" in streams
    assert streams["smr_envelope"].shape == (32,)


def test_record_streams_overwrites_each_chunk():
    ev = Evaluator.live(
        _smr_ir(), sample_rate_hz=256, channel_names=("Cz",),
        record_streams=True,
    )
    ev.start(skip_warmup=True)
    rng = np.random.default_rng(1)
    ev.step_chunk(rng.standard_normal((32, 1)))
    first = ev.last_streams()["raw"].copy()
    ev.step_chunk(rng.standard_normal((32, 1)))
    second = ev.last_streams()["raw"]
    assert not np.array_equal(first, second), "stream snapshot must refresh per chunk"
