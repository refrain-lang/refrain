# External corpus fixtures

Protocols contributed by downstream consumers, kept here as **test corpus**
for the "resolves ⇒ runs" conformance check in
`tests/test_resolves_implies_runs.py`.

These are deliberately **not** in `examples/`. `examples/` is the curated
reference set; this directory is a corpus to run things against. A protocol
here is not endorsed, reviewed or recommended by the Refrain project.

## `skin_temp_warming.refrain` + `emotibit_md_ancillary.json`

Finger skin-temperature training ("hand warming") on an EmotiBit MD
thermopile, from the Coherence Recorder, 2026-10-04. Supplied alongside
`refrain-biosignal-modality-gaps.md`.

Two things make it valuable as corpus material and both are worth knowing
before reading it:

- **Every temperature in it is written `uV` and means °C.** The file is kept
  as it was filed, so it still reads that way and its header comment still
  explains why: when it was written Refrain had two base dimensions, time and
  voltage, with no temperature dimension and no `C` unit, so it type-checked
  while lying and the resolver printed `default = 1.5 uV` under a control
  labelled "Target rise (deg C)". That was the units gap (G1 in their
  report), not a defect in the file.

  **The gap is closed as of refrain#98's first tranche.** Temperature is a
  base dimension, `C` is a surface unit, `temperature { }` is a control kind,
  and an input declares what its channels hold: `input "skin" { montage =
  bipolar(plus: "TH", minus: "T1"); unit = "C" }`. The honest version of this
  protocol types in Celsius from the input to the threshold, and
  `tests/test_resolve_dimensions.py` resolves exactly that shape. The fixture
  is left alone because its value here is as the record of what a downstream
  consumer actually sent.

- **It runs at 15 Hz with a DC-coupled differential sensor montage.** Nothing
  else in `examples/` or `bench/protocols/` is shaped like that — every other
  protocol in the corpus is AC-coupled EEG at 250–256 Hz. It exercises
  `bipolar()` as a differential *sensor* measurement rather than an EEG
  montage, `seed = percentile`, and a `formula` derive at a low rate.

The author's own header notes it is a draft, not validated against a human.
