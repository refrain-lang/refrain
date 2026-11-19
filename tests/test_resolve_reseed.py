# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""`reseed` on a block: the declaration, its compile-time refusals, and the
downstream-hold set baked at resolve time (SPEC §4.13, IR-JSON §3.7).

Declaration only — nothing here runs a session. What the engines do with
`reseed_holds` at a block boundary is pinned by `tests/test_eval_reseed.py`.

The protocol below is a two-block staged session with two thresholds: `fixed_t`
is a plain `absolute(5 uV)` and `seeded_t` reads the seeded control. That split
is what makes the hold set testable at all — the set has to name the holds that
read the re-seeded control and leave the others alone.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from refrain.ir_json import ir_to_json_obj
from refrain.parser import parse
from refrain.resolver import ResolveError, resolve

REPO = pathlib.Path(__file__).resolve().parent.parent

_SRC = '''
protocol "reseed_demo" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "reseed demo" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "fixed_t"  { signal = "env"; type = absolute(5 uV) }
  threshold "seeded_t" { signal = "env"; type = absolute(value: smr_threshold_uv) }
  reward "one" { continuous = sigmoid("env" / "fixed_t", midpoint: 1.0, steepness: 3)%(one_event)s }
  reward "two" {
    continuous = sigmoid("env" / "seeded_t", midpoint: 1.0, steepness: 3)
    event      = dwell(condition: above("env", "seeded_t"), duration: 500 ms)
  }
  output { %(outputs)s }
  block "one" { threshold = "fixed_t";  reward = "one"; output = ["audio_gain", "audio_chime"] }
  block "two" { threshold = "seeded_t"; reward = "two"; output = ["audio_gain", "audio_chime"]%(reseed)s }
  controls {
    plain_uv = voltage { default = 5 uV; range = (1 uV, 10 uV); live_tunable = true }
    smr_threshold_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = %(seed_window)s; target_pct = 50 }
    }%(extra_controls)s
  }
  session { phases = [
    phase { name = "warmup"; duration = 120 s; output_muted = true },
    phase { name = "one";    duration = %(block_one_duration)s; block = "one" },
    phase { name = "two";    duration = 120 s; block = "two" },
  ] }
}
'''

_REWARD_OUTPUTS = "audio_gain = reward.continuous; audio_chime = reward.event"
# An output channel that binds a hold directly, rather than relaying the active
# block's bundle: the third kind of hold site.
_HOLD_OUTPUTS = (
    'audio_gain = dwell(condition: above("env", "seeded_t"), duration: 300 ms); '
    "audio_chime = reward.event"
)
# A seeded control nothing in the pipeline references: `_resolve_control_seeds`
# folds its seed out, so a `reseed` naming it would compile and re-measure a
# value no stream reads.
_DEAD_SEED_CONTROL = '''
    dead_uv = voltage {
      default = 3 uV; range = (1 uV, 9 uV)
      seed = percentile { from = "env"; window = 10 s; target_pct = 50 }
    }'''
# A hold on the UNSEEDED threshold, in the other block's bundle.
_INDEPENDENT_HOLD = (
    '\n    event      = dwell(condition: above("env", "fixed_t"), duration: 500 ms)'
)


def _staged_protocol(
    reseed: str | None = None,
    *,
    one_event: str = "",
    outputs: str = _REWARD_OUTPUTS,
    extra_controls: str = "",
    seed_window: str = "30 s",
    block_one_duration: str = "120 s",
) -> str:
    """The two-block protocol. `reseed` is the list literal as written in the
    DSL, or None for a block that declares none.

    The warm-up phase is 120 s so that a 60 s seed window clears the existing
    warm-up check and the test that names the re-seed window check actually
    reaches it.
    """
    return _SRC % {
        "reseed": "" if reseed is None else f"; reseed = {reseed}",
        "one_event": one_event,
        "outputs": outputs,
        "extra_controls": extra_controls,
        "seed_window": seed_window,
        "block_one_duration": block_one_duration,
    }


def _staged_protocol_with_dead_seed(reseed: str) -> str:
    return _staged_protocol(reseed, extra_controls=_DEAD_SEED_CONTROL)


def _staged_protocol_with_output_hold(reseed: str) -> str:
    return _staged_protocol(reseed, outputs=_HOLD_OUTPUTS)


def _staged_protocol_two_independent_holds(reseed: str) -> str:
    return _staged_protocol(reseed, one_event=_INDEPENDENT_HOLD)


