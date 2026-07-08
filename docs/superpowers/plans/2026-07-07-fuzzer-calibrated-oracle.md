# Fuzzer Calibrated (Differential) Oracle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fuzzer's oracle agree with the engine on near/below-noise-floor thresholds by feeding the reward-semantic prediction the engine's real per-sample envelope instead of an idealized one.

**Architecture:** A differential oracle. The fuzzer runs the engine once per scenario with `record_streams=True`, harvests each derive's **bit-exact per-sample envelope** from `last_streams()`, and feeds it to the oracle's existing, independently-authored reward-semantic layers (threshold/percentile/condition/dwell/reward). The DSP (envelope) is shared with the engine; layers 2–4 stay independent. Two tiny coupled fixes ride along.

**Tech Stack:** Python 3.10+, numpy/scipy, pytest, ruff.

## Global Constraints

- Python floor 3.10. No new third-party dependencies.
- Source files start with the 2-line Apache header.
- `src/refrain/fuzz/` ruff-clean; CI gate `ruff check src/refrain --select F,E9` clean.
- Tests via `.venv/bin/python -m pytest tests/fuzz/ -q`; keep green.
- The oracle's reward-semantic layers (`_ordinal_percentile_truth`, `predict_absolute_leaf_truth`, `_walk_condition`, `apply_dwell`, phase muting) MUST remain **independently authored** from the engine's eval code — never "fixed to match the engine." (Reference-drift discipline; enforce in review.)
- The real envelope MUST be the engine's own per-sample stream (`Evaluator(record_streams=True).last_streams()[<derive>]`), bit-exact — NOT per-chunk taps, NOT a scipy re-implementation, NOT a rebuilt impl chain.
- The 7 currently-fuzzed protocols (`realistic_smr`, `smr_cz`, `smr_cz_brainbit`, `micro_05_reward`, and the Inc-1 `micro_single_above/below`, `micro_center_bandwidth`) MUST stay clean (no regression from the clear-margin cases).
- Differential-oracle sharing is envelope-only: the oracle still computes threshold/percentile/condition/dwell **itself** from the real envelope (do NOT consume the engine's threshold or reward streams as the prediction).

## File structure

- `src/refrain/fuzz/runner.py` — `_run_one_scenario` sources the real per-sample envelope from the engine run; `run_batch` gains skip-not-crash. New internal helper `_engine_run_with_streams`.
- `src/refrain/fuzz/oracle.py` — `predict` accepts real envelopes; `_predicted_envelope_timeline` / `_noise_floor_envelope` retired (or bypassed).
- `src/refrain/fuzz/surface.py` — `_threshold_surface` absolute branch resolves control-refs.
- `docs/superpowers/ci/calibrated-oracle-reprobe.md` — the corpus re-probe result.
- Tests: `tests/fuzz/test_oracle_realenv.py` (new), `test_surface.py` / `test_batch.py` (extend), `test_oracle_*` (adjust for the new envelope source).

Reference (verified): `last_streams()` keys are bare derive names; `Evaluator(ir, source, record_streams=True)`; `step_chunk(chunk) -> list[Event]`; `source.iter_chunks(chunk_size)`.

---

### Task 0: GATING validation — real-envelope oracle clean-rate (throwaway harness)

**This task decides whether the architecture proceeds.** It measures the clean-rate on the failing corpus BEFORE any production change. If it fails, later tasks do not run as written.

**Files:**
- Create: `scratch/validate_realenv.py` (throwaway; not committed to src/tests)

**Interfaces:** none (measurement only).

- [ ] **Step 1: Write the validation harness**

Create `scratch/validate_realenv.py`. For each target protocol it: builds the surface (with control-ref absolute resolved via a local patch), builds the corpus, and for each scenario runs the engine with `record_streams=True` to harvest the real per-sample envelope, then feeds that envelope to `oracle.predict` (via a monkeypatch of `_predicted_envelope_timeline` returning the harvested per-derive array), then `check_scenario` against the engine's actual events. Count PASS / SPURIOUS / MISSED / VACUITY per protocol.

```python
import glob, numpy as np
from pathlib import Path
import refrain.fuzz.surface as S
from refrain.fuzz.surface import ThresholdSurface, _arg, _first_positional, _resolve_control_default, _strip_prefix
from refrain.ir import IRNumberLit, IRControlRef
_o = S._threshold_surface
def _patch(t, ir):
    c = t.threshold_call
    if c.callee == "absolute":
        v = _arg(c, "value") or _first_positional(c)
        uv = float(v.value) if isinstance(v, IRNumberLit) else (_resolve_control_default(ir, v) if isinstance(v, IRControlRef) else None)
        return ThresholdSurface(name=t.name, signal=_strip_prefix(t.signal), kind="absolute", absolute_uv=uv)
    return _o(t, ir)
S._threshold_surface = _patch

from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.compose import filesystem_loader
from refrain.fuzz.surface import build_surface
from refrain.fuzz.runner import _build_corpus, _apply_phase_override
from refrain.synthetic import channels_for_synthetic, render_scenario
from refrain.sources import SyntheticSource
from refrain.eval_ import Evaluator
import refrain.fuzz.oracle as O
from refrain.fuzz.check import check_scenario, ActualEvent, VacuityError

RP = "/Users/jcroall/git/refrain-protocols"
loader = filesystem_loader([Path(RP), Path(RP) / "lib"])
CS = 64

def engine_envelope_and_events(ir, surf, sc):
    fs = surf.sample_rate_hz
    src = SyntheticSource(render_scenario(sc, channels=channels_for_synthetic(ir)), duration_s=sc.duration_s)
    ev = Evaluator(_apply_phase_override(ir, sc.phase_override), src, record_streams=True)
    env = {d.name: [] for d in surf.derives}
    events = []
    for ch in src.iter_chunks(CS):
        out = ev.step_chunk(ch)
        streams = ev.last_streams()
        for d in surf.derives:
            arr = streams.get(d.name)
            if arr is not None:
                env[d.name].extend(np.asarray(arr).tolist())
        for e in out:
            if getattr(e, "kind", "") == "event":
                events.append(ActualEvent(sample=int(round(e.timestamp_s * fs)), kind="event", channel=e.channel))
    n = int(round(sc.duration_s * fs))
    env = {k: (v[:n] + [v[-1]] * (n - len(v)) if len(v) < n else v[:n]) for k, v in env.items() if v}
    return env, events

def verdict(ir, surf, sc):
    fs = surf.sample_rate_hz
    real_env, actual = engine_envelope_and_events(ir, surf, sc)
    _pe = O._predicted_envelope_timeline
    O._predicted_envelope_timeline = lambda d, scn, nn, ff: list(real_env.get(d.name, [O._noise_floor_envelope(d, ff)] * nn))[:nn]
    try:
        exp = O.predict(sc, surf)
        r = check_scenario(scenario_label=sc.label, expected=exp, actual=actual, fs=fs,
                           collar_samples=int(0.5 * fs), coverage_tags=sc.coverage_tags,
                           total_samples=int(round(sc.duration_s * fs)))
        return r.verdict.name
    except VacuityError:
        return "VACUITY"
    finally:
        O._predicted_envelope_timeline = _pe
```

Then a driver that iterates the target list and prints a per-protocol PASS/violation summary + an aggregate clean-rate.

- [ ] **Step 2: Run on the 31 dirty/percentile protocols + the 7 clear-margin regressors**

The target list: the 13 near-floor absolute + 18 percentile single-leaf from the re-probe, plus the 7 currently-fuzzed protocols (bench/protocols + examples paths). Run:

`.venv/bin/python scratch/validate_realenv.py`

Record: (a) clean-rate on the 31, (b) any regression among the 7 (must be zero), (c) wall-clock, (d) the top residual-failure reasons.

- [ ] **Step 3: Decision gate**

- If clean-rate on the 31 is high (target ≥ ~80%) AND the 7 regressors stay clean → **proceed to Task 1**. Record the numbers.
- If regressors regress or clean-rate is low → **STOP and report**: the residuals identify which layer-2–4 assumption also needs calibrating (e.g. the settle-collar width vs the real envelope's transient, or per-sample vs per-chunk percentile window edges). Do not proceed with the production change until the residual cause is understood. This is the gate; a failure here is a successful outcome (cheap course-correction), not a task failure.

- [ ] **Step 4: Commit the recorded numbers only**

The harness is throwaway (`scratch/` is git-ignored). Commit a short note capturing the measured clean-rate to the ledger / a scratch report; no src changes yet.

---

### Task 1: Resolve control-ref absolute thresholds (`surface.py`)

**Files:**
- Modify: `src/refrain/fuzz/surface.py` (`_threshold_surface`, absolute branch)
- Test: `tests/fuzz/test_surface.py`

**Interfaces:**
- Produces: `_threshold_surface` populates `absolute_uv` for `absolute(value: <control>)` and `absolute(<control>)`, mirroring the percentile branch's control-ref resolution.

- [ ] **Step 1: Write the failing test**

A `refrain-protocols` baseline protocol declares `absolute(value: thr_uv)` with `thr_uv` default `2.0 uV`. Add to `tests/fuzz/test_surface.py`:

```python
def test_absolute_threshold_resolves_control_ref():
    from refrain.parser import parse_file
    from refrain.resolver import resolve
    from refrain.compose import filesystem_loader
    from pathlib import Path
    from refrain.fuzz.surface import build_surface
    RP = Path("/Users/jcroall/git/refrain-protocols")
    loader = filesystem_loader([RP, RP / "lib"])
    ir = resolve(parse_file(RP / "protocols/alpha_up_pz_baseline.refrain"), None, parent_loader=loader)
    surf = build_surface(ir)
    thr = next(t for t in surf.thresholds if t.kind == "absolute")
    assert thr.absolute_uv == 2.0
```

(If the refrain-protocols path is unavailable in CI, add a committed minimal fixture `bench/protocols/micro_absolute_control_thr.refrain` — `absolute(value: thr_uv)`, `thr_uv default 8 uV` — and assert `== 8.0`. Prefer the committed fixture so the test isn't path-dependent; use it as the primary assertion.)

- [ ] **Step 2: Run it — FAIL** (`absolute_uv` is `None`).

Run: `.venv/bin/python -m pytest tests/fuzz/test_surface.py::test_absolute_threshold_resolves_control_ref -q`

- [ ] **Step 3: Implement**

In `_threshold_surface`, replace the absolute branch:

```python
    if kind == "absolute":
        val = _arg(call, "value") or _first_positional(call)
        if isinstance(val, IRNumberLit):
            absolute_uv = float(val.value)
        elif isinstance(val, IRControlRef):
            absolute_uv = _resolve_control_default(ir, val)
        else:
            absolute_uv = None
        return ThresholdSurface(
            name=t.name, signal=signal, kind="absolute", absolute_uv=absolute_uv
        )
```

(`_arg`, `_first_positional`, `_resolve_control_default`, `IRControlRef` are already imported/defined in surface.py.)

- [ ] **Step 4: Run it — PASS.** Then full fuzz suite green.

Run: `.venv/bin/python -m pytest tests/fuzz/ -q`

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/surface.py tests/fuzz/test_surface.py bench/protocols/micro_absolute_control_thr.refrain
git commit -m "feat(fuzz): resolve control-ref absolute thresholds (mirror percentile branch)"
```

---

### Task 2: Batch skip-not-crash (`runner.py`)

**Files:**
- Modify: `src/refrain/fuzz/runner.py` (`run_batch`)
- Test: `tests/fuzz/test_batch.py`

**Interfaces:**
- Consumes: `ProtocolOutcome`, `ERRORED`, `fuzz_protocol`, `VacuityError` (existing).
- Produces: `run_batch` never propagates a per-protocol exception; any exception → an `ERRORED` outcome so the batch completes.

- [ ] **Step 1: Write the failing test**

An evaluator-setup error (a montage needing a channel the synthetic source lacks) currently aborts the whole batch. Add to `tests/fuzz/test_batch.py`:

```python
def test_batch_eval_error_becomes_errored_not_crash(tmp_path, capsys):
    import shutil
    d = tmp_path / "c"; d.mkdir()
    shutil.copy(REPO_ROOT / "bench/protocols/realistic_smr.refrain", d / "ok.refrain")
    # a protocol whose montage needs a channel the synthetic source won't have
    (d / "needs_c3.refrain").write_text(
        'protocol "needs_c3" {\n'
        '  requires { sample_rate = ">= 256 Hz"; channels = ["Cz"] }\n'
        '  input "raw" { montage = bipolar(plus: "C3", minus: "Cz") }\n'
        '  derive "env" { from = "raw"\n'
        '    pipeline = [ bandpass(band: (12 Hz, 15 Hz), order: 4), hilbert(), magnitude(), smooth(tau: 250 ms) ] }\n'
        '  threshold "t" { signal = "env"; type = absolute(8 uV) }\n'
        '  reward { event = dwell(condition: above("env", "t"), duration: 250 ms) }\n'
        '  output { audio_chime = reward.event }\n'
        '  session { phases = [ phase { name = "training"; duration = 30 min } ] }\n'
        '}\n')
    rc = main(["fuzz", str(d), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "errored 1" in out       # the batch completed and reported it
    assert "fuzzed 1" in out        # the good protocol still fuzzed
    assert rc == 1                  # errors keep the build red
```

(`REPO_ROOT`, `main` already imported in test_batch.py.)

- [ ] **Step 2: Run it — FAIL** (the batch aborts with a traceback instead of reporting `errored 1`).

- [ ] **Step 3: Implement**

In `run_batch`, broaden the per-protocol guard from `VacuityError`-only to any exception:

```python
        try:
            outcomes.append(fuzz_protocol(
                resolved, path=path, max_scenarios=max_scenarios, chunk_size=chunk_size,
            ))
        except VacuityError as exc:
            outcomes.append(ProtocolOutcome(
                path=path, status=ERRORED, reason=f"generator-bug: {_short_reason(exc)}"))
        except Exception as exc:  # noqa: BLE001 — batch must never abort on one protocol
            outcomes.append(ProtocolOutcome(
                path=path, status=ERRORED, reason=f"eval-error: {_short_reason(exc)}"))
```

Single-file mode is unchanged (it still surfaces the exception for debugging).

- [ ] **Step 4: Run it — PASS.** Full fuzz suite green.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/runner.py tests/fuzz/test_batch.py
git commit -m "fix(fuzz): batch classifies per-protocol eval errors as ERRORED (never aborts)"
```

---

### Task 3: Real-envelope oracle — production wiring

**Files:**
- Modify: `src/refrain/fuzz/runner.py` (`_run_one_scenario` sources the real envelope from the engine run), `src/refrain/fuzz/oracle.py` (`predict` accepts real envelopes)
- Test: `tests/fuzz/test_oracle_realenv.py` (new)

**Interfaces:**
- Consumes: Task 0's validated mechanism; `Evaluator(record_streams=True)`, `last_streams()`.
- Produces: `predict(scenario, surface, *, real_envelopes)` where `real_envelopes: dict[str, list[float]]` maps derive name → per-sample envelope; `_run_one_scenario` runs the engine with `record_streams=True`, harvests envelopes + events in one pass, and passes the envelopes to `predict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/fuzz/test_oracle_realenv.py
from pathlib import Path
from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.fuzz.surface import build_surface
from refrain.fuzz.runner import fuzz_protocol, FUZZED
REPO_ROOT = Path(__file__).resolve().parents[2]

def test_near_floor_absolute_fuzzes_clean():
    # A committed near-floor fixture: above(env, 3 uV) on a band whose quiet
    # envelope straddles ~3 uV — dirty under the idealized oracle, clean under
    # the real-envelope oracle.
    ir = resolve(parse_file(REPO_ROOT / "bench/protocols/micro_near_floor.refrain"), None)
    out = fuzz_protocol(ir, path="near_floor", max_scenarios=40, chunk_size=64)
    assert out.status == FUZZED
    assert out.passed is True
```

Create `bench/protocols/micro_near_floor.refrain`: single `above(env, absolute(3 uV))` on a 12–15 Hz band (a threshold low enough that the idealized 2.0-µV-floor oracle mispredicts but the real envelope resolves). The exact threshold is tuned during implementation so the test is a genuine near-floor case that is dirty pre-change and clean post-change (verify both).

- [ ] **Step 2: Run it — FAIL** (dirty under the current idealized oracle: SPURIOUS/violation).

- [ ] **Step 3: Implement the envelope source + predict signature**

In `runner.py`, replace `_run_one_scenario`'s `eval_protocol` call with a single engine run that records streams and harvests both events and per-derive per-sample envelopes (mirror Task 0's `engine_envelope_and_events`, productionized): build `Evaluator(scenario_ir, source, record_streams=True)`, loop `source.iter_chunks(chunk_size)` calling `step_chunk`, accumulate `last_streams()[d.name]` per derive and the emitted events; pad/truncate each envelope to `total_samples`. Pass `real_envelopes=<dict>` to `predict`.

In `oracle.py`, change `predict(scenario, surface)` → `predict(scenario, surface, *, real_envelopes)`. Replace Step 1's `env_per_derive = {d.name: _predicted_envelope_timeline(...)}` with `env_per_derive = real_envelopes` (each already per-sample). Delete `_predicted_envelope_timeline` and `_noise_floor_envelope` (retire the idealized path) — grep for other callers first and update them. Downstream (leaf truth, percentile rank, condition, dwell, collar, muting) is unchanged.

- [ ] **Step 4: Run it — PASS.** Then the clear-margin regression:

Run: `.venv/bin/python -m pytest tests/fuzz/ -q`
Expected: green — the 7 clear-margin protocols still fuzz clean (their envelopes are unchanged in the regions that matter; the real envelope only differs near the floor).

- [ ] **Step 5: Run ruff** — `.venv/bin/ruff check src/refrain/fuzz/` → "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/runner.py src/refrain/fuzz/oracle.py tests/fuzz/test_oracle_realenv.py bench/protocols/micro_near_floor.refrain
git commit -m "feat(fuzz): differential oracle — predict from the engine's real per-sample envelope"
```

---

### Task 4: Percentile clean + metamorphic on the real envelope

**Files:**
- Modify: `tests/fuzz/test_oracle_realenv.py`, any `test_oracle_*` that constructed the idealized envelope directly (adjust to the new `predict` signature)
- Test: same

**Interfaces:**
- Consumes: Task 3's `predict(..., real_envelopes=...)`.

- [ ] **Step 1: Write the failing test**

```python
def test_percentile_single_leaf_fuzzes_clean():
    ir = resolve(parse_file(REPO_ROOT / "bench/protocols/micro_single_pct.refrain"), None)
    out = fuzz_protocol(ir, path="pct", max_scenarios=40, chunk_size=64)
    assert out.status == FUZZED
    assert out.passed is True
```

(`micro_single_pct.refrain` exists from Inc 1 — currently skipped as `single percentile-leaf reward (needs calibrated oracle)`. This task also removes that skip reason from the detector so percentile single-leaf is now supported — update `surface._classify_single_leaf` to no longer raise for `percentile`, and update the Inc-1 test that asserted that skip reason.)

- [ ] **Step 2: Run it — FAIL** (still skipped / or dirty).

- [ ] **Step 3: Implement**

Remove the `percentile` skip in `_classify_single_leaf` (percentile single-leaf is now supported by the calibrated oracle). Fix any `test_oracle_*` broken by the `predict` signature change by passing a `real_envelopes` dict (for unit tests that don't run the engine, construct a synthetic per-sample envelope array directly). Update the Inc-1 `test_percentile_single_leaf_reason` (it asserted the now-removed skip) to instead assert the protocol fuzzes.

- [ ] **Step 4: Run it — PASS.** Confirm metamorphic still holds on a newly-clean protocol (the existing rank/hold sweep checks run as part of the corpus; no new assertion needed if they pass). Full suite green; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/refrain/fuzz/surface.py tests/fuzz/
git commit -m "feat(fuzz): percentile single-leaf now fuzzable via the calibrated oracle"
```

---

### Task 5: Corpus re-probe + coverage

**Files:**
- Modify: `tests/fuzz/test_batch.py` (coverage counts)
- Create: `docs/superpowers/ci/calibrated-oracle-reprobe.md`

**Interfaces:** none new — verifies the whole increment end-to-end.

- [ ] **Step 1: Re-probe refrain-protocols**

Run the batch over `protocols` + `drafts` (with `--library`), capturing fuzzed/skipped/errored + by-reason breakdown. Confirm: the near-floor absolute + percentile protocols now fuzz clean; the batch runs to completion (no aborts — Task 2); errored count is only genuinely-broken protocols.

- [ ] **Step 2: Record the unlock**

Create `docs/superpowers/ci/calibrated-oracle-reprobe.md` with before (Inc 1: fuzzed ~7, many dirty/skipped) vs after (fuzzed N), the by-reason breakdown, and the remaining long tail (coherence/weighted/inhibit/bandpower/staged). This is the roadmap's re-probe gate; state whether refrain-protocols CI is now wire-able.

- [ ] **Step 3: Update the refrain-repo batch coverage assertion**

`test_batch.py`'s corpus test will show a higher fuzzed count (the Inc-1 percentile fixture `micro_single_pct` + the new near-floor fixture now fuzz). Set the exact numbers from the observed report; run the failing assertion first, then set to the observed values.

- [ ] **Step 4: Full suite + CI gate**

Run: `.venv/bin/python -m pytest tests/fuzz/ -q` → green.
Run: `.venv/bin/ruff check src/refrain --select F,E9` → clean; `.venv/bin/ruff check src/refrain/fuzz/` → clean.

- [ ] **Step 5: Commit**

```bash
git add tests/fuzz/test_batch.py docs/superpowers/ci/calibrated-oracle-reprobe.md
git commit -m "test(fuzz): calibrated-oracle corpus re-probe + coverage"
```

---

## Self-review notes

- **Spec coverage:** differential oracle via real per-sample envelope (T0 validate, T3 build) ✓; layers 2–4 kept independent (envelope-only sharing, Global Constraints + T3) ✓; bit-exact via `record_streams`/`last_streams` (T0/T3) ✓; gating validation first (T0) ✓; resolve control-ref absolute (T1) ✓; batch skip-not-crash (T2) ✓; percentile unlock (T4) ✓; clear-margin regression (T0 gate + T3/T4 full-suite) ✓; re-probe + coverage (T5) ✓; reference-drift discipline (Global Constraints) ✓.
- **Gate honesty:** Task 0 is a real go/no-go; a low clean-rate STOPS the plan (documented), not papered over. Tasks 1–2 (the coupled fixes) are independent of the gate and could land regardless.
- **Known validate-at-build:** the `micro_near_floor.refrain` threshold (T3) and the exact re-probe/coverage counts (T5) are tuned/observed against their tests, not asserted blind. Delete `_predicted_envelope_timeline` only after grepping all callers (T3 Step 3).
- **Performance risk:** T0 measures wall-clock; if the engine-run-with-streams roughly doubles cost on an already-slow suite and is prohibitive, that surfaces at the gate before the production change.
