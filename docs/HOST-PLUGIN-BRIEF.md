# Host-application integration brief

This is a **prompt template** for briefing a Claude Code session
working in a host application repo (an EEG recorder, an LSL relay,
clinical workstation, etc.) about Refrain. The session it briefs is
expected to:

1. Map the host application's existing architecture
2. Read Refrain's docs without touching the implementation
3. Produce a *design document* — not code — proposing how a Refrain-
   backed NF plugin should fit into the host

Adapt the absolute paths at the start to wherever you have Refrain
checked out on the machine running the host-side Claude Code session.

---

```
# Build a basic neurofeedback plugin spec for the recorder

You are picking up the design phase for embedding Refrain — an open-
source NF protocol engine — into this recording application. Your job
this session is *not* to write the plugin yet. Your job is to brief
yourself on the codebase and Refrain, then produce a design document
that proposes a minimal NF plugin architecture appropriate to this
recorder. Brainstorm patient-feedback modalities, clinician workflow,
session lifecycle, and integration points. Push back on parts of the
default Refrain integration that don't fit this recorder's grain.

## What Refrain is, briefly

Refrain is a declarative description language for clinical
neurofeedback protocols. A `.refrain` text file describes a complete
NF protocol: hardware requirements, electrode montage, signal
processing pipeline, threshold logic, reward expression, output
bindings, clinician-tunable controls, session structure. A Python
package (`pip install refrain[eval]`) parses these files, type-checks
them against an amp profile, and provides a streaming evaluator that
turns chunks of EEG into reward events.

Refrain lives at `/Users/jcroall/git/refrain/refrain` on this machine.
The repo is structured with the language design in `docs/` (CONCEPT,
SPEC, TOUR, PRIMITIVES) and the implementation in `src/refrain/`.

What Refrain does:
- Parse and validate protocol files
- Type-check expressions, validate hardware against an amp profile
- Compute reward / inhibit / output signals from streaming EEG chunks
  using a curated math library (bandpass, hilbert, magnitude, smooth,
  bandpower, coherence, percentile threshold, dwell, sigmoid, mute,
  etc. — see `docs/PRIMITIVES.md`)
- Emit timestamped events (chimes, gain values) back to the host
- Expose internal computed values (envelope traces, threshold lines,
  dwell sub-conditions, pre-gating reward, post-gating output) via
  `Evaluator.last_taps()` for clinician observation windows

What Refrain explicitly does NOT do:
- Own amp acquisition
- Render patient-facing audio, video, or ambient effects
- Manage session UI, scheduling, or storage
- Connect to clinical EHRs, billing, anything clinical-product-shaped

Those are this recorder's responsibilities. Refrain is the math engine.

## Read first

In this order. Don't skip any of these — your design will be naive
without them.

1. `/Users/jcroall/git/refrain/refrain/docs/EMBEDDING.md` — the
   integration walkthrough for hosts wiring Refrain in. The five-
   method API surface is here. Read top to bottom.

2. `/Users/jcroall/git/refrain/refrain/docs/CONCEPT.md` — what Refrain
   is for and why it exists. You don't need to memorize this but you
   need to understand the framing: protocols are *clinical artifacts*,
   not implementation details. The recorder is a host for these
   artifacts; clinicians will compose / share / cite them.

3. `/Users/jcroall/git/refrain/refrain/examples/smr_cz_brainbit.refrain`
   — a complete protocol file. Read every line. Understand what each
   block does. This is the protocol you're targeting for the first
   working version of the plugin.

4. `/Users/jcroall/git/refrain/refrain/docs/SPEC.md` §4.7, §4.8, §7
   — reward block shape, output bindings, and runtime semantics. You
   need this to understand what events the evaluator will emit and how
   inhibits gate them.

5. `/Users/jcroall/git/refrain/refrain/src/refrain/amp_profiles/brainbit_flex.json`
   — the amp profile for the target hardware. The 4-channel layout
   and AC coupling shape what the plugin can do.

6. `/Users/jcroall/git/refrain/refrain/docs/PRIMITIVES.md` — the
   standard library of math operators that appear inside protocol
   files. Skim the table-of-contents and scan: Acquisition (bipolar,
   referential), Spectral (bandpass, hilbert, bandpower, coherence),
   Time-series (magnitude, rectify, smooth, differentiate),
   Statistics (auto_range, percentile), Mappings (sigmoid, linear),
   Conditions (above/below/inside/all_of/any_of), Events (dwell),
   Inhibit actions (mute/freeze/flag). You don't need to memorize
   parameters; you need to know what's in the standard library when
   a clinical question lands. Coherence training (e.g. "deep state /
   flow" via inter-hemispheric alpha synchrony) is supported as of
   refrain==0.1.0.

You do NOT need to read the implementation in `src/refrain/`. Treat
the package as a black box with the API documented in EMBEDDING.md.

## Refrain version to pin against

Pin to `refrain==0.1.0` (or a newer version that is
backward-compatible — Refrain follows semver, and v0.x.y bumps are
additive). The available primitive surface as of v0.1.0 is in
`docs/PRIMITIVES.md`. If you find yourself wanting a primitive
that's not in the standard library, flag it as a Refrain feature
request rather than working around it host-side — the standard
library is intentionally small but grows on demand.

## The integration contract — five methods

```python
import refrain
from refrain.amp_profile import load_amp_profile
from refrain.resolver import resolve
from refrain.eval_ import Evaluator

