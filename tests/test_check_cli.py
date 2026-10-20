# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""`refrain check` must not say more than it verified (refrain#97).

The reported failure, in three commands:

    $ refrain check  thermal.refrain      -> OK
    $ refrain fuzz   thermal.refrain      -> SKIPPED (unsupported: ...)
      production                          -> crash on the first chunk

`OK` read as a verdict on the protocol, and a protocol that had never run
reached a downstream repository on the strength of it. The upstream issue says
`OK` was "true of what `check` does — parse and resolve"; it was not even that.
`check` parsed and stopped, so it printed `OK` for protocols the resolver
rejects outright, which is the case `test_a_protocol_the_resolver_rejects_is
_not_ok` pins.

`check` now parses, resolves, and steps one chunk of synthetic data — the
"resolving implies running" property that `test_resolves_implies_runs.py`
asserts over the corpus, applied to one file per invocation — and its verdict
line names all three.
"""

from __future__ import annotations

from pathlib import Path

from refrain.cli import main

REPO = Path(__file__).resolve().parents[1]

OK_SRC = '''protocol "check_ok" {
  meta {
    version  = "0.1.0"
    evidence = "demo"
    description = "parses, resolves and steps"
  }
  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  derive "smr_envelope" {
    from = "raw"
    pipeline = [bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(),
                smooth(tau: 250 ms)]
  }
  output {
    audio_gain = 0
  }
}'''

# `nonexistent` is not a declared control, so this fails in the resolver. Before
# the fix `check` printed OK for it.
UNRESOLVABLE_SRC = '''protocol "check_unresolvable" {
  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  derive "filtered" {
    from = "raw"
    pipeline = [bandpass(center: nonexistent, bandwidth: ratio(0.5))]
  }
  output {
    audio_gain = 0
  }
}'''

# The fourth crashing shape from refrain#94: an inhibit metric reading a derive
# through `passthrough()`. It resolves cleanly — the IR holds
# `arg input -> IRStreamRef derive/skin_rate` — and then dies at evaluator
# build, because `_instantiate_call` instantiates the primitive against the raw
# acquired channel list rather than the resolved single-channel reference:
#
#   ValueError: passthrough() requires a single-channel source (got 3 channels)
#
# This is the class `check` could not see: resolve says yes, the session dies.
RESOLVES_BUT_CANNOT_RUN_SRC = '''protocol "check_resolves_but_cannot_run" {
  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["Cz"]
  }
  input "raw" {
    montage = referential(active: "Cz", reference: "linked_ears")
  }
  derive "skin_rate" {
    from = "raw"
    pipeline = [smooth(tau: 250 ms)]
  }
  inhibit "emg" {
    metric    = passthrough(input: "skin_rate")
    threshold = absolute(100 uV)
    action    = mute(release: 200 ms)
  }
  output {
    audio_gain = 0
  }
}'''

UNPARSEABLE_SRC = 'protocol "check_unparseable" { requires { sample_rate = '


def _write(tmp_path: Path, src: str) -> str:
    p = tmp_path / "p.refrain"
    p.write_text(src)
    return str(p)


# ---------------------------------------------------------------------------
# The verdict line
# ---------------------------------------------------------------------------


def test_the_ok_line_names_every_stage_it_verified(tmp_path, capsys):
    """A one-word `OK` is what let a never-run protocol ship. The verdict now
    says which stages it got through, so the obvious next question ("did it
    run?") is already answered on the line."""
    code = main(["check", _write(tmp_path, OK_SRC)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.startswith("OK:"), out
    for stage in ("parses", "resolves", "steps one chunk"):
        assert stage in out, f"verdict line does not mention {stage!r}: {out!r}"
    # The detail the old line carried is still there.
    assert "'check_ok'" in out
    assert "top-level statements" in out


def test_the_ok_line_reports_the_rate_and_channels_it_stepped(tmp_path, capsys):
    """"Steps one chunk" is only checkable if the line says at what rate and on
    how many channels — the two things that decide whether the step was the one
    a host would make."""
    code = main(["check", _write(tmp_path, OK_SRC)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "256 Hz" in out, out
    assert "3 channels" in out, out  # Cz + the A1/A2 linked_ears pair


# ---------------------------------------------------------------------------
# The three ways it can fail
# ---------------------------------------------------------------------------


def test_a_parse_error_still_reports_parse_failed(tmp_path, capsys):
    code = main(["check", _write(tmp_path, UNPARSEABLE_SRC)])
    cap = capsys.readouterr()
    assert code == 1
    assert "parse failed" in cap.err
    assert "OK" not in cap.out


def test_a_protocol_the_resolver_rejects_is_not_ok(tmp_path, capsys):
    """This is the sharpest form of the defect: `check` parsed and stopped, so
    it printed OK for a protocol `refrain resolve` refuses on the next line."""
    code = main(["check", _write(tmp_path, UNRESOLVABLE_SRC)])
    cap = capsys.readouterr()
    assert code == 1, cap.out
    assert "resolve failed" in cap.err
    assert "OK" not in cap.out


def test_a_protocol_that_resolves_but_cannot_be_stepped_is_not_ok(tmp_path, capsys):
    """The resolve-then-crash class. `check` must report the evaluator's own
    message, because that message is the only thing that says which primitive
    refused."""
    code = main(["check", _write(tmp_path, RESOLVES_BUT_CANNOT_RUN_SRC)])
    cap = capsys.readouterr()
    assert code == 1, cap.out
    assert "resolves but does not run" in cap.err, cap.err
    assert "passthrough() requires a single-channel source" in cap.err, cap.err
    assert "OK" not in cap.out


def test_a_missing_file_is_still_exit_2(tmp_path, capsys):
    code = main(["check", str(tmp_path / "nope.refrain")])
    assert code == 2
    assert "no such file" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Stepping must not fail protocols that are fine
# ---------------------------------------------------------------------------


def test_a_passthrough_montage_protocol_is_not_padded_into_a_crash(capsys):
    """`channels_for_synthetic` appends A1/A2 so `linked_ears` resolves, which
    a `passthrough()` montage cannot take — it requires a single-channel
    source. If `check` padded unconditionally it would fail five shipped bench
    protocols and report its own rig as the protocol's fault."""
    path = REPO / "bench" / "protocols" / "micro_passthrough_identity.refrain"
    assert path.exists(), path
    code = main(["check", str(path)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "1 channel" in out, out


def test_a_protocol_with_extends_passes_once_the_library_is_given(capsys):
    """Resolving is now part of `check`, so `check` needs the same `--library`
    search path `resolve` has — otherwise every protocol with an `extends`
    becomes a false failure."""
    path = REPO / "examples" / "othmer_ilf_cz_pz.refrain"
    assert path.exists(), path

    code = main(["check", str(path)])
    cap = capsys.readouterr()
    assert code == 1, cap.out
    assert "resolve failed" in cap.err

    code = main(["check", str(path), "--library", str(REPO / "examples")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.startswith("OK:"), out
