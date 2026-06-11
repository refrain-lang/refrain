# `flutter_cue` Protocol + Gap RFC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author the `flutter_cue.refrain` example protocol — a NeurOptimal-style dynamical neurofeedback protocol that detects EEG "flutter" (variance + critical-slowing-down) across 20 envelopes and cues the trainee with a brief audio takeaway — verify it resolves and runs, and write the gap RFC.

**Architecture:** The protocol authors one per-band signal subgraph (envelope → variance + autocorrelation → fused EWS score → flutter inhibit) and relies on **band fan-out** (plan 2) × **per-site fan-out** (channels) to expand it to 10 bands × C3/C4 = 20 envelopes. Audio is tonic (`reward = 1.0`); per-envelope `mute(release:)` inhibits OR-compose to deliver the takeaway. Uses the new `autocorr` primitive (plan 1).

**Tech Stack:** the Refrain `.refrain` language, the CLI (`refrain check`/`resolve`/`run`), the streaming evaluator, pytest, synthetic signal sources (`src/refrain/synthetic.py`).

**This is plan 3 of 3** from `docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md`. **Depends on plan 1 (`autocorr`) and plan 2 (band fan-out) being implemented and merged** — the protocol uses `autocorr(...)` and the `bands { }` block. Do not start Task 1's verification steps until 1 and 2 are in this worktree.

---

### Task 1: Author `flutter_cue.refrain` and verify it resolves

**Files:**
- Create: `examples/flutter_cue.refrain`
- Test: CLI (`refrain check`, `refrain resolve`)

- [ ] **Step 1: Write the protocol**

Create `examples/flutter_cue.refrain`:

```refrain
// NeurOptimal-style dynamical neurofeedback: detect "flutter" (an early-warning
// surge in a band envelope's variability that precedes a phase-state shift) and
// cue the trainee with a brief audio "takeaway" — no operant target, no reward
// to chase. Flutter = the fused early-warning signature: rolling VARIANCE (fast)
// + rolling lag-1 AUTOCORRELATION (critical slowing down) of each band envelope,
// each surging above its own self-calibrating baseline (the micro-AutoNav).
//
// 10 bands/hemisphere x {C3, C4} = 20 envelopes, authored once and fanned out
// over the bands{} block (band axis) and the `sites` set placement (channel axis).
//
// Design: docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md
// Grounding: Scheffer 2009 (Nature), Dakos 2012 (PLoS ONE), Maturana 2020
// (Nat. Commun.), Yang 2012 (J. Neurosci.). Research software, not a medical device.

protocol "flutter_cue_v1" {

  meta {
    version          = "0.1.0"
    evidence         = "demo"
    description      = "Dynamical flutter-cue: variance + critical-slowing-down across 20 envelopes, audio takeaway"
    author           = "Bay Area Peak Performance"
    citation         = "Scheffer et al. 2009 Nature 461:53-59; Maturana et al. 2020 Nat Commun 11:2172; Yang et al. 2012 J Neurosci 32:1061"
    population        = "adults_18_plus"
    indication        = "self_regulation_wellness"
    safety_monitoring = ["pre_session_check", "intra_session_clinician_observation"]
  }

  requires {
    sample_rate = ">= 256 Hz"
    channels    = ["C3", "C4"]
  }

  // Band axis: 10 bands/hemisphere. delta..gamma; sub-Hz is out (see gap RFC).
  bands {
    delta  = (1 Hz, 4 Hz)
    theta1 = (4 Hz, 6 Hz)
    theta2 = (6 Hz, 8 Hz)
    alpha1 = (8 Hz, 10 Hz)
    alpha2 = (10 Hz, 12 Hz)
    smr    = (12 Hz, 15 Hz)
    beta1  = (15 Hz, 18 Hz)
    beta2  = (18 Hz, 22 Hz)
    beta3  = (22 Hz, 30 Hz)
    gamma  = (30 Hz, 45 Hz)
  }

  controls {
    // Channel axis: a 2-site set, fanned out per hemisphere.
    sites = placement {
      kind    = "set"
      default = ["C3", "C4"]
      allowed = ["C3", "C4"]
      min     = 1
      max     = 2
    }
    // The one live knob = micro-AutoNav sensitivity. Higher pct => fewer, more
    // selective takeaways; self-calibrating to the trainee's own EWS distribution.
    sensitivity = percent {
      default      = 90
      range        = (70, 99)
      label        = "Flutter sensitivity (EWS score percentile)"
      live_tunable = true
    }
  }

  // ----- per-band x per-channel subgraph (authored once; fanned out) -----

  input "raw" {
    montage = referential(active: sites, reference: "linked_ears")
  }

  // Band envelope (the "Time-Frequency Envelope").
  derive "env" {
    from = "raw"
    pipeline = [ bandpass(band: bands, order: 4), hilbert(), magnitude() ]
  }

  // Fast indicator: rolling variance  Var = E[e^2] - E[e]^2.
  derive "var" {
    formula = smooth("env" * "env", tau: 1 s) - smooth("env", tau: 1 s) * smooth("env", tau: 1 s)
  }
  derive "varN" { from = "var"; pipeline = [ auto_range(window: 30 s) ] }   // -> [0,1]

  // Principled indicator: critical slowing down (lag-1 autocorrelation of the
  // envelope). lag 125 ms (not 1 sample!) so it spans a meaningful interval.
  derive "ac1"  { from = "env"; pipeline = [ autocorr(lag: 125 ms, window: 4 s) ] }  // -> [-1,1]
  derive "acN"  { from = "ac1"; pipeline = [ auto_range(window: 30 s) ] }   // -> [0,1]

  // Fuse the two normalized indicators into one EWS score. max() = OR-like
  // (fire if EITHER surges). For AND-like specificity use "varN" * "acN".
  derive "score" {
    formula = ("varN" > "acN") ? "varN" : "acN"
  }

  // The takeaway: when the EWS score surges above its own recent baseline, mute
  // the audio briefly. Per-envelope inhibits OR-compose, so any flutter anywhere
  // takes the sound away. (release doubles as the refractory.)
  inhibit "flutter" {
    metric    = "score"
    threshold = percentile(target_pct: sensitivity, window: 2 min)
    action    = mute(release: 400 ms)
  }

  // Tonic audio, no operant target. Inhibits above mute it on flutter.
  reward { continuous = 1.0 }
  output { audio_gain = reward.continuous }
}
```

