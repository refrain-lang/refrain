# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Composition tests: extends / amend / remove / final.

Two layers:

  1. Direct `compose()` tests with an in-memory loader — verify the
     merge logic in isolation.
  2. End-to-end `resolve()` tests on real composed protocols —
     verify that composed ASTs feed the existing resolver cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refrain import ast as A
from refrain.amp_profile import load_amp_profile
from refrain.compose import (
    ComposeError,
    compose,
    default_library_dirs,
    filesystem_loader,
    parse_ref,
)
from refrain.parser import parse
from refrain.resolver import resolve


REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"
AMP_PATH = REPO / "src" / "refrain" / "amp_profiles" / "q21.json"


def _dict_loader(refs: dict[str, str]):
    """In-memory loader: maps refs to parsed Refrain source strings."""
    parsed = {k: parse(v) for k, v in refs.items()}

    def loader(ref: str) -> A.File:
        if ref not in parsed:
            raise ComposeError(f"cannot resolve parent {ref!r}")
        return parsed[ref]

    return loader


# ---------------------------------------------------------------------------
# parse_ref
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("library/foo/bar@1.2", ("library/foo/bar", "1.2")),
        ("library/foo", ("library/foo", None)),
        ("plain", ("plain", None)),
        ("a@1", ("a", "1")),
        ("a@b@c", ("a@b", "c")),  # last @ wins; rare but defined
    ],
)
def test_parse_ref(ref, expected):
    assert parse_ref(ref) == expected


# ---------------------------------------------------------------------------
# No-extends short circuit
# ---------------------------------------------------------------------------


def test_no_extends_returns_input_unchanged():
    src = 'protocol "P" { meta { version = "1.0" } }'
    f = parse(src)
    assert compose(f, loader=None) is f


def test_extends_without_loader_raises():
    src = 'protocol "C" extends "library/p@1" { meta { version = "1.0" } }'
    with pytest.raises(ComposeError, match="no parent loader is configured"):
        compose(parse(src), loader=None)


def test_missing_parent_raises_useful_message():
    src = 'protocol "C" extends "library/missing@1" { meta { version = "1.0" } }'
    loader = _dict_loader({})  # empty registry
    with pytest.raises(ComposeError, match="cannot resolve parent"):
        compose(parse(src), loader=loader)


# ---------------------------------------------------------------------------
# Basic full inheritance (child declares only meta override)
# ---------------------------------------------------------------------------


def test_child_inherits_everything_except_overridden_meta():
    parent = '''
        protocol "parent" {
          meta {
            version     = "1.0.0"
            evidence    = "clinical"
            description = "parent desc"
          }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" {
            from = "raw"
            pipeline = [smooth(tau: 100 ms)]
          }
          reward {
            continuous = sigmoid("env", midpoint: 5 uV, steepness: 1)
          }
        }
    '''
    child = '''
        protocol "child" extends "library/parent@1" {
          amend meta {
            description = "child desc"
          }
        }
    '''
    merged = compose(parse(child), _dict_loader({"library/parent@1": parent}))
    # Composition preserves child's protocol name; extends is cleared.
    assert merged.protocol.name == "child"
    assert merged.protocol.extends is None
    # The merged body has parent's named decls + meta with the amend applied.
    meta = next(s for s in merged.protocol.body if isinstance(s, A.SectionBlock) and s.keyword == "meta")
    fields = {a.target: a.value for a in meta.body if isinstance(a, A.Assignment)}
    assert fields["version"].value == "1.0.0"   # inherited
    assert fields["evidence"].value == "clinical"  # inherited
    assert fields["description"].value == "child desc"  # overridden


# ---------------------------------------------------------------------------
# amend named decl
# ---------------------------------------------------------------------------


