# Refrain — A Concept Document

*An open description language for clinical neurofeedback protocols.*

*Draft for socialization — May 2026.*

---

## Summary

Clinical neurofeedback has a reproducibility problem with measurable scientific cost. Protocols are described in prose, implemented inside closed clinical software, and hand-translated by every research group that tries to replicate them. The 2025 JAMA Psychiatry meta-analysis on NF for ADHD found no significant effect across 38 RCTs — but a small significant effect when the analysis was restricted to studies using established standard protocols. The signal exists. Protocol heterogeneity is destroying it.

Other fields with the same problem have solved it the same way: by replacing prose protocols with declarative artifacts that are simultaneously human-readable, machine-executable, and citable. Bioinformatics did this with Snakemake/CWL/Nextflow. Networking did it with P4. Audio DSP did it with Faust. Infrastructure did it with Terraform. Aerospace and automotive did it with SCADE.

Refrain is a proposed declarative description language for clinical NF protocols. A Refrain file is a text artifact that fully specifies what a NF protocol does — the montage, the signal processing, the thresholds, the rewards, the inhibits, the session structure — at a level of precision that a compatible runtime can execute it directly, and a peer reviewer can audit it directly. The same artifact runs on different amplifiers. The same artifact appears in a paper supplement. The same artifact fulfills the existing CRED-nf reporting checklist by construction.

This document lays out the problem, the vision, the pattern Refrain inherits from other fields, the proposed solution at concept level, and the open questions. It is a draft to socialize, not a commitment.

---

## The Problem

### Clinical NF has a reproducibility crisis, and the field admits it

Neurofeedback as a clinical practice has been around for fifty years. The literature on its efficacy remains contentious — and the contention is, increasingly, not really about whether NF works but about whether *the studies of NF* can be trusted to tell us whether it works.

The most recent large meta-analysis (Westwood et al., *JAMA Psychiatry*, 2025) pooled 38 randomized controlled trials with 2,472 participants on NF for ADHD. The headline result was sobering: no significant improvement on probably-blinded outcome measures. But buried in the analysis was a finding that points directly at the methodological bottleneck — *when the analysis was restricted to RCTs using established standard protocols, a small significant improvement appeared*. The treatment effect, whatever it is, is being lost in the variance across implementations of "neurofeedback" that share a name and almost nothing else.

Recent systematic reviews say the quiet part aloud:

- "Reporting of EEG neurofeedback parameters and outcomes varies widely; greater transparency is required to validate brain-behaviour changes."
- "None of the reviewed studies preregistered protocols or shared data and analysis code, reflecting a broader reproducibility gap in applied neuroscience."
- "Heterogeneity in protocols and placebo effects complicate interpretation… future large-scale, well-controlled trials needed to establish robust, standardized protocols."
- "Protocol standardization regarding electrode placement, targeted frequency bands, and session parameters will facilitate replication and meta-analytic synthesis."

The community has responded with the **CRED-nf checklist** (Ros et al., *Brain*, 2020) — a consensus standard for what a NF paper must report: electrode locations, frequency bands, threshold algorithms, reward modalities, contingency timing, session structure, control conditions, and so on. CRED-nf is endorsed by the EQUATOR Network and used as a quality-assessment rubric in 2024-2025 systematic reviews. It is the most coordinated standardization effort in the field, and it is clearly necessary.

But CRED-nf is a checklist for *reporting in prose*. It tells authors what to describe. It does not give them a way to describe it that another lab can then re-run. The translation from "a paper that contains all CRED-nf items" to "a protocol another group can replicate" still requires a clinical neuroscientist re-implementing in MATLAB, Python, or whatever closed clinical software they use. The replication gap is the implementation gap.

### Protocols are opaque inside closed software

Most clinical NF runs on closed-source software: Cygnet (Bee Medic), BrainMaster, Neuroguide, Neurofield's clinical suite, and others. These are competent commercial products with decades of clinical use. They are also, from a research-replicability standpoint, sealed. A practitioner picks a "preset" inside a GUI; the preset corresponds to a specific configuration of filters, thresholds, mappings, and inhibits that exists as an opaque data structure in the vendor's binary. Researchers cannot inspect the preset; they cannot diff it against a similar one; they cannot publish it as a supplement.

The consequence is that "Othmer ILF at T3-T4" — to take a real example — is a name for a clinical practice, but the actual protocol that a given clinician runs on a given day depends on a vendor binary, a clinician's tuning history, a session's parameter trajectory, and convention. None of this is captured in a way that survives the patient-clinician encounter.

