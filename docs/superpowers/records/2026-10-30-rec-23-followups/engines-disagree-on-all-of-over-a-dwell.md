# Follow-up: `all_of([dwell(...)])` in an output binding resolves today, and the two engines disagree about it from the first sample

**Status:** open. Pre-existing, reachable at 0.24 and every release before it, nothing to
do with the 0.25.0 declarations, and **no freeze or pause is involved**. Found while
checking the final review's "Declined to judge" item 7, which said no spelling reaches a
non-top-level `dwell` in an output binding. Four do.

The companion document,
`followup-resolver-accepts-an-event-stream-in-a-boolean-position.md`, is about the
language accepting the shape at all. This one is about the engines disagreeing on a shape
that compiles **today**: someone could hit this without 0.25.0 existing.

## The divergence

```refrain
output { fb = all_of([dwell(condition: above("level","t"), duration: 250 ms)]) ? 1.0 : 0.0 }
```

Never-dipping signal, 256 Hz, 320 samples, chunks of 32, no inhibit, no pause:

| | first non-zero `output/fb` | non-zero samples |
|---|---|---|
| Python | **0** | **320 of 320** |
| Rust | **63** | **1** |

Python reads the channel as fully on from the very first sample and stays there for the
whole session, whatever the signal does. Rust fires it on one sample, 250 ms in, which is
what the author wrote. For a feedback channel that is the difference between a tone that
never stops and a single ding — and the drift gate cannot see it, because no fixture
carries the shape.

Three more spellings behave the same way, all measured:

| Spelling | Python | Rust |
|---|---|---|
| `all_of([dwell(...)]) ? 1.0 : 0.0` | first=0, n=320 | first=63, n=1 |
| `any_of([dwell(...)]) ? 1.0 : 0.0` | first=0, n=320 | first=63, n=1 |
| `all_of([dwell_rearm(...)]) ? 1.0 : 0.0` | first=0, n=320 | first=63, n=1 |
| `all_of([dwell(...)])` bound bare to a channel | first=0, n=320 | first=63, n=1 |

## Why Python answers "always on"

Verified directly rather than read off the code. A `dwell` instance returns a
`DwellResult` — an object with `events` and `holds` arrays, not an array — and
`AllOfImpl.step` stacks its arguments:

```python
>>> AllOfImpl().step(DwellResult(np.zeros(4, bool), np.zeros(4, bool)))
np.True_                       # dtype=bool, shape=()
>>> np.stack([DwellResult(...)]).dtype
dtype('object')
```

`np.stack` of a single `DwellResult` is a one-element **object** array, and `np.all` over
an object array folds Python truthiness: a `DwellResult` instance is always truthy, so
the result is a 0-d `True` with no samples in it at all. That scalar then broadcasts
across the chunk at the output stage, which is why the channel reads 1.0 everywhere —
including before the dwell could possibly have fired, and on a signal that never
qualifies.

Rust has no such object: `CNode::AnyOf`/`AllOf` receive the dwell node's evaluated
boolean vector (its rising-edge stream), so it answers as if the `all_of` were not there.
Neither answer is the author's intent, but Rust's is the harmless one.

With a genuine condition beside it the shapes no longer stack and Python raises instead:
`all_of([dwell(...), above("level","t")])` gives `ValueError: all input arrays must have
the same shape`, while Rust runs and answers `first=63, n=1`.

## Reach

14 spellings tried that put an event stream in a boolean position — `dwell` and
`dwell_rearm` are the only two primitives whose output is `event_stream`
(`src/refrain/primitives.py:725,747`):

- **1 is the legal form** — `output { fb = dwell(...) }` at the top level. Both engines
  agree (first=63, n=1). Untouched by any of this.
- **3 are refused by the resolver**, all three being a ternary *condition*: in an output
  binding, in `reward { continuous }`, and in a derive formula. "ternary condition must
  produce a boolean stream, got event_stream" is the one check that exists.