# Once per session:
ir = resolve(
    refrain.parse_file("smr_cz_brainbit.refrain"),
    load_amp_profile("brainbit_flex.json"),
)
evaluator = Evaluator.live(
    ir,
    sample_rate_hz=250,
    channel_names=("Cz", "F3", "F4", "Pz"),  # your placement
)
evaluator.start()  # → enters warmup automatically

# In your amp data callback:
for event in evaluator.step_chunk(chunk):  # chunk: (n_samples, n_channels) float64
    # event.channel: "audio_chime" | "audio_gain" | ...
    # event.kind:    "event" | "value"
    # event.value:   None for events, [0, 1] float for value
    dispatch_to_renderer(event)

# Clinician adjusts a knob:
evaluator.set_control("smr_target_pct", 65)

# End of session:
evaluator.stop()
```

That's the whole surface. `evaluator.state` and
`evaluator.warmup_remaining_s` are read-only properties for UI.

The protocol declares which output channels exist (`audio_chime`,
`audio_gain`, `video_clarity`, `ambient_density`, ...). The renderer
dispatches on channel name; unknown channel names just don't render.

## Your job this session — produce a design document

Save it as `docs/refrain-plugin-design.md` (or wherever your project
convention puts design docs). Cover:

1. **Codebase orientation.** Spend the first portion of this session
   mapping the existing recorder. Find: where amp samples come in,
   where they currently get processed/displayed, what plugin or
   extension mechanism (if any) already exists, how the recorder
   manages sessions today. Summarize what you find before proposing
   anything. The plugin should fit the recorder's grain, not impose
   a foreign architecture.

2. **Plugin shape.** Where does the Refrain plugin sit in the
   recorder's data flow? Propose a concrete location (file path,
   class name) and a clean interface between the plugin and the rest
   of the recorder. If a plugin system already exists, fit into it;
   if not, sketch what a minimal one looks like.

3. **Patient feedback design — brainstorm openly.** SMR Cz with the
   default output bindings produces:
     - `audio_chime` — discrete event, fires when SMR is above
       threshold and theta/high-beta are below, sustained 250 ms
     - `audio_gain` — analog [0, 1], gated to zero unless the dwell
       condition is currently met (in the existing example)

   For each output channel:
     - What patient experience does this map onto?
     - What's the simplest renderer that demonstrates the protocol
       is working?
     - What rendering options does the recorder already have?
     - What's worth building for a v1 patient experience, and what's
       a stretch?

   Brainstorm broadly: ambient soundscapes vs single tones vs music
   gain-modulation; abstract visual vs game-shaped vs nothing-visual;
   haptic, ambient lighting, anything else the recorder can drive.
   Pick one to recommend for v1 with reasoning.

4. **Clinician workflow.**
   - How does the clinician load a `.refrain` protocol file? (file
     picker? built-in library? URL?)
   - How do they confirm electrode placement matches the protocol's
     `requires.channels`?
   - How do they start / stop a session?
   - During warmup, what's shown?
   - During run, what live values do they want visible? (current
     SMR envelope, current threshold, recent event count, …)
   - Live tunable controls — how is the UI? (knob? slider? text
     entry?)
   - Where does the recorded EEG go? Does the recorder save events
     too?
   - What does post-session review look like?

5. **Error handling and edges.**
   - BrainBit disconnect mid-session: what happens?
   - Dropped/late samples: tolerable up to what threshold?
   - Protocol load fails (file not found, parse error, hardware
     mismatch with amp profile): how is this surfaced?
   - Resource budget exceeded: what's the failsafe?
   - Session crash recovery: not in scope for v1?

6. **Telemetry and logging.** What gets logged for post-session
   review? Just the EEG, just the events, both? Format? Storage
   location? Privacy considerations?

7. **Validation strategy.** How will you know the plugin actually
   works when integrated end-to-end? Bench-test plan: synthetic data
   first, then your own EEG, then someone else. What's the bar for
   each stage?

8. **Out of scope for v1.** What are you explicitly NOT building?
   (Multi-protocol library, EHR integration, billing, multi-amp,
   cloud sync, anything else you sense in the codebase that you
   need to defend the scope against.) Be explicit; this is where
   most NF projects bog down.

9. **Open questions.** Anything you can't decide without input from
   the user. Be specific; vague open questions are useless. Each
   open question should have a concrete decision-point with two or
   three named options.

## Constraints

- **Don't write code yet.** A design document with concrete
  proposals. Code lands in a follow-up session after this design is
  reviewed.
- **No new dependencies** beyond what the recorder already uses,
  except `refrain` itself. Pin to `refrain==0.1.0` from PyPI when
  it's published, or `pip install -e /Users/jcroall/git/refrain/refrain`
  during local development.
- **Single protocol first.** Don't design for the full protocol
  library yet. SMR Cz on BrainBit working end-to-end is the v1 bar.
- **Refrain owns the math.** Don't propose duplicating Refrain's
  filter pipeline in the recorder. If Refrain's evaluator can't do
  something the design wants, flag it as a Refrain-side feature
  request, don't work around it.

## Before you start

Confirm in plain prose:
1. What you understand the gating goal of this session to be
2. The high-level architectural fit between this recorder and Refrain
   as you see it after reading EMBEDDING.md
3. What you'll do first to orient yourself in this codebase

Then start.
```
