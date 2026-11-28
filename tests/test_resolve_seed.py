# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Resolver tests for the control `seed = percentile { ... }` sub-block.

Task 5 scope: the resolver parses the seed block into a pending map
(`_Resolver._pending_seeds`) and rejects malformed blocks immediately.
Cross-section validation (the `from` derive existing, `target_pct`
resolving to a sibling control, baking the window) is Task 6's post-pass.
"""

from refrain.compile_json import compile_to_ir_json
from tests._seed_fixtures import BASE, GOOD  # verified block-syntax fixtures (Task 4)
from tests._seed_fixtures import MODE_SRC  # verified ternary mode-conditional

# BASE is a `%`-template: substitute ONE seed line via `BASE % {"seed": "..."}`.
# NEVER str.format() — the protocol body is full of literal `{}`.


def test_seed_block_rejects_unknown_statistic():
    src = BASE % {"seed": 'seed = median { from = "env"; window = 60 s; target_pct = reward_pct }'}
    res = compile_to_ir_json(src)
    assert res.errors and "percentile" in res.errors[0].message


def test_seed_block_requires_duration_window():
    src = BASE % {"seed": 'seed = percentile { from = "env"; window = 60; target_pct = reward_pct }'}
    res = compile_to_ir_json(src)
    assert res.errors and "window" in res.errors[0].message


def test_good_seed_resolves_and_bakes_window():
    obj = compile_to_ir_json(GOOD).ir_json
    seed = obj["controls"]["thr_uv"]["seed"]
    assert seed["from"] == "derive/env"
    assert seed["window_samples"] == 60 * 256


def test_unknown_from_is_a_resolve_error():
    src = BASE % {"seed": 'seed = percentile { from = "nope"; window = 60 s; target_pct = reward_pct }'}
    res = compile_to_ir_json(src)
    assert res.errors and "nope" in res.errors[0].message


def test_target_pct_must_be_a_percent_control_or_number():
    # bind to a voltage control (thr_uv) -> rejected
    src = BASE % {"seed": 'seed = percentile { from = "env"; window = 60 s; target_pct = thr_uv }'}
    res = compile_to_ir_json(src)
    assert res.errors and "percent" in res.errors[0].message


def test_target_pct_number_literal_is_accepted():
    src = BASE % {"seed": 'seed = percentile { from = "env"; window = 60 s; target_pct = 40 }'}
    obj = compile_to_ir_json(src).ir_json
    assert obj["controls"]["thr_uv"]["seed"]["target_pct"]["node"] == "number"


def test_target_pct_percent_literal_is_accepted():
    src = BASE % {"seed": 'seed = percentile { from = "env"; window = 60 s; target_pct = 40 % }'}
    res = compile_to_ir_json(src)
    assert not res.errors
    assert res.ir_json["controls"]["thr_uv"]["seed"]["target_pct"]["value"] == 40


def test_target_pct_in_another_unit_is_refused():
    """The evaluator reads the literal's value and never its unit, so a
    percentile written `50 s` ran as the 50th percentile without comment."""
    for bad in ("50 s", "50 ms", "50 uV", "50 Hz"):
        src = BASE % {"seed": f'seed = percentile {{ from = "env"; window = 60 s; target_pct = {bad} }}'}
        res = compile_to_ir_json(src)
        assert res.errors, bad
        assert "target_pct" in res.errors[0].message and "percentile" in res.errors[0].message


def test_an_unknown_seed_field_is_refused():
    """A misspelled `scale` must not resolve to a rule with the default
    multiplier — that seeds a 3x-median limit at the median."""
    for stray in ("cale = 3.0", "scal = 3.0", "target = 50", "percentile = 50"):
        src = BASE % {"seed": f'seed = percentile {{ from = "env"; window = 60 s; target_pct = 40; {stray} }}'}
        res = compile_to_ir_json(src)
        field = stray.split(" ")[0]
        assert res.errors, stray
        assert field in res.errors[0].message and "scale" in res.errors[0].message


def test_scale_on_the_line_after_a_bare_target_pct_is_read_as_scale():
    """The layout that found the bug, end to end: one field per line, no `;`,
    `scale` straight after a unit-less number. It parsed as
    `target_pct = 50 s` plus a field named `cale`, the rule resolved with
    `scale = 1.0`, and the session it seeded was muted from start to finish."""
    src = BASE % {"seed": """seed = percentile {
          from       = "env"
          window     = 60 s
          target_pct = 50
          scale      = 3.0
        }"""}
    res = compile_to_ir_json(src)
    assert not res.errors
    seed = res.ir_json["controls"]["thr_uv"]["seed"]
    assert seed["scale"] == 3.0
    assert seed["target_pct"]["value"] == 50
    assert seed["target_pct"].get("unit") is None


_SEED = 'seed = percentile { from = "env"; window = 60 s; target_pct = reward_pct }'


def test_window_longer_than_warmup_is_a_resolve_error():
    # shrink the 90 s warmup below the 60 s window (replace before %-substitution)
    tmpl = BASE.replace('duration = 90 s; output_muted = true',
                        'duration = 30 s; output_muted = true')
    res = compile_to_ir_json(tmpl % {"seed": _SEED})
    assert res.errors and "warmup" in res.errors[0].message.lower()


def test_window_equal_to_warmup_is_allowed():
    tmpl = BASE.replace('duration = 90 s; output_muted = true',
                        'duration = 60 s; output_muted = true')
    assert not compile_to_ir_json(tmpl % {"seed": _SEED}).errors


def test_seed_dropped_when_control_folded_out():
    # adaptive -> percentile branch survives, absolute(thr_uv) is deleted, so
    # thr_uv is unreferenced. Its seed must be dropped from the resolved IR.
    obj = compile_to_ir_json(MODE_SRC, bindings={"threshold_style": "adaptive"}).ir_json
    assert "seed" not in obj["controls"].get("thr_uv", {})


def test_seed_kept_when_control_referenced():
    obj = compile_to_ir_json(MODE_SRC, bindings={"threshold_style": "baseline"}).ir_json
    assert "seed" in obj["controls"]["thr_uv"]

# ---------------------------------------------------------------------------
# `scale` — a plain multiplier on the statistic (SPEC §4.9.4)
# ---------------------------------------------------------------------------


def _scaled(scale: str) -> str:
    """BASE with a seed whose only difference from `_SEED` is `scale`."""
    return BASE % {
        "seed": 'seed = percentile { from = "env"; window = 60 s; '
                f"target_pct = reward_pct; scale = {scale} }}"
    }


def test_scale_resolves_as_a_plain_multiplier():
    obj = compile_to_ir_json(_scaled("3.0")).ir_json
    assert obj["controls"]["thr_uv"]["seed"]["scale"] == 3.0


def test_scale_absent_is_one():
    # Read off the resolved IR rather than the wire: the emitter omits the key
    # at 1.0 on purpose, which is what keeps an existing seed byte-identical.
    from refrain.parser import parse
    from refrain.resolver import resolve

    ir = resolve(parse(BASE % {"seed": _SEED}))
    assert ir.controls["thr_uv"].seed.scale == 1.0


def test_a_non_positive_scale_is_refused():
    """Review Focus 4: `scale = 0` seeds a muscle limit at 0 uV, so its
    inhibit mutes the whole session; a negative one lands on the control's
    range floor and reads as a working protocol."""
    for bad in ("0", "0.0", "-1.0", "-0.5"):
        res = compile_to_ir_json(_scaled(bad))
        assert res.errors, bad
        assert "scale must be greater than zero" in res.errors[0].message, bad


def test_scale_may_not_bind_a_control():
    """A value written once, at the warmup->run edge, cannot usefully be
    retuned live — so the multiplier is a literal, and a control reference is
    refused by name rather than silently read as zero."""
    res = compile_to_ir_json(_scaled("reward_pct"))
    assert res.errors
    assert "scale must be a plain number" in res.errors[0].message
    assert "reward_pct" in res.errors[0].message


def test_a_non_finite_scale_is_named_for_what_it_is_not_called_a_control():
    """`nan` and `inf` are bare identifiers to this grammar, so they arrive at
    `scale` the same way a control name does. Reporting them as "the control
    'nan'" sends the author hunting for a control they never declared."""
    for word in ("nan", "inf", "infinity", "NaN"):
        res = compile_to_ir_json(_scaled(word))
        assert res.errors, word
        message = res.errors[0].message
        assert "scale must be a plain number" in message, word
        assert f"the name {word!r}" in message, message
        assert "the control" not in message, message


def test_a_scale_with_a_unit_is_refused():
    """The multiplier is dimensionless: `scale = 3 uV` would read as a limit,
    not a multiple of one."""
    res = compile_to_ir_json(_scaled("3.0 uV"))
    assert res.errors
    assert "scale must be a plain number" in res.errors[0].message
