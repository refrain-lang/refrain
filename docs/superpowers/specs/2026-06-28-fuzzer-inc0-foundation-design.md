# Protocol fuzzer — Increment 0: foundation (skip + batch + CI) — design

> Status: approved design (brainstorm complete), ready for the TDD plan.
> Parent: [[2026-06-28-fuzzer-parity-roadmap-design]] (Increment 0).
> Builds on the protocol fuzzer v1 (`refrain fuzz`, PR #49) on Refrain v0.11.0.

## Goal

Lay the foundation that lets the fuzzer run over the **whole** protocol corpus
without crashing, so every later increment visibly shrinks a skip list instead of
adding a new crash. Concretely, Increment 0 delivers:

1. A typed `UnsupportedProtocol(reason)` exception and a **graceful-skip** path in
   the CLI (unsupported shape → report and exit 0, never crash).
2. A **batch/dir runner** `refrain fuzz <path>...` that walks directories, fuzzes
   each protocol, and prints an aggregate report with a coverage metric and a
   by-reason skip breakdown.
3. **CI wiring** in `refrain` (this PR) that runs the batch over the protocol
   corpus and gates on genuine violations only, plus a ready-to-commit workflow
   file + instructions for `refrain-protocols` (its PR is a separate follow-up).

Increment 0 does **not** make any currently-skipping protocol fuzzable — it only
turns crashes into typed, labelled skips. Adding actual support for
single-condition reward, center/bandwidth bandpass, etc. is Increments 1+.

## Decisions locked in brainstorming

- **Skip classification = hybrid.** Explicit detectors raise *specific* reasons for
  the two dominant unsupported shapes (single-condition reward; center/bandwidth
  bandpass). A **guarded backstop** around introspection/generation only catches
  remaining failures and labels them `unsupported: unclassified (<reason>)`. The
  backstop never wraps the evaluator/oracle/check comparison, so engine bugs still
  surface as real violations.
- **refrain-protocols CI = workflow file now, PR later.** Wire CI fully in this
  repo; ship a committed, ready-to-use workflow + instructions for
  `refrain-protocols`; open that repo's PR as a separate follow-up.
- **Coverage metric = batch stdout + CI log.** Print `coverage: fuzzed N / total M
  (P%)` plus the by-reason skip breakdown in the batch report. No committed report
  file or PR-comment bot in this increment.
- **Exit codes = align to the roadmap contract.** Reconcile parse/resolve failures
  from today's exit 1 → exit 2, so the single-file contract is exactly: unsupported
  → 0, violation → 1, parse/resolve/missing-file → 2, generator bug → 2.

## Single-file contract: `refrain fuzz <file>`

| Outcome | Exit | Output |
|---|--:|---|
| Fuzzed, no violation | 0 | normal report |
| Fuzzed, MISSED/SPURIOUS/metamorphic violation | 1 | report + violation detail |
| **Unsupported shape** (`UnsupportedProtocol`) | **0** | `SKIPPED (unsupported: <reason>)` |
| Parse / resolve error | **2** (was 1) | diagnostic |
| Missing file | 2 | diagnostic |
| Generator bug (`VacuityError`) | 2 | `GENERATOR BUG: …` |

The only behavioural changes to the existing single-file path are (a) the new
`UnsupportedProtocol` → exit-0 SKIPPED branch and (b) parse/resolve → exit 2.

## Architecture

### `UnsupportedProtocol` exception — `src/refrain/fuzz/errors.py` (new)

```python
class UnsupportedProtocol(Exception):
    """A protocol shape the fuzzer cannot yet represent. Carries a stable,
    feature-mapped `reason` string used for the skip breakdown / coverage."""
    def __init__(self, reason: str): ...
    reason: str
```

Reasons are short, stable, and map to roadmap increments so the skip breakdown
tracks coverage by feature. Initial vocabulary:

- `single-condition reward` (Increment 1)
- `center/bandwidth bandpass` (Increment 2)
- `unclassified (<short detail>)` — backstop bucket; later increments reclassify
  entries out of it as they add explicit detectors + support.

### Explicit detectors — `src/refrain/fuzz/surface.py`

Two existing `ValueError` raise-sites become typed, specific skips:

- `_reward_condition_from_ir` (currently raises "reward.event has no
  all_of/any_of condition" at line ~367): when the reward condition is a bare
  recognizable leaf (`dwell(above/below(...))` rather than `all_of`/`any_of`),
  raise `UnsupportedProtocol("single-condition reward")`.
- `_band_from_call` (currently raises at line ~175 for the center/bandwidth form):
  raise `UnsupportedProtocol("center/bandwidth bandpass")`.

These are *recognition only*. Inc 1/2 replace the raise with real support.

### Guarded backstop — in the CLI runner, not in surface.py

A single helper fuzzes one protocol and classifies the outcome:

```
fuzz_protocol(path, opts) -> ProtocolOutcome
  FUZZED(pass|violation)            # ran the full pipeline + checks
  SKIPPED(reason)                   # UnsupportedProtocol OR guarded backstop
  ERRORED(kind)                     # parse/resolve error, missing file
```

The backstop wraps **only** the introspect + corpus-generation stages
(`build_surface`, `_fuzz_corpus`, collar/channel prep). `UnsupportedProtocol`
from those stages → `SKIPPED(reason)`. The legacy `ValueError`/`KeyError`/etc.
still thrown by not-yet-converted introspection paths → `SKIPPED("unclassified
(<exc msg>)")`. The per-scenario evaluate → oracle → check loop is **outside** the
backstop: its exceptions, `VacuityError`, and MISSED/SPURIOUS verdicts keep their
existing (violation / generator-bug) semantics. This is what keeps engine bugs
from being silently swallowed as skips.

`_cmd_fuzz` (single-file) is refactored to call `fuzz_protocol` and map its
outcome to the contract table above.

### Batch runner — `refrain fuzz <path>...`

- The positional becomes `nargs='+'`, accepting one or more files/dirs (a small
  generalization of the roadmap's `<dir>`; lets CI cover refrain's two corpus dirs
  — `bench/protocols` + `examples` — and refrain-protocols' `protocols/`+`lib/`+
  `drafts/` in one aggregated report). Each dir is walked recursively for
  `*.refrain`; files are taken as-is. A single file path keeps today's behaviour
  and output (no regression for `refrain fuzz foo.refrain`).
- Multiple inputs (or any directory) trigger **batch mode**: run `fuzz_protocol`
  on each, collect outcomes, and print an aggregate report:

  ```
  fuzzed 7 (pass 7 / fail 0) / skipped 14 / errored 0
  coverage: fuzzed 7 / total 21 (33%)
  skips by reason:
    single-condition reward      9
    center/bandwidth bandpass    3
    unclassified (…)             2
  ```

- **Batch exit code:** `1` iff at least one **violation** OR at least one
  **errored** file (a parse/resolve failure in the corpus is a real problem worth
  failing CI, reported in its own bucket). Skips never affect the exit. `0`
  otherwise. (Single-file mode is unchanged: it cannot "error and continue" — a
  parse/resolve error is its own exit 2.)
- `--max-scenarios` (existing flag) is honoured per protocol in batch. Its default
  stays `0` (no cap) for single-file; the **CI invocation** passes an explicit cap
  chosen after measuring wall-clock (see Risks). `--chunk-size`, `--amp`,
  `--library` pass through to every protocol in the batch.

### CI wiring — `.github/workflows/test.yml`

Add a `fuzz` step to the existing `test` job (or a small dedicated single-Python
job, mirroring `rust-equivalence`) that runs:

```
refrain fuzz bench/protocols examples --max-scenarios <CI_CAP>
```

The step fails the build on exit 1 (violation or errored file). The coverage line
and skip breakdown land in the CI log. Skips do not fail the build.

### refrain-protocols handoff

Commit, in this repo, a ready-to-use workflow + short README under
`docs/superpowers/ci/refrain-protocols-fuzz.md` (workflow YAML + the
`refrain fuzz protocols lib drafts` invocation + notes on pinning the refrain
version). The actual `refrain-protocols` PR that adds the workflow is tracked as a
follow-up, opened once Inc 0 lands and a refrain version exposing batch mode is
installable.

## Testing (TDD, `tests/fuzz/`)

- **Detectors** (`test_surface.py` / new `test_unsupported.py`): a single-condition
  reward protocol and a center/bandwidth protocol each raise
  `UnsupportedProtocol` with the exact reason; an `all_of`/`above`/`below` protocol
  still builds a surface (no false skip).
- **Single-file skip** (`test_cli_fuzz.py`): unsupported protocol → exit 0 with a
  `SKIPPED (unsupported: <reason>)` line; supported protocol still exits 0 with a
  report; parse/resolve error now → exit 2; missing file → exit 2.
- **Guarded backstop**: an introspection shape that throws a legacy
  `ValueError` (e.g. an inhibit/weighted protocol) → `SKIPPED("unclassified …")`,
  not a crash; a forced exception inside the evaluate/oracle loop is **not**
  swallowed (still surfaces as violation/error).
- **Batch runner** (new `test_batch.py`): a temp dir of mixed
  supported/unsupported/parse-error protocols → correct
  `fuzzed/skipped/errored` counts, coverage line, by-reason breakdown; exit 0 when
  only skips; exit 1 when a seeded violation or a parse-error file is present;
  `nargs='+'` aggregation across two dirs; single-file path unchanged.

All tests run via `.venv/bin/python -m pytest tests/fuzz/ -q`; `src/refrain/fuzz/`
stays ruff-clean (`.venv/bin/ruff check src/refrain/fuzz/`).

## Out of scope (Increment 0)

- Making single-condition / center-bandwidth / inhibit / weighted / bandpower
  protocols actually fuzzable (Increments 1–5).
- The `refrain-protocols` PR itself (follow-up; workflow file shipped here).
- Regression-baseline tracking (fail only when a previously-passing protocol
  regresses) — roadmap defers this until skip/violation noise warrants it.
- Committed coverage-report file and PR-comment bot.
- Randomised scenarios/shrinking, Rust-backend parity, calibrated oracle.

## Risks / open questions for the plan

- **Batch wall-clock / `CI_CAP`.** Measure `refrain fuzz bench/protocols examples`
  wall-clock during the build; pick a `--max-scenarios` CI cap that keeps the gate
  fast (target: well under the existing `test` job). Record the measured number in
  the plan and the workflow.
- **Backstop over-capture.** The guarded backstop must wrap introspection/
  generation *only*. The plan must pin the exact call boundary so no
  evaluate/oracle/check exception is reclassified as a skip. A regression test
  asserts a loop-raised exception is not swallowed.
- **`unclassified` detail string.** Keep it short and stable enough to be useful in
  the breakdown without leaking long tracebacks; derived from the exception
  message, truncated.
- **Corpus discovery.** Confirm the recursive `*.refrain` walk excludes test
  fixtures and any non-target files; for refrain the explicit `bench/protocols`
  `examples` inputs sidestep this, but the dir-walk must be deterministic (sorted)
  for stable reports.
