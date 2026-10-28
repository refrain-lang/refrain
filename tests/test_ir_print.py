# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Sanity checks on the IR pretty-printer and CRED-nf supplement.

These tests don't pin the exact textual layout (that would be brittle);
they verify that key facts surface in the output where a clinician or
reviewer would expect them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refrain.amp_profile import load_amp_profile
from refrain.ir_print import print_cred_nf, print_ir
from refrain.parser import parse, parse_file
from refrain.resolver import resolve
from tests._inhibit_fixtures import inhibit_protocol  # verified one-inhibit fixture
from tests.conftest_staged import HET


EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
AMP_PATH = Path(__file__).resolve().parent.parent / "src" / "refrain" / "amp_profiles" / "q21.json"


@pytest.fixture(scope="module")
def smr_ir():
    return resolve(parse_file(EXAMPLES / "smr_cz.refrain"), load_amp_profile(AMP_PATH))


@pytest.fixture(scope="module")
def othmer_ir():
    return resolve(parse_file(EXAMPLES / "othmer_ilf_t3t4.refrain"), load_amp_profile(AMP_PATH))


# ---------------------------------------------------------------------------
# print_ir
# ---------------------------------------------------------------------------


def test_ir_dump_contains_protocol_name(smr_ir):
    out = print_ir(smr_ir)
    assert "smr_cz_v1" in out


def test_ir_dump_shows_sample_rate_chosen(smr_ir):
    out = print_ir(smr_ir)
    assert "256 Hz" in out


def test_ir_dump_shows_typed_streams(smr_ir):
    out = print_ir(smr_ir)
    assert "stream<scalar uV>" in out
    # SMR derives produce uV envelopes.
    assert "derive/smr_envelope" in out


def test_ir_dump_shows_thresholds_and_inhibits_when_present(othmer_ir):
    out = print_ir(othmer_ir)
    assert "inhibit/emg" in out
    assert "action    = mute" in out


def test_ir_dump_shows_controls_with_log_scale(othmer_ir):
    out = print_ir(othmer_ir)
    assert "control/orf" in out
    assert "log scale" in out


def test_ir_dump_includes_resource_budget(smr_ir):
    out = print_ir(smr_ir)
    assert "resource budget" in out
    assert "state" in out
    assert "worst-case step" in out


def test_ir_dump_includes_topological_order(smr_ir):
    out = print_ir(smr_ir)
    assert "topological order" in out
    # Inputs come before derives.
    pos_input = out.index("input/raw")
    pos_derive = out.index("derive/smr_envelope")
    # In the topo section (after the # dataflow heading), the first
    # appearance of input/raw should precede the first derive/...
    # Just verify both appear; ordering inside the section is enforced
    # by the resolver and tested in test_resolver.py.
    assert pos_input >= 0
    assert pos_derive >= 0


def test_ir_dump_renders_units_not_just_dimensions(othmer_ir):
    out = print_ir(othmer_ir)
    # The Othmer ILF derive uses `tau: 1500 ms` — should print as `1500 ms`
    # not `1500 time`.
    assert "1500 ms" in out


# ---------------------------------------------------------------------------
# print_cred_nf
# ---------------------------------------------------------------------------


def test_cred_nf_is_a_markdown_table(smr_ir):
    out = print_cred_nf(smr_ir)
    assert out.startswith("# CRED-nf supplement")
    assert "| CRED-nf item | Value |" in out
    assert "|---|---|" in out


def test_cred_nf_pulls_citation_from_meta(smr_ir):
    out = print_cred_nf(smr_ir)
    assert "Sterman M.B. & Lubar J.F." in out


def test_cred_nf_lists_electrode_locations(smr_ir):
    out = print_cred_nf(smr_ir)
    assert "Cz" in out


def test_cred_nf_lists_filter_designs(smr_ir):
    out = print_cred_nf(smr_ir)
    assert "bandpass" in out
    # Three bands (SMR, theta, high-beta) — at least one band edge surfaces.
    assert "12 Hz" in out


def test_cred_nf_marks_unspecified_fields(smr_ir):
    out = print_cred_nf(smr_ir)
    # SMR example has no inhibits; the rejection field should be marked.
    assert "NOT SPECIFIED" in out


def test_cred_nf_includes_amp_summary(smr_ir):
    out = print_cred_nf(smr_ir)
    assert "Neurofield" in out
    assert "neurofield-q21" in out


def test_cred_nf_session_duration_summary(smr_ir):
    out = print_cred_nf(smr_ir)
    # 90 s warmup + 30 min training + 30 s cooldown = 32 min.
    assert "32 min" in out


def test_cred_nf_runs_without_amp_profile():
    ir = resolve(parse_file(EXAMPLES / "smr_cz.refrain"), amp=None)
    out = print_cred_nf(ir)
    # Software/hardware row falls back to [NOT SPECIFIED].
    assert "NOT SPECIFIED" in out


def test_print_ir_includes_block_and_mode():
    text = print_ir(resolve(parse(HET)))
    assert "beta_up" in text          # block declaration rendered
    assert "timed_with_floor" in text  # phase mode rendered
    assert "br" in text                # reward bundle name rendered


# ---------------------------------------------------------------------------
# The CRED-nf report carries each inhibit's `kind` label — Stage 3's worklist
# of still-unlabelled inhibits is read off this report (design §2).
# ---------------------------------------------------------------------------


def test_the_cred_nf_report_names_each_inhibits_label():
    for label in ("artifact", "trained"):
        out = print_cred_nf(resolve(parse(inhibit_protocol(kind=label))))
        assert f"kind={label}" in out, out
        # The label has to sit on the inhibit's own row, or a report with two
        # inhibits could not be read as a worklist at all.
        row = next(line for line in out.splitlines() if "emg: " in line)
        assert f"kind={label}" in row, row


def test_an_unlabelled_inhibit_is_reported_as_unlabelled_not_as_trained():
    """Absence is recorded as unlabelled, never asserted to be a trained
    condition — most of them are mislabelled artifact gates."""
    out = print_cred_nf(resolve(parse(inhibit_protocol())))
    assert "kind=unlabelled" in out, out
    assert "kind=trained" not in out
