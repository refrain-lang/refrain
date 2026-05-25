# Rust-core host-integration brief (mobile: Swift / Kotlin)

This is a **prompt template** for briefing a Claude Code (or similar) session
working in your **neurofeedback app repo** (iOS/Swift and/or Android/Kotlin)
about consuming the portable Refrain **Rust core**. The session it briefs is
expected to:

1. Map the app's existing architecture (acquisition, audio/render, session UI)
2. Read this brief + the linked contract without touching the core's internals
3. Produce a *design document* — not code — proposing how the Refrain Rust
   core plugs into the app

It mirrors `docs/HOST-PLUGIN-BRIEF.md` (which targets the Python package); use
**that** one if your host is Python/desktop, and **this** one for the mobile app.
Adapt the paths/URLs at the top to wherever the core's bindings are published.

---

```
# Design a neurofeedback feedback layer over the Refrain Rust core

You are picking up the design phase for embedding the Refrain Rust core — a
portable, compiled neurofeedback protocol engine — into this app. Your job this
session is *not* to write the integration yet. Brief yourself on this codebase
and the core's contract, then produce a design document proposing how a
Refrain-backed feedback layer fits this app. Push back on parts of the default
integration that don't fit this app's grain.

## What the Refrain Rust core is

Refrain is a declarative description language for clinical neurofeedback
protocols (a `.refrain` file = hardware montage, DSP pipeline, threshold logic,
reward/inhibit expressions, output bindings, clinician controls, session
phases). Protocols are authored/compiled OFF-DEVICE by the Refrain Python
front-end into a portable wire format called **IR-JSON** (the protocol, fully
resolved and type-checked, with filter coefficients pre-baked). 

The **Rust core** is a single compiled library that loads an IR-JSON asset and
turns streaming EEG chunks into feedback events — the SAME implementation that
runs on desktop (validated to machine-precision parity against the Python
reference). On this app it ships as:
  - iOS: a uniffi-generated **Swift** package wrapping an `xcframework`
  - Android: a uniffi-generated **Kotlin** library in an **AAR** (cargo-ndk)

What the core does:
- Deserialize an IR-JSON protocol (no parser/Python needed on-device)
- Per chunk of EEG, compute montage → derives (bandpass/hilbert/magnitude/
  smooth/bandpower/coherence) → thresholds (percentile/absolute) → inhibits →
  reward (sigmoid continuous + dwell events) → output bindings
- Return timestamped feedback events (e.g. `audio_chime` discrete, `audio_gain`
  analog [0,1]) for the app to render
- Expose internal computed values (envelope traces, threshold lines, dwell
  sub-conditions, pre/post-gating reward) for clinician observation

What the core explicitly does NOT do (these are the APP's job):
- Acquire EEG (BLE/headset SDK/LSL — platform-specific, stays host-side)
- Render patient-facing audio/video/haptics/ambient effects
- Manage session UI, scheduling, storage, or any clinical-product surface
- Author or compile `.refrain` files (that happens off-device; the app ships
  pre-compiled IR-JSON assets)

The core is the math engine. It is acquisition- and transport-agnostic.

## Two contract facts that bite if missed

1. **Sample rate is a host input, baked at compile time.** The IR-JSON is
   emitted FOR a specific runtime sample rate (the rate your amp actually
   streams), because filter coefficients are baked at that rate. Ship the
   IR-JSON variant matching your device's rate; pass the same rate when you
   construct the core. (The protocol only declares a *minimum* rate.)
2. **`channels` you pass is the physical acquisition layout, not the protocol's
   required channels.** A protocol may `require` `["Cz"]` but use a
   `linked_ears` reference — so you must pass the full electrode layout your amp
   provides, e.g. `["Cz","A1","A2"]`, including reference electrodes. The
   montage resolves names against the layout you pass.

## The integration contract (uniffi surface)

Swift:
```swift
import RefrainCore

// Once per session — load the pre-compiled protocol asset:
let irJson = try String(contentsOf: protocolURL)           // bundled IR-JSON
let core = try RefrainCore(
    irJson: irJson,
    sampleRateHz: 250.0,                                   // must match the bake
    channelNames: ["Cz", "A1", "A2"]                       // physical layout
)

