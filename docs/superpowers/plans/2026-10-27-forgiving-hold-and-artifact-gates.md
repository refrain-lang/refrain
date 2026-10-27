# Forgiving Holds, Artifact Gates and Block Re-seeding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four optional declarations to the refrain language — a dip tolerance on the hold primitives, a `kind` label on an inhibit that makes artifact-muted time unobservable rather than failing, block-boundary re-seeding of named controls, and a `scale` multiplier on a seed rule — and lock in with tests that a mute already silences continuous output, shipping as refrain 0.25.0 with every existing protocol's IR byte-identical.

**Architecture:** Each declaration is optional and absent-means-today. The hold state machines in `src/refrain/primitive_impls.py` and `refrain-core/src/dsp.rs` gain a dip counter and a second per-sample input (an observability mask); the evaluator computes an artifact-only sub-mask alongside the existing `muted` gate and feeds it to both hold paths. Re-seeding reuses the existing `PercentileImpl` buffer so the arithmetic stays bit-exact, fires at a block boundary before thresholds step, and records every event in `seed_report()`. A new per-protocol IR version `"0.6"` is returned only when a declaration is present that would make an older engine run the protocol differently.

**Tech Stack:** Python 3.11+ (numpy, lark, jsonschema, pytest), Rust (serde, PyO3 via maturin), JSON Schema draft 2020-12, TypeScript/vitest in the adjacent refrain-editor repo.

**Spec:** `docs/superpowers/specs/2026-10-25-forgiving-hold-and-artifact-gates-design.md`

## Global Constraints

- Target version **0.25.0**. Bump `pyproject.toml:7` and `refrain-core/pyproject.toml:11` together; `refrain-core/Cargo.toml` stays at `0.1.0`. `tests/test_version_lockstep.py` fails the build if the two pyproject versions drift.
- One PR against `main`, one release. refrain CI runs 12–28 minutes a round, so changes batch.
- Every new dual-backend test file must be named `tests/test_eval_*.py`. The drift gate's step 4 (`refrain-core/tools/check_equivalence.py:113`) globs exactly `tests/test_eval_*.py`; a parity test outside that glob is never run under `REFRAIN_EVAL_BACKEND=rust`.
- Capture `pytest`'s exit code immediately, never after a pipe. No `-q` on refrain's suite (the drift gate's own internal `-q` is the gate's business, not ours).
- All four declarations are optional. Absence must reproduce today's behaviour **exactly** and emit **byte-identical** IR JSON.
- IR version `"0.6"` is returned if and only if one of these is present: a hold whose `tolerance` bakes to **one or more samples**; an inhibit with `kind = "artifact"`; a block with a non-empty `reseed`; a seed rule with `scale != 1.0`. An inhibit with `kind = "trained"`, and an unlabelled one, keep the protocol's current version.
- `tolerance` default is absent, meaning 0 samples. `scale` default is 1.0.
- `tolerance` is a fixed duration literal, not control-bindable — it follows `dwell_rearm`'s `rearm` (`src/refrain/resolver.py:3094-3099`), not `dwell`'s `duration`.
- `kind` is legal on an inhibit whose action is `mute` or `freeze`; refused at resolve time on `flag`.
- Hold semantics are **forgive, don't credit**: a streak of N samples always means N samples genuinely above threshold and observable. A forgiven dip leaves the streak exactly where it was.
- Python and Rust must be bit-identical. `legacy/` is frozen. Never widen a band.

## Review Focus

Five conditions the design implies but does not pin, each most likely to bite a real session. Each line's test is added to the task that owns the code.

1. **A dip and an artifact mute overlapping.** If the dip counter advances on samples the engine could not observe, an artifact mute longer than `tolerance` resets the streak through the dip path and the `kind` label buys nothing. Unobservable must win over false: the dip counter is frozen while the observability mask is false. *(Task 3, Task 6.)*
2. **`tolerance` greater than or equal to `duration`.** Once a hold is satisfied, a condition true for one sample in every `tolerance` keeps `holds` true forever, so holds-gated audio never drops. A protocol author would expect that to be refused, not rewarded. Refuse `tolerance >= duration` at resolve time. *(Task 3.)*
3. **A `tolerance` that bakes to zero samples.** `tolerance: 1 ms` at 256 Hz rounds to 0 samples, so it changes no behaviour — but a syntax-based version predicate would still push the document to IR 0.6 and get it refused by a 0.24 engine for nothing. The predicate reads the baked sample count. Unlike `dwell_samples`, `tolerance_samples` must **not** be floored at 1. *(Task 4.)*
4. **A non-positive `scale` on a seed rule.** `scale = 0` makes a seeded muscle limit 0 µV, so the inhibit mutes the whole session; a negative scale clamps to the control's range floor and reads as a working protocol. Refuse `scale <= 0` at resolve time. *(Task 8.)*
5. **`reseed` naming a control whose seed was dropped as dead.** `_resolve_control_seeds` (`src/refrain/resolver.py:1618-1619`) silently declines to attach a seed to a control nothing references, so `reseed = ["x"]` would compile and then do nothing at run time. Refuse it, with a message that says the seed was folded out. *(Task 9.)*

---

## File Structure

**New files**

| File | Responsibility |
|---|---|
| `tests/test_ir_corpus_identity.py` | Compiles every in-repo protocol and compares against a committed hash snapshot. The safety net for "no protocol's behaviour moved". |
| `tests/fixtures/ir_corpus_identity.json` | The committed snapshot: relative path → sha256 of canonical IR JSON, plus the list of protocols that do not resolve standalone. |
| `src/refrain/schema/ir-json-v0.6.schema.json` | IR-JSON v0.6 wire schema. Copy of v0.5 plus four fields. |
| `tests/test_eval_mute_gating.py` | Item 5: a mute zeroes continuous output and falsifies event channels, both backends. |
| `tests/test_eval_tolerance.py` | Dual-backend tolerance behaviour, including chunk-boundary dips. |
| `tests/test_eval_inhibit_kind.py` | Dual-backend `kind` behaviour on the `output`-bound hold path. |
| `tests/test_eval_artifact_mask_reward.py` | Dual-backend `kind` behaviour on the `reward { event = ... }` path. |
| `tests/test_eval_seed_scale.py` | Dual-backend seeded value with `scale`, plus the warm-up fail-closed pin. |
| `tests/test_resolve_reseed.py` | Resolve-time `reseed` parsing, validation and the downstream-hold walk. |
| `tests/test_eval_reseed.py` | Dual-backend re-seed firing, keep-old-value, hold reset and report history. |
| `tests/fuzz/test_oracle_tolerance.py` | Oracle dip-forgiveness and the four §1 invariants as metamorphic relations. |
| `bench/protocols/micro_14_tolerance.refrain` | Golden-vector fixture: one dwell with `tolerance`. |
| `bench/protocols/micro_15_artifact_gate.refrain` | Golden-vector fixture: an `kind = "artifact"` inhibit plus a hold on both paths. |
| `bench/protocols/seed_scale_emg.refrain` | The worked per-person muscle limit from design §4. |
| `bench/protocols/reseed_staged.refrain` | Golden-vector fixture: two blocks, the second re-seeding. |
| `refrain-core/tests/tolerance.rs` | Rust golden-vector suite for `micro_14_tolerance`. |
| `refrain-core/tests/artifact_gate.rs` | Rust golden-vector suite for `micro_15_artifact_gate`. |
| `refrain-core/tests/reseed.rs` | Rust golden-vector suite for `reseed_staged` and `seed_scale_emg`. |

**Modified files**

| File | Change |
|---|---|
| `src/refrain/primitives.py:715-762` | `tolerance` ParamSpec on `_DWELL`, `_DWELL_REARM`, `_HOLD_FOR`. |
| `src/refrain/primitive_impls.py:951-1053` | Dip counter and observability mask in `DwellImpl`, `DwellRearmImpl`, `HoldForImpl`. |
| `src/refrain/resolver.py` | `tolerance` validation; `kind` on an inhibit; `reseed` on a block; `scale` on a seed; the downstream-hold walk. |
| `src/refrain/ir.py:209-217, 265-274, 375-390` | `IRInhibit.kind`, `IRControlSeed.scale`, `IRBlock.reseed` + `IRBlock.reseed_holds`. |
| `src/refrain/ir_json.py:151-190, 240-266, 435-443, 470-476, 560-566` | Version predicate clauses, `tolerance_samples` baking, conditional new keys. |
| `src/refrain/eval_.py` | Artifact sub-mask; mask into both hold paths; `_compute_muted` moved above the reward stage; re-seed ingestion and firing; `seed_report()` history. |
| `src/refrain/fuzz/surface.py:103-115, 432-440` | `dwell_tolerance_ms` on `LogicalSurface`. |
| `src/refrain/fuzz/oracle.py:188-280` | `tolerance_samples` in `apply_dwell`. |
| `src/refrain/editor/{describe,render}.py`, `protocol-model.schema.json` | The four declarations through the editor model. |
| `refrain-core/src/dsp.rs:789-885` | Dip counter and observability mask in `Dwell` and `DwellRearm`. |
| `refrain-core/src/ir.rs:87-109, 137-142, 248-258, 274-282, 348-380` | `SUPPORTED_IR_VERSIONS`, `ControlSeed.scale`, `Block.reseed`/`reseed_holds`, `Inhibit.kind`, `Coeffs.tolerance_samples`. |
| `refrain-core/src/eval.rs` | Artifact sub-mask into env; mask into the reward stage; re-seed machinery; `SeedReportEntry.history`. |
| `refrain-core/tools/gen_fixtures.py:370-400` | The four new fixture stems. |
| `tests/test_ir_json_schema.py:28-34` | `"0.6"` in `SCHEMA_BY_VERSION`. |
| `docs/SPEC.md`, `docs/IR-JSON.md`, `docs/PRIMITIVES.md`, `docs/EMBEDDING.md`, `docs/CONFORMANCE.md` | The four declarations and the `seed_report()` history shape. |

---

### Task 1: The corpus IR identity guard

The safety net goal 5 depends on. Written and green **before** any engine change, so every later task's commit proves the corpus did not move.

**Files:**
- Create: `tests/test_ir_corpus_identity.py`
- Create: `tests/fixtures/ir_corpus_identity.json`

**Interfaces:**
- Produces: `tests/test_ir_corpus_identity.py::snapshot()` — a module-level helper returning `tuple[dict[str, str], list[str]]`: path → sha256 hex of `json.dumps(ir_to_json_obj(ir, sample_rate_hz=256), sort_keys=True)`, and the sorted list of protocol paths that raise during resolve. Later tasks call nothing from here; they only keep it green.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ir_corpus_identity.py
CORPUS = sorted(REPO.glob("examples/**/*.refrain")) + sorted((REPO / "bench" / "protocols").glob("*.refrain"))

def test_corpus_is_the_expected_size():
    # 9 examples + 1 othmer library parent + 25 bench protocols as of 0.24.0.
    assert len(CORPUS) == 35

def test_every_protocol_ir_is_byte_identical_to_the_snapshot():
    hashes, unresolvable = snapshot()
    expected = json.loads(SNAPSHOT.read_text())
    assert hashes == expected["hashes"]

