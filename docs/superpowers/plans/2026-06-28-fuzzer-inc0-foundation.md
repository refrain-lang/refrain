# Fuzzer Increment 0 (foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `refrain fuzz` run over the whole protocol corpus without crashing — turn unsupported shapes into typed, labelled skips, add a batch/dir runner with an aggregate coverage report, and wire it into CI.

**Architecture:** Add an `UnsupportedProtocol(reason)` exception; convert the two dominant `surface.py` crash sites (single-condition reward, center/bandwidth bandpass) into typed skips; introduce a `fuzz/runner.py` that fuzzes one protocol into a `ProtocolOutcome` (FUZZED/SKIPPED/ERRORED) behind a **guarded backstop** wrapping introspection/generation only (never the evaluate→oracle→check loop); refactor single-file `_cmd_fuzz` onto it (reconciling exit codes) and add a batch dispatcher over `nargs='+'` paths with an aggregate report.

**Tech Stack:** Python 3.10+, argparse CLI, pytest (in-process `main([...])` + `capsys`), ruff.

## Global Constraints

- Python floor 3.10 (CI matrix 3.10–3.13). No new third-party dependencies.
- Every source file starts with the existing 2-line Apache header (copy from any `src/refrain/fuzz/*.py`).
- `src/refrain/fuzz/` must stay ruff-clean: `.venv/bin/ruff check src/refrain/fuzz/` → "All checks passed!".
- Tests run via `.venv/bin/python -m pytest tests/fuzz/ -q` and must stay green.
- Single-file exit-code contract (target end state): fuzzed-no-violation → 0; fuzzed-violation → 1; unsupported → 0 (with `SKIPPED (unsupported: <reason>)`); parse/resolve error or missing file → 2; generator bug (`VacuityError`) → 2.
- Batch exit code: 1 iff ≥1 violation OR ≥1 errored file; else 0. Skips never affect the exit.
- Skip reason vocabulary: `single-condition reward`, `center/bandwidth bandpass`, `unclassified (<short detail>)`.
- Reuse over reinvent: move existing helpers, don't reimplement them. Keep the oracle independent (never run the evaluator inside introspection/prediction).

## File structure

- `src/refrain/fuzz/errors.py` (new) — `UnsupportedProtocol`.
- `src/refrain/fuzz/surface.py` (modify) — two raise sites become typed skips.
- `src/refrain/synthetic.py` (modify) — host the shared `channels_for_synthetic(ir)` (moved out of `cli.py` to break the CLI↔runner import cycle).
- `src/refrain/fuzz/runner.py` (new) — `ProtocolOutcome`, status constants, `fuzz_protocol`, `discover_protocols`, `run_batch`, `render_batch_report`, and the moved fuzz-only pipeline helpers.
- `src/refrain/cli.py` (modify) — thin `fuzz` dispatcher (`_fuzz_single` / `_fuzz_batch`), `nargs='+'` positional, exit-code reconciliation; delete the moved helpers.
- `.github/workflows/test.yml` (modify) — add the `fuzz` CI step.
- `docs/superpowers/ci/refrain-protocols-fuzz.md` (new) — ready-to-commit workflow + instructions for the refrain-protocols follow-up PR.
- Tests: `tests/fuzz/test_unsupported.py` (new), `tests/fuzz/test_runner.py` (new), `tests/fuzz/test_batch.py` (new), `tests/fuzz/test_cli_fuzz.py` (extend), `tests/fuzz/test_synthetic_channels.py` (new, small).

Reference corpus fixtures (verified by probe):
- Fuzzable: `bench/protocols/realistic_smr.refrain`, `bench/protocols/micro_05_reward.refrain`, `examples/smr_cz.refrain`.
- Single-condition reward (bare `dwell(above/below)`): `bench/protocols/micro_01_passthrough.refrain`.
- Center/bandwidth bandpass: `examples/othmer_ilf_t3t4.refrain`.
- Unclassified (`unrecognized condition expr IRCall`): `bench/protocols/micro_08_bandpower.refrain`.
- Resolve error without a library loader: `examples/othmer_ilf_cz_pz.refrain` (extends `library/othmer/ilf_base@1`; resolves with `--library examples/library`).

---

### Task 1: `UnsupportedProtocol` + single-condition & center/bandwidth detectors

**Files:**
- Create: `src/refrain/fuzz/errors.py`
- Modify: `src/refrain/fuzz/surface.py` (add import; `_band_from_call` ~line 169-180; `_reward_condition_from_ir` ~line 360-367)
- Test: `tests/fuzz/test_unsupported.py`

**Interfaces:**
- Produces: `refrain.fuzz.errors.UnsupportedProtocol(reason: str)` with attribute `.reason: str`. `surface.build_surface(ir)` now raises it for single-condition / center-bandwidth protocols.

- [ ] **Step 1: Write the failing test**