def _staged_protocol_with_a_chained_threshold(reseed: str, *, hops: int = 1) -> str:
    """The two-block protocol with the hold moved behind a chain of thresholds.

    A threshold's level may be another threshold rather than a number — the
    evaluator evaluates exactly that (`eval_.py:1184-1188`) — so
    `smr_threshold_uv -> seeded_t -> chained_t` is legal, and a re-seed moves
    `chained_t` just as surely as it moves `seeded_t`. `hops` adds a third
    link, which is what tells a transitive closure apart from two hops.
    """
    chain = ['threshold "chained_t" { signal = "env"; type = absolute(value: "seeded_t") }']
    held = "chained_t"
    for i in range(hops - 1):
        name = f"link{i}_t"
        chain.append(
            f'threshold "{name}" {{ signal = "env"; type = absolute(value: "{held}") }}'
        )
        held = name
    src = _staged_protocol(reseed).replace(
        'threshold "seeded_t" { signal = "env"; type = absolute(value: smr_threshold_uv) }',
        'threshold "seeded_t" { signal = "env"; type = absolute(value: smr_threshold_uv) }\n  '
        + "\n  ".join(chain),
    ).replace('above("env", "seeded_t")', f'above("env", "{held}")')
    assert f'above("env", "{held}")' in src, "the hold did not move onto the chain"
    return src


def _staged_protocol_with_field(field: str, value: str) -> str:
    return _SRC % {
        "reseed": f"; {field} = {value}",
        "one_event": "",
        "outputs": _REWARD_OUTPUTS,
        "extra_controls": "",
        "seed_window": "30 s",
        "block_one_duration": "120 s",
    }


# A block that names no bundle falls through to the top-level reward, which is
# the `"reward/event"` hold site.
_TOP_LEVEL_SRC = '''
protocol "reseed_top" {
  meta { version = "1.0.0"; evidence = "clinical"; description = "top-level reward" }
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude() ]
  }
  threshold "seeded_t" { signal = "env"; type = absolute(value: smr_threshold_uv) }
  reward {
    continuous = sigmoid("env" / "seeded_t", midpoint: 1.0, steepness: 3)
    event      = dwell(condition: above("env", "seeded_t"), duration: 500 ms)
  }
  output { audio_gain = reward.continuous; audio_chime = reward.event }
  block "one" { output = ["audio_gain"] }
  block "two" { output = ["audio_gain", "audio_chime"]; reseed = ["smr_threshold_uv"] }
  controls {
    smr_threshold_uv = voltage {
      default = 2.0 uV; range = (0.5 uV, 10 uV); live_tunable = true
      seed = percentile { from = "env"; window = 30 s; target_pct = 50 }
    }
  }
  session { phases = [
    phase { name = "warmup"; duration = 120 s; output_muted = true },
    phase { name = "one";    duration = %(one_phase)s },
    phase { name = "two";    duration = 120 s; block = "two" },
  ] }
}
'''
_TIMED_ONE_PHASE = '120 s; block = "one"'
_OPEN_ONE_PHASE = '0 s; block = "one"; mode = open'


def _top_level_reward_protocol(one_phase: str = _TIMED_ONE_PHASE) -> str:
    return _TOP_LEVEL_SRC % {"one_phase": one_phase}


def _staged_example_with_a_reseed() -> str:
    """`examples/staged_beta_alpha.refrain`, with a seed on its alpha control
    and a `reseed` on the block that trains it.

    Built from the committed example rather than hand-written because of what
    the example has that no hand-written fixture would think to: the phase
    immediately before its second block activates no block at all and runs in
    open mode, which is exactly the shape a naive window check refuses.
    """
    src = (REPO / "examples" / "staged_beta_alpha.refrain").read_text()
    seeded = src.replace(
        'alpha_uv = voltage { default = 5 uV; '
        'label = "Alpha threshold (seeded at warmup)"; live_tunable = true }',
        'alpha_uv = voltage {\n'
        '      default = 5 uV; live_tunable = true\n'
        '      seed = percentile { from = "alpha_envelope"; window = 30 s; target_pct = 50 }\n'
        '    }',
    ).replace(
        'block "alpha_up" { threshold = "alpha_t"; reward = "alpha_reward"; '
        'output = ["audio_gain", "audio_chime"] }',
        'block "alpha_up" { threshold = "alpha_t"; reward = "alpha_reward"; '
        'output = ["audio_gain", "audio_chime"]; reseed = ["alpha_uv"] }',
    )
    assert seeded.count("reseed") == 1 and "seed = percentile" in seeded, (
        "the example's text moved; this fixture's two substitutions no longer apply"
    )
    return seeded


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