def test_the_set_of_unresolvable_protocols_has_not_grown():
    _, unresolvable = snapshot()
    expected = json.loads(SNAPSHOT.read_text())
    # examples/othmer_ilf_cz_pz.refrain extends a library parent and needs an
    # import resolver, so it does not resolve standalone.
    assert unresolvable == expected["unresolvable"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_ir_corpus_identity.py -v`
Expected: FAIL — `tests/fixtures/ir_corpus_identity.json` does not exist.

- [ ] **Step 3: Generate the snapshot from the current tree**

Write `snapshot()` in the test module, then generate `tests/fixtures/ir_corpus_identity.json` by calling it once and dumping `{"hashes": ..., "unresolvable": ...}` with `indent=2`. Suppress `ResolveWarning` while compiling — five protocols warn about bare `midpoint`/`low`/`high` numbers and the warnings are not the subject. Measured on `main` at `bef24b0`: 34 resolve (26 at IR 0.1, 2 at 0.2, 2 at 0.3, 3 at 0.4, 1 at 0.5), 1 does not.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_ir_corpus_identity.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ir_corpus_identity.py tests/fixtures/ir_corpus_identity.json
git commit -m "test: pin every in-repo protocol's IR byte-for-byte"
```

---

### Task 2: A mute silences the continuous output (design item 5)

Verification only. No engine change. `src/refrain/eval_.py:1313` already does `gated = np.where(muted, 0.0, clamped)` and `:1308` does `values.astype(bool) & ~muted`; `refrain-core/src/eval.rs:1756-1766` is the twin. What is missing is a test that says so.

**Files:**
- Create: `tests/test_eval_mute_gating.py`
- Modify: `docs/SPEC.md` §7.4 (inhibit semantics)

**Interfaces:**
- Consumes: the `backend` fixture from `tests/conftest.py:45`.
- Produces: nothing other code reads.

- [ ] **Step 1: Write the failing tests**

Build a protocol with one continuous output channel, one event channel and one `mute`-action inhibit whose metric is driven over threshold by the injected signal. Use `Evaluator.live(ir, sample_rate_hz=256, channel_names=..., backend=backend)`.

```python
def test_continuous_output_reads_zero_while_muted(backend):
    taps_by_chunk = _run(backend, signal_with_burst_of_emg_at=(2.0, 3.0))
    muted = [t["muted"] for t in taps_by_chunk]
    assert any(muted)
    for t in taps_by_chunk:
        if t["muted"]:
            assert t["output/audio_gain"] == 0.0

def test_event_output_reads_false_while_muted(backend):
    for t in _run(backend, signal_with_burst_of_emg_at=(2.0, 3.0)):
        if t["muted"]:
            assert t["output/audio_chime"] == 0.0

def test_continuous_output_resumes_after_the_mute_clears(backend):
    taps = _run(backend, signal_with_burst_of_emg_at=(2.0, 3.0))
    after = [t for t in taps if not t["muted"]][-5:]
    assert any(t["output/audio_gain"] > 0.0 for t in after)

def test_suppress_on_mute_on_a_feedback_layer_is_not_an_engine_behaviour(backend):
    """`suppress_on_mute` reaches the IR JSON and the Rust IR struct and is
    acted on by NEITHER engine — it is a declaration for the downstream
    player. Pinned so the next reader does not mistake it for a bug."""
    with_flag = _run(backend, suppress_on_mute=True)
    without = _run(backend, suppress_on_mute=False)
    assert [t["output/audio_gain"] for t in with_flag] == [t["output/audio_gain"] for t in without]
```

- [ ] **Step 2: Run the tests on both backends**

Run: `python -m pytest tests/test_eval_mute_gating.py -v; echo "exit=$?"`
Run: `REFRAIN_EVAL_BACKEND=rust python -m pytest tests/test_eval_mute_gating.py -v; echo "exit=$?"`
Expected: PASS on both. These tests document existing behaviour, so a failure here means the design's reading of `eval_.py:1308-1313` is wrong — stop and re-read rather than changing the engine.

- [ ] **Step 3: Document the `suppress_on_mute` asymmetry**

Add a paragraph to `docs/SPEC.md` §7.4 stating that a `mute` or `freeze` inhibit zeroes every continuous `output/<channel>` and falsifies every event channel in both engines, and that `feedback { ... suppress_on_mute }` is a player-facing declaration that neither engine acts on.

- [ ] **Step 4: Commit**

```bash
git add tests/test_eval_mute_gating.py docs/SPEC.md
git commit -m "test: lock mute output gating on both backends"
```

---

### Task 3: `tolerance` in the Python hold primitives

The state machine, in Python only, with no IR or Rust yet. The hard part of the release gets its own gate.

**Files:**
- Modify: `src/refrain/primitives.py:715-762`
- Modify: `src/refrain/primitive_impls.py:951-1053`
- Modify: `src/refrain/resolver.py` (`_resolve_call` per-primitive checks, near the `rearm` fixed-duration check at `:3094-3099`)
- Modify: `tests/test_primitive_impls.py`
- Modify: `tests/test_resolver.py`
- Modify: `docs/PRIMITIVES.md`, `docs/SPEC.md` §5.6

**Interfaces:**
- Produces:
  - `DwellImpl(*, duration_ms: float, sample_rate_hz: float, tolerance_ms: float = 0.0)`, with public attributes `dwell_samples: int` and `tolerance_samples: int`, and `step(self, condition: np.ndarray, observable: np.ndarray | None = None) -> DwellResult`.
  - `DwellRearmImpl(*, duration_ms, rearm_ms, sample_rate_hz, tolerance_ms=0.0)`, same `step` signature.
  - `HoldForImpl(*, duration_ms, sample_rate_hz, tolerance_ms=0.0)`, with `step(self, condition, observable=None) -> np.ndarray`.
  - `tolerance_samples = max(0, int(round(tolerance_ms / 1000.0 * sample_rate_hz)))` — **not** floored at 1, unlike `dwell_samples`.
  - `observable=None` means every sample is observable, which is today's behaviour. Task 6 passes a real mask.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_primitive_impls.py`. `_c(*runs)` builds a boolean array from alternating true/false run lengths.

```python
def test_tolerance_absent_is_identical_to_today():
    cond = _c(10, 3, 10)  # 10 true, 3 false, 10 true
    a = DwellImpl(duration_ms=50, sample_rate_hz=200).step(cond)
    b = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=0.0).step(cond)
    assert np.array_equal(a.events, b.events) and np.array_equal(a.holds, b.holds)

def test_a_dip_at_exactly_tolerance_is_forgiven():
    # 200 Hz: tolerance 15 ms -> 3 samples. A 3-sample dip is forgiven.
    impl = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)  # dwell 10
    r = impl.step(_c(5, 3, 5))
    assert r.events.sum() == 1 and int(np.flatnonzero(r.events)[0]) == 12

def test_a_dip_one_sample_longer_than_tolerance_resets():
    impl = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    r = impl.step(_c(5, 4, 5))
    assert r.events.sum() == 0

def test_a_forgiven_dip_does_not_advance_the_streak():
    """Forgive, don't credit: 10 genuinely-true samples are still required."""
    impl = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    r = impl.step(_c(9, 3, 1))
    assert r.events.sum() == 1 and int(np.flatnonzero(r.events)[0]) == 12

def test_a_forgiven_dip_keeps_holds_true():
    impl = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    r = impl.step(_c(10, 3, 2))
    assert r.holds.all()

def test_an_unobservable_sample_freezes_the_streak_and_the_dip_counter():
    """Review Focus 1. A 10-sample unobservable window that is ALSO condition-
    false must not reset the streak, even though 10 > tolerance_samples."""
    impl = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    cond = _c(9, 10, 1)
    observable = ~_c(9, 10, 1, start=False)  # false exactly over the 10-sample run
    r = impl.step(cond, observable)
    assert r.events.sum() == 1 and int(np.flatnonzero(r.events)[0]) == 19

def test_a_dip_is_carried_across_a_step_boundary():
    impl = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    first = impl.step(_c(5, 2))          # dip in progress, 2 of 3 used
    second = impl.step(_c(0, 1, 5, start=False))  # 1 more false, then true
    assert first.events.sum() == 0 and second.events.sum() == 1

def test_reset_clears_the_dip_counter():
    impl = DwellImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    impl.step(_c(5, 2))
    impl.reset()
    assert impl.step(_c(0, 2, 10, start=False)).events.sum() == 1

def test_dwell_rearm_gives_one_event_per_burst_across_a_forgiven_dip():
    impl = DwellRearmImpl(duration_ms=50, rearm_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    assert impl.step(_c(10, 3, 10)).events.sum() == 1

def test_dwell_rearm_credits_the_whole_dip_when_tolerance_is_exceeded():
    """A dip past tolerance credits the re-arm counter from the dip's FIRST
    sample, so re-arming is never slower than it is today."""
    tol = DwellRearmImpl(duration_ms=50, rearm_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    plain = DwellRearmImpl(duration_ms=50, rearm_ms=50, sample_rate_hz=200)
    cond = _c(10, 12, 10)  # dip of 12 > tolerance 3, and > rearm 10
    assert tol.step(cond).events.sum() == plain.step(cond).events.sum() == 2

def test_hold_for_forgives_a_dip():
    impl = HoldForImpl(duration_ms=50, sample_rate_hz=200, tolerance_ms=15.0)
    assert impl.step(_c(10, 3, 2)).all()
```

Append to `tests/test_resolver.py`:

```python
def test_tolerance_may_not_bind_a_control():
    with pytest.raises(ResolveError, match="tolerance must be a fixed duration"):
        resolve(parse(_dwell_protocol(tolerance="tol_ms")))

def test_tolerance_at_or_above_duration_is_refused():
    """Review Focus 2: once satisfied, such a hold never drops."""
    with pytest.raises(ResolveError, match="tolerance .* must be shorter than"):
        resolve(parse(_dwell_protocol(duration="250 ms", tolerance="250 ms")))

def test_tolerance_must_be_a_duration_literal():
    with pytest.raises(ResolveError, match="tolerance"):
        resolve(parse(_dwell_protocol(tolerance="100")))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_primitive_impls.py tests/test_resolver.py -v -k "tolerance or unobservable or dip"; echo "exit=$?"`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'tolerance_ms'`.

- [ ] **Step 3: Add the `tolerance` parameter to the three primitive specs**

In `src/refrain/primitives.py`, append `ParamSpec("tolerance", "duration", default=None, required=False)` to the single signature of `_DWELL` (`:715`), `_DWELL_REARM` (`:731`) and `_HOLD_FOR` (`:748`), and extend each `doc` with one clause naming the dip-forgiveness.

- [ ] **Step 4: Implement the dip counter and the observability mask**

In `src/refrain/primitive_impls.py`, rewrite `DwellImpl.step` and `DwellRearmImpl.step` over this per-sample rule, which needs no lookahead and so transcribes identically into Rust:

```
for i in range(n):
    if observable is not None and not observable[i]:
        pass                                  # streak, dip and out_streak all frozen
    elif condition[i]:
        streak += 1; dip = 0; out_streak = 0  # out_streak only in DwellRearmImpl
    else:
        dip += 1
        if dip > tolerance_samples:
            streak = 0
            # DwellRearmImpl only: credit the whole dip the first time tolerance
            # is exceeded, then one sample per false sample thereafter.
            out_streak += dip if dip == tolerance_samples + 1 else 1
            if out_streak >= rearm_samples:
                armed = True
    is_holding = streak >= dwell_samples
    holds[i] = is_holding
    if is_holding and not was_holding:        # DwellImpl / HoldForImpl
        events[i] = True
    was_holding = is_holding
```

`DwellRearmImpl` keeps its own rising-edge rule (`if is_holding and armed`). `reset()` on all three clears `_dip` alongside the existing state. `HoldForImpl.step(condition, observable=None)` forwards both arguments to `super().step(...)` and returns `.holds`.

- [ ] **Step 5: Add the two resolve-time refusals**

In `src/refrain/resolver.py`, beside the existing `rearm` fixed-duration check at `:3094-3099`, refuse a `tolerance` argument that is not an `A.NumberLit` with a time unit, and refuse `tolerance_ms >= duration_ms` when `duration` is itself a literal. When `duration` binds a control, the comparison is not knowable at resolve time — skip it rather than guessing, and say so in a comment.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_primitive_impls.py tests/test_resolver.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Expected: PASS. The corpus identity test must still pass — no existing protocol declares `tolerance`, so no IR moves.

- [ ] **Step 7: Document the primitive surface**

`docs/PRIMITIVES.md`: the `tolerance` argument on all three primitives, with the forgive-don't-credit rule and the four invariants from design §1. `docs/SPEC.md` §5.6: the same, as normative text.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/primitives.py src/refrain/primitive_impls.py src/refrain/resolver.py \
        tests/test_primitive_impls.py tests/test_resolver.py docs/PRIMITIVES.md docs/SPEC.md
git commit -m "feat: dip tolerance on dwell, dwell_rearm and hold_for"
```

---

### Task 4: `tolerance` end to end — IR 0.6, the v0.6 schema, and the Rust twin

**Files:**
- Create: `src/refrain/schema/ir-json-v0.6.schema.json`
- Create: `bench/protocols/micro_14_tolerance.refrain`
- Create: `tests/test_eval_tolerance.py`
- Create: `refrain-core/tests/tolerance.rs`
- Modify: `src/refrain/ir_json.py:151-190` (version predicate), `:240-266` (coeff baking)
- Modify: `refrain-core/src/dsp.rs:789-885`, `refrain-core/src/ir.rs:87-92, 348-380`, `refrain-core/src/eval.rs:430-450`
- Modify: `refrain-core/tools/gen_fixtures.py:370-387`
- Modify: `tests/test_ir_json.py`, `tests/test_ir_json_schema.py:28-34`
- Modify: `docs/IR-JSON.md`

**Interfaces:**
- Consumes: `DwellImpl.tolerance_samples` and the `step(condition, observable)` signature from Task 3.
- Produces:
  - `Coeffs.tolerance_samples: Option<usize>` in `refrain-core/src/ir.rs`, emitted by `_bake_coeffs` as `"tolerance_samples"` **only when nonzero**.
  - `Dwell::new(dwell_samples: usize, tolerance_samples: usize)` and `DwellRearm::new(dwell_samples: usize, rearm_samples: usize, tolerance_samples: usize)` in `refrain-core/src/dsp.rs`.
  - `Dwell::step(&mut self, cond: &[bool], observable: Option<&[bool]>) -> (Vec<bool>, Vec<bool>)` and `DwellRearm::step(&mut self, cond: &[bool], observable: Option<&[bool]>) -> Vec<bool>`. Task 6 passes `Some(mask)`.
  - `SUPPORTED_IR_VERSIONS` gains `"0.6"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_ir_json.py`:

```python
def test_a_hold_with_tolerance_emits_ir_0_6():
    j = ir_to_json_obj(resolve(parse(_dwell_protocol(tolerance="100 ms"))), sample_rate_hz=256)
    assert j["refrain_ir_version"] == "0.6"

def test_tolerance_bakes_to_samples():
    j = ir_to_json_obj(resolve(parse(_dwell_protocol(tolerance="100 ms"))), sample_rate_hz=256)
    assert _dwell_coeffs(j)["tolerance_samples"] == 26  # round(0.100 * 256)

def test_a_tolerance_that_bakes_to_zero_samples_does_not_bump_the_version():
    """Review Focus 3: tolerance: 1 ms at 256 Hz rounds to 0 samples, so it
    changes nothing and must not be refused by a 0.24 engine."""
    j = ir_to_json_obj(resolve(parse(_dwell_protocol(tolerance="1 ms"))), sample_rate_hz=256)
    assert j["refrain_ir_version"] == "0.1"
    assert "tolerance_samples" not in _dwell_coeffs(j)

def test_a_protocol_without_tolerance_emits_no_tolerance_key():
    j = ir_to_json_obj(resolve(parse(_dwell_protocol())), sample_rate_hz=256)
    assert "tolerance_samples" not in _dwell_coeffs(j)
```

`tests/test_ir_json_schema.py`: add `"0.6": SCHEMA_DIR / "ir-json-v0.6.schema.json"` to `SCHEMA_BY_VERSION` and a test that the corpus covers v0.6 once `micro_14_tolerance` is generated.

`tests/test_eval_tolerance.py` — the dual-backend cases design §"Dual-backend parity" names as the minimum:

```python
def test_a_dip_at_exactly_tolerance_rewards_on_both_backends(backend): ...
def test_a_dip_one_sample_past_tolerance_rewards_on_neither_backend(backend): ...
def test_a_forgiven_dip_spanning_a_chunk_boundary_rewards(backend):
    """The dip counter is the new carried state; chunk edges are where carried
    state drifts. Chunk size chosen so the dip straddles two chunks."""
def test_holds_gated_continuous_output_keeps_sounding_through_a_forgiven_dip(backend):
    """Gate item 9. The pattern alpha_theta calls out by name:
    output { audio_gain = reward.event.holds ? reward.continuous : 0 }"""
def test_dwell_rearm_emits_one_event_per_burst_across_a_forgiven_dip(backend): ...
def test_tolerance_zero_is_sample_identical_to_no_tolerance(backend):
    """Invariant 3 of design §1, at the engine level."""
```

`refrain-core/tests/tolerance.rs`: the standard golden-vector shape used by `refrain-core/tests/events.rs` — load `micro_14_tolerance.{ir,io,events}.json`, run the core, compare streams to `1e-9` and the event list exactly.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_ir_json.py tests/test_eval_tolerance.py -v; echo "exit=$?"`
Expected: FAIL — `refrain_ir_version` is `"0.1"`, no `tolerance_samples` key, missing schema file.

- [ ] **Step 3: Bake `tolerance_samples` and extend the version predicate**

In `src/refrain/ir_json.py`, add to `_emit_coeffs` (beside the `dwell_samples` line at `:263`):

```python
if getattr(impl, "tolerance_samples", 0):
    c["tolerance_samples"] = int(impl.tolerance_samples)
```

The `getattr(..., 0)` guard is what keeps every existing protocol's IR byte-identical. In `_protocol_ir_version`, add a `"0.6"` clause above the `"0.5"` one, returning `"0.6"` when any call in `_all_stream_exprs(ir)` is a `dwell`/`dwell_rearm`/`hold_for` whose **baked** `tolerance_samples` is at least 1. Reuse `_bake_coeffs`'s sample-rate handling rather than re-deriving the rounding; a protocol emitted without a sample rate cannot bake, and such a document carries no `tolerance_samples` either, so it keeps its current version.

- [ ] **Step 4: Write the v0.6 schema**

Copy `src/refrain/schema/ir-json-v0.5.schema.json` to `ir-json-v0.6.schema.json`, change `$id`, `title`, `description` ("v0.6: adds hold dip tolerance, inhibit kind, block re-seeding and seed scale") and the `version` const to `"0.6"`, and add `"tolerance_samples": {"type": "integer", "minimum": 1}` to `$defs/Coeffs/properties`. Leave v0.1–v0.5 untouched. The other three fields land in this file in Tasks 6, 8 and 9.

- [ ] **Step 5: Implement the Rust twin**

- `refrain-core/src/dsp.rs`: add `tolerance_samples: usize` and `dip: usize` to `Dwell` and `DwellRearm`; change both `new` signatures and both `step` signatures as above; transcribe the Task 3 per-sample rule exactly. `reset()` clears `dip`.
- `refrain-core/src/ir.rs`: `Coeffs.tolerance_samples: Option<usize>` with `#[serde(default)]`; `"0.6"` appended to `SUPPORTED_IR_VERSIONS`.
- `refrain-core/src/eval.rs`: the three `CNode` arms at `:430-450` pass `None` as `observable` for now and read `tolerance_samples.unwrap_or(0)` at build time (`RewardEvent` construction at `:2762` and the `HoldFor`/`DwellEvent`/`DwellRearmEvent` builders).

Add unit tests in `refrain-core/src/dsp.rs`'s `mod tests` mirroring the Task 3 Python cases one for one — the dip at exactly tolerance, one sample past, the frozen dip counter under an unobservable sample, and the cross-chunk dip.

- [ ] **Step 6: Add the fixture protocol and generate golden vectors**

`bench/protocols/micro_14_tolerance.refrain`: one `referential` input, one `band_envelope` derive, one `absolute` threshold, `reward { event = dwell(condition: above(...), duration: 250 ms, tolerance: 100 ms) }`, one event output channel and one `holds`-gated continuous channel. Add `"micro_14_tolerance"` to the stem loop in `gen_fixtures.py:370-387` and to `EVENT_BEARING` (`:179`), then regenerate:

Run: `PYTHONPATH="$PWD" python refrain-core/tools/gen_fixtures.py`

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_ir_json.py tests/test_ir_json_schema.py tests/test_eval_tolerance.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Run: `cd refrain-core && cargo test; echo "exit=$?"`
Run: `python refrain-core/tools/check_equivalence.py; echo "exit=$?"`
Expected: PASS on all three. The corpus identity test is the one that proves no existing protocol's IR moved.

- [ ] **Step 8: Document the wire format**

`docs/IR-JSON.md`: the v0.6 version tag, `tolerance_samples` in `Coeffs`, and the behaviour-based predicate (a tolerance that bakes to 0 samples does not bump the version).

- [ ] **Step 9: Commit**

```bash
git add src/refrain/ir_json.py src/refrain/schema/ir-json-v0.6.schema.json \
        refrain-core/src/dsp.rs refrain-core/src/ir.rs refrain-core/src/eval.rs \
        refrain-core/tests/tolerance.rs refrain-core/tools/gen_fixtures.py \
        refrain-core/tests/fixtures/micro_14_tolerance.* bench/protocols/micro_14_tolerance.refrain \
        tests/test_eval_tolerance.py tests/test_ir_json.py tests/test_ir_json_schema.py docs/IR-JSON.md
git commit -m "feat: IR 0.6 and the Rust twin for hold dip tolerance"
```

---

### Task 5: Fuzzer scenarios for the forgiving hold

Gate item 3. Without this, a protocol carrying `tolerance` either produces false fuzzer failures or is silently declared unsupported.

**Files:**
- Modify: `src/refrain/fuzz/surface.py:103-115, 432-440`
- Modify: `src/refrain/fuzz/oracle.py:188-280, 311-322`
- Create: `tests/fuzz/test_oracle_tolerance.py`
- Modify: `tests/fuzz/test_oracle_dsp.py` (regression: a zero tolerance changes no prediction)
- Modify: `docs/PROTOCOL-FUZZER.md`

**Interfaces:**
- Consumes: `LogicalSurface` from `src/refrain/fuzz/surface.py:103`.
- Produces:
  - `LogicalSurface.dwell_tolerance_ms: float = 0.0`, read by `_dwell_tolerance_ms_from_ir(ir) -> float` beside `_dwell_ms_from_ir` at `:432`.
  - `apply_dwell(..., tolerance_samples: int = 0, ...)` — a new keyword-only parameter on the existing signature at `oracle.py:188`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/fuzz/test_oracle_tolerance.py
def test_zero_tolerance_predicts_exactly_what_it_predicted_before():
    truth = [...]
    assert apply_dwell(truth, dwell_samples=10, fs=200, collar_s=0.0,
                       muted_mask=[False]*len(truth)).fire_samples == \
           apply_dwell(truth, dwell_samples=10, fs=200, collar_s=0.0,
                       muted_mask=[False]*len(truth), tolerance_samples=0).fire_samples

def test_a_sub_tolerance_false_run_is_forgiven_in_the_prediction(): ...
def test_a_none_sample_inside_a_dip_stays_dont_care_rather_than_forgiven():
    """A 3-valued None is "the oracle cannot tell", which is not the same as
    "the signal dipped". Forgiveness applies to robust-False runs only."""

# The four invariants of design §1, as metamorphic relations over a sweep.
def test_monotone_in_tolerance():
    """Raising tolerance moves a reward earlier or leaves it; never later,
    never away."""
    times = [_first_fire(tolerance_ms=t) for t in (0, 25, 50, 100, 150)]
    assert all(b is not None and b <= a for a, b in zip(times, times[1:]))

def test_no_free_rewards():
    """At least `duration` samples genuinely above threshold behind any fire."""

def test_zero_is_identity_end_to_end():
    """Same protocol with `tolerance: 0 ms` and with no tolerance at all:
    sample-identical engine output."""

def test_idempotent_under_dip_filling():
    """A condition stream whose sub-tolerance false runs are filled in by hand
    produces the same reward times as the original does with that tolerance."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/fuzz/test_oracle_tolerance.py -v; echo "exit=$?"`
Expected: FAIL — `apply_dwell() got an unexpected keyword argument 'tolerance_samples'`.

- [ ] **Step 3: Carry the tolerance into the surface**

`surface.py`: add the field, read it from the reward event's `tolerance` argument (absent → 0.0), and leave every other surface shape alone.

- [ ] **Step 4: Teach the oracle dip forgiveness**

`oracle.py`: in `apply_dwell`'s streak loop (`:218-231`), apply the same per-sample rule as Task 3, with one difference the test above pins — forgiveness applies only where `truth_per_sample[i] is False`, never where it is `None`. A `None` sample is already covered by the settle collar, so treating it as a forgivable dip would turn an honest don't-care into an assertion. Pass `tolerance_samples` from `predict(...)` at `:311-322`, baked with the same `int(round(ms / 1000.0 * fs))` the evaluator uses.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/fuzz/ -v; echo "exit=$?"`
Expected: PASS. The whole fuzz suite, because `apply_dwell` is shared.

- [ ] **Step 6: Document and commit**

`docs/PROTOCOL-FUZZER.md`: the tolerance surface field and the four invariants as the specification the scenarios check.

```bash
git add src/refrain/fuzz/surface.py src/refrain/fuzz/oracle.py tests/fuzz/ docs/PROTOCOL-FUZZER.md
git commit -m "feat: fuzzer oracle models hold dip tolerance"
```

---

### Task 6: `kind` on an inhibit, and the observability mask on the `output`-bound hold path

The three protocols that bind holds inside `output { }` — `alpha_theta_narrowband`, `alpha_theta_narrowband_melodic`, `smr_classic_cz`. The mask already exists here; the change is to pass two masks instead of one.

**Files:**
- Create: `bench/protocols/micro_15_artifact_gate.refrain`
- Create: `tests/test_eval_inhibit_kind.py`
- Create: `refrain-core/tests/artifact_gate.rs`
- Modify: `src/refrain/ir.py:209-217`, `src/refrain/resolver.py:1108-1147`, `src/refrain/ir_json.py:151-190, 435-443`, `src/refrain/eval_.py:1276-1290, 1813-1814, 1902-1928`
- Modify: `src/refrain/ir_print.py:411` (`_artifact_rejection`)
- Modify: `refrain-core/src/ir.rs:274-282`, `refrain-core/src/eval.rs:430-450, 1541-1586, 1716-1731, 2588-2598`
- Modify: `src/refrain/schema/ir-json-v0.6.schema.json`
- Modify: `tests/test_resolver.py`, `tests/test_ir_json.py`, `tests/test_ir_print.py`
- Modify: `docs/SPEC.md` §4.6, §7.4

**Interfaces:**
- Consumes: `DwellImpl.step(condition, observable)` from Task 3; `Dwell::step(cond, observable)` from Task 4.
- Produces:
  - `IRInhibit.kind: str | None = None` — `"artifact"`, `"trained"` or `None`. Appended last so positional construction elsewhere is unaffected.
  - `Evaluator._compute_artifact_muted(inhibit_active: dict[str, np.ndarray], chunk_size: int, active_block=None) -> np.ndarray` — the same combination rule as `_compute_muted` (`eval_.py:1902`), restricted to inhibits whose `kind == "artifact"`.
  - `Evaluator._hold_for_observable: np.ndarray` — set alongside `_hold_for_eligible` in the output loop and read at `eval_.py:1813`.
  - Rust: `Inhibit.kind: Option<String>`, `CompiledInhibit.kind: Option<String>`, and the env key `"__hold_for_observable"` inserted and removed beside `"__hold_for_eligible"` (`eval.rs:1716-1731`).
  - JSON: `"kind"` on an inhibit object, emitted **only when not None**.

- [ ] **Step 1: Write the failing tests**

`tests/test_resolver.py`:

```python
def test_kind_artifact_resolves_on_a_mute_inhibit(): ...
def test_kind_artifact_resolves_on_a_freeze_inhibit(): ...
def test_kind_on_a_flag_inhibit_is_refused():
    """A flag gates nothing, so kind = "artifact" on one would be a silent
    no-op in a safety-shaped declaration."""
    with pytest.raises(ResolveError, match="kind .* flag .* gates nothing"):
        resolve(parse(_inhibit_protocol(action="flag()", kind="artifact")))

def test_an_unknown_kind_is_refused():
    with pytest.raises(ResolveError, match='kind must be "artifact" or "trained"'):
        resolve(parse(_inhibit_protocol(kind="artefact")))

def test_an_unlabelled_inhibit_still_resolves():
    assert resolve(parse(_inhibit_protocol())).inhibits["emg"].kind is None
```

`tests/test_ir_json.py`:

```python
def test_kind_artifact_emits_ir_0_6_and_the_kind_key(): ...
def test_kind_trained_keeps_the_current_version_and_emits_the_kind_key():
    """It runs identically on a 0.24 engine, so refusing the document at load
    would be a refusal for documentation."""
    j = ir_to_json_obj(resolve(parse(_inhibit_protocol(kind="trained"))), sample_rate_hz=256)
    assert j["refrain_ir_version"] == "0.1" and j["inhibits"]["emg"]["kind"] == "trained"

def test_an_unlabelled_inhibit_emits_no_kind_key(): ...
```

`tests/test_eval_inhibit_kind.py` — a protocol binding `hold_for` inside `output { }`:

```python
def test_an_artifact_mute_holds_the_output_bound_hold_clock(backend): ...
def test_a_trained_mute_resets_the_output_bound_hold_clock(backend): ...
def test_an_unlabelled_mute_resets_it_exactly_as_today(backend): ...
def test_an_artifact_mute_spanning_a_chunk_boundary_holds_the_clock(backend): ...
def test_a_clench_under_an_artifact_mute_banks_nothing(backend):
    """Design §evidence, "On the muscle learner": muscle leaks into 12-15 Hz,
    so the clock must not climb on contaminated signal."""
def test_a_dip_inside_an_artifact_mute_does_not_count_against_tolerance(backend):
    """Review Focus 1, at the engine level: unobservable wins over false."""
def test_a_phase_mute_still_resets_the_clock(backend):
    """Row 4 of the design §1 forgiveness table — a mid-session rest is not an
    artifact."""
def test_an_inactive_block_channel_still_resets_the_clock(backend):  # row 5
def test_a_frozen_transport_clock_still_resets_the_clock(backend):   # row 6
```

Those last three, with the two tolerance rows already covered in Task 3 and the two mute rows above, are gate item 8: a test for every row of the §1 forgiveness table.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_resolver.py tests/test_ir_json.py tests/test_eval_inhibit_kind.py -v -k "kind or artifact"; echo "exit=$?"`
Expected: FAIL — `IRInhibit` has no `kind` field.

- [ ] **Step 3: Resolve and emit `kind`**

- `src/refrain/ir.py`: `kind: str | None = None` on `IRInhibit`, after `loc`.
- `src/refrain/resolver.py:1138` (`_resolve_inhibit`): read `kind` with `self._string_field`, validate against `{"artifact", "trained"}`, and refuse it when `action_kind == "flag"` with a message that says a flag gates nothing so the label would be a silent no-op. Note that `_resolve_inhibit` has no unknown-field check, so an unvalidated `kind` would today be silently dropped — that is why the value set is validated rather than merely read.
- `src/refrain/ir_json.py:435` (`_emit_inhibit`): add `"kind"` only when `ih.kind is not None`. In `_protocol_ir_version`, extend the `"0.6"` clause with `any(ih.kind == "artifact" for ih in ir.inhibits.values())`.
- `src/refrain/schema/ir-json-v0.6.schema.json`: `"kind": {"enum": ["artifact", "trained"]}` in `$defs/Inhibit/properties`.

- [ ] **Step 4: Feed the observability mask to the output-bound hold path**

- `src/refrain/eval_.py`: add `_compute_artifact_muted` beside `_compute_muted` (`:1902`); call it at `:1241` next to the `muted` line; in the output loop at `:1281`, set `self._hold_for_observable = ~artifact_muted` alongside `_hold_for_eligible`; at `:1813-1814`, pass the condition and the observability mask as two arguments instead of AND-ing one in:

```python
if call.callee in ("hold_for", "dwell", "dwell_rearm") and hasattr(self, "_hold_for_eligible"):
    condition = dynamic_chunks[0].astype(bool) & self._hold_for_eligible
    return impl.step(condition, self._hold_for_observable)
```

The ordering matters and is worth a comment: `_hold_for_eligible` carries the mute, the muted rest and the inactive-channel rules, all of which still zero the streak. The observability mask carries only the artifact label, and it is checked **first** in the per-sample rule, which is what makes an artifact-muted sample neither a success nor a failure.

- `refrain-core/src/eval.rs`: `Inhibit.kind` → `CompiledInhibit.kind` in `build_inhibit` (`:2588`); accumulate an `artifact_muted` vector in the inhibit loop at `:1541-1586` under the same `allowed` filter; insert `"__hold_for_observable"` beside `"__hold_for_eligible"` at `:1720` and remove it at `:1731`; the three `CNode` arms at `:430-450` read it and pass `Some(&mask)` to `step`.

- [ ] **Step 5: Add the fixture protocol and regenerate golden vectors**

`bench/protocols/micro_15_artifact_gate.refrain`: one `band_envelope` derive for the trained band plus one `bandpower` derive for EMG, an `inhibit "emg"` with `kind = "artifact"` and `action = mute(release: 350 ms)`, a `reward { event = dwell(..., tolerance: 100 ms) }`, and an `output { }` channel binding `hold_for` directly — so the single fixture exercises both hold paths. Add the stem to `gen_fixtures.py`, to `EVENT_BEARING` and to `TAP_BEARING` (the `muted` tap matters here), then regenerate.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_resolver.py tests/test_ir_json.py tests/test_eval_inhibit_kind.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Run: `cd refrain-core && cargo test; echo "exit=$?"`
Run: `python refrain-core/tools/check_equivalence.py; echo "exit=$?"`
Expected: PASS.

- [ ] **Step 7: Report which inhibits are still unlabelled**

Design §2 asks for this and it is what gives Stage 3 its worklist: "The catalog should be able to report how many inhibits are still unlabelled." The data is already exposed by `IRInhibit.kind` after step 3, so what is missing is a documented place to read it, not a subsystem. The natural home is the CRED-nf report, which already summarises artifact rejection.

Extend `_artifact_rejection(ir)` in `src/refrain/ir_print.py:411` so each inhibit's line names its label — `artifact`, `trained` or `unlabelled` — and `print_cred_nf` therefore carries the count for free. Stage 3 gets its worklist by looping the corpus through `print_cred_nf`. Tests in `tests/test_ir_print.py`:

```python
def test_the_cred_nf_report_names_each_inhibits_label(): ...
def test_an_unlabelled_inhibit_is_reported_as_unlabelled_not_as_trained():
    """Absence is recorded as unlabelled, never asserted to be a trained
    condition — most of them are mislabelled artifact gates."""
```

This is the report, not the compile-time advisory; whether an advisory is worth the noise in this release stays out of scope.

- [ ] **Step 8: Document**

`docs/SPEC.md` §4.6: the `kind` field, its two values, and that absence is recorded as unlabelled rather than asserted to be `"trained"`. §7.4: the behaviour table from design §1, verbatim, since the table is the contract. State that the compiler may advise on an unlabelled inhibit but must not refuse one, and that a labelled artifact gate is a reward change where it applies.

- [ ] **Step 9: Commit**

```bash
git add src/refrain/ir.py src/refrain/resolver.py src/refrain/ir_json.py src/refrain/eval_.py \
        src/refrain/ir_print.py src/refrain/schema/ir-json-v0.6.schema.json refrain-core/src/ \
        refrain-core/tests/artifact_gate.rs refrain-core/tools/gen_fixtures.py \
        refrain-core/tests/fixtures/micro_15_artifact_gate.* \
        bench/protocols/micro_15_artifact_gate.refrain \
        tests/test_eval_inhibit_kind.py tests/test_resolver.py tests/test_ir_json.py \
        tests/test_ir_print.py docs/SPEC.md
git commit -m "feat: inhibit kind, and artifact-muted time is unobservable on the output hold path"
```

---

### Task 7: The observability mask reaches the reward-event path

The path 44 of the 45 live protocols use, and the one the design's measurement is about: today the clock runs straight through a mute, the rising edge fires inside the blind window at 2.945 s, and because `dwell` does not re-arm the burst never produces another event.

The Python change is a reorder: `_eval_reward_event` runs at `eval_.py:1200`/`:1225`, and `muted` does not exist until `:1241`. **Rust needs no reorder** — the inhibit loop at `eval.rs:1541` already runs before the reward stage at `:1656`, so the Rust change is only to thread the mask in. The design says both engines move; they do not, and the asymmetry is worth a comment in each.

**Files:**
- Create: `tests/test_eval_artifact_mask_reward.py`
- Modify: `src/refrain/eval_.py:1157-1241, 1817-1884`
- Modify: `refrain-core/src/eval.rs:645-700, 1656-1726`
- Modify: `refrain-core/tests/artifact_gate.rs`
- Modify: `docs/SPEC.md` §7.4

**Interfaces:**
- Consumes: `_compute_artifact_muted` and `_hold_for_observable` from Task 6.
- Produces: `Evaluator._eval_reward_event(..., observable: np.ndarray | None = None)` — a new keyword-only parameter on the existing signature at `eval_.py:1817`, forwarded to `impl.step(condition_chunk, observable)` at `:1884`. Rust: `step_reward_event(re, &env, n, observable: Option<&[bool]>)` at `eval.rs:645`.

- [ ] **Step 1: Write the failing test**

The headline case is the design's own measurement, reproduced as a test. 256 Hz, a 250 ms hold, a genuine 2 s burst with a swallow 100 ms in, an artifact-labelled EMG mute from 2.80 s to 3.65 s.

```python
def test_a_swallow_inside_a_genuine_burst_still_earns_its_one_reward(backend):
    """Design §evidence. Today the rising edge fires at 2.945 s inside the
    blind window and the trainee hears nothing; under the pause rule it fires
    at 3.797 s, after the mute clears, and they hear it."""
    fired_at = _first_delivered_event_time(backend, _swallow_scenario())
    assert fired_at == pytest.approx(3.797, abs=1 / 256)

def test_an_unlabelled_mute_leaves_the_reward_event_path_exactly_as_today(backend):
    """Today this path applies NO mask at all. Absence must reproduce that,
    not the output path's reset — the two differ, and goal 5 forbids moving
    either without a declaration present."""
    assert _first_raw_event_time(backend, _swallow_scenario(kind=None)) == pytest.approx(2.945, abs=1/256)

def test_a_trained_mute_leaves_the_reward_event_path_exactly_as_today(backend): ...

def test_a_block_scoped_artifact_inhibit_only_masks_inside_its_block(backend):
    """An inhibit outside the active block's `inhibit` set does not gate
    output, so it must not make the reward clock unobservable either."""

def test_the_reward_event_holds_tap_agrees_across_backends(backend): ...
def test_the_muted_tap_keyset_is_unchanged(backend):
    """No tap is added by this release; the exact-equality keyset parity test
    must still pass."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_eval_artifact_mask_reward.py -v; echo "exit=$?"`
Expected: FAIL — the first test reports ~2.945 s (the event is masked at the output stage and lost).

- [ ] **Step 3: Move `_compute_muted` above the reward stage in Python**

In `src/refrain/eval_.py`, move the active-block lookup (`ph`/`active_block`, currently at `:1210-1216`) and the `muted` / `artifact_muted` computation (currently `:1241`) to just after the inhibit loop ends at `:1158`, before the reward stage at `:1160`. Then pass `observable=~artifact_muted` into both `_eval_reward_event` call sites (`:1200` top-level, `:1225` per-bundle).

Two things to be careful about, both worth a comment:
- The active-block lookup currently sits between the reward stage and the output stage; moving it up must not change `active_channels` or `active_bundle`, which are derived from the same `ph` and read later.
- Every reward bundle is stepped on every chunk "keeping dwell timers warm" (`:1217-1229`). The mask applies to all of them, active or not, since an artifact is an artifact regardless of which block is live.

- [ ] **Step 4: Thread the mask in Rust**

`refrain-core/src/eval.rs`: give `step_reward_event` (`:645`) an `observable: Option<&[bool]>` parameter and pass it to `re.dwell.step(&condition, observable)`; do the same at the top-level reward-event site (`:1656-1685`). Add a comment at the inhibit loop saying the Rust ordering already placed `muted` before the reward stage, so no reorder was needed here — which is why the Python diff for this task is larger than the Rust one.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_eval_artifact_mask_reward.py tests/test_eval_taps.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Run: `python -m pytest tests/ ; echo "exit=$?"`
Run: `cd refrain-core && cargo test; echo "exit=$?"`
Run: `python refrain-core/tools/check_equivalence.py; echo "exit=$?"`
Expected: PASS. Run the **whole** Python suite here, not just the new file — this is the reorder, and the staged-protocol and autopilot suites are the ones that read `active_block` and `ChunkFacts`.

- [ ] **Step 6: Document and commit**

`docs/SPEC.md` §7.4: state explicitly that the observability mask reaches the reward-event path only for inhibits labelled `"artifact"`, and that unlabelled and `"trained"` inhibits contribute nothing there, as now — so no existing protocol changes until one is labelled.

```bash
git add src/refrain/eval_.py refrain-core/src/eval.rs refrain-core/tests/artifact_gate.rs \
        tests/test_eval_artifact_mask_reward.py docs/SPEC.md
git commit -m "feat: artifact-muted time is unobservable on the reward-event path too"
```

---

### Task 8: `scale` on a seed rule, and the worked per-person muscle limit

**Files:**
- Create: `bench/protocols/seed_scale_emg.refrain`
- Create: `tests/test_eval_seed_scale.py`
- Modify: `src/refrain/ir.py:265-274`, `src/refrain/resolver.py:182-187, 1484-1528, 1573-1620`, `src/refrain/ir_json.py:151-190, 470-476`, `src/refrain/eval_.py:864-902`
- Modify: `refrain-core/src/ir.rs:137-142`, `refrain-core/src/eval.rs` (seed latch fire path)
- Modify: `src/refrain/schema/ir-json-v0.6.schema.json`
- Modify: `tests/test_resolve_seed.py`, `tests/test_ir_json_seed.py`, `tests/test_eval_seed.py`
- Modify: `docs/SPEC.md` §4.9.4

**Interfaces:**
- Produces:
  - `IRControlSeed.scale: float = 1.0`, appended after `loc`.
  - `_PendingSeed.scale: float` in `src/refrain/resolver.py:182`.
  - `ControlSeed.scale: f64` in `refrain-core/src/ir.rs` with `#[serde(default = "one")]`.
  - JSON: `"scale"` on a seed object, emitted **only when `!= 1.0`**.
  - The seeded value is `statistic_result * scale`, then clamped to the control's declared `range`.

- [ ] **Step 1: Write the failing tests**

`tests/test_resolve_seed.py`:

```python
def test_scale_resolves_as_a_plain_multiplier():
    ir = resolve(parse(_seeded_protocol(scale="3.0")))
    assert ir.controls["emg_limit_uv"].seed.scale == 3.0

def test_scale_absent_is_one():
    assert resolve(parse(_seeded_protocol())).controls["emg_limit_uv"].seed.scale == 1.0

def test_a_non_positive_scale_is_refused():
    """Review Focus 4: scale = 0 makes a seeded muscle limit 0 uV, so the
    inhibit mutes the whole session."""
    for bad in ("0", "0.0", "-1.0"):
        with pytest.raises(ResolveError, match="scale must be greater than zero"):
            resolve(parse(_seeded_protocol(scale=bad)))

def test_scale_may_not_bind_a_control():
    with pytest.raises(ResolveError, match="scale must be a plain number"):
        resolve(parse(_seeded_protocol(scale="some_control")))
```

`tests/test_ir_json_seed.py`:

```python
def test_a_scaled_seed_emits_ir_0_6_and_the_scale_key(): ...
def test_an_unscaled_seed_emits_no_scale_key_and_keeps_version_0_3():
    j = ir_to_json_obj(resolve(parse(_seeded_protocol())), sample_rate_hz=256)
    assert j["refrain_ir_version"] == "0.3" and "scale" not in j["controls"]["emg_limit_uv"]["seed"]
```

`tests/test_eval_seed_scale.py`:

```python
def test_the_seeded_value_is_the_statistic_times_the_scale(backend):
    """A constant-fill warm-up makes the percentile exact by construction:
    median 5.0 uV, scale 3.0, seeded 15.0 uV."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=3.0) == pytest.approx(15.0)

def test_a_scaled_seed_is_still_clamped_to_the_controls_range(backend):
    """scale cannot push a limit outside what the protocol declared as sane."""
    assert _seeded_value(backend, fill=5.0, target_pct=50, scale=100.0,
                         control_range=(0.5, 30.0)) == pytest.approx(30.0)

def test_the_scale_appears_in_the_seed_report(backend): ...

def test_a_warm_up_seed_that_cannot_fill_its_window_still_fails_closed(backend):
    """Gate item 10. Pinned so the asymmetry with a re-seed's keep-the-old-value
    rule (Task 10) is deliberate on the record rather than discovered in a
    clinic: a trainee whose warm-up got cut short hears nothing all session."""
    report, taps = _short_warmup_run(backend)
    assert report["emg_limit_uv"]["status"] == "insufficient_samples"
    assert all(t["output/audio_gain"] == 0.0 for t in taps)

def test_the_worked_muscle_limit_composition_runs_end_to_end(backend):
    """A derive feeding ONLY an inhibit, a control seeded from it, and
    `threshold = absolute(<control>)` on the inhibit — design §4."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_resolve_seed.py tests/test_ir_json_seed.py tests/test_eval_seed_scale.py -v -k scale; echo "exit=$?"`
Expected: FAIL — `_PendingSeed.__init__() got an unexpected keyword argument 'scale'`.

- [ ] **Step 3: Parse, validate and carry `scale`**

- `src/refrain/resolver.py:1484` (`_parse_control_seed`): read `scale` as an `A.NumberLit` with no unit, default 1.0; refuse a non-number (so a control ref is refused with a message naming it) and refuse `<= 0`.
- `src/refrain/ir.py`: `scale: float = 1.0` on `IRControlSeed`.
- `src/refrain/resolver.py:1620`: pass it through to the constructed `IRControlSeed`.
- `src/refrain/ir_json.py:470` (`_emit_seed`): add `"scale"` only when `seed.scale != 1.0`; extend the `"0.6"` clause in `_protocol_ir_version` with `any(c.seed is not None and c.seed.scale != 1.0 for c in ir.controls.values())`.
- `src/refrain/schema/ir-json-v0.6.schema.json`: `"scale": {"type": "number", "exclusiveMinimum": 0}` in the seed object, which currently closes with `additionalProperties: false`.

- [ ] **Step 4: Apply the scale at the seed latch, and clamp only the scaled value**

`src/refrain/eval_.py:864-902` (`_step_seeds`): multiply the percentile result by `latch.scale`, then clamp to the control's `range_low`/`range_high` **only when `scale != 1.0`**. Mirror in `refrain-core/src/eval.rs`'s latch fire path. Add `scale` to the `seed_report()` entry in both engines (`eval_.py:1621` and `SeedReportEntry` at `eval.rs:875`).

The conditional clamp needs saying out loud, because the design asserts the seeded value is "still clamped to the control's declared range" and **there is no such clamp today**. `_apply_control` (`eval_.py:1681-1692`) writes the value straight through, and `_step_seeds` does not clamp either, so an unscaled seed whose percentile lands outside its declared range is written unclamped right now. Clamping every seed would move the seeded value, and therefore the reward behaviour, of an existing protocol with no new declaration present — the one thing goal 5 forbids. So the clamp attaches to `scale`, which is the new thing and the one that can push a value far out. Whether an unscaled seed should also be clamped is a behaviour change with its own argument, and it belongs to Stage 3.

Add the test that puts the asymmetry on the record:

```python
def test_an_unscaled_seed_outside_its_range_is_still_written_unclamped(backend):
    """Today's behaviour, pinned. The clamp attaches to `scale` so that adding
    it moves no existing protocol's seeded value."""
    assert _seeded_value(backend, fill=50.0, target_pct=50,
                         control_range=(0.5, 30.0)) == pytest.approx(50.0)
```

- [ ] **Step 5: Add the worked fixture protocol**

`bench/protocols/seed_scale_emg.refrain`, verbatim from design §4: a `bandpower(input: "raw", band: (50 Hz, 100 Hz), window: 100 ms)` derive, an `emg_limit_uv` voltage control with `seed = percentile { from = "emg_envelope"; window = 60 s; target_pct = 50; scale = 3.0 }`, and an `inhibit "emg"` with `metric = rectify("emg_envelope")`, `threshold = absolute(emg_limit_uv)`, `action = mute(release: 350 ms)` and `kind = "artifact"`. Generate it the way `seed_smr_baseline` is generated (`gen_fixtures.py:399`): `skip_warmup=False`, a constant-fill signal, `amp=None`.

Note for the implementer: this protocol is deliberately **not** in the editor round-trip subset — `_match_reward_component` (`src/refrain/editor/describe.py:318`) matches an artifact inhibit only in the `bandpower` + `percentile` shape, not `rectify` + `absolute`. That is expected and Task 11 does not try to change it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_resolve_seed.py tests/test_ir_json_seed.py tests/test_eval_seed.py tests/test_eval_seed_scale.py tests/test_seed_export_state.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Run: `cd refrain-core && cargo test; echo "exit=$?"`
Run: `python refrain-core/tools/check_equivalence.py; echo "exit=$?"`
Expected: PASS.

- [ ] **Step 7: Document**

`docs/SPEC.md` §4.9.4: the `scale` field, default 1.0, refused at or below zero, applied before the range clamp. Add the "three times the warm-up median" reading and why a high percentile is the wrong tool for a muscle limit (a 95th-percentile gate trips 5 % of warm-up by construction). Record the harsh constraint from design §4 as normative: a seeded control's window must fit inside a timed, output-muted phase 0, and a seed that does not complete mutes the output for the rest of the session.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/ir.py src/refrain/resolver.py src/refrain/ir_json.py src/refrain/eval_.py \
        src/refrain/schema/ir-json-v0.6.schema.json refrain-core/src/ \
        bench/protocols/seed_scale_emg.refrain refrain-core/tools/gen_fixtures.py \
        refrain-core/tests/fixtures/seed_scale_emg.* \
        tests/test_eval_seed_scale.py tests/test_resolve_seed.py tests/test_ir_json_seed.py docs/SPEC.md
git commit -m "feat: scale multiplier on a seed rule, and the worked per-person muscle limit"
```

---

### Task 9: The `reseed` declaration, its compile-time checks, and the downstream-hold set

Declaration and resolve-time validation only. No runtime behaviour, so a reviewer can gate the language surface before the evaluator work lands.

**Files:**
- Create: `tests/test_resolve_reseed.py`
- Modify: `src/refrain/ir.py:375-390`, `src/refrain/resolver.py:1224-1268, 1270-1300`, `src/refrain/ir_json.py:151-190, 560-566`
- Modify: `refrain-core/src/ir.rs:248-258`
- Modify: `src/refrain/schema/ir-json-v0.6.schema.json`
- Modify: `docs/SPEC.md` §4.7 (blocks), `docs/IR-JSON.md`

**Interfaces:**
- Produces:
  - `IRBlock.reseed: tuple[str, ...] = ()` — bare control names, in declaration order.
  - `IRBlock.reseed_holds: tuple[str, ...] = ()` — the baked downstream-hold set, computed at resolve time so both engines read the identical set rather than rediscovering it at runtime. Each entry is a hold **site**, one of: `"reward/event"` (the top-level reward event), `"reward_bundle/<name>"` (a bundle's event), or `"output/<channel>"` (a hold bound in that output channel's expression). Sorted, so the emitted JSON is stable.
  - `Resolver._reseed_downstream_holds(control_name: str) -> tuple[str, ...]` — the dependency walk: from `control/<name>`, through any threshold whose `threshold_call` references it and any condition expression that references the control or such a threshold, to each hold site. Reuses the `walk` closure shape of `_referenced_control_targets` (`resolver.py:1536-1556`).
  - Rust: `Block.reseed: Vec<String>` and `Block.reseed_holds: Vec<String>`, both `#[serde(default)]`.
  - JSON: `"reseed"` and `"reseed_holds"` on a block object, emitted **only when `reseed` is non-empty**.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_resolve_reseed.py
def test_reseed_resolves_to_the_named_controls():
    ir = resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]')))
    assert ir.blocks["two"].reseed == ("smr_threshold_uv",)

