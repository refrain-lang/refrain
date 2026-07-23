# Biosignal-Platform Reframe — Cross-Repo Migration Plan

*Status: proposal / working plan. Draft 2026-07-23.*

This document is the migration plan for a framing change: from **open clinical
neurofeedback** to **general-purpose open biosignal processing**. It covers what
changes in each `refrain-lang` repo, guidance for the first-party consuming apps
(recorder, companion), the fork-vs-evolve decision for the editor, and the order
to do it in.

> **Note on visibility.** This file lives in the *public* `refrain` repo but
> references sibling repos including the private `refrain-editor`. It contains no
> secrets, only architecture and naming. If any of the positioning notes below
> should stay internal, move this file to a private repo before publishing —
> flagged again in §9.

---

## 1. The decision, stated precisely

The platform broadens. **Refrain does not become general-purpose DSP.**

- **The platform** (recorder + companion + future runtimes) is a general-purpose
  open biosignal processing tool — EEG, ECG/HRV, GSR, temperature — for
  clinicians, researchers, and biohackers. General-wellness intended use.
- **Refrain** stays what it is: a bounded declarative DSL for **biosignal
  *training paradigms*** — the operant/feedback loop (input → derive → threshold
  → reward/inhibit → output). It is *one layer inside* the platform, not the
  platform.

The consequence that drives everything below: Refrain is a **proper subset** of
the platform's scope. The platform's new "just record and analyze" workflows
have no reward loop, so Refrain does not describe them — and shouldn't. The
reframe is therefore mostly **narrative + targeted vocabulary**, not semantics.
The evaluator, IR-JSON schema, and embedding API do not change.

### The clean-slate window

There are **no external consumers** — only the first-party recorder and
companion. This is the one moment where breaking changes (schema tags, package
names, vocabulary) are nearly free. Batch the breaking parts *now*, in one
coordinated pass, rather than preserving compatibility we don't yet owe anyone.

### What this reframe is NOT

Keep the discipline from `docs/CONCEPT.md` §"What Refrain is not": Refrain is
**not** a general-purpose signal-processing language, and broadening the platform
must not erode that. numpy/MNE/MATLAB still exist. The only wording that changes
is the *domain* of the DSL: "clinical NF protocols" → "biosignal training/
feedback paradigms." The bounded-DSL principle is reaffirmed, not relaxed.

---

## 2. Change surface at a glance

| Layer | Reframe impact | Breaking? |
|---|---|---|
| Language semantics (parser, resolver, IR, evaluator) | **none** | no |
| IR-JSON schema / embedding API / Rust core parity | **none** | no |
| `refrain` docs & framing (README, CONCEPT, SPEC prose) | heavy | no |
| Language vocabulary (`montage`, band/site framing) | targeted, mostly docs | maybe (naming only) |
| `refrain-protocols` schema (modality) | moderate | yes (tags) — do now |
| `refrain-protocols` *distributed content* (goals, evidence, citations, comments) → general-wellness neutral | heavy | yes (vocab) — do now |
| `refrain-editor` presentation layer (sites, bands, labels) | moderate | no (first-party) |
| Consuming apps (recorder, companion) | pin bump + modality handling | no at IR level |

The single most important line: **nothing breaks at the IR level.** Every
existing `.refrain` file and its compiled IR-JSON keeps running unchanged.

---

## 3. `refrain` (this repo) — framing + vocabulary

### 3.1 Narrative / docs (do first, low risk)

- **`README.md:3`** subtitle — *"An open description language for clinical
  neurofeedback protocols."* → *"An open description language for biosignal
  training paradigms."* Update the one-paragraph blurb below it (line 11) the
  same way: a `.refrain` file describes a complete **biosignal training
  protocol** (EEG neurofeedback, HRV coherence, GSR/temperature biofeedback…).
- **`docs/CONCEPT.md:3`** subtitle — same change. Then the body:
  - *Vision* and *Why Now* sections currently build the whole argument on
    clinical-NF + FDA-SaMD auditability. Reframe to lead with the general
    biosignal-processing category (BioExplorer/BioEra as the decades-old
    reference points), general-wellness intended use, clinician-and-researcher
    tool. NF becomes *one* worked example, not the thesis.
  - *"What Refrain is not"* (line ~160) — **keep the non-goal**, widen its
    wording: `Refrain describes clinical NF protocols` → `Refrain describes
    biosignal training/feedback paradigms`. Everything outside training
    paradigms (raw recording, general analysis) still "belongs elsewhere."
  - *The CRED-nf bridge* — demote from *raison d'être* to *one supported
    reporting standard*. Keep the feature; add a sentence that HRV biofeedback
    and other modalities have their own reporting norms (e.g. Lehrer/Gevirtz for
    HRV) that the same protocol-as-artifact model serves.
