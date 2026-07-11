# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
from __future__ import annotations

from pathlib import Path

from refrain.parser import parse, parse_file
from refrain.resolver import resolve
from refrain.synthetic import channels_for_synthetic

REPO_ROOT = Path(__file__).resolve().parents[2]

# A `placement` control substitutes its bound channels into the montage at
# resolve time but does NOT have to appear in `requires.channels` — these
# protocols declare no channels at all. The synthetic source must still
# generate what the montage reads, or the evaluator dies with
# "bipolar: plus channel 'C3' not in source".
_PLACEMENT_PREAMBLE = '''protocol "placement_fixture" {
  meta {
    version = "1.0.0"
    evidence = "demo"
    description = "montage channels absent from requires.channels"
  }
  requires {
    sample_rate = ">= 250 Hz"
  }
'''

_PLACEMENT_BODY = '''
  derive "smr_envelope" {
    from = "raw"
    pipeline = [
      bandpass(band: (12 Hz, 15 Hz), order: 4),
      hilbert(),
      magnitude(),
      smooth(tau: 250 ms),
    ]
  }
  threshold "smr_t" {
    signal = "smr_envelope"
    type   = absolute(value: 5.0 uV)
  }
  reward {
    event = dwell(
      condition: all_of([above("smr_envelope", "smr_t")]),
      duration: 250 ms
    )
  }
  output {
    audio_chime = reward.event
  }
}
'''


def _placement_ir(controls: str, montage: str):
    src = _PLACEMENT_PREAMBLE + controls + montage + _PLACEMENT_BODY
    return resolve(parse(src), None)


def test_channels_include_requires_and_ears():
    ir = resolve(parse_file(REPO_ROOT / "bench/protocols/realistic_smr.refrain"), None)
    chans = channels_for_synthetic(ir)
    assert "A1" in chans and "A2" in chans
    for c in ir.requires.channels:
        assert c in chans


def test_channels_include_bipolar_montage_legs_absent_from_requires():
    ir = _placement_ir(
        controls='''
  controls {
    motor = placement {
      kind    = "bipolar"
      default = ("C3", "C4")
      allowed = [("C3", "C4")]
      label   = "Bipolar motor pair"
    }
  }
''',
        montage='''
  input "raw" {
    montage = bipolar(pair: motor)
  }
''',
    )
    assert not ir.requires.channels  # the gap: the montage is the only source
    chans = channels_for_synthetic(ir)
    assert "C3" in chans and "C4" in chans


def test_channels_include_referential_active_absent_from_requires():
    ir = _placement_ir(
        controls='''
  controls {
    site = placement {
      kind    = "active"
      default = "C3"
      allowed = ["C3", "C4"]
      label   = "Active site"
    }
  }
''',
        montage='''
  input "raw" {
    montage = referential(active: site, reference: "device")
  }
''',
    )
    assert not ir.requires.channels
    chans = channels_for_synthetic(ir)
    assert "C3" in chans


def test_channels_include_vector_referential_channel_list():
    """`referential` has a vector form — an array of channels rather than a
    single `active`. Those electrodes are named nowhere else either.

    This pins the channel set only. The evaluator does NOT yet implement the
    vector montage (`ReferentialImpl` takes a single `active`), so such a
    protocol still fails at evaluator construction; no corpus protocol uses
    the form. Getting the channels right is what this function owes it."""
    ir = _placement_ir(
        controls="",
        montage='''
  input "raw" {
    montage = referential(channels: ["C3", "C4"], reference: "linked_ears")
  }
''',
    )
    assert not ir.requires.channels
    chans = channels_for_synthetic(ir)
    assert "C3" in chans and "C4" in chans


def test_montage_channels_do_not_renumber_existing_channels():
    """A montage naming a channel the protocol already carries must not move
    it. `SignalGenerator` draws noise as one (n_samples, n_channels) block, so
    a channel's samples depend on its index: reordering silently changes the
    synthetic signal — and every result derived from it — for a protocol that
    was passing before."""
    src = _PLACEMENT_PREAMBLE.replace(
        'sample_rate = ">= 250 Hz"',
        'sample_rate = ">= 250 Hz"\n    channels = ["Cz"]',
    ) + '''
  input "raw" {
    montage = referential(active: "Cz", reference: "A2")
  }
''' + _PLACEMENT_BODY
    ir = resolve(parse(src), None)
    # A2 is named by the montage AND is an ear channel. It must keep the index
    # it had when the ears were the only thing appended.
    assert channels_for_synthetic(ir) == ("Cz", "A1", "A2")


def test_referential_reference_channel_is_a_channel_not_a_keyword():
    """`reference` may be a physical channel — include it — but the keyword
    references (`linked_ears`, `common_average`, `device`) are not channels
    and must never be synthesized as one."""
    ir = _placement_ir(
        controls="",
        montage='''
  input "raw" {
    montage = referential(active: "Cz", reference: "device")
  }
''',
    )
    assert "device" not in channels_for_synthetic(ir)

    ir_named_ref = _placement_ir(
        controls="",
        montage='''
  input "raw" {
    montage = referential(active: "Cz", reference: "Fpz")
  }
''',
    )
    assert "Fpz" in channels_for_synthetic(ir_named_ref)