def test_reseed_absent_is_empty_and_is_todays_behaviour():
    assert resolve(parse(_staged_protocol())).blocks["two"].reseed == ()

def test_reseed_naming_an_unknown_control_is_refused():
    with pytest.raises(ResolveError, match='unknown control "nope"'):
        resolve(parse(_staged_protocol(reseed='["nope"]')))

def test_reseed_naming_a_control_with_no_seed_rule_is_refused():
    with pytest.raises(ResolveError, match="has no `seed` rule"):
        resolve(parse(_staged_protocol(reseed='["plain_uv"]')))

def test_reseed_naming_a_control_whose_seed_was_folded_out_is_refused():
    """Review Focus 5: _resolve_control_seeds declines to attach a seed to a
    control nothing references, so this would compile and then do nothing."""
    with pytest.raises(ResolveError, match="seed was dropped"):
        resolve(parse(_staged_protocol_with_dead_seed(reseed='["dead_uv"]')))

def test_a_reseed_window_that_cannot_fit_the_preceding_block_is_refused():
    """Mirrors the existing warm-up check at resolver.py:1611-1619. Phase
    durations are numeric literals, so this is always knowable."""
    with pytest.raises(ResolveError, match="exceeds the preceding block"):
        resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]',
                                       block_one_duration="30 s", seed_window="60 s")))

