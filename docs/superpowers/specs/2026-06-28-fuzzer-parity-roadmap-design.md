# Protocol fuzzer — feature-parity roadmap — design

> Status: approved design (brainstorm complete), ready for per-increment plans.
> Builds on: the protocol fuzzer v1 (PR #49, `refrain fuzz`) reconciled onto
> Refrain v0.11.0. Supersedes the standalone staged-fuzzer plan as the *parent*
> program — staged becomes one increment within this roadmap.

## Goal

Make the protocol fuzzer track Refrain's feature set as it ships, so it can fuzz
the real protocol library (not just `smr_cz`-shaped protocols) and gate
engine/protocol correctness in CI for both **refrain** and **refrain-protocols**.

The fuzzer was built against `smr_cz`'s shape (single `all_of`/`any_of` of
`above`/`below` leaves, `band=(lo,hi)` bandpass, absolute/percentile thresholds).
Probed against the 21 protocols in the main repo it fuzzes only 4; the rest crash.
This roadmap closes that gap incrementally, prioritised by what the corpus actually
uses, and wires the fuzzer into CI so coverage growth is visible and regressions
are caught.

See [[feedback-fuzzer-evolves-with-refrain]] and [[project-protocol-fuzzer]].

## Corpus evidence (priorities are data-driven)

Feature usage across the `refrain-protocols` library (59 real `.refrain` files
under `protocols/`+`lib/`+`drafts/`; the repo's "213" count includes test
fixtures, which are not the fuzz target):

| Feature | % of corpus | v1 fuzzer |
|---|--:|---|
| `dwell()` reward | 96% | handled |
| **single-condition reward** (NOT `all_of`/`any_of`) | ~80% | crashes |
| **`center:`/`bandwidth:` bandpass** | 59% | crashes |
| percentile / absolute thresholds | 62% / 37% | handled |
| `inhibit` | 25% | crashes |
| weighted-composite reward | 13% | crashes |
| `bandpower` | 10% | crashes |
| coherence / staged / autocorr / HRV-passthrough | 5 / 5 / 1 / 1% | crashes |
| `extends` (library inheritance) | 0% | n/a |

The two dominant unlocks are single-condition reward (~80%) and the
center/bandwidth bandpass form (59%); everything else is a long tail.

## Guiding principle: extend-in-place, lockstep

Each feature is added by **generalising the existing pipeline stages**, not by a
parallel per-feature subsystem. One coherent codebase; the non-supported case is
simply "a stage reports it cannot represent this shape." When Refrain adds a
language/runtime feature, extending the fuzzer to cover it is part of that
feature's definition of done.

## The reusable per-feature method

Every breadth increment follows the same five-stage template — the durable recipe
for keeping the fuzzer in lockstep:

1. **Introspect** (`surface.py`) — extract the feature's structure from the
   resolved IR / IR-JSON. If a shape cannot be represented, raise a typed
   `UnsupportedProtocol(reason)`.
2. **Render** (`synthetic.py`) — produce EEG that exercises the feature (only when
   it needs signal shapes the renderer does not already emit).
3. **Predict** (`oracle.py`) — model the feature's expected behaviour analytically,
   preserving oracle independence (predict from coefficients/semantics, never run
   the evaluator).
4. **Check / Generate** (`check.py` / `generate.py`) — directed scenarios that
   exercise the feature and verdicts that assert it.
5. **Graceful skip** — anything a stage cannot represent yields
   `UnsupportedProtocol(reason)`; the CLI/batch reports `SKIPPED (unsupported:
   <reason>)` instead of crashing.

## Skip / batch / CI contract

**Single-file `refrain fuzz <file>`:**
- Unsupported shape → **exit 0** with a clear `SKIPPED (unsupported: <reason>)`
  line. Unsupported features are reported, never fatal.
- Genuine engine violation (MISSED/SPURIOUS/metamorphic) → **exit 1**.
- Parse/resolve error, or missing file → **exit 2**.

**Batch `refrain fuzz <dir>` (recursive):**
- Walks the directory, fuzzes each protocol, prints an aggregate:
  `fuzzed N (pass P / fail F) / skipped K` with a by-reason breakdown of skips.
