# Contributing to Refrain

Thanks for your interest. Refrain is a small project — contributions
are welcome, but please discuss substantial changes in an issue before
opening a PR.

## Quick start

```
git clone https://github.com/refrain-lang/refrain
cd refrain
python -m venv .venv
source .venv/bin/activate
pip install -e ".[eval,dev]"
pytest -q
```

Should see 350+ tests passing.

## Running tests

```
pytest                              # full suite
pytest tests/test_parser_*.py       # parser only
pytest tests/test_eval_*.py         # evaluator only
pytest -k coherence                 # specific feature
pytest -k "not coherence"           # everything except
pytest -x                           # stop at first failure
```

Tests that depend on the real-EEG fixture at `data/CRJA_20240228_EO.xdf`
skip automatically when the file is absent. CI doesn't have access to
that file, so those tests are skipped on CI but run locally if you
have the recording.

## Pull request flow

- Branch from `main` (don't branch from another in-flight branch
  unless you mean to stack PRs).
- Commit in themed chunks — one logical change per commit. Aggregate
  cleanup commits during review.
- Run `pytest -q` and `ruff check src/refrain --select F,E9` before
  pushing.
- Open a PR against `main`. The template will prompt you for a
  summary, test plan, and any spec-impact notes.
- We use **rebase merge** to keep history linear and bisectable.

## Style

- Apache-2.0 file headers on new source files (match the existing
  pattern).
- No new top-level runtime dependencies without prior discussion —
  use the `[eval]` extra for evaluator deps (mne, pyxdf, scipy) and
  `[dev]` for test deps. The parser/resolver/IR core should remain
  installable with zero extras.
- Match the existing code style. `ruff` is the canonical formatter
  config; CI checks `F` and `E9` errors.
- Frozen, slotted dataclasses for AST / IR node types.
- Type annotations on public surfaces (the embedding API in
  `src/refrain/eval_.py`, the registry types in
  `src/refrain/primitives.py`).

## What's a good first contribution?

- **Bug reports** with a reproduction. Synthetic-signal repros are
  most useful because they're deterministic; recorded-EEG repros are
  harder to share. The `SignalGenerator` in `refrain.synthetic` is
  the suggested tool.
- **Documentation improvements.** The docs are the contract; if
  something's unclear or wrong, file an issue or open a PR.
- **Spec ambiguities** that the resolver or evaluator currently
  papers over. `docs/DESIGN-NOTES.md` is the running list — pick one
  and propose a resolution.
- **New primitives motivated by a clinical use case.** Please file an
  issue first describing the protocol you're trying to express; we
  want to be deliberate about the standard library's surface.

## What we're not yet looking for

- **Major refactors of language design.** We're in v0.x. The spec is
  intentionally fluid but breaking changes need a coordinated story.
- **Speculative primitives** without a real protocol motivating them.
  The standard library is intentionally small; we'd rather grow it
  on demand than carry unused surface.
- **Performance optimizations without measured baselines.** Profile
  first; the existing per-chunk costs are well within the 4 ms/chunk
  budget we declare for live operation.

## Spec changes

If your contribution changes the language contract (something in
`docs/SPEC.md`, `docs/PRIMITIVES.md`, or the embedding API in
`docs/EMBEDDING.md`), update the relevant doc in the same PR. If
you're flagging a spec ambiguity for future resolution, add a note
to `docs/DESIGN-NOTES.md` instead of changing SPEC.md directly.

## Cutting a release (maintainers)

`refrain` and `refrain-core` are versioned **in lockstep**: both are
built from the same commit on every `v*` tag by `release.yml` and are
Rust↔Python equivalence-gated in CI, so they share one version number
(a CI test, `tests/test_version_lockstep.py`, fails if they drift).
The consequence is that `refrain-core`'s number bumps even on
Python-only releases; that's accepted.

1. Open a PR titled `release: vX.Y.Z` that bumps `version` in **both**
   `pyproject.toml` (root) and `refrain-core/pyproject.toml`, and moves
   the `[Unreleased]` CHANGELOG entries into a new `[X.Y.Z]` section.
   (`refrain-core/Cargo.toml` stays `0.1.0` — wheel versions come from
   the pyproject files.) Minor bump for additive features, patch for
   fixes.
2. Merge it, then tag `vX.Y.Z` **on the merge commit** and push the
   tag. `release.yml` builds host wheels for both packages plus the
   mobile artifacts and attaches them to the GitHub Release.
3. **Never push the tag before the release PR merges** — tagging the
   pre-bump commit publishes wheels with the old version string. If a
   mispointed tag was already published: `gh release delete vX.Y.Z
   --cleanup-tag --yes`, delete the local tag, re-tag the merge
   commit, push again.

## Reporting security issues

See `SECURITY.md`. Don't file public issues for security concerns.

## Code of conduct

Be kind. Disagreement on technical or clinical-design topics is
expected and welcome; personal attacks are not. We don't have a
formal Code of Conduct yet but intend to operate as if we did.

## License

By contributing, you agree that your contributions will be licensed
under the project's Apache-2.0 license.