def test_an_unexpected_block_field_is_still_refused():
    """_resolve_block has a strict allowed-field set; reseed joins it."""
    with pytest.raises(ResolveError, match="unexpected field"):
        resolve(parse(_staged_protocol_with_field("resed", '["x"]')))

def test_the_downstream_hold_set_names_the_reward_event():
    ir = resolve(parse(_staged_protocol(reseed='["smr_threshold_uv"]')))
    assert ir.blocks["two"].reseed_holds == ("reward_bundle/two",)

def test_the_downstream_hold_set_names_an_output_bound_hold():
    ir = resolve(parse(_staged_protocol_with_output_hold(reseed='["smr_threshold_uv"]')))
    assert "output/audio_gain" in ir.blocks["two"].reseed_holds

def test_a_hold_not_downstream_of_the_reseeded_control_is_not_in_the_set():
    """Narrower than a blanket boundary reset: every reward bundle is stepped
    on every chunk to keep dwell timers warm (eval_.py:1217-1229), so a
    blanket reset would change what the staged protocols already do with no
    new declaration present — the one thing goal 5 forbids."""
    ir = resolve(parse(_staged_protocol_two_independent_holds(reseed='["smr_threshold_uv"]')))
    assert ir.blocks["two"].reseed_holds == ("reward_bundle/two",)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_resolve_reseed.py -v; echo "exit=$?"`
Expected: FAIL — `IRBlock` has no `reseed` field; `_resolve_block` refuses `reseed` as an unexpected field.

- [ ] **Step 3: Parse and validate `reseed`**

- `src/refrain/ir.py`: `reseed: tuple[str, ...] = ()` and `reseed_holds: tuple[str, ...] = ()` on `IRBlock`.
- `src/refrain/resolver.py:1224` (`_resolve_block`): add `"reseed"` to the allowed-field set at `:1226` and read it with the existing `_str_list` helper.
- `src/refrain/resolver.py:1270` (`_validate_staging`): for each named control, refuse an unknown name; refuse one with no pending seed; refuse one whose seed was dropped as dead, with a message that says the control is referenced by nothing so its seed was folded out. Run this **after** `_resolve_control_seeds` so the dead-seed decision is already made — check the call order at `:332` and move the staging validation if needed, saying why in a comment.
- The window-fits-the-preceding-block check: for each phase that activates a re-seeding block, sum the durations of the contiguous run of preceding phases that activate the **same** block as the phase immediately before it, and refuse when the seed's `window_ms` exceeds that. Mirror the message shape of the warm-up check at `:1611-1619`.

- [ ] **Step 4: Implement the downstream-hold walk**

`_reseed_downstream_holds` in `src/refrain/resolver.py`. Build it in two passes: first the set of threshold canonical names whose `threshold_call` references `control/<name>`; then, for each hold site, walk its expression and record the site if the walk reaches the control directly or any threshold in that set. The hold sites are the top-level `reward.event`, each reward bundle's `event`, and each `output` channel expression containing a `hold_for`/`dwell`/`dwell_rearm` call. Sort the result.

- [ ] **Step 5: Emit and deserialize**

- `src/refrain/ir_json.py:560` (`_emit_block`): add `"reseed"` and `"reseed_holds"` only when `b.reseed` is non-empty; extend the `"0.6"` clause with `any(b.reseed for b in ir.blocks.values())`.
- `refrain-core/src/ir.rs:248`: the two `Vec<String>` fields with `#[serde(default)]`.
- `src/refrain/schema/ir-json-v0.6.schema.json`: both arrays of strings on the block object, and `"reseed_holds"` required when `"reseed"` is present (`dependentRequired`), matching the emitter.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_resolve_reseed.py tests/test_staged_resolve.py tests/test_ir_json.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Run: `cd refrain-core && cargo test; echo "exit=$?"`
Expected: PASS.