def test_reseed_resolves_to_the_named_controls():
    ir = resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]')))
    assert ir.blocks["two"].reseed == ("smr_threshold_uv",)


def test_reseed_absent_is_empty_and_is_todays_behaviour():
    ir = resolve(parse(_staged_protocol()))
    assert ir.blocks["two"].reseed == ()
    assert ir.blocks["two"].reseed_holds == ()


def test_reseed_naming_an_unknown_control_is_refused():
    with pytest.raises(ResolveError, match='unknown control "nope"'):
        resolve(parse(_staged_protocol(reseed='["nope"]')))


def test_reseed_naming_a_control_with_no_seed_rule_is_refused():
    with pytest.raises(ResolveError, match="has no `seed` rule"):
        resolve(parse(_staged_protocol(reseed='["plain_uv"]')))


def test_reseed_naming_a_control_whose_seed_was_folded_out_is_refused():
    """`_resolve_control_seeds` declines to attach a seed to a control nothing
    references, so without this refusal the declaration would compile and then
    do nothing for the whole session."""
    with pytest.raises(ResolveError, match="seed was dropped"):
        resolve(parse(_staged_protocol_with_dead_seed(reseed='["dead_uv"]')))


def test_reseed_naming_the_same_control_twice_is_refused():
    """A control re-measures itself once per boundary; the second entry could
    only fire against an emptied buffer and record a miss."""
    with pytest.raises(ResolveError, match="twice"):
        resolve(parse(_staged_protocol(reseed='["smr_threshold_uv", "smr_threshold_uv"]')))


def test_a_repeated_unknown_control_is_reported_as_unknown_not_as_a_duplicate():
    """Fixing the name is more use than being told it is written twice. The
    duplicate check sits first in the loop but can only ever fire on a name
    every earlier entry's checks passed, so the first occurrence of a wrong
    name is refused as wrong before the repeat is ever examined."""
    with pytest.raises(ResolveError, match='unknown control "nope"'):
        resolve(parse(_staged_protocol(reseed='["nope", "nope"]')))


def test_an_unexpected_block_field_is_still_refused():
    """`_resolve_block` has a strict allowed-field set; `reseed` joins it
    without opening the door to a typo of it."""
    with pytest.raises(ResolveError, match="unexpected field"):
        resolve(parse(_staged_protocol_with_field("resed", '["x"]')))


def test_the_allowed_field_message_names_reseed():
    """The list beside the set, which has been stale before in this resolver."""
    with pytest.raises(ResolveError) as exc:
        resolve(parse(_staged_protocol_with_field("resed", '["x"]')))
    assert "reseed" in str(exc.value).split("allowed:")[1]


# ---------------------------------------------------------------------------
# The window check
# ---------------------------------------------------------------------------


def test_a_reseed_window_that_cannot_fit_the_preceding_block_is_refused():
    """Mirrors the warm-up window check: a measurement window longer than the
    signal available before the boundary can never fill."""
    with pytest.raises(ResolveError, match="exceeds the preceding block"):
        resolve(parse(_staged_protocol(
            reseed='["smr_threshold_uv"]',
            block_one_duration="30 s",
            seed_window="60 s",
        )))


def test_a_window_that_fits_the_preceding_block_exactly_is_accepted():
    """The boundary of the refusal, not just its far side — `>` and `>=` are
    one character apart and only one of them is right."""
    ir = resolve(parse(_staged_protocol(
        reseed='["smr_threshold_uv"]',
        block_one_duration="60 s",
        seed_window="60 s",
    )))
    assert ir.blocks["two"].reseed == ("smr_threshold_uv",)


def test_an_output_muted_rest_does_not_count_toward_the_window():
    """Ingestion into a re-seed buffer is frozen while output is muted, so a
    long rest adds no samples and must not be allowed to pay for the window.
    The same phase left unmuted does pay for it — which is the half that says
    the refusal is about muting and not about the extra phase."""
    def with_a_rest(muted: str) -> str:
        return _staged_protocol(
            reseed='["smr_threshold_uv"]', block_one_duration="30 s", seed_window="60 s",
        ).replace(
            'phase { name = "one";    duration = 30 s; block = "one" },',
            'phase { name = "one";    duration = 30 s; block = "one" },\n'
            f'    phase {{ name = "rest";   duration = 600 s{muted} }},',
        )

    with pytest.raises(ResolveError, match="exceeds the preceding block"):
        resolve(parse(with_a_rest("; output_muted = true")))
    # A live phase of the same length is signal, and does fit the window.
    ir = resolve(parse(with_a_rest('; block = "one"')))
    assert ir.blocks["two"].reseed == ("smr_threshold_uv",)


