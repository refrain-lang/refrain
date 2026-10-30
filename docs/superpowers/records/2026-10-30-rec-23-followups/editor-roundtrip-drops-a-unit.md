# Follow-up: a compound reward's `midpoint` loses its unit on an editor round-trip

**Found:** REC-23 Task 11, by the implementer, while writing a test it then deliberately wrote *around*.
Pre-existing, unrelated to the four new declarations, and out of scope for 0.25.0.

## What happens

`_match_reward`'s compound bare-reference arm reads `_arg(cont, "midpoint").value` and **discards the
unit**, while `reward.operant_compound_abs`'s catalog slot is typed `number` and renders unitless. So a
protocol writing

```refrain
sigmoid("<a microvolt stream>", midpoint: 6 uV, ...)
```

inside an `all_of` compound round-trips as `midpoint: 6`.

## Why nothing is broken today

It resolves to the same value — with a warning — so the IR is identical and no test fails. The editor's
promise is a byte-exact round-trip of the *source*, and that promise is what is broken: **the source
that comes back is not the source that was authored.** A practitioner who opens a protocol in the editor
and saves it gets a file whose units have quietly been dropped, in a position where the resolver happens
to guess right.

## Why it is worth a ticket

The round-trip test cannot see this class of defect at all, because it compares resolved IR rather than
source text. Any editor change that drops a unit the resolver can infer will pass. That is the same
shape as the trap this task existed to close — a field dropped on both the describe and render sides is
invisible — one level further out, at the level of the test's *comparison*, not the model's coverage.

So the suggested work is two things, and the second is the more valuable:

1. Carry the unit through that arm, so `midpoint: 6 uV` round-trips as written.
2. Add a source-text comparison to the round-trip suite for at least the protocols whose rendering is
   expected to be byte-exact, so unit loss and other source-level drift are visible without resolving.

## What Task 11 did about it

Nothing, correctly: it is out of scope. It wrote its own `operant_compound_abs` test source over a
dimensionless signal so the new test does not depend on the behaviour either way — which is the right
call for the task and also the reason the defect is still invisible to the suite.

---

## Addendum: a second, worse instance of the same class — and it is now a cross-repo divergence

REC-23 Task 11's review found that `src/refrain/editor/describe.py` accepted an artifact inhibit whose
action is `freeze(...)` and re-rendered it as `mute`. Verified identically before and after the task:
`in_subset: True`, and the sole IR difference is `action_kind: "freeze"` → `"mute"`.

That is the same class as the unit loss above — the editor returns something other than what was
authored — but it is not cosmetic. **A mute silences the output; a freeze holds the last value.** For an
artifact gate those are different interventions: one tells the trainee nothing is happening, the other
tells them nothing is changing. Turning one into the other silently is a clinical difference, not a
formatting one.

It was fixed in 0.25.0 (Ruling R58) rather than deferred, because the `kind` label that release shipped
is legal on a `mute` **or** a `freeze` inhibit, so the release itself makes a `freeze` artifact inhibit a
more attractive thing to write. No protocol anywhere uses a `freeze` action — verified across refrain,
refrain-protocols and refrain-editor's vendored fixtures — so the fix moved nothing that exists.

**What is still open, and it is the interesting part.** `refrain-editor/src/describe.ts:360-373` has no
equivalent action check. So the two describers now disagree: Python refuses a `freeze` artifact inhibit,
TypeScript accepts and corrupts it. Because no corpus contains a `freeze` action, **neither repository's
parity suite can see the disagreement** — the suites compare the two implementations only on inputs the
corpus contains, and this input is not in it.

That is the same limit as the engine's dual-backend drift gate, which proves parity only for inputs the
fixtures exercise. It reads as "the two implementations agree" and means "the two implementations agree
on the corpus we have." Worth stating once, plainly, somewhere permanent: **every parity suite in this
project is a statement about the corpus, not about the implementations.**

The divergence points the safe way — the stricter side is the engine-facing one — and REC-23 Task 12
adds a guard test that fails the day a vendored protocol carries a `freeze` artifact inhibit, so the gap
cannot be walked into unknowingly. The TypeScript side needs a *refusal* added, not a renderer extended,
and that belongs with the port work described in this document's sibling items.