// In your amp callback (chunk = n_samples * n_channels, row-major f64):
let events = core.stepChunk(chunk: flatSamples, nChannels: 3)
for e in events {
    switch e.kind {
    case .event: renderer.fire(e.channel)                  // e.g. "audio_chime"
    case .value: renderer.set(e.channel, e.value)          // [0,1], e.g. "audio_gain"
    }
}

// Clinician adjusts a knob:
core.setControl(name: "smr_target_pct", value: 65.0)

// End of session:
core.stop()
```

Kotlin is the mirror image (same method names, `stepChunk(chunk: DoubleArray,
nChannels: Int)`). The exact event/record types are generated by uniffi; treat
them as the documented contract, not the Rust internals.

## Read first (in order)

1. The Refrain Rust core production roadmap
   (`docs/superpowers/plans/2026-05-24-rust-core-production-roadmap.md`) — what
   the core is and the binding it exposes. Read the M4 (mobile) section.
2. `docs/EMBEDDING.md` — the host division-of-labour and the five-method
   embedding model (the Rust surface mirrors it). Read top to bottom.
3. `docs/IR-JSON.md` (once written in M5) — the wire format your app loads, and
   the sample-rate / channel-layout rules above.
4. One example protocol's IR-JSON asset (the build produces these) — open it to
   see the output channels your renderer must handle.
5. `docs/PRIMITIVES.md` — the math library that appears in protocols, so you
   know what feedback signals a protocol can produce.

You do NOT need to read the Rust source. Treat the core as a black box behind
the uniffi contract above.

## Your job this session — produce a design document

Save it where this app keeps design docs. Cover:

1. **Codebase orientation.** Where do amp samples arrive? What's the current
   audio/render stack? How are sessions managed today? What's the threading
   model of the acquisition callback (the core's `stepChunk` is synchronous and
   single-threaded; if your callback can't tolerate ~sub-ms work, propose a
   worker queue)? Summarize before proposing.
2. **Where the core sits** in the data flow — concrete file/module/class, and a
   clean boundary between "core returns events" and "app renders them".
3. **Asset pipeline.** How do IR-JSON protocol assets get into the app bundle,
   and how do you guarantee the bundled rate matches the device's actual stream
   rate? (One asset per supported rate? Resample host-side to a single baked
   rate?) This is the #1 correctness trap — design it explicitly. Note: if a
   protocol uses parameterized `placement` (clinician-chosen sites), that
   binding is resolved **off-device** in the Python front-end (the build that
   emits IR-JSON) — the core only ever loads a fully site-bound IR-JSON. So
   either bundle one pre-bound asset per site configuration, or run the front-end
   server-side and deliver the resolved IR-JSON; the on-device core never binds
   placement.
4. **Channel mapping.** How do you map the headset's electrode layout to the
   `channelNames` you pass, including reference electrodes? How do you surface a
   placement mismatch to the clinician?
5. **Patient feedback design.** For each output channel a protocol declares
   (`audio_chime` event, `audio_gain` value, …): what patient experience, what
   minimal renderer proves it works, what's v1 vs stretch?
6. **Clinician workflow.** Protocol selection, placement confirmation,
   start/stop, warmup display, live values to show (envelope, threshold, recent
   events — via the core's taps), live-tunable controls UI.
7. **Error handling.** Headset disconnect mid-session; dropped/late samples
   (tolerance?); asset load failure; rate mismatch detection; session crash
   recovery (in scope for v1?).
8. **Telemetry.** What's logged for review — raw EEG, events, taps? Format,
   storage, privacy.
9. **Validation.** Synthetic chunks → your own EEG → another person. The core
   ships with golden-vector conformance tests; reuse them to prove the on-device
   build matches the reference.
10. **Out of scope for v1** and **open questions** (each with 2–3 named options).

## Constraints

- **Don't write code yet** — a design document with concrete proposals.
- **Don't reimplement any DSP host-side.** If the core can't compute something
  the design wants, flag it as a Refrain-side feature request (a new primitive
  or an IR-JSON change), don't work around it. The core is the one canonical
  implementation; duplicating math defeats the reproducibility goal.
- **Acquisition + rendering are yours; math is the core's.** Keep that line clean.
- **Pin the core version** and the IR-JSON schema version your assets target.

## Before you start, confirm in plain prose

1. The gating goal of this session.
2. The architectural fit between this app and the Rust core after reading
   EMBEDDING.md + the contract above.
3. What you'll do first to orient in this codebase.

Then start.
```
