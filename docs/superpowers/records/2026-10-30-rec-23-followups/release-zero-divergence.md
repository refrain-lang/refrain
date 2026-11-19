# Follow-up: `release: 0 ms` behaves differently on the two backends

Found while implementing REC-23 Task 6. **Pre-existing, not caused by REC-23.** No protocol in the
repo uses `release: 0`, which is why the drift gate has never caught it.

## The divergence

| | `action_release_ms` in the IR | hangover applied |
|---|---|---|
| Python `src/refrain/eval_.py:976` | `0.0` | **200 ms** |
| Rust `refrain-core/src/eval.rs:2635` | `0.0` | **0 ms** |

Python: `release_ms = ih.action_release_ms or 200.0` — `0.0` is falsy, so an explicit zero is
silently replaced by the 200 ms default.
Rust: `ih.action_release_ms.unwrap_or(200.0)` — correctly distinguishes `Some(0.0)` from `None`.

An author who writes `release: 0 ms`, meaning "mute only while the artifact is actually present",
gets a 200 ms hangover on the Python backend and none on the Rust one.

`src/refrain/ir_print.py:113` carries the same falsy-zero bug cosmetically: `if
ih.action_release_ms` suppresses the annotation for an explicit zero, so `print_cred_nf` would
not show `release 0 ms` at all.

## Why it was not fixed inside REC-23

Both directions are a behaviour change for a protocol outside this repo, with no new declaration
present — which this release's goal 5 forbids. And which direction is *correct* is a product
question, not a mechanical one:

- **Honour the explicit zero** (make Python match Rust): respects what the author wrote. Removes
  200 ms of hangover from any clinic protocol that currently says `release: 0 ms` on the Python
  backend.
- **Treat 0 as "unset"** (make Rust match Python): preserves today's Python behaviour, but means
  there is no way to express "no hangover", which is a gap in the language.

The first looks right to me, with a release note, but it is the kind of call that wants a human.

## The more interesting finding: a gap in the drift gate

The dual-backend drift gate proves parity **only for inputs the fixtures exercise.** This
divergence has survived every green gate run because no golden-vector protocol sets `release: 0`.
A gate that reads as "the two engines agree" actually reads "the two engines agree on the corpus
we happen to have".

Worth considering: a parity sweep over edge values of numeric IR fields (0, absent, negative
where legal) rather than only over hand-written protocols. That would have caught this, and it
would catch the next one of these without anyone having to think of it.

## Also reported alongside, unverified by me

`bandpower` resolves as a pipeline stage but crashes on the first chunk (pre-existing). Reported
by the Task 6 implementer; I have not independently confirmed it.
