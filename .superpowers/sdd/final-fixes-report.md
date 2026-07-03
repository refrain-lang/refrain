# Final-Review Fixes Report — Increment 1 Protocol Fuzzer

## Changes Applied

### Fix 1 (IMPORTANT) — absolute-only single-leaf detector (`surface.py`)

Added a guard in `_classify_single_leaf` after the existing `percentile` check:

```python
if thr.kind != "absolute":
    raise UnsupportedProtocol(f"single {thr.kind}-threshold reward (unsupported)")
```

Without this, a `dynamic` (or other) threshold would silently pass as supported and reach a generator/oracle that assumes `absolute` semantics — causing false passes or crashes.

**TDD evidence (Fix 1):** Added `test_dynamic_threshold_single_leaf_raises` in `tests/fuzz/test_unsupported.py`. This is a direct unit test on `_classify_single_leaf` with constructed `ThresholdSurface(name="t", signal="s", kind="dynamic")` and `DeriveSurface(name="s", band=(4.0,8.0), sos=[[1,0,0,1,0,0]], ...)`. Asserts `UnsupportedProtocol` with reason `"single dynamic-threshold reward (unsupported)"`. Test passes.

### Fix 2 (MINOR) — misnamed duplicate test (`test_unsupported.py`)

`test_percentile_single_leaf_defers_to_calibrated_oracle` was asserting the composite-signal reason via `composite_smr_theta.refrain` — identical to the already-existing `test_single_condition_reward_raises_typed_skip`. Since the identical composite test already exists, the duplicate was deleted (not renamed). Net change: –1 function, +1 function (Fix 1 test) = same count (10 tests).

### Fix 3 (MINOR) — consistent driven-leaf in warmup (`generate.py`)

Changed `_percentile_warmup_scenarios` from:
```python
first_leaf = next(_all_leaves(surface.reward_condition))
```
to:
```python
first_leaf = _driven_leaf(surface)
```

This uses the same "first `above` leaf, else first" selection as `_reward_positive_segments`, ensuring consistency. For `realistic_smr` the first leaf IS the `above` leaf, so this is a no-op. **Snapshot test `test_all_of_corpus_unchanged_after_gating` confirmed still passing.**

### Fix 4 (MINOR) — couple preroll to `_TAIL_PAD_S` (`generate.py`)

Changed `preroll = 2.0` to `preroll = _TAIL_PAD_S` in `_reward_positive_segments`'s `below` branch. The design requires `preroll == _TAIL_PAD_S` so the end-roll terminates exactly at `total_s`. Since `_TAIL_PAD_S = 2.0` the value is unchanged; this is a semantic coupling fix.

### Fix 5 (MINOR) — guard non-literal ratio arg (`surface.py`)

Changed:
```python
ratio = float(bw_expr.args[0].value.value)
```
to:
```python
inner = bw_expr.args[0].value
if not isinstance(inner, IRNumberLit):
    raise UnsupportedProtocol("center/bandwidth bandpass")
ratio = float(inner.value)
```

Without the guard, a non-literal `ratio(...)` arg (e.g. a control ref) raises `AttributeError` which is swallowed into the backstop "unclassified" bucket instead of a proper typed `UnsupportedProtocol`.

### Fix 6 (MINOR) — type annotations (`surface.py`)

Added full parameter and return type annotations to:
- `_classify_single_leaf(leaf: ConditionLeaf, derives: tuple[DeriveSurface, ...], thresholds: tuple[ThresholdSurface, ...]) -> ConditionLeaf`
- `_reward_condition_from_ir(ir: IRProtocol, derives: tuple[DeriveSurface, ...], thresholds: tuple[ThresholdSurface, ...]) -> ConditionNode | ConditionLeaf`

## Verification

| Check | Result |
|-------|--------|
| `test_dynamic_threshold_single_leaf_raises` (Fix 1 TDD) | PASS |
| `tests/fuzz/test_unsupported.py` (10 tests) | PASS |
| `test_all_of_corpus_unchanged_after_gating` (Fix 3 no-op) | PASS |
| Full `tests/fuzz/` suite | PASS (exit code 0) |
| `ruff check src/refrain/fuzz/` | All checks passed |
| `ruff check src/refrain --select F,E9` | All checks passed |

## Files Changed

- `/Users/jcroall/git/refrain/refrain/.claude/worktrees/fuzzer-parity-inc1/src/refrain/fuzz/surface.py` — Fix 1 (absolute guard), Fix 5 (IRNumberLit guard), Fix 6 (type annotations)
- `/Users/jcroall/git/refrain/refrain/.claude/worktrees/fuzzer-parity-inc1/src/refrain/fuzz/generate.py` — Fix 3 (`_driven_leaf` in warmup), Fix 4 (`_TAIL_PAD_S` coupling)
- `/Users/jcroall/git/refrain/refrain/.claude/worktrees/fuzzer-parity-inc1/tests/fuzz/test_unsupported.py` — Fix 1 (new test), Fix 2 (delete duplicate)

## Self-Review

- Fix 1: The guard correctly orders percentile before the absolute check (percentile guard first, then `!= absolute` catch-all). Any new threshold kind (e.g. `session_baseline`) will be caught without code changes.
- Fix 2: Deletion rather than rename was correct — the test body was identical to an already-named test; renaming would leave two identically-asserting tests.
- Fix 3: No-op confirmed by snapshot test. `_driven_leaf` degrades gracefully for single-leaf protocols.
- Fix 4: `_TAIL_PAD_S = 2.0` so runtime behavior is unchanged; coupling is correct for future changes to `_TAIL_PAD_S`.
- Fix 5: `AttributeError` from non-literal ratio is now a clean typed skip.
- Fix 6: Annotations match existing style (`-> ConditionNode | ConditionLeaf`, `tuple[X, ...]`).

No concerns.