- [ ] **Step 2: `refrain check` — verify it parses + type-checks**

Run: `cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/flutter-cue && python -m refrain check examples/flutter_cue.refrain`
Expected: OK / no errors. If it errors:
- `evidence`/`indication` enum rejected → grep `resolver.py` for the allowed enum values and pick valid ones (e.g. `evidence = "experimental"` if allowed, else keep `"demo"`).
- `reward { continuous = 1.0 }` literal rejected → change to `continuous = 1.0 * 1.0` or the minimal accepted always-on expression (grep `test_eval_*` for a constant-continuous precedent); the takeaway only needs `audio_gain` muted by the inhibits.
- duration control not needed here (all timescales are literals) — sensitivity is `percent`, which is proven (`smr_cz.refrain`).

- [ ] **Step 3: `refrain resolve` — verify the 20-envelope fan-out**

Run: `python -m refrain resolve examples/flutter_cue.refrain | python -c "import sys,json; ir=json.load(sys.stdin); print('inhibits:', len([k for k in ir.get('inhibits', {})]))"`
(Adjust to the actual `refrain resolve` output shape — it may print IR text, not JSON. If text, instead grep the resolved output for the count of `flutter@` inhibits.)
Expected: **20** flutter inhibits (`flutter@delta@C3` … `flutter@gamma@C4`), 20 `env@*@*`, 20 `score@*@*`, 2 inputs (`raw@C3`, `raw@C4`).

- [ ] **Step 4: Commit**

```bash
git add examples/flutter_cue.refrain
git commit -m "feat(examples): flutter_cue — NeurOptimal-style dynamical flutter-cue protocol"
```

---

### Task 2: Behavioral test — flutter triggers the takeaway