Create `tests/fuzz/test_unsupported.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Increment 0 detectors: unsupported shapes raise UnsupportedProtocol."""
from __future__ import annotations

from pathlib import Path

import pytest

from refrain.fuzz.errors import UnsupportedProtocol
from refrain.fuzz.surface import build_surface
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ir(rel: str, *, library: str | None = None):
    from refrain.compose import filesystem_loader
    loader = filesystem_loader([REPO_ROOT / library]) if library else None
    return resolve(parse_file(REPO_ROOT / rel), None, parent_loader=loader)


def test_single_condition_reward_raises_typed_skip():
    ir = _ir("bench/protocols/micro_01_passthrough.refrain")
    with pytest.raises(UnsupportedProtocol) as exc:
        build_surface(ir)
    assert exc.value.reason == "single-condition reward"


def test_center_bandwidth_bandpass_raises_typed_skip():
    ir = _ir("examples/othmer_ilf_t3t4.refrain")
    with pytest.raises(UnsupportedProtocol) as exc:
        build_surface(ir)
    assert exc.value.reason == "center/bandwidth bandpass"


def test_supported_protocol_still_builds():
    ir = _ir("bench/protocols/realistic_smr.refrain")
    surface = build_surface(ir)  # no raise
    assert surface.protocol_name


def test_unrecognized_condition_stays_valueerror_not_typed():
    # micro_08_bandpower hits `_condition_from_ir` with a non-leaf IRCall;
    # Increment 0 leaves this as a plain ValueError (-> backstop "unclassified"),
    # NOT one of our typed skips.
    ir = _ir("bench/protocols/micro_08_bandpower.refrain")
    with pytest.raises(ValueError) as exc:
        build_surface(ir)
    assert not isinstance(exc.value, UnsupportedProtocol)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_unsupported.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'refrain.fuzz.errors'`.

- [ ] **Step 3: Create the exception module**

Create `src/refrain/fuzz/errors.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Typed errors for the protocol fuzzer."""
from __future__ import annotations


class UnsupportedProtocol(Exception):
    """A protocol shape the fuzzer cannot yet represent.

    `reason` is a short, stable, feature-mapped string used for the batch
    skip breakdown and the coverage metric (e.g. "single-condition reward").
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


__all__ = ["UnsupportedProtocol"]
```

- [ ] **Step 4: Wire the two detectors in surface.py**

Add the import near the top of `src/refrain/fuzz/surface.py` (after the `from ..ir_json import ir_to_json_obj` line):

```python
from .errors import UnsupportedProtocol
```

Replace the body of `_band_from_call` (currently raising `ValueError` when `band is None`):

```python
def _band_from_call(call: IRCall) -> tuple[float, float]:
    """Read `band: (lo Hz, hi Hz)` from a bandpass call."""
    band = _arg(call, "band")
    if band is None:
        # The center:/bandwidth: declaration form lands here (Increment 2).
        raise UnsupportedProtocol("center/bandwidth bandpass")
    lo, hi = band.elements  # IRTuple of two IRNumberLit (Hz)
    return (float(lo.value), float(hi.value))
```

Replace `_reward_condition_from_ir` so a recognized bare leaf becomes a typed skip:

```python
def _reward_condition_from_ir(ir: IRProtocol) -> ConditionNode:
    event = ir.reward.event
    if isinstance(event, IRCall) and event.callee == "dwell":
        cond = _arg(event, "condition")
        node = _condition_from_ir(cond)  # unrecognized exprs -> ValueError (backstop)
        if isinstance(node, ConditionNode):
            return node
        if isinstance(node, ConditionLeaf):
            # A bare dwell(above/below(...)) reward (Increment 1).
            raise UnsupportedProtocol("single-condition reward")
    raise ValueError("surface: reward.event has no all_of/any_of condition")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_unsupported.py -q`
Expected: PASS (4 tests).

Run: `.venv/bin/python -m pytest tests/fuzz/ -q && .venv/bin/ruff check src/refrain/fuzz/`
Expected: full fuzz suite green; "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/errors.py src/refrain/fuzz/surface.py tests/fuzz/test_unsupported.py
git commit -m "feat(fuzz): UnsupportedProtocol + single-condition/center-bandwidth detectors"
```

---

### Task 2: Move `channels_for_synthetic` to `synthetic.py` (decouple shared helper)

**Files:**
- Modify: `src/refrain/synthetic.py` (add public `channels_for_synthetic`)
- Modify: `src/refrain/cli.py` (delete `_channels_for_synthetic`, import + use the moved one)
- Test: `tests/fuzz/test_synthetic_channels.py`

**Interfaces:**
- Produces: `refrain.synthetic.channels_for_synthetic(ir) -> tuple[str, ...]` — protocol's required channels plus standard ear channels `A1`, `A2`.

**Why:** `_channels_for_synthetic` is used by both `_cmd_run` and the fuzz path. The new `fuzz/runner.py` needs it but must not import `cli.py` (cycle). Hosting it in `synthetic.py` (where `render_scenario` already lives) is the cycle-free home both call sites can share.

- [ ] **Step 1: Write the failing test**

Create `tests/fuzz/test_synthetic_channels.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
from __future__ import annotations

