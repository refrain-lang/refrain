# Follow-up: the vocabulary gate covers `docs/`, and the engine source is where the next violation comes from

**Found:** Task 10, by the implementer — who was caught by the gate rather than catching it.

## What happened

`tests/test_framing.py::test_no_retired_role_words` failed on `docs/EMBEDDING.md`. The implementer had
written "a threshold a clinician set by hand". This repository retired "clinician" in favour of
"practitioner" and enforces it on living documents. It was fixed and the commit amended.

## The part that is still true

The gate reads `docs/`. It does not read the engine source, and the engine source still says
"clinician" in **15 lines across 7 files** (counted with
`grep -rn clinician src/refrain/ refrain-core/src/`, 2026-10-29, at commit `9775b46`):

```
src/refrain/ir_print.py:7            "a clinician-readable textual dump"
src/refrain/ir_print.py:129          lines.append("# controls (clinician-tunable)")   <- EMITTED
src/refrain/primitive_impls.py:686   "the most commonly clinician-tuned parameter"
src/refrain/primitive_impls.py:771   "so a clinician's slider can't wedge the impl"
src/refrain/eval_.py:1687            "a host clinician observation window"
src/refrain/eval_.py:1750            "so the clinician retains full visibility"
src/refrain/eval_.py:1926            "populate a clinician observation ..."
src/refrain/eval_.py:2035            "the clinician just took responsibility for the value"
refrain-core/src/eval.rs:92          "One binding from a clinician control to ..."
refrain-core/src/eval.rs:1422        "Live-retune a clinician control in place"
refrain-core/src/eval.rs:3155        "`target_pct` is the clinician's live-tunable knob"
refrain-core/src/ir.rs:41            "Declared clinician controls, keyed by bare name"
refrain-core/src/python.rs:152       "live-retune a clinician control in place"
refrain-core/src/mobile.rs:173       "live-retune a clinician control in place"
refrain-core/src/mobile.rs:231       "For clinician observation / ..."
```

That is the whole of `src/refrain/` and `refrain-core/src/`. It does not cover `legacy/` (frozen),
`tests/`, `bench/`, or `examples/` — I did not survey those.

Two of these are not comments:

- **`src/refrain/ir_print.py:129`** appends the literal string `"# controls (clinician-tunable)"` to the
  output of `print_ir()`. A retired word is being printed by the toolchain, and the gate cannot see it
  because it only reads `docs/`.
- **`refrain-core/src/mobile.rs:173` and `:231`** are doc comments on uniffi-exported functions, so they
  become the generated Swift and Kotlin API documentation a mobile developer reads.

## Why this is worth a ticket rather than a sweep

Because this is how the word gets back into the docs. The implementer did not invent the phrase — the
natural way to write a host-facing document is to read the code and describe what it does, and the code
says "clinician". A gate that polices the output while the input still carries the word will keep
failing people who are doing the right thing. Each failure is cheap; the pattern is not.

## Suggested shape

- Replace the word in the 15 lines above, starting with the emitted `ir_print.py` string and the two
  `mobile.rs` doc comments that reach a published API surface.
- Extend `tests/test_framing.py` to read `src/refrain/**` and `refrain-core/src/**`, or at minimum any
  string literal that reaches program output.
- Decide deliberately whether the gate should cover comments as well as emitted text. Covering comments
  is what stops the recurrence; covering only emitted text is defensible and cheaper.

Out of scope for REC-23. Nothing is broken.
