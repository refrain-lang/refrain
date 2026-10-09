# Protocol-owned feedback and review, with a generic Recorder host

Date: 2026-10-09
Status: design for review
Scope: Refrain, refrain-protocols, and Coherence Recorder on macOS

## Intended outcome

Choosing SMR must not open an alpha/theta video or present an alpha/theta-only
cue. A protocol defines the meaning of its feedback, advice, and review
measures. Recorder validates and renders those declarations, stores the
clinician's media choices, and records what happened. Both a clean `main`
checkout and the installed macOS app must behave the same way.

This design follows the existing alpha/theta ownership inventory in
`refrain-protocols/docs/recorder-to-refrain-alpha-theta-migration.md`. It does
not change the clinical claims of either protocol or treat authored test EEG
as evidence of client outcomes.

## Verified starting point

- On Recorder `main`, `settings.ts` supplies one nonempty
  `youtubeMusicUrl` default for every neurofeedback session.
  `NFSetupDialog.tsx` loads and saves that global URL without checking the
  selected protocol; `App.tsx` opens it whenever present. This explains the
  SMR leak. The title of the remote video is not part of this verification.
- Protocol-library `main` already defines SMR and classic alpha/theta bands,
  rewards, inhibits, controls, and phases. Classic alpha/theta also declares
  warmup seeding and an autopilot policy. Refrain already exposes structured
  `advice`, `apply_advice`, `dismiss_advice`, and `mark_equipment_change` APIs.
  Recorder still runs a separate alpha/theta `GuidanceEngine`.
- Draft Refrain PR #101 adds `hold_for` and `at_or_above`. Draft protocols PR
  #30 uses them for narrow-band Approach, Crossover, and Sustained outputs and
  declares a melodic asset bundle and two attack times. Draft Recorder PR
  #200 renders these outputs but retains legacy protocol-ID gong rules, a
  three-second sustained-crossover detector, fixed three-role mixing, and
  alpha/theta-specific summaries and views.

## Chosen boundary

Refrain and its protocol files own **what constitutes feedback**: signal
conditions, guards, dwell, rearm, phase muting, output names, semantic roles,
advice policy, and named measures. Recorder owns **how a person hears or sees
it**: selecting licensed local assets, waveform mixing, safe output limits,
device routing, browser windows, UI widgets, capture files, and saved
clinician preferences. The protocol may permit an optional external
background and name a compatible sound pack; it cannot store a user's URL or
silently start media.

Two alternatives were considered. Continuing to add protocol-ID and field-name
checks to Recorder is faster per edit but repeats the SMR leak. Treating all
audio as arbitrary `meta` strings avoids a parser change but leaves contracts
unvalidated. A versioned typed contract is recommended so both evaluators and
hosts can reject unsupported behavior before a session.

## Refrain contracts

### Feedback declaration

Add a versioned `feedback` declaration to protocol source and IR. Each entry
references an existing named `output` channel; it does not duplicate signal
logic. The declaration states:

- semantic role and operator-facing label;
- analog layer or discrete event, with priority and whether it is suppressed
  by an inhibit, a muted phase, or an input gap;
- attack/release defaults for a layer, or rearm and desired minimum cue
  interval for an event;
- asset **role** and optional default sound-pack ID, never a local path;
- preview availability and order;
- whether an independent external background is supported. Its default is
  off unless the clinician explicitly selects a source for this protocol.

The compiler validates referenced output channels, compatible value/event
types, unique priorities and roles, bounded durations, and phase/inhibit
references. IR versioning rejects an unsupported declaration before training.
Python and Rust must resolve the same declaration and emit the same feedback
states/events. A host may apply a stricter audio safety limit than the
protocol's desired cue interval; it records a suppressed cue separately from
an absent evaluator event. Playback crossfades and limiting remain Recorder
algorithms.

`hold_for` from PR #101 supplies sustained state semantics. If a protocol
needs an event that fires once per qualifying hold and rearms only after a
specified clean interval out of condition, add a general event/rearm primitive
to Refrain; do not rebuild that timer from telemetry in Recorder. A data gap,
equipment interruption, inhibit, or muted phase resets the held state through
the evaluator's reset path. Generic host signal-quality detection reports the
interruption; it does not infer alpha/theta state.

### Measures and presentation

