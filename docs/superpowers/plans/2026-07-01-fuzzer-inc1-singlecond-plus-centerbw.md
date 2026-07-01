# Fuzzer Increment 1 (single-condition + center/bandwidth) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fuzz absolute single-condition rewards (above & below) and center/bandwidth-declared bandpass derives — the two entangled features that gate the bulk of the `refrain-protocols` corpus together.

**Architecture:** All changes are in `surface.py` (introspection) and `generate.py` (scenario generation); `oracle.py` is untouched (it reads the baked SOS, present in IR-JSON for both declaration forms). A supportability detector turns a sole `ConditionLeaf` into either a supported leaf (absolute threshold) or a specifically-labelled skip; the generator is generalized off its hardcoded `smr_envelope`/above-semantics; and `_band_from_call` learns the center/bandwidth form via pure arithmetic.

**Tech Stack:** Python 3.10+, pytest (in-process `main([...])` / `fuzz_protocol`), ruff.

## Global Constraints

- Python floor 3.10. No new third-party dependencies.
- Source files start with the 2-line Apache header.
- `src/refrain/fuzz/` ruff-clean; CI gate `ruff check src/refrain --select F,E9` clean.
- Tests via `.venv/bin/python -m pytest tests/fuzz/ -q`; keep green.
- Supported single-leaf = sole `above/below(signal, threshold)` where signal names a derive with a baked SOS AND threshold resolves AND threshold kind is `absolute`. Everything else skips with a specific reason.
- Skip reason vocabulary (exact strings): `single percentile-leaf reward (needs calibrated oracle)`, `composite-signal reward condition`, `non-bandpass (coherence) reward signal`, `reward condition without a resolvable threshold`.
- center/bandwidth band formula (from `primitive_impls._resolve_band`): `band = (center / sqrt(ratio), center * sqrt(ratio))`.
- Generator changes MUST be provable no-ops for the 4 currently-fuzzed `all_of` protocols (`realistic_smr`, `smr_cz`, `smr_cz_brainbit`, `micro_05_reward`).
- Reuse over reinvent: reuse `_resolve_control_default`, `_arg`, `_amplitude_for_truth`, `bandpass_gain_at`; don't duplicate.

## File structure

- `src/refrain/fuzz/surface.py` — single-leaf detector (`_reward_condition_from_ir` + a `_classify_single_leaf` helper), `reward_condition` type, center/bandwidth in `_band_from_call`.
- `src/refrain/fuzz/generate.py` — `_driven_leaf`/`_driven_derive`, op-aware drive, gate the smr_cz-specific extras.
- `bench/protocols/micro_single_above.refrain`, `micro_single_below.refrain`, `micro_center_bandwidth.refrain` — new fixtures.
- Tests: `tests/fuzz/test_unsupported.py` (extend), `test_surface.py` (extend, center/bandwidth), `test_runner.py` (extend, end-to-end), `test_generate.py` (extend, no-op guard), `test_batch.py` (extend, coverage counts).

Reference (verified): sole `above(env, absolute)` fuzzes 10/10; center/bandwidth SOS is always in IR-JSON; `alpha_up_pz` center=9.798/ratio=1.5 → band (8,12).

---

### Task 1: center/bandwidth band reading (`_band_from_call`)

**Files:**
- Modify: `src/refrain/fuzz/surface.py` (`_band_from_call`, ~line 169-180; uses existing `_resolve_control_default`, `_arg`)
- Create: `bench/protocols/micro_center_bandwidth.refrain`
- Test: `tests/fuzz/test_surface.py`

**Interfaces:**
- Produces: `_band_from_call(call, ir)` now returns a `(lo, hi)` tuple for BOTH the `band=(lo,hi)` and `center:`/`bandwidth:` forms. (It gains an `ir` param to resolve a control-ref center.)

- [ ] **Step 1: Create the center/bandwidth fixture**

Create `bench/protocols/micro_center_bandwidth.refrain`:

```
protocol "micro_center_bandwidth" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "cb_env" {
    from = "raw"
    pipeline = [ bandpass(center: 13.5 Hz, bandwidth: ratio(1.234568), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ]
  }
  threshold "cb_t" { signal = "cb_env"; type = absolute(8 uV) }
  reward {
    event = dwell(condition: above("cb_env", "cb_t"), duration: 250 ms)
    continuous = sigmoid("cb_env" / "cb_t", midpoint: 1.0, steepness: 3)
  }
  output { audio_chime = reward.event }
  session { phases = [ phase { name = "training"; duration = 30 min } ] }
}
```