For a field hoping to mature into evidence-based clinical practice, this is a structural problem, not a procedural one. No amount of better reporting in prose closes the gap. The protocol *itself* needs to be a portable, inspectable, reproducible artifact.

### The cost is paid in research signal

The JAMA Psychiatry result is the cleanest example: the treatment effect, restricted to studies using consistent protocols, becomes detectable. Across the broader field of NF research — for ADHD, PTSD, anxiety, performance enhancement, neurorehabilitation — meta-analyses repeatedly report high heterogeneity that "complicates interpretation." Heterogeneity is the language journals use to say "we cannot tell whether this works because every study did it differently."

The cost is also paid by clinicians who want to base their practice on evidence; by patients trying to compare treatments; by insurers deciding whether to reimburse; by regulators considering software-as-medical-device classifications. All of them face the same underlying problem: the protocol is not a thing they can point at.

---

## The Vision

Imagine the clinical NF field five years from now, if this problem is solved.

A clinician who reads a 2027 paper reporting positive results from "Othmer ILF training at T3-T4 for treatment-resistant depression" finds, in the supplementary materials, a single text file. The file is human-readable: a few hundred lines describing the montage, the signal pipeline, the inhibit bands, the reward mapping, the session schedule. The file is also machine-executable: pasted into the clinician's recorder software, it runs the exact protocol the paper studied, on whatever compatible amplifier the clinic owns.

A research group running a multi-site replication study no longer has to send the lead investigator's MATLAB scripts back and forth, accompanied by phone calls about "what did you mean by 'theta inhibit at threshold'?" Each site checks in the same protocol file from version control; the runtimes verify the file's hardware requirements against each site's amplifier and refuse to run if there is a mismatch. The protocol is, literally, the protocol.

A regulatory submission for a software-as-medical-device classification includes the protocol files as part of the artifact. Auditors can inspect what the device actually does — frequency bands, threshold algorithms, reward contingencies — at a level of formality the FDA's existing software lifecycle expectations already anticipate.

A clinician who reads a recent paper and wants to try a small variant on their own patients writes a fifteen-line file that extends the published protocol with an additional inhibit, then runs it. The variant is its own artifact; if outcomes look promising, it can be shared as a derivative work. Composition is cheap.

A practitioner moving between clinics no longer has to relearn a vendor's GUI; they bring their library of protocol files with them. Vendors compete on the quality of their runtime, the polish of their patient-facing UX, and the strength of their normative databases — not on lock-in to opaque preset libraries.

The core move is small but consequential: **the protocol becomes the artifact.** Not a paragraph in a paper; not a row in a vendor database; not lore in a clinician's head. A text file. Versioned. Diffable. Citable. Executable.

This vision is not novel. It is the same move several adjacent fields have already made.

---

## The Pattern in Other Fields

The architectural pattern Refrain proposes — *a domain-shaped declarative artifact, executed by a typed runtime, with primitive operations underneath* — is well-trodden. Several fields have adopted it. The forcing functions vary; the resulting architecture does not.

### Bioinformatics: Snakemake, CWL, Nextflow

In the early 2010s, genomics research had a reproducibility crisis closely analogous to NF's: pipelines were brittle shell scripts, multi-site collaborations couldn't reproduce each other's results, and journals increasingly demanded computational reproducibility. The field responded with a generation of declarative workflow languages — Snakemake, CWL (Common Workflow Language), Nextflow, WDL. Each captures the structure of a bioinformatics pipeline as a text artifact: inputs, steps, dependencies, parameters, expected outputs. The artifact runs on any compatible engine. Today, every major genomics consortium and many funding agencies expect workflow files in published research.

The forcing function was soft — research reproducibility, multi-site collaboration, cross-engine portability — but it was enough. Adoption took roughly five years from credible first implementations to "expected default."

### Networking: P4

P4 (Programming Protocol-Independent Packet Processors) is the canonical declarative language for programmable network dataplanes. Its forcing function is hard: silicon at line rate cannot run general-purpose code. P4 captures packet processing as parse → match → action pipelines, compiles to NPU-specific microcode, runs at terabit speeds.

The lesson NF can take from P4 is structural rather than circumstantial. Packet processing has, like NF, a finite stable set of primitive operations and a natural pipeline shape. P4 captures the *structure*; the primitives carry the *math*. The same architectural pattern works whenever those two conditions hold.

### Audio DSP: Faust

Faust (Functional AUdio STream) is the closest structural cousin to what Refrain would be. It is a declarative language for real-time audio signal processing developed at GRAME / IRCAM. A Faust program describes a DSP graph; the compiler emits optimized C++ / Rust / WebAssembly for any supported target. The same Faust file runs on a guitar pedal, a mobile app, a research platform, and a desktop audio editor.