- **`README.md` clinical-use disclaimer** — reframe from "medical device / FDA /
  clinical use" language toward **general-wellness, research-and-education, not a
  medical device, no medical claims**. Honest positioning, not evasion: the
  general-wellness category is a real, well-understood envelope.
- **`docs/TOUR.md`, `docs/SPEC.md`, `docs/PRIMITIVES.md`** — sweep prose for
  "neurofeedback"/"NF" used as the *category* (vs. as a legitimate example).
  Change category uses; keep example uses. (2000+ occurrences exist across the
  tree — most are in dated specs/plans/CI logs that are historical record and
  should **not** be rewritten. Only touch living, user-facing docs.)

### 3.2 Vocabulary (needs a decision — see §8)

Three EEG/NF-shaped names, in decreasing awkwardness:

1. **`montage`** — genuinely EEG-specific; already papered over with
   `passthrough()` for the HRV tachogram (see `docs/hrv-feature-request-
   response.md`). Options:
   - **(A, recommended)** Keep `montage` as the block name; document it as *one
     kind of signal-source binding* — `referential`/`bipolar`/`laplacian` are
     EEG montages; `passthrough` is the identity binding for single-channel
     non-EEG sources. Zero code change, one docs paragraph.
   - **(B)** Introduce a neutral synonym (e.g. `source =`) accepted alongside
     `montage =`, with the EEG montages as one family. More work; only worth it
     if the tachogram-as-"montage" framing keeps biting.
   - Recommendation: **A now**, revisit B if GSR/temp make it hurt.
2. **`reward` / `inhibit`** — operant-conditioning terms, **modality-neutral by
   nature** (HRV coherence *rewards* coherence; GSR relaxation *rewards*
   downregulation). **Leave unchanged.** They are correct for every training
   paradigm.
3. **band / site framing in prose** — "frequency band" is already generic (the
   HRV LF band is 0.04–0.15 Hz); "site" is EEG-specific but survives as the
   channel-naming slot. Keep; document that "site" generalizes to "channel/
   source label" for non-EEG.

### 3.3 First-class modality? (decision — see §8)

Today modality lives only in the *protocol-library* `meta` (see §4), not in the
language. Options: leave modality as library metadata (Refrain stays
modality-blind — clean), or add an optional `meta.modality` hint to the language
schema so runtimes/editors can adapt UI without re-deriving it. Recommendation:
**keep the language modality-blind**; let `refrain-protocols` own the modality
vocabulary. The language shouldn't grow a taxonomy it doesn't execute on.

---

## 4. `refrain-protocols` — make the distributed library maximally neutral

The distributed protocols are the artifacts people actually pick up and run, so
this is where neutrality matters most. Two independent axes:

- **Axis A — modality-neutral** (biosignal, not EEG-only): the `meta` schema.
- **Axis B — intended-use-neutral** (general-wellness, not clinical/medical): the
  *content* of the distributed files — goals, evidence claims, citations, titles,
  comments, disclaimers.

Axis B is the bigger lift and the one that keeps the whole platform out of the
medical-device corner the reframe is stepping away from. Today the distributed
library reads clinically: `goals = ["adhd_attention"]` / `["mood_regulation",
"trauma_recovery"]`, `evidence = "established"` framed as *clinical-literature
support*, citations presented as efficacy evidence, and file comments like *"the
depression FAA protocol."* (~90 clinical/indication references across
`protocols/` + docs.) Directive: **make what's distributed as neutral as
possible** — general-wellness biosignal-training building blocks, no diagnostic
or therapeutic claims.

### 4.1 Axis A — `schema/protocol-meta.schema.json` (modality)

Already partway neutral (it has a `modality` field). This is the one real
**breaking-but-additive schema change** — do it in the clean-slate window.

- **`modality`** — `enum: ["eeg", "hrv"]` → widen to `["eeg", "ecg", "hrv",
  "gsr", "emg", "temp"]` (keep both `ecg` and `hrv` — signal vs. derived
  tachogram differ). Keep default `eeg` so existing files stay valid.
- **`site`** — description *"Scalp site(s)… 'tachogram'"* mixes concepts.
  Generalize the description to "channel / source label (EEG scalp site, or
  `tachogram`/`gsr`/… for other modalities)." Free string already; no type change.
- **`bands`** — EEG-centric but already free-form (HRV uses `hrv-lf`). Keep;
  document as optional and modality-scoped (omit for modalities with no band
  concept, e.g. raw GSR level).
