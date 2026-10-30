# Released schemas 0.1–0.5 reject a null range bound, and a shipped example fails its own schema today

**Status:** found during REC-23, deliberately not fixed. Needs a product decision.
**Severity:** live, not theoretical — a file in this repository is affected right now.

## What is wrong

A control that declares no `range` emits `"range_low": null` and `"range_high": null`. The JSON
schemas for IR versions 0.1 through 0.5 type those three fields — `default`, `range_low`, `range_high` —
as a bare reference to the expression definition, which does not admit `null`. So any document with a
rangeless control **fails validation against the schema it declares itself to be**.

Version 0.6's schema was widened during REC-23, because the release added the first bench protocol with
a rangeless control and the gate caught it immediately. The five earlier versions were left alone.

## Why nobody noticed

`tests/test_ir_json_schema.py` validates only the golden vectors. It never validates anything under
`examples/`. So `examples/staged_beta_alpha.refrain` — which has two rangeless controls, `beta_uv` and
`alpha_uv` — has been failing its own version 0.2 schema with **8 validation errors** without anything
in the suite noticing.

That is the more useful finding of the two: the schema suite proves the golden vectors validate, and
reads as though it proves the compiler emits valid documents. It does not.

## Why it was not fixed here

Widening a released schema retroactively changes what already-published documents claim to be valid
against. Some third-party runtime may be validating incoming protocols against a pinned copy of the 0.2
schema; loosening it changes what that runtime accepts. That is a compatibility decision with an owner,
not a drive-by fix inside a feature release — and nothing in REC-23 forced the call, since 0.6 is the
only version the release emits.

## The two options

| | What it does | Cost |
|---|---|---|
| **Widen 0.1–0.5** to admit `null` on the three fields | Makes the shipped example valid; brings all six schemas into line | Changes what five released schemas accept. Anyone validating against a pinned copy sees different behaviour |
| **Leave them** | No compatibility question | A shipped example stays invalid against its own schema, and a third party validating a rangeless 0.2 document rejects a document the compiler emitted |

Either way, **extend `tests/test_ir_json_schema.py` to validate `examples/` as well as the golden
vectors.** That is the change that makes the next one of these fail loudly instead of sitting for a year,
and it carries no compatibility question at all. Recommended regardless of which option is chosen.