def test_amend_inhibit_overrides_one_field():
    parent = '''
        protocol "parent" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          inhibit "emg" {
            metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = percentile(target_pct: 95, window: 2 min)
            action    = mute(release: 200 ms)
          }
        }
    '''
    child = '''
        protocol "child" extends "library/parent@1" {
          amend inhibit "emg" {
            threshold = percentile(target_pct: 90, window: 2 min)
          }
        }
    '''
    merged = compose(parse(child), _dict_loader({"library/parent@1": parent}))
    inhibit = next(
        s for s in merged.protocol.body
        if isinstance(s, A.NamedDecl) and s.keyword == "inhibit" and s.name == "emg"
    )
    fields = {a.target: a.value for a in inhibit.body if isinstance(a, A.Assignment)}
    # threshold's target_pct should now be 90.
    threshold_call = fields["threshold"]
    target_arg = next(a for a in threshold_call.args if a.name == "target_pct")
    assert target_arg.value.value == 90
    # metric and action inherited from parent.
    assert "metric" in fields
    assert "action" in fields


def test_amend_target_not_in_parent_raises():
    parent = 'protocol "parent" { meta { version = "1.0" } }'
    child = '''
        protocol "child" extends "library/parent@1" {
          amend inhibit "nonexistent" { threshold = absolute(5 uV) }
        }
    '''
    with pytest.raises(ComposeError, match="amend target inhibit \"nonexistent\""):
        compose(parse(child), _dict_loader({"library/parent@1": parent}))


def test_amend_section_block_reward():
    parent = '''
        protocol "parent" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          derive "env" { from = "raw"; pipeline = [smooth(tau: 100 ms)] }
          reward {
            continuous = sigmoid("env", midpoint: 5 uV, steepness: 1)
            event      = dwell(condition: above("env", 5 uV), duration: 100 ms)
          }
        }
    '''
    child = '''
        protocol "child" extends "library/parent@1" {
          amend reward {
            continuous = sigmoid("env", midpoint: 10 uV, steepness: 2)
          }
        }
    '''
    merged = compose(parse(child), _dict_loader({"library/parent@1": parent}))
    reward = next(
        s for s in merged.protocol.body
        if isinstance(s, A.SectionBlock) and s.keyword == "reward"
    )
    fields = {a.target: a.value for a in reward.body if isinstance(a, A.Assignment)}
    # continuous replaced; event inherited.
    assert "continuous" in fields and "event" in fields
    # The new continuous has midpoint=10 uV.
    cont = fields["continuous"]
    mid_arg = next(a for a in cont.args if a.name == "midpoint")
    assert mid_arg.value.value == 10


# ---------------------------------------------------------------------------
# Named-decl replacement (no amend keyword)
# ---------------------------------------------------------------------------


def test_child_redeclaring_input_replaces_parent_input():
    parent = '''
        protocol "parent" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
        }
    '''
    child = '''
        protocol "child" extends "library/parent@1" {
          input "raw" { montage = bipolar(plus: "Cz", minus: "Pz") }
        }
    '''
    merged = compose(parse(child), _dict_loader({"library/parent@1": parent}))
    inp = next(
        s for s in merged.protocol.body
        if isinstance(s, A.NamedDecl) and s.keyword == "input" and s.name == "raw"
    )
    montage = next(a.value for a in inp.body if isinstance(a, A.Assignment) and a.target == "montage")
    plus = next(arg for arg in montage.args if arg.name == "plus")
    assert plus.value.value == "Cz"


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_named_decl():
    parent = '''
        protocol "parent" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          inhibit "emg" {
            metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = absolute(8 uV2)
            action    = mute(release: 200 ms)
          }
        }
    '''
    child = '''
        protocol "child" extends "library/parent@1" {
          remove inhibit "emg"
        }
    '''
    merged = compose(parse(child), _dict_loader({"library/parent@1": parent}))
    inhibits = [
        s for s in merged.protocol.body
        if isinstance(s, A.NamedDecl) and s.keyword == "inhibit"
    ]
    assert inhibits == []


def test_remove_target_not_in_parent_raises():
    parent = 'protocol "parent" { meta { version = "1.0" } }'
    child = '''
        protocol "child" extends "library/parent@1" {
          remove inhibit "ghost"
        }
    '''
    with pytest.raises(ComposeError, match="remove target inhibit \"ghost\""):
        compose(parse(child), _dict_loader({"library/parent@1": parent}))


# ---------------------------------------------------------------------------
# final
# ---------------------------------------------------------------------------