- [ ] **Step 7: Document and commit**

`docs/SPEC.md` §4.7: the `reseed` field, off by default, each named control needing a `seed` rule, the trailing-window semantics and the compile-time window check. State the cost plainly: if blocks train materially different states the trailing window reads the previous block's signal, which is a reason to leave re-seeding off. `docs/IR-JSON.md`: both new block keys and the hold-site spelling.

```bash
git add src/refrain/ir.py src/refrain/resolver.py src/refrain/ir_json.py \
        src/refrain/schema/ir-json-v0.6.schema.json refrain-core/src/ir.rs \
        tests/test_resolve_reseed.py docs/SPEC.md docs/IR-JSON.md
git commit -m "feat: block reseed declaration, its compile checks and the downstream-hold set"
```

---

### Task 10: Re-seeding at run time, on both backends

**Files:**
- Create: `bench/protocols/reseed_staged.refrain`
- Create: `tests/test_eval_reseed.py`
- Create: `refrain-core/tests/reseed.rs`
- Modify: `src/refrain/eval_.py:324-343, 840-902, 1106-1120, 1602-1631`
- Modify: `refrain-core/src/eval.rs` (seed latch, phase cursor, `SeedReportEntry`)
- Modify: `refrain-core/tools/gen_fixtures.py`
- Modify: `docs/EMBEDDING.md` (the `seed_report()` shape), `docs/SPEC.md` §4.7