**Files:**
- Test: `tests/test_eval_flutter_cue.py` (new; mirror `tests/test_eval_coherence_integration.py`)

- [ ] **Step 1: Write the failing behavioral test**

Create `tests/test_eval_flutter_cue.py` (mirror the coherence integration test structure — the `backend` fixture, `Evaluator.live`, synthetic source, output capture):

```python
"""flutter_cue: a variance/autocorrelation surge mutes the tonic audio (takeaway)."""
import numpy as np
from pathlib import Path
from refrain import parse, resolve          # adjust imports to the test-suite's helpers
from refrain.eval_ import Evaluator         # adjust to the real evaluator entrypoint

SR = 256.0
PROTO = Path("examples/flutter_cue.refrain")

def _signal_calm_then_flutter(n_calm, n_flutter):
    """Two C3/C4 channels: calm (steady 10 Hz) then a turbulent burst (amplitude-
    modulated, autocorrelation-raising) in the alpha band, on both channels."""
    rng = np.random.default_rng(0)
    t_calm = np.arange(n_calm) / SR
    calm = 5.0 * np.sin(2 * np.pi * 10 * t_calm) + 0.5 * rng.standard_normal(n_calm)
    t_fl = np.arange(n_flutter) / SR
    # rising-variance, slowing burst: amplitude swells and the carrier drifts slower
    burst = (5.0 + 8.0 * np.sin(2 * np.pi * 0.5 * t_fl)) * np.sin(2 * np.pi * 10 * t_fl) \
            + 0.5 * rng.standard_normal(n_flutter)
    ch = np.concatenate([calm, burst])
    return np.stack([ch, ch])  # C3, C4 (shape [2, N]) — adjust to the source API

def test_flutter_cue_takeaway_fires_on_surge(backend):
    ir = resolve(parse(PROTO.read_text()), amp=<test amp profile with C3,C4>,
                 bindings={"sites": ["C3", "C4"]})
    sig = _signal_calm_then_flutter(int(60 * SR), int(20 * SR))
    ev = Evaluator.live(ir, sample_rate_hz=SR, backend=backend)
    gains = []
    for chunk in _chunks(sig, size=256):       # feed via the source/step API the suite uses
        out = ev.step_chunk(chunk)
        gains.append(out["audio_gain"])         # adjust to the real output accessor
    gains = np.concatenate(gains)
    calm_gain = gains[: int(55 * SR)].mean()     # tonic region (post warm-up)
    flutter_gain = gains[int(62 * SR):].mean()   # during the burst
    # Tonic audio ~1.0 when calm; muted (lower mean) during the flutter burst.
    assert calm_gain > 0.8
    assert flutter_gain < calm_gain - 0.1
```

> Fill the `<test amp profile…>` and source/step plumbing from the coherence integration test (`tests/test_eval_coherence_integration.py`) — reuse its amp-profile builder, `_chunks` helper, and output accessor verbatim. The assertion is the new part: tonic-when-calm, muted-during-flutter.

- [ ] **Step 2: Run it (requires plans 1 & 2 implemented)**

Run: `python -m pytest tests/test_eval_flutter_cue.py -q`
Expected: PASS on the python backend. If `flutter_gain` is not below `calm_gain`, the synthetic burst isn't crossing the 90th-pct EWS threshold — lower `sensitivity` in the test (bind a lower percentile) or increase the burst's variance/slowing, until the takeaway demonstrably fires. Document the final stimulus parameters in a comment.

- [ ] **Step 3: Run the rust backend too (parity of the takeaway)**

Run: `cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/flutter-cue && PATH="$HOME/.cargo/bin:$PATH" REFRAIN_EVAL_BACKEND=rust python -m pytest tests/test_eval_flutter_cue.py -q`
Expected: PASS — takeaway fires identically on both backends (autocorr parity from plan 1; fan-out is front-end so the IR is backend-agnostic).

- [ ] **Step 4: Commit**

```bash
git add tests/test_eval_flutter_cue.py
git commit -m "test(eval): flutter_cue takeaway fires on a variance/autocorrelation surge (both backends)"
```