(center 13.5, ratio 1.234568 → sqrt≈1.11111 → band ≈ (12.15, 15.0).)

- [ ] **Step 2: Write the failing test**

Add to `tests/fuzz/test_surface.py`:

```python
def test_center_bandwidth_band_from_args():
    from refrain.parser import parse_file
    from refrain.resolver import resolve
    from refrain.fuzz.surface import _band_from_call, _arg
    from refrain.ir import IRCall
    ir = resolve(parse_file(REPO_ROOT / "bench/protocols/micro_center_bandwidth.refrain"), None)
    # find the bandpass call in the cb_env derive
    call = None
    def walk(o):
        nonlocal call
        if isinstance(o, IRCall) and o.callee == "bandpass":
            call = o
        for a in getattr(o, "args", []):
            walk(a.value)
        for attr in ("expression", "value"):
            v = getattr(o, attr, None)
            if v is not None and not isinstance(v, (str, int, float)):
                walk(v)
    walk(ir.derives["cb_env"])
    lo, hi = _band_from_call(call, ir)
    assert abs(lo - 12.15) < 0.1 and abs(hi - 15.0) < 0.1
```

(`REPO_ROOT` already defined at the top of `test_surface.py`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_surface.py::test_center_bandwidth_band_from_args -q`
Expected: FAIL — `_band_from_call` raises `UnsupportedProtocol("center/bandwidth bandpass")` (and/or the signature mismatch).

- [ ] **Step 4: Implement center/bandwidth reading**

Replace `_band_from_call` in `surface.py`. Add `import math` at the top of the file if not present.

```python
def _band_from_call(call: IRCall, ir: IRProtocol) -> tuple[float, float]:
    """Read `band: (lo Hz, hi Hz)`, or derive edges from the `center:`/`bandwidth:`
    form. Formula mirrors primitive_impls._resolve_band (the resolver's source of
    truth): band = (center / sqrt(ratio), center * sqrt(ratio))."""
    band = _arg(call, "band")
    if band is not None:
        lo, hi = band.elements
        return (float(lo.value), float(hi.value))
    center_expr = _arg(call, "center")
    bw_expr = _arg(call, "bandwidth")
    if center_expr is None or bw_expr is None:
        raise UnsupportedProtocol("center/bandwidth bandpass")
    if isinstance(center_expr, IRNumberLit):
        center = float(center_expr.value)
    elif isinstance(center_expr, IRControlRef):
        center = _resolve_control_default(ir, center_expr)
    else:
        raise UnsupportedProtocol("center/bandwidth bandpass")
    # bandwidth is `ratio(R)` — an IRCall wrapping one number.
    if not (isinstance(bw_expr, IRCall) and bw_expr.callee == "ratio" and bw_expr.args):
        raise UnsupportedProtocol("center/bandwidth bandpass")
    ratio = float(bw_expr.args[0].value.value)
    if center is None or center <= 0 or ratio <= 0:
        raise UnsupportedProtocol("center/bandwidth bandpass")
    sqrt_r = math.sqrt(ratio)
    return (center / sqrt_r, center * sqrt_r)
```

Update the one caller of `_band_from_call` (in `_derive_surface`) to pass `ir`. Find it: `grep -n "_band_from_call" src/refrain/fuzz/surface.py` and add the `ir` argument (the caller already has `ir` in scope — `_derive_surface(d, ir, j)`). Ensure `IRControlRef` and `IRNumberLit` are imported (they are — check the import block).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_surface.py -q && .venv/bin/ruff check src/refrain/fuzz/`
Expected: PASS; "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/surface.py bench/protocols/micro_center_bandwidth.refrain tests/fuzz/test_surface.py
git commit -m "feat(fuzz): read center/bandwidth bandpass band edges (band = center/sqrt(r), center*sqrt(r))"
```

---

### Task 2: single-leaf supportability detector

**Files:**
- Modify: `src/refrain/fuzz/surface.py` (`_reward_condition_from_ir` ~line 357-367; `reward_condition` field ~line 101; `build_surface` call site ~line 408)
- Create: `bench/protocols/micro_single_above.refrain`, `bench/protocols/micro_single_below.refrain`
- Test: `tests/fuzz/test_unsupported.py`

**Interfaces:**
- Consumes: Task 1's `_band_from_call` (so center/bandwidth derives build).
- Produces: `build_surface(ir).reward_condition` may now be a `ConditionLeaf` (supported absolute single-leaf) or `ConditionNode`. Unsupported single-leaf shapes raise `UnsupportedProtocol` with the exact reasons in Global Constraints.

- [ ] **Step 1: Create the two absolute single-leaf fixtures**

`bench/protocols/micro_single_above.refrain` — derive named `up_env` (NOT `smr_envelope`, to exercise the generalization), edge-frequency band, absolute threshold:

```
protocol "micro_single_above" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "up_env" {
    from = "raw"
    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ]
  }
  threshold "up_t" { signal = "up_env"; type = absolute(8 uV) }
  reward {
    event = dwell(condition: above("up_env", "up_t"), duration: 250 ms)
    continuous = sigmoid("up_env" / "up_t", midpoint: 1.0, steepness: 3)
  }
  output { audio_chime = reward.event }
  session { phases = [ phase { name = "training"; duration = 30 min } ] }
}
```

`bench/protocols/micro_single_below.refrain` — identical but `below` and a higher absolute threshold so the quiet baseline is clearly TRUE:

```
protocol "micro_single_below" {
  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }
  input "raw" { montage = referential(active: "Cz", reference: "linked_ears") }
  derive "down_env" {
    from = "raw"
    pipeline = [ bandpass(band: (4 Hz, 8 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ]
  }
  threshold "down_t" { signal = "down_env"; type = absolute(8 uV) }
  reward {
    event = dwell(condition: below("down_env", "down_t"), duration: 250 ms)
    continuous = sigmoid("down_t" / "down_env", midpoint: 1.0, steepness: 3)
  }
  output { audio_chime = reward.event }
  session { phases = [ phase { name = "training"; duration = 30 min } ] }
}
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/fuzz/test_unsupported.py`:

```python
def test_single_above_absolute_builds():
    ir = _ir("bench/protocols/micro_single_above.refrain")
    from refrain.fuzz.surface import ConditionLeaf
    surf = build_surface(ir)          # no raise
    assert isinstance(surf.reward_condition, ConditionLeaf)
    assert surf.reward_condition.op == "above"

def test_single_below_absolute_builds():
    ir = _ir("bench/protocols/micro_single_below.refrain")
    from refrain.fuzz.surface import ConditionLeaf
    surf = build_surface(ir)
    assert isinstance(surf.reward_condition, ConditionLeaf)
    assert surf.reward_condition.op == "below"

def test_percentile_single_leaf_defers_to_calibrated_oracle():
    # examples/dyadic uses a coherence signal; use a percentile-over-real-derive
    # from the corpus. alpha_theta has no threshold; instead assert the reason
    # vocabulary via a percentile fixture is exercised by the batch test. Here,
    # assert composite/coherence/no-threshold reasons hold:
    with pytest.raises(UnsupportedProtocol) as e:
        build_surface(_ir("bench/protocols/composite_smr_theta.refrain"))
    assert e.value.reason == "composite-signal reward condition"

def test_coherence_single_leaf_reason():
    with pytest.raises(UnsupportedProtocol) as e:
        build_surface(_ir("examples/dyadic_alpha_coherence_pz.refrain"))
    assert e.value.reason == "non-bandpass (coherence) reward signal"

def test_no_threshold_single_leaf_reason():
    with pytest.raises(UnsupportedProtocol) as e:
        build_surface(_ir("examples/alpha_theta.refrain"))
    assert e.value.reason == "reward condition without a resolvable threshold"
```

Add a dedicated percentile-single-leaf fixture to assert the calibrated-oracle reason (the refrain repo has no percentile single-leaf that isn't otherwise entangled). Create `bench/protocols/micro_single_pct.refrain` (same as `micro_single_above` but `type = percentile(target_pct: 70, window: 30 s)`), and:

```python
def test_percentile_single_leaf_reason():
    with pytest.raises(UnsupportedProtocol) as e:
        build_surface(_ir("bench/protocols/micro_single_pct.refrain"))
    assert e.value.reason == "single percentile-leaf reward (needs calibrated oracle)"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_unsupported.py -q`
Expected: FAIL — the single-leaf cases currently raise `UnsupportedProtocol("single-condition reward")` (Inc 0), not the new specific reasons / not a built surface.

- [ ] **Step 4: Implement the detector**

In `surface.py`, change the `reward_condition` field annotation (~line 101):

```python
    reward_condition: ConditionNode | ConditionLeaf
```

Replace `_reward_condition_from_ir` and add a classifier. It needs the built `derives` and `thresholds` to validate the leaf, so give it those params:

```python
def _reward_condition_from_ir(ir, derives, thresholds) -> ConditionNode | ConditionLeaf:
    event = ir.reward.event
    if isinstance(event, IRCall) and event.callee == "dwell":
        cond = _arg(event, "condition")
        node = _condition_from_ir(cond)
        if isinstance(node, ConditionNode):
            return node
        if isinstance(node, ConditionLeaf):
            return _classify_single_leaf(node, derives, thresholds)
    raise ValueError("surface: reward.event has no all_of/any_of condition")


def _classify_single_leaf(leaf, derives, thresholds):
    derive = next((d for d in derives if d.name == leaf.signal), None)
    thr = next((t for t in thresholds if t.name == leaf.threshold), None)
    if derive is None:
        raise UnsupportedProtocol("composite-signal reward condition")
    if derive.sos is None:
        raise UnsupportedProtocol("non-bandpass (coherence) reward signal")
    if thr is None:
        raise UnsupportedProtocol("reward condition without a resolvable threshold")
    if thr.kind == "percentile":
        raise UnsupportedProtocol("single percentile-leaf reward (needs calibrated oracle)")
    return leaf
```

Update the `build_surface` call site (~line 408) — it already builds `derives` and `thresholds` before this line:

```python
    reward_condition = _reward_condition_from_ir(ir, derives, thresholds)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_unsupported.py -q && .venv/bin/ruff check src/refrain/fuzz/`
Expected: PASS; "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/surface.py bench/protocols/micro_single_above.refrain bench/protocols/micro_single_below.refrain bench/protocols/micro_single_pct.refrain tests/fuzz/test_unsupported.py
git commit -m "feat(fuzz): single-leaf supportability detector (absolute supported; percentile/composite/coherence/no-threshold skip)"
```

---

### Task 3: driven-derive generalization + gate smr_cz-specific extras (above)

**Files:**
- Modify: `src/refrain/fuzz/generate.py` (`_dwell_scenarios`, `_percentile_warmup_scenarios`, `generate_hold_duration_sweep`, `generate_characterization_probe`, `generate_rank_sweep`; add `_driven_leaf`/`_driven_derive`, `_reward_threshold_names`)
- Test: `tests/fuzz/test_runner.py`, `tests/fuzz/test_generate.py`

**Interfaces:**
- Consumes: Task 2 (a supported single-leaf `reward_condition`).
- Produces: `fuzz_protocol` on `micro_single_above.refrain` and `micro_center_bandwidth.refrain` → `FUZZED`, `passed is True`, non-vacuous. `all_of` corpora unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/fuzz/test_runner.py`:

```python
def test_single_above_absolute_fuzzes_clean():
    out = _run("bench/protocols/micro_single_above.refrain", max_scenarios=40)
    assert out.status == FUZZED
    assert out.passed is True

def test_center_bandwidth_single_above_fuzzes_clean():
    out = _run("bench/protocols/micro_center_bandwidth.refrain", max_scenarios=40)
    assert out.status == FUZZED
    assert out.passed is True
```

Add to `tests/fuzz/test_generate.py` a no-op guard (import the generators + `build_surface`):

```python
def test_all_of_corpus_unchanged_driven_derive():
    from refrain.fuzz.runner import _build_corpus
    from refrain.fuzz.surface import build_surface
    from refrain.parser import parse_file
    from refrain.resolver import resolve
    surf = build_surface(resolve(parse_file(REPO_ROOT / "bench/protocols/realistic_smr.refrain"), None))
    labels = sorted(sc.label for sc in _build_corpus(surf))
    # snapshot captured before this task (paste the exact list from the RED run):
    assert labels == EXPECTED_REALISTIC_SMR_LABELS
```

To get `EXPECTED_REALISTIC_SMR_LABELS`: before implementing, run a one-off print of `sorted(sc.label for sc in _build_corpus(surf))` for `realistic_smr` and paste it as the constant. This freezes the all_of corpus so the generalization is a proven no-op.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_runner.py::test_single_above_absolute_fuzzes_clean -q`
Expected: FAIL — `StopIteration` (hardcoded `smr_envelope`) turned into `RuntimeError: generator raised StopIteration`, since `up_env` ≠ `smr_envelope`.

- [ ] **Step 3: Implement the driven-derive + gating**

In `generate.py` add helpers:

```python
def _driven_leaf(surface):
    """The leaf whose derive the 'reward-positive' scenarios drive: the first
    above-leaf, else the first leaf (a sole below)."""
    leaves = list(_all_leaves(surface.reward_condition))
    for leaf in leaves:
        if leaf.op == "above":
            return leaf
    return leaves[0]

def _driven_derive(surface):
    leaf = _driven_leaf(surface)
    return next(d for d in surface.derives if d.name == leaf.signal)

def _reward_threshold_names(surface):
    return {leaf.threshold for leaf in _all_leaves(surface.reward_condition)}

def _reward_has_percentile(surface):
    names = _reward_threshold_names(surface)
    return any(t.kind == "percentile" and t.name in names for t in surface.thresholds)

def _is_single_leaf(surface):
    from .surface import ConditionLeaf
    return isinstance(surface.reward_condition, ConditionLeaf)
```

Replace the three hardcoded `smr_derive = next(d for d in surface.derives if d.name == "smr_envelope")` lines in `_dwell_scenarios`, `_percentile_warmup_scenarios`, and `generate_hold_duration_sweep` with `smr_derive = _driven_derive(surface)`.

Gate `_percentile_warmup_scenarios` — at its top:

```python
def _percentile_warmup_scenarios(surface):
    if not _reward_has_percentile(surface):
        return
    ...  # unchanged body
```

Skip the characterization probe for single-leaf rewards — at the top of `generate_characterization_probe`:

```python
def generate_characterization_probe(surface):
    if _is_single_leaf(surface):
        return
    ...  # unchanged body
```

Scope `generate_rank_sweep` to reward-referenced thresholds — inside its loop, after `if thr.kind != "percentile": continue`, add:

```python
        if thr.name not in _reward_threshold_names(surface):
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_runner.py::test_single_above_absolute_fuzzes_clean tests/fuzz/test_runner.py::test_center_bandwidth_single_above_fuzzes_clean tests/fuzz/test_generate.py -q`
Expected: PASS. Then confirm no regression on an all_of protocol:
Run: `.venv/bin/python -m pytest tests/fuzz/ -q`
Expected: green (~4 min).

- [ ] **Step 5: Run ruff**

Run: `.venv/bin/ruff check src/refrain/fuzz/`
Expected: "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/generate.py tests/fuzz/test_runner.py tests/fuzz/test_generate.py
git commit -m "feat(fuzz): generalize driven derive + gate smr_cz-specific extras (single above fuzzes)"
```

---

### Task 4: below inverted driver

**Files:**
- Modify: `src/refrain/fuzz/generate.py` (`_dwell_scenarios`, `generate_hold_duration_sweep` — op-aware inverted shape)
- Test: `tests/fuzz/test_runner.py`

**Interfaces:**
- Consumes: Task 3 (`_driven_leaf`/`_driven_derive`).
- Produces: `fuzz_protocol` on `micro_single_below.refrain` → `FUZZED`, `passed is True`, non-vacuous.

- [ ] **Step 1: Write the failing test**

Add to `tests/fuzz/test_runner.py`:

```python
def test_single_below_absolute_fuzzes_clean():
    out = _run("bench/protocols/micro_single_below.refrain", max_scenarios=40)
    assert out.status == FUZZED
    assert out.passed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_runner.py::test_single_below_absolute_fuzzes_clean -q`
Expected: FAIL — the dwell/hold scenarios spike the band (making `below` FALSE), so the oracle predicts fire where the engine does not, and/or vacuity. (Reproduce per-scenario with the brainstorm probe if the failure mode is unclear.)

- [ ] **Step 3: Implement the op-aware inverted driver**

The reward-positive state for a sole `below` leaf is the band **low** (quiet). To create a bounded TRUE window of length `hold` the scenario pre-rolls a spike (band high → `below` FALSE, resets the dwell timer) then goes quiet for `hold` (band low → `below` TRUE). Add a helper that yields the segments for the driven leaf held-TRUE for `hold_s` starting at `start_s`:

```python
def _reward_positive_segments(surface, *, start_s, hold_s):
    """BandSegments that hold the driven leaf TRUE for `hold_s` from `start_s`.
    above: a tone spike over [start, start+hold]. below: a PRE-ROLL spike over
    [start-preroll, start] (band high => below FALSE, resets dwell) then quiet
    over [start, start+hold] (band low => below TRUE)."""
    leaf = _driven_leaf(surface)
    d = _driven_derive(surface)
    if leaf.op == "above":
        return (BandSegment(band=d.band, channel=d.channel,
                            start_s=start_s, end_s=start_s + hold_s,
                            content=Tone(amplitude_uv=30.0)),)
    # below: pre-roll spike then quiet hold. Pre-roll >= settle so the FALSE
    # state is established before the quiet TRUE window.
    preroll = 1.5
    return (BandSegment(band=d.band, channel=d.channel,
                        start_s=max(0.0, start_s - preroll), end_s=start_s,
                        content=Tone(amplitude_uv=30.0)),)
```

Rewrite `_dwell_scenarios` to use it (keeping the `fill_s`/`_training_phase` structure and the met/missed hold lengths):

```python
def _dwell_scenarios(surface):
    fs = surface.sample_rate_hz
    fill_s = _longest_percentile_window_s(surface) + _FILL_PAD_S
    dwell_s = surface.dwell_ms / 1000.0
    settle_s = 1.0
    for tag, hold_s in (("dwell:met", max(2.0 * dwell_s + settle_s, 1.0)),
                        ("dwell:missed", max(0.1, dwell_s - 0.1))):
        total = fill_s + hold_s + _TAIL_PAD_S
        yield Scenario(
            label=tag.replace(":", "_"),
            duration_s=total, sample_rate_hz=fs,
            segments=_reward_positive_segments(surface, start_s=fill_s, hold_s=hold_s),
            controls={}, coverage_tags=frozenset({tag}),
            phase_override=_training_phase(total),
        )
```

Rewrite `generate_hold_duration_sweep` similarly, replacing its hardcoded segment with `_reward_positive_segments(surface, start_s=fill_s, hold_s=hold_s)`.

> NOTE: for an `above` leaf `_reward_positive_segments` yields exactly the segment the current code produced (30 µV tone over the hold window) → provable no-op for `all_of` protocols (their driven leaf is `above`). The `test_all_of_corpus_unchanged_driven_derive` guard from Task 3 must still pass; re-run it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_runner.py::test_single_below_absolute_fuzzes_clean tests/fuzz/test_generate.py::test_all_of_corpus_unchanged_driven_derive -q`
Expected: PASS. If below is still vacuous/failing, reproduce per-scenario (predict + count should_fire/dont_care) and adjust `preroll`/`hold` — the arbiter is `passed is True` + no `VacuityError`.

Then full suite: `.venv/bin/python -m pytest tests/fuzz/ -q` → green.

- [ ] **Step 5: Run ruff** — `.venv/bin/ruff check src/refrain/fuzz/` → "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/generate.py tests/fuzz/test_runner.py
git commit -m "feat(fuzz): op-aware inverted driver for sole below leaf (single below fuzzes)"
```

---

### Task 5: batch coverage + integration + corpus re-probe

**Files:**
- Modify: `tests/fuzz/test_batch.py`, `tests/fuzz/test_cli_fuzz.py` (update counts / add skip-reason assertions)
- Create: `docs/superpowers/ci/inc1-corpus-reprobe.md` (record the refrain-protocols unlock)

**Interfaces:** none new — verifies the whole increment end-to-end.

- [ ] **Step 1: Update the batch coverage test**

The Inc 0 `test_batch_aggregates_multiple_paths` asserts `/ total 22` and `rc == 0`. Three new fixtures were added to `bench/protocols/` (`micro_single_above`, `micro_single_below`, `micro_center_bandwidth`) plus `micro_single_pct` — update the total and expected fuzzed count. Write the failing assertion first:

```python
def test_inc1_batch_coverage_reflects_unlock(capsys):
    rc = main(["fuzz", "bench/protocols", "examples", "--library", "examples", "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    # 22 (Inc 0) + 4 new fixtures = 26; fuzzed rises from 4 to 4 + above/below/centerbw = 7
    assert "/ total 26" in out
    assert "coverage: fuzzed 7" in out
    assert "single percentile-leaf reward (needs calibrated oracle)" in out
    assert rc == 0
```

- [ ] **Step 2: Run it to verify it fails, then reconcile the exact counts**

Run: `.venv/bin/python -m pytest tests/fuzz/test_batch.py::test_inc1_batch_coverage_reflects_unlock -q`
Expected: FAIL. Read the actual report from the failure output; set the exact `total` and `fuzzed` numbers (the `othmer_ilf_t3t4` ILF protocol may or may not flip — use the real numbers). Update the existing `test_batch_aggregates_multiple_paths` assertion (`/ total 22` → the new total) to match.

- [ ] **Step 3: Make it pass (no code change — the counts follow from Tasks 1-4)**

If a count is off only because `othmer_ilf_t3t4` unexpectedly flipped/regressed, investigate; otherwise set the assertions to the observed values. Run: `.venv/bin/python -m pytest tests/fuzz/test_batch.py -q` → green.

- [ ] **Step 4: Re-probe the real corpus and record the unlock**

Run the probe (from `/Users/jcroall/git/refrain-protocols`) that counts `fuzzed / skipped / errored` over `protocols drafts --library <RP>`:

```bash
.venv/bin/refrain fuzz /Users/jcroall/git/refrain-protocols/protocols /Users/jcroall/git/refrain-protocols/drafts --library /Users/jcroall/git/refrain-protocols --max-scenarios 2
```

Create `docs/superpowers/ci/inc1-corpus-reprobe.md` recording the before (Inc 0) vs after fuzzed/total and the by-reason skip breakdown, confirming the empirical unlock (expected: the absolute single-leaf protocols not blocked by a third feature now fuzz; percentile single-leaf under the calibrated-oracle reason). This is the roadmap's "re-run the corpus probe before the next increment" gate and states what the next increment (calibrated oracle) should target.

- [ ] **Step 5: Full suite + CI gate**

Run: `.venv/bin/python -m pytest tests/fuzz/ -q` → green.
Run: `.venv/bin/ruff check src/refrain --select F,E9` → clean; `.venv/bin/ruff check src/refrain/fuzz/` → "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add tests/fuzz/test_batch.py tests/fuzz/test_cli_fuzz.py docs/superpowers/ci/inc1-corpus-reprobe.md
git commit -m "test(fuzz): Inc 1 batch coverage + refrain-protocols re-probe (single-cond + center/bandwidth unlock)"
```

---

## Self-review notes

- **Spec coverage:** center/bandwidth reading (T1) ✓; single-leaf detector + skip taxonomy (T2) ✓; driven-derive + extras gating, above (T3) ✓; below inverted driver (T4) ✓; batch coverage + re-probe (T5) ✓; `oracle.py` untouched ✓; absolute-only support / percentile deferred with exact reason (T2) ✓; all_of no-op guard (T3/T4) ✓; 2 (well, 4) new fixtures (T2/T1) ✓.
- **Type consistency:** `_band_from_call(call, ir)` new signature used at its single caller; `reward_condition: ConditionNode | ConditionLeaf`; `_driven_leaf`/`_driven_derive`/`_reward_threshold_names`/`_is_single_leaf` consistent across T3/T4; skip-reason strings copied verbatim from Global Constraints.
- **Known validate-at-build items:** the below `preroll`/`hold` (T4) and the exact batch counts (T5) are validated by their tests, not asserted blind. The `EXPECTED_REALISTIC_SMR_LABELS` snapshot (T3) is captured from the RED run.
- **No-op discipline:** every generator change is structured so an `above` driven leaf reproduces the prior segments; the byte-identical corpus guard (T3) protects the 4 fuzzed protocols across T3 and T4.