def test_final_blocks_amend():
    parent = '''
        protocol "safety_base" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          inhibit "safety_emg" {
            metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = percentile(target_pct: 99, window: 2 min)
            action    = mute(release: 200 ms)
            final     = true
          }
        }
    '''
    child = '''
        protocol "bad_child" extends "library/safety_base@1" {
          amend inhibit "safety_emg" { threshold = percentile(target_pct: 50, window: 2 min) }
        }
    '''
    with pytest.raises(ComposeError, match="cannot amend final inhibit"):
        compose(parse(child), _dict_loader({"library/safety_base@1": parent}))


def test_final_blocks_remove():
    parent = '''
        protocol "safety_base" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          inhibit "safety_emg" {
            metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = absolute(99 uV2)
            action    = mute(release: 200 ms)
            final     = true
          }
        }
    '''
    child = '''
        protocol "no_safety" extends "library/safety_base@1" {
          remove inhibit "safety_emg"
        }
    '''
    with pytest.raises(ComposeError, match="cannot remove final inhibit"):
        compose(parse(child), _dict_loader({"library/safety_base@1": parent}))


def test_final_blocks_redeclaration():
    parent = '''
        protocol "safety_base" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          inhibit "safety_emg" {
            metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = absolute(99 uV2)
            action    = mute(release: 200 ms)
            final     = true
          }
        }
    '''
    child = '''
        protocol "shadower" extends "library/safety_base@1" {
          inhibit "safety_emg" { action = flag() }
        }
    '''
    with pytest.raises(ComposeError, match="cannot redeclare final inhibit"):
        compose(parse(child), _dict_loader({"library/safety_base@1": parent}))


def test_final_false_is_not_a_lock():
    parent = '''
        protocol "lenient" {
          meta { version = "1.0" }
          input "raw" { montage = bipolar(plus: "T3", minus: "T4") }
          inhibit "emg" {
            metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = absolute(8 uV2)
            action    = mute(release: 200 ms)
            final     = false
          }
        }
    '''
    child = '''
        protocol "child" extends "library/lenient@1" {
          remove inhibit "emg"
        }
    '''
    merged = compose(parse(child), _dict_loader({"library/lenient@1": parent}))
    assert not any(
        isinstance(s, A.NamedDecl) and s.keyword == "inhibit"
        for s in merged.protocol.body
    )


def test_final_control_blocks_child_override():
    # Parent declares a final placement control; child trying to redeclare it must error.
    parent = '''
        protocol "base" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          controls { site = placement { kind = "active"; default = "F3"; allowed = ["F3"]; final = true } }
          input "raw" { montage = referential(active: "F3", reference: "linked_ears") }
          reward { continuous = sigmoid("raw", midpoint: 0 uV, steepness: 1) }
          output { audio_gain = reward.continuous }
        }
    '''
    child = '''
        protocol "v2" extends "library/base@1" {
          controls { site = placement { kind = "active"; default = "Cz"; allowed = ["Cz"] } }
        }
    '''
    with pytest.raises(ComposeError, match="final"):
        compose(parse(child), _dict_loader({"library/base@1": parent}))


# ---------------------------------------------------------------------------
# Chained inheritance
# ---------------------------------------------------------------------------


def test_chained_inheritance_a_to_b_to_c():
    a = 'protocol "a" { meta { version = "1.0" } }'
    b = '''
        protocol "b" extends "library/a@1" {
          input "x" { montage = bipolar(plus: "T3", minus: "T4") }
        }
    '''
    c = '''
        protocol "c" extends "library/b@1" {
          derive "y" { from = "x"; pipeline = [smooth(tau: 100 ms)] }
        }
    '''
    merged = compose(parse(c), _dict_loader({
        "library/a@1": a,
        "library/b@1": b,
    }))
    names = {
        s.name for s in merged.protocol.body
        if isinstance(s, A.NamedDecl)
    }
    assert names == {"x", "y"}


def test_cycle_detection():
    a = 'protocol "a" extends "library/b@1" { meta { version = "1.0" } }'
    b = 'protocol "b" extends "library/a@1" { meta { version = "1.0" } }'
    with pytest.raises(ComposeError, match="cycle in extends chain"):
        compose(parse(a), _dict_loader({
            "library/a@1": a, "library/b@1": b,
        }))


