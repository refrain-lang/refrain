# Protocol Fuzzer

> **Status:** designed, not yet implemented. The full architecture lives in the
> [design spec](superpowers/specs/2026-05-27-protocol-fuzzer-design.md); this
> doc is the friendlier introduction.

A tool that reads a Refrain protocol's own definition, manufactures fake EEG it
already knows the answer to, runs the evaluator against it, and **checks
reality against the prediction**. Two questions it answers from one machine:

1. **Is the engine correct?** Does the evaluator faithfully implement what the
   protocol *means*?
2. **Is my protocol correct — and do I understand the language?** When you've
   just written a `.refrain` file, does it actually do what you intended?

Plus a bonus for a brand-new language: **where is the SPEC ambiguous?** Building
an independent prediction forces the semantics to be pinned down. Where the
spec is silent, an oracle-vs-evaluator disagreement is neither an engine bug
nor a protocol bug — it's a **spec gap**, surfaced rather than hidden.

---

## Why it exists

A protocol is a recipe: *"watch the brainwaves, play a reward chime when SMR is
up, theta is down, and there's no muscle artifact — but only if that state
holds for a quarter second."* Two things could be broken and you'd have no easy
way to know:

- The **engine** might compute the recipe wrong.
- Your **recipe** might not say what you meant. (Easy when you're learning the
  language — you write `below` where you meant `above`.)

Today the only honest check is to hook up a real EEG amp, sit there, and
eyeball whether the chime fires at the right moments. Slow, expensive, and
you're just trusting your eyes.

The fuzzer replaces all of that with an automated, reproducible loop.

---

## The core trick

Instead of feeding the engine real, messy brain data and *hoping* you can tell
if it behaved, you feed it **fake data you designed on purpose** — and because
you designed it, **you already know what should happen.**

> It's like testing a thermostat. You don't wait for a hot day. You hold a
> lighter to the sensor and check that the AC kicks on. You *control the input*
> so you *know the right output*.

The "lighter" is synthetic EEG. The tool reads the protocol, sees it cares
about the 12–15 Hz band, and manufactures a fake signal with exactly that
frequency turned up loud — and every other band quiet. Now it can say with
confidence: *"Given this input, the chime should fire at 3.2 seconds."* Then
it runs the real engine and checks: **did it?**

---

## What happens, start to finish

Take the SMR protocol (`examples/smr_cz.refrain`) as the running example. The
fuzzer:

### 1. Reads the recipe

It pulls the testable structure out of your protocol: the three frequency
bands (SMR 12–15, theta 4–8, high-beta 22–30), the thresholds (two adaptive,
one fixed at 8 µV), the `all_of` rule, the 250 ms hold, the controls, the
session phases. This is the "knowledge of the protocol" everything else uses.

### 2. Designs a batch of scenarios

Each scenario is engineered to poke a specific part of the logic. For SMR:

| Scenario | What it does | Should happen |
|---|---|---|
| *SMR up, theta down, artifact quiet* | drives every condition true | chime **fires** |
| *Same, but theta creeps up* | breaks one condition | chime **doesn't fire** |
| *Good state held a hair too short* | misses the 250 ms hold | chime **doesn't fire** |
| *High-beta spike* | trips the artifact threshold | chime **doesn't fire** |
| *Total silence* | nothing in any band | nothing ever fires |
| *Tone sweep across spectrum* | characterization probe | filters peak at the declared bands |
| *Rank sweep across the percentile* | gradually push SMR above threshold | firing rate **monotonically increases** |

### 3. Two halves work independently and in parallel

- One half **writes down the prediction** — "chime here, silence there" —
  working purely from the protocol's logic and the math of its filters.
- The other half **renders the scenario into actual fake EEG samples** and
  runs them through the real engine, just like a recording would be.

These two halves never talk to each other. That's deliberate: if they shared
assumptions, they could both be wrong in the same way and the test would pass
anyway.

### 4. Compares predictions to reality

Match everywhere → pass. A chime where there shouldn't be one, or a missing
chime where there should be → it flags exactly which scenario, which moment,
and (in the report) which branch of the protocol's logic is implicated.

---

## What you get back

`refrain fuzz protocol.refrain` produces a report with two co-equal sections:

### "What your protocol does"

A plain-language behavioral summary derived from the predictions, across all
scenarios. Something like:

> Reward fires when SMR is up, theta is down, and high-beta is quiet — and it
> stops the instant theta crosses its threshold or high-beta spikes above 8 µV.
> The 250 ms hold smooths over brief dips. During warmup and cooldown, output
> is muted.

