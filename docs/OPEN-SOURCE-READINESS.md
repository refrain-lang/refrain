# Open-source readiness: audit and plan

Status: **proposal, nothing implemented yet.** Written 2026-11-23 against
`main` at `c7b6c59` (v0.25.1). This file is a hand-off for continuing the work
in a local session. Treat it as a working document, not as permanent project
docs: once the plan is turned into issues/milestones, delete it or fold it into
`ROADMAP.md`.

## Goal

Make Refrain something we are proud to open-source: a **research prototype for
biosignal analysis and feedback** (bio, not only neuro), motivated by the
reproducibility problem (hence the name). The pitch: after using visual
programming tools (BioExplorer, BioEra) and proprietary platforms, we wanted
something far more programmable. HCL/Terraform ("infrastructure as code") and
Faust (declarative real-time DSP) are the inspiration.

The project should also be a visible example of **good AI-assisted
development**: spec-first, gated by independent oracles (the fuzzer, the
Python↔Rust equivalence gate), with tickets, templates and CI that a reviewer
would respect. It should not look vibe-coded.

## Headline

The engineering is far ahead of the presentation. There is real substance:

- about 20k lines of Python reference implementation, about 7k lines of Rust
  core, and Swift/Kotlin bindings through uniffi
- about 1,370 test functions
- a Python↔Rust golden-vector equivalence gate and a fixture-drift gate
- a protocol fuzzer (oracle plus metamorphic tier) that gates the corpus in CI
- versioned IR-JSON with a schema and `docs/CONFORMANCE.md`
- a disciplined CHANGELOG and a real defect history (D1–D9, the #100 fixture bug)

A stranger would still conclude the project is vibe-coded, for three reasons:
the front door is stale, the configured standards are not enforced, and
AI-session scaffolding sits in public view. Most of the work is making the
existing rigor visible, honest and enforced.

## Findings

### 1. Stale front door
- `README.md` says v0.1.0, 25 primitives and 350+ tests. Reality: 0.25.1 and
  about 1,370 tests. `CONTRIBUTING.md` repeats "350+".
- `docs/PROTOCOL-FUZZER.md` says "designed, not yet implemented". The fuzzer
  ships and gates CI.
- `refrain-core/README.md` describes a PoC branch and PR #13.
  `refrain-core/Cargo.toml` is at 0.1.0 with the description "neurofeedback
  protocols (PoC)".
- `STAGING.md` is obsolete (it describes the migration out of the original
  host app).
- `SECURITY.md` still has a placeholder email.
- The `src/refrain/resolver.py` module docstring says composition is deferred.
  Composition shipped long ago.

### 2. Private and company coupling (this repo)
- 29 files mention the company or its private apps. Examples: `author =
  "Peak Mind"` in `examples/*.refrain` and in `docs/SPEC.md`, and
  `scripts/release.sh` says it mirrors the private apps' scripts.
  Find them with:
  `grep -rIl -i "peak.mind\|coherence" --exclude-dir=.git .`
- About 30 references to private Linear tickets (REC-23, WOR-142, WOR-165).
- 77 hits for personal or scratch paths (`/Users/…`, `/private/tmp`, `claude-501`).
- `docs/superpowers/` holds about 98 AI-session plans, specs, records and raw
  CI logs.
- Some docs are customer- or prompt-shaped:
  `docs/*-feature-request-response.md`, `docs/HOST-PLUGIN-BRIEF.md` and
  `docs/RUST-CORE-HOST-BRIEF.md`.
- 9 commits in history carry a personal email (refrain-protocols has the same).

### 3. The gates don't match the claims
| Configured in `pyproject.toml` | Measured | Enforced in CI |
|---|---|---|
| ruff rules `E,F,W,I,N,B,UP,PL` | 225 errors | only `F,E9` |
| ruff format (implied) | 34 of 39 files would reformat | no |
| mypy `strict = true` | 549 errors in 25 files | not run |
| Rust fmt/clippy | not checked | not run |
| coverage | never measured | no |

- **Speed:** a serial `pytest -q` did not finish within 15 minutes in the
  cloud container, so pass/fail is unknown. CI runs that same serial suite on
  4 Python versions per PR. There is no PR-vs-nightly split.
- **Real data:** the real-EEG tests skip in CI because the recording is
  private, so CI never exercises a real signal.
- **Parser fuzzing:** SECURITY.md lists parser DoS as in scope, but nothing
  fuzzes the parser or the IR deserializer with hostile input. The protocol
  fuzzer tests semantics, not robustness.

### 4. Core structure
- Monoliths: `src/refrain/resolver.py` (4,259 lines), `src/refrain/eval_.py`
  (2,845) and `refrain-core/src/eval.rs` (3,445).
- `import refrain` eagerly imports editor → resolver → compose → parser.
- There is no declared public API surface and no deprecation policy.
- The resolver bails on the first error. A language needs multi-error
  diagnostics with locations.
- The Python/Rust role split is not documented in one place. The intent:
  Python is the reference and authoring side, Rust is the canonical runtime,
  and IR-JSON is the contract between them.

### 5. Missing community and research scaffolding
- Missing files: `CODE_OF_CONDUCT.md`, `CITATION.cff` (important for a
  reproducibility project), `.github/CODEOWNERS`, `.github/dependabot.yml`,
  `.pre-commit-config.yaml`, `ARCHITECTURE.md`, `ROADMAP.md` and
  `.git-blame-ignore-revs`.
- There is no docs site and no PyPI release yet.
- The issue templates are plain markdown rather than forms with acceptance
  criteria.

### 6. Adjacent repos in the org
- **refrain-protocols (public).** The reference protocol library: 18 current
  EEG protocols, 30 legacy, 1 HRV. It is also the fuzz corpus and the source of
  the editor's parity fixtures.
  - Its CI pins refrain to **v0.24.0** (behind), and a 0.25.1 bump branch is
    unmerged.
  - Company authorship runs through the protocols and `catalog.json`.
  - It has its own `docs/superpowers/` with personal paths, plus
    private-app-specific docs and tooling: a Recorder migration doc and
    `tools/session_report/`.
  - It has private ticket references, a stale `docs/fork-audit-2026-07.md`,
    and 32 remote branches, many stale.
  - CONTRIBUTING exists only as `docs/contributing.md`.
- **refrain-editor (private, TypeScript).** Good code: a parity gate against
  `render.py` and about 334 tests.
  - No README, LICENSE or CONTRIBUTING.
  - It vendors `catalog.json`, the schema and fixtures from this repo through a
    sync script.
  - Its version and consumer tooling are built around the private apps.
  - `refrain-versions.json` is stale.
  - It has its own `docs/superpowers/`.
- **protocols (private):** empty, and its name collides with
  refrain-protocols.
- **refrain.dev (private):** empty.

## What success looks like

1. **Ten-minute stranger test.** Someone runs `pip install refrain`, follows
   one README page, runs a protocol on synthetic data **and** on an open
   dataset, and sees feedback output.
2. **Every configured gate is enforced in CI:**
   - full ruff rule set and format
   - mypy on a ratchet (public API strict first)
   - clippy `-D warnings` and rustfmt
   - a coverage floor
   - the equivalence, fixture-drift and fuzz gates
   PR CI should take under about 10 minutes, with a nightly tier for deep
   fuzzing, mutation testing and mobile builds.
3. **Zero private residue:** no company names, private tickets or personal
   paths; attribution is "Refrain authors".
4. **Bio, not neuro, in practice:** end-to-end HRV, EDA and temperature
   examples on open data, alongside EEG.
5. **Evidence of reliability, not assertions:**
   - a public conformance suite
   - DSP checked against SciPy/MNE reference results
   - Hypothesis round-trip properties on parse/unparse
   - coverage-guided fuzzing of the parser and the IR deserializer
   - a documented record of what the fuzzer has caught
6. **Two or three published protocols reproduced** in Refrain, with citations
   and exported CRED-nf supplements. This is the "refrain" story made concrete.
7. **Citable releases:** `CITATION.cff`, a Zenodo DOI per release, PyPI via
   trusted publishing with attestations. Capstone: a JOSS paper.
8. **A documented, gated, auditable AI-assisted process** (Phase 6).

## Phased plan

### Phase 0: decisions (owner)
See "Open decisions" below.

### Phase 1: hygiene and front door
- Remove company, ticket and path references in all three repos.
  Regenerate `catalog.json` in refrain-protocols.
- Delete `STAGING.md`.
- Move `docs/superpowers/` out: archive it privately, and curate durable
  decisions into `docs/rfcs/NNNN-title.md` (one template).
- Retire or generalize the customer- and prompt-shaped docs.
- Rewrite `README.md`. Fix `CONTRIBUTING.md`, `docs/PROTOCOL-FUZZER.md`,
  `refrain-core/README.md`, the Cargo metadata and the resolver docstring.
- Reorganize `docs/` into user guide, reference, contributor and design
  sections.
- Add `CODE_OF_CONDUCT.md` (Contributor Covenant), `CITATION.cff` and
  `CODEOWNERS`, and set a real security contact (or GitHub private reporting
  only).

### Phase 2: make the gates honest
- Add `.pre-commit-config.yaml` (ruff, ruff-format, rustfmt, end-of-file and
  whitespace checks).
- Run `ruff format` in **one mechanical commit**, and list it in
  `.git-blame-ignore-revs`.
- Lint: either fix the backlog or narrow the configured rules to what will be
  enforced. Ratchet with per-file ignores and enforce the full set in CI.
- mypy: ratchet module by module (start with `eval_` embedding API,
  `primitives`, `ir`, `parser`). Add a CI job that fails on regressions.
- Add `cargo fmt --check`, `cargo clippy -- -D warnings` and `cargo deny`.
- Coverage: pytest-cov plus a floor, and cargo-llvm-cov reporting.
- Speed: pytest-xdist, `@pytest.mark.slow`, and per-test durations
  (`--durations=50`). PR CI runs the fast tier on 3.10 and 3.13; nightly runs
  everything.
- Supply chain: dependabot (pip, cargo, actions), CodeQL, Actions pinned by
  SHA, PyPI trusted publishing, sigstore/GitHub attestations.

### Phase 3: core cleanup
- Split `resolver.py` into a package: references, dimensions/types, hardware,
  composition, controls/autopilot, budget. Split `eval_.py` the same way.
  Keep behavior identical and let the equivalence and fuzz gates prove it.
- Make top-level imports in `src/refrain/__init__.py` lazy.
- Declare the public API (`__all__`, `docs/EMBEDDING.md`) and a deprecation
  policy.
- Multi-error diagnostics with source locations.
- Write `ARCHITECTURE.md`: Python reference/authoring → IR-JSON contract → Rust
  runtime → bindings.

### Phase 4: reliability evidence
- Hypothesis properties: `unparse(parse(x))` round-trip identity, and resolver
  invariants.
- atheris on the parser, cargo-fuzz on IR-JSON deserialization.
- Open-data fixtures, cached with checksums in CI, replacing the private
  recording. Candidates: PhysioNet EEG Motor Movement/Imagery, a PhysioNet HRV
  set, WESAD (EDA, temperature, BVP).
- Nightly mutation testing (mutmut, cargo-mutants).
- Latency benchmarks tracked over time (`bench/` exists).
- A fuzzer catch-record doc.

### Phase 5: biosignal breadth
- Audit SPEC, primitives and amp profiles for EEG-only assumptions.
- One worked example per modality (EEG, HRV, EDA, temperature).
- Reframe the remaining "neurofeedback"-only wording: Cargo metadata, PyPI
  classifiers, the docs.

### Phase 6: AI-assisted development as a visible strength
- `AGENTS.md` (and/or `CLAUDE.md`): conventions, the exact gates to run, the
  definition of done, and where spec changes go (SPEC vs DESIGN-NOTES vs RFC).
- Issue **forms** (`.github/ISSUE_TEMPLATE/*.yml`) with acceptance criteria,
  "gate(s) that prove it", and spec impact. These are tickets a human or an
  agent can pick up cold.
- Labels: `agent-ready`, `needs-rfc`, `good-first-issue`, `spec`, `dsp`,
  `fuzzer`, `rust-core`.
- An RFC process (`docs/rfcs/0000-template.md`).
- A "How Refrain is built" doc stating the model plainly:
  - the human owns the spec and the oracles
  - agents implement against the gates
  - the fuzzer and the Python↔Rust equivalence gate are independent checks
  - D1–D9 and #100 are the evidence

### Phase 7: launch
- Define "research preview" versus the criteria for a 1.0 language freeze.
- Publish the docs site (mkdocs-material from `docs/` to GitHub Pages; the
  refrain.dev repo or domain could host it).
- JOSS submission and an announcement.

## Adjacent-repo recommendations
- **refrain-protocols:** keep it separate and public. It is a content library
  with its own cadence. Clean it up (Phase 1), bump the CI pin to the latest
  release, automate bumps, and prune stale branches. Decide whether `legacy/`
  ships in the wheel.
- **refrain-editor:** merge it into this repo under `editor/` (TS). It mirrors
  `src/refrain/editor/*.py` and vendors files from here, so a monorepo removes
  the sync script and the drift risk. If it stays separate: add a README,
  LICENSE and CONTRIBUTING, drop the private-consumer tooling, then make it
  public.
- **protocols:** archive or delete it (empty, name collision).
- **refrain.dev:** use it for the docs site, or delete it and build the site
  from this repo.

## Open decisions
1. **History:** rewrite git history to remove the personal email and company
   references, or clean only the current files going forward? (Recommendation:
   leave history alone.)
2. **Tracking:** move the public plan to GitHub Issues and milestones, or keep
   Linear internal and mirror to GitHub?
3. **`docs/superpowers/`:** archive it privately, or curate a subset into
   public RFCs?
4. **Bar for the first public announcement:** recommendation: Phases 1–2 plus
   the ten-minute stranger test, with the rest done in public.
5. **refrain-editor:** merge it into the monorepo or keep it separate?

## Suggested first steps locally
```bash
git fetch origin claude/open-source-readiness-audit-td8ujb
git checkout claude/open-source-readiness-audit-td8ujb
python -m venv .venv && . .venv/bin/activate
pip install -e ".[eval,dev]" pytest-xdist
pytest -n auto --durations=50 -q        # first: get a pass/fail + timing baseline
ruff check src/refrain --statistics     # 225 at audit time
ruff format --check src/refrain         # 34 files at audit time
mypy src/refrain | tail -1              # 549 errors at audit time
```
Then turn the phases above into issues and milestones, and start with Phase 1.