Faust is used heavily in academic biosignal processing. The community has demonstrated that the architectural pattern handles real-time DSP for biological signals well. Its existence is one of the strongest structural reasons to believe a Refrain-shaped approach for NF is feasible.

### Infrastructure: Terraform / HCL

Before Terraform, cloud infrastructure was managed through web consoles, ad-hoc scripts, and tribal knowledge. The pattern was almost identical to today's clinical NF: state lived inside vendor systems, configurations couldn't be diffed across teams, reproducibility depended on the memory of whoever set things up.

Terraform's HCL gave the industry a declarative artifact for infrastructure: a text file that captures what the infrastructure should be. The file is portable across cloud vendors; the runtime adapts. Adoption took roughly five years; today it is the default expectation in industry.

The forcing function was soft: portability, scale, GitOps compatibility, audit. None of these were urgent in any single project. All of them, together, eventually compelled the shift.

### Safety-critical embedded: SCADE, Simulink

The aerospace, nuclear, and rail industries have been doing model-based development for decades. SCADE, Simulink with Stateflow, and similar tools allow engineers to describe control systems as declarative dataflow diagrams; certified code generators emit the binary that flies the airplane or controls the reactor. The forcing function is regulatory: certification standards (DO-178C, IEC 61508, EN 50128) treat the *model* as the source of truth and the generated code as machine output. The artifact is what gets reviewed.

This is the closest analogue to where regulated digital health is heading. The FDA's evolving framework for software-as-medical-device increasingly emphasizes the artifact that defines the device's behavior, not just the binary that runs it. A NF system whose protocol artifact is auditable maps cleanly onto this trajectory.

### The pattern, distilled

Across these fields, the architectural pattern is consistent:

1. **The artifact is text.** Diffable, reviewable, citable, mergeable.
2. **The artifact is declarative.** It describes *what*, not *how*.
3. **Primitives carry the math.** A small, curated library of typed operations does the actual work; the language composes them.
4. **The compiler/runtime gives static guarantees.** Type checks, resource bounds, validity checks happen before execution.
5. **The artifact is portable across runtimes.** A protocol authored against the language runs on any compliant implementation.
6. **There is an escape hatch for novel work.** Custom primitives can be defined when the standard library doesn't cover what you need.
7. **The pattern coexists with vendor competition.** Vendors compete on runtime quality, not on lock-in.

Refrain proposes to instantiate this pattern for clinical neurofeedback. The pattern is well-understood; the work is the *content* of the language — the primitive set, the protocol library, the runtime — rather than the form.

---

## The Proposed Solution: Refrain

Refrain is a declarative description language for clinical neurofeedback protocols.

A Refrain file (`.refrain`) describes a complete NF protocol: the required hardware capabilities, the channel montage, the signal processing pipeline (filters, envelopes, derivatives, statistics), the threshold logic, the inhibit/artifact gates, the reward expression, the output bindings (audio gain, video modulation, ambient effects, discrete events), the clinician-tunable controls, and the session structure.

The file is text. It is human-readable. It is the canonical artifact: when it appears in a paper supplement, it *is* the protocol; when it runs on a recorder, it *is* what runs.

A Refrain runtime parses the file, type-checks it, validates that the connected amplifier meets the protocol's hardware requirements, computes worst-case latency and resource budgets statically, and executes the resulting pipeline in real time. The reference runtime ships as an open-source recorder plugin; Coherence Workstation will be the first commercial runtime, and the open language definition allows other vendors to build compliant runtimes.

### What's in the box

The language ships with a curated **standard library of primitives**: acquisition operators (bipolar, referential, Laplacian, source projection); spectral operators (bandpass, Hilbert, bandpower, FFT); statistical operators (RMS, percentile tracking, z-score against external norms providers, auto-ranging); cross-channel operators (coherence, asymmetry, phase-lag); time-series math (differentiate, smooth, decimate); condition operators (above, below, all_of, dwell); reward mappings (linear, sigmoid, dead-zone, hysteresis); and output bindings (audio gain, video modulation, ambient effects, chime events).

This library is designed to cover roughly 80% of clinically deployed NF protocols out of the box: SMR / theta-beta training, Othmer ILF, alpha-theta (Peniston), z-score training (Thatcher LZT), coherence training, asymmetry training, peak alpha frequency training. The first reference protocol library would include faithful, citable implementations of the major clinical families.

### The CRED-nf bridge