**Interfaces:**
- Consumes: `IRBlock.reseed` / `IRBlock.reseed_holds` from Task 9; `_compute_artifact_muted` from Task 6; `IRControlSeed.scale` from Task 8.
- Produces:
  - `_SeedLatch.fresh: int` — samples ingested since the latch fired or since the last re-seed. A re-seed fires only when `fresh >= window_samples`; otherwise the old value is kept. This is what makes "a short window" mean something real: the latch's `PercentileImpl` buffer is a `maxlen` deque that never shrinks, so buffer length alone would report a full window of stale samples.
  - `Evaluator._step_reseeds(active_block, t0_s) -> None` — called from the seed slot at `eval_.py:1106`, before any threshold steps, on the chunk where the phase cursor first enters a phase whose block declares a non-empty `reseed`.
  - `Evaluator._ingest_reseed_buffers(stream_values, artifact_muted, freeze_ingest) -> None` — called at the **end** of the chunk, after the mute gate exists. Ingestion order: firing reads the trailing window through the previous chunk, which is causal and needs no reordering of the inhibit stage.
  - `seed_report()[name]["history"]` — a list, one record per seeding event (the warm-up seed included, so the list is never empty for a seeded control), each `{"at_time_s", "old_value", "new_value", "source", "window_s", "target_pct", "scale", "status", "trigger"}` where `trigger` is `"warmup"` or `"block:<name>"`. Additive: every key hosts already parse stays where it is. Rust: `SeedReportEntry.history: Vec<SeedHistoryEntry>`, serialized by the existing `seed_report()` PyO3 path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_reseed.py