from pathlib import Path

from refrain.parser import parse_file
from refrain.resolver import resolve
from refrain.synthetic import channels_for_synthetic

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_channels_include_requires_and_ears():
    ir = resolve(parse_file(REPO_ROOT / "bench/protocols/realistic_smr.refrain"), None)
    chans = channels_for_synthetic(ir)
    assert "A1" in chans and "A2" in chans
    for c in ir.requires.channels:
        assert c in chans
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/fuzz/test_synthetic_channels.py -q`
Expected: FAIL — `ImportError: cannot import name 'channels_for_synthetic'`.

- [ ] **Step 3: Add the function to synthetic.py**

Append to `src/refrain/synthetic.py` (and add its name to any `__all__` if present):

```python
def channels_for_synthetic(ir) -> tuple[str, ...]:
    """Channels for synthetic sources: everything the protocol's `requires`
    asks for plus the standard ear channels (so `linked_ears` references
    resolve). Falls back to `Cz` when no channels are required."""
    channels = list(ir.requires.channels) or ["Cz"]
    for ear in ("A1", "A2"):
        if ear not in channels:
            channels.append(ear)
    return tuple(channels)
```

- [ ] **Step 4: Update cli.py to use the moved helper**

In `src/refrain/cli.py`: delete the local `def _channels_for_synthetic(ir)` definition (lines ~401-410). Add `channels_for_synthetic` to the existing import from `.synthetic` (or add `from .synthetic import channels_for_synthetic`). Replace both call sites (`_cmd_run` ~line 353 and `_cmd_fuzz` ~line 546) `_channels_for_synthetic(ir)` → `channels_for_synthetic(ir)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_synthetic_channels.py tests/test_cli*.py -q`
Expected: PASS. Then the full suite:
Run: `.venv/bin/python -m pytest -q`
Expected: green (confirms `refrain run --synthetic` unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/refrain/synthetic.py src/refrain/cli.py tests/fuzz/test_synthetic_channels.py
git commit -m "refactor: move channels_for_synthetic into synthetic.py (shared, cycle-free)"
```

---

### Task 3: `runner.py` — outcome model + `fuzz_protocol` with guarded backstop

**Files:**
- Create: `src/refrain/fuzz/runner.py`
- Modify: `src/refrain/cli.py` (delete the moved fuzz-only helpers `_fuzz_corpus`, `_fuzz_collar_samples`, `_fuzz_one_scenario`, `_apply_phase_override`; import what the single-file path still needs from `.fuzz.runner`)
- Test: `tests/fuzz/test_runner.py`

**Interfaces:**
- Consumes: `surface.build_surface`, `errors.UnsupportedProtocol`, `synthetic.channels_for_synthetic`.
- Produces:
  - `ProtocolOutcome` (frozen dataclass): `path: str`, `status: str`, `passed: bool | None`, `reason: str | None`, `report: str | None`.
  - status constants `FUZZED = "fuzzed"`, `SKIPPED = "skipped"`, `ERRORED = "errored"`.
  - `fuzz_protocol(ir, *, path, max_scenarios, chunk_size) -> ProtocolOutcome` (may raise `VacuityError`; never catches scenario-loop exceptions).
  - moved helpers `_build_corpus`, `_collar_samples`, `_run_one_scenario`, `_apply_phase_override`.

- [ ] **Step 1: Write the failing tests**

Create `tests/fuzz/test_runner.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""fuzz_protocol() outcome classification + guarded backstop."""
from __future__ import annotations

from pathlib import Path

import pytest

from refrain.fuzz import runner
from refrain.fuzz.runner import FUZZED, SKIPPED, fuzz_protocol
from refrain.parser import parse_file
from refrain.resolver import resolve

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ir(rel: str):
    return resolve(parse_file(REPO_ROOT / rel), None)


def _run(rel: str, **kw):
    ir = _ir(rel)
    return fuzz_protocol(ir, path=rel, max_scenarios=kw.get("max_scenarios", 2),
                         chunk_size=kw.get("chunk_size", 64))


def test_supported_protocol_is_fuzzed_and_passes():
    out = _run("bench/protocols/realistic_smr.refrain")
    assert out.status == FUZZED
    assert out.passed is True
    assert out.report and "Engine check" in out.report


def test_single_condition_is_skipped_with_typed_reason():
    out = _run("bench/protocols/micro_01_passthrough.refrain")
    assert out.status == SKIPPED
    assert out.reason == "single-condition reward"


def test_unrecognized_condition_is_skipped_unclassified():
    out = _run("bench/protocols/micro_08_bandpower.refrain")
    assert out.status == SKIPPED
    assert out.reason.startswith("unclassified (")


def test_backstop_does_not_swallow_scenario_loop_errors(monkeypatch):
    # An exception inside the evaluate/oracle/check loop must propagate,
    # NOT be reclassified as a skip (that would hide engine bugs).
    monkeypatch.setattr(runner, "predict", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        _run("bench/protocols/realistic_smr.refrain")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'refrain.fuzz.runner'`.

