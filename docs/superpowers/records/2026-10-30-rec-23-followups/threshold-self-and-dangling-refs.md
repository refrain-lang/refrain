# The resolver silently accepts a self-referential threshold and a dangling threshold name

**Status:** found during REC-23 while closing the re-seed dependency walk. Deliberately not fixed.
**Severity:** a protocol that is almost certainly a typo compiles and runs.

## What is wrong

A threshold's `level` may legally name another threshold. The resolver does not check that the name
refers to a *different*, *existing* threshold:

- **Self-reference.** A threshold whose level names itself resolves without complaint.
- **Mutual reference.** Two thresholds naming each other both resolve.
- **Dangling name.** A name that matches no declared threshold is kept as a plain string literal rather
  than refused.

The mechanism behind all three: a string-literal reference is converted to a threshold reference only
when its target is **already declared** at that point in resolution. A forward reference, a self
reference, and a name that was never declared all stay plain string literals, and nothing afterwards
asks whether that was what the author meant.

## How it surfaced

REC-23 made the re-seed dependency walk close transitively over threshold references. The ruling that
authorised it claimed the graph was acyclic "because a cycle is already refused." **That reasoning was
wrong** — the implementer checked rather than taking it, found cycles do resolve, and built termination
on a property that holds regardless: the walk only ever adds names not already in its set, inside the
finite set of declared thresholds.

So the re-seed walk is safe. What this finding records is the pre-existing hole the check exposed.

## Why it was not fixed in REC-23

Refusing any of the three changes what compiles, for protocols with no new declaration present — which
the release forbids. A dangling threshold name in particular may be load-bearing somewhere: it currently
degrades to a string literal, and some protocol may depend on that silently.

## The fix, when it is taken

Three separate decisions, worth taking separately:

| Case | Suggested treatment | Why |
|---|---|---|
| Self-reference | Refuse at resolve time | There is no reading of it that means anything |
| Mutual reference | Refuse at resolve time | Same |
| Dangling name | Warn, or refuse behind a flag | Needs a survey of real protocols first — a refusal could break a document that works today |

Before refusing the dangling case, survey the protocol corpus and any out-of-repo sets for threshold
levels that are string literals matching nothing. If none exist, refusal is safe and a typo stops being
silent.