def test_an_open_mode_training_phase_leaves_the_window_unchecked():
    """An open phase has no duration — the practitioner ends it when they end
    it — so how much signal precedes the boundary is unknown, not zero. The
    warm-up check guards on exactly this, and a check that refused here would
    refuse a protocol that fills its window every session."""
    ir = resolve(parse(_top_level_reward_protocol(one_phase=_OPEN_ONE_PHASE)))
    assert ir.blocks["two"].reseed == ("smr_threshold_uv",)


def test_the_staged_example_compiles_with_a_reseed_on_its_second_block():
    """`examples/staged_beta_alpha.refrain` puts an open-mode, output-muted
    rest immediately before its second block. A window check that measured the
    run of phases before the boundary would read a budget of zero there and
    refuse a legal Stage-3 protocol."""
    ir = resolve(parse(_staged_example_with_a_reseed()))
    assert ir.blocks["alpha_up"].reseed == ("alpha_uv",)
    assert ir.blocks["alpha_up"].reseed_holds == ("reward_bundle/alpha_reward",)
    assert ir.blocks["beta_up"].reseed == ()


# ---------------------------------------------------------------------------
# The downstream-hold set
# ---------------------------------------------------------------------------


def test_the_downstream_hold_set_names_the_reward_event():
    ir = resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]')))
    assert ir.blocks["two"].reseed_holds == ("reward_bundle/two",)


def test_the_downstream_hold_set_names_the_top_level_reward_event():
    """A block that names no bundle trains against the top-level reward, whose
    event is a separate site from any bundle's."""
    ir = resolve(parse(_top_level_reward_protocol()))
    assert ir.blocks["two"].reseed_holds == ("reward/event",)


def test_the_downstream_hold_set_names_an_output_bound_hold():
    ir = resolve(parse(_staged_protocol_with_output_hold(reseed='["smr_threshold_uv"]')))
    assert "output/audio_gain" in ir.blocks["two"].reseed_holds


def test_an_expression_that_reads_the_control_without_holding_is_not_a_site():
    """A site is a place with a streak to restart. An output channel bound
    straight to a sigmoid on the re-seeded threshold reads the control on every
    chunk and has no clock, so naming it would hand the engine a site with
    nothing in it to reset."""
    src = _staged_protocol(
        reseed='["smr_threshold_uv"]',
        outputs='audio_gain = sigmoid("env" / "seeded_t", midpoint: 1.0, steepness: 3); '
                "audio_chime = reward.event",
    )
    ir = resolve(parse(src))
    assert ir.blocks["two"].reseed_holds == ("reward_bundle/two",)


def test_a_hold_that_reads_the_control_without_a_threshold_is_in_the_set():
    """The other half of "downstream": a condition may compare against the
    control itself rather than against a threshold the control sets, and such a
    hold's streak is just as stale after a re-seed."""
    src = _staged_protocol(
        reseed='["smr_threshold_uv"]',
        outputs='audio_gain = reward.continuous; '
                'audio_cue = dwell(condition: above("env", smr_threshold_uv), '
                "duration: 300 ms)",
    ).replace(', "audio_chime"', ', "audio_cue"')
    ir = resolve(parse(src))
    assert "output/audio_cue" in ir.blocks["two"].reseed_holds


def test_a_hold_behind_a_chained_threshold_is_in_the_set():
    """The walk closes over threshold references instead of stopping one hop
    out. A threshold whose level is another threshold moves when the first one
    moves, so a hold reading the far end of the chain is holding a streak
    measured against a threshold that changed — the staleness this feature
    exists to remove. One hop and three are both in."""
    for hops in (1, 3):
        ir = resolve(parse(_staged_protocol_with_a_chained_threshold(
            '["smr_threshold_uv"]', hops=hops,
        )))
        assert ir.blocks["two"].reseed_holds == ("reward_bundle/two",), f"hops={hops}"


def test_a_hold_not_downstream_of_the_reseeded_control_is_not_in_the_set():
    """Narrower than a blanket boundary reset on purpose: every reward bundle
    is stepped on every chunk to keep its dwell timers warm, so resetting all
    of them would change what the staged protocols already do with no new
    declaration present. Block one's hold reads the fixed threshold."""
    ir = resolve(parse(_staged_protocol_two_independent_holds(reseed='["smr_threshold_uv"]')))
    assert ir.blocks["two"].reseed_holds == ("reward_bundle/two",)


