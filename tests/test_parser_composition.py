# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Coverage of composition operators and structural constructs:
extends, amend, remove, final-as-field, derive forms, reward shapes,
session phases, controls types, custom primitive declarations, and
multi-channel input lists."""

from __future__ import annotations

import pytest

from refrain import ast as A
from refrain import parse

# ---------------------------------------------------------------------------
# extends (SPEC §11)
# ---------------------------------------------------------------------------


def test_protocol_with_extends():
    f = parse(
        '''
        protocol "child" extends "library/parent@1.2" {
          meta { version = "1.0.0" }
        }
        '''
    )
    assert f.protocol.name == "child"
    assert f.protocol.extends == "library/parent@1.2"


def test_protocol_without_extends():
    f = parse('protocol "solo" { meta { version = "1.0.0" } }')
    assert f.protocol.extends is None


# ---------------------------------------------------------------------------
# amend (SPEC §11.2)
# ---------------------------------------------------------------------------


def test_amend_named_decl():
    f = parse(
        '''
        protocol "child" extends "lib/p@1" {
          amend inhibit "emg" {
            threshold = absolute(15 uV2)
          }
        }
        '''
    )
    stmt = f.protocol.body[0]
    assert isinstance(stmt, A.AmendDecl)
    assert stmt.target_kw == "inhibit"
    assert stmt.target_name == "emg"
    assert len(stmt.body) == 1
    assert stmt.body[0].target == "threshold"


def test_amend_section_block_reward():
    f = parse(
        '''
        protocol "child" extends "lib/p@1" {
          amend reward {
            continuous = sigmoid("x", midpoint: 1.0, steepness: 5)
          }
        }
        '''
    )
    stmt = f.protocol.body[0]
    assert isinstance(stmt, A.AmendDecl)
    assert stmt.target_kw == "reward"
    assert stmt.target_name is None


def test_amend_section_block_meta():
    f = parse(
        '''
        protocol "child" extends "lib/p@1" {
          amend meta {
            description = "Stricter variant"
          }
        }
        '''
    )
    stmt = f.protocol.body[0]
    assert isinstance(stmt, A.AmendDecl) and stmt.target_kw == "meta"


# ---------------------------------------------------------------------------
# remove (SPEC §11.3)
# ---------------------------------------------------------------------------


def test_remove_multiple_named_decls():
    f = parse(
        '''
        protocol "child" extends "lib/p@1" {
          remove inhibit "emg"
          remove inhibit "high_beta"
          remove derive "high_beta_envelope"
        }
        '''
    )
    body = f.protocol.body
    assert all(isinstance(s, A.RemoveDecl) for s in body)
    assert [(s.target_kw, s.target_name) for s in body] == [
        ("inhibit", "emg"),
        ("inhibit", "high_beta"),
        ("derive", "high_beta_envelope"),
    ]


# ---------------------------------------------------------------------------
# final-as-field (SPEC §11.4 — `final = true` inside a declaration)
# ---------------------------------------------------------------------------


def test_final_is_a_regular_field_assignment():
    # SPEC §11.4 uses `final = true` as a body field, not a syntactic
    # keyword on the declaration. The parser treats it as an Assignment.
    f = parse(
        '''
        protocol "safety_base" {
          inhibit "safety_emg" {
            metric    = bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)
            threshold = percentile(target_pct: 99, window: 2 min)
            action    = mute(release: 200 ms)
            final     = true
          }
        }
        '''
    )
    inhibit_decl = f.protocol.body[0]
    assert isinstance(inhibit_decl, A.NamedDecl)
    final_assignment = inhibit_decl.body[-1]
    assert isinstance(final_assignment, A.Assignment)
    assert final_assignment.target == "final"
    assert final_assignment.value == A.BoolLit(value=True)


# ---------------------------------------------------------------------------
# derive forms (SPEC §4.4) — pipeline vs formula
# ---------------------------------------------------------------------------


def test_derive_pipeline_form():
    f = parse(
        '''
        protocol "P" {
          derive "smr_env" {
            from = "raw"
            pipeline = [
              bandpass(band: (12 Hz, 15 Hz), order: 4),
              hilbert(),
              magnitude(),
              smooth(tau: 250 ms),
            ]
          }
        }
        '''
    )
    derive = f.protocol.body[0]
    assert isinstance(derive, A.NamedDecl) and derive.keyword == "derive"
    from_assign, pipeline_assign = derive.body
    assert from_assign.target == "from"
    assert from_assign.value == A.StringLit("raw")
    pipeline = pipeline_assign.value
    assert isinstance(pipeline, A.Array)
    assert len(pipeline.elements) == 4
    assert all(isinstance(e, A.Call) for e in pipeline.elements)


def test_derive_formula_form_arithmetic():
    f = parse(
        '''
        protocol "P" {
          derive "asym" {
            formula = ("L" - "R") / ("L" + "R")
          }
        }
        '''
    )
    derive = f.protocol.body[0]
    formula = derive.body[0].value
    assert isinstance(formula, A.BinaryOp) and formula.op == "/"


def test_derive_formula_form_nested_calls():
    f = parse(
        '''
        protocol "P" {
          derive "smr_env" {
            formula = smooth(
              magnitude(hilbert(bandpass("raw", band: (12 Hz, 15 Hz)))),
              tau: 250 ms
            )
          }
        }
        '''
    )
    derive = f.protocol.body[0]
    formula = derive.body[0].value
    assert isinstance(formula, A.Call) and formula.callee == "smooth"
    inner = formula.args[0].value
    assert isinstance(inner, A.Call) and inner.callee == "magnitude"


def test_derive_formula_with_align_to():
    f = parse(
        '''
        protocol "P" {
          derive "cmp" {
            formula = align_to("raw_env", target: "auto") > "auto"
          }
        }
        '''
    )
    formula = f.protocol.body[0].body[0].value
    assert isinstance(formula, A.BinaryOp) and formula.op == ">"
    assert isinstance(formula.left, A.Call) and formula.left.callee == "align_to"


# ---------------------------------------------------------------------------
# reward shapes (SPEC §4.7) — continuous-only, event-only, both
# ---------------------------------------------------------------------------


def test_reward_continuous_only():
    f = parse(
        '''
        protocol "P" {
          reward {
            continuous = sigmoid("x", midpoint: 0.5, steepness: 4)
          }
        }
        '''
    )
    reward = f.protocol.body[0]
    targets = [s.target for s in reward.body]
    assert targets == ["continuous"]


def test_reward_event_only():
    f = parse(
        '''
        protocol "P" {
          reward {
            event = dwell(condition: above("a", "b"), duration: 250 ms)
          }
        }
        '''
    )
    reward = f.protocol.body[0]
    targets = [s.target for s in reward.body]
    assert targets == ["event"]


def test_reward_both_continuous_and_event():
    f = parse(
        '''
        protocol "P" {
          reward {
            continuous = sigmoid("x", midpoint: 0 uV, steepness: 0.5)
            event = dwell(condition: above("t", "a"), duration: 1000 ms)
          }
        }
        '''
    )
    reward = f.protocol.body[0]
    targets = [s.target for s in reward.body]
    assert sorted(targets) == ["continuous", "event"]


# ---------------------------------------------------------------------------
# custom primitive declaration (SPEC §4.11)
# ---------------------------------------------------------------------------


def test_custom_decl_parses_as_named_decl():
    # SPEC §4.11 shows two pieces of syntax that the §3 grammar does not
    # define: (1) a bare type expression on the RHS of `signature =`, and
    # (2) `:`-separated, comma-joined field-init form inside an anonymous
    # record `{ state_kb: 4, worst_case_us: 50 }`. SPEC §3 only defines
    # `=`-form assignments inside blocks. Per the §3 grammar, we accept
    # the signature as a string literal and the budget as a block_expr
    # with `=`-form assignments. Both interpretations are documented in
    # the PR as spec ambiguities to resolve in a future revision.
    f = parse(
        '''
        protocol "P" {
          custom "my_phase_metric" {
            module    = "myplugin.phase:compute"
            signature = "(stream<vector<19> uV>) -> stream<scalar dimensionless>"
            budget    = {
              state_kb = 4
              worst_case_us = 50
            }
          }
        }
        '''
    )
    custom = f.protocol.body[0]
    assert isinstance(custom, A.NamedDecl) and custom.keyword == "custom"
    assert custom.name == "my_phase_metric"
    field_names = [s.target for s in custom.body]
    assert field_names == ["module", "signature", "budget"]
    budget = custom.body[2].value
    assert isinstance(budget, A.BlockExpr) and budget.name is None
    assert [s.target for s in budget.body] == ["state_kb", "worst_case_us"]


def test_custom_decl_minimal():
    f = parse(
        '''
        protocol "P" {
          custom "x" {
            module = "m:f"
          }
        }
        '''
    )
    custom = f.protocol.body[0]
    assert isinstance(custom, A.NamedDecl) and custom.keyword == "custom"
    assert custom.name == "x"


# ---------------------------------------------------------------------------
# Session phases (SPEC §4.10)
# ---------------------------------------------------------------------------


def test_session_phases_array_of_block_exprs():
    f = parse(
        '''
        protocol "P" {
          session {
            phases = [
              phase { name = "warmup";   duration = 60 s; output_muted = true },
              phase { name = "training"; duration = 25 min },
              phase { name = "cooldown"; duration = 60 s; output_muted = true },
            ]
          }
        }
        '''
    )
    session = f.protocol.body[0]
    phases_assign = session.body[0]
    assert phases_assign.target == "phases"
    arr = phases_assign.value
    assert isinstance(arr, A.Array)
    assert len(arr.elements) == 3
    for elt in arr.elements:
        assert isinstance(elt, A.BlockExpr) and elt.name == "phase"


# ---------------------------------------------------------------------------
# Controls — typed block expressions (SPEC §4.9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("control_type", ["frequency", "duration", "voltage", "percent", "boolean"])
def test_control_typed_block(control_type):
    src = f'''
        protocol "P" {{
          controls {{
            x = {control_type} {{
              default = 1
            }}
          }}
        }}
    '''
    f = parse(src)
    controls = f.protocol.body[0]
    assign = controls.body[0]
    assert assign.target == "x"
    assert isinstance(assign.value, A.BlockExpr)
    assert assign.value.name == control_type


def test_control_full_orf_form():
    f = parse(
        '''
        protocol "P" {
          controls {
            orf = frequency {
              range        = (0.0001 Hz, 0.5 Hz)
              default      = 0.01 Hz
              log          = true
              label        = "Optimal Reinforcement Frequency"
              live_tunable = true
            }
          }
        }
        '''
    )
    block = f.protocol.body[0].body[0].value
    assert isinstance(block, A.BlockExpr) and block.name == "frequency"
    fields = {s.target: s.value for s in block.body}
    assert fields["log"] == A.BoolLit(True)
    assert isinstance(fields["range"], A.Tuple)


# ---------------------------------------------------------------------------
# Multi-channel input lists (SPEC §4.2, §4.3)
# ---------------------------------------------------------------------------


def test_requires_channels_multi():
    f = parse(
        '''
        protocol "P" {
          requires {
            channels = ["F3", "F4", "T3", "T4", "Cz", "Pz"]
          }
        }
        '''
    )
    arr = f.protocol.body[0].body[0].value
    assert isinstance(arr, A.Array)
    assert len(arr.elements) == 6
    assert all(isinstance(e, A.StringLit) for e in arr.elements)


def test_input_referential_with_channel_list():
    f = parse(
        '''
        protocol "P" {
          input "raw_19ch" {
            montage = referential(
              channels: ["Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4",
                         "O1", "O2", "F7", "F8", "T3", "T4", "T5", "T6",
                         "Fz", "Cz", "Pz"],
              reference: "linked_ears"
            )
          }
        }
        '''
    )
    inp = f.protocol.body[0]
    montage = inp.body[0].value
    assert isinstance(montage, A.Call) and montage.callee == "referential"
    chans = next(a for a in montage.args if a.name == "channels").value
    assert isinstance(chans, A.Array)
    assert len(chans.elements) == 19


# ---------------------------------------------------------------------------
# Output bindings (SPEC §4.8)
# ---------------------------------------------------------------------------


def test_output_binding_with_expression():
    f = parse(
        '''
        protocol "P" {
          output {
            audio_gain = 0.2 + 0.8 * reward.continuous
          }
        }
        '''
    )
    binding = f.protocol.body[0].body[0]
    assert binding.target == "audio_gain"
    val = binding.value
    assert isinstance(val, A.BinaryOp) and val.op == "+"


def test_output_event_chime_binding():
    f = parse(
        '''
        protocol "P" {
          output {
            audio_chime = reward.event
          }
        }
        '''
    )
    val = f.protocol.body[0].body[0].value
    assert isinstance(val, A.MemberAccess) and val.member == "event"


# ---------------------------------------------------------------------------
# Imports (SPEC §3 `import_decl`)
# ---------------------------------------------------------------------------


def test_import_without_alias():
    f = parse(
        '''
        import "library/safety_base@1.0";
        protocol "P" {
          meta { version = "1.0" }
        }
        '''
    )
    assert len(f.imports) == 1
    assert f.imports[0].path == "library/safety_base@1.0"
    assert f.imports[0].alias is None


def test_import_with_alias():
    f = parse(
        '''
        import "library/parent@1" as parent;
        protocol "P" {
          meta { version = "1.0" }
        }
        '''
    )
    assert f.imports[0].alias == "parent"


def test_multiple_imports():
    f = parse(
        '''
        import "lib/a@1";
        import "lib/b@2" as b;
        protocol "P" {
          meta { version = "1.0" }
        }
        '''
    )
    assert len(f.imports) == 2


# ---------------------------------------------------------------------------
# groups block (named allowed groups)
# ---------------------------------------------------------------------------


def test_groups_block_parses():
    src = '''
        protocol "p" {
          meta { version = "1.0"; evidence = "clinical"; description = "x" }
          groups { sensorimotor = ["C3","Cz","C4"]; frontal = ["F3","Fz","F4"] }
        }
    '''
    proto = parse(src).protocol
    groups = [s for s in proto.body if isinstance(s, A.SectionBlock) and s.keyword == "groups"]
    assert len(groups) == 1
    entries = {s.target: s.value for s in groups[0].body if isinstance(s, A.Assignment)}
    assert set(entries) == {"sensorimotor", "frontal"}
    assert isinstance(entries["sensorimotor"], A.Array)
    assert [e.value for e in entries["sensorimotor"].elements] == ["C3", "Cz", "C4"]