---

### Task 3: Write the gap RFC

**Files:**
- Create: `docs/proposals/2026-06-11-dynamical-neurofeedback-gaps.md`

- [ ] **Step 1: Create the directory + write the RFC**

Create `docs/proposals/2026-06-11-dynamical-neurofeedback-gaps.md`:

````markdown
# RFC: Dynamical-neurofeedback gaps surfaced by the flutter-cue protocol

> Status: proposal / discussion. Companion to the design spec
> `docs/superpowers/specs/2026-06-11-flutter-cue-protocol-design.md` and the
> `flutter_cue` example. Captures what a NeurOptimal-style protocol wants that
> Refrain does not yet express cleanly, and what was deliberately left out of the
> first PoC.

## Context

The `flutter_cue` protocol reconstructs the core of NeurOptimal — detect a
"flutter" (early-warning surge in EEG variability) and cue with a brief audio
"takeaway" — using two new language pieces (`autocorr`, band fan-out) plus
existing primitives. Building it surfaced five gaps. Each is scoped here; none
block the PoC.

## 1. Session phases / macro-AutoNav

NeurOptimal flows a session through phases (warm-up → letting-go → working-deeply
→ release) and "AutoNav" auto-navigates between them, retuning targets/sensitivity
moment to moment. The PoC implements only the **micro** level of AutoNav — the
self-calibrating `percentile` threshold. The **macro** arc (different
band/sensitivity configs over a session, with automatic transitions) is
unmodeled.

Refrain already has `session { phases = [phase { … }] }` (timed warm-up/run/
cooldown) and staged/segmented protocols (the staged-protocols design: N blocks,
host-driven advance, named blocks gating which components emit). A macro-AutoNav
would extend that with **condition-driven** phase transitions and per-phase
sensitivity. Proposal: prototype phase-conditional sensitivity as a control whose
value is driven by a phase index, before any "auto" transition logic. Out of scope
until there's a concrete clinical ask.

## 2. First-class negative-feedback "takeaway" output

The PoC expresses pure negative feedback by a workaround: a contingency-free
`reward { continuous = 1.0 }` muted by per-envelope `inhibit … mute(release:)`.
This is clean but semantically inverted — `inhibit` was designed for artifact
rejection, not as the primary feedback channel.

Proposal: a first-class output form for "on by default, briefly interrupted on an
event," e.g.

```refrain
output { audio_gain = takeaway(on: <event/boolean stream>, hold: 400 ms) }
```

`takeaway(on, hold)` = 1.0 normally, 0.0 for `hold` after each rising edge of
`on`. This removes the degenerate-reward requirement and, crucially, accepts an
**arbitrary boolean/event stream** — which also resolves gap #3.

## 3. AND-combine over arbitrary booleans