The most consequential single design decision: the language schema is structurally aligned with the CRED-nf checklist. Every CRED-nf item maps to a field in the protocol file. A complete Refrain protocol *automatically generates* a CRED-nf-compliant supplementary materials table. CRED-nf compliance becomes a tooling feature rather than a manual obligation.

This is the alignment with the field's existing standardization momentum. CRED-nf described, in prose, what a NF protocol must contain. Refrain makes that description executable.

### The escape hatch

Researchers will inevitably need primitives the standard library doesn't provide — novel phase metrics, custom artifact rejection schemes, experimental connectivity measures. Refrain accommodates this with **typed custom primitives**: a researcher writes a Python module exporting a typed function, declares its input/output contract and resource budget, and the Refrain compiler accepts it as a first-class operation. The DSL stays bounded; the long tail of research NF stays accessible.

This is not a workaround — it's the same architectural choice P4 makes (externs), Faust makes (foreign functions), and Snakemake makes (custom rules with arbitrary code). The escape hatch is what keeps the language honest about its own scope.

### What Refrain is not

A few non-goals are worth stating explicitly to keep the scope honest:

- **Not a general-purpose signal-processing language.** numpy, MATLAB, MNE-Python already exist. Refrain describes clinical NF protocols. Anything outside that domain belongs elsewhere.
- **Not a graphical programming environment.** OpenViBE went down that road, gained research adoption, did not cross to clinical product. The lesson stands: text artifacts are diffable, citable, and shareable in ways graphs are not. A GUI editor on top of Refrain is welcome — but the canonical artifact is text.
- **Not a clinical product.** Refrain is the engine; clinical products (Coherence Workstation among them) are the workflow polish, the patient-facing UX, the session management, the EHR integration, and the support that clinicians actually pay for. The language enables those products without being one.
- **Not a fully community-owned standard, yet.** Refrain begins as a working language with one canonical implementation (open source) and one canonical commercial runtime (Coherence Workstation). If the language earns adoption, the natural trajectory is toward a community-governed standard with multiple compliant implementations. That trajectory is open; the commitment today is only to start.

---

## Why Now

Three convergent conditions make this a credible moment to start.

**The clinical literature is asking for it.** CRED-nf has been published, endorsed, and is being used as a quality rubric. The 2025 JAMA Psychiatry meta-analysis surfaces the cost of not having standardized protocols in language stark enough to compel attention. The community has done the work of agreeing what *should* be reported; what's missing is the format that makes reporting executable.

**Hardware is ready.** Modern research-grade EEG amplifiers (the Neurofield Q21 we work with, similar designs from BrainProducts, Cognionics, OpenBCI) are DC-coupled, simultaneous-sampling, 24-bit, with software-controllable impedance checks. They support the entire range of clinically deployed NF protocols, including the demanding sub-mHz Othmer-style infra-low frequency work. The constraint that historically forced specialized NF hardware no longer holds; commodity research-grade amps suffice.

**Regulatory tailwinds.** The FDA's evolving framework for software-as-medical-device increasingly emphasizes auditability of the artifact that defines device behavior. The EU's MDR has a similar trajectory. A NF software stack whose protocol artifact is human-auditable, version-controlled, and statically analyzable maps cleanly onto where regulated digital health is going. Building that infrastructure now is positioning, not premature.

**The architectural pattern is well-understood.** Refrain isn't proposing a novel language design; it's proposing a new instance of a well-established pattern. The risks of "we don't know if this approach works" don't apply here. The risks are about *adoption*, not viability.

---

## Open Questions

The honest version of this document includes the parts that aren't figured out.

**The minimum primitive set.** What primitives are needed to faithfully capture the major clinical NF protocol families? We have a hypothesis (~12 primitives) but no validation. The first concrete experiment is to author Othmer ILF, SMR/theta-beta, and alpha-theta in Refrain syntax and see whether the abstraction holds. If we find ourselves adding a new primitive for every protocol, the abstraction is too small.

**The norms-and-operators boundary.** Live z-score training (LZT) and source-space NF require external assets — normative databases, head models, inverse operators — that are clinical IP. The Refrain core defines an *interface* (the "norms provider" hook); commercial runtimes supply implementations. This boundary is sensible in principle but the precise interface needs design work.

**Hardware capability registry.** A protocol declares what it requires (DC coupling, sample rate, impedance support); the runtime checks. Who maintains the list of what each amplifier model supports? Initially, us; ideally, vendors themselves; possibly, a community-maintained registry. This is a governance question more than a technical one.