- Exits **1 iff** at least one genuine violation occurred; skips never fail.

**CI (both `refrain` and `refrain-protocols`):**
- Run batch mode over the protocol directory.
- **Gate on violations only.** Skips are reported, not gated.
- Surface a **coverage metric** (`fuzzed / total`) so coverage growth is visible
  and a drop (a previously-fuzzed protocol now skipping or failing) is noticeable.
- v1 gate is simple: any genuine violation fails the build. Regression-baseline
  tracking (fail only when a previously-passing protocol regresses) is a later
  refinement if skip/violation noise warrants it.

## Increment roadmap (build order)

Each increment is its own spec → plan → build PR, converts "skipped" protocols into
"fuzzed," and stays mergeable to current `main`.

- **Increment 0 — foundation.** `UnsupportedProtocol` exception + graceful skip in
  the CLI; batch/dir runner with the aggregate report; CI wiring in both repos
  with the coverage metric. After this, the whole corpus runs in CI today (most
  protocols skip), and every later increment visibly shrinks the skip list.
- **Increment 1 — single-condition reward (~80%).** Treat a bare
  `dwell(above/below(...))` (and other non-`all_of`/`any_of` reward conditions) as
  a one-leaf condition in `surface.py` + `oracle.py`. The Task-2 review already
  flagged this exact limitation. Largest single coverage jump.
- **Increment 2 — `center:`/`bandwidth:` bandpass (59%).** Read band edges from the
  center/bandwidth args (or derive from the baked SOS, which exists in IR-JSON
  regardless of declaration form, so the oracle's gain math already works).
- **Increment 3 — `inhibit` masking (25%).** Model the inhibit primitive's gating
  of reward emission in the oracle + scenarios.
- **Increment 4 — weighted-composite reward (13%).** Introspect and predict
  `combine="weighted"` reward components.
- **Increment 5 — `bandpower` (10%).** New derive primitive in introspection +
  prediction.
- **Increment 6 — long tail.** coherence (5%), **staged** (5% — design + 15-task
  plan already written: `…/specs/2026-06-26-staged-fuzzer-design.md`,
  `…/plans/2026-06-26-staged-fuzzer.md`), autocorr/flutter (1%), HRV-passthrough /
  low-Fs envelope (1%). Sequenced by remaining corpus impact when reached.

After each increment, re-run the corpus probe to confirm the empirical unlock
before starting the next.

## Success metric

Corpus coverage (fuzzed ÷ total) rising toward ~100% across both repos, CI green,
and the by-reason skip list shrinking each increment. The fuzzer stays mergeable to
current `main` throughout (it stranded ~4 minor versions behind once; don't repeat
that).

## Out of scope (for this roadmap)

- Randomised scenarios with shrinking; Rust-backend parity fuzzing; calibrated
  (vs analytic-margin) oracle. These layer on the substrate later.
- `extends`/library inheritance for fuzzing — 0% of the current corpus; revisit if
  library-based protocols appear in the fuzz target. (The CLI already accepts
  `--library`; batch mode should pass it through when a lib path is configured.)
- refrain-editor CI — not a fuzz target for now (only refrain + refrain-protocols).

## Risks / open questions for planning

- **Batch runtime.** Some scenarios run minutes of synthetic EEG; fuzzing 59
  protocols could be slow. Increment 0 should cap per-protocol scenario count for
  CI (e.g. a `--max-scenarios` default for batch) and/or parallelise, and measure
  wall-clock before enabling a blocking CI gate.
- **`UnsupportedProtocol` granularity.** Reasons must be specific enough to track
  coverage by feature (e.g. `weighted-composite reward`, `center/bandwidth
  bandpass`) so the skip breakdown maps to roadmap increments.
- **Single-condition reward edge cases (Increment 1).** Confirm the IR shape of a
  non-`all_of` reward condition (bare `above`/`below` `IRCall` vs other ops seen in
  `micro_07_ilf`/`micro_08_bandpower` — "unrecognized condition expr IRCall") and
  enumerate which condition ops the oracle must recognise.
- **Coverage metric location.** Decide where the `fuzzed/total` number is surfaced
  (CI log, a committed coverage report, or a PR comment) so trends are visible.