Add named protocol measures evaluated from the same Refrain streams as reward
and feedback. A measure declares its value type, unit, label, clean-time
eligibility, and requested aggregates such as fraction of eligible time,
total duration, episode count, mean, and longest episode. For alpha/theta,
declare literal theta-over-alpha (`ratio > 1.00`) separately from the
clinician-adjustable shaping target and from actual feedback exposure.
Recorder's generic aggregator uses contiguous timestamped values and breaks
episodes at gaps, guards, pauses, and phase boundaries. It preserves raw
telemetry and the measure declaration/version for saved-session replay.

Presentation metadata may request a generic same-scale two-series comparison
or time line by naming declared streams/measures, labels, and units. Recorder
chooses colors and draws the chart. It no longer guesses a `theta_alpha_ratio`
card, searches envelope names for `theta` and `alpha`, or inserts a crossover
panel into every session overview. Historical captures lacking the new
contract remain readable through a versioned legacy decoder; new sessions do
not depend on those heuristics.

### Advice

Recorder adapts Refrain's existing structured advisor API. It displays
`collecting`, `hold`, `hint`, or `adjust` with the named limiting check,
clean-window evidence, eligible control, proposed value, and policy
provenance. Applying/dismissing calls the evaluator and records its audit
event. Equipment changes call `mark_equipment_change`. Automatic application
occurs only where the protocol policy permits it and the operator enabled
autopilot. Once this path passes comparison checks, remove Recorder's
alpha/theta-specific `GuidanceEngine` and its `crossover_target` callbacks.

## Recorder migration

1. **Fix the SMR media leak first.** Remove the global nonempty YouTube
   default. Key remembered external-media choices by protocol identity and
   make a new protocol's media choice off. Show the chosen source and an
   explicit enable state in setup. A saved alpha/theta URL never auto-enables
   for SMR. Migration of old settings preserves the URL as a dormant
   preference, not an auto-start command.
2. Parse and validate the new feedback contract at setup and session start.
   Build previews and live renderer channels from that contract. Replace
   `protocol_id` sets and the fixed three-role texture manifest with a
   generic ordered-layer/event adapter. Keep asset resolution, licensing,
   loudness checks, output reconnection, and volume ceiling in Recorder.
3. Switch advice to the evaluator API, then switch summaries and live/review
   views to declared measures and presentation metadata. Record the resolved
   contract, engine/protocol revisions, pack hash, user media choice, live
   controls, emitted/suppressed cues, and data gaps.
4. Delete the legacy alpha/theta telemetry detector and special-case UI only
   after replay parity is established. Keep old-capture decoding isolated
   from the new runtime path.

## Order and stopping checks

| Milestone | Refrain/protocol deliverable | Recorder deliverable | Smallest proof |
| --- | --- | --- | --- |
| 1. Media isolation | External-media capability can initially be optional metadata; no engine change. | No global video auto-start; per-protocol disabled-by-default choice. | Select SMR after alpha/theta: setup and session open no video. Re-select alpha/theta and explicitly opt in: chosen video opens. |
| 2. Feedback contract | Typed declaration, IR/version checks, Python/Rust parity; protocol files name roles and outputs. | Generic contract-driven preview and renderer; remove protocol-ID asset selection and held-cue computation. | Replay authored clean, guarded, paused, and gapped streams: both evaluators agree; Recorder emits or suppresses each declared cue exactly once as specified. |
| 3. Advice and review | Named measures and existing advisor policy exposed with provenance. | Generic advisor adapter and measure-driven live/saved views; remove duplicate guidance/field-name inference. | SMR, classic alpha/theta, and narrow-band mock sessions show only their declared advice and measures; saved overview matches recorded events. |
| 4. Release parity | Merged, pinned Refrain and protocol revisions. | Same source and packaged behavior, bundled rights-cleared audio. | Clean `main` clone and installed macOS app pass the same discovery, preview, mock-session, and saved-review checks. |

Each milestone is separately reviewable. Do not merge a protocol that requires
an IR capability before both supported evaluator backends and the host can
load it. One real recorded session may be used as a regression replay with
its observations and provenance intact; authored streams test semantics, not
clinical efficacy. The final audit searches runtime Recorder code for
alpha/theta protocol IDs and reward-field names and justifies any remaining
references (for example, isolated historical-capture migration code).