def test_a_reseed_at_a_block_boundary_rewrites_the_control(backend):
    """Constant-fill signals per block make the percentile exact: block one
    runs at 5.0 uV, block two's trailing window is all 5.0, so a 50th
    percentile re-seed writes 5.0 where warm-up wrote 2.0."""

def test_the_reseeded_value_is_bit_identical_across_backends(backend): ...

def test_the_reseed_uses_a_trailing_window_ending_at_the_boundary(backend):
    """A ramp through block one: the re-seeded value reflects the last
    `window` of block one, not its first."""

def test_a_reseed_resets_the_hold_clock_for_downstream_holds_only(backend):
    """A streak banked against the old threshold is not meaningful against the
    new one. A hold NOT downstream keeps its streak."""

def test_a_block_entered_twice_reseeds_on_every_entry(backend):
    report = _run_abab(backend)["smr_threshold_uv"]
    assert [h["trigger"] for h in report["history"]] == ["warmup", "block:two", "block:two"]

def test_a_short_reseed_window_keeps_the_old_value_and_records_the_miss(backend):
    """Deliberately asymmetric with warm-up seeding, which fails closed. At a
    re-seed there IS a known-good value — the one that has been running all
    session — so killing the session is not proportionate."""
    report = _run_with_frozen_ingestion(backend)["smr_threshold_uv"]
    assert report["value"] == pytest.approx(2.0)          # unchanged
    assert report["history"][-1]["status"] == "insufficient_samples"
    assert report["history"][-1]["new_value"] is None

def test_ingestion_is_frozen_while_an_artifact_inhibit_is_muting(backend):
    """An EMG-contaminated sample would push the threshold up at exactly the
    moment it must not."""

def test_ingestion_is_frozen_during_an_output_muted_rest_phase(backend):
    """Mirrors the set_ingesting freeze refrain already applies to rolling
    percentile thresholds (eval_.py:1116-1119)."""

def test_a_host_write_permanently_disarms_the_reseed_too(backend):
    """If the practitioner moved a threshold by hand during block one, block
    three does not take it back."""
    report = _run_with_host_write(backend)["smr_threshold_uv"]
    assert report["status"] == "disarmed_by_host"
    assert [h["trigger"] for h in report["history"]] == ["warmup"]

def test_seed_report_history_agrees_across_backends(backend): ...

def test_a_resumed_session_starts_its_reseed_buffers_empty(backend):
    """Out of scope by design, documented rather than hidden: an
    export_state / seed-state round-trip does not carry re-seed buffers."""