# ---------------------------------------------------------------------------
# Version compatibility (SPEC §11.5 — light-touch)
# ---------------------------------------------------------------------------


def test_major_version_match_is_ok():
    parent = '''
        protocol "p" {
          meta { version = "1.5.0" }
        }
    '''
    child = '''
        protocol "c" extends "library/p@1" {
          meta { version = "1.0" }
        }
    '''
    # Same major (1.x); should compose.
    compose(parse(child), _dict_loader({"library/p@1": parent}))


def test_major_version_mismatch_raises():
    parent = '''
        protocol "p" {
          meta { version = "2.0.0" }
        }
    '''
    child = '''
        protocol "c" extends "library/p@1" {
          meta { version = "1.0" }
        }
    '''
    with pytest.raises(ComposeError, match="major version mismatch"):
        compose(parse(child), _dict_loader({"library/p@1": parent}))


# ---------------------------------------------------------------------------
# Filesystem loader
# ---------------------------------------------------------------------------


def test_filesystem_loader_finds_parent(tmp_path):
    (tmp_path / "library" / "x").mkdir(parents=True)
    (tmp_path / "library" / "x" / "p.refrain").write_text(
        'protocol "p" { meta { version = "1.0" } }'
    )
    loader = filesystem_loader([tmp_path])
    f = loader("library/x/p")
    assert f.protocol.name == "p"


def test_filesystem_loader_searches_in_order(tmp_path):
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    (d1 / "library").mkdir(parents=True)
    (d2 / "library").mkdir(parents=True)
    (d1 / "library" / "x.refrain").write_text(
        'protocol "from_a" { meta { version = "1.0" } }'
    )
    (d2 / "library" / "x.refrain").write_text(
        'protocol "from_b" { meta { version = "1.0" } }'
    )
    loader = filesystem_loader([d1, d2])
    assert loader("library/x").protocol.name == "from_a"
    loader_reverse = filesystem_loader([d2, d1])
    assert loader_reverse("library/x").protocol.name == "from_b"


def test_filesystem_loader_reports_searched_dirs(tmp_path):
    loader = filesystem_loader([tmp_path])
    with pytest.raises(ComposeError, match="searched"):
        loader("library/no_such")


def test_default_library_dirs_from_env(monkeypatch):
    monkeypatch.setenv("REFRAIN_LIBRARY_PATH", "/path/one:/path/two::/path/three")
    dirs = default_library_dirs()
    assert [str(d) for d in dirs] == ["/path/one", "/path/two", "/path/three"]


def test_default_library_dirs_empty_when_unset(monkeypatch):
    monkeypatch.delenv("REFRAIN_LIBRARY_PATH", raising=False)
    assert default_library_dirs() == []


# ---------------------------------------------------------------------------
# End-to-end: the shipped Othmer ILF Cz-Pz example resolves
# ---------------------------------------------------------------------------


def test_shipped_othmer_cz_pz_variant_resolves():
    amp = load_amp_profile(AMP_PATH)
    file_ast = __import__("refrain").parse_file(EXAMPLES / "othmer_ilf_cz_pz.refrain")
    loader = filesystem_loader([EXAMPLES])
    ir = resolve(file_ast, amp, parent_loader=loader)

    # The Cz-Pz montage from the child.
    inp = ir.inputs["ilf"]
    plus = next(a for a in inp.montage.args if a.name == "plus").value.value
    minus = next(a for a in inp.montage.args if a.name == "minus").value.value
    assert (plus, minus) == ("Cz", "Pz")

    # The parent's derive pipeline survived intact.
    band = ir.derives["band"]
    assert "control/orf" in band.upstream
    assert "input/ilf" in band.upstream

    # The parent's EMG inhibit survived intact.
    assert "emg" in ir.inhibits

    # Meta merged: description from child, citation from parent.
    desc = ir.meta.fields["description"].value
    citation = ir.meta.fields["citation"].value
    assert "Cz-Pz" in desc
    assert "Othmer" in citation

    # Channels from the child amend.
    assert ir.requires.channels == ("Cz", "Pz")