**Versioning and evolution.** Protocols authored against schema v1.0 must keep loading as the language evolves over years. Some adjacent languages (Pkl, P4) have done this well; others have struggled. The migration story needs to be designed early, not retrofitted.

**Adoption strategy.** Soft-forcing-function adoption — bioinformatics, Terraform — takes roughly five years from credible first implementation to default expectation. The question of how Refrain crosses from "research curiosity" to "expected default" is partly about quality of implementation and partly about community engagement (CRED-nf authors, journal editors, clinical organizations). We do not have a worked-out answer.

**OpenViBE's failure modes.** OpenViBE gained research adoption and did not cross to clinical product. The reasons we identify — visual rather than text artifact; research-grade rather than clinical UX; stagnation in active development — are addressable, but identifying failure modes is not the same as avoiding them. We should expect to discover failure modes our analysis missed.

---

## Path Forward

The plan, in honest order:

**Phase 0: Validate the math.** Before committing further to Refrain as productionization, validate that the underlying clinical-feel works. The first concrete experiment: a hand-coded `LiveDerivation` plugin in our existing recorder, implementing the proposed Othmer ILF math (DC-coupled bipolar acquisition, narrow bandpass at the ORF, differentiation, rectification, smoothing, auto-ranging, sigmoid mapping). Run on a Q21 with a breakout box, T3-T4 placement, on a willing subject (likely the author). Vary the parameters during the session. Find out whether this approach produces the felt phenomenology of Othmer ILF training. If yes, the bet is worth pursuing. If no, the design space changes substantially.

**Phase 1: Concept document and socialization.** This document. Shared with clinical and engineering colleagues for criticism. The goal is to surface the framing problems, the assumptions that don't hold, the parts the audience finds unconvincing, and the things that could be made stronger.

**Phase 2: Language v0.1.** Once the validation succeeds and the framing is sharper, draft the formal Refrain specification: syntax, type system, primitive contracts, runtime semantics, CRED-nf mapping. Implement the parser, IR compiler, validator, and a runtime that walks the IR via the existing recorder plugin path. Author three reference protocols in Refrain (SMR, Othmer ILF, alpha-theta). Verify that the same protocol files run on the recorder via the language as on the hand-coded plugin.

**Phase 3: Limited public beta.** With the language working and reference protocols validated, share with research partners. Particularly: the CRED-nf authors and adjacent NF research labs. Iterate on primitive set, syntax, and clinical UX based on real users.

**Phase 4: Coherence Workstation integration.** Refrain becomes the protocol runtime in the clinical product. Customers author or import Refrain protocols; CW provides the curated GUI editor, the patient-facing renderer, the session management, the clinical workflow polish that turns a language into a product.

**Phase 5: Standard.** If the language earns sustained adoption, transition governance to a community body. Multiple compliant implementations. Refrain becomes the format the field uses.

Phases 0 and 1 are committed. Phase 2 is contingent on Phase 0 success. Beyond that, the path is conditional on the previous phase earning the next.

---

## A Note on Naming

The name Refrain was chosen for its dual resonance. Musically, a refrain is a passage returned to — a structured part of a piece that recurs across iterations. Clinically, a NF protocol is a structured practice the patient returns to across sessions, with the same shape recurring while gradually shifting in calibration. The metaphor captures both the artifact's stability and the practice's repetition.

Practically, the name is short, pronounceable, distinctive in the relevant search space, and not heavily trademarked. The canonical implementation lives at `github.com/refrain-lang`. The language file extension is `.refrain`. The language website is `refrain.dev`.

---

## Closing

This document proposes a particular architectural move for clinical neurofeedback: replace prose protocols with declarative artifacts. The move is not novel; the same move has been made, successfully, in adjacent fields with comparable problem shapes. The forcing functions in NF are softer than in those fields, but they are real and growing — the literature is asking for it, the hardware supports it, the regulatory trajectory aligns with it, and the existing standardization effort (CRED-nf) points toward exactly this kind of artifact without quite getting there.

What's proposed is an open description language called Refrain. The first concrete step is to validate that the underlying clinical math produces the right experience for a patient. The second is to draft the language formally. The remaining phases are conditional on the previous ones earning their next step.

The honest version of this proposal includes the things we don't know: the right primitive set is a hypothesis; the norms-provider boundary needs design; OpenViBE's failure modes are addressable but not automatically avoided; adoption of soft-forcing-function languages takes years.

This document is a draft. Critique, disagreement, and reframing are welcome. The strongest version of the proposal will be the one that survives reading by people whose perspectives differ from the author's.

---

*Comments, questions, and disagreements: please share.*