- [ ] **Step 3: Create runner.py (move helpers + implement fuzz_protocol)**

Create `src/refrain/fuzz/runner.py`. Move the four helper bodies verbatim from `cli.py` (renamed as noted) and add the outcome model + `fuzz_protocol`. Note `predict` is imported at module scope so the backstop test can monkeypatch `runner.predict`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Run the fuzzer over a single protocol and classify the outcome.

`fuzz_protocol` introspects + generates behind a guarded backstop (so an
unrepresentable shape becomes a typed SKIP rather than a crash), then runs
the evaluate -> oracle -> check loop OUTSIDE that backstop (so genuine
engine violations and generator bugs surface, never silently skipped)."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..eval_ import eval_protocol
from ..sources import SyntheticSource
from ..synthetic import channels_for_synthetic, render_scenario
from .check import (
    ActualEvent,
    VacuityError,
    check_metamorphic_monotonic,
    check_scenario,
)
from .errors import UnsupportedProtocol
from .generate import (
    generate_characterization_probe,
    generate_directed_scenarios,
    generate_hold_duration_sweep,
    generate_rank_sweep,
)
from .oracle import predict, settle_time_s
from .report import render_report
from .scenario import Verdict
from .surface import build_surface

FUZZED = "fuzzed"
SKIPPED = "skipped"
ERRORED = "errored"

# Introspection/generation failures we treat as "unclassified" skips. NOT a
# blanket `except Exception` — the evaluate/oracle/check loop runs outside this.
_BACKSTOP_ERRORS = (ValueError, KeyError, TypeError, AttributeError, IndexError)


@dataclass(frozen=True, slots=True)
class ProtocolOutcome:
    path: str
    status: str                 # FUZZED | SKIPPED | ERRORED
    passed: bool | None = None  # FUZZED: True=no violation, False=violation
    reason: str | None = None   # SKIPPED/ERRORED: the (short) reason
    report: str | None = None   # FUZZED: full single-file report text


def _short_reason(exc: Exception) -> str:
    msg = (str(exc).splitlines() or [""])[0] or type(exc).__name__
    return msg.removeprefix("surface: ")[:60]


def fuzz_protocol(ir, *, path: str, max_scenarios: int, chunk_size: int) -> ProtocolOutcome:
    """Fuzz one resolved protocol. Raises VacuityError on a generator bug."""
    try:
        surface = build_surface(ir)
        corpus = _build_corpus(surface)
        if max_scenarios > 0:
            corpus = corpus[:max_scenarios]
        collar_samples = _collar_samples(surface, chunk_size)
        channels = channels_for_synthetic(ir)
    except UnsupportedProtocol as exc:
        return ProtocolOutcome(path=path, status=SKIPPED, reason=exc.reason)
    except _BACKSTOP_ERRORS as exc:
        return ProtocolOutcome(
            path=path, status=SKIPPED, reason=f"unclassified ({_short_reason(exc)})"
        )

    # --- evaluate -> oracle -> check: OUTSIDE the backstop ---
    results = []
    all_tags: set[str] = set()
    for scenario in corpus:
        all_tags |= set(scenario.coverage_tags)
        results.append(_run_one_scenario(
            scenario, ir=ir, surface=surface, channels=channels,
            collar_samples=collar_samples, chunk_size=chunk_size,
        ))
    metamorphic = (
        check_metamorphic_monotonic(results, tag_prefix="metamorphic:rank_sweep:")
        + check_metamorphic_monotonic(results, tag_prefix="metamorphic:hold_duration_sweep")
    )
    report = render_report(
        protocol_name=surface.protocol_name, results=results,
        metamorphic_violations=metamorphic, all_coverage_tags=all_tags,
    )
    has_violation = bool(metamorphic) or any(
        r.verdict in (Verdict.MISSED, Verdict.SPURIOUS) for r in results
    )
    return ProtocolOutcome(
        path=path, status=FUZZED, passed=not has_violation, report=report
    )


# --- moved verbatim from cli.py (fuzz-only pipeline helpers) ---

def _build_corpus(surface):
    return (
        list(generate_directed_scenarios(surface))
        + list(generate_characterization_probe(surface))
        + list(generate_rank_sweep(surface))
        + list(generate_hold_duration_sweep(surface))
    )


def _collar_samples(surface, chunk_size: int) -> int:
    fs = surface.sample_rate_hz
    chunk_s = chunk_size / fs
    candidates = [
        settle_time_s(sos=d.sos, tau_s=(d.smooth_tau_ms or 0.0) / 1000.0,
                      chunk_s=chunk_s, fs=fs)
        for d in surface.derives if d.sos is not None
    ]
    collar_s = max(candidates) if candidates else 0.0
    return int(round(collar_s * fs))


