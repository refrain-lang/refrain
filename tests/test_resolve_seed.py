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
