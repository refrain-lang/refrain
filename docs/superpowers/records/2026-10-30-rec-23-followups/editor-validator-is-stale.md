# Follow-up: refrain-editor's `validateModel` rejects 24 of its own 41 canonical models

**Found:** REC-23 Task 12, on the first run of the gate the task existed to build. Fixed in that task
(Ruling R61). Written up because the *shape* of the defect is worth keeping, not the defect.

## What the gate found

refrain-editor vendors 41 protocol models across 123 fixture files, and until this task **not one was
passed through its own schema validator**. On the first run:

- **41 of 41 pass the vendored JSON Schema.** The shape of the data is fine.
- **24 of 41 fail the full `validateModel`**, in its two catalog-aware checks — the ones JSON Schema
  cannot express.

The models are canonical: they are generated from the Python reference implementation and round-trip
byte-exact. **The validator was wrong, not the data.**

## The two causes, and why the second is the interesting one

**1. A synthetic block id the validator does not know about.** `threshold.conditional` is deliberately
catalog-free. `src/describe.ts:419` emits it, `src/render.ts:240` special-cases it, and the Python side
does the same. `validateModel`'s "every block id exists in the catalog" check therefore rejects a block
that the repository's own describer produces and its own renderer handles.

**2. A duplicated source of truth, where the authoritative copy is derived and the duplicate is
hardcoded.** This is the one to remember.

```ts
// src/render.ts:26 — authoritative, DERIVED from the renderer's own bodies
export const RENDERABLE_CONTROL_KINDS = new Set([...Object.keys(CONTROL_BODY), "mode", "boolean"]);

// src/model.ts:75 — a private copy, hardcoded, two kinds behind
const RENDERABLE_KINDS = ["frequency", "percent", "voltage", "number"];
```

The authoritative set is re-exported publicly (`src/index.ts:1`) and is the subset gate itself
(`src/describe.ts:627`). It grows automatically the moment someone adds a renderer body. The duplicate
cannot, and had already fallen two kinds behind — `mode` and `boolean`.

That asymmetry is what makes this worth a note. A duplicated constant is an ordinary smell; a duplicate
of a *derived* value is a guarantee of future drift, because the thing it copies is designed to change
without anyone editing it.

## Why it stayed invisible

Nothing ever called `validateModel` on real data. `test/validate.test.ts` covers hand-written good and
bad cases, and `test/describe-export.test.ts` validates a single inline protocol. Both are green and
neither can see this, because neither uses a model from the corpus.

Two further things kept it quiet, and both are worth knowing:

- **The assertion the plan originally specified could not have found it.**
  `expect(() => validateModel(model)).not.toThrow()` — against a function that returns `{ok, errors}`
  and never throws. It would have passed on all 41 while 24 were invalid, inside the very test written to
  close this gap.
- **The fix cannot come from a resync.** The synthetic block id is in neither catalog version, and
  `src/model.ts` is not a vendored file. No amount of re-vendoring would have moved it.

## The fix

Two edits in `src/model.ts`: import `RENDERABLE_CONTROL_KINDS` from `./render.js` and delete the local
array; allow `threshold.conditional`, matching `render.ts:240`. Neither introduces a new opinion — both
make the validator agree with code already shipping beside it.

## What to take from it

A validator is not exempt from the rule it enforces. This one asserted that models must match the
catalog and the renderer, while holding its own stale idea of what the renderer supports. The question
worth asking of any check: **does it derive its expectations, or restate them?**

---

## Addendum: how it was fixed, and the third disagreement found while fixing the first two

Fixed on branch `rec-23-vendor-resync` in `/Users/jcroall/.superset/projects/refrain-editor`, commit
`5913ceb`, as a **separate commit from the resync** — `git diff ea6f8a1..5913ceb -- vendor/` is empty, so
no vendored artifact moved. All 41 models now pass both layers.

Two refinements to the fix as ruled, both from the implementer, and the second is the interesting one.

**The synthetic block id became one exported constant, not a third literal.** Writing
`"threshold.conditional"` into `model.ts` as a bare string would have put the same literal in a third
file — precisely the shape the *other* half of this defect was about. It is now
`CONDITIONAL_THRESHOLD_BLOCK`, used by the describer that emits it, the renderer that dispatches on it,
and the validator that admits it.

**The first cut over-weakened the validator, and the implementer caught it.** Exempting the synthetic id
from *every* block list would have made the validator **looser than the renderer**: that id sitting in
`derives` would have validated and then thrown at render time. `render.ts:240` special-cases it only
inside the thresholds loop, so the exemption is scoped to match, with a test that it is rejected
anywhere else.

That is the same class of defect as the two this document opens with — a validator and a renderer
disagreeing about what is legal — **pointing the other way.** Worth stating as the general rule: when you
loosen a check to match a renderer, the risk is not only that you loosen too little. Both directions are
the same bug.

## What remains

`ui/src/blocks.ts:51-57` is still a second catalog consumer, and it will not offer the four declarations
REC-23 added. The control-kind duplication is now gone from `model.ts`, and since the authoritative set is
publicly exported from `render.ts` via `index.ts`, a future duplicate would be a choice rather than an
accident — but nothing structurally prevents one.
