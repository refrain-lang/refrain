# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Ask 4 — first-class identity montage ``passthrough()``.

A single raw channel can be carried through a protocol unchanged without
co-opting ``referential(reference: "device")``. ``passthrough()`` takes no
args and emits the source's single channel verbatim (scalar stream).
"""

from __future__ import annotations

import numpy as np
import pytest

from refrain.eval_ import Evaluator
from refrain.parser import parse
from refrain.resolver import resolve

_PROTO = """
    protocol "passthrough_test" {
      meta {
        version = "1.0"
        evidence = "demo"
        description = "passthrough montage"
      }
      requires {
        sample_rate = ">= 4 Hz"
        channels    = ["tachogram"]
      }
      input "tach" {
        montage = passthrough()
      }
      derive "env" {
        from = "tach"
        pipeline = [ rectify() ]
      }
      reward {
        continuous = "env"
      }
      output {
        audio_gain = reward.continuous
      }
    }
"""


def test_passthrough_resolves_to_single_scalar_channel():
    ir = resolve(parse(_PROTO))
    inp = ir.inputs["tach"]
    assert inp.montage.callee == "passthrough"
    # identity montage carries the single source channel through as a scalar
    assert inp.stream_type.value_kind == "scalar"