def test_the_hold_set_is_sorted_so_the_wire_is_stable():
    ir = resolve(parse(_staged_protocol_with_output_hold(reseed='["smr_threshold_uv"]')))
    holds = ir.blocks["two"].reseed_holds
    assert list(holds) == sorted(holds) and len(holds) == 2


def test_a_reseeding_block_with_no_holds_downstream_bakes_an_empty_set():
    """Continuous-only bundles have no streak to reset, and the one hold left
    in the protocol — bound in an output channel, on the fixed threshold —
    reads nothing the re-seed touches. The declaration still stands, the
    control is still re-measured, so an empty set is an answer rather than a
    missing one."""
    src = _staged_protocol(
        reseed='["smr_threshold_uv"]',
        outputs='audio_gain = reward.continuous; '
                'audio_cue = dwell(condition: above("env", "fixed_t"), duration: 300 ms)',
    ).replace(
        '\n    event      = dwell(condition: above("env", "seeded_t"), duration: 500 ms)', ""
    ).replace(', "audio_chime"', ', "audio_cue"')
    ir = resolve(parse(src))
    assert ir.blocks["two"].reseed == ("smr_threshold_uv",)
    assert ir.blocks["two"].reseed_holds == ()


# ---------------------------------------------------------------------------
# The wire format
# ---------------------------------------------------------------------------


def test_a_reseeding_block_emits_both_keys_and_ir_0_6():
    """An engine that dropped the keys would run the whole session on the
    warm-up value, so the document cannot claim a version that has no slot
    for them."""
    obj = ir_to_json_obj(resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]'))))
    assert obj["refrain_ir_version"] == "0.6"
    assert obj["blocks"]["two"]["reseed"] == ["smr_threshold_uv"]
    assert obj["blocks"]["two"]["reseed_holds"] == ["reward_bundle/two"]


def test_a_block_without_a_reseed_emits_neither_key_and_keeps_its_version():
    """Absence stays absent on the wire: an emitted `"reseed": []` would be a
    new key in every existing staged document and move every pinned IR hash.
    0.3 is what this protocol's seeded control already asked for."""
    obj = ir_to_json_obj(resolve(parse(_staged_protocol())))
    assert obj["refrain_ir_version"] == "0.3"
    for block in obj["blocks"].values():
        assert "reseed" not in block and "reseed_holds" not in block


def test_the_sibling_block_of_a_reseeding_one_still_emits_neither_key():
    """The keys are per block, not per document — block one re-seeds nothing
    even in a protocol whose version has risen to 0.6."""
    obj = ir_to_json_obj(resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]'))))
    assert "reseed" not in obj["blocks"]["one"]


def test_the_reseed_keys_and_the_version_tag_never_disagree():
    """The two halves of the version rule, checked against each other rather
    than each against a remembered expectation."""
    documents = {
        "no reseed": _staged_protocol(),
        "reseed": _staged_protocol(reseed='["smr_threshold_uv"]'),
        "reseed + output hold": _staged_protocol_with_output_hold('["smr_threshold_uv"]'),
        "staged example + reseed": _staged_example_with_a_reseed(),
    }
    tagged = set()
    for label, src in documents.items():
        obj = ir_to_json_obj(resolve(parse(src)))
        carries = any("reseed" in b for b in obj["blocks"].values())
        assert carries == (obj["refrain_ir_version"] == "0.6"), (
            f"{label}: reseed present={carries} but version={obj['refrain_ir_version']}"
        )
        if carries:
            tagged.add(label)
    # Both halves must actually be exercised, or the loop passes over four
    # documents that all agree trivially.
    assert len(tagged) == 3, sorted(tagged)


def test_the_emitted_document_validates_against_the_v0_6_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (REPO / "src" / "refrain" / "schema" / "ir-json-v0.6.schema.json").read_text()
    )
    obj = ir_to_json_obj(resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]'))))
    jsonschema.Draft202012Validator(schema).validate(obj)


def test_the_schema_refuses_a_block_carrying_one_key_without_the_other():
    """The emitter writes both or neither, and `dependentRequired` is what
    stops a hand-edited document from asking an engine to reset holds it was
    never told about."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (REPO / "src" / "refrain" / "schema" / "ir-json-v0.6.schema.json").read_text()
    )
    obj = ir_to_json_obj(resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]'))))
    del obj["blocks"]["two"]["reseed_holds"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(obj)