def _apply_phase_override(ir, phase_override):
    # MOVE the existing body verbatim from cli.py:_apply_phase_override.
    ...


def _run_one_scenario(scenario, *, ir, surface, channels, collar_samples, chunk_size):
    fs = surface.sample_rate_hz
    scenario_ir = _apply_phase_override(ir, scenario.phase_override)
    gen = render_scenario(scenario, channels=channels)
    source = SyntheticSource(gen, duration_s=scenario.duration_s)
    actual: list[ActualEvent] = []
    for ev in eval_protocol(scenario_ir, source, chunk_size=chunk_size):
        if ev.kind != "event":
            continue
        actual.append(ActualEvent(
            sample=int(round(ev.timestamp_s * fs)), kind=ev.kind, channel=ev.channel,
        ))
    expected = predict(scenario, surface)
    return check_scenario(
        scenario_label=scenario.label, expected=expected, actual=actual, fs=fs,
        collar_samples=collar_samples, coverage_tags=scenario.coverage_tags,
        total_samples=int(round(scenario.duration_s * fs)),
    )


__all__ = [
    "ERRORED", "FUZZED", "SKIPPED", "ProtocolOutcome", "fuzz_protocol",
]
```

> When moving `_apply_phase_override`, copy its real body from `cli.py` (it currently lives at ~line 434) — including any imports it needs (e.g. `dataclasses`); do not leave the `...` placeholder.

- [ ] **Step 4: Delete the moved helpers from cli.py**

Remove `_fuzz_corpus`, `_fuzz_collar_samples`, `_fuzz_one_scenario`, and `_apply_phase_override` from `cli.py`. (The next task rewires `_cmd_fuzz`; until then `_cmd_fuzz` is temporarily broken — its tests are updated in Task 4. Run only the targeted tests in Step 5.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/test_runner.py -q`
Expected: PASS (4 tests).

Run: `.venv/bin/ruff check src/refrain/fuzz/`
Expected: "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/fuzz/runner.py src/refrain/cli.py tests/fuzz/test_runner.py
git commit -m "feat(fuzz): runner.py with fuzz_protocol + guarded backstop (moved pipeline helpers)"
```

---

### Task 4: Refactor single-file `_cmd_fuzz` onto `fuzz_protocol` + reconcile exit codes

**Files:**
- Modify: `src/refrain/cli.py` (`_cmd_fuzz` → thin; new `_fuzz_single(path, args)`; `_parse_resolve_or_report` parse/resolve → exit 2)
- Test: `tests/fuzz/test_cli_fuzz.py` (extend)

**Interfaces:**
- Consumes: `runner.fuzz_protocol`, `runner.FUZZED/SKIPPED`, `check.VacuityError`.
- Produces: `_fuzz_single(path: str, args) -> int` mapping a `ProtocolOutcome` to the single-file exit contract.

- [ ] **Step 1: Write the failing tests**

Replace `tests/fuzz/test_cli_fuzz.py` with:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Tests for the single-file `refrain fuzz` CLI path + exit-code contract."""
from __future__ import annotations

from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SMR = str(REPO_ROOT / "bench" / "protocols" / "realistic_smr.refrain")
SINGLE_COND = str(REPO_ROOT / "bench" / "protocols" / "micro_01_passthrough.refrain")
OTHMER_LIB = str(REPO_ROOT / "examples" / "othmer_ilf_cz_pz.refrain")


def test_supported_protocol_runs_and_reports(capsys):
    rc = main(["fuzz", SMR, "--max-scenarios", "3"])
    combined = "".join(capsys.readouterr())
    assert "What your protocol does" in combined
    assert "Engine check" in combined
    assert rc in (0, 1)


def test_unsupported_protocol_skips_with_exit_zero(capsys):
    rc = main(["fuzz", SINGLE_COND, "--max-scenarios", "3"])
    combined = "".join(capsys.readouterr())
    assert rc == 0
    assert "SKIPPED (unsupported: single-condition reward)" in combined


def test_resolve_error_returns_exit_two(capsys):
    # othmer_ilf_cz_pz `extends` a library with no loader -> ResolveError.
    rc = main(["fuzz", OTHMER_LIB])
    combined = "".join(capsys.readouterr())
    assert rc == 2
    assert "resolve failed" in combined.lower()


def test_missing_file_returns_exit_two(capsys):
    rc = main(["fuzz", "/nonexistent/path.refrain"])
    combined = "".join(capsys.readouterr())
    assert rc == 2
    assert "no such file" in combined.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_cli_fuzz.py -q`
Expected: FAIL — `test_unsupported_protocol_skips_with_exit_zero` (no SKIPPED line yet) and `test_resolve_error_returns_exit_two` (currently exit 1).

- [ ] **Step 3: Reconcile parse/resolve exit codes**

