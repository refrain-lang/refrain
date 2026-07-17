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