def test_a_protocol_with_no_reseed_does_not_ingest_after_the_latch(backend):
    """Absence must reproduce today's behaviour exactly, including the cost."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_eval_reseed.py -v; echo "exit=$?"`
Expected: FAIL — `Evaluator` has no `_step_reseeds`.

- [ ] **Step 3: Keep the latch buffer ingesting, with the two freezes**

In `src/refrain/eval_.py`, add `fresh: int = 0` and `history: list[dict]` to `_SeedLatch` (`:324`), and build a `self._reseed_controls: set[str]` of every control named in any block's `reseed` (empty for every existing protocol, which is what keeps today's cost unchanged). Add `_ingest_reseed_buffers`, called at the end of `_process_chunk`: for each latch whose control is in `_reseed_controls` and whose `armed` flag is still set, ingest `src[~artifact_muted]` when `not freeze_ingest`, and add the ingested count to `fresh`. Reuse the latch's existing `PercentileImpl` — same `window_ms`, same `target_pct`, same NaN-skip rule — so the arithmetic is the one baseline seeding already ships and nothing new has to be proved bit-exact.

- [ ] **Step 4: Fire the re-seed at the boundary**

Add `_step_reseeds`, called from the seed slot at `eval_.py:1106` before the threshold loop. On the chunk where the phase cursor first enters a phase whose block declares a non-empty `reseed`: for each named control, skip a latch that a host write disarmed (`:1602-1610`); if `fresh >= window_samples`, recompute the percentile, apply `scale`, clamp to range, `_apply_control`, refresh `control_chunks_cache` the way the warm-up latch does at `:900`, reset `fresh` to 0, and call `reset()` on every hold impl named by the block's `reseed_holds`; otherwise keep the old value. Append a history record either way.

Build the `reseed_holds` → impl mapping once in `__init__` from the IR, so the runtime does a dict lookup rather than a dependency walk — that is the point of baking the set.

- [ ] **Step 5: Mirror in Rust**

`refrain-core/src/eval.rs`: `fresh: u64` and `history: Vec<SeedHistoryEntry>` on `SeedLatch`; the artifact-masked end-of-chunk ingest; the boundary fire in the same position relative to the threshold loop; the hold-reset map keyed by the `reseed_holds` site strings. The phase cursor already tracks `phase_index`, so "first chunk in this phase" is `phase_elapsed == 0` after the advance — confirm against the Python cursor rather than assuming, since an off-by-one here fires the re-seed a chunk early or late and the gate will catch it as drift.

- [ ] **Step 6: Add the fixture protocol and regenerate golden vectors**

`bench/protocols/reseed_staged.refrain`: two blocks, a seeded `smr_threshold_uv`, block two declaring `reseed = ["smr_threshold_uv"]`, a reward bundle per block. Generate with `skip_warmup=False` and a per-block constant-fill signal, `amp=None`, as `seed_smr_baseline` is. Add to `EVENT_BEARING` and `TAP_BEARING`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_eval_reseed.py tests/test_eval_seed.py tests/test_seed_export_state.py tests/test_staged_eval.py tests/test_staged_parity.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Run: `cd refrain-core && cargo test; echo "exit=$?"`
Run: `python refrain-core/tools/check_equivalence.py; echo "exit=$?"`
Expected: PASS.

- [ ] **Step 8: Document**

`docs/EMBEDDING.md` (the seed-report section at `:465`): the `history` array, its keys, that it is additive so nothing host-facing breaks at 0.25.0, and that a resumed session starts its re-seed buffers empty. `docs/SPEC.md` §4.7: the ingestion policy (during `run`, frozen under an artifact mute and during an output-muted rest), the keep-the-old-value rule and why it differs from warm-up fail-closed, the host-write rule, and that a block entered twice re-seeds twice.

- [ ] **Step 9: Commit**

```bash
git add src/refrain/eval_.py refrain-core/src/eval.rs refrain-core/tests/reseed.rs \
        refrain-core/tools/gen_fixtures.py refrain-core/tests/fixtures/reseed_staged.* \
        bench/protocols/reseed_staged.refrain tests/test_eval_reseed.py \
        docs/EMBEDDING.md docs/SPEC.md
git commit -m "feat: block-boundary re-seeding, recorded in the seed report"
```

---

### Task 11: The editor — model schema, describe, render, round-trip

Gate item 5. The editor model is a *subset* mechanism: `_match_block` reads only `threshold` and `_match_reward_component` reads a fixed slot list, so an undeclared field is silently dropped and the re-rendered protocol differs. Without this task a Stage 3 protocol carrying any of the four declarations falls out of the round-trip subset.

**Files:**
- Modify: `src/refrain/editor/protocol-model.schema.json`
- Modify: `src/refrain/editor/catalog.json` (block slot lists and templates)
- Modify: `src/refrain/editor/render.py:23-25` (`_fill`), `:28-40` (`_render_seed`), `:249-252` (block rendering)
- Modify: `src/refrain/editor/describe.py:17-36, 318-328, 496-560, 597-601`
- Modify: `tests/test_editor_roundtrip.py`, `tests/test_editor_model_schema.py`, `tests/test_editor_render.py`

**Interfaces:**
- Consumes: `IRControlSeed.scale` (Task 8), `IRInhibit.kind` (Task 6), `IRBlock.reseed` (Task 9), the `tolerance` call argument (Task 3).
- Produces, in the ProtocolModel:
  - `control.seed.scale: number` — a new property on the seed object, which closes with `additionalProperties: false` and so **requires** the schema change. Rendered by the hand-written `_render_seed`, so it needs no catalog work.
  - `blocks[].reseed: string[]` — a new optional property on the block object. Rendered by the hand-written block loop at `render.py:249`, so it needs no catalog work either.
  - A `tolerance` slot on each of the eight `kind: "reward"` catalog blocks, and a `kind` slot on `inhibit.artifact`. These ride in `blockNode.slots`, which is an open map (`additionalProperties: {$ref: slotValue}`), so the model schema needs no change — but the **catalog** does, and so does `_fill`.
  - `_fill(template, slots_def, slot_values)` gains optional-slot support: a slot whose catalog entry carries `"optional": true` and which the model omits is substituted with `""`. Today `_fill` formats `template.format(**{n: ... for n, v in slot_values.items()})` (`render.py:25`), so a placeholder with no slot value raises `KeyError` — which is why a fixed template cannot carry an optional clause as it stands.

The tradeoff worth naming: an optional slot's value carries its own leading separator (`", tolerance: 100 ms"`), because the alternative is template-level conditionality, which is a much larger mechanism for four fields. The `"optional": true` marker is what keeps that cheap trick honest — without it, a mistyped slot name would silently render as empty rather than failing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_editor_roundtrip.py
ALL_FOUR = '''...'''   # one protocol carrying tolerance, kind, reseed and scale

def test_a_protocol_using_all_four_declarations_is_in_the_subset_and_round_trips():
    d = describe_protocol(ALL_FOUR)
    assert d["in_subset"] is True and d["model"] is not None
    assert _ir(ALL_FOUR) == _ir(render_protocol(d["model"]))

def test_the_model_carries_the_tolerance():
    assert describe_protocol(ALL_FOUR)["model"]["reward"]["slots"]["dwell_tolerance"] == "100 ms"

def test_the_model_carries_the_inhibit_kind(): ...
def test_the_model_carries_the_block_reseed_list(): ...
def test_the_model_carries_the_seed_scale(): ...

def test_dropping_a_declaration_from_the_model_changes_the_rendered_ir():
    """The guard that makes the four tests above mean something: if describe
    silently dropped a field, the round-trip would still pass whenever render
    also omitted it."""
    model = describe_protocol(ALL_FOUR)["model"]
    del model["reward"]["slots"]["dwell_tolerance"]
    assert _ir(ALL_FOUR) != _ir(render_protocol(model))
```

```python
# tests/test_editor_model_schema.py
def test_a_seed_with_a_scale_validates(): ...
def test_a_seed_with_an_unknown_field_is_still_rejected():
    """The seed object closes with additionalProperties: false; adding scale
    must not open it."""
def test_a_block_with_a_reseed_list_validates(): ...
def test_every_in_repo_protocol_in_the_subset_validates_against_the_model_schema():
    """What the ticket's "fixtures validated against it" asks for, on this
    side of the vendor boundary. Task 12 does the same for refrain-editor."""
    for path in CORPUS:
        d = describe_protocol(path.read_text())
        if d["in_subset"]:
            validate_model(d["model"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_editor_roundtrip.py tests/test_editor_model_schema.py -v -k "tolerance or kind or reseed or scale or validates"; echo "exit=$?"`
Expected: FAIL — `in_subset` is False, or the round-trip IR differs because the declarations were dropped.

- [ ] **Step 3: Extend the model schema**

`src/refrain/editor/protocol-model.schema.json`: `"scale": {"type": "number", "exclusiveMinimum": 0}` inside `$defs/control/properties/seed/properties`; `"reseed": {"type": "array", "items": {"type": "string"}}` inside the `blocks` item's `properties`.

- [ ] **Step 4: Add optional-slot support to `_fill`**

`render.py:23-25`: iterate the catalog's slot definitions rather than the model's slot values, substituting `""` for a slot marked `"optional": true` and absent from the model, and leaving a missing non-optional slot to raise as it does today. Add a unit test in `tests/test_editor_render.py` for each of the three cases: optional and present, optional and absent, non-optional and absent.

- [ ] **Step 5: Add the catalog slots and templates**

`src/refrain/editor/catalog.json`:
- `inhibit.artifact`: a `{"name": "kind", "type": "raw", "optional": true}` slot, and `{kind}` appended to its template after the `action = mute(...)` clause.
- each of the eight `kind: "reward"` blocks (`reward.operant`, `reward.operant_abs`, `reward.operant_compound`, `reward.operant_compound_abs`, `reward.operant_compound_anyof`, `reward.set_all`, `reward.weighted`, `reward.passthrough`): a `{"name": "tolerance", "type": "raw", "optional": true}` slot, and `{tolerance}` inserted immediately after `duration: {dwell}` inside the `dwell(...)` call.

- [ ] **Step 6: Carry the four declarations through describe and render**

- `describe.py:17` (`_seed_view`) and `:37` (`_seed_view_from_ast`): emit `scale` when present. `render.py:28` (`_render_seed`): render it. Both paths matter — `_seed_view_from_ast` exists for a seed the resolver folded out, and a protocol that round-trips through only one of them re-renders with the scale missing.
- `describe.py:318` (`_match_reward_component`, the `inhibit.artifact` arm): set the `kind` slot to `'; kind = "artifact"'` when the body carries one, and omit it otherwise.
- `describe.py:496-560` (the three reward-matching arms, all of which already read `_expr_to_str(_arg(ev, "duration"))`): set the `tolerance` slot to `", tolerance: 100 ms"` from the dwell's `tolerance` argument, and omit it when absent.
- `describe.py:597` (`_match_block`): add `reseed` from the block body, defaulting to `[]`. `render.py:249-252`: render `reseed = [...]` alongside `threshold = [...]`, and only when non-empty.

Everywhere: absent in the source means absent in the model and absent in the rendered output, or the round-trip test on an existing protocol breaks.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_editor_*.py tests/test_ir_corpus_identity.py -v; echo "exit=$?"`
Expected: PASS. Run the whole editor family, not just the new cases — `_fill`, `_seed_view` and `_match_block` are shared by every block in the catalog, and `tests/test_editor_catalog_growth.py` round-trips the weighted-composite and HRV families through them.

- [ ] **Step 8: Commit**

```bash
git add src/refrain/editor/ tests/test_editor_*.py
git commit -m "feat: the four new declarations survive an editor round-trip"
```

---

### Task 12: refrain-editor — resync the vendored schema and validate every fixture

Gate item 6. A second repo, checked out adjacent, on `main` at `aa3dae0`. Its `scripts/sync-vendor.sh` takes a refrain checkout as an argument, so there is no ordering problem — this task runs after Task 11 and nothing in refrain waits on it.

**Files (in refrain-editor):**
- Modify: `vendor/protocol-model.schema.json` and the vendored parity fixtures (both regenerated, not hand-edited)
- Create: `test/vendor-fixtures-validate.test.ts`

**Interfaces:**
- Consumes: `src/refrain/editor/protocol-model.schema.json` from Task 11, via `npm run sync-vendor`.
- Produces: nothing refrain reads.

- [ ] **Step 1: Write the failing test**

The gap found during design: refrain-editor vendors roughly 41 protocol models across 123 fixture files and **not one is passed through `validateModel`**. `test/validate.test.ts` covers hand-written good and bad cases; `test/describe-export.test.ts` validates a single inline protocol.

```ts
// test/vendor-fixtures-validate.test.ts
const fixtures = globSync('vendor/fixtures/**/*.json');

it('vendors a non-trivial number of fixtures', () => {
  // A floor, so a glob that stopped matching fails the build instead of
  // turning the loop below into zero assertions.
  expect(fixtures.length).toBeGreaterThanOrEqual(100);
});

describe.each(fixtures)('%s', (path) => {
  it('its protocol model validates against the vendored schema', () => {
    const model = extractModel(JSON.parse(readFileSync(path, 'utf8')));
    if (model === null) return;           // not every fixture carries one
    expect(() => validateModel(model)).not.toThrow();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails, and find out how**

Run (in the refrain-editor checkout): `npm test -- vendor-fixtures-validate; echo "exit=$?"`
Expected: FAIL. Two failure modes are both informative and must be distinguished before fixing anything: the test harness cannot find or shape the models (fix the test), or models that were never checked turn out to be invalid against the schema (a real pre-existing bug — report it, do not paper over it by loosening the schema).

- [ ] **Step 3: Resync the vendored schema and fixtures**

Run: `npm run sync-vendor -- <path to the refrain worktree>`
Then re-run the test. The schema now carries `seed.scale` and `blocks[].reseed` from Task 11.

- [ ] **Step 4: Run the full editor suite**

Run: `npm test; echo "exit=$?"`
Expected: PASS.

- [ ] **Step 5: Commit (in refrain-editor, on its own branch)**

```bash
git add vendor/ test/vendor-fixtures-validate.test.ts
git commit -m "test: validate every vendored fixture model against the vendored schema"
```

---

### Task 13: Release 0.25.0 and run the whole gate

**Files:**
- Modify: `pyproject.toml:7`, `refrain-core/pyproject.toml:11`
- Modify: `docs/CONFORMANCE.md`, `CHANGELOG.md`

- [ ] **Step 1: Bump both versions together**

`0.24.0` → `0.25.0` in both files. `refrain-core/Cargo.toml` stays at `0.1.0` — `tests/test_version_lockstep.py` compares the two pyproject versions only.

- [ ] **Step 2: Run the version lockstep test**

Run: `python -m pytest tests/test_version_lockstep.py -v; echo "exit=$?"`
Expected: PASS with both at 0.25.0.

- [ ] **Step 3: Write the changelog entry and the conformance note**

`CHANGELOG.md`: the four declarations, each with its default and the sentence that absence reproduces today's behaviour; IR-JSON 0.6 and its behaviour-based predicate; the `seed_report()` `history` addition as additive. `docs/CONFORMANCE.md`: IR 0.6 in the supported-versions list and the new golden-vector families.

- [ ] **Step 4: Run the whole gate**

Each command's exit code captured immediately, never after a pipe, and no `-q` on refrain's suite.

```bash
python -m pytest tests/; echo "exit=$?"                              # gate 1
python refrain-core/tools/check_equivalence.py; echo "exit=$?"       # gates 2, and 4 via verify_fixtures
python -m pytest tests/fuzz/ -v; echo "exit=$?"                      # gate 3
python -m pytest tests/test_ir_corpus_identity.py -v; echo "exit=$?" # gate 4
python -m pytest tests/test_editor_roundtrip.py -v; echo "exit=$?"   # gate 5
python -m pytest tests/test_version_lockstep.py -v; echo "exit=$?"   # gate 7
```

Then, in refrain-editor: `npm test; echo "exit=$?"` (gate 6).

Gates 8, 9 and 10 are test cases rather than commands: a test per row of the §1 forgiveness table (Tasks 3 and 6), holds-gated continuous output sounding through a forgiven dip (Task 4), and the warm-up fail-closed pin (Task 8). Confirm each by name in the suite output rather than assuming the file ran.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml refrain-core/pyproject.toml CHANGELOG.md docs/CONFORMANCE.md
git commit -m "release: v0.25.0 — forgiving holds, artifact gates, block re-seeding"
```

---

## Out of scope, by decision

Recorded here so an executor does not read them as gaps.

| Not doing | Where it went |
|---|---|
| Changing any protocol file, or labelling any live inhibit | Stage 3. Infrastructure now, protocols later. |
| The replay report choosing per-family tolerance values | Stage 3. Needs `tools/session_report/` merged in refrain-protocols (branch `session-report-plan-a55318f5`) and session recordings located, which `lib/sessions/` does not hold. |
| A second, lower threshold line; a depth floor on the tolerance | Measurement killed both — see design §Non-goals. |
| A spindle-detector primitive | Costly, and not needed for feedback. |
| Shortening declared latency below the bandwidth-time floor | A separate argument with its own evidence. |
| Carrying re-seed buffers across a session resume | Documented in Task 10, not implemented. |
| A compile-time *advisory* on an unlabelled inhibit | Left to the builder's judgement on noise. Task 6 ships the *report* instead, which is what the worklist actually needs. |
| Clamping an unscaled seed to its control's declared range | Task 8 explains why: there is no such clamp today, so adding one would move an existing protocol's seeded value with no declaration present. |
| Changing `suppress_on_mute` so an engine acts on it | Task 2 documents the asymmetry rather than quietly fixing it. |