The PoC tunes OR↔AND by fusing *normalized* indicators into one score
(`max` = OR-like, `*` = AND-like) and thresholding that. That works because the
fuse happens before a single threshold. A general "fire when condition A AND
condition B (each its own adaptive threshold)" needs a boolean → feedback path.
Today `all_of`/`any_of` produce booleans but the only way to turn a boolean into a
brief mute is the inhibit metric/threshold form (single metric, no raw-boolean
input). The `takeaway(on:)` output (#2) closes this: `on = all_of([above(varN,
tv), above(acN, ta)])`.

## 4. Infra-low / sub-Hz bands

NeurOptimal claims coverage to 0.001 Hz. The PoC caps at 1 Hz: variance and
autocorrelation at sub-Hz need minute-to-many-minute windows, impractical for a
live cue. Refrain *can* do sub-Hz (the Othmer ILF example trains a single slow
band to 0.0001 Hz with DC coupling), but as one slow reward band, not as many
fast variance/CSD detectors. Proposal: document the regime boundary; if infra-low
flutter is wanted, it is a distinct slow-detector design (long windows, DC
coupling, decimation) — not the same 20-envelope fast path.

## 5. `autocorr` detrend + CSD methodology, and multi-axis fan-out

- **`autocorr` detrend (deferred from plan 1).** v1 `autocorr(lag, window)`
  mean-centers within its window; it does not remove a slow linear trend. The EWS
  literature detrends (Gaussian-kernel or first-difference) first. Proposal: add
  `detrend: bool = true` (within-window linear detrend) and/or a `decimate`
  primitive so CSD can be computed at a chosen timescale without the
  oversampling-inflation footgun (lag-1-sample ≈ 1 at 256 Hz).
- **Higher-level `early_warning()` composite.** Optionally bundle variance + ρ₁
  into one indicator primitive once the two-indicator pattern is validated.
- **Multi-axis fan-out generalization.** The band fan-out (plan 2) plus the
  per-site pass already produce the band × channel cross product by composition.
  A general N-axis fan-out (arbitrary author-declared replication axes, declared
  combine semantics per axis) would subsume both; defer until a third axis is
  motivated.

## Non-goals

This RFC does not propose reproducing NeurOptimal's proprietary JTFA/NDS math.
The flutter-cue work is a transparent, citable reconstruction of the *mechanism*,
not a clone.
````

- [ ] **Step 2: Commit**

```bash
git add docs/proposals/2026-06-11-dynamical-neurofeedback-gaps.md
git commit -m "docs(rfc): dynamical-neurofeedback gaps (phases, takeaway output, sub-Hz, CSD methodology)"
```

---

### Task 4: Wire into docs + changelog

**Files:**
- Modify: `README.md` (the examples list, if it enumerates examples) and/or `examples/` index
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Reference the example + new features**

Add `flutter_cue.refrain` to any examples enumeration (grep `README.md` and `examples/` for how `othmer_ilf_*` / `alpha_theta` are listed) with a one-line description. Add a CHANGELOG entry under the next unreleased version: the `autocorr` primitive, the `bands { }` block + band fan-out, and the `flutter_cue` example.

- [ ] **Step 2: Final full-suite run**

Run: `cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/flutter-cue && python -m pytest -q && PATH="$HOME/.cargo/bin:$PATH" python refrain-core/tools/check_equivalence.py 2>&1 | tail -15`
Expected: full Python suite PASS; dual-backend drift gate PASS (incl. `autocorr` parity + `flutter_cue` if added to the corpus).

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md examples/
git commit -m "docs: list flutter_cue example + changelog (autocorr, bands fan-out, flutter-cue)"
```

---

## Self-review (completed by plan author)

- **Spec coverage:** Delivers spec deliverables ② (the `flutter_cue.refrain` protocol — montage, 10-band `bands` block, per-band EWS detector fusing variance + `autocorr`, adaptive `percentile` threshold, tonic-reward + `mute` takeaway, `sensitivity` control) and ③ (the gap RFC covering all five spec'd RFC items: phases/macro-AutoNav, first-class takeaway output, AND-on-booleans, sub-Hz bands, CSD methodology + multi-axis fan-out). Behavioral verification proves the takeaway fires (both backends).
- **Placeholder scan:** No TBD/TODO. The protocol and RFC are written in full inline. Three steps flag verify-then-adjust against real APIs (the `evidence` enum + constant-`continuous` acceptance in 1.2; the `refrain resolve` output shape in 1.3; the coherence-test plumbing to reuse in 2.1) — each with the exact fallback, not vague deferral. The synthetic-stimulus tuning in 2.2 is an explicit calibrate-until-it-fires loop, not a placeholder.
- **Type consistency:** The protocol's entity names (`env`/`var`/`varN`/`ac1`/`acN`/`score`/`flutter`) and the fan-out suffixes (`@<band>@<site>`) match plan 2's naming and the 20-envelope assertions. `autocorr(lag, window)` and the `bands { }` block match the surfaces defined in plans 1 and 2. `sensitivity` is a `percent` control (proven in `smr_cz.refrain`); all timescales are literals (no dependency on an unproven duration-control kind).
- **Dependency note:** Tasks 1.2+ require plans 1 (`autocorr`) and 2 (band fan-out) merged into this worktree first; stated in the header and Task 2 Step 2.
```