In `cli.py` `_parse_resolve_or_report`, change the two `return 1` after `ParseError`/`ResolveError` to `return 2` (leave the `amp` load error at its current code — out of scope for the contract).

- [ ] **Step 4: Rewrite `_cmd_fuzz` onto `fuzz_protocol`**

Replace the `_cmd_fuzz` body with a dispatcher delegating to `_fuzz_single` (batch added in Task 5):

```python
def _cmd_fuzz(args: argparse.Namespace) -> int:
    return _fuzz_single(args.file, args)


def _fuzz_single(path: str, args: argparse.Namespace) -> int:
    from .fuzz.check import VacuityError
    from .fuzz.runner import FUZZED, SKIPPED, fuzz_protocol

    args = argparse.Namespace(**{**vars(args), "file": path})
    ir = _parse_resolve_or_report(args)
    if isinstance(ir, int):
        return ir
    try:
        outcome = fuzz_protocol(
            ir, path=path, max_scenarios=args.max_scenarios, chunk_size=args.chunk_size,
        )
    except VacuityError as exc:
        print(f"GENERATOR BUG: {exc}", file=sys.stderr)
        return 2
    if outcome.status == SKIPPED:
        print(f"SKIPPED (unsupported: {outcome.reason})")
        return 0
    print(outcome.report, file=sys.stderr)            # FUZZED
    return 0 if outcome.passed else 1
```