- **4 resolve and diverge**, the table above.
- **6 resolve and are unevaluable in Python while Rust produces something**: `all_of`
  with a real condition beside the dwell (ValueError / runs), `hold_for(condition:
  dwell(...))` in an output binding (AttributeError / dead stream), a `dwell` nested in a
  reward condition (AttributeError / dead stream), `all_of` over a dwell in a reward
  condition (ValueError / dead stream), `hold_for` over a dwell in a reward condition
  (AttributeError / dead stream), and `metric = dwell(...)` on an inhibit (TypeError /
  318 unmuted samples).

So of the 10 spellings that compile, Rust answers all 10 and Python answers 4 of them
differently and raises on the other 6. **No protocol in this repository uses any of
them**: every `all_of` / `any_of` in the 40 tracked `.refrain` files takes
`above`/`below`/`inside` comparisons, and the four nearest uses are reward conditions
inside `dwell(condition: all_of([...]))`, which is the ordinary form.

## Reach, corrected: the probe above understated it twice

Two things the re-review found that the 14-spelling sweep missed, both recorded here
because the sweep was offered as the reach statement:

**A fourth refusal exists, and it is a different check.** `reward "a" { signal =
dwell(...) }` — an event stream as a weighted composite's component signal — is refused
with "reward \"a\".signal must be a [0,1] success metric (scalar, dimensionless — wrap it
in sigmoid/linear…)". That is the success-metric check, not the ternary-condition one, so
the resolver has two independent guards that happen to catch an event stream rather than
one. Worth knowing when the refusal below is written: the pattern to follow already
exists in two places.

**Three further spellings resolve, in numeric rather than boolean positions.** A `dwell`
as the *whole top-level expression* of something that is not an `output` binding:

| Spelling | Resolver | Python | Rust |
|---|---|---|---|
| `derive "d" { formula = dwell(...) }` | accepts | `AttributeError: 'DwellResult' object has no attribute 'size'` | `first=63 n=1` |
| `reward { continuous = dwell(...) }` | accepts | same `AttributeError` | `first=63 n=1` |
| `reward "live" { continuous = dwell(...) }` (a bundle) | accepts | same `AttributeError` | `first=63 n=1` |

`dwell_rearm` behaves the same way in all three. With `inhibit { metric = dwell(...) }`
— which the original sweep did cover — that makes **four** numeric positions where an
event stream is accepted as a number, Rust evaluates it as its rising-edge stream, and
Python raises on the first chunk. So the hole is not specific to boolean positions: the
resolver permits an `event_stream` almost anywhere a stream is expected, and the two
refusals it does make are the ternary condition and the component signal. (`threshold`
is not a fifth: `dynamic(...)` is not a primitive in this stdlib, so there is no
expression slot there to abuse.)

## What to do about it

Fixing this in the engines is the wrong order of work: the shape should not compile, and
`followup-resolver-accepts-an-event-stream-in-a-boolean-position.md` proposes that
refusal, which closes all four divergences, all six unevaluable boolean spellings and the
four numeric ones at once, and moves no protocol that exists.

**The half-fix, and why it was declined at handover.** The re-review argued for a cheap
improvement that needs no language change: make `AllOfImpl` / `AnyOfImpl` reject a
non-array argument instead of folding its truthiness, which turns a silent always-on
patient-facing tone into a loud `TypeError`, affects no existing protocol, and could ship
in a patch release. The argument is good as far as it goes — of the two failure modes, a
raise is plainly better than a tone that never stops.

It was declined at handover for a reason worth keeping with it: Python already diverges
from Rust here (640 of 640 samples on from sample 0, against one event at 63), and making
Python *raise* leaves the engines divergent in a **new** way — one raises, the other
plays — rather than closing the divergence. The clean fix moves both engines in one
place, at compile time, and it is a language change with its own release note. Whoever
picks this up should see both options: the half-fix is available and defensible if the
resolver refusal is going to take a release to land, and the two are not mutually
exclusive — a `TypeError` in the impl is a reasonable backstop *behind* a resolver
refusal, for IR that arrives from somewhere other than this compiler.

Worth noting for its own sake: this is a divergence the dual-backend drift gate was
built to catch and structurally cannot, for the same reason the Critical survived it —
the shape is not in the fixture corpus, so parity is only ever asserted about protocols
somebody thought to write down.
