# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
# tests/_inhibit_fixtures.py — the one-inhibit protocol the `kind` resolve and
# emit tests share. Compile-verified against the real surface syntax; do not
# hand-edit the syntax, re-compile and confirm no errors instead.
"""A single `inhibit "emg"` whose action and `kind` label the caller chooses.

Shared by `tests/test_resolver.py` and `tests/test_ir_json.py` so the two
agree on one document: the resolve tests say what the label means and the emit
tests say what reaches the wire, and a divergence between the two documents
would hide exactly the drift those tests exist to catch.

The protocol deliberately uses `bandpass`/`hilbert`/`magnitude` rather than
`band_envelope`: an unlabelled copy must emit IR-JSON 0.1, which it cannot do
if the derive pipeline pushes it to 0.5 on complex FIR taps.
"""

from __future__ import annotations

_BASE = '''protocol "inhibit_kind_demo" {
  meta { version = "1.0.0"; evidence = "demo"; description = "inhibit kind" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz", "A1"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "thr" { signal = "env"; type = absolute(2 uV) }
  inhibit "emg" {
    metric    = bandpower(input: "raw", band: (20 Hz, 50 Hz), window: 100 ms)
    threshold = absolute(55 uV2)
    action    = __ACTION____KIND__
  }
  reward { continuous = sigmoid("env" / "thr", midpoint: 1.0, steepness: 3) }
  output { fb = reward.continuous }
}'''


def inhibit_protocol(
    *, action: str = "mute(release: 200 ms)", kind: str | None = None
) -> str:
    """Protocol source with one inhibit. `kind=None` writes no `kind` field."""
    label = "" if kind is None else f'\n    kind      = "{kind}"'
    return _BASE.replace("__ACTION__", action).replace("__KIND__", label)