(Remove the now-unused imports left behind in `_cmd_fuzz`; keep `Verdict`/`render_report`/`build_surface` imports only if still referenced elsewhere — they are not, so delete them from the old `_cmd_fuzz` scope.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/ -q && .venv/bin/ruff check src/refrain/fuzz/ src/refrain/cli.py`
Expected: green; "All checks passed!".

- [ ] **Step 6: Commit**

```bash
git add src/refrain/cli.py tests/fuzz/test_cli_fuzz.py
git commit -m "feat(fuzz): single-file graceful skip + exit-code contract (parse/resolve -> 2)"
```

---

### Task 5: Batch runner `refrain fuzz <path>...` + aggregate report

**Files:**
- Modify: `src/refrain/cli.py` (argparser `file` → `paths` `nargs='+'`; `_cmd_fuzz` dispatch; `_fuzz_batch`)
- Modify: `src/refrain/fuzz/runner.py` (`discover_protocols`, `run_batch`, `render_batch_report`)
- Test: `tests/fuzz/test_batch.py`

**Interfaces:**
- Produces:
  - `discover_protocols(paths: list[str]) -> list[str]` — sorted, de-duplicated `*.refrain` (dirs walked recursively; files taken as-is).
  - `run_batch(paths, *, amp, library, max_scenarios, chunk_size, resolve_fn) -> list[ProtocolOutcome]`.
  - `render_batch_report(outcomes, total) -> str`.
  - `batch_exit_code(outcomes) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/fuzz/test_batch.py`:

```python
# Copyright 2026 Refrain Language Authors.
# Licensed under the Apache License, Version 2.0 (see LICENSE).
"""Batch/dir runner: aggregate report, coverage, exit codes."""
from __future__ import annotations

import shutil
from pathlib import Path

from refrain.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED = REPO_ROOT / "bench/protocols/realistic_smr.refrain"
UNSUPPORTED = REPO_ROOT / "bench/protocols/micro_01_passthrough.refrain"


def _mixed_dir(tmp_path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    shutil.copy(SUPPORTED, d / "supported.refrain")
    shutil.copy(UNSUPPORTED, d / "skipme.refrain")
    (d / "broken.refrain").write_text("this is not a valid protocol {{{")
    return d


def test_batch_reports_counts_coverage_and_breakdown(tmp_path, capsys):
    rc = main(["fuzz", str(_mixed_dir(tmp_path)), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "fuzzed 1" in out and "skipped 1" in out and "errored 1" in out
    assert "coverage: fuzzed 1 / total 3" in out
    assert "single-condition reward" in out
    assert rc == 1  # the broken/errored file makes the batch fail


def test_batch_exit_zero_when_only_skips(tmp_path, capsys):
    d = tmp_path / "c"
    d.mkdir()
    shutil.copy(SUPPORTED, d / "ok.refrain")
    shutil.copy(UNSUPPORTED, d / "skip.refrain")
    rc = main(["fuzz", str(d), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert rc == 0
    assert "errored 0" in out


def test_batch_aggregates_multiple_paths(tmp_path, capsys):
    rc = main(["fuzz", "bench/protocols", "examples",
               "--library", "examples/library", "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "coverage: fuzzed" in out and "/ total 21" in out
    assert rc == 0  # only skips/known-passes across the real corpus


def test_single_file_path_unchanged(capsys):
    rc = main(["fuzz", str(SUPPORTED), "--max-scenarios", "2"])
    out = "".join(capsys.readouterr())
    assert "Engine check" in out  # single-file report, not the batch summary
    assert rc in (0, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/fuzz/test_batch.py -q`
Expected: FAIL — argparser rejects multiple positionals / no batch summary.

- [ ] **Step 3: Add batch functions to runner.py**

Append to `src/refrain/fuzz/runner.py` (and extend `__all__`):

```python
import os
from collections import Counter


def discover_protocols(paths: list[str]) -> list[str]:
    found: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    if fn.endswith(".refrain"):
                        found.append(os.path.join(root, fn))
        else:
            found.append(p)
    return sorted(dict.fromkeys(found))


def run_batch(paths, *, max_scenarios, chunk_size, resolve_fn) -> list[ProtocolOutcome]:
    """`resolve_fn(path) -> ir | str`: returns the resolved IR, or an error
    string (parse/resolve diagnostic) which becomes an ERRORED outcome."""
    outcomes: list[ProtocolOutcome] = []
    for path in discover_protocols(paths):
        resolved = resolve_fn(path)
        if isinstance(resolved, str):
            outcomes.append(ProtocolOutcome(path=path, status=ERRORED, reason=resolved))
            continue
        try:
            outcomes.append(fuzz_protocol(
                resolved, path=path, max_scenarios=max_scenarios, chunk_size=chunk_size,
            ))
        except VacuityError as exc:
            outcomes.append(ProtocolOutcome(
                path=path, status=ERRORED, reason=f"generator-bug: {_short_reason(exc)}"))
    return outcomes


def batch_exit_code(outcomes) -> int:
    bad = sum(
        1 for o in outcomes
        if o.status == ERRORED or (o.status == FUZZED and o.passed is False)
    )
    return 1 if bad else 0


def render_batch_report(outcomes, total) -> str:
    fuzzed = [o for o in outcomes if o.status == FUZZED]
    skipped = [o for o in outcomes if o.status == SKIPPED]
    errored = [o for o in outcomes if o.status == ERRORED]
    n_pass = sum(1 for o in fuzzed if o.passed)
    n_fail = len(fuzzed) - n_pass
    pct = int(round(100 * len(fuzzed) / total)) if total else 0
    lines = [
        f"fuzzed {len(fuzzed)} (pass {n_pass} / fail {n_fail}) "
        f"/ skipped {len(skipped)} / errored {len(errored)}",
        f"coverage: fuzzed {len(fuzzed)} / total {total} ({pct}%)",
    ]
    if skipped:
        lines.append("skips by reason:")
        for reason, n in sorted(Counter(o.reason for o in skipped).items()):
            lines.append(f"  {reason}: {n}")
    if errored:
        lines.append("errors:")
        for o in errored:
            lines.append(f"  {o.path}: {o.reason}")
    if n_fail:
        lines.append("violations:")
        for o in fuzzed:
            if o.passed is False:
                lines.append(f"  [VIOLATION] {o.path}")
    return "\n".join(lines)
```

- [ ] **Step 4: Wire the dispatcher in cli.py**

Change the argparser positional from `file` to `paths` with `nargs="+"`:

```python
    fuzz_cmd.add_argument(
        "paths", nargs="+",
        help="Protocol file(s) or directory(ies). A directory is walked "
             "recursively for *.refrain; multiple inputs or a directory run "
             "in batch mode with an aggregate report.",
    )
```

Rewrite `_cmd_fuzz` to dispatch, and add `_fuzz_batch`:

```python
def _cmd_fuzz(args: argparse.Namespace) -> int:
    paths = args.paths
    if len(paths) == 1 and not Path(paths[0]).is_dir():
        return _fuzz_single(paths[0], args)
    return _fuzz_batch(paths, args)


def _fuzz_batch(paths: list[str], args: argparse.Namespace) -> int:
    from .fuzz.runner import (
        batch_exit_code, discover_protocols, render_batch_report, run_batch,
    )

    def resolve_fn(path: str):
        ir = _parse_resolve_or_report(argparse.Namespace(**{**vars(args), "file": path}))
        if isinstance(ir, int):
            return f"parse/resolve error (exit {ir})"
        return ir

    total = len(discover_protocols(paths))
    outcomes = run_batch(
        paths, max_scenarios=args.max_scenarios, chunk_size=args.chunk_size,
        resolve_fn=resolve_fn,
    )
    print(render_batch_report(outcomes, total))
    return batch_exit_code(outcomes)
```

> `_parse_resolve_or_report` prints its own diagnostic to stderr; `resolve_fn` maps its int return to a short reason string for the ERRORED bucket. `_fuzz_single` already builds its own `file` namespace (Task 4), so both paths reuse the existing resolver untouched.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/fuzz/ -q && .venv/bin/ruff check src/refrain/fuzz/ src/refrain/cli.py`
Expected: green; "All checks passed!". If `test_batch_aggregates_multiple_paths` shows `total` ≠ 21, adjust the assertion to the actual discovered count and note it (fixtures may have changed); the real assertion is `rc == 0` + a coverage line.

- [ ] **Step 6: Commit**

```bash
git add src/refrain/cli.py src/refrain/fuzz/runner.py tests/fuzz/test_batch.py
git commit -m "feat(fuzz): batch/dir runner with aggregate coverage report + exit contract"
```

---

### Task 6: CI wiring + refrain-protocols handoff doc

**Files:**
- Modify: `.github/workflows/test.yml` (add a `fuzz` step to the `test` job)
- Create: `docs/superpowers/ci/refrain-protocols-fuzz.md`

**Interfaces:** none (CI + docs). Verification is a real local run of the exact CI command.

- [ ] **Step 1: Measure batch wall-clock and pick `CI_CAP`**

Run, timing each, to choose a `--max-scenarios` cap that keeps the gate fast:

```bash
time .venv/bin/python -m refrain.cli fuzz bench/protocols examples \
  --library examples/library --max-scenarios 4
time .venv/bin/python -m refrain.cli fuzz bench/protocols examples \
  --library examples/library --max-scenarios 8
```

Record the wall-clock for each in the commit message. Pick the largest cap whose runtime stays comfortably under the existing `test` job (target < ~60s). Use that number as `CI_CAP` below. Confirm the command exits 0 and prints `coverage: fuzzed 4 / total 21` (or the then-current counts).

- [ ] **Step 2: Add the CI step to test.yml**

In `.github/workflows/test.yml`, inside the `test` job's `steps:`, after "Run pytest", add (replace `CI_CAP` with the measured number):

```yaml
      - name: Fuzz the protocol corpus (gate on violations only)
        run: |
          refrain fuzz bench/protocols examples \
            --library examples/library --max-scenarios CI_CAP
```

(The `refrain` console script is installed by `pip install -e ".[eval,dev]"`. Batch mode exits non-zero only on a genuine violation or an errored file; skips do not fail the build, and the coverage line lands in the CI log.)

- [ ] **Step 3: Write the refrain-protocols handoff doc**

Create `docs/superpowers/ci/refrain-protocols-fuzz.md`:

```markdown
# refrain-protocols: fuzz CI (Increment 0 handoff)

Wire `refrain fuzz` into the refrain-protocols repo CI. This file is the
ready-to-commit workflow + invocation; open it as a separate PR in
refrain-protocols once a refrain version exposing batch mode is installable.

## Workflow (`.github/workflows/fuzz.yml`)

\```yaml
name: fuzz
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
jobs:
  fuzz:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install refrain (pin to a version with batch fuzz)
        run: |
          python -m pip install --upgrade pip
          pip install "refrain[eval]>=0.12"   # the version that ships batch fuzz
      - name: Fuzz the protocol library (gate on violations only)
        run: refrain fuzz protocols lib drafts --max-scenarios 8
\```

## Notes
- Gate is violations-only; skips are reported (coverage line in the log), not failed.
- Bump the `--max-scenarios` cap / pin as coverage and runtime evolve.
- As later increments unlock features, the `coverage: fuzzed N / total M` line rises.
```

- [ ] **Step 4: Verify the exact CI command locally**

Run: `refrain fuzz bench/protocols examples --library examples/library --max-scenarios CI_CAP; echo "exit=$?"`
Expected: `exit=0`, a `coverage: fuzzed … / total 21` line, and a `skips by reason:` breakdown.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/test.yml docs/superpowers/ci/refrain-protocols-fuzz.md
git commit -m "ci(fuzz): gate refrain corpus on violations; refrain-protocols handoff doc"
```

---

## Self-review notes

- **Spec coverage:** UnsupportedProtocol (T1) ✓; explicit detectors single-condition+center/bandwidth (T1) ✓; guarded backstop wrapping introspect/generate only (T3, with a regression test that loop errors propagate) ✓; single-file skip+exit contract (T4) ✓; batch runner + aggregate report + coverage + by-reason breakdown (T5) ✓; batch exit 1 on violation-or-errored (T5) ✓; CI wiring + coverage in log (T6) ✓; refrain-protocols handoff doc (T6) ✓; `--max-scenarios` honoured per protocol + measured CI cap (T5/T6) ✓; `nargs='+'` generalization (T5) ✓.
- **Out of scope (unchanged):** no new feature *support* (Inc 1+), no refrain-protocols PR, no regression baseline, no committed coverage file / PR-comment bot.
- **Type consistency:** `ProtocolOutcome(path, status, passed, reason, report)` and constants `FUZZED/SKIPPED/ERRORED` used identically across T3/T4/T5; `fuzz_protocol(ir, *, path, max_scenarios, chunk_size)` signature stable; `channels_for_synthetic` single definition (T2) consumed by cli + runner.
- **Known follow-through:** T3 leaves `_cmd_fuzz` temporarily broken until T4 — run only targeted tests between those tasks (called out in T3 Step 4). The `_apply_phase_override` body must be copied verbatim from cli.py, not left as `...`.
```