You read that and compare it to what you **meant**. If you fat-fingered `below`
instead of `above`, the summary will say *"reward fires when SMR is **low**"*
and your mistake jumps out — **even though the engine is working perfectly.**
This is the part that catches *your* mistakes and teaches you the language.

It also flags **structural smells**: a branch the fuzzer couldn't drive
(threshold set impossibly high, two conditions mutually exclusive, an inhibit
that always fires). Free protocol QA.

### "Engine check"

Pass / fail across all scenarios, with a coverage matrix showing which branches
of the logic were exercised true *and* false. A mismatch here means the
**engine** is wrong, not your recipe. This is the part that catches the
*program's* mistakes — a stronger sibling of the existing bench equivalence
gate, because no per-protocol hand-written baseline is needed.

### Diagnosis at a glance

| Disagreement | Diagnosis |
|---|---|
| prediction ≠ engine output | **engine bug** |
| protocol behavior ≠ your intent | **protocol bug** (you mis-wrote it) |
| a branch can't be reached or asserted | **protocol smell** |
| prediction ≠ engine output, but the SPEC is silent on the case | **spec gap** |

---

## Why you can trust it

Two honesty rules keep the tool from quietly lying to you.

### The prediction never asks the engine

The prediction is computed from the protocol's logic and from the math of the
filters directly — using the filter coefficients baked into the IR-JSON, but
**evaluating their transfer function**, not **running the cascade**. So it
doesn't peek at the engine's answer. The one residual risk — that the
coefficients themselves are wrong, and both predictor and engine share that
error — is closed by a dedicated **band-response characterization probe**: a
tone sweep that asserts each band actually peaks where the protocol *declared*
it should.

### Don't-care is principled, explained, and measured

Near a threshold the right answer is genuinely on a knife's edge — the signal
is noisy there, so even a correct engine flickers. Rather than cry wolf, the
tool marks those moments **DON'T-CARE** and only renders a hard pass/fail
where it's truly confident.

Three guards keep don't-care honest:
- **Every don't-care interval carries a reason** (near-boundary / settle-collar
  / pre-window-fill / phase-muted) — shown in the report so you trust *why* it
  stayed silent.
- **The coverage matrix distinguishes "branch driven and crisply asserted"
  from "branch driven only in don't-care."** A branch only ever seen in
  don't-care is flagged as "reachable but unassertable" — a coverage gap as
  serious as unreachable.
- **A scenario that made zero crisp assertions fails loud** as a generator
  bug. "All green" can't secretly mean "checked almost nothing."

For boundary sensitivity itself — where the don't-care zone sits — the tool
adds two narrow **monotonic relations**: as SMR rank crosses the percentile
threshold, firing rate must be **non-decreasing**; as hold duration crosses
the dwell, firing must be **non-decreasing**. These assert the *direction* of
behavior at boundaries without an absolute prediction.

---

## In one sentence

> It turns your protocol into its own test suite — manufacturing brain signals
> it already knows the answer to, then checking that both your recipe and the
> engine behave.

---

## Scope and limits

**v1 targets the SMR protocol** end-to-end (montage, three envelopes, both
threshold kinds, the `all_of` rule, the 250 ms dwell, phase-gated outputs).
That's enough to exercise the whole pipeline before generalizing across the
rest of the IR.

**Generalization to other protocols** — coherence, weighted-composite reward,
multi-site placement, the `inhibit`-primitive masking path — is deliberately
deferred. The architecture (a "scenario" contract consumed independently by
predictor and renderer) is built so those drop in as they're added to the
language.

**Randomized fuzzing with shrinking** (Hypothesis-style search for unexpected
edge cases) is layer 2. v1 is **directed**: it enumerates the protocol's
logical branches systematically, which gives a reproducible coverage gate and
a worked example of every branch. Randomized search builds on the same
substrate.

---

## See also

- [Design spec](superpowers/specs/2026-05-27-protocol-fuzzer-design.md) —
  the full architecture, the oracle's DSP math, the four soundness treatments,
  TDD strategy, and the open SPEC questions surfaced by this work.
- [`src/refrain/synthetic.py`](../src/refrain/synthetic.py) — the existing
  hand-scheduled synthetic generator the fuzzer extends.
- [`bench/baselines/_dsp.py`](../bench/baselines/_dsp.py) — the reference DSP
  the fuzzer's predictor models analytically.
