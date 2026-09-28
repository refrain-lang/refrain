# Biosignal Reframe — Phase 1, Workstreams 1 & 2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the `refrain` repo's user-facing docs from "clinical neurofeedback" to "biosignal training paradigms," and make everything `refrain-protocols` distributes modality-neutral and general-wellness-neutral — new goal vocabulary, widened schema, provenance-not-efficacy framing, clinical fields removed, modality backfilled, catalog and CI regenerated.

**Architecture:** Two repos, two branches, no shared code. Workstream 1 is prose plus one package-metadata string in `refrain`, guarded by a new phrase-level CI gate. Workstream 2 is a schema rewrite in `refrain-protocols` that deliberately breaks the existing corpus test, followed by mechanical per-file corpus edits that make it pass again, plus two new CI gates that close the holes which let today's drift happen unnoticed. Neither workstream touches the parser, resolver, IR, evaluator, or embedding API.

**Tech Stack:** Python 3.12, pytest, jsonschema, the `refrain` parser (pinned at `v0.21.0` in `refrain-protocols` CI), Markdown.

**Spec:** `docs/proposals/2026-07-23-biosignal-platform-reframe.md`. Sections §3 (workstream 1) and §4 (workstream 2). Decisions §8 #4 (hybrid neutralization) and §8 #5 (the eight-bucket goals vocabulary) are locked.

> The proposal lands on `main` via its own branch, `claude/biosignal-platform-framing-8gk0it`. This branch was rebased onto `main` for merge ordering, so until that branch merges the path above resolves only there.

## Workspaces

Both worktrees already exist. Do not create new ones.

| Workstream | Repo | Worktree path | Branch | Based on |
|---|---|---|---|---|
| 1 | `refrain-lang/refrain` | `/Users/jcroall/git/refrain/refrain/.claude/worktrees/biosignal-reframe` | `worktree-biosignal-reframe` | `origin/claude/biosignal-platform-framing-8gk0it` |
| 2 | `refrain-lang/refrain-protocols` | `/Users/jcroall/git/refrain-protocols/.claude/worktrees/biosignal-reframe` | `worktree-biosignal-reframe` | `origin/main` |

Tasks 1–5 run in the `refrain` worktree. Tasks 6–15 run in the `refrain-protocols` worktree. The two workstreams have **no build-order dependency** — `refrain-protocols` CI pins `refrain @ v0.21.0` and the parser treats `meta` as free-form, so the goal-vocabulary change needs no `refrain` release. They can run in parallel.

## Global Constraints

Copied verbatim from the spec; every task inherits these.

- **Language semantics do not change.** No edits to the parser, resolver, IR, or evaluator. "The evaluator, IR-JSON schema, and embedding API do not change."
- **IR-JSON and the embedding API do not change.** "Every existing `.refrain` file and its compiled IR-JSON keeps running unchanged."
- **`montage` stays as the block name** (spec §3.2 option A): "Keep `montage` as the block name; document it as *one kind of signal-source binding*." Do not add a `source =` synonym — that would be a language change.
- **Refrain stays modality-blind** (spec §3.3): "keep the language modality-blind; let `refrain-protocols` own the modality vocabulary." Do not add `meta.modality` to the language schema.
- **`reward` / `inhibit` are unchanged** (spec §3.2 item 2): "modality-neutral by nature… Leave unchanged."
- **Dated specs, plans, and audits are historical record — do not rewrite them.** Spec §3.1: "most are in dated specs/plans/CI logs that are historical record and should **not** be rewritten. Only touch living, user-facing docs." This exempts `refrain/docs/superpowers/**`, `refrain-protocols/docs/superpowers/**`, and `refrain-protocols/docs/fork-audit-2026-07.md`.
- **Keep the untested-badge honesty.** Spec §4.3: the badge keeps saying "untested, not validated, not a medical device, no health claims."
- **The "what Refrain is not" non-goal survives**, widened only in domain (spec §3.1): Refrain is still not a general-purpose signal-processing language.
- **No new dependencies.** `jsonschema` and `pytest` are already in `refrain-protocols` CI; nothing new is needed in either repo.
- **Unknown enum values still bucket to "Other" in host apps** (spec §4.3): "Unknown-value → 'Other' bucketing stays."

## Decisions taken at plan time

The spec left §8 items 1, 2, 3 open and the survey turned up four more. Resolved before writing this plan:

| Question | Decision |
|---|---|
| `montage` keyword (spec §8 #1) | Keep it, document it as a signal-source binding — option A. Forced by the "no language semantics" constraint. |
| Modality in the language (spec §8 #2) | Keep Refrain modality-blind. Same constraint. |
| Which signals get schema names (spec §8 #3) | The six proposed **plus breathing**: `eeg`, `ecg`, `hrv`, `gsr`, `emg`, `temp`, `resp`. The new `interoception` goal already covers breathing, so omitting it would force a second breaking change. |
| 21 files with an off-schema `evidence` value | Give each a real tier by copying the rating its own protocol family already carries; add a CI gate so it cannot drift again. Per-file table in Task 10. |
| The five clinically-shaped extra fields | Drop four — `indication`, `population`, `safety_monitoring`, `outcome_measures`. **Keep `control_ref`** (a pointer to a matching sham protocol: study design, not a health claim). |
| `hardware = "clinical_amp"` | Rename to `research_amp`. Behavior for host apps is unchanged. |
| The word "clinician" in distributed files (64 hits, 19 files) | Not covered by the spec; **recommendation below, flag at plan review.** Replace with "practitioner" for the operator role, and drop it entirely from user-facing titles ("clinician-chosen pair" → "chosen pair"). Rationale: the general-wellness envelope has practitioners, not clinicians, but the role itself is real and removing it would make several protocols unreadable. Task 12 carries this; if the review says otherwise, that task changes and nothing else does. |

## Review Focus

Five failure modes the spec implies but which no task's core assertions would otherwise exercise. Each one's test is added to the task that owns the code.

1. **A host app meets a goal string that isn't in the eight buckets** — a user's own protocol file with `goals = ["my_own_thing"]`. The picker must still list it (bucketed to "Other"), not drop or crash on it. Closing the schema enum must not make the *catalog builder* strict. → test in Task 15.
2. **A protocol file with no `modality` line** — after the backfill, every shipped file declares one, but a user's file won't. Hosts must still see `eeg`. The schema default has to actually be reachable through the catalog. → test in Task 13.
3. **A file whose `citation` is a placeholder, not a reference** — two files say `citation = "clinical convention (anxiety/rumination)"`. `citation` is required once `status` graduates past draft, so a naive scrub that empties it silently breaks the existing gate. → test in Task 10.
4. **A goals list emptied by the `trauma_recovery` drop** — the schema requires at least one goal. All four affected files happen to carry a second goal today, so the remap is safe now; nothing stops a later edit from emptying one. → test in Task 8.
5. **A typo'd `evidence` tier passing CI** — exactly what happened: 19 files say `demo`, 2 say `clinical`, and no test ever looked. The new gate must reject an unknown tier, not just the known-bad ones. → test in Task 9.

---

# Workstream 1 — `refrain` docs and framing

Worktree: `/Users/jcroall/git/refrain/refrain/.claude/worktrees/biosignal-reframe`

## File Structure (workstream 1)

| File | Responsibility | Action |
|---|---|---|
| `tests/test_framing.py` | The gate: asserts the retired category phrases appear in no living user-facing surface, and that the general-wellness positioning is present. One place to add a phrase when one is retired. | Create |
| `README.md` | Front door: subtitle, one-paragraph blurb, the reproducibility paragraph, the disclaimer section. | Modify (lines 3, 9, 11, 58, 115–119) |
| `pyproject.toml` | PyPI-visible package description — carries the same subtitle. | Modify (line 8) |
| `src/refrain/__init__.py` | Module docstring — carries the same subtitle. Docstring only; no code. | Modify (line 3) |
| `docs/CONCEPT.md` | The positioning argument: Summary, The Problem, The Vision, The CRED-nf bridge, What Refrain is not, Why Now. | Modify (lines 3, 15, 23, 36–38, 56–74, 130, 144–148, 156–165, 167–179) |
| `docs/SPEC.md` | Normative spec; only the opening framing sentence and the `montage` prose. | Modify (line 15, plus the `montage` section) |
| `docs/TOUR.md`, `docs/PRIMITIVES.md` | Walkthrough and primitive reference; category-vs-example sweep only. | Modify |
| `CHANGELOG.md` | Release note for the reframe. | Modify |

The gate lives in one file so that retiring a phrase later is a one-line edit, not a hunt.

---

### Task 1: The framing gate

The test comes first so every later task in this workstream has something that goes from red to green.

**Files:**
- Create: `tests/test_framing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RETIRED_PHRASES: list[str]` and `LIVING_DOCS: list[str]` — Tasks 2–5 add to `RETIRED_PHRASES` as they retire wording, and nothing else imports from here.

- [ ] **Step 1: Write the failing test**

Create `tests/test_framing.py`:

```python
# Copyright 2026 Refrain Language Authors. Apache-2.0.
"""Framing gate for the biosignal reframe.

Refrain describes biosignal *training paradigms*, not specifically clinical
neurofeedback. These assertions pin the wording on the living, user-facing
surfaces so the category framing cannot quietly come back.

Dated specs and plans under docs/superpowers/ are historical record and are
deliberately NOT covered here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Living, user-facing surfaces. Everything else in the tree is either code,
# tests, or dated historical record.
LIVING_DOCS = [
    "README.md",
    "pyproject.toml",
    "src/refrain/__init__.py",
    "docs/CONCEPT.md",
    "docs/SPEC.md",
    "docs/TOUR.md",
    "docs/PRIMITIVES.md",
    "docs/EMBEDDING.md",
    "docs/IR-JSON.md",
    "CONTRIBUTING.md",
]

# Phrases that name the OLD category. Substring match, case-insensitive.
# These are phrases, not single words: "clinical" alone is legitimate in
# "not a medical device" and in the `research_amp` hardware discussion.
RETIRED_PHRASES = [
    "clinical neurofeedback protocols",
    "clinical NF protocols",
    "for clinical neurofeedback",
]


@pytest.mark.parametrize("rel", LIVING_DOCS)
def test_no_retired_category_phrases(rel):
    text = (ROOT / rel).read_text(encoding="utf-8").lower()
    for phrase in RETIRED_PHRASES:
        assert phrase.lower() not in text, (
            f"{rel}: retired category phrase {phrase!r} — Refrain describes "
            f"biosignal training paradigms, not specifically clinical NF."
        )


def test_readme_carries_the_new_subtitle():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "An open description language for biosignal training paradigms." in text


def test_readme_states_general_wellness_positioning():
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "general-wellness" in text, "README must state the general-wellness envelope"
    assert "not a medical device" in text, "the honest disclaimer stays"
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /Users/jcroall/git/refrain/refrain/.claude/worktrees/biosignal-reframe
python -m pytest tests/test_framing.py -q
```

Expected: failures on `README.md`, `pyproject.toml`, `src/refrain/__init__.py`, `docs/CONCEPT.md`, `docs/SPEC.md` for the retired phrase, plus both README assertions.

- [ ] **Step 3: Commit the gate red**

```bash
git add tests/test_framing.py
git commit -m "test: add framing gate for the biosignal reframe

Fails against the current docs by design; tasks 2-5 turn it green."
```

---

### Task 2: Subtitle and package metadata

**Files:**
- Modify: `README.md:3`, `README.md:9`, `pyproject.toml:8`, `src/refrain/__init__.py:3`, `docs/CONCEPT.md:3`

**Interfaces:**
- Consumes: `tests/test_framing.py` from Task 1.
- Produces: the canonical subtitle string `An open description language for biosignal training paradigms.` — Tasks 3–5 reuse it verbatim.

- [ ] **Step 1: Change the four subtitle sites**

`README.md:3` and `docs/CONCEPT.md:3` — replace:

```
*An open description language for clinical neurofeedback protocols.*
```

with:

```
*An open description language for biosignal training paradigms.*
```

`pyproject.toml:8` — replace:

```toml
description = "An open description language for clinical neurofeedback protocols"
```

with:

```toml
description = "An open description language for biosignal training paradigms"
```

`src/refrain/__init__.py:3` — replace:

```python
"""Refrain — an open description language for clinical neurofeedback protocols.
```

with:

```python
"""Refrain — an open description language for biosignal training paradigms.
```

- [ ] **Step 2: Change the status line at `README.md:9`**

Replace `Pre-clinical validation.` with `Pre-validation — see the positioning note below.`

- [ ] **Step 3: Run the gate**

```bash
python -m pytest tests/test_framing.py -q
```

Expected: the retired-phrase test now passes for `pyproject.toml`, `src/refrain/__init__.py`, and `docs/CONCEPT.md`; `README.md` and `docs/SPEC.md` still fail (their body prose is Tasks 3 and 5); `test_readme_carries_the_new_subtitle` passes; `test_readme_states_general_wellness_positioning` still fails.

- [ ] **Step 4: Confirm nothing else broke**

```bash
python -m pytest -q -x
```

Expected: the whole suite passes except `tests/test_framing.py`, which is still partially red by design.

- [ ] **Step 5: Commit**

```bash
git add README.md pyproject.toml src/refrain/__init__.py docs/CONCEPT.md
git commit -m "docs: retitle Refrain as a biosignal training-paradigm language"
```

---

### Task 3: README body and disclaimer

**Files:**
- Modify: `README.md:11`, `README.md:58`, `README.md:115-119`

**Interfaces:**
- Consumes: the subtitle string from Task 2.
- Produces: the general-wellness disclaimer wording that Task 4 mirrors in `docs/CONCEPT.md`.

- [ ] **Step 1: Rewrite the blurb at `README.md:11`**

Replace the paragraph beginning `A Refrain file (`.refrain`) describes a complete clinical neurofeedback protocol` with:

```markdown
A Refrain file (`.refrain`) describes a complete biosignal training protocol — EEG neurofeedback, HRV coherence, GSR or temperature biofeedback — covering required hardware, signal-source binding, signal-processing pipeline, threshold logic, inhibit gates, reward expression, output bindings, and practitioner-tunable controls, at a level of precision a runtime can execute directly and a peer reviewer can audit directly.
```

- [ ] **Step 2: Demote the CRED-nf argument at `README.md:58`**

Replace that paragraph with:

```markdown
Biosignal training has a documented reproducibility problem driven by protocol heterogeneity across studies and proprietary closed-source software. In EEG neurofeedback the CRED-nf reporting checklist (Ros et al., *Brain*, 2020) describes in prose what a protocol must contain; HRV biofeedback has its own reporting norms (Lehrer & Gevirtz). Refrain makes either description executable: a single text file that is the protocol, the paper supplement, and the runnable artifact. CRED-nf is one supported reporting standard, not the reason Refrain exists.
```

- [ ] **Step 3: Rewrite the disclaimer section, `README.md:115-119`**

Replace the whole section — heading and both paragraphs — with:

```markdown
## Positioning and intended use

**Refrain is research and general-wellness software. It is not a medical device.** It has not been cleared by the FDA, CE-marked, or approved by any regulatory authority, and it makes no diagnostic or therapeutic claim. The Apache-2.0 license disclaims all warranties; use is at your own risk and subject to applicable law, institutional policy, and — for research use — IRB requirements.

The reference implementation has not been validated against any specific outcome. Validation is the responsibility of the host application and whoever runs the protocol. Refrain captures *what a protocol computes*; it does not assert that any protocol is appropriate for any given person or purpose.
```

- [ ] **Step 4: Run the gate**

```bash
python -m pytest tests/test_framing.py -q
```

Expected: every `README.md` assertion passes, including `test_readme_states_general_wellness_positioning`. Only `docs/SPEC.md` remains red.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): general-wellness positioning, CRED-nf as one standard"
```

---

### Task 4: `docs/CONCEPT.md` — the positioning argument

The largest prose edit in this workstream: 38 uses of "clinical" and the whole Vision/Why-Now argument currently rest on clinical NF plus FDA-SaMD auditability.

**Files:**
- Modify: `docs/CONCEPT.md` — lines 15, 23, 36–38, 56–74, 130, 144–148, 156–165, 167–179

**Interfaces:**
- Consumes: the subtitle from Task 2 and the disclaimer wording from Task 3.
- Produces: nothing other tasks read.

- [ ] **Step 1: Rewrite the Summary paragraph, line 15**

Replace `Refrain is a proposed declarative description language for clinical NF protocols.` with `Refrain is a declarative description language for biosignal training paradigms — the operant loop of input, derive, threshold, reward or inhibit, output.` Keep the rest of the paragraph; change `what a NF protocol does` to `what a training protocol does`, and `the montage` to `the signal-source binding`. Change the last sentence to: `For EEG neurofeedback, the same artifact fulfills the existing CRED-nf reporting checklist by construction.`

- [ ] **Step 2: Widen the problem statement, line 23 and lines 36–38**

Retitle the heading at line 23 from `### Clinical NF has a reproducibility crisis, and the field admits it` to `### Biosignal training has a reproducibility problem, and the field admits it`. In lines 36–38, keep the CRED-nf description intact — it is accurate about EEG neurofeedback — but open line 36 with `In EEG neurofeedback, the community has responded with the **CRED-nf checklist**…` and change `a clinical neuroscientist re-implementing` at line 38 to `someone re-implementing`.

- [ ] **Step 3: Rewrite The Vision, lines 56–74**

Lead with the general biosignal-processing category rather than clinical NF. Open the section with:

```markdown
Biosignal processing tools have existed for decades — BioExplorer and BioEra let practitioners wire up signal chains visually, and people built real work on them. What none of them produced was a *portable artifact*: the protocol lived inside the tool, in a format only that tool could read.

Refrain's vision is the artifact. One text file that describes a complete training paradigm — for brainwaves, heart-rate variability, skin conductance, temperature — precisely enough to execute, review, publish, and re-run somewhere else. It is general-wellness and research software, used by practitioners, researchers, and people training themselves.
```

Then keep the existing portability and auditability arguments that follow, deleting any sentence whose force comes from FDA-SaMD or clinical-device auditability. EEG neurofeedback becomes one worked example in this section, not the thesis.

- [ ] **Step 4: Fix line 130**

Replace `Refrain is a declarative description language for clinical neurofeedback protocols.` with `Refrain is a declarative description language for biosignal training paradigms.`

- [ ] **Step 5: Demote the CRED-nf bridge, lines 144–148**

Keep the section and the feature. Change the opening of line 146 from `The most consequential single design decision:` to `One consequential design decision:`. After line 148, add:

```markdown
CRED-nf is one supported reporting standard, not the only one. HRV biofeedback has its own reporting norms (Lehrer & Gevirtz), and the same protocol-as-artifact model serves them: the file already contains what those checklists ask authors to describe.
```

- [ ] **Step 6: Widen the non-goal, lines 156–165**

Keep the section and its force. Change `Refrain describes clinical NF protocols` to `Refrain describes biosignal training/feedback paradigms`. Add one sentence: `Everything outside a training paradigm — raw recording, general analysis, arbitrary DSP — still belongs elsewhere. numpy, MNE, and MATLAB exist and Refrain is not trying to replace them.`

- [ ] **Step 7: Rewrite Why Now, lines 167–179**

Replace the `**The clinical literature is asking for it.**` bullet at line 171 with:

```markdown
**The reporting standards are asking for it.** CRED-nf has been published, endorsed, and is used as a quality rubric in recent systematic reviews; HRV biofeedback has equivalent reporting norms. The community has done the work of agreeing what *should* be reported. What is missing is the format that makes reporting executable.
```

Remove the FDA-SaMD sentences elsewhere in the section; replace their argument with the general-wellness envelope and the absence of a portable artifact.

- [ ] **Step 8: Run the gate and the suite**

```bash
python -m pytest tests/test_framing.py -q && python -m pytest -q
```

Expected: `docs/CONCEPT.md` passes the gate; the rest of the suite is unaffected.

- [ ] **Step 9: Verify no category framing survived**

```bash
grep -n -i "clinical" docs/CONCEPT.md
```

Expected: only legitimate uses remain — quoting a cited paper's own title or scope, or discussing research-grade hardware. Read each hit; there should be no sentence left that says Refrain is *for* clinical work.

- [ ] **Step 10: Commit**

```bash
git add docs/CONCEPT.md
git commit -m "docs(concept): lead with biosignal processing, demote CRED-nf to one standard"
```

---

### Task 5: `docs/SPEC.md`, `docs/TOUR.md`, `docs/PRIMITIVES.md` — category sweep and the `montage` paragraph

The rule from the spec: change uses of "neurofeedback"/"NF" where they name the *category*; keep them where they are a legitimate example.

**Files:**
- Modify: `docs/SPEC.md:15`, plus the `montage` section of `docs/SPEC.md`
- Modify: `docs/TOUR.md`, `docs/PRIMITIVES.md`

**Interfaces:**
- Consumes: the subtitle from Task 2.
- Produces: the `montage`-as-signal-source-binding paragraph, which is the only deliverable the spec's §8 #1 decision requires.

- [ ] **Step 1: Fix the SPEC opening, line 15**

Replace:

```
Refrain is a declarative description language for clinical neurofeedback protocols. A Refrain file describes, in full, what a NF protocol does: required hardware, channel montage, signal-processing pipeline, threshold logic, inhibit gates, reward expression, output bindings, clinician-tunable controls, and session structure.
```

with:

```
Refrain is a declarative description language for biosignal training paradigms. A Refrain file describes, in full, what a training protocol does: required hardware, signal-source binding, signal-processing pipeline, threshold logic, inhibit gates, reward expression, output bindings, practitioner-tunable controls, and session structure. EEG neurofeedback is the most developed worked example; HRV coherence training uses the same structure.
```

- [ ] **Step 2: Add the `montage` paragraph to `docs/SPEC.md`**

Find the `montage` definition and add, immediately after it:

```markdown
`montage` names one kind of **signal-source binding** — how a named input is
derived from the hardware's channels. `referential(...)`, `bipolar(...)`, and
`laplacian(...)` are EEG montages in the conventional sense. `passthrough()` is
the identity binding for a single-channel non-EEG source: an HRV tachogram, a
skin-conductance level, a temperature trace. The keyword is EEG-flavored for
historical reasons; the concept is not. Nothing about a `passthrough` input
requires an EEG amplifier.
```

- [ ] **Step 3: Sweep `docs/SPEC.md` for the remaining category uses**

```bash
grep -n -i "clinician\|clinical\|neurofeedback\|\bNF\b" docs/SPEC.md
```

For each hit, decide category-or-example. Change `clinician-tunable` to `practitioner-tunable` throughout (11 hits). Leave any sentence where neurofeedback is genuinely the example being discussed.

- [ ] **Step 4: Sweep `docs/TOUR.md` and `docs/PRIMITIVES.md`**

```bash
grep -n -i "clinician\|clinical\|neurofeedback\|\bNF\b" docs/TOUR.md docs/PRIMITIVES.md
```

Same rule. `docs/TOUR.md` has 8 uses of "clinical" and 7 of "clinician"; `docs/PRIMITIVES.md` has 4 of "clinical". Where the tour walks through an SMR protocol, that is an example and stays; where it says the language is *for* clinical work, that is the category and changes.

- [ ] **Step 5: Run the gate and the full suite**

```bash
python -m pytest -q
```

Expected: all green, including `tests/test_framing.py`.

- [ ] **Step 6: Commit**

```bash
git add docs/SPEC.md docs/TOUR.md docs/PRIMITIVES.md
git commit -m "docs: category sweep; document montage as a signal-source binding"
```

---

### Task 6: Changelog and workstream-1 verification

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the release note**

Add at the top of the unreleased section:

```markdown
### Changed
- **Framing.** Refrain is described as a language for *biosignal training
  paradigms* rather than specifically clinical neurofeedback. EEG
  neurofeedback remains the most developed worked example. No language,
  IR-JSON, or embedding-API change — every existing `.refrain` file and its
  compiled IR keeps running unchanged.
- **Positioning.** The clinical-use disclaimer is now a general-wellness and
  research positioning statement: not a medical device, no diagnostic or
  therapeutic claim.
- **`montage`** is documented as one kind of signal-source binding.
  `passthrough()` is the identity binding for single-channel non-EEG sources.
  The keyword itself is unchanged.
- CRED-nf is described as one supported reporting standard rather than the
  language's reason for existing.
```

- [ ] **Step 2: Run the full suite and show the output**

```bash
python -m pytest -q
```

Expected: all tests pass. Paste the output into the PR; a claim of green without the run does not count.

- [ ] **Step 3: Commit and push**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note the biosignal-platform reframe"
git push -u origin worktree-biosignal-reframe
```

- [ ] **Step 4: Open the PR**

Target `claude/biosignal-platform-framing-8gk0it` (the branch the approved proposal lives on), not `main`. Title: `docs: reframe Refrain as a biosignal training-paradigm language`. Body must state: no language, IR-JSON, or embedding-API change; links to `docs/proposals/2026-07-23-biosignal-platform-reframe.md` §3.

---

# Workstream 2 — `refrain-protocols` neutralization

Worktree: `/Users/jcroall/git/refrain-protocols/.claude/worktrees/biosignal-reframe`

## File Structure (workstream 2)

| File | Responsibility | Action |
|---|---|---|
| `schema/protocol-meta.schema.json` | The contract: goal vocabulary, modality list, hardware classes, provenance-framed descriptions. | Modify |
| `tests/test_corpus_schema.py` | New gate — validates **every** corpus file against the whole schema. Today's tests only check three fields by hand, which is how 21 files drifted. | Create |
| `tests/test_neutrality.py` | New gate — forbids the four dropped fields and the indication vocabulary in distributed prose. | Create |
| `tests/test_schema_fields.py` | Existing hand-written schema samples; the goal strings in them are now retired. | Modify |
| `protocols/**/*.refrain`, `drafts/scp_cz.refrain` | The 44 distributed files: goals, evidence, dropped fields, comments, modality, hardware. | Modify |
| `catalog.json` | Derived cache; regenerated, never hand-edited. | Regenerate |
| `README.md`, `docs/evidence.md`, `docs/tagging.md`, `docs/host-app-guide.md`, `docs/conventions.md`, `docs/contributing.md`, `ROADMAP.md` | Library docs. | Modify |
| `CHANGELOG.md` | Breaking-change note. | Modify |
| `docs/fork-audit-2026-07.md`, `docs/superpowers/**` | Dated historical record. | **Do not touch** |

Two new test files rather than one: the corpus-schema gate is about *validity*, the neutrality gate is about *content*. They fail for different reasons and a reviewer should be able to reject one without the other.

---

### Task 7: Rewrite the schema

This deliberately turns the existing corpus test red. That failure is the specification for Tasks 8–13.

**Files:**
- Modify: `schema/protocol-meta.schema.json`
- Modify: `tests/test_schema_fields.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the goal enum (`focus_attention`, `calm_stress`, `sleep_quality`, `alertness_performance`, `mood_balance`, `flow_connectivity`, `deep_meditative`, `interoception`), the modality enum (`eeg`, `ecg`, `hrv`, `gsr`, `emg`, `temp`, `resp`), and the hardware enum (`generic`, `brainbit_flex`, `research_amp`). Tasks 8, 12, and 13 read these.

- [ ] **Step 1: Replace the `goals` property**

```json
    "goals": {
      "description": "Wellness-framed training goal categories (MULTI-membership). Drives the picker's groups. Controlled vocab; a host app buckets an unknown value into 'Other' rather than dropping the protocol. These are training goals, not indications: no diagnostic or therapeutic claim is made or implied.",
      "type": "array",
      "items": {
        "enum": [
          "focus_attention", "calm_stress", "sleep_quality",
          "alertness_performance", "mood_balance", "flow_connectivity",
          "deep_meditative", "interoception"
        ]
      },
      "minItems": 1
    },
```

- [ ] **Step 2: Replace the `evidence` property**

```json
    "evidence": {
      "description": "How established the signal-training APPROACH is in prior art (distinct from `status`, which is our file's maturity). This is provenance about the technique, not a claim about outcomes for any person.",
      "enum": ["established", "probable", "exploratory"]
    },
```

- [ ] **Step 3: Replace `citation`, `modality`, `hardware`, and `site`**

```json
    "citation": { "type": "string", "description": "Author-anchored reference for where the approach comes from — origin and prior art, not proof of outcome. Required once status > draft." },
```

```json
    "modality": {
      "description": "Signal family the protocol trains.",
      "enum": ["eeg", "ecg", "hrv", "gsr", "emg", "temp", "resp"],
      "default": "eeg"
    },
    "hardware": {
      "description": "Capability class the protocol targets. 'research_amp' = greyed out on a consumer device.",
      "enum": ["generic", "brainbit_flex", "research_amp"]
    },
```

```json
    "site": { "type": "string", "description": "Channel / source label — an EEG scalp site such as 'Cz' or 'F3/F4', or a non-EEG source label such as 'tachogram', 'gsr', 'temp'." },
```

- [ ] **Step 4: Generalize the `bands` description**

```json
    "bands": {
      "description": "Band chips shown on the row. Modality-scoped and optional: EEG bands like 'alpha'/'smr', HRV bands like 'hrv-lf'. Omit for modalities with no band concept, e.g. a raw skin-conductance level.",
      "type": "array",
      "items": { "type": "string" }
    },
```

- [ ] **Step 5: Reword the `status` description**

Change `"OUR file maturity / test status (distinct from clinical \`evidence\`)."` to `"OUR file maturity / test status (distinct from \`evidence\`, which describes the technique's prior art)."`

- [ ] **Step 6: Update the six goal strings in `tests/test_schema_fields.py`**

Every `"goals": ["adhd_attention"]` and `goals = ["adhd_attention"]` in that file becomes `focus_attention` (lines 13, 28, 34, 42, 48, 53).

- [ ] **Step 7: Run the tests and confirm the corpus goes red**

```bash
cd /Users/jcroall/git/refrain-protocols/.claude/worktrees/biosignal-reframe
python -m pytest -q
```

Expected: `tests/test_schema_fields.py` passes. `tests/test_catalog.py::test_meta_schema` fails on 33 files with `unknown goal 'adhd_attention'` and similar. That red is the spec for Task 8.

- [ ] **Step 8: Commit**

```bash
git add schema/protocol-meta.schema.json tests/test_schema_fields.py
git commit -m "feat(schema)!: wellness goal vocabulary, 7 modalities, research_amp

BREAKING: goals enum replaced (8 buckets), modality widened to
eeg/ecg/hrv/gsr/emg/temp/resp, hardware clinical_amp -> research_amp.
Evidence and citation reframed as technique provenance, not efficacy.
Corpus remap follows in the next commits."
```

---

### Task 8: Remap the goals across the corpus

33 of the 44 files need a goals edit. The other 11 already use goals that survive unchanged.

**Files:**
- Modify: 33 `.refrain` files (table below)

**Interfaces:**
- Consumes: the goal enum from Task 7.
- Produces: a corpus that satisfies `test_meta_schema`.

The mapping, applied token-by-token, preserving order and dropping duplicates:

| Old goal | New goal |
|---|---|
| `adhd_attention` | `focus_attention` |
| `calm_anxiety` | `calm_stress` |
| `sensorimotor_sleep` | `sleep_quality` |
| `mood_regulation` | `mood_balance` |
| `trauma_recovery` | **removed** (never replaced) |
| `alertness_performance`, `flow_connectivity`, `deep_meditative` | unchanged |

Four files carry `trauma_recovery`; each has a second goal, so none is left empty:

| File | Before | After |
|---|---|---|
| `protocols/eeg/alpha_theta.refrain` | `["deep_meditative", "trauma_recovery"]` | `["deep_meditative"]` |
| `protocols/eeg/faa_f3f4.refrain` | `["mood_regulation", "trauma_recovery"]` | `["mood_balance"]` |
| `protocols/eeg/legacy/alpha_down_pz.refrain` | `["trauma_recovery", "deep_meditative"]` | `["deep_meditative"]` |
| `protocols/eeg/legacy/alpha_theta_pz.refrain` | `["deep_meditative", "trauma_recovery"]` | `["deep_meditative"]` |

One addition beyond a pure rename — flag this at review if unwanted: `protocols/hrv_resonance.refrain` gains `interoception`, giving the new bucket its one member. Result: `["calm_stress", "flow_connectivity", "interoception"]`. Spec §4.2.1 lists HRV under `interoception`; without this, the bucket ships empty.

- [ ] **Step 1: Write the emptied-goals guard (Review Focus item 4)**

Append to `tests/test_schema_fields.py`:

```python
def test_empty_goals_rejected():
    # The trauma_recovery drop must never leave a file with no goal at all.
    doc = {"description": "d", "status": "draft", "goals": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)
```

- [ ] **Step 2: Run it**

```bash
python -m pytest tests/test_schema_fields.py::test_empty_goals_rejected -q
```

Expected: PASS — `minItems: 1` already enforces it. This test pins that behavior so a later schema edit cannot silently relax it.

- [ ] **Step 3: Apply the renames**

```bash
grep -rl 'adhd_attention\|calm_anxiety\|sensorimotor_sleep\|mood_regulation' \
  --include='*.refrain' protocols drafts \
  | xargs sed -i '' \
    -e 's/"adhd_attention"/"focus_attention"/g' \
    -e 's/"calm_anxiety"/"calm_stress"/g' \
    -e 's/"sensorimotor_sleep"/"sleep_quality"/g' \
    -e 's/"mood_regulation"/"mood_balance"/g'
```

- [ ] **Step 4: Remove `trauma_recovery` by hand in the four files above**

`sed` is the wrong tool here — the token appears in both first and second position, so removing it also means fixing the comma. Edit each of the four files directly to the "After" value in the table.

- [ ] **Step 5: Add `interoception` to the HRV protocol**

In `protocols/hrv_resonance.refrain:15`, change the goals line to:

```
    goals           = ["calm_stress", "flow_connectivity", "interoception"]
```

- [ ] **Step 6: Verify no old goal survives**

```bash
grep -rn 'adhd_attention\|calm_anxiety\|sensorimotor_sleep\|mood_regulation\|trauma_recovery' \
  --include='*.refrain' protocols drafts
```

Expected: no output.

- [ ] **Step 7: Run the corpus gate**

```bash
python -m pytest tests/test_catalog.py -q
```

Expected: `test_meta_schema` passes for all 44 files. `test_catalog_current` fails — the catalog is now stale. That is expected and Task 15 fixes it.

- [ ] **Step 8: Commit**

```bash
git add protocols drafts tests/test_schema_fields.py
git commit -m "feat(protocols)!: remap goals to the wellness vocabulary

BREAKING: adhd_attention->focus_attention, calm_anxiety->calm_stress,
sensorimotor_sleep->sleep_quality, mood_regulation->mood_balance;
trauma_recovery dropped from 4 files, each of which keeps another goal.
hrv_resonance gains interoception."
```

---

### Task 9: The corpus-validity gate

Today `test_meta_schema` hand-checks three fields. Nothing validates a file against the whole schema, which is why 21 files carry an `evidence` value that was never allowed.

**Files:**
- Create: `tests/test_corpus_schema.py`

**Interfaces:**
- Consumes: the schema from Task 7.
- Produces: `_meta(path) -> dict` — a parse-only meta reader duplicated from `tests/test_catalog.py` so the two gates stay independent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus_schema.py`:

```python
# Copyright 2026 Refrain Language Authors. Apache-2.0.
"""Whole-schema validation of every distributed protocol.

tests/test_catalog.py hand-checks a few fields. This validates the entire
`meta` block against protocol-meta.schema.json, which is what catches a tier
or enum value nobody declared -- the hole that let `evidence = "demo"` sit in
19 files unnoticed.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from refrain.parser import parse

ROOT = Path(__file__).resolve().parents[1]
ALL = sorted((ROOT / "protocols").rglob("*.refrain")) + sorted(
    (ROOT / "drafts").rglob("*.refrain")
)
SCHEMA = json.loads((ROOT / "schema" / "protocol-meta.schema.json").read_text())


def _meta(path: Path) -> dict:
    f = parse(path.read_text())
    out: dict = {}
    for stmt in f.protocol.body:
        if getattr(stmt, "keyword", None) == "meta":
            for a in stmt.body:
                v = a.value
                out[a.target] = (
                    [getattr(e, "value", None) for e in v.elements]
                    if hasattr(v, "elements") else getattr(v, "value", None)
                )
    return out


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_whole_meta_validates(path):
    jsonschema.validate(_meta(path), SCHEMA)


def test_unknown_evidence_tier_rejected():
    # Review Focus 5: the gate must reject ANY unknown tier, not just the two
    # bad values that happened to be in the corpus.
    doc = {"description": "d", "status": "draft", "goals": ["focus_attention"],
           "evidence": "definitely_not_a_tier"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)
```

- [ ] **Step 2: Run it and confirm it fails on 21 files**

```bash
python -m pytest tests/test_corpus_schema.py -q
```

Expected: 21 failures, each `'demo' is not one of ['established', 'probable', 'exploratory']` or the same for `'clinical'`. `test_unknown_evidence_tier_rejected` passes.

- [ ] **Step 3: Commit the gate red**

```bash
git add tests/test_corpus_schema.py
git commit -m "test: validate every protocol against the whole meta schema

Red on 21 files carrying an evidence tier that was never in the enum."
```

---

### Task 10: Assign real evidence tiers

Every file below gets the tier its own protocol family already carries elsewhere in the library.

**Files:**
- Modify: the 21 `.refrain` files in the table
- Modify: `protocols/eeg/high_beta_down.refrain:54`, `protocols/eeg/legacy/hibeta_down_cz.refrain:17` (the placeholder citations)

**Interfaces:**
- Consumes: the evidence enum from Task 7.
- Produces: a corpus that passes `tests/test_corpus_schema.py`.

| File | Family | Now | Becomes |
|---|---|---|---|
| `protocols/eeg/alpha_coherence.refrain` | `placement_alpha_coherence` | `demo` | `exploratory` |
| `protocols/eeg/alpha_theta.refrain` | `alpha_theta` | `demo` | `established` |
| `protocols/eeg/alpha_up.refrain` | `alpha_up` | `demo` | `probable` |
| `protocols/eeg/beta_attention.refrain` | `beta_attention` | `demo` | `probable` |
| `protocols/eeg/beta_focus_staged_fz.refrain` | `beta_focus_fz` | `demo` | `probable` |
| `protocols/eeg/high_beta_down.refrain` | `high_beta_down` | `demo` | `probable` |
| `protocols/eeg/placement_smr_bipolar.refrain` | `placement_smr` | `demo` | `established` |
| `protocols/eeg/placement_smr_set.refrain` | `placement_smr` | `demo` | `established` |
| `protocols/eeg/smr.refrain` | `smr` | `demo` | `established` |
| `protocols/eeg/smr_classic_baseline_staged_cz.refrain` | `smr_cz` | `demo` | `established` |
| `protocols/eeg/legacy/alpha_symmetry_c3c4_brainbit.refrain` | `alpha_coherence_c3c4` | `demo` | `exploratory` |
| `protocols/eeg/legacy/alpha_theta_pz_brainbit.refrain` | `alpha_theta_pz` | `clinical` | `established` |
| `protocols/eeg/legacy/alpha_up_pz_brainbit.refrain` | `alpha_up_pz` | `demo` | `probable` |
| `protocols/eeg/legacy/beta_up_cz.refrain` | `beta_up_cz` | `demo` | `probable` |
| `protocols/eeg/legacy/composite_smr_theta_cz_brainbit.refrain` | `composite_smr_theta_cz` | `demo` | `established` |
| `protocols/eeg/legacy/high_beta_down_cz_brainbit.refrain` | `hibeta_down_cz` | `demo` | `probable` |
| `protocols/eeg/legacy/placement_smr_active.refrain` | `placement_smr` | `demo` | `established` |
| `protocols/eeg/legacy/smr_classic_cz.refrain` | `smr_cz` | `demo` | `established` |
| `protocols/eeg/legacy/smr_cz_modulating.refrain` | `smr_cz` | `demo` | `established` |
| `protocols/eeg/legacy/smr_graded_cz.refrain` | `smr_cz` | `demo` | `established` |
| `protocols/eeg/legacy/smr_up_c4_brainbit.refrain` | `smr_up_c4` | `clinical` | `established` |

- [ ] **Step 1: Write the placeholder-citation guard (Review Focus item 3)**

Append to `tests/test_corpus_schema.py`:

```python
PLACEHOLDER_CITATIONS = ("clinical convention", "convention", "n/a", "tbd", "")


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_citation_is_a_real_reference(path):
    m = _meta(path)
    if m.get("status") in ("draft", "roadmap"):
        return
    cite = (m.get("citation") or "").strip()
    assert cite, f"{path.name}: status>{m['status']} requires a citation"
    assert cite.lower() not in PLACEHOLDER_CITATIONS, (
        f"{path.name}: citation {cite!r} is a placeholder, not a reference"
    )
    assert "clinical convention" not in cite.lower(), (
        f"{path.name}: citation {cite!r} still frames provenance clinically"
    )
```

- [ ] **Step 2: Run it and confirm two failures**

```bash
python -m pytest tests/test_corpus_schema.py::test_citation_is_a_real_reference -q
```

Expected: FAIL on `high_beta_down.refrain` and `hibeta_down_cz.refrain`, both `citation = "clinical convention (anxiety/rumination)"`.

- [ ] **Step 3: Replace the two placeholder citations**

In both files, replace the citation with a real reference for high-beta down-training:

```
    citation        = "Hammond D.C. (2005). Neurofeedback treatment of depression and anxiety. Journal of Adult Development, 12(2-3), 131-137."
```

The paper's own title contains the clinical words; that is the reference's real name and must not be altered. This is exactly the "you cannot make these techniques *not* originate in clinical literature" floor the spec calls out — provenance is honest, claims are not.

- [ ] **Step 4: Apply the evidence tiers**

Apply the table file by file. The two `"clinical"` values can be done mechanically:

```bash
grep -rl 'evidence.*= *"clinical"' --include='*.refrain' protocols \
  | xargs sed -i '' 's/= *"clinical"/= "established"/'
```

The 19 `"demo"` files need three different targets, so edit them per the table rather than with one substitution.

- [ ] **Step 5: Verify the gate is green**

```bash
python -m pytest tests/test_corpus_schema.py -q
grep -rn 'evidence.*"demo"\|evidence.*"clinical"' --include='*.refrain' protocols drafts
```

Expected: all tests pass; the grep returns nothing.

- [ ] **Step 6: Commit**

```bash
git add protocols tests/test_corpus_schema.py
git commit -m "fix(protocols): real evidence tiers for the 21 drifted files

19 files said 'demo' and 2 said 'clinical' -- neither was ever in the enum,
because nothing validated the field. Each now carries the tier its own
protocol family already used. Two placeholder citations replaced with the
actual reference."
```

---

### Task 11: Drop the four clinical fields

`indication` (21 files), `population` (17), `safety_monitoring` (21), `outcome_measures` (17). `control_ref` (17) stays.

**Files:**
- Create: `tests/test_neutrality.py`
- Modify: the affected `.refrain` files

**Interfaces:**
- Consumes: `_meta` pattern from Task 9.
- Produces: `FORBIDDEN_FIELDS` and `INDICATION_WORDS` — Task 12 extends `INDICATION_WORDS`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_neutrality.py`:

```python
# Copyright 2026 Refrain Language Authors. Apache-2.0.
"""A distributed protocol makes no diagnostic or therapeutic claim.

The library ships general-wellness signal-training building blocks. Provenance
(`evidence`, `citation`) is kept and is honest about where a technique comes
from. What is NOT shipped is any statement about what a protocol is for,
medically, or who should receive it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from refrain.parser import parse

ROOT = Path(__file__).resolve().parents[1]
ALL = sorted((ROOT / "protocols").rglob("*.refrain")) + sorted(
    (ROOT / "drafts").rglob("*.refrain")
)

# Dropped from the distributed contract. A host app that needs these keeps
# them host-side; a neutral library ships no indications.
# `control_ref` is deliberately NOT here: a pointer to a matching sham
# protocol is study design, not a health claim.
FORBIDDEN_FIELDS = (
    "indication",
    "population",
    "safety_monitoring",
    "outcome_measures",
)


def _meta(path: Path) -> dict:
    f = parse(path.read_text())
    out: dict = {}
    for stmt in f.protocol.body:
        if getattr(stmt, "keyword", None) == "meta":
            for a in stmt.body:
                v = a.value
                out[a.target] = (
                    [getattr(e, "value", None) for e in v.elements]
                    if hasattr(v, "elements") else getattr(v, "value", None)
                )
    return out


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_no_indication_fields(path):
    m = _meta(path)
    present = [f for f in FORBIDDEN_FIELDS if f in m]
    assert not present, (
        f"{path.name}: {present} dropped from the distributed contract — "
        f"a neutral library ships no indications. Keep them host-side."
    )
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/test_neutrality.py -q
```

Expected: FAIL on 21 files.

- [ ] **Step 3: Delete the four fields**

```bash
grep -rl 'indication\|population\|safety_monitoring\|outcome_measures' \
  --include='*.refrain' protocols drafts \
  | xargs sed -i '' \
    -e '/^[[:space:]]*indication[[:space:]]*=/d' \
    -e '/^[[:space:]]*population[[:space:]]*=/d' \
    -e '/^[[:space:]]*safety_monitoring[[:space:]]*=/d' \
    -e '/^[[:space:]]*outcome_measures[[:space:]]*=/d'
```

- [ ] **Step 4: Confirm `control_ref` survived**

```bash
grep -rc 'control_ref' --include='*.refrain' protocols | grep -v ':0' | wc -l
```

Expected: `17`.

- [ ] **Step 5: Run the gate and the parse check**

```bash
python -m pytest tests/test_neutrality.py tests/test_corpus_schema.py tests/test_catalog.py::test_parses -q
```

Expected: all pass. Every file still parses — the deletions were whole lines inside `meta`.

- [ ] **Step 6: Commit**

```bash
git add protocols drafts tests/test_neutrality.py
git commit -m "feat(protocols)!: drop indication, population, safety_monitoring, outcome_measures

BREAKING: removed from the distributed contract. A host app that needs
condition, population, or clinical-instrument metadata keeps it host-side.
control_ref stays -- a sham-protocol pointer is study design, not a claim."
```

---

### Task 12: Scrub the prose

Comments, titles, descriptions, and summaries. This is the "make no claim" half of the reframe — the fields are gone, but several files still *say* the clinical thing in prose.

**Files:**
- Modify: `tests/test_neutrality.py`
- Modify: the `.refrain` files listed below

**Interfaces:**
- Consumes: `INDICATION_WORDS` extension point from Task 11.

Confirmed hits to fix:

| File | Line | What it says | Becomes |
|---|---|---|---|
| `protocols/eeg/faa_f3f4.refrain` | 4 | `the depression FAA protocol` | describe the F3/F4 alpha-asymmetry training; no condition named |
| `protocols/eeg/faa_f3f4.refrain` | 15 | `description = "…(mood / approach-motivation)"` | `"Frontal alpha asymmetry F3/F4 (left-right alpha balance)"` |
| `protocols/eeg/legacy/alpha_down_pz.refrain` | 9 | `used in trauma-oriented desensitization work` | `Rewards lowering alpha at the back of the head.` |
| `protocols/eeg/high_beta_down.refrain` | 27, 48 | `over-arousal/anxiety work`, `associated with anxiety and rumination` | describe the band and direction only |
| `protocols/eeg/legacy/hibeta_down_cz.refrain` | 2, 12 | `clinical convention (anxiety/rumination)` | `Rewards lowering fast high-beta activity.` |
| `protocols/eeg/legacy/high_beta_down_cz_brainbit.refrain` | 25 | `linked to anxiety and rumination` | drop the clause |
| `protocols/eeg/legacy/alpha_up_pz_brainbit.refrain` | 26 | `for calm and lowered anxiety` | `for a calm, settled state` |
| `protocols/eeg/beta_attention.refrain` | 11 | `the standard ADHD/attention menu` | `the standard attention menu` |
| `protocols/eeg/alpha_theta.refrain` | 59 | `descent is not treated as a failure` | keep — "treated" here is plain English, not a therapeutic claim |
| 6 files | various | `patient` (11 hits) | `person` |
| 19 files | various | `clinician` (64 hits) | `practitioner`; in user-facing titles drop it — `"Alpha coherence — clinician-chosen pair"` becomes `"Alpha coherence — chosen pair"` |

- [ ] **Step 1: Write the failing test**

Append to `tests/test_neutrality.py`:

```python
# Words that name a condition or a medical role. A distributed file may cite a
# paper whose TITLE contains them -- provenance is honest -- but must not use
# them in its own prose.
INDICATION_WORDS = (
    "depression", "depressive", "trauma", "ptsd", "anxiety", "anxious",
    "adhd", "patient", "diagnos", "clinician", "clinical",
)

PROSE_FIELDS = ("description", "title", "summary")


def _citation_spans(text: str) -> list[str]:
    """Lines that ARE a citation -- exempt: a reference keeps its real title."""
    return [ln for ln in text.splitlines() if ln.lstrip().startswith("citation")]


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_no_indication_language_in_prose(path):
    text = path.read_text(encoding="utf-8")
    exempt = set(_citation_spans(text))
    offenders = []
    for i, line in enumerate(text.splitlines(), 1):
        if line in exempt:
            continue
        low = line.lower()
        for w in INDICATION_WORDS:
            if w in low:
                offenders.append(f"{i}: {w!r} in {line.strip()[:80]}")
    assert not offenders, (
        f"{path.name}: indication language in distributed prose:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.name)
def test_prose_fields_present(path):
    # The scrub must not leave a file with an empty label.
    m = _meta(path)
    for field in PROSE_FIELDS:
        if field in m:
            assert (m[field] or "").strip(), f"{path.name}: {field} emptied by the scrub"
```

- [ ] **Step 2: Run it**

```bash
python -m pytest tests/test_neutrality.py::test_no_indication_language_in_prose -q 2>&1 | tail -40
```

Expected: roughly 19 files fail; the report names every line to fix.

- [ ] **Step 3: Apply the two mechanical replacements**

```bash
grep -rl '\bpatient\b' --include='*.refrain' protocols drafts \
  | xargs sed -i '' 's/\bpatient\b/person/g; s/\bpatients\b/people/g; s/\bpatient'"'"'s\b/person'"'"'s/g'

grep -rl '\bclinician\b' --include='*.refrain' protocols drafts \
  | xargs sed -i '' 's/\bclinician\b/practitioner/g; s/\bClinician\b/Practitioner/g'
```

- [ ] **Step 4: Fix the condition-naming prose by hand**

Work down the test output. For each hit, describe the *signal training* — which band, which direction, which site — and name no condition. The failing test is the checklist; it goes green when the list is empty.

Note `practitioner-chosen` will still appear in titles after Step 3. In user-facing `title` and `summary` fields only, drop the word: `"Alpha coherence — practitioner-chosen pair"` becomes `"Alpha coherence — chosen pair"`. Keep it in comments, where the operator role is the point.

- [ ] **Step 5: Run both neutrality gates**

```bash
python -m pytest tests/test_neutrality.py -q
```

Expected: all pass, including `test_prose_fields_present` — nothing was scrubbed to an empty string.

- [ ] **Step 6: Run the corpus gates**

```bash
python -m pytest tests/test_corpus_schema.py tests/test_catalog.py::test_parses -q
```

Expected: pass. The edits were prose; nothing structural moved.

- [ ] **Step 7: Commit**

```bash
git add protocols drafts tests/test_neutrality.py
git commit -m "feat(protocols): describe the signal training, not an indication

Comments, titles, and summaries no longer name a condition. 'patient' ->
'person', 'clinician' -> 'practitioner' (dropped entirely from user-facing
titles). Citations keep their real paper titles -- provenance is honest."
```

---

### Task 13: Backfill modality and rename the hardware class

**Files:**
- Modify: 42 `.refrain` files — the 41 EEG protocols lacking a `modality` line, plus `drafts/scp_cz.refrain` (an EEG draft that also lacks one)
- Modify: the one file with `hardware = "clinical_amp"`
- Modify: `tests/test_corpus_schema.py`

**Interfaces:**
- Consumes: the modality and hardware enums from Task 7.

Today only two of the 44 files declare `modality`: `protocols/hrv_resonance.refrain` (`hrv`) and `protocols/eeg/critical_fluctuation.refrain` (`eeg`). The other 42 rely on the schema default — 41 under `protocols/eeg/` and the EEG draft `drafts/scp_cz.refrain`.

- [ ] **Step 1: Write the failing test plus the default-reachability check (Review Focus item 2)**

Append to `tests/test_corpus_schema.py`:

```python
EEG_DIR = ROOT / "protocols" / "eeg"


EEG_FILES = sorted(EEG_DIR.rglob("*.refrain")) + [ROOT / "drafts" / "scp_cz.refrain"]


@pytest.mark.parametrize("path", EEG_FILES, ids=lambda p: p.name)
def test_eeg_protocols_declare_modality(path):
    # Explicit beats implicit: a host app filtering by modality should not have
    # to know the default to find every EEG protocol.
    assert _meta(path).get("modality") == "eeg", (
        f"{path.name}: EEG protocols declare modality = \"eeg\" explicitly"
    )


def test_missing_modality_still_defaults_to_eeg():
    # Review Focus 2: a user's own file may omit it. The default must survive
    # in the schema so hosts can rely on it.
    assert SCHEMA["properties"]["modality"]["default"] == "eeg"
    doc = {"description": "d", "status": "draft", "goals": ["focus_attention"]}
    jsonschema.validate(doc, SCHEMA)  # valid without modality


def test_no_clinical_amp_hardware():
    for path in ALL:
        assert _meta(path).get("hardware") != "clinical_amp", (
            f"{path.name}: hardware 'clinical_amp' renamed to 'research_amp'"
        )
```

- [ ] **Step 2: Run it**

```bash
python -m pytest tests/test_corpus_schema.py -q 2>&1 | tail -20
```

Expected: `test_eeg_protocols_declare_modality` fails on 42 files (41 under `protocols/eeg/` plus `drafts/scp_cz.refrain`); `test_no_clinical_amp_hardware` fails on 1; the two default checks pass.

- [ ] **Step 3: Rename the hardware value**

```bash
grep -rl 'hardware.*= *"clinical_amp"' --include='*.refrain' protocols drafts \
  | xargs sed -i '' 's/"clinical_amp"/"research_amp"/'
```

- [ ] **Step 4: Backfill `modality`**

Insert `modality` immediately after the `status` line in each EEG file that lacks one, matching that file's existing alignment. Verify the count first:

```bash
for f in $(find protocols/eeg drafts -name '*.refrain'); do
  grep -q 'modality' "$f" || echo "$f"
done | wc -l
```

Expected: `42`. Then add the line to each. Two alignment styles are in use — 22 files write `status            =` and 22 write `status          =` — so match the neighbouring lines in the file you are editing rather than inserting one fixed string.

- [ ] **Step 5: Verify**

```bash
python -m pytest tests/test_corpus_schema.py -q
grep -rn 'clinical_amp' --include='*.refrain' protocols drafts
for f in $(find protocols drafts -name '*.refrain'); do grep -q 'modality' "$f" || echo "MISSING $f"; done
```

Expected: all tests pass; the grep returns nothing.

- [ ] **Step 6: Commit**

```bash
git add protocols drafts tests/test_corpus_schema.py
git commit -m "feat(protocols)!: backfill modality=eeg, rename clinical_amp to research_amp

BREAKING: hardware value renamed. Every EEG protocol now declares its
modality explicitly instead of relying on the schema default; the default
stays 'eeg' for user files that omit it."
```

---

### Task 14: Library docs

Small files; 19 uses of "clinical" and 15 of "clinician" across them.

**Files:**
- Modify: `README.md` (53 lines), `docs/evidence.md` (18), `docs/tagging.md` (29), `docs/host-app-guide.md` (38), `docs/conventions.md` (16), `docs/contributing.md` (12), `ROADMAP.md` (20)
- Modify: `.github/workflows/ci.yml` — two comments describe the fuzz amp profile as "a clinical amp"; reword to "a research-grade amp". Comments only; no step changes.
- **Do not touch:** `docs/fork-audit-2026-07.md`, `docs/superpowers/**`

- [ ] **Step 1: `README.md`**

Change the subtitle from `Reference neurofeedback & HRV protocol library` to `Reference biosignal training-protocol library`. Keep the tags-not-folders and files-are-source-of-truth sections verbatim — both are already modality-agnostic. Reframe the ⚠️ untested badge from clinical to general-wellness while keeping every honest word: untested, not validated, not a medical device, no health claims.

- [ ] **Step 2: `docs/evidence.md`**

Keep the two-axis model — our file maturity (`status`) versus the technique's standing (`evidence`) — and the untested-badge honesty. Rewrite the `evidence` axis from "clinical-literature support" to "how established the signal-training approach is in prior art." Add one paragraph:

```markdown
`evidence` and `citation` describe **where a technique comes from**, not what it
will do for anyone. An `established` tier means the approach has a long track
record in the literature; it is not a claim that the protocol treats anything.
The library ships general-wellness building blocks and makes no diagnostic or
therapeutic claim. Pretending these techniques have no origin in clinical
research would be dishonest — citing that origin as provenance is not the same
as claiming an outcome.
```

- [ ] **Step 3: `docs/tagging.md`**

Replace the old eight goals with the new eight and their labels: `focus_attention` (Focus & attention), `calm_stress` (Calm & stress relief), `sleep_quality` (Sleep quality), `alertness_performance` (Alertness & performance), `mood_balance` (Mood & balance), `flow_connectivity` (Flow & connectivity), `deep_meditative` (Deep meditative states), `interoception` (Body awareness). State that these are training goals, not indications. Document the widened `modality` list, `research_amp`, and the four dropped fields. Sweep "clinical"/"clinician" per Task 12's rule.

- [ ] **Step 4: `docs/host-app-guide.md`**

Keep "list by parse, resolve on select" exactly as is. Add a `modality` filter chip to the recommended picker surface. Rename the "Clinical-safety UX" section to "Trust & provenance" and rewrite it around: show the `evidence` tier and `citation` as where the technique comes from; show the untested badge; do not present either as an outcome claim. Remove `indication` and `population` from the recommended surface — they no longer exist in distributed files. Keep the unknown-value-buckets-to-Other guidance verbatim.

- [ ] **Step 5: `docs/conventions.md`, `docs/contributing.md`, `ROADMAP.md`**

Sweep for "clinical"/"clinician" using Task 12's category-versus-example rule.

- [ ] **Step 6: Verify the docs name only live vocabulary**

```bash
grep -rn 'adhd_attention\|calm_anxiety\|sensorimotor_sleep\|mood_regulation\|trauma_recovery\|clinical_amp\|indication\|population\|safety_monitoring\|outcome_measures' \
  README.md ROADMAP.md docs/evidence.md docs/tagging.md docs/host-app-guide.md \
  docs/conventions.md docs/contributing.md
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add README.md ROADMAP.md docs/evidence.md docs/tagging.md docs/host-app-guide.md docs/conventions.md docs/contributing.md .github/workflows/ci.yml
git commit -m "docs: general-wellness framing, new goal vocabulary, modality filter"
```

---

### Task 15: Regenerate the catalog and run the real CI

**Files:**
- Regenerate: `catalog.json`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_corpus_schema.py`

- [ ] **Step 1: Write the tolerant-listing test (Review Focus item 1)**

Append to `tests/test_corpus_schema.py`:

```python
def test_catalog_lists_a_protocol_with_an_unknown_goal(tmp_path):
    """A user's own file with a goal outside the eight buckets still LISTS.

    Closing the schema enum must not make the picker strict: the host buckets
    an unknown value into "Other" rather than dropping the protocol.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_catalog", ROOT / "tools" / "build_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    f = tmp_path / "mine.refrain"
    f.write_text(
        'protocol "mine" {\n'
        '  meta {\n'
        '    description = "my own thing"\n'
        '    status      = "draft"\n'
        '    goals       = ["my_own_goal"]\n'
        '  }\n'
        '}\n'
    )
    meta = mod.read_meta(f)
    assert meta["goals"] == ["my_own_goal"], "the builder must not filter goals"
    assert "_error" not in meta, "an unknown goal is not a parse failure"
```

- [ ] **Step 2: Run it**

```bash
python -m pytest tests/test_corpus_schema.py::test_catalog_lists_a_protocol_with_an_unknown_goal -q
```

Expected: PASS — `build_catalog.read_meta` is parse-only and already tolerant. The test pins that so a later "tighten the builder" change cannot break the picker contract.

- [ ] **Step 3: Regenerate the catalog**

```bash
python tools/build_catalog.py
```

Expected output: `catalog.json: 44 protocols (N draft/roadmap)`.

- [ ] **Step 4: Spot-check the regenerated catalog**

```bash
python3 -c "
import json, collections
d = json.load(open('catalog.json'))
g = collections.Counter(x for e in d['protocols'] for x in (e['meta'].get('goals') or []))
m = collections.Counter(e['meta'].get('modality') for e in d['protocols'])
ev = collections.Counter(e['meta'].get('evidence') for e in d['protocols'])
print('goals    ', dict(g)); print('modality ', dict(m)); print('evidence ', dict(ev))
print('dropped fields present:',
      {k for e in d['protocols'] for k in e['meta']}
      & {'indication','population','safety_monitoring','outcome_measures'})
"
```

Expected: only the eight new goals; `interoception` count is 1; modality is `{'eeg': 43, 'hrv': 1}`; evidence is only `established`/`probable`/`exploratory`; the dropped-fields set is empty.

- [ ] **Step 5: Run the exact CI command set and show the output**

These are what `.github/workflows/ci.yml` runs. Run all of them, paste the output in the PR.

```bash
python tools/build_catalog.py
git diff --exit-code catalog.json || echo "STALE — commit the regenerated catalog"
python -m pytest -q
```

Then the behavioural gate, which the reframe must not disturb. CI splits the corpus in two — protocols that reference `amp.<field>` resolve against a research-grade amp profile, the rest at `amp=None` — so run both halves exactly as `.github/workflows/ci.yml` does:

```bash
set -eu
AMP="$(python -c 'import refrain,os;print(os.path.join(os.path.dirname(refrain.__file__),"amp_profiles","q21.json"))')"
LITERAL="$(grep -rLE --include='*.refrain' '\bamp\.[a-z_]' protocols || true)"
[ -n "$LITERAL" ] && refrain fuzz $LITERAL --library lib --seed 42
READERS="$(grep -rlE --include='*.refrain' '\bamp\.[a-z_]' protocols || true)"
[ -n "$READERS" ] && refrain fuzz $READERS --library lib --amp "$AMP" --seed 42
```

One thing to watch: that split is decided by a grep, and the workflow's own comment says the `[a-z_]` after the dot is there to stop prose like "…clinical amp." being matched. Task 12 removes that prose anyway, so no protocol should change sides. If `LITERAL` and `READERS` come back with different counts than on `main`, a protocol moved between halves and the cause is an accidental body edit, not the reframe.

Expected: the fuzz result is unchanged from `main` — same pass/skip counts, 0 violations. The reframe touched metadata and prose, not a single pipeline, threshold, or reward. If the numbers move, something structural was edited by accident; stop and find it.

- [ ] **Step 6: Add the changelog entry**

```markdown
## Unreleased — biosignal reframe (BREAKING)

The library is now a general-purpose **biosignal** training-protocol library:
modality-neutral, and general-wellness rather than clinical in what it ships.
No protocol's behaviour changed — no pipeline, threshold, reward, or inhibit
was touched, and the fuzzer result is identical.

### Breaking
- **`goals` vocabulary replaced.** `adhd_attention`→`focus_attention`,
  `calm_anxiety`→`calm_stress`, `sensorimotor_sleep`→`sleep_quality`,
  `mood_regulation`→`mood_balance`. `trauma_recovery` is removed;
  `interoception` is new. Hosts bucket unknown values into "Other" as before.
- **`modality` widened** to `eeg`, `ecg`, `hrv`, `gsr`, `emg`, `temp`, `resp`;
  default stays `eeg`. Every EEG protocol now declares it explicitly.
- **`hardware`**: `clinical_amp` → `research_amp`.
- **Dropped from distributed files:** `indication`, `population`,
  `safety_monitoring`, `outcome_measures`. A host that needs them keeps them
  host-side. `control_ref` is kept.

### Changed
- `evidence` and `citation` describe how established a **technique** is and
  where it comes from — provenance, not efficacy. The tiers are unchanged.
- 21 files carried an `evidence` value that was never in the enum (19 `demo`,
  2 `clinical`); each now has the tier its protocol family already used.
- Comments, titles, and summaries describe the signal training rather than a
  condition. Citations keep their real paper titles.

### Added
- `tests/test_corpus_schema.py` — validates every protocol against the whole
  schema. Its absence is why the `evidence` drift went unnoticed.
- `tests/test_neutrality.py` — forbids the dropped fields and indication
  language in distributed prose.
```

- [ ] **Step 7: Commit and push**

```bash
git add catalog.json CHANGELOG.md tests/test_corpus_schema.py
git commit -m "chore: regenerate catalog for the neutralized vocabulary"
git push -u origin worktree-biosignal-reframe
```

- [ ] **Step 8: Open the PR**

Target `main`. Title: `feat!: neutralize the distributed library — biosignal and general-wellness`. The body must carry the changelog's breaking section verbatim, the pasted `pytest -q` and fuzz output, and a line naming the downstream work this unblocks (below).

---

## Downstream work this creates — out of scope here, must be flagged in the PR

Neither item belongs in workstreams 1 or 2, and neither is optional afterwards.

1. **`refrain-editor` vendored fixtures go stale.** Thirteen files under `vendor/fixtures/*.model.json` embed the old goal strings and, in at least one case, an `indication` field. They are vendored copies of protocol files, so they need re-vendoring after this lands — otherwise the editor's round-trip tests assert against a vocabulary that no longer exists. This is the first task of workstream 3. The editor's own `ui/src/goals/` module is unaffected: its "goal" is an authoring concept (site plus band plus direction), not the picker's `meta.goals`.
2. **The recorder reads `meta.goals` dynamically** — `recorder/backend/nf/protocol_meta.py` maps `indication` values to labels but hardcodes no goal vocabulary, so the picker will not break. The `indication` map becomes dead code once the field is gone; delete it when the recorder's pin is bumped (spec §7 step 4).

---

## Self-review

**Spec coverage.** §3.1 narrative → Tasks 2–5. §3.1 disclaimer → Task 3. §3.2 `montage` → Task 5 Step 2. §3.2 `reward`/`inhibit` unchanged → Global Constraints. §3.2 band/site prose → Tasks 5 and 7. §3.3 modality-blind language → Global Constraints. §4.1 modality/site/bands/hardware → Task 7, backfill in Task 13. §4.2 goals → Task 8. §4.2 evidence/citation kept and reframed → Tasks 7, 10, 14. §4.2 comments → Task 12. §4.2 dropped fields → Task 11. §4.2.1 eight buckets and the corpus remap → Task 8. §4.3 README/evidence.md/tagging.md/host-app-guide.md → Task 14. §4.3 modality backfill → Task 13. §4.3 catalog and CI regen → Tasks 9, 11, 15.

**Gap found and closed:** the spec names `tools/gen_seed_protocols.py` as needing regeneration (§4.3). That file does not exist in this repo — `tools/` contains only `build_catalog.py`. No task is needed; noted here so an executor does not go looking for it.

**Two spec items deliberately out of scope**, both named in the spec itself as later steps: §5 (editor modality-awareness — workstream 3) and §6 (recorder and companion pin bumps — workstream 4). §9's question about whether the proposal file should move to a private repo is unresolved and untouched by this plan.