- **`hardware`** — `["generic", "brainbit_flex", "clinical_amp"]` is
  EEG-amp-centric *and* clinically loaded. Rename `clinical_amp` → a neutral
  capability class (e.g. `research_amp` / `pro_sensor`) and add non-EEG classes
  as they arrive, or collapse to `generic` + free `requires_features`.
- **`reward_style` / `direction` / `threshold_style`** — modality-neutral. Leave.

### 4.2 Axis B — neutralize the distributed content (intended-use)

The strong form of the reframe. Recommended mapping (dial back per §8 if you want
to retain clinical provenance for researchers):

| Clinical vector, today | Neutral form |
|---|---|
| `goals` diagnosis names — `adhd_attention`, `calm_anxiety`, `mood_regulation`, `trauma_recovery` | wellness-outcome names — `focus_attention`, `calm_stress`, `mood_balance`; **drop `trauma_recovery`** (fold to `resilience`/`calm`) |
| `goals` already-neutral — `sensorimotor_sleep`, `alertness_performance`, `flow_connectivity`, `deep_meditative` | keep (maybe `sensorimotor_sleep` → `sleep_quality`) |
| new modality goals | `hrv_coherence`, `stress_downregulation`, `relaxation_arousal`, `interoception` |
| `evidence` = *"clinical-literature support"* (`established`/`probable`/`exploratory`) | reframe meaning to *"how established the signal-training **approach** is"* (prior art), **not** clinical efficacy. Keep tiers; strip "clinical" from the label. Consider renaming the field to `maturity`/`provenance` |
| `citation` framed as efficacy evidence | keep — but frame as **origin / prior art**, neutral provenance, not proof of outcome |
| titles/summaries — *"Sharpen attention", "Lift mood"* | already general-wellness in tone — **good, this is the target register**; keep |
| file **comments** — *"the depression FAA protocol", "for mood / approach-motivation"* | strip clinical/indication phrasing; describe the **signal training**, not an indication |
| `indication` / `population` / `safety_monitoring` fields (referenced in host-app-guide) | drop from the distributed contract, or make clearly optional/host-side; a neutral library doesn't ship indications |

The FAA protocol is the sharpest example: today `goals = ["mood_regulation",
"trauma_recovery"]` with a "depression protocol" comment. Neutralized: `goals =
["mood_balance"]`, comment describes the F3/F4 alpha-asymmetry *training*, no
depression/trauma claim.

**Floor to be honest about:** you cannot make SMR/theta-beta *not* originate in
clinical literature. Maximal-neutral doesn't mean hiding provenance — it means
the distributed artifacts make **no diagnostic or therapeutic claim**, present as
general-wellness signal-training building blocks, and carry citations as origin,
not efficacy. That's the honest neutral, and it matches the essay's
general-wellness envelope.

### 4.3 Docs & mechanical passes

- **README** — *"Reference neurofeedback & HRV protocol library"* → *"Reference
  biosignal training-protocol library."* Keep tags-not-folders and
  files-are-source-of-truth verbatim (modality-agnostic already). Reframe the
  ⚠️ untested badge language from clinical → general-wellness while keeping its
  honesty ("untested, not validated, not a medical device, no health claims").
- **`docs/evidence.md`, `docs/tagging.md`** — sweep "clinical," "medical device,"
  "clinician owns responsibility," "the science supports it" → general-wellness /
  prior-art framing. Keep the two-axis (status vs. maturity) model and the
  untested-badge honesty.
- **`docs/host-app-guide.md`** — grouping/filter guidance holds; **add a
  `modality` filter chip**; **rename the "Clinical-safety UX" section** to a
  neutral "trust & provenance" framing and drop `indication`/`population` from
  the recommended surface. "List by parse, resolve on select" unchanged.
- **Backfill `modality = "eeg"`** on every existing EEG file in one mechanical
  pass (currently relying on default). `hrv_resonance.refrain` already sets `hrv`.
- **Regenerate** `catalog.json` and the seed generator (`tools/gen_seed_
  protocols.py`, `tools/build_catalog.py`) so the neutralized vocabulary is the
  source, and update `schema` + CI (`tests/test_schema_fields.py`) to the new
  enums. Unknown-value → "Other" bucketing stays.

---

## 5. `refrain-editor` — assessment: **evolve, do not fork**

**Recommendation: do NOT create a second biosignal-neutral version. Evolve the
existing editor in place, made modality-aware.** The goal-orientation *is*
flexible enough. Rationale:

- **The core is already signal-agnostic.** `src/` (lexer, parser, ast, model,
  catalog, render, describe) is just the `.refrain` grammar — no EEG assumptions.
  Same grammar, all modalities.
- **The architecture is already injection-based.** `ui/src/README.md` documents
  an `EditorAdapters` boundary (`makeRecorderAdapters`, future portal adapters);
  `ProtocolEditor` runs with no backend. A modality is another axis of
  configuration, not a reason to fork.
- **EEG-specificity is a thin, mostly-additive presentation layer**, concentrated
  in a handful of files:
  - `ui/src/sites.ts` — 10-20 electrode dropdown, **already "device-agnostic,
    current value always preserved."** Make the option list modality-scoped
    (EEG → 10-20; HRV → `tachogram`; GSR → `gsr`; temp → `temp`), driven by the
    protocol's modality. It already survives non-standard values.
  - `ui/src/BandField.tsx` — labeled "Frequency band (Hz)", **already generic
    Hz** (works for the HRV LF band). Keep; maybe relabel per modality.
  - `ui/src/PlacementField.tsx`, `labels.ts`, `help.ts`, `blockPresentation.ts`
    — sweep for hard "electrode/scalp/EEG" copy; make modality-driven or neutral.
  - Section components (`InputsSection`, `DerivesSection`, `ThresholdsSection`,
    `RewardSection`, `InhibitsSection`, `StagesSection`) are
    **training-paradigm-shaped, not EEG-shaped** — they map to Refrain blocks.
    Leave the structure; gate visibility by modality/shape where a section is
    N/A.
- **A fork would be permanent drift for zero benefit.** With no external
  consumers, the package rename (`nf-editor` → neutral, e.g.
  `@refrain-lang/editor` is already the scoped name) and the presentation
  generalization are cheap *now*. Two editors = two test suites, two round-trip
  guarantees, forever.

**Where a genuinely separate surface *is* warranted — and it's not a fork of this
editor:** the platform's new *record-and-analyze* workflow (no reward loop) is a
different app concern that Refrain doesn't model at all. That belongs in the
**recorder** as its own view, not as a second `refrain-editor`. Don't confuse
"biosignal-neutral training editor" (this repo, evolve it) with "recording/
analysis UI" (recorder, new surface). Naming them distinctly in the plan avoids
the trap.

Concrete editor tasks:
1. Modality-scope `sites.ts` source options (biggest single win).
2. Make `BandField`/`PlacementField`/labels/help modality-aware or neutral.
3. Gate section visibility by modality where a block type doesn't apply.
4. Cosmetic: drop the `nf-` connotation in package/CSS-prefix naming if desired
   (`nf-editor-*` classes → neutral) — optional, low priority.
5. Keep the adapter boundary exactly as is; the recorder/companion adapters don't
   change.

---

## 6. Guidance for consuming apps (recorder + companion)

### 6.1 The contracts that do NOT change

Build on these with confidence — the reframe leaves them byte-stable:

- **IR-JSON** (`docs/IR-JSON.md`) — unchanged schema; existing compiled protocols
  keep running.
- **Embedding API** (`docs/EMBEDDING.md`) — `Evaluator.live / start / step_chunk /
  set_control / stop`, `export_state()` / `seed_state=`, `last_taps()` —
  unchanged. HRV already rides these.
- **`refrain.read_meta`** parse-only discovery — unchanged. The protocol-picker
  contract in `refrain-protocols/docs/host-app-guide.md` stands.
- **Rust core / Python parity** — unchanged.

### 6.2 What consuming apps must absorb

- **Modality tag.** Read `meta.modality` (default `eeg`) from protocols; use it
  to (a) route to the right input source, (b) filter/label in the picker, (c)
  hand the editor its modality so `sites.ts` scopes correctly.
- **Generalized `meta` schema.** Bump to the new `protocol-meta.schema.json`;
  handle the widened `modality`/`goals`/`hardware` enums. Unknown values already
  bucket into "Other" — keep that behavior.
- **Source/channel naming.** Stop assuming a scalp site; a channel label may be
  `tachogram`/`gsr`/`temp`. The `passthrough()` montage is the identity binding
  for single-channel non-EEG sources.
- **Multi-modal source handling (recorder).** Route ECG→tachogram, GSR, temp to
  the evaluator as single-channel `passthrough` inputs at their native rates.
  (Multi-*rate* multi-source is the deferred M2 item in `docs/hrv-feature-
  request-response.md` — not required for single-modality-at-a-time sessions.)
- **Picker (recorder).** Add a modality filter chip; show modality in row chips
  (per §4 host-app-guide update).
- **Editor embedding.** Pass modality into `<ProtocolEditor>`; the adapter
  boundary is unchanged.

