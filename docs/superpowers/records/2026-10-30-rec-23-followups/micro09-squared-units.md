# One bench protocol states its muscle threshold in microvolts squared

**Status:** found during REC-23, deliberately not changed.
**Severity:** cosmetic in the engine, misleading as an example.

## What is wrong

`bench/protocols/micro_09_inhibit.refrain:35` writes:

```
threshold = absolute(55 uV2)
```

The threshold is a band power, in microvolts squared. The user's stated model is amplitude in
microvolts: *"I don't use squares. microvolts is where its at. thats our model."*

## Why it was not changed in REC-23

Changing it moves that protocol's compiled output. The byte-identity guard
(`tests/fixtures/ir_corpus_identity.json`) exists precisely to catch that, and the release's hard rule is
that no existing protocol's compiled output moves without a new declaration present. So this needs its
own change, with its own before-and-after recorded — not a quiet edit inside a feature release.

## The fix, when it is taken

Follow `bench/protocols/micro_15_artifact_gate.refrain:50-53` and
`bench/protocols/seed_scale_emg.refrain`: put a `sqrt()` stage after the band power so the metric is an
amplitude, and restate the threshold in microvolts. Both of those protocols carry a comment explaining
why the stage is there.

**Pick the new threshold deliberately rather than by taking a square root of 55.** √55 ≈ 7.4 µV, and
whether that is the right muscle limit is a clinical question, not an arithmetic one.

## The related thing worth fixing at the same time

REC-23 found that the resolver's unit check cannot see through `rectify` in metric position:
`_rectify_output` assumes volts when its input type is absent, so a band power in metric position
**matches a microvolt threshold by accident** and no warning fires. That is why this and the
`seed_scale_emg` mismatch both compiled silently.

The resolver does warn correctly when the control is declared as a bare `number`. Teaching `rectify` to
carry its input's type through would make the whole class of unit mismatch visible at compile time
instead of requiring someone to notice by eye — which is how the `seed_scale_emg` one was caught.

---

## A related instance — and I was wrong about the cause. Corrected below.

`tests/fixtures/external/skin_temp_warming.refrain` declares a skin **temperature** in degrees Celsius
as a `voltage`:

```
baseline_c = voltage {
  default = 30 uV
  range   = (15 uV, 40 uV)
  label   = "Session baseline (deg C)"
  ...
}
```

I first wrote that this was because **refrain has no temperature type**, that there was nothing else the
author could have written, and that a thermal protocol has to lie about its units to compile.

**All three of those are false, and have been since before this release started.** I took the claim from
the fixture's own header comment instead of checking the resolver. The header was true when it was
written, and the language moved underneath it.

### What is actually true, verified

Commit `4e64c7e` ("feat(types): sqrt(), and a temperature dimension that a stream can carry", #112) —
on this branch's history, three commits before REC-23 began — added all three missing pieces:

- a **temperature dimension** and a `C` unit (`C` is in the grammar's `UNIT` terminal);
- a **`temperature` control kind** (`src/refrain/resolver.py:4059`), whose own comment reads: *"A thermal
  protocol's target rise is the quantity a practitioner actually sets, and **before this kind existed**
  it had to be declared `voltage` and labelled '(deg C)' in prose."*
- an **`input` `unit = "<unit>"` override** (`src/refrain/resolver.py:872-884`), whose docstring names
  **this exact protocol** as its motivation: *"A thermopile channel typed as uV is what forced a shipped
  skin-temperature protocol to write every target in `uV` and mean degrees Celsius — the file
  type-checked while lying."*

So the language grew exactly the feature, for exactly this file, and **the file was never migrated.**

### The real finding, which is smaller and more actionable

That fixture opens with a prominent, capitalised instruction to the reader:

> `// UNITS WART, read this first: Refrain's type system has exactly two base dimensions, time and`
> `// voltage. There is no temperature dimension and no C unit, so every temperature below is written`
> `// uV and MEANS degrees Celsius.`

**That header is now false, and it is load-bearing documentation.** The file is referenced from
`docs/SPEC.md`, `tests/test_resolves_implies_runs.py`, `tests/test_editor_catalog_growth.py` and
`tests/fixtures/external/README.md`. Anyone who reads it learns that refrain cannot express a
temperature, and stops looking.

### The migration, verified end to end

Three mechanical changes, and the protocol resolves with real temperature dimensions:

1. `unit = "C"` on `input "skin"`;
2. `voltage` → `temperature` on `baseline_c` and `target_rise_c`;
3. `uV` → `C` on their literals.

Confirmed by parsing and resolving the migrated file: `baseline_c` and `target_rise_c` both come out
carrying `Dimensions(..., temperature=1)` with `unit='C'`, and nothing else in the protocol objects.
Without step 1 the resolver correctly refuses the file — `` `-` requires matching dimensions, got
stream<scalar uV> vs stream<scalar C> `` — which is the type system doing its job.

### What to take from it

Two things, and the second is the one I would keep.

**A stale comment is worse than no comment**, because it is evidence. This one survived the commit that
falsified it, in a file whose motivating role is cited in that commit's own code.

**And a comment is not a source.** I asserted a language gap on the authority of a header written before
the gap was closed, in a release where I had already recorded seven false claims-of-absence by other
people. The check was one `grep` for `temperature` in the resolver. "There is no X" is only ever a claim
about where you looked.