### 6.3 Companion specifics

The companion consumes the same protocol library and evaluator state. Its changes
are: modality-aware protocol display/selection, and honoring the general-wellness
framing in its user-facing copy (no medical-device claims). No engine coupling
changes.

### 6.4 Pinning during the transition

- Consuming apps: **pin to a `refrain` release tag**, never `main` (existing
  guidance). The reframe lands as a normal minor version; the docs/vocabulary
  changes carry no runtime break.
- The **only** coordinated bump is the `refrain-protocols` `meta` schema. Land
  that, backfill `modality`, then bump the recorder/companion pins together. Do
  it in the clean-slate window so no compatibility shims are needed.

---

## 7. Sequencing

Order matters only where a downstream repo reads an upstream contract.

1. **`refrain` docs + vocabulary decision (§3).** Pure narrative + one docs
   paragraph for `montage`. No code risk. Unblocks consistent language everywhere
   else. → new minor release.
2. **`refrain-protocols` — both axes (§4).** Axis A: widen `modality`/`hardware`,
   generalize `site`/`bands`. Axis B: neutralize the distributed content —
   wellness-reframe `goals`, strip clinical framing from `evidence`/citations/
   comments/docs, drop indication fields. Backfill `modality = "eeg"`, regenerate
   catalog + seeds, update CI enums. → schema + library release. **This is the
   heaviest single unit of work in the plan.**
3. **`refrain-editor` modality-awareness (§5).** Depends on knowing the modality
   vocabulary from step 2. → editor release.
4. **Consuming apps (§6).** Bump `refrain` and `refrain-protocols` pins; absorb
   modality; add picker filter; pass modality to the editor. Do recorder +
   companion together.
5. **`refrain.dev` site** (private) — marketing/framing follows the same subtitle
   change; lowest urgency, no code dependency. The legacy private `protocols`
   repo appears superseded by `refrain-protocols` — confirm and archive.

Steps 1–2 can proceed immediately; 3 waits on 2; 4 waits on 2–3.

---

## 8. Open decisions (need James)

1. **`montage` vocabulary** — keep + document as signal-source binding (A,
   recommended) vs. add a neutral `source =` synonym (B)?
2. **Modality in the language** — keep Refrain modality-blind (recommended) vs.
   add optional `meta.modality` to the language schema?
3. **Modality vocabulary to ship now** — which signals get first-class enum
   values in `protocol-meta.schema.json` (proposed: eeg, ecg, hrv, gsr, emg,
   temp)?
4. **How far to neutralize the distributed content (§4.2)** — the key positioning
   call. Maximal (recommended, per your directive): wellness-reframe all
   diagnosis-flavored `goals`, drop `trauma_recovery`, strip "clinical" from
   `evidence`, reframe citations as provenance, drop `indication`/`population`.
   Dial-back option: keep the clinical `evidence` tier + citations as-is (retains
   research credibility) but still drop diagnosis-flavored goals and
   medical-claim comments. Confirm the depth.
5. **`goals` vocabulary** — confirm the neutralized set + non-EEG additions
   (proposed keep/rename: `focus_attention`, `calm_stress`, `mood_balance`,
   `sleep_quality`, `alertness_performance`, `flow_connectivity`,
   `deep_meditative`; new: `hrv_coherence`, `stress_downregulation`,
   `relaxation_arousal`, `interoception`; drop: `trauma_recovery`).
6. **Editor package/CSS rename** — drop the `nf-` connotation now, or leave it?
   (cosmetic; free in the clean-slate window)
7. **This doc's home** — keep in public `refrain`, or move to a private repo
   (§9)?

---

## 9. Sensitivity / visibility note

This plan sits in the public `refrain` repo and names the private `refrain-editor`
and `protocols` repos, and discusses regulatory positioning. Nothing here is a
secret, and the reframe itself is going public (the course-correction essay). But
if the regulatory-positioning notes (§3.1 disclaimer, §1 general-wellness) or the
private-repo references are meant to stay internal, relocate this file to a
private repo before the branch merges. Flagged so the choice is deliberate.

---

## 10. One-line summary

Broaden the *platform*; keep *Refrain* a bounded training-paradigm DSL. In the
language it's ~90% narrative, ~10% naming, ~0% semantics — but the **distributed
protocol library** is the real content work: make it maximally neutral on *both*
axes (biosignal, not EEG-only; general-wellness, not clinical) so what people
pick up carries no diagnostic or therapeutic claim. Do the breaking schema +
vocabulary bits now while there are no external consumers. Evolve the editor —
don't fork it.
